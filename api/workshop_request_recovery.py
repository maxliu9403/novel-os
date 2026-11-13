"""Read-only error projection for an older, misclassified model request.

The current worker diagnostic identifies the failed physical attempt. The shared
request boundary verifies its terminal evidence. Neither timestamps nor an old
HTTP line alone can replace the current Workshop error.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from functools import wraps

from core.source_request_session import SourceRequestSession


def authority(session: dict) -> dict:
    """Identity of the author operation, independent of worker/pause revisions."""
    from core.creative_types import digest
    from .workshop_delivery import checkpoint_fingerprint
    return {
        'host': 'workshop', 'session_id': session['id'],
        'operation': session.get('active_operation', 'brainstorm'),
        'run_id': session.get('delivery_run_id'),
        'source_revision': session.get('delivery_source_revision'),
        'input_sha256': digest({
            'author_context': checkpoint_fingerprint(session, session['skill_version']),
            'turn_intent': session.get('turn_intent', 'discuss'),
            'turn_scope': session.get('turn_scope'),
            'capacity_replan': session.get('capacity_replan'),
            'active_turn_request': session.get('active_turn_request'),
        }),
    }


def pointer(root: Path) -> Path:
    return Path(root) / 'request-recovery' / 'current.json'


def current(session: dict, root: Path | None) -> dict | None:
    """Read the explicitly bound current request; never select a recent receipt."""
    if (root is None or session.get('status') not in {'paused', 'error'}
            or session.get('restart_required') or session.get('active_operation') == 'titles'):
        return None
    if not pointer(root).exists() and not pointer(root).is_symlink():
        return None
    from core.request_recovery import project_request_recovery, RequestRecoveryError
    try:
        return project_request_recovery(Path(root), pointer(root), authority(session))
    except (RequestRecoveryError, OSError, ValueError, KeyError, TypeError):
        return {'can_resume': False, 'can_retry_unknown': False, 'reason': 'invalid_binding',
                'message': '当前请求记录无法核对，未重新发送；请检查已保存的请求与讨论依据。'}


def needs_probe(session: dict) -> bool:
    """A known legacy unknown may be rebound only by executing its saved path."""
    from core.source_route_context import enabled as route_source_enabled
    from . import workshop_development
    log = (session.get('logs') or [{}])[-1]
    journaled = (route_source_enabled(session.get('source_context')) or (
        workshop_development.can_resume(session) and session.get('turn_intent') != 'deliver'))
    return bool(journaled and session.get('status') == 'error' and (
        session.get('request_recovery_probe') or (
            log.get('event') == 'turn_failed' and log.get('error_type') == 'RequestOutcomeUnknown'
            and log.get('message') == session.get('last_error'))))


def bound_worker(function):
    """Bind every generation/review/evidence request inside the existing worker."""
    @wraps(function)
    def run(self, session_id, skill, revision, cancel_event):
        from core.request_recovery import request_recovery_scope, RequestRecoveryError
        try:
            with self._locked(session_id) as session:
                if session['status'] != 'running' or session['revision'] != revision or cancel_event.is_set():
                    return
                bound = authority(session)
                capture_only = bool(session.get('request_recovery_probe') and
                                    session.get('resumed_from_revision') == revision - 1)
            root = self._path(session_id)
            with request_recovery_scope(root, pointer(root), bound, capture_only=capture_only):
                return function(self, session_id, skill, revision, cancel_event)
        except (RequestRecoveryError, OSError, ValueError, KeyError, TypeError):
            # Binding setup precedes the worker's own failure boundary. A bad
            # author snapshot must still settle the durable running state.
            message = '当前请求或创作依据无法核对，已保留原记录，未重新发送模型请求。'
            with self._locked(session_id) as session:
                if session['status'] != 'running' or session['revision'] != revision or cancel_event.is_set():
                    return
                session.update(status='error', last_error=message)
                self._log(session, 'turn_failed', message, error_type='RequestRecoveryError')
                self._save(session)
            raise RuntimeError(message) from None
    return run


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
