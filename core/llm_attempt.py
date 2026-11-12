"""Opt-in, request-bound durable completion evidence.

A successful adapter return is the completion boundary. Parseable JSON, partial
CLI output and local process termination are evidence, never successful results.
Private artifacts stay on disk; observers receive only allowlisted metadata.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import threading
from uuid import uuid4
try:
    from .codex_diagnostic_codes import ERROR_CODES, COMPONENTS, LEVELS
except ImportError:
    from codex_diagnostic_codes import ERROR_CODES, COMPONENTS, LEVELS

sys.modules.setdefault("llm_attempt" if __name__ == "core.llm_attempt" else "core.llm_attempt", sys.modules[__name__])
_current = ContextVar("llm_completion_attempt", default=None)
_EVENTS = frozenset("""request.start request.completed request.failed request.timeout request.cancelled request.incomplete
process_started process_stopped stdin_sent first_output first_cli_event heartbeat cli_event retry cli_error
cli.model_output cli.diagnostic cli.completion_window cli.completed cli.timeout cli.cancelled cli.failed
upstream.event upstream.observer_started upstream.observer_stopped upstream.observer_unavailable
upstream.metrics upstream.activity content.progress provider.stream_chunk""".split())
_ENUMS = {
    "phase": {"startup", "input_transfer", "awaiting_cli_event", "cli_active", "turn_completed", "turn_failed"},
    "provider": {"codex", "claude_cli", "openai", "anthropic", "gemini", "gemini_openai", "openai_compatible", "azure"},
    "reasoning_effort": {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"},
    "reasoning_summary": {"none", "auto", "concise", "detailed"},
    "timeout_scope": {"local_process", "sdk_request"},
    "last_cli_event": {"thread.started", "turn.started", "turn.completed", "turn.failed", "error", "item.started", "item.updated", "item.completed"},
    "last_item_type": {"agent_message", "reasoning", "command_execution", "file_change", "mcp_tool_call", "web_search", "todo_list", "error"},
    "error_category": {"authentication", "rate_limit", "timeout", "transport", "upstream", "http_client_error", "cli_error"},
    "usage_scope": {"request_total"}, "output_stream": {"stdout", "stderr"},
    "diagnostic_code": ERROR_CODES, "diagnostic_component": COMPONENTS, "diagnostic_level": LEVELS,
    "activity_kind": {"text", "reasoning_summary", "transport", "error"},
    "metric_temporality": {"delta", "cumulative"}, "observation_source": {"otel_metrics"},
    "finish_reason": {"stop", "end_turn", "stop_sequence", "length", "max_tokens", "content_filter", "tool_calls", "tool_use", "unknown"},
    "endpoint_category": {"models", "responses", "usage", "instructions", "other"},
    "upstream_event": {"codex.conversation_starts", "codex.api_request", "codex.sse_event", "codex.websocket_request", "codex.websocket_event", "codex.turn_ttft"},
    "event_kind": {"response." + suffix for suffix in (
        "created", "queued", "in_progress", "completed", "failed", "incomplete", "cancelled",
        "output_item.added", "output_item.done", "content_part.added", "content_part.done",
        "output_text.delta", "output_text.done", "reasoning_summary_text.delta", "reasoning_summary_text.done",
        "reasoning_summary_part.added", "reasoning_summary_part.done", "reasoning_text.delta", "reasoning_text.done")} | {"error"},
}
_NUMBERS = frozenset("""pid elapsed_seconds timeout_seconds max_tokens configured_max_tokens effective_max_tokens
completion_grace_limit completion_grace_seconds system_chars user_chars system_bytes user_bytes output_chars output_bytes
stdout_bytes stderr_bytes first_output_seconds last_output_seconds idle_seconds first_cli_event_seconds input_sent_seconds
model_output_chars first_model_output_seconds last_model_output_seconds discarded_stdout_lines discarded_stderr_lines
retry_count http_status final_output_bytes duration_ms ttft_ms upstream_attempt dropped_event_count
input_token_count output_token_count cached_token_count cache_write_token_count reasoning_token_count tool_token_count metric_count""".split())
_BOOLEANS = {"max_tokens_supported", "final_output_exists", "success", "local_process_stopped"}
_USAGE = {"input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "cache_creation_input_tokens", "total_tokens"}


def safe_diagnostic(entry: dict) -> dict:
    """Never forward arbitrary strings, error bodies, paths, URLs or content."""
    result = ({"event": entry["event"], "time": datetime.now(timezone.utc).isoformat()}
              if isinstance(entry.get("event"), str) and entry["event"] in _EVENTS else {})
    if not result:
        return result
    for key, allowed in _ENUMS.items():
        if isinstance(entry.get(key), str) and entry[key] in allowed:
            result[key] = entry[key]
    for key in _NUMBERS:
        value = entry.get(key)
        if type(value) in (float, int) and 0 <= value <= 2**63 - 1 and math.isfinite(value):
            result[key] = value
    for key in ("exit_code", "returncode", "return_code"):
        if type(entry.get(key)) is int and -255 <= entry[key] <= 255:
            result[key] = entry[key]
    for key in _BOOLEANS:
        if type(entry.get(key)) is bool:
            result[key] = entry[key]
    for key in ("thread_id", "request_id"):
        if isinstance(entry.get(key), str) and re.fullmatch(r"[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", entry[key]):
            result[key] = entry[key]
    if isinstance(entry.get("usage"), dict):
        result["usage"] = {key: value for key, value in entry["usage"].items()
                           if key in _USAGE and type(value) is int and 0 <= value <= 2**63 - 1}
    if isinstance(entry.get("usage_incomplete_fields"), (list, tuple)):
        result["usage_incomplete_fields"] = [key for key in entry["usage_incomplete_fields"] if isinstance(key, str) and key in _USAGE]
    if "error_type" in entry:
        result["error_type"] = entry["error_type"] if isinstance(entry["error_type"], str) and entry["error_type"] in {
            "LLMError", "LLMCancelledError", "TimeoutExpired", "TimeoutError", "APITimeoutError",
            "ReadTimeout", "ConnectTimeout", "ConnectionError", "OSError", "ValueError", "PipelineStopped", "LLMIncompleteResponseError",
        } else "Error"
    return result


def _json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


def _directory_fd(directory: Path, *, create=False):
    """Walk anchored directory descriptors; never follow a symlink component."""
    path = Path(os.path.abspath(directory))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return path, fd
    except BaseException:
        os.close(fd)
        raise


def _open(fd, name, flags):
    child = os.open(name, flags | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    info = os.fstat(child)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(child)
        raise ValueError("Attempt artifact is not a private regular file")
    return child


def _read(fd, name):
    with os.fdopen(_open(fd, name, os.O_RDONLY), "rb") as stream:
        return stream.read()


def _atomic(fd, name, data):
    # Reject existing links before replacement; the replacement itself is
    # atomic and anchored to the original directory even if paths are renamed.
    try:
        old = _open(fd, name, os.O_RDONLY)
    except FileNotFoundError:
        pass
    else:
        os.close(old)
    temporary = f".{name}.{uuid4().hex}.tmp"
    try:
        with os.fdopen(_open(fd, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


def _recover(fd, identity):
    manifest = json.loads(_read(fd, "attempt.json"))
    if manifest != {"schema_version": 1, "identity": identity}:
        raise ValueError("Completion attempt identity mismatch")
    try:
        receipt = json.loads(_read(fd, "completion.json"))
    except FileNotFoundError:
        return None
    if (not isinstance(receipt, dict) or receipt.get("identity") != identity
            or receipt.get("schema_version") != 1 or receipt.get("proof") != "adapter_return"):
        raise ValueError("Completion receipt identity or proof mismatch")
    if receipt.get("request_sha256") is not None:
        request = json.loads(_read(fd, "request.json"))
        if request != {"identity": identity, "request_sha256": receipt["request_sha256"]}:
            raise ValueError("Completion request checksum mismatch")
    raw = _read(fd, "response.txt")
    if receipt.get("sha256") != _digest(raw) or receipt.get("bytes") != len(raw):
        raise ValueError("Completion response checksum mismatch")
    return raw.decode("utf-8")


def recover_completion(directory: Path, identity: str) -> str | None:
    """Return only a committed, identity-checked complete response; never send."""
    try:
        _, fd = _directory_fd(directory)
    except FileNotFoundError:
        return None
    try:
        return _recover(fd, identity)
    finally:
        os.close(fd)


class CompletionAttempt:
    def __init__(self, directory, identity, observer=None):
        if not isinstance(identity, str) or not identity:
            raise ValueError("Completion attempt requires an immutable request identity")
        self.directory, self._fd = _directory_fd(Path(directory), create=True)
        self.identity, self.observer = identity, observer
        self._lock = threading.RLock()
        self._cli_fd = None
        self._closed = False
        self._stream_sizes = {}
        self._lease = None
        self._binding = None
        try:
            self._lease = _open(self._fd, "attempt.lock", os.O_WRONLY | os.O_CREAT)
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            manifest = {"schema_version": 1, "identity": identity}
            try:
                stored = json.loads(_read(self._fd, "attempt.json"))
            except FileNotFoundError:
                _atomic(self._fd, "attempt.json", _json(manifest))
            else:
                if stored != manifest:
                    raise ValueError("Completion attempt identity mismatch")
        except BaseException:
            self.close()
            raise

    def bind_request(self, system, user, *, provider, model, parameters=None):
        """Bind the exact full prompt; metadata never contains its source text."""
        binding = {"identity": self.identity, "request_sha256": _digest(_json({
            "system": system, "user": user, "provider": provider, "model": model, "parameters": parameters}))}
        with self._lock:
            self._ensure_open()
            try:
                previous = json.loads(_read(self._fd, "request.json"))
            except FileNotFoundError:
                _atomic(self._fd, "request.json", _json(binding))
                self._binding = binding
            else:
                if previous != binding:
                    raise ValueError("Completion attempt request changed")
                raise ValueError("Completion attempt was already dispatched; recover it or create a new attempt")

    def save_response(self, response: str):
        """Commit a normally returned adapter response before business parsing."""
        if not isinstance(response, str) or not response.strip():
            raise ValueError("A complete response must contain text")
        with self._lock:
            if self._closed:
                # An SDK may return after the caller's local wait ended. Acquire
                # a fresh identity-checked lease, never reuse a closed fd or
                # emit progress into a later run's observer.
                with completion_attempt(self.directory, self.identity) as late:
                    self._verify_late_binding(late)
                    late.save_response(response)
                return
            existing = _recover(self._fd, self.identity)
            if existing is not None:
                if existing != response:
                    raise ValueError("Completion attempt already contains a different response")
                return
            raw = response.encode("utf-8")
            try:
                binding = json.loads(_read(self._fd, "request.json"))
            except FileNotFoundError:
                binding = {}
            _atomic(self._fd, "response.txt", raw)
            _atomic(self._fd, "completion.json", _json({"schema_version": 1, "identity": self.identity,
                "sha256": _digest(raw), "bytes": len(raw), "proof": "adapter_return",
                "request_sha256": binding.get("request_sha256")}))

    def save_partial_response(self, response: str):
        """Keep received SDK text without a completion receipt."""
        if response:
            with self._lock:
                if self._closed:
                    with completion_attempt(self.directory, self.identity) as late:
                        self._verify_late_binding(late)
                        late.save_partial_response(response)
                    return
                _atomic(self._fd, "partial-response.txt", response.encode("utf-8"))

    def record_diagnostic(self, entry):
        record = safe_diagnostic(entry)
        if not record:
            return
        with self._lock:
            if self._closed:
                return
            with os.fdopen(_open(self._fd, "diagnostics.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND), "ab") as stream:
                stream.write(_json(record) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            _atomic(self._fd, "progress.json", _json(record))
        if self.observer is not None:
            try:
                self.observer(json.loads(_json(record)))
            except Exception:
                pass

    def cli_directory(self):
        with self._lock:
            self._ensure_open()
            if self._cli_fd is None:
                try:
                    os.mkdir("cli", 0o700, dir_fd=self._fd)
                except FileExistsError:
                    pass
                self._cli_fd = os.open("cli", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._fd)
            return self.directory / "cli"

    def read_cli_final(self):
        self.cli_directory()
        return _read(self._cli_fd, "last-message.txt").decode("utf-8")

    def sync_cli_evidence(self):
        """Retain a stopped child's output even when it is not complete."""
        with self._lock:
            self.cli_directory()
            try:
                final = _open(self._cli_fd, "last-message.txt", os.O_RDONLY)
            except FileNotFoundError:
                return
            try:
                os.fsync(final)
                os.fsync(self._cli_fd)
            finally:
                os.close(final)

    def capture_cli_output(self, stdout, stderr):
        """Preserve private cumulative streams separately from public metadata."""
        with self._lock:
            self.cli_directory()
            for name, value in (("stdout.jsonl", stdout), ("stderr.txt", stderr)):
                if value is None:
                    continue
                raw = value.encode("utf-8") if isinstance(value, str) else value
                if len(raw) > self._stream_sizes.get(name, 0):
                    _atomic(self._cli_fd, name, raw)
                    self._stream_sizes[name] = len(raw)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._cli_fd is not None:
                os.close(self._cli_fd)
            if self._lease is not None:
                os.close(self._lease)
            os.close(self._fd)

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("Completion attempt is closed")

    def _verify_late_binding(self, reopened):
        if self._binding is None or json.loads(_read(reopened._fd, "request.json")) != self._binding:
            raise ValueError("Late response requires the original dispatched request binding")


def current_attempt() -> CompletionAttempt | None:
    return _current.get()


@contextmanager
def completion_attempt(directory: Path, identity: str, observer=None):
    attempt = CompletionAttempt(directory, identity, observer)
    token = _current.set(attempt)
    try:
        yield attempt
    finally:
        _current.reset(token)
        attempt.close()
