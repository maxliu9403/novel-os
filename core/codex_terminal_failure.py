"""Conservative outcome evidence from a completed Codex JSONL invocation.

This is deliberately separate from diagnostic classification: transient errors,
stderr and model text are never proof that an upstream request failed. Callers
must supply stdout from the same, already stopped attempt and prefer any saved
complete response before consulting this parser.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import json
import re


MAX_STDOUT_BYTES = 8 * 1024 * 1024
MAX_EVENT_CHARS = 256 * 1024
PROVIDER_CODES = frozenset({"deactivated_workspace"})
_HTTP_ERROR = re.compile(r"^unexpected status ([45]\d{2})(?: [A-Za-z][A-Za-z -]{0,79})?:\s*")


@dataclass(frozen=True)
class CodexTerminalHTTPFailure:
    status_code: int
    provider_code: str = ""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Ambiguous JSON object")
        result[key] = value
    return result


def terminal_http_failure(
    stdout: str | bytes,
    *,
    exit_code: int,
    complete_response: bool = False,
) -> CodexTerminalHTTPFailure | None:
    """Return safe evidence only for an unambiguous terminal HTTP rejection.

    Ambiguous, malformed or oversized logs stay unknown instead of inspecting a
    truncated tail that could omit successful completion. A negative exit code
    means a locally killed process, not a verified upstream failure.
    """
    if type(exit_code) is not int or exit_code <= 0 or complete_response:
        return None
    if not isinstance(stdout, (str, bytes)) or len(stdout) > MAX_STDOUT_BYTES:
        return None
    if isinstance(stdout, bytes):
        try:
            stdout = stdout.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        if len(stdout.encode("utf-8")) > MAX_STDOUT_BYTES:
            return None
    except UnicodeEncodeError:
        return None

    terminal = None
    started = False
    for line in io.StringIO(stdout):
        if not line.strip():
            continue
        if len(line) > MAX_EVENT_CHARS:
            return None
        try:
            event = json.loads(line, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            return None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return None
        kind = event["type"]
        # exec is one turn. Conflicting, repeated or subsequent activity cannot
        # be assigned to this terminal error without further evidence.
        if terminal is not None or kind == "turn.completed":
            return None
        if kind == "turn.started":
            if started:
                return None
            started = True
        if kind == "turn.failed":
            terminal = event
    if terminal is None:
        return None
    error = terminal.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str):
        return None
    match = _HTTP_ERROR.match(message)
    if match is None:
        return None

    provider_code = ""
    try:
        body, _ = json.JSONDecoder(object_pairs_hook=_unique_object).raw_decode(message[match.end():])
    except (ValueError, RecursionError):
        body = None
    if isinstance(body, dict):
        detail = body.get("detail")
        code = detail.get("code") if isinstance(detail, dict) else None
        if isinstance(code, str) and code in PROVIDER_CODES:
            provider_code = code
    return CodexTerminalHTTPFailure(int(match[1]), provider_code)
