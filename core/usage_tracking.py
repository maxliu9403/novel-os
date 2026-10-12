"""Owner-scoped, metadata-only observations of real model usage.

Counters are provider observations, never estimates from prose. Cached input
and reasoning output are subsets of input/output, not additional tokens.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import fcntl
import hashlib
import inspect
import json
import logging
import os
import sys
from pathlib import Path
import tempfile
import uuid

# Legacy entry points import core files as top-level modules. Both supported
# import paths must share the same ContextVars, even when mixed by plugins.
sys.modules.setdefault("usage_tracking" if __name__ == "core.usage_tracking" else "core.usage_tracking", sys.modules[__name__])

LOG = logging.getLogger(__name__)
STAGES = ("brainstorming", "manuscript", "cover")
_scope = ContextVar("novel_usage_scope", default=None)
_request = ContextVar("novel_usage_request", default=None)
_request_identity = ContextVar("novel_usage_request_identity", default=None)


def _integer(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _mapping(value):
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    return dump() if callable(dump) else {}


def normalize_usage(value, provider="") -> dict:
    raw = _mapping(value)
    def first(*names):
        return next((v for name in names if (v := _integer(raw.get(name))) is not None), None)
    input_count = first("input_tokens", "prompt_tokens", "prompt_token_count")
    output_count = first("output_tokens", "completion_tokens", "candidates_token_count")
    cached = first("cached_input_tokens", "cached_tokens", "cache_read_input_tokens", "cached_content_token_count")
    if cached is None:
        cached = _integer(_mapping(raw.get("input_tokens_details") or raw.get("prompt_tokens_details")).get("cached_tokens"))
    reasoning = first("reasoning_tokens", "thoughts_token_count")
    if reasoning is None:
        reasoning = _integer(_mapping(raw.get("output_tokens_details") or raw.get("completion_tokens_details")).get("reasoning_tokens"))
    cache_write = first("cache_creation_input_tokens")
    # Anthropic reports uncached input separately; canonical input is inclusive.
    if provider == "anthropic" and input_count is not None:
        input_count += (cached or 0) + (cache_write or 0)
    if provider == "gemini" and output_count is not None:
        output_count += reasoning or 0
    if input_count is not None and cached is not None and cached > input_count:
        cached = None
    if output_count is not None and reasoning is not None and reasoning > output_count:
        reasoning = None
    total = input_count + output_count if input_count is not None and output_count is not None else first("total_tokens", "total_token_count")
    return {k: v for k, v in {
        "input_tokens": input_count, "output_tokens": output_count,
        "cached_input_tokens": cached, "reasoning_tokens": reasoning,
        "cache_creation_input_tokens": cache_write, "total_tokens": total,
    }.items() if v is not None}


@contextmanager
def usage_scope(owner_root: str | Path, stage: str):
    if stage not in STAGES:
        raise ValueError("Unknown usage stage")
    token = _scope.set((Path(owner_root).resolve(), stage))
    try:
        yield
    finally:
        _scope.reset(token)


def scoped_usage(stage, owner):
    """Bind a concrete owner at a service boundary, including CLI callers."""
    def decorate(fn):
        signature = inspect.signature(fn)
        @wraps(fn)
        def run(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            with usage_scope(owner(bound.arguments), stage):
                return fn(*args, **kwargs)
        return run
    return decorate


def cover_owner(context):
    root = Path(context.cover_root).resolve()
    return root.parent.parent if root.name == "covers" and root.parent.name == "outputs" else root.parent


def _directory(root: Path) -> Path:
    directory = root / "outputs" / "usage"
    if not directory.resolve().is_relative_to(root):
        raise ValueError("Usage directory escapes its owner")
    return directory


def _write_record(root, record, *, replace_usage=False):
    directory = _directory(root)
    directory.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(record["request_id"].encode()).hexdigest()
    path = directory / f"{identity}.json"
    if path.is_symlink() or (directory / ".lock").is_symlink():
        raise ValueError("Usage records must remain inside their owner")
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = json.loads(path.read_text()) if path.is_file() else {}
        # A final observation may enrich an incomplete one, never lose counters.
        if old and old.get("status") != "started" and record["status"] == "started":
            return
        if not replace_usage:
            record["usage"] = {**old.get("usage", {}), **record["usage"]}
        record["first_observed_at"] = old.get("first_observed_at") or record["recorded_at"]
        fd, temp = tempfile.mkstemp(prefix=".usage-", dir=directory)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(record, handle, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


def record_usage(*, provider: str, model: str, usage=None, request_id: str,
                 status="completed", kind="text", image_count=None, observed_model=None,
                 usage_is_normalized=False, usage_incomplete_fields=None, replace_usage=False):
    """Best-effort one-attempt hook. No owner scope means no persisted event."""
    scope = _scope.get()
    if scope is None:
        return
    try:
        root, stage = scope
        record = {
            "schema_version": 1, "request_id": str(request_id)[:200],
            "stage": stage, "provider": str(provider)[:100], "model": str(model)[:200],
            "observed_model": str(observed_model)[:200] if observed_model else None,
            "kind": kind, "status": status, "usage": dict(usage or {}) if usage_is_normalized else normalize_usage(usage, provider),
            "image_count": _integer(image_count), "recorded_at": datetime.now(timezone.utc).isoformat(),
            "usage_incomplete_fields": sorted(usage_incomplete_fields or []),
        }
        _write_record(root, record, replace_usage=replace_usage)
    except Exception:
        LOG.warning("Could not persist model usage observation", exc_info=False)


@dataclass
class UsageRequest:
    request_id: str
    usage: dict
    provider: str = ""
    model: str = ""
    observed_model: str | None = None
    http_attempts: int = 0
    finished_status: str | None = None
    incomplete_fields: set[str] = field(default_factory=set)

    @property
    def active_id(self):
        return self.request_id if self.http_attempts <= 1 else f"{self.request_id}:retry:{self.http_attempts - 1}"


@contextmanager
def usage_request_identity(identity):
    token = _request_identity.set(identity)
    try:
        yield
    finally:
        _request_identity.reset(token)


@contextmanager
def usage_request(provider, model):
    state = UsageRequest(_request_identity.get() or uuid.uuid4().hex, {}, provider, model)
    token = _request.set(state)
    status = "failed"
    record_usage(provider=provider, model=model, request_id=state.request_id, status="started")
    try:
        yield state
        status = "completed"
    finally:
        state.finished_status = status
        record_usage(provider=provider, model=model, request_id=state.active_id,
                     usage=state.usage, observed_model=state.observed_model, status=status,
                     usage_is_normalized=True, usage_incomplete_fields=state.incomplete_fields, replace_usage=True)
        _request.reset(token)


def current_request():
    return _request.get()


def tracking_enabled():
    return _scope.get() is not None


def observe_usage(value, *, provider="", observed_model=None, replace=False, incomplete_fields=()):
    state = current_request()
    if state is not None:
        if replace:
            state.usage.clear()
            state.incomplete_fields = set(incomplete_fields)
        state.usage.update(normalize_usage(value, provider))
        if observed_model:
            state.observed_model = str(observed_model)
        if state.finished_status and value:
            # A cancelled synchronous SDK worker may finish remotely after the
            # caller is released. Preserve its late counters under that attempt.
            record_usage(provider=state.provider, model=state.model, request_id=state.active_id,
                         usage=state.usage, observed_model=state.observed_model,
                         status=state.finished_status, usage_is_normalized=True,
                         usage_incomplete_fields=state.incomplete_fields, replace_usage=True)


def http_attempt_started(_request):
    """httpx request hook: observe SDK-internal retries without changing policy."""
    state = current_request()
    if state is None:
        return
    if state.http_attempts:
        record_usage(provider=state.provider, model=state.model, request_id=state.active_id,
                     usage=state.usage, status="failed", usage_is_normalized=True,
                     usage_incomplete_fields=state.incomplete_fields, replace_usage=True)
        state.usage.clear()
        state.incomplete_fields.clear()
    state.http_attempts += 1
    record_usage(provider=state.provider, model=state.model, request_id=state.active_id, status="started")


def read_records(owner_root):
    root = Path(owner_root).resolve()
    try:
        directory = _directory(root)
    except ValueError:
        return {}
    result = {}
    for path in directory.glob("*.json"):
        if path.is_symlink():
            continue
        try:
            record = json.loads(path.read_text())
            if record.get("schema_version") == 1 and record.get("stage") in STAGES:
                result[record["request_id"]] = record
        except (OSError, ValueError, KeyError):
            continue
    return result
