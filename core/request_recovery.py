"""Bind shared request recovery to the host operation actually executing it.

Pointers contain identities, never model input or credentials. Only an executing
host can establish a pointer; readers never search for a 'latest' unknown request.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
from pathlib import Path

from .creative_types import digest
from .reference_assets import atomic_json


VERSION = 'host-request-recovery.v1'
_scope = ContextVar('host_request_recovery', default=None)


class RequestRecoveryError(ValueError):
    pass


class RequestRecoveryCaptured(ValueError):
    pass


def _safe(root, path):
    root, path = Path(root).absolute(), Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise RequestRecoveryError('恢复记录路径异常，原请求未重新执行。')
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RequestRecoveryError('恢复请求不属于当前任务。') from exc
    if not relative.parts or any(part in {'.', '..'} for part in relative.parts):
        raise RequestRecoveryError('恢复记录路径无效。')
    return path


@contextmanager
def request_recovery_scope(root, pointer, authority, *, capture_only=False):
    root = Path(root).absolute()
    pointer = _safe(root, pointer)
    if not isinstance(authority, dict) or not authority:
        raise RequestRecoveryError('恢复操作缺少任务依据。')
    previous = _bound_session(root, pointer, authority)
    expected = None
    if previous is not None:
        # Validate before a caller can recreate a missing/corrupt receipt.
        recovery = previous.recovery()
        saved = previous._read()
        if recovery['can_retry_unknown'] or any(a.get('status') == 'pending' for a in saved.get('retry_authorizations', [])):
            expected = previous
    token = _scope.set((root, pointer, deepcopy(authority), bool(capture_only), expected))
    try:
        yield
    finally:
        _scope.reset(token)


def bind_request_recovery(session):
    """Called inside the request lease, before reading/dispatching its receipt."""
    scope = _scope.get()
    if scope is None:
        return
    root, pointer, authority, capture_only, expected = scope
    request_path = _safe(root, session.path)
    if capture_only or expected is not None:
        saved = session._read()
        complete = saved.get(session.response_key) is not None or session._recovered(saved) is not None
        if capture_only:
            if complete:
                # Earlier cached steps are not the unresolved legacy request.
                return
            if not session._unknown(saved):
                raise RequestRecoveryError('当前步骤与原未完成请求无法对应，未发送新请求；原记录保留。')
        if expected is not None and (expected.identity, expected.kind, expected.path.absolute()) != (session.identity, session.kind, session.path.absolute()):
            target = expected._read()
            target_complete = target.get(expected.response_key) is not None or expected._recovered(target) is not None
            if not target_complete:
                if complete:
                    return
                raise RequestRecoveryError('当前请求与已确认的重新执行请求不同，未发送新请求。')
    record = {'schema': VERSION, 'authority': authority, 'authority_sha256': digest(authority),
              'request': {'path': request_path.relative_to(root).as_posix(),
                          'identity': session.identity, 'kind': session.kind}}
    record['sha256'] = digest(record)
    atomic_json(_safe(root, pointer), record)
    if capture_only:
        raise RequestRecoveryCaptured('已定位原未完成请求，原记录与进度保留；请使用当前恢复操作继续。')


def _bound_session(root, pointer, authority):
    from .source_request_session import SourceRequestSession
    root = Path(root).absolute()
    pointer = _safe(root, pointer)
    if not pointer.exists():
        return None
    try:
        if not pointer.is_file() or pointer.stat().st_size > 128_000:
            raise ValueError('invalid pointer')
        record = json.loads(pointer.read_text(encoding='utf-8'))
        if (not isinstance(record, dict) or record.get('schema') != VERSION
                or record.get('sha256') != digest({k: v for k, v in record.items() if k != 'sha256'})
                or record.get('authority_sha256') != digest(record.get('authority'))):
            raise ValueError('invalid binding')
        if record['authority'] != authority:
            return None
        request = record['request']
        relative = Path(request['path'])
        if relative.is_absolute() or any(part in {'.', '..'} for part in relative.parts):
            raise ValueError('invalid relative path')
        path = _safe(root, root / relative)
        return SourceRequestSession(path, request['identity'], kind=request['kind'])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RequestRecoveryError('恢复记录无法核验，原请求与成果已保留，未重新执行。') from exc


def project_request_recovery(root, pointer, authority):
    """Pure projection; stale host operations do not authorize current work."""
    session = _bound_session(root, pointer, authority)
    if session is None:
        return None
    try:
        return session.recovery()
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RequestRecoveryError('原请求记录无法核验，已有成果保留，未重新执行。') from exc


def authorize_request_retry(root, pointer, authority, *, request_sha256, receipt_sha256, operation_id):
    session = _bound_session(root, pointer, authority)
    if session is None or session.identity != request_sha256:
        raise RequestRecoveryError('当前请求已变化，请刷新后使用最新恢复操作。')
    try:
        prior = session.retry_authorization(operation_id, receipt_sha256)
        if prior is not None:
            return prior
        session.authorize_retry(operation_id, receipt_sha256)
        return session.retry_authorization(operation_id, receipt_sha256)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RequestRecoveryError(str(exc)) from exc
