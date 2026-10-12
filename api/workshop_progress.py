"""Small durable projections of verified request events for the workshop UI.

The append-log lock is held by the caller when projecting. Progress never owns
session state; the session's revision/job identity and terminal status win.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import stat
import uuid


MAX_PROGRESS_BYTES = 8192
_PHASES = {"preparing", "starting", "waiting", "receiving", "parsing", "validating",
           "repairing", "delivery", "completed", "failed", "cancelled"}
_IDENTITY = ("session_id", "revision", "job_id", "operation")
_TERMINAL = {"completed", "failed", "cancelled"}
_STAGE_FIELDS = ("delivery_stage", "stage_index", "completed_stages", "current_stage_status")
_ACTIVITY_PREFIXES = {"reasoning_summary": "reasoning", "text": "text", "transport": "transport"}
_REQUEST_FIELDS = ("request_id", "request_started_at", "last_cli_event_at", "last_content_at",
                   "last_upstream_event_at", "upstream_event", "upstream_endpoint_category",
                   "last_activity_observed_at", "activity_kind", "last_reasoning_observed_at",
                   "last_text_observed_at", "last_transport_observed_at",
                   "reasoning_event_count", "text_event_count", "transport_event_count", "activity_time_basis", "request_closed")


def _read(directory_fd: int, name: str) -> dict:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROGRESS_BYTES:
                return {}
            value = json.loads(os.read(fd, MAX_PROGRESS_BYTES + 1))
            return value if isinstance(value, dict) else {}
        finally:
            os.close(fd)
    except (OSError, ValueError, UnicodeError):
        return {}


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1e18 else None


def _count(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else 0


def _latest(current, previous, *, strict=False):
    prior = _timestamp(previous)
    if prior is None:
        return True
    current_time = datetime.fromisoformat(current.replace("Z", "+00:00"))
    prior_time = datetime.fromisoformat(prior.replace("Z", "+00:00"))
    return current_time > prior_time if strict else current_time >= prior_time


def persist_progress(directory_fd: int, name: str, record: dict) -> None:
    """Best effort; caller holds the revision/operation append log's flock."""
    temporary = f".progress-{uuid.uuid4().hex}"
    try:
        previous = _read(directory_fd, name)
        event, now = record["event"], record["timestamp"]
        same = all(previous.get(key) == record.get(key) for key in _IDENTITY)
        if same and previous.get("attempt", 0) > record["attempt"]:
            return  # A delayed telemetry batch from an older attempt.
        if not same or previous.get("attempt") != record["attempt"]:
            # A format repair is another request in the same turn. Keep the
            # evidence that earlier requests returned content, while resetting
            # the per-request counters and identifiers. Older sidecars can be
            # upgraded from the current request's recorded content.
            carried = {}
            if same:
                carried = {
                    "cumulative_content_chars": _number(previous.get("cumulative_content_chars"))
                        if "cumulative_content_chars" in previous else _number(previous.get("content_chars")),
                    "last_turn_content_at": _timestamp(previous.get("last_turn_content_at"))
                        or _timestamp(previous.get("last_content_at")),
                    "last_completed_attempt": previous.get("last_completed_attempt"),
                    "repairing_response": previous.get("repairing_response") is True,
                    **{key: previous.get(key) for key in _STAGE_FIELDS},
                }
            previous = {key: record.get(key) for key in _IDENTITY}
            previous.update(attempt=record["attempt"], phase="preparing", content_chars=0,
                            cumulative_content_chars=0, last_turn_content_at=None,
                            last_completed_attempt=None, repairing_response=False)
            previous.update(carried)
        elif (record.get("request_id") and previous.get("request_id")
              and record["request_id"] != previous["request_id"]):
            if event != "request.start":
                return
            # Coarse delivery stages can issue multiple requests within one
            # callback. Keep saved-stage and whole-turn evidence, reset request
            # observations so an old delta cannot look like current activity.
            for key in _REQUEST_FIELDS:
                previous.pop(key, None)
            previous.update(content_chars=0, phase="preparing")
        p = previous
        # Read legacy sidecars without losing their already observed content.
        if "cumulative_content_chars" not in p:
            p["cumulative_content_chars"] = _number(p.get("content_chars")) or 0
            p["last_turn_content_at"] = _timestamp(p.get("last_content_at"))
        if record.get("request_id"):
            p["request_id"] = record["request_id"]
        if event == "request.start" and not p.get("request_started_at"):
            p["request_started_at"] = now
        if event in {"upstream.observer_started", "upstream.activity"}:
            p["activity_time_basis"] = "batch_observed"
        # Upstream export can be delayed; it never advances the task phase or
        # pretends that a heartbeat is model-generated text.
        if event == "upstream.event":
            current = _timestamp(record.get("source_timestamp")) or now
            if _latest(current, p.get("last_upstream_event_at")):
                p["last_upstream_event_at"] = current
                p["upstream_event"] = record.get("event_kind") or record.get("upstream_event")
                p["upstream_endpoint_category"] = record.get("endpoint_category")
        elif event != "upstream.activity":
            p["last_worker_check_at"] = now
        if event in {"first_cli_event", "cli_event", "cli_error"}:
            p["last_cli_event_at"] = now
        for source, target in (("configured_max_tokens", "configured_max_tokens"),
                               ("effective_max_tokens", "effective_max_tokens"),
                               ("max_tokens_supported", "output_limit_supported")):
            if source in record:
                p[target] = record[source]
        if "configured_max_tokens" not in p and "max_tokens" in record:
            p["configured_max_tokens"] = record["max_tokens"]
        phase = p.get("phase", "preparing")
        if event.startswith("delivery.stage."):
            # Completion means the worker saved and checked its checkpoint, so
            # it legitimately follows the model attempt's terminal event.
            p.update({key: record[key] for key in ("delivery_stage", "stage_index")})
            p["completed_stages"] = max(_count(p.get("completed_stages")), record["completed_stages"])
            p["current_stage_status"] = event.rsplit(".", 1)[1]
            if phase not in {"failed", "cancelled"}:
                phase = p["phase"] = "delivery"
        if event in {"request.completed", "request.failed", "request.cancelled", "request.timeout",
                     "attempt.completed", "attempt.failed", "attempt.cancelled", "attempt.timeout"}:
            p["request_closed"] = True
        # A validation failure ends an attempt; the bounded format repair loop
        # explicitly announces continuation before the next request starts.
        if event == "format_retry" and phase == "failed":
            phase = "repairing"
        if phase not in _TERMINAL:
            if event == "upstream.activity" and p.get("request_id") and not p.get("request_closed"):
                kind, observed = record.get("activity_kind"), _timestamp(record.get("observed_at"))
                prefix, count = _ACTIVITY_PREFIXES.get(kind), _count(record.get("metric_count"))
                if prefix and observed and count:
                    count_key, time_key = f"{prefix}_event_count", f"last_{prefix}_observed_at"
                    p[count_key] = min(2**63 - 1, _count(p.get(count_key)) + count)
                    if _latest(observed, p.get(time_key)):
                        p[time_key] = observed
                    # A metric batch has no ordering between its series. At an
                    # equal observation time retain known text rather than
                    # letting arbitrary exporter ordering relabel it summary.
                    if kind != "transport" and _latest(observed, p.get("last_activity_observed_at"),
                            strict=kind == "reasoning_summary" and p.get("activity_kind") == "text"):
                        p.update(last_activity_observed_at=observed, activity_kind=kind)
            chars = record.get("model_output_chars")
            if event in {"request.completed", "cli.completed"}:
                chars = record.get("output_chars", chars)
            previous_chars = _number(p.get("content_chars")) or 0
            if _number(chars) is not None and chars > previous_chars:
                total = (_number(p.get("cumulative_content_chars")) or 0) + chars - previous_chars
                p.update(content_chars=chars, last_content_at=now,
                         cumulative_content_chars=min(total, 1e18), last_turn_content_at=now)
                phase = "receiving"
            if event in {"request.start", "process_started", "stdin_sent"}:
                phase = "starting"
            elif event in {"first_cli_event", "cli_event"} and phase in {"preparing", "starting", "waiting"}:
                phase = "waiting"
            elif event in {"request.completed", "cli.completed"}:
                phase = "parsing"
                if (_number(p.get("content_chars")) or 0) > 0:
                    p["last_completed_attempt"] = record["attempt"]
            elif event == "validation.start":
                phase = "parsing" if record.get("phase") in {"response_parse", "titles_parse"} else "validating"
            elif event == "format_retry":
                phase = "repairing"
                p["repairing_response"] = True
            elif event == "attempt.completed":
                phase = "completed"
                p["repairing_response"] = False
            elif event in {"attempt.timeout", "attempt.failed"}:
                phase = "failed"
            elif event in {"attempt.cancelled", "request.cancelled", "cli.cancelled"}:
                phase = "cancelled"
            p["phase"] = phase
        payload = json.dumps(p, ensure_ascii=True, separators=(",", ":")).encode()
        if len(payload) > MAX_PROGRESS_BYTES:
            return
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            raise
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    except Exception:
        pass
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except OSError:
            pass


def runtime_progress(session: dict, directory: Path) -> dict | None:
    """Public whitelist, bound to the authorized session rather than a supplied path."""
    if session.get("status") not in {"running", "error"} or not session.get("job_id"):
        return None
    revision = session.get("revision")
    operation = session.get("active_operation", "brainstorm")
    if type(revision) is not int or revision < 0 or operation not in {"brainstorm", "titles"}:
        return None
    p = {}
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            p = _read(fd, f"{revision}-{operation}.progress.json")
        finally:
            os.close(fd)
    except OSError:
        pass
    expected = {"session_id": session.get("id"), "revision": revision,
                "job_id": session.get("job_id"), "operation": operation}
    if not all(p.get(k) == v for k, v in expected.items()):
        p = {}
    start = next((_timestamp(row.get("time")) for row in reversed(session.get("logs", []))
                  if row.get("event") in {"turn_started", "titles_started"}), None)
    deadline = None
    timeout = _number(session.get("turn_timeout_seconds"))
    if start and timeout is not None and timeout <= 7 * 86400:
        deadline = (datetime.fromisoformat(start.replace("Z", "+00:00")) + timedelta(seconds=timeout)).isoformat()
    phase = p.get("phase") if isinstance(p.get("phase"), str) and p["phase"] in _PHASES else "preparing"
    if session.get("status") == "error":
        phase = "failed"
    # The model/validator may have finished while the worker still saves session
    # artifacts. Only session state is authoritative for user-visible completion.
    elif phase == "completed":
        phase = "validating"
    result = {**expected, "started_at": start, "deadline_at": deadline, "phase": phase,
              "attempt": p.get("attempt") if type(p.get("attempt")) is int and 1 <= p["attempt"] <= 12 else None,
              "request_id": None, "content_chars": _number(p.get("content_chars")) or 0,
              "cumulative_content_chars": (_number(p.get("cumulative_content_chars")) or 0)
                  if "cumulative_content_chars" in p else _number(p.get("content_chars")) or 0,
              "last_completed_attempt": p.get("last_completed_attempt")
                  if type(p.get("last_completed_attempt")) is int and 1 <= p["last_completed_attempt"] <= 12 else None,
              "repairing_response": p.get("repairing_response") is True,
              "upstream_event": None, "configured_max_tokens": _number(p.get("configured_max_tokens")),
              "effective_max_tokens": _number(p.get("effective_max_tokens")),
              "output_limit_supported": p.get("output_limit_supported") if type(p.get("output_limit_supported")) is bool else None}
    for key in ("last_worker_check_at", "last_cli_event_at", "last_content_at", "last_upstream_event_at", "last_turn_content_at",
                "request_started_at", "last_reasoning_observed_at", "last_text_observed_at", "last_transport_observed_at",
                "last_activity_observed_at"):
        result[key] = _timestamp(p.get(key))
    for prefix in _ACTIVITY_PREFIXES.values():
        result[f"{prefix}_event_count"] = _count(p.get(f"{prefix}_event_count"))
    kind = p.get("activity_kind")
    result["activity_kind"] = kind if isinstance(kind, str) and kind in {"reasoning_summary", "text"} else None
    result["activity_time_basis"] = "batch_observed" if p.get("activity_time_basis") == "batch_observed" else None
    category = p.get("upstream_endpoint_category")
    result["upstream_endpoint_category"] = category if isinstance(category, str) and category in {"models", "responses", "usage", "instructions", "other"} else None
    stage = p.get("delivery_stage")
    index = {"story_design": 1, "chapter_plan": 2, "assembly": 3}.get(stage) if isinstance(stage, str) else None
    valid_stage = index is not None and type(p.get("stage_index")) is int and p["stage_index"] == index
    result["delivery_stage"] = stage if valid_stage else None
    result["stage_index"] = index if valid_stage else None
    completed = p.get("completed_stages")
    result["completed_stages"] = completed if type(completed) is int and 0 <= completed <= 3 else 0
    status = p.get("current_stage_status")
    result["current_stage_status"] = status if valid_stage and isinstance(status, str) and status in {"started", "completed", "reused"} else None
    if "last_turn_content_at" not in p:
        result["last_turn_content_at"] = result["last_content_at"]
    # Validate again at the API boundary, including data read from disk.
    from .workshop_diagnostics import _safe_event
    safe = _safe_event({"event": "upstream.event", "request_id": p.get("request_id"),
                        "event_kind": p.get("upstream_event"), "upstream_event": p.get("upstream_event")})
    result["request_id"] = safe.get("request_id")
    result["upstream_event"] = next((safe[key] for key in ("event_kind", "upstream_event")
                                      if safe.get(key) not in {None, "other"}), None)
    return result
