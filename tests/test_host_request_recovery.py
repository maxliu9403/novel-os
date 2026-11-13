"""Host recovery follows one executing request, never a newest-receipt guess."""
import asyncio
import hashlib
import json

import pytest

from core.creative_types import digest
from core.llm_attempt import completion_attempt
from core.reference_assets import atomic_json
from core.request_recovery import (
    active_planning_prompt,
    RequestRecoveryCaptured,
    RequestRecoveryError,
    authorize_request_retry,
    project_request_recovery,
    request_recovery_scope,
)
from core.source_request_session import RequestOutcomeUnknown, SourceRequestSession
from core.source_routes import VERSION


class Model:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def complete(self, system, user):
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return '完整且可复用的回复'


@pytest.fixture
def host(tmp_path):
    root = tmp_path.resolve() / 'host'
    root.mkdir()
    return root, root / 'active-request.json', {'run_id': 'run-one', 'phase': 'outline'}


def make_request(root, name='current', *, kind='design', status='pending_unknown', **extra):
    system, user = '完整系统要求', f'{name} 的完整原文与作者要求'
    if kind == 'design':
        payload = {'source': user}
        identity = digest([VERSION, system, payload])
        record = {'system': system, 'payload': payload, 'request_sha256': identity, 'status': status}
    else:
        identity = digest({'system': system, 'user': user})
        record = {'system': system, 'user': user, 'state': status}
    record.update(extra)
    path = root / 'requests' / f'{name}.json'
    atomic_json(path, record)
    return SourceRequestSession(path, identity, kind=kind)


def bind_unknown(host, request):
    model = Model()
    with request_recovery_scope(*host), pytest.raises(RequestOutcomeUnknown):
        request.execute(model)
    assert model.calls == []
    return project_request_recovery(*host)


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def authorize(host, projection, operation='retry-one'):
    return authorize_request_retry(*host, request_sha256=projection['request_sha256'],
                                   receipt_sha256=projection['receipt_sha256'], operation_id=operation)


@pytest.mark.parametrize('kind,status', [('design', 'pending_unknown'), ('evidence', 'dispatched')])
def test_projection_uses_only_executed_pointer_and_never_writes_or_scans(host, kind, status):
    root, pointer, _ = host
    request = make_request(root, kind=kind, status=status)
    projection = bind_unknown(host, request)
    unrelated = make_request(root, 'newer-unrelated', kind=kind, status=status)
    assert unrelated.identity != request.identity
    before = snapshot(root)
    for _ in range(2):
        current = project_request_recovery(*host)
        assert current == projection
        assert current['can_retry_unknown'] and not current['can_resume']
        assert current['request_sha256'] == request.identity
        assert current['receipt_sha256'] == hashlib.sha256(request.path.read_bytes()).hexdigest()
    assert json.loads(pointer.read_text())['request']['path'] == 'requests/current.json'
    assert snapshot(root) == before


def test_absent_pointer_does_not_scan_or_create_one(host):
    root, pointer, _ = host
    make_request(root)
    before = snapshot(root)
    assert project_request_recovery(*host) is None
    assert not pointer.exists()
    assert snapshot(root) == before


@pytest.mark.parametrize('transport', ['inline', 'paged', 'unwrapped'])
def test_legacy_planner_prompt_lookup_is_read_only_and_keeps_embedded_delimiters(host, transport):
    root, pointer, authority = host
    original = 'STAGE: semantic_review\nAn author quote: \n\n[SOURCE_EVIDENCE]\n旧资料。'
    packet = {'content': original}
    packet['sha256'] = digest(packet)
    user = {'inline': original + '\n\n[SOURCE_EVIDENCE]\n' + json.dumps(packet) + '\n[/SOURCE_EVIDENCE]',
            'paged': json.dumps({'original_task': original, 'evidence_units': [{'path': ['content']}]}),
            'unwrapped': original}[transport]
    session = SourceRequestSession(root / 'receipt.json', digest({'system': 's', 'user': user}), kind='evidence')
    atomic_json(session.path, {'state': 'dispatched', 'system': 's', 'user': user})
    bind_unknown(host, session)
    before = snapshot(root)
    assert active_planning_prompt() is None
    with request_recovery_scope(*host):
        assert active_planning_prompt() == hashlib.sha256(original.encode()).hexdigest()
    with request_recovery_scope(root, pointer, {**authority, 'phase': 'chapter.write'}):
        assert active_planning_prompt() is None
    assert snapshot(root) == before


def test_scope_restores_outer_authority_after_nested_exception_and_copies_input(host):
    root, pointer, authority = host
    outer = make_request(root, 'outer')
    inner = make_request(root, 'inner')
    inner_pointer = root / 'inner-pointer.json'
    original = dict(authority)
    with request_recovery_scope(*host):
        authority['phase'] = 'changed-by-caller'
        with pytest.raises(RuntimeError):
            with request_recovery_scope(root, inner_pointer, {'run_id': 'inner'}):
                with pytest.raises(RequestOutcomeUnknown):
                    inner.execute(Model())
                raise RuntimeError('leave inner scope')
        with pytest.raises(RequestOutcomeUnknown):
            outer.execute(Model())
    assert json.loads(pointer.read_text())['authority'] == original
    assert json.loads(inner_pointer.read_text())['authority'] == {'run_id': 'inner'}
    before = pointer.read_bytes()
    with pytest.raises(RequestOutcomeUnknown):
        inner.execute(Model())
    assert pointer.read_bytes() == before


def test_concurrent_contexts_keep_distinct_host_authority(host):
    root, _, _ = host

    async def run(name):
        pointer = root / f'{name}-pointer.json'
        request = make_request(root, name)
        authority = {'run_id': name}
        with request_recovery_scope(root, pointer, authority):
            await asyncio.sleep(0)
            with pytest.raises(RequestOutcomeUnknown):
                request.execute(Model())
        return pointer, request, authority

    async def gather():
        return await asyncio.gather(run('left'), run('right'))

    for pointer, request, authority in asyncio.run(gather()):
        assert project_request_recovery(root, pointer, authority)['request_sha256'] == request.identity
        assert json.loads(pointer.read_text())['authority'] == authority


def test_retry_authorizes_exactly_once_preserves_original_and_replays_consumed_operation(host):
    root, _, _ = host
    request = make_request(root)
    projection = bind_unknown(host, request)
    original = request.path.read_bytes()
    assert authorize(host, projection) == 'pending'
    assert authorize(host, projection) == 'pending'
    assert project_request_recovery(*host)['can_resume']
    assert not project_request_recovery(*host)['can_retry_unknown']
    model = Model(TimeoutError('local wait ended'))
    with request_recovery_scope(*host), pytest.raises(TimeoutError):
        request.execute(model)
    assert authorize(host, projection) == 'consumed'
    with request_recovery_scope(*host), pytest.raises(RequestOutcomeUnknown):
        request.execute(model)
    assert len(model.calls) == 1
    record = json.loads(request.path.read_text())
    assert len(record['retry_authorizations']) == 1
    assert record['attempts'][0]['retry_operation_id'] == 'retry-one'
    archive = request.directory / 'history' / f"{projection['receipt_sha256']}.json"
    assert archive.read_bytes() == original
    before = snapshot(root)
    with pytest.raises(RequestRecoveryError, match='状态已变化'):
        authorize(host, projection, 'different-operation')
    assert snapshot(root) == before


def test_successful_retry_reuses_saved_response_without_a_second_call(host):
    request = make_request(host[0])
    projection = bind_unknown(host, request)
    authorize(host, projection)
    model = Model()
    with request_recovery_scope(*host):
        assert request.execute(model) == '完整且可复用的回复'
        assert request.execute(model) == '完整且可复用的回复'
    assert len(model.calls) == 1
    assert authorize(host, projection) == 'consumed'
    assert project_request_recovery(*host)['can_resume']
    assert not project_request_recovery(*host)['can_retry_unknown']


@pytest.mark.parametrize('change', ['receipt', 'request', 'authority'])
def test_stale_binding_cannot_authorize_and_leaves_all_records_unchanged(host, change):
    request = make_request(host[0])
    projection = bind_unknown(host, request)
    if change == 'receipt':
        record = json.loads(request.path.read_text())
        record['observed_at'] = 'later-observation'
        atomic_json(request.path, record)
    elif change == 'request':
        projection['request_sha256'] = 'f' * 64
    else:
        host = (*host[:2], {'run_id': 'other-run', 'phase': 'outline'})
        assert project_request_recovery(*host) is None
    before = snapshot(host[0])
    with pytest.raises(RequestRecoveryError):
        authorize(host, projection)
    assert snapshot(host[0]) == before


def test_saved_completion_arriving_before_authorization_wins_without_repurchase(host):
    request = make_request(host[0], active_attempt_id='a' * 32)
    projection = bind_unknown(host, request)
    receipt = request.path.read_bytes()
    with completion_attempt(request.directory / 'attempts' / ('a' * 32), request.identity) as attempt:
        attempt.save_response('迟到但已完整保存的回复')
    before = snapshot(host[0])
    current = project_request_recovery(*host)
    assert current['reason'] == 'saved_completion'
    assert current['can_resume'] and not current['can_retry_unknown']
    assert current['receipt_sha256'] == projection['receipt_sha256']
    with pytest.raises(RequestRecoveryError, match='不需要重新执行'):
        authorize(host, projection)
    assert snapshot(host[0]) == before
    assert request.path.read_bytes() == receipt
    model = Model()
    with request_recovery_scope(*host):
        assert request.execute(model) == '迟到但已完整保存的回复'
    assert model.calls == []


@pytest.mark.parametrize('status', ['pending_unknown', 'dispatched'])
def test_capture_only_binds_without_dispatch_or_changing_original_receipt(host, status):
    request = make_request(host[0], status=status)
    original = request.path.read_bytes()
    model = Model()
    with request_recovery_scope(*host, capture_only=True), pytest.raises(RequestRecoveryCaptured):
        request.execute(model)
    assert request.path.read_bytes() == original
    assert model.calls == []
    assert json.loads(host[1].read_text())['request']['identity'] == request.identity


@pytest.mark.parametrize('status', ['prepared', 'failed'])
@pytest.mark.parametrize('existing_pointer', [False, True])
def test_capture_only_rejects_fresh_or_failed_steps_before_replacing_pointer(host, status, existing_pointer):
    root, pointer, _ = host
    if existing_pointer:
        complete = make_request(root, 'complete', status='response_received', raw='已保存', response_sha256=digest('已保存'))
        with request_recovery_scope(*host):
            assert complete.execute(Model()) == '已保存'
    original_pointer = pointer.read_bytes() if pointer.exists() else None
    request = make_request(root, 'unmatched-step', status=status)
    original_request = request.path.read_bytes()
    model = Model()
    with request_recovery_scope(*host, capture_only=True), pytest.raises(RequestRecoveryError):
        request.execute(model)
    assert (pointer.read_bytes() if pointer.exists() else None) == original_pointer
    assert request.path.read_bytes() == original_request
    assert model.calls == []


def test_capture_only_reuses_completed_steps_then_stops_at_first_unsaved_request(host):
    complete = make_request(host[0], 'complete', status='response_received', raw='保存的回复', response_sha256=digest('保存的回复'))
    pending = make_request(host[0], 'pending')
    model = Model()
    with request_recovery_scope(*host, capture_only=True):
        assert complete.execute(model) == '保存的回复'
        assert not host[1].exists()
        with pytest.raises(RequestRecoveryCaptured):
            pending.execute(model)
    assert model.calls == []
    assert project_request_recovery(*host)['request_sha256'] == pending.identity


@pytest.mark.parametrize('other_request', ['different_identity', 'same_hash_different_path'])
def test_pending_authorization_cannot_be_redirected_to_another_request(host, other_request):
    root, pointer, _ = host
    target = make_request(root, 'authorized-target')
    projection = bind_unknown(host, target)
    authorize(host, projection)
    if other_request == 'different_identity':
        other = make_request(root, 'different-request', status='prepared')
    else:
        other_path = root / 'requests' / 'same-request-other-path.json'
        other_path.write_bytes(target.path.read_bytes())
        other = SourceRequestSession(other_path, target.identity)
    original_pointer = pointer.read_bytes()
    original_target, original_other = target.path.read_bytes(), other.path.read_bytes()
    model = Model()
    with request_recovery_scope(*host), pytest.raises(RequestRecoveryError, match='请求不同'):
        other.execute(model)
    assert pointer.read_bytes() == original_pointer
    assert target.path.read_bytes() == original_target
    assert other.path.read_bytes() == original_other
    assert model.calls == []
    assert target.retry_authorization('retry-one', projection['receipt_sha256']) == 'pending'


def test_pending_target_survives_completed_replay_and_is_the_only_new_dispatch(host):
    root, pointer, _ = host
    target = make_request(root, 'authorized-target')
    projection = bind_unknown(host, target)
    authorize(host, projection)
    complete = make_request(root, 'earlier-complete', status='response_received', raw='先前完整回复', response_sha256=digest('先前完整回复'))
    other = make_request(root, 'unrelated-incomplete', status='prepared')
    original_pointer = pointer.read_bytes()
    original_target = target.path.read_bytes()
    model = Model()
    with request_recovery_scope(*host):
        assert complete.execute(model) == '先前完整回复'
        assert pointer.read_bytes() == original_pointer
        assert target.path.read_bytes() == original_target
        assert model.calls == []
        with pytest.raises(RequestRecoveryError, match='请求不同'):
            other.execute(model)
        assert pointer.read_bytes() == original_pointer
        assert target.path.read_bytes() == original_target
        assert model.calls == []
        assert target.execute(model) == '完整且可复用的回复'
    assert len(model.calls) == 1
    assert json.loads(pointer.read_text())['request']['path'] == target.path.relative_to(root).as_posix()
    assert target.retry_authorization('retry-one', projection['receipt_sha256']) == 'consumed'


@pytest.mark.parametrize('path_kind', ['foreign_absolute', 'parent_escape', 'request_symlink', 'pointer_symlink'])
def test_foreign_or_symlinked_paths_fail_closed(host, path_kind):
    root, pointer, _ = host
    request = make_request(root)
    projection = bind_unknown(host, request)
    foreign = root.parent / 'foreign.json'
    foreign.write_bytes(request.path.read_bytes())
    if path_kind == 'pointer_symlink':
        saved_pointer = root.parent / 'saved-pointer.json'
        saved_pointer.write_bytes(pointer.read_bytes())
        pointer.unlink()
        pointer.symlink_to(saved_pointer)
    elif path_kind == 'request_symlink':
        request.path.unlink()
        request.path.symlink_to(foreign)
    else:
        record = json.loads(pointer.read_text())
        record['request']['path'] = str(foreign) if path_kind == 'foreign_absolute' else '../foreign.json'
        record['sha256'] = digest({k: v for k, v in record.items() if k != 'sha256'})
        atomic_json(pointer, record)
    before = snapshot(root)
    foreign_before = foreign.read_bytes()
    with pytest.raises(RequestRecoveryError):
        project_request_recovery(*host)
    with pytest.raises(RequestRecoveryError):
        authorize(host, projection)
    assert snapshot(root) == before
    assert foreign.read_bytes() == foreign_before


def test_foreign_executing_request_cannot_establish_host_pointer(host):
    request = make_request(host[0].parent, 'foreign')
    model = Model()
    original = request.path.read_bytes()
    with request_recovery_scope(*host), pytest.raises(RequestRecoveryError, match='不属于'):
        request.execute(model)
    assert not host[1].exists()
    assert request.path.read_bytes() == original
    assert model.calls == []


@pytest.mark.parametrize('corruption', ['digest', 'authority_digest', 'invalid_json'])
def test_corrupt_pointer_never_projects_or_authorizes(host, corruption):
    projection = bind_unknown(host, make_request(host[0]))
    pointer = host[1]
    if corruption == 'invalid_json':
        pointer.write_text('{broken')
    else:
        record = json.loads(pointer.read_text())
        if corruption == 'digest':
            record['request']['identity'] = 'f' * 64
        else:
            record['authority']['phase'] = 'chapter'
            record['sha256'] = digest({k: v for k, v in record.items() if k != 'sha256'})
        atomic_json(pointer, record)
    before = snapshot(host[0])
    with pytest.raises(RequestRecoveryError):
        project_request_recovery(*host)
    with pytest.raises(RequestRecoveryError):
        authorize(host, projection)
    assert snapshot(host[0]) == before


@pytest.mark.parametrize('corruption', ['missing_status', 'invalid_status', 'numeric_status', 'list_status', 'attempts_type', 'authorizations_type', 'authorization_entry', 'authorization_list_status'])
def test_corrupt_mutable_receipt_fails_closed_without_dispatch(host, corruption):
    request = make_request(host[0])
    projection = bind_unknown(host, request)
    record = json.loads(request.path.read_text())
    if corruption == 'missing_status':
        record.pop('status')
    elif corruption == 'invalid_status':
        record['status'] = 'unknown_future_typo'
    elif corruption == 'numeric_status':
        record['status'] = 3
    elif corruption == 'list_status':
        record['status'] = []
    elif corruption == 'attempts_type':
        record['attempts'] = {'not': 'a list'}
    elif corruption == 'authorizations_type':
        record['retry_authorizations'] = {'not': 'a list'}
    elif corruption == 'authorization_list_status':
        record['retry_authorizations'] = [{'status': []}]
    else:
        record['retry_authorizations'] = [None]
    atomic_json(request.path, record)
    before = snapshot(host[0])
    with pytest.raises(RequestRecoveryError):
        project_request_recovery(*host)
    with pytest.raises(RequestRecoveryError):
        authorize(host, projection)
    assert snapshot(host[0]) == before
    model = Model()
    with pytest.raises(ValueError):
        request.execute(model)
    assert request.path.read_bytes() == before['requests/current.json'][0]
    assert model.calls == []
