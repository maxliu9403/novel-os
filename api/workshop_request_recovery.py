"""Read-only error projection for an older, misclassified model request.

The current worker diagnostic identifies the failed physical attempt. The shared
request boundary verifies its terminal evidence. Neither timestamps nor an old
HTTP line alone can replace the current Workshop error.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from core.source_request_session import SourceRequestSession


def _read(path: Path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Unsafe request diagnostic path")
    return path.read_text("utf-8")


def _current_request(session, root):
    revision = session.get("revision")
    if type(revision) is not int or revision < 0 or not session.get("job_id"):
        return None
    # Only legacy generic adapter errors need projection. A later business,
    # parsing or storage failure must keep its own explanation.
    log = (session.get("logs") or [{}])[-1]
    if (session.get("status") != "error" or not isinstance(log, dict)
            or log.get("event") != "turn_failed" or log.get("provider") != "codex"
            or log.get("error_type") != "LLMError"
            or log.get("message") != session.get("last_error")):
        return None
    terminal = None
    for line in _read(root / "diagnostics" / f"{revision}-brainstorm.jsonl").splitlines():
        record = json.loads(line)
        if not isinstance(record, dict):
            return None
        if isinstance(record.get("event"), str) and record["event"].startswith("request."):
            terminal = record
    if (not terminal or terminal.get("event") != "request.failed"
            or terminal.get("error_type") != "LLMError"
            or terminal.get("session_id") != session.get("id")
            or terminal.get("job_id") != session.get("job_id")
            or terminal.get("revision") != revision):
        return None
    identifier = terminal.get("request_id")
    return identifier if isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{32}", identifier) else None


def failure_message(session: dict, root: Path | None) -> str | None:
    """Return a verified safe explanation, without migrating or retrying a task."""
    if root is None:
        return None
    root = Path(root)
    try:
        request_id = _current_request(session, root)
        if request_id is None:
            return None
        matches = []
        # Locate by an exact physical-request ID before opening any full prompt.
        for progress in (root / "source-evidence" / "requests").glob("*/execution/*/attempts/*/progress.json"):
            observed = json.loads(_read(progress))
            if (not isinstance(observed, dict) or observed.get("event") != "request.failed"
                    or observed.get("request_id") != request_id):
                continue
            attempt_id = progress.parent.name
            identity = progress.parents[2].name
            if not re.fullmatch(r"[a-f0-9]{32}", attempt_id) or not re.fullmatch(r"[a-f0-9]{64}", identity):
                return None
            receipt = progress.parents[4] / f"{identity}.json"
            record = json.loads(_read(receipt))
            if not isinstance(record, dict) or record.get("active_attempt_id") != attempt_id:
                continue
            recovery = SourceRequestSession(receipt, identity, kind="evidence").recovery()
            execution = recovery.get("execution") or {}
            status = execution.get("http_status")
            if (recovery.get("can_resume") and not recovery.get("can_retry_unknown")
                    and type(status) is int and 400 <= status <= 599):
                matches.append(recovery["message"])
        # Ambiguous duplicated attempt ownership cannot establish current cause.
        return matches[0] if len(matches) == 1 else None
    except (OSError, ValueError, TypeError, KeyError):
        # Diagnostic damage cannot break the session page or authorize a retry.
        return None
