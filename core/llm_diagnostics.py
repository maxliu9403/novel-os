"""Content-free progress observations for a single Codex CLI process.

``Popen.communicate`` returns cumulative output after each timeout. This module
consumes only each new suffix, and projects JSONL events onto a fixed metadata
schema. Prompts, generated text, reasoning, commands, and raw errors never enter
the emitted records.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from uuid import UUID


_CLI_EVENTS = frozenset({
    "thread.started", "turn.started", "turn.completed", "turn.failed", "error",
    "item.started", "item.updated", "item.completed",
})
_ITEM_TYPES = frozenset({
    "agent_message", "reasoning", "command_execution", "file_change",
    "mcp_tool_call", "web_search", "todo_list", "error",
})
_TERMINAL_EVENTS = frozenset({
    "cli.completed", "cli.timeout", "cli.cancelled", "cli.failed",
    "completed", "timeout", "cancelled", "failed", "finished",
    "process_completed", "process_failed",
})
_ERROR_CATEGORIES = frozenset({
    "authentication", "rate_limit", "timeout", "transport", "upstream",
    "http_client_error", "cli_error",
})
_USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens")
_MAX_LINE_BYTES = 64 * 1024
# A completed skeleton can exceed 64 KiB in one agent_message JSONL event.
# Permit a bounded 1 MiB stdout event while keeping noisy stderr at 64 KiB.
_MAX_MODEL_EVENT_BYTES = 1024 * 1024
_MAX_ERROR_CHARS = 4096
_MAX_COUNTER = 2**63 - 1
_HTTP_STATUS = re.compile(
    r"\b(?:HTTP(?:/\d(?:\.\d)?)?(?:\s+status)?|"
    r"(?:unexpected\s+)?status(?:_code|\s+code)?)"
    r"\s*[:=]?\s*([45]\d{2})\b", re.IGNORECASE,
)
_RECONNECT = re.compile(
    r"\b(?:reconnecting|retrying)(?:\s+(?:request|stream))?"
    r"[\s.:]*(?:attempt\s*)?(\d{1,3})\s*/\s*(\d{1,3})\b",
    re.IGNORECASE,
)


class _BoundedLines:
    """Discard an oversized line until its newline, then resume normally."""

    def __init__(self, consume: Callable[[bytes], None], max_line_bytes: int = _MAX_LINE_BYTES):
        self._consume = consume
        self._max_line_bytes = max_line_bytes
        self.buffer = bytearray()
        self.discarding = False
        self.discarded_lines = 0

    def feed(self, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            newline = data.find(b"\n", offset)
            end = newline if newline >= 0 else len(data)
            if not self.discarding:
                if len(self.buffer) + end - offset > self._max_line_bytes:
                    self.buffer.clear()
                    self.discarding = True
                    self.discarded_lines += 1
                else:
                    self.buffer.extend(data[offset:end])
            if newline < 0:
                return
            self.flush()
            offset = newline + 1

    def flush(self) -> None:
        if self.buffer and not self.discarding:
            self._consume(bytes(self.buffer))
        self.buffer.clear()
        self.discarding = False


class CLIDiagnostics:
    """Best-effort, allowlisted metadata observer; callback failures are ignored.

    ``first_output_seconds`` measures process I/O, not the first model token.
    ``first_cli_event_seconds`` separately measures the first recognized JSONL
    event. Call ``heartbeat`` from the existing process polling loop; it emits
    at most once per ``heartbeat_seconds`` without another diagnostic event.
    """

    def __init__(
        self, emit: Callable[[dict], None], heartbeat_seconds: float = 30,
    ):
        self._emit = emit
        self._heartbeat_seconds = (
            heartbeat_seconds if math.isfinite(heartbeat_seconds) and heartbeat_seconds > 0
            else 30
        )
        self._started_at = time.monotonic()
        self._last_emitted_at = self._started_at
        self._pid: int | None = None
        self._stdout_bytes = 0
        self._stderr_bytes = 0
        self._first_output_at: float | None = None
        self._last_output_at: float | None = None
        self._input_sent_at: float | None = None
        self._first_cli_event_at: float | None = None
        self._last_cli_event: str | None = None
        self._last_item_type: str | None = None
        self._thread_id: str | None = None
        self._usage: dict[str, int] = {}
        self._usage_turns: dict[int, dict[str, int]] = {}
        self._usage_turn = 0
        self._usage_incomplete_fields: list[str] = []
        self._model_output_chars = 0
        self._message_lengths: dict[str, int] = {}
        self._first_model_output_at: float | None = None
        self._last_model_output_at: float | None = None
        self._last_model_output_emitted_at: float | None = None
        self._retry_count = 0
        self._http_status: int | None = None
        self._error_category: str | None = None
        self._finished: dict | None = None
        self._stdout_lines = _BoundedLines(self._observe_json_line, _MAX_MODEL_EVENT_BYTES)
        self._stderr_lines = _BoundedLines(self._observe_stderr_line)

    def start(self, pid: int) -> None:
        if self._finished is not None:
            return
        self._pid = pid if type(pid) is int and pid > 0 else None
        self._publish("process_started")

    def observe(self, stdout: bytes | str | None, stderr: bytes | str | None) -> None:
        """Consume cumulative snapshots, including mixed bytes/text snapshots."""
        if self._finished is not None:
            return
        for stream, cumulative, lines in (
            ("stdout", stdout, self._stdout_lines),
            ("stderr", stderr, self._stderr_lines),
        ):
            if cumulative is None:
                continue
            data = cumulative.encode("utf-8", errors="replace") if isinstance(cumulative, str) else cumulative
            if not isinstance(data, bytes):
                continue
            counter = f"_{stream}_bytes"
            previous = getattr(self, counter)
            if len(data) <= previous:
                continue
            setattr(self, counter, len(data))
            now = time.monotonic()
            self._last_output_at = now
            if self._first_output_at is None:
                self._first_output_at = now
                self._publish("first_output", output_stream=stream)
            lines.feed(data[previous:])

    def input_sent(self) -> None:
        """Entire input is available on stdin; CLI/model consumption is unknown."""
        if self._finished is None and self._input_sent_at is None:
            self._input_sent_at = time.monotonic()
            self._publish("stdin_sent")

    def heartbeat(self) -> None:
        if self._finished is None and time.monotonic() - self._last_emitted_at >= self._heartbeat_seconds:
            self._publish("heartbeat")

    def finish(self, event: str, **fields) -> dict:
        """Flush an unterminated last line and emit a safe terminal snapshot."""
        if self._finished is not None:
            return dict(self._finished)
        self._stdout_lines.flush()
        self._stderr_lines.flush()
        safe_fields = {}
        for key in (
            "returncode", "return_code", "exit_code", "output_chars", "output_bytes",
            "final_output_bytes",
        ):
            value = fields.get(key)
            if type(value) is int and -_MAX_COUNTER <= value <= _MAX_COUNTER:
                safe_fields[key] = value
        value = fields.get("timeout_seconds")
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            safe_fields["timeout_seconds"] = value
        if type(fields.get("final_output_exists")) is bool:
            safe_fields["final_output_exists"] = fields["final_output_exists"]
        if isinstance(fields.get("error_category"), str) and fields["error_category"] in _ERROR_CATEGORIES:
            safe_fields["error_category"] = fields["error_category"]
        self._finished = self._publish(
            event if isinstance(event, str) and event in _TERMINAL_EVENTS else "finished", **safe_fields,
        )
        return dict(self._finished)

    def _snapshot(self) -> dict:
        now = time.monotonic()

        def elapsed(instant: float | None) -> float | None:
            return round(max(0, instant - self._started_at), 3) if instant is not None else None

        if self._last_cli_event in {"turn.completed", "turn.failed"}:
            phase = self._last_cli_event.replace(".", "_")
        elif self._last_cli_event is not None:
            phase = "cli_active"
        elif self._input_sent_at is not None:
            phase = "awaiting_cli_event"
        elif self._pid is not None:
            phase = "input_transfer"
        else:
            phase = "startup"
        return {
            "pid": self._pid,
            "phase": phase,
            "elapsed_seconds": elapsed(now),
            "stdout_bytes": self._stdout_bytes,
            "stderr_bytes": self._stderr_bytes,
            "first_output_seconds": elapsed(self._first_output_at),
            "last_output_seconds": elapsed(self._last_output_at),
            "idle_seconds": round(max(0, now - (
                self._last_output_at if self._last_output_at is not None else self._started_at
            )), 3),
            "first_cli_event_seconds": elapsed(self._first_cli_event_at),
            "input_sent_seconds": elapsed(self._input_sent_at),
            "last_cli_event": self._last_cli_event,
            "last_item_type": self._last_item_type,
            "retry_count": self._retry_count,
            "http_status": self._http_status,
            "error_category": self._error_category,
            "thread_id": self._thread_id,
            "usage": dict(self._usage),
            "usage_scope": "request_total",
            "usage_incomplete_fields": self._usage_incomplete_fields,
            "model_output_chars": self._model_output_chars,
            "first_model_output_seconds": elapsed(self._first_model_output_at),
            "last_model_output_seconds": elapsed(self._last_model_output_at),
            "discarded_stdout_lines": self._stdout_lines.discarded_lines,
            "discarded_stderr_lines": self._stderr_lines.discarded_lines,
        }

    def _publish(self, event: str, **fields) -> dict:
        record = {"event": event, **self._snapshot(), **fields}
        self._last_emitted_at = time.monotonic()
        try:
            # A consumer may mutate its argument. Never let it change our state.
            self._emit({**record, "usage": dict(record["usage"])})
        except Exception:
            pass
        return record

    def _observe_json_line(self, line: bytes) -> None:
        try:
            payload = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            return
        if not isinstance(payload, dict):
            return
        event = payload.get("type")
        if not isinstance(event, str) or event not in _CLI_EVENTS:
            return
        item = payload.get("item")
        item_type = item.get("type") if isinstance(item, dict) else None
        if not isinstance(item_type, str) or item_type not in _ITEM_TYPES:
            item_type = None
        previous = (self._last_cli_event, self._last_item_type)
        previous_error = (self._retry_count, self._http_status, self._error_category)
        self._last_cli_event = event
        self._last_item_type = item_type
        if event == "thread.started":
            thread_id = payload.get("thread_id")
            if isinstance(thread_id, str) and len(thread_id) == 36:
                try:
                    self._thread_id = str(UUID(thread_id))
                except ValueError:
                    pass
        usage = payload.get("usage")
        if event == "turn.started" and previous[0] != "turn.started":
            self._usage_turn += 1
        if event == "turn.completed" and isinstance(usage, dict):
            self._usage_turns[self._usage_turn] = {
                key: value for key in _USAGE_KEYS
                if type(value := usage.get(key)) is int and 0 <= value <= _MAX_COUNTER
            }
            # Replace a repeated completion snapshot for the same turn; sum
            # only across explicit turn boundaries. Never add cached input to
            # input. Retain partial observed sums, with explicit incompleteness.
            self._usage = {key: sum(turn.get(key, 0) for turn in self._usage_turns.values())
                           for key in _USAGE_KEYS
                           if any(key in turn for turn in self._usage_turns.values())}
            self._usage_incomplete_fields = [key for key in _USAGE_KEYS
                                            if not all(key in turn for turn in self._usage_turns.values())]
            if {"input_tokens", "output_tokens"}.intersection(self._usage_incomplete_fields):
                self._usage_incomplete_fields.append("total_tokens")
        if event in {"error", "turn.failed"}:
            error = payload.get("error")
            status = None
            if isinstance(error, dict):
                message = error.get("message")
                status = error.get("status_code", error.get("status"))
            else:
                message = payload.get("message")
            if isinstance(message, str):
                self._classify_error(message[:_MAX_ERROR_CHARS], emit=False)
            if type(status) is int and 400 <= status <= 599:
                self._record_status(status)
            if self._error_category is None:
                self._error_category = "cli_error"
        if self._first_cli_event_at is None:
            self._first_cli_event_at = time.monotonic()
            self._publish("first_cli_event")
        elif previous != (self._last_cli_event, self._last_item_type):
            self._publish("cli_event")
        elif previous_error != (self._retry_count, self._http_status, self._error_category):
            self._publish("retry" if self._retry_count != previous_error[0] else "cli_error")

        if item_type == "agent_message" and isinstance(item.get("text"), str):
            # Count visible message snapshots only. Reasoning text never counts
            # as user-visible output, and cumulative updates are not double-counted.
            message_id = item.get("id")
            if not isinstance(message_id, str) or len(message_id) > 256:
                message_id = "__legacy_message__"
            if message_id not in self._message_lengths and len(self._message_lengths) >= 256:
                return
            length = len(item["text"])
            previous_length = self._message_lengths.get(message_id, 0)
            if length > previous_length:
                self._message_lengths[message_id] = length
                self._model_output_chars += length - previous_length
                now = time.monotonic()
                self._last_model_output_at = now
                if self._first_model_output_at is None:
                    self._first_model_output_at = now
                if (self._last_model_output_emitted_at is None
                        or now - self._last_model_output_emitted_at >= 1
                        or event == "item.completed"):
                    self._last_model_output_emitted_at = now
                    self._publish("cli.model_output")

    def _observe_stderr_line(self, line: bytes) -> None:
        self._classify_error(line[:_MAX_ERROR_CHARS].decode("utf-8", errors="replace"))

    def _record_status(self, status: int) -> None:
        self._http_status = status
        if status in {401, 403}:
            self._error_category = "authentication"
        elif status == 429:
            self._error_category = "rate_limit"
        elif status in {408, 504}:
            self._error_category = "timeout"
        elif status >= 500:
            self._error_category = "upstream"
        else:
            self._error_category = "http_client_error"

    def _classify_error(self, message: str, *, emit: bool = True) -> None:
        previous = (self._retry_count, self._http_status, self._error_category)
        lowered = message.lower()
        retry = _RECONNECT.search(message)
        if retry:
            attempt, total = int(retry[1]), int(retry[2])
            if 0 < attempt <= total:
                # Codex can mirror the same numbered reconnect in both streams.
                self._retry_count = max(self._retry_count, attempt)
        status = _HTTP_STATUS.search(message)
        if status:
            self._record_status(int(status[1]))
        elif any(phrase in lowered for phrase in (
            "stream disconnected", "connection reset", "connection refused",
            "transport error", "network error", "error sending request", "dns error",
        )):
            self._error_category = "transport"
        elif any(phrase in lowered for phrase in ("timed out", "timeout exceeded", "request timeout")):
            self._error_category = "timeout"
        elif any(phrase in lowered for phrase in ("rate limit exceeded", "rate_limit_exceeded")):
            self._error_category = "rate_limit"
        if emit and previous != (self._retry_count, self._http_status, self._error_category):
            self._publish("retry" if retry else "cli_error")
