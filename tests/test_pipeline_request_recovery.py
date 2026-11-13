"""Native task API, real resume/runtime/stage, and one local model substitute."""
from copy import deepcopy
import hashlib
import json

import pytest

from api import project_tasks, project_task_summary
from api.jobs import JobRunner
from core import pipeline_request_recovery as recovery_host
from core.creative_types import digest
from core.llm_client import LLMCancelledError
from core.reference_assets import atomic_json
from core.source_request_session import SourceRequestSession
from core.request_recovery import RequestRecoveryError, request_recovery_scope
from pipeline_models import StageResult, RunManifest
from pipeline_runner import PipelineError, PipelineRunner
from test_project_tasks import _client, _seed, _wait_job


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def native(tmp_path, monkeypatch):
    project, manifest, path = _seed(tmp_path, max_retries=2, retry_backoff_seconds=0)
    manifest.stages.clear()
    manifest.record(StageResult(phase='outline', status='failed', error='Initial stage pending'))
    prompt = project / 'outputs/input/prompt.md'
    prompt.parent.mkdir(parents=True)
    prompt.write_text('The heroine keeps her independence. The complete original story remains here.')
    (prompt.parent / 'brief.json').write_text(json.dumps({'title': 'A Saved Story',
        'premise': prompt.read_text(), 'language': 'English', 'audience': 'Adult readers'}))
    path.write_text(json.dumps(manifest.to_dict()))
    jobs = JobRunner()
    monkeypatch.setattr(project_tasks, 'runner', jobs)
    monkeypatch.setattr(project_task_summary, 'runner', jobs)
    calls, receipts = [], []
    system = ['exact outline rules']

    class Model:
        def complete(self, rules, user):
            calls.append((rules, user))
            if len(calls) == 1:
                raise LLMCancelledError('Local model process cancelled; remote outcome unobserved')
            return 'Complete outline response'

    def execute(self, current, root, store):
        # Only the story workload is replaced: native resume locking, stage
        # identity, runtime cancellation, receipts and persistence stay real.
        self._rerun_started = False
        self._last_valid_state_snapshot = ''

        def operation():
            user = prompt.read_text()
            identity = digest({'system': system[0], 'user': user})
            receipt = root / 'outputs/source-evidence/requests' / (identity + '.json')
            if not receipt.exists():
                atomic_json(receipt, {'system': system[0], 'user': user, 'state': 'prepared'})
            receipts.append(receipt)
            return SourceRequestSession(receipt, identity, kind='evidence').execute(Model())

        try:
            self._stage(current, root, store, 'outline', None, operation, lambda value: None, [])
        except PipelineError as exc:
            current.status = 'paused' if exc.blocked else 'failed'
            current.error = str(exc)
        else:
            current.status, current.error = 'completed', None
        store.save(current)
        return current

    monkeypatch.setattr(PipelineRunner, '_execute_scoped', execute)
    stopped = PipelineRunner(project).resume(manifest.run_id, project)
    assert stopped.status == 'paused'
    assert len(calls) == 1 and stopped.get('outline').attempt == 1
    manifest = json.loads(path.read_text())
    client = _client(tmp_path)
    return {'project': project, 'manifest': manifest, 'path': path, 'prompt': prompt,
            'jobs': jobs, 'calls': calls, 'receipts': receipts, 'system': system,
            'client': client, 'url': f'/api/projects/book/tasks/{stopped.run_id}'}


def retry_body(detail, operation='retry-exact-outline'):
    recovery = detail['request_recovery']
    return {'request_recovery': {'action': 'retry_unknown', 'operation_id': operation,
        'request_sha256': recovery['request_sha256'], 'receipt_sha256': recovery['receipt_sha256']}}


def test_cancelled_native_stage_exposes_unknown_without_get_mutation_or_ordinary_submit(native):
    n = native
    before = files(n['project'])
    detail = n['client'].get(n['url']).json()
    assert detail['status'] == 'paused'
    assert detail['can_resume'] is False
    assert detail['request_recovery']['can_retry_unknown'] is True
    assert detail['request_recovery']['can_resume'] is False
    assert n['client'].get('/api/projects/book').json()['task_can_resume'] is False
    blocked = n['client'].post(n['url'] + '/resume')
    assert blocked.status_code == 409
    assert files(n['project']) == before and len(n['calls']) == 1
    assert not n['jobs'].latest_for_project('book', project_path=str(n['project'].resolve()),
                                         kind='novel.resume', run_id=n['manifest']['run_id'])


def test_explicit_retry_uses_native_worker_once_and_archives_original_receipt(native):
    n = native
    detail = n['client'].get(n['url']).json()
    body = retry_body(detail)
    original = n['receipts'][0].read_bytes()
    submitted = n['client'].post(n['url'] + '/resume', json=body)
    assert submitted.status_code == 202, submitted.text
    job = _wait_job(n['jobs'], submitted.json()['job_id'])
    assert job['status'] == 'done', job
    assert len(n['calls']) == 2
    completed = n['client'].get(n['url']).json()
    assert completed['status'] == 'completed'
    repeated = n['client'].post(n['url'] + '/resume', json=body)
    assert repeated.status_code == 202
    assert repeated.json()['job_id'] == submitted.json()['job_id'] and len(n['calls']) == 2
    altered = deepcopy(body)
    altered['request_recovery']['receipt_sha256'] = 'f' * 64
    assert n['client'].post(n['url'] + '/resume', json=altered).status_code == 409
    receipt = n['receipts'][0]
    history = receipt.parent / 'execution' / receipt.stem / 'history'
    archived = history / (body['request_recovery']['receipt_sha256'] + '.json')
    assert archived.read_bytes() == original
    record = json.loads(receipt.read_text())
    assert len(record['retry_authorizations']) == 1
    assert record['retry_authorizations'][0]['status'] == 'consumed'
    assert record['response'] == 'Complete outline response'
    assert json.loads(n['path'].read_text())['spec']['approval_policy'] == 'review_required'


@pytest.mark.parametrize('field', ['request_sha256', 'receipt_sha256'])
def test_wrong_request_or_stale_receipt_cannot_authorize_or_queue(native, field):
    n = native
    body = retry_body(n['client'].get(n['url']).json())
    body['request_recovery'][field] = 'f' * 64
    before = files(n['project'])
    response = n['client'].post(n['url'] + '/resume', json=body)
    assert response.status_code == 409
    assert files(n['project']) == before and len(n['calls']) == 1


@pytest.mark.parametrize('change', ['prompt', 'spec', 'phase', 'newer_run'])
def test_changed_host_authority_rejects_old_retry(native, change):
    n = native
    body = retry_body(n['client'].get(n['url']).json())
    manifest = json.loads(n['path'].read_text())
    if change == 'prompt':
        n['prompt'].write_text('A changed, separately authorized complete story.')
    elif change == 'spec':
        manifest['spec']['title'] = 'A changed author title'
        n['path'].write_text(json.dumps(manifest))
    elif change == 'phase':
        manifest['current_phase'] = 'chapter.write'
        manifest['current_chapter'] = 1
        n['path'].write_text(json.dumps(manifest))
    else:
        later = n['path'].parent.parent / 'newer-run/run.json'
        later.parent.mkdir()
        manifest.update(run_id='newer-run', created_at='2099-01-01T00:00:00Z')
        later.write_text(json.dumps(manifest))
    before = files(n['project'])
    assert n['client'].get(n['url']).json()['can_resume'] is False
    assert n['client'].post(n['url'] + '/resume').status_code == 409
    rejected = n['client'].post(n['url'] + '/resume', json=body)
    assert rejected.status_code == 409
    assert files(n['project']) == before and len(n['calls']) == 1


def test_legacy_unknown_probe_rebinds_original_request_without_model_call(native):
    n = native
    recovery_root = recovery_host.directory(n['project'], n['manifest']['run_id'])
    (recovery_root / 'current.json').unlink()
    manifest = json.loads(n['path'].read_text())
    manifest.update(status='failed', error='RequestOutcomeUnknown: Original remote result unconfirmed')
    n['path'].write_text(json.dumps(manifest))
    receipt = n['receipts'][0]
    original = receipt.read_bytes()
    before = files(n['project'])
    detail = n['client'].get(n['url']).json()
    assert detail['request_recovery'] is None and detail['can_resume'] is True
    assert files(n['project']) == before
    submitted = n['client'].post(n['url'] + '/resume')
    assert submitted.status_code == 202, submitted.text
    assert _wait_job(n['jobs'], submitted.json()['job_id'])['status'] == 'error'
    detail = n['client'].get(n['url']).json()
    assert detail['can_resume'] is False and detail['request_recovery']['can_retry_unknown']
    assert receipt.read_bytes() == original and len(n['calls']) == 1
    probe = json.loads((recovery_root / 'probe.json').read_text())
    pointer = json.loads((recovery_root / 'current.json').read_text())
    assert probe['captured'] == {'pointer_sha256': pointer['sha256'], 'request': pointer['request']}
    assert probe['resolved'] is False
    retried = n['client'].post(n['url'] + '/resume', json=retry_body(detail))
    assert retried.status_code == 202, retried.text
    assert _wait_job(n['jobs'], retried.json()['job_id'])['status'] == 'done'
    assert len(n['calls']) == 2
    assert json.loads((recovery_root / 'probe.json').read_text())['resolved'] is True


def test_legacy_probe_stays_no_send_after_prompt_reconstruction_drift(native):
    n = native
    (recovery_host.directory(n['project'], n['manifest']['run_id']) / 'current.json').unlink()
    manifest = json.loads(n['path'].read_text())
    manifest.update(status='failed', error='RequestOutcomeUnknown: Original remote result unconfirmed')
    n['path'].write_text(json.dumps(manifest))
    original = n['receipts'][0].read_bytes()
    n['system'][0] = 'A newer runtime template cannot impersonate the original request'
    for _ in range(2):
        submitted = n['client'].post(n['url'] + '/resume')
        assert submitted.status_code == 202, submitted.text
        assert _wait_job(n['jobs'], submitted.json()['job_id'])['status'] == 'error'
        assert len(n['calls']) == 1 and n['receipts'][0].read_bytes() == original
        detail = n['client'].get(n['url']).json()
        assert detail['request_recovery'] is None


def test_late_complete_response_can_resume_without_purchasing_generation(native):
    n = native
    receipt = n['receipts'][0]
    record = json.loads(receipt.read_text())
    raw = 'Complete outline saved after the local interruption'
    record.update(state='received', response=raw, response_sha256=hashlib.sha256(raw.encode()).hexdigest())
    receipt.write_text(json.dumps(record))
    before = files(n['project'])
    detail = n['client'].get(n['url']).json()
    assert detail['can_resume'] and detail['request_recovery']['can_resume']
    assert not detail['request_recovery']['can_retry_unknown']
    assert files(n['project']) == before
    submitted = n['client'].post(n['url'] + '/resume')
    assert submitted.status_code == 202, submitted.text
    assert _wait_job(n['jobs'], submitted.json()['job_id'])['status'] == 'done'
    assert len(n['calls']) == 1


def test_corrupt_current_request_fails_closed_without_get_or_resume_rewrite(native):
    n = native
    receipt = n['receipts'][0]
    record = json.loads(receipt.read_text())
    record['user'] = 'Altered request without matching its immutable identity'
    receipt.write_text(json.dumps(record))
    before = files(n['project'])
    detail = n['client'].get(n['url']).json()
    assert detail['can_resume'] is False
    assert detail['request_recovery']['reason'] == 'invalid_binding'
    assert detail['request_recovery']['can_retry_unknown'] is False
    assert n['client'].post(n['url'] + '/resume').status_code == 409
    assert files(n['project']) == before and len(n['calls']) == 1


def start_probe(n):
    root = recovery_host.directory(n['project'], n['manifest']['run_id'])
    (root / 'current.json').unlink()
    manifest = json.loads(n['path'].read_text())
    manifest.update(status='failed', error='RequestOutcomeUnknown: Original remote result unconfirmed')
    n['path'].write_text(json.dumps(manifest))
    assert recovery_host.prepare_resume(n['project'], manifest) is True
    return root, manifest


def earlier_completion(n, root, manifest):
    system, user, response = 'Earlier completed rules', n['prompt'].read_text(), 'Earlier complete result'
    identity = digest({'system': system, 'user': user})
    path = n['receipts'][0].parent / (identity + '.json')
    atomic_json(path, {'system': system, 'user': user, 'state': 'received', 'response': response,
                      'response_sha256': hashlib.sha256(response.encode()).hexdigest()})
    with request_recovery_scope(n['project'], root / 'current.json', recovery_host.authority(n['project'], manifest)):
        assert SourceRequestSession(path, identity, kind='evidence').execute(
            lambda *_: pytest.fail('A saved response must not call a model')) == response


def test_earlier_complete_pointer_does_not_retire_uncaptured_probe(native):
    n = native
    root, manifest = start_probe(n)
    earlier_completion(n, root, manifest)
    before = files(n['project'])
    assert recovery_host.projection(n['project'], manifest) is None
    assert recovery_host.prepare_resume(n['project'], manifest) is True
    assert files(n['project']) == before
    submitted = n['client'].post(n['url'] + '/resume')
    assert submitted.status_code == 202
    assert _wait_job(n['jobs'], submitted.json()['job_id'])['status'] == 'error'
    assert n['client'].get(n['url']).json()['request_recovery']['can_retry_unknown']
    assert len(n['calls']) == 1


@pytest.mark.parametrize('damage', ['symlink', 'list', 'resolved_string', 'wrong_authority'])
def test_probe_marker_validates_before_completed_pointer_branch(native, damage):
    n = native
    root, manifest = start_probe(n)
    earlier_completion(n, root, manifest)
    path = root / 'probe.json'
    marker = json.loads(path.read_text())
    if damage == 'symlink':
        retained = root / 'retained-marker.json'
        path.rename(retained)
        path.symlink_to(retained)
    elif damage == 'list':
        path.write_text('[]')
    else:
        if damage == 'resolved_string':
            marker['resolved'] = 'false'
        else:
            marker['authority']['spec_sha256'] = 'f' * 64
        marker.pop('sha256')
        marker['sha256'] = digest(marker)
        path.write_text(json.dumps(marker))
    before = files(n['project'])
    with pytest.raises(RequestRecoveryError):
        recovery_host.prepare_resume(n['project'], manifest)
    detail = n['client'].get(n['url']).json()
    assert detail['can_resume'] is False
    assert n['client'].post(n['url'] + '/resume').status_code == 409
    assert files(n['project']) == before and len(n['calls']) == 1


def test_unresolved_probe_authority_cannot_be_replaced_after_input_change(native):
    n = native
    root, manifest = start_probe(n)
    n['prompt'].write_text('A later edited source cannot replace the frozen legacy probe.')
    before = files(n['project'])
    with pytest.raises(RequestRecoveryError):
        recovery_host.prepare_resume(n['project'], manifest)
    assert n['client'].post(n['url'] + '/resume').status_code == 409
    assert files(n['project']) == before and len(n['calls']) == 1


def capture_probe(n):
    root, _ = start_probe(n)
    submitted = n['client'].post(n['url'] + '/resume')
    assert submitted.status_code == 202
    assert _wait_job(n['jobs'], submitted.json()['job_id'])['status'] == 'error'
    return root, json.loads(n['path'].read_text())


def test_exact_captured_terminal_failure_can_retire_probe(native, monkeypatch):
    n = native
    root, manifest = capture_probe(n)
    monkeypatch.setattr(SourceRequestSession, '_terminal_failure', lambda self, record:
        {'outcome': 'failed', 'reason': 'http_error', 'http_status': 402})
    recovery = recovery_host.projection(n['project'], manifest)
    assert recovery['can_resume'] and not recovery['response_available']
    assert recovery_host.prepare_resume(n['project'], manifest) is False
    assert json.loads((root / 'probe.json').read_text())['resolved'] is True
    assert len(n['calls']) == 1


def test_completed_capture_retires_fence_for_later_stage(native):
    n = native
    root, manifest = capture_probe(n)
    receipt = n['receipts'][0]
    record = json.loads(receipt.read_text())
    response = 'Original request completed after interruption'
    record.update(state='received', response=response, response_sha256=hashlib.sha256(response.encode()).hexdigest())
    receipt.write_text(json.dumps(record))
    assert recovery_host.prepare_resume(n['project'], manifest) is False
    manifest['stages']['outline']['status'] = 'done'
    manifest.update(current_phase='chapter.plan', current_chapter=1, error='Later connection setup failed')
    assert recovery_host.projection(n['project'], manifest) is None
    assert recovery_host.prepare_resume(n['project'], manifest) is False
    assert recovery_host.needs_probe(n['project'], manifest) is False
    rules, user = 'New chapter planning rules', 'Already confirmed outline'
    identity = digest({'system': rules, 'user': user})
    new_request = receipt.parent / (identity + '.json')
    atomic_json(new_request, {'system': rules, 'user': user, 'state': 'prepared'})
    with recovery_host.stage_scope(n['project'], RunManifest.from_dict(manifest)):
        assert SourceRequestSession(new_request, identity, kind='evidence').execute(
            lambda *_: n['calls'].append(('new stage', user)) or 'New stage response') == 'New stage response'
    assert len(n['calls']) == 2


def test_capture_marker_storage_failure_cannot_offer_paid_retry(native, monkeypatch):
    n = native
    root, _ = start_probe(n)
    original_marker = (root / 'probe.json').read_bytes()
    save = recovery_host.atomic_json
    def reject_marker(path, value):
        if path.name == 'probe.json':
            raise OSError('Local marker storage failure')
        return save(path, value)
    monkeypatch.setattr(recovery_host, 'atomic_json', reject_marker)
    submitted = n['client'].post(n['url'] + '/resume')
    assert submitted.status_code == 202
    assert _wait_job(n['jobs'], submitted.json()['job_id'])['status'] == 'error'
    assert (root / 'probe.json').read_bytes() == original_marker and len(n['calls']) == 1
    assert n['client'].get(n['url']).json()['request_recovery'] is None
    original_request = SourceRequestSession(n['receipts'][0], n['receipts'][0].stem, kind='evidence').recovery()
    rejected = n['client'].post(n['url'] + '/resume', json=retry_body({'request_recovery': original_request}))
    assert rejected.status_code == 409 and len(n['calls']) == 1
