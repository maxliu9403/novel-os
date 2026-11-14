"""Native run/stage ownership for shared request recovery; no receipt scanning."""
from contextlib import contextmanager
import hashlib
import json
import re
from pathlib import Path

from .creative_types import digest
from .reference_assets import atomic_json
from .request_recovery import (RequestRecoveryError, project_request_recovery,
                               request_recovery_scope, authorize_request_retry, RequestRecoveryCaptured)


def directory(project, run_id):
    if not isinstance(run_id, str) or not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in run_id):
        raise RequestRecoveryError('任务身份无效。')
    path = Path(project) / 'outputs/runs' / run_id / 'request-recovery'
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise RequestRecoveryError('任务恢复目录不能是别名。')
    return path


def authority(project, manifest):
    from .pipeline_runner import PipelineRunner
    from .pipeline_models import RunManifest
    value = manifest.to_dict() if hasattr(manifest, 'to_dict') else manifest
    model = RunManifest.from_dict(value)
    spec = {k: v for k, v in value['spec'].items() if k not in {'project_path', 'prompt_path'}}
    return {'host': 'novel', 'run_id': value['run_id'], 'phase': value.get('current_phase'),
            'chapter': value.get('current_chapter'), 'spec_sha256': digest(spec),
            'inputs': PipelineRunner._stage_input_hashes(Path(project), model.current_phase, model.current_chapter, model)}


PROBE_VERSION = 'native-request-probe.v1'


def _read_probe(root):
    path = root / 'probe.json'
    if path.is_symlink():
        raise RequestRecoveryError('恢复核对记录不能是别名。')
    if not path.exists():
        return None
    try:
        if not path.is_file() or path.stat().st_size > 128_000:
            raise ValueError('invalid marker')
        data = json.loads(path.read_text())
        if (not isinstance(data, dict) or data.get('schema') != PROBE_VERSION
                or set(data) - {'schema', 'authority', 'resolved', 'captured', 'sha256'}
                or type(data.get('resolved')) is not bool
                or data.get('sha256') != digest({k: v for k, v in data.items() if k != 'sha256'})):
            raise ValueError('invalid marker')
        bound = data.get('authority')
        if (not isinstance(bound, dict) or bound.get('host') != 'novel'
                or bound.get('run_id') != root.parent.name
                or not isinstance(bound.get('phase'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', str(bound.get('spec_sha256', '')))
                or not isinstance(bound.get('inputs'), dict)):
            raise ValueError('invalid authority')
        captured = data.get('captured')
        if captured is not None:
            request = captured.get('request') if isinstance(captured, dict) else None
            if (not isinstance(captured, dict) or set(captured) != {'pointer_sha256', 'request'}
                    or not re.fullmatch(r'[a-f0-9]{64}', str(captured.get('pointer_sha256', '')))
                    or not isinstance(request, dict) or set(request) != {'path', 'identity', 'kind'}
                    or not isinstance(request['path'], str) or not request['path']
                    or Path(request['path']).is_absolute() or '..' in Path(request['path']).parts
                    or request['kind'] not in {'design', 'evidence'}
                    or not re.fullmatch(r'[a-f0-9]{64}', str(request['identity']))):
                raise ValueError('invalid captured request')
        if data['resolved'] and captured is None:
            raise ValueError('resolution lacks captured request')
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RequestRecoveryError('恢复核对记录无法核验，未发送新请求。') from exc


def _write_probe(root, data):
    # Recheck the path at every write, including the resolution branch.
    path = root / 'probe.json'
    if path.is_symlink():
        raise RequestRecoveryError('恢复核对记录不能是别名。')
    value = {k: v for k, v in data.items() if k != 'sha256'}
    try:
        atomic_json(path, {**value, 'sha256': digest(value)})
    except OSError as exc:
        raise RequestRecoveryError('恢复核对记录未能保存，未发送新请求。') from exc


def _captured(record):
    return {'pointer_sha256': record['sha256'], 'request': record['request']}


def _state(project, manifest, *, projection_only=False):
    """Read the request binding, optionally without rebuilding current stage inputs.

    Project cards are read frequently while a run is in a later stage.  A
    completed request bound to an earlier stage cannot affect that projection;
    rebuilding the current stage authority in that case used to walk every
    publication receipt in the book.  The execution paths keep the default
    full validation and authority construction.
    """
    value = manifest.to_dict() if hasattr(manifest, 'to_dict') else manifest
    # Keep the manifest contract validation on the lightweight branch.  The
    # optimization skips only stage-input construction, never run identity or
    # schema checks.
    from .pipeline_models import RunManifest
    RunManifest.from_dict(value)
    root = directory(project, value['run_id'])
    probe = _read_probe(root)  # Always validate before considering any pointer.
    pointer = root / 'current.json'
    record = recovery = None
    if pointer.is_symlink():
        raise RequestRecoveryError('恢复记录不能是别名。')
    if probe is None and not pointer.exists():
        return root, None, None, None, None
    current = None
    if pointer.exists():
        if not pointer.is_file() or pointer.stat().st_size > 128_000:
            raise RequestRecoveryError('恢复记录无效。')
        record = json.loads(pointer.read_text())
        bound = record.get('authority') if isinstance(record, dict) else None
        if (not isinstance(bound, dict) or bound.get('host') != 'novel'
                or bound.get('run_id') != value.get('run_id')
                or not isinstance(bound.get('phase'), str)
                or ('chapter' in bound and bound.get('chapter') is not None
                    and type(bound.get('chapter')) is not int)):
            raise RequestRecoveryError('恢复请求不属于当前任务。')
        recovery = project_request_recovery(project, pointer, bound)
        # A completed pointer from a different stage is historical evidence for
        # the UI only.  It is safe to skip the expensive current authority
        # construction because _project will discard the stale completion.
        if (projection_only and recovery and recovery.get('response_available')
                and (probe is None or probe.get('resolved') is True)
                and (bound.get('phase'), bound.get('chapter')) !=
                    (value.get('current_phase'), value.get('current_chapter'))):
            return root, None, probe, record, recovery
    current = authority(project, manifest)
    if probe and not probe['resolved']:
        captured = probe.get('captured')
        if captured is not None and (record is None or captured != _captured(record)
                                     or record['authority'] != probe['authority']):
            raise RequestRecoveryError('已定位的原请求已变化，恢复核对仍保留。')
        # A verified response for the exact captured request can finish recovery
        # even if a later explicit edit changed the current source. An earlier
        # completed request cannot replace the unresolved original authority.
        completed_target = bool(captured and recovery and recovery.get('response_available'))
        if probe['authority'] != current and not completed_target:
            raise RequestRecoveryError('原恢复核对依据已变化，未发送新请求。')
    return root, current, probe, record, recovery


def _project(current, probe, record, recovery):
    if probe and not probe['resolved'] and probe.get('captured') is None:
        return None  # Still a no-send probe, even beside an earlier completion.
    if record is None:
        return None
    if record['authority'] != current:
        if recovery.get('response_available'):
            return None
        raise RequestRecoveryError('当前创作依据与未完成请求不同。')
    return recovery


def projection(project, manifest):
    try:
        _, current, probe, record, recovery = _state(project, manifest, projection_only=True)
        return _project(current, probe, record, recovery)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError):
        return {'can_resume': False, 'can_retry_unknown': False, 'reason': 'invalid_binding',
                'message': '当前请求记录无法核验，已有成果保留，未重新执行。'}


def needs_probe(project, manifest):
    value = manifest.to_dict() if hasattr(manifest, 'to_dict') else manifest
    # This helper is used by read-only task-card projection.  It must retain
    # the binding checks, while avoiding a second current-stage authority walk
    # after projection() has already established a completed stale pointer.
    _, _, probe, _, _ = _state(project, manifest, projection_only=True)
    return not probe['resolved'] if probe else 'RequestOutcomeUnknown:' in (value.get('error') or '')


def prepare_resume(project, manifest):
    """Validate the frozen probe before restoring inputs or retiring its fence."""
    value = manifest.to_dict() if hasattr(manifest, 'to_dict') else manifest
    root, current, probe, record, recovery = _state(project, manifest)
    if probe and not probe['resolved']:
        if probe.get('captured') is None:
            return True
        if not recovery['can_resume']:
            raise RequestRecoveryError(recovery['message'])
        # _state verified the exact captured pointer. Shared recovery verifies
        # completion, a pending retry allowance, or terminal-failure evidence.
        _write_probe(root, {**probe, 'resolved': True})
        return False
    active = _project(current, probe, record, recovery)
    if active is not None:
        if not active['can_resume']:
            raise RequestRecoveryError(active['message'])
        return False
    if probe is None and 'RequestOutcomeUnknown:' in (value.get('error') or ''):
        _write_probe(root, {'schema': PROBE_VERSION, 'authority': current or authority(project, manifest), 'resolved': False})
        return True
    return False


@contextmanager
def stage_scope(project, manifest, *, capture_only=False):
    root, current, probe, record, recovery = _state(project, manifest)
    _project(current, probe, record, recovery)
    current = current or authority(project, manifest)
    if probe and not probe['resolved']:
        capture_only = True
    elif capture_only:
        if probe is not None:
            raise RequestRecoveryError('已完成的恢复核对不能作为新的捕获操作。')
        probe = {'schema': PROBE_VERSION, 'authority': current, 'resolved': False}
        _write_probe(root, probe)
    try:
        with request_recovery_scope(project, root / 'current.json', current, capture_only=capture_only):
            yield
    except RequestRecoveryCaptured:
        # The shared scope has safely bound the actual unresolved request. Save
        # that precise request/path/kind before offering any paid retry.
        probe = _read_probe(root)
        if not capture_only or probe is None or probe['resolved'] or probe['authority'] != current:
            raise RequestRecoveryError('原请求捕获缺少有效恢复依据。') from None
        pointer = root / 'current.json'
        project_request_recovery(project, pointer, current)
        saved = json.loads(pointer.read_text())
        captured = _captured(saved)
        if saved['authority'] != current or (probe.get('captured') is not None and probe['captured'] != captured):
            raise RequestRecoveryError('捕获请求与原恢复目标不同，未重新发送。') from None
        _write_probe(root, {**probe, 'captured': captured})
        raise


def authorize(project, manifest, request):
    root, _, probe, _, _ = _state(project, manifest)
    if probe and not probe['resolved'] and probe.get('captured') is None:
        raise RequestRecoveryError('请先完成原请求的恢复核对。')
    return authorize_request_retry(project, root / 'current.json', authority(project, manifest),
        request_sha256=request['request_sha256'], receipt_sha256=request['receipt_sha256'],
        operation_id=request['operation_id'])


def operation_path(project, run_id, operation_id):
    return directory(project, run_id) / 'operations' / (hashlib.sha256(operation_id.encode()).hexdigest() + '.json')


def read_operation(project, run_id, request):
    path = operation_path(project, run_id, request['operation_id'])
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise RequestRecoveryError('恢复操作路径异常。')
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if value.get('request') != request or value.get('run_id') != run_id:
        raise RequestRecoveryError('同一恢复操作不能用于不同请求。')
    return value


def save_operation(project, run_id, request, job_id=None):
    atomic_json(operation_path(project, run_id, request['operation_id']),
                {'run_id': run_id, 'request': request, 'job_id': job_id})
