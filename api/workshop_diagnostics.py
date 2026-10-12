"""Private, bounded metadata logs for workshop model requests.

These JSONL files live beside session.json (under the persistent /data volume
in deployment), not in session.json or the public API. Operators may read them
to correlate a revision, job, format attempt and request without replaying a
model request. No prompt, model output, raw exception, credentials or private
reasoning is accepted. Unknown fields and provider-defined event names are
dropped or normalized. Diagnostics are best effort and never control inference.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
from typing import Callable
import weakref

from .workshop_progress import persist_progress, runtime_progress  # noqa: F401


MAX_FILE_BYTES = 5 * 1024 * 1024
TERMINAL_RESERVE_BYTES = 64 * 1024
MAX_RECORD_BYTES = 4096
_LOCKS = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()

_EVENTS = frozenset({
    "request.start", "request.completed", "request.failed", "request.cancelled", "request.timeout",
    "process_started", "stdin_sent", "first_output", "first_cli_event", "cli_event", "cli_error", "retry", "heartbeat",
    "cli.completed", "cli.timeout", "cli.cancelled", "cli.failed",
    "attempt.start", "attempt.completed", "attempt.failed", "attempt.cancelled", "attempt.timeout",
    "validation.failure", "validation.start", "contract.normalized", "format_retry", "diagnostics.capped", "profile.switch",
    "cli.model_output", "upstream.observer_started", "upstream.observer_unavailable",
    "upstream.observer_stopped", "upstream.event", "upstream.activity",
    "delivery.stage.started", "delivery.stage.completed", "delivery.stage.reused",
})
_TERMINALS = frozenset(event for event in _EVENTS if event.endswith(
    (".completed", ".failed", ".cancelled", ".timeout")))
_CLI_EVENTS = frozenset({
    "thread.started", "turn.started", "turn.completed", "turn.failed", "item.started",
    "item.updated", "item.completed", "error", "retry", "message_start", "message_delta",
    "message_stop", "content_block_start", "content_block_delta", "content_block_stop",
    "result", "system", "assistant", "user", "rate_limit_event", "stream_event", "ping",
})
_ITEM_TYPES = frozenset({
    "agent_message", "reasoning", "command_execution", "file_change", "mcp_tool_call",
    "web_search", "todo_list", "error", "message", "text", "thinking", "tool_use", "tool_result",
})
_ERROR_CATEGORIES = frozenset({
    "timeout", "cancelled", "provider_error", "initialization_error", "json_decode",
    "response_schema", "skeleton_contract", "input_revision", "titles_schema", "authentication",
    "authorization", "rate_limit", "server_error", "network", "connection", "http_error",
    "process_error", "process_exit", "empty_output", "invalid_output", "cli_error", "unknown",
    "transport", "upstream", "http_client_error",
})
_PHASES = frozenset({"initialization", "completion", "response_parse", "skeleton_validation",
                    "input_revision_validation", "titles_parse", "titles_validation", "startup",
                    "input_transfer", "awaiting_cli_event", "cli_active", "turn_completed", "turn_failed"})
_NUMERIC_FIELDS = frozenset({
    "timeout_seconds", "system_chars", "system_bytes", "user_chars", "user_bytes", "max_tokens",
    "elapsed_seconds", "pid", "stdout_bytes", "stderr_bytes", "first_output_seconds",
    "last_output_seconds", "idle_seconds", "first_cli_event_seconds", "retry_count",
    "http_status", "final_output_bytes", "dropped_events", "discarded_stdout_lines", "discarded_stderr_lines",
    "input_sent_seconds", "output_chars", "output_bytes",
    "configured_max_tokens", "effective_max_tokens", "model_output_chars", "first_model_output_seconds",
    "last_model_output_seconds",
    "duration_ms", "ttft_ms", "observed_seconds", "upstream_attempt", "input_token_count",
    "output_token_count", "cached_token_count", "reasoning_token_count", "tool_token_count",
    "cache_write_token_count", "normalized_fields", "dropped_event_count",
})
_USAGE_FIELDS = frozenset({"input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
                          "cached_tokens", "reasoning_tokens", "cache_read_input_tokens",
                          "cache_creation_input_tokens"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_MODEL = re.compile(r"[A-Za-z0-9_.:/-]{1,200}\Z")
_DELIVERY_STAGES = {"story_design": 1, "chapter_plan": 2, "assembly": 3}


def _identifier(value) -> str | None:
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else None


def _timestamp(value):
    if isinstance(value, str) and len(value) <= 40:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None and 2000 <= parsed.year <= 2200:
                return value
        except ValueError:
            pass
    return None


def _safe_event(event: dict) -> dict:
    """Allow metadata by both field and value shape; never stringify unknown objects."""
    if not isinstance(event, dict) or not isinstance(event.get("event"), str) or event["event"] not in _EVENTS:
        return {}
    safe = {"event": event["event"]}
    if event["event"] == "upstream.activity":
        # Stream-event counters are not token usage or body lengths. Keep this
        # projection separate so even a malformed caller cannot mix the two.
        count, observed = event.get("metric_count"), _timestamp(event.get("observed_at"))
        kind, temporality = event.get("activity_kind"), event.get("metric_temporality")
        if (not isinstance(kind, str) or kind not in {"reasoning_summary", "text", "transport"}
                or type(count) is not int or not 1 <= count <= 2**63 - 1
                or not isinstance(temporality, str) or temporality not in {"delta", "cumulative"}
                or event.get("observation_source") != "otel_metrics" or observed is None):
            return {}
        safe.update(activity_kind=event["activity_kind"], metric_count=count,
                    metric_temporality=event["metric_temporality"], observed_at=observed,
                    observation_source="otel_metrics")
        for key in ("metric_window_start", "metric_window_end"):
            stamp = _timestamp(event.get(key))
            if stamp is not None:
                safe[key] = stamp
        request_id = _identifier(event.get("request_id"))
        if request_id:
            safe["request_id"] = request_id
        return safe
    if event["event"].startswith("delivery.stage."):
        stage, index, completed = event.get("delivery_stage"), event.get("stage_index"), event.get("completed_stages")
        if (not isinstance(stage, str) or stage not in _DELIVERY_STAGES
                or type(index) is not int or index != _DELIVERY_STAGES[stage]
                or type(completed) is not int or not 0 <= completed <= 3):
            return {}
        safe.update(delivery_stage=stage, stage_index=index, completed_stages=completed)
        return safe
    for key in _NUMERIC_FIELDS:
        value = event.get(key)
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1e18:
            safe[key] = value
    if type(event.get("exit_code")) is int and -65536 <= event["exit_code"] <= 65536:
        safe["exit_code"] = event["exit_code"]
    if type(event.get("final_output_exists")) is bool:
        safe["final_output_exists"] = event["final_output_exists"]
    for key in ("max_tokens_supported", "success"):
        if type(event.get(key)) is bool:
            safe[key] = event[key]
    if "effective_max_tokens" in event and event["effective_max_tokens"] is None:
        safe["effective_max_tokens"] = None
    source_timestamp = _timestamp(event.get("source_timestamp"))
    if source_timestamp is not None:
        safe["source_timestamp"] = source_timestamp
    for key in ("request_id", "thread_id", "provider", "error_type"):
        value = _identifier(event.get(key))
        if value is not None:
            safe[key] = value
    value = event.get("model")
    if isinstance(value, str) and _MODEL.fullmatch(value):
        safe["model"] = value
    for key, allowed in (("last_cli_event", _CLI_EVENTS), ("last_item_type", _ITEM_TYPES),
                         ("error_category", _ERROR_CATEGORIES), ("phase", _PHASES),
                         ("output_stream", {"stdout", "stderr"}),
                         ("prompt_profile", {"exploration", "delivery"}),
                         ("narrative_mode", {"short_novel", "standalone_long", "multi_volume", "series_installment"}),
                         ("timeout_scope", {"local_process", "sdk_request", "request"}),
                         ("endpoint_category", {"models", "responses", "usage", "instructions", "other"}),
                         ("upstream_event", {"codex.conversation_starts", "codex.api_request", "codex.sse_event",
                                             "codex.websocket_request", "codex.websocket_event", "codex.turn_ttft"}),
                         ("event_kind", {"response.created", "response.queued", "response.in_progress", "response.completed",
                                         "response.failed", "response.incomplete", "response.cancelled", "response.output_text.delta",
                                         "response.output_text.done", "response.output_item.added",
                                         "response.output_item.done", "response.content_part.added",
                                         "response.content_part.done", "response.reasoning_summary_text.delta",
                                         "response.reasoning_summary_text.done", "response.reasoning_summary_part.added",
                                         "response.reasoning_summary_part.done", "response.reasoning_text.delta",
                                         "response.reasoning_text.done", "error"}),
                         ("reasoning_summary", {"none", "auto", "concise", "detailed"}),
                         ("reasoning_effort", {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})):
        if key in event and event[key] is not None:
            safe[key] = event[key] if isinstance(event[key], str) and event[key] in allowed else "other"
    usage = event.get("usage")
    if event.get("usage_scope") == "request_total":
        safe["usage_scope"] = "request_total"
        safe["usage_incomplete_fields"] = [key for key in event.get("usage_incomplete_fields", [])
                                          if key in {*_USAGE_FIELDS, "total_tokens"}]
    if isinstance(usage, dict):
        safe["usage"] = {key: value for key, value in usage.items()
                         if key in _USAGE_FIELDS and type(value) is int and 0 <= value <= 10**18}
    return safe


def diagnostic_error_category(exc: Exception) -> str:
    """Classify exceptions without inspecting or persisting their text."""
    from core.llm_client import LLMCancelledError

    current = exc
    for _ in range(8):
        if isinstance(current, LLMCancelledError):
            return "cancelled"
        if isinstance(current, (TimeoutError, subprocess.TimeoutExpired)) or type(current).__name__ in {
            "APITimeoutError", "ReadTimeout", "ConnectTimeout",
        }:
            return "timeout"
        current = current.__cause__
        if current is None:
            break
    return "provider_error"


def make_diagnostic_callback(session: dict, *, attempt: int, operation: str,
                             route: dict | None = None) -> Callable[[dict], None] | None:
    """Bind immutable session metadata; snapshots opt in using _diagnostic_dir.

    The directory and files are restricted to 0700/0600. A per-path thread lock
    and flock serialize O_APPEND writes across clients and worker processes.
    Ordinary events stop before the reserved terminal tail. If even that tail
    fills, a terminal event replaces only the tail, retaining the initial trace
    and guaranteeing room for the latest outcome within the same 5 MiB cap.
    """
    directory = session.get("_diagnostic_dir")
    if directory is None:
        return None
    revision = session.get("revision")
    if type(revision) is not int or revision < 0 or operation not in {"brainstorm", "titles"}:
        return None
    if type(attempt) is not int or not 1 <= attempt <= 12:
        return None
    directory = Path(directory)
    path = directory / f"{revision}-{operation}.jsonl"
    context = {"session_id": _identifier(session.get("id")),
               "task_id": _identifier(session.get("task_id") or session.get("id")),
               "job_id": _identifier(session.get("job_id")), "revision": revision,
               "attempt": attempt, "operation": operation, "route": "workshop"}
    baseline = _safe_event({"event": "attempt.start", **(route or {})})
    baseline.pop("event", None)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(str(path.absolute()))
        if lock is None:
            lock = threading.Lock()
            _LOCKS[str(path.absolute())] = lock
    request_id = None
    dropped = 0
    cap_reported = False

    def append(event: dict) -> None:
        nonlocal request_id, dropped, cap_reported
        # A diagnostics disk failure must not discard a completed model answer.
        try:
            safe = _safe_event(event)
            if not safe:
                return
            with lock:
                supplied_request = safe.get("request_id")
                if supplied_request and (safe["event"] == "request.start" or request_id is None):
                    request_id = supplied_request
                record = {**baseline, **safe, **context,
                          "timestamp": datetime.now(timezone.utc).isoformat()}
                if supplied_request is not None or request_id is not None:
                    record["request_id"] = supplied_request or request_id
                if dropped:
                    record["dropped_events"] = dropped
                payload = (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
                if len(payload) > MAX_RECORD_BYTES:
                    return
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fchmod(directory_fd, 0o700)
                    fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory_fd)
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX)
                        os.fchmod(fd, 0o600)
                        size = os.fstat(fd).st_size
                        # The small projection must keep advancing even when
                        # the append log has reached its independent size cap.
                        persist_progress(directory_fd, f"{revision}-{operation}.progress.json", record)
                        ordinary_limit = MAX_FILE_BYTES - TERMINAL_RESERVE_BYTES
                        terminal = record["event"] in _TERMINALS
                        if not terminal and size + len(payload) > ordinary_limit:
                            dropped += 1
                            if cap_reported:
                                return
                            cap_reported = True
                            record = {**context, "event": "diagnostics.capped",
                                      "timestamp": record["timestamp"], "dropped_events": dropped}
                            if request_id is not None:
                                record["request_id"] = request_id
                            payload = (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
                            # One marker per attempt may use reserved space, while
                            # still leaving at least one full terminal record free.
                            if size + len(payload) > MAX_FILE_BYTES - MAX_RECORD_BYTES:
                                return
                        if terminal and size + len(payload) > MAX_FILE_BYTES:
                            # Retain complete initial lines and release the bounded tail.
                            prefix = os.pread(fd, ordinary_limit, 0)
                            boundary = prefix.rfind(b"\n") + 1
                            os.ftruncate(fd, boundary)
                            dropped += 1
                            record["dropped_events"] = dropped
                            record["tail_compacted"] = True
                            payload = (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
                        view = memoryview(payload)
                        while view:
                            written = os.write(fd, view)
                            if written <= 0:
                                raise OSError("Incomplete diagnostic append")
                            view = view[written:]
                        if terminal:
                            os.fsync(fd)
                    finally:
                        os.close(fd)
                finally:
                    os.close(directory_fd)
        except Exception:
            return

    return append
