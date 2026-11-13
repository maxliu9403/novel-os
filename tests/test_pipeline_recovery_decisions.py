"""A verified complete response must not block later native author decisions."""
import json

import pytest

from api import project_tasks, project_task_summary
from api.jobs import JobRunner
from core import pipeline_request_recovery as recovery_host
from core.creative_types import digest
from core.planning_author_decisions import pending_decisions
from core.reference_assets import atomic_json
from core.source_request_session import SourceRequestSession
from pipeline_models import RunSpec, StageResult
from pipeline_runner import PipelineError, PipelineRunner
from test_planning_evidence_recovery import _waiting
from test_planning_upgrade import saved_run
from test_project_tasks import _client, _seed, _wait_job


def bind_response(project, manifest, *, pending=False):
    system, user = 'Exact saved request rules', 'Exact author context'
    identity = digest({'system': system, 'user': user})
    path = project / 'outputs/source-evidence/requests' / f'{identity}.json'
    atomic_json(path, {'system': system, 'user': user, 'state': 'prepared'})
    request = SourceRequestSession(path, identity, kind='evidence')
    calls = []

    def model(system, user):
        calls.append((system, user))
        if pending:
            raise TimeoutError('Local wait ended without a complete result')
        return 'Verified complete saved response'

    with recovery_host.stage_scope(project, manifest):
        if pending:
            with pytest.raises(TimeoutError):
                request.execute(model)
        else:
            assert request.execute(model) == 'Verified complete saved response'
    recovery = recovery_host.projection(project, manifest)
    if pending:
        recovery_host.authorize(project, manifest.to_dict(), {
            'operation_id': 'explicit-retry', 'request_sha256': request.identity,
            'receipt_sha256': recovery['receipt_sha256'],
        })
        assert recovery_host.projection(project, manifest)['can_resume']
    return request, calls


def waiting_run(tmp_path):
    project, manifest, path = _seed(tmp_path, run_id='test-run', outline_planning_mode='staged')
    manifest.stages.clear()
    manifest.record(StageResult(phase='outline', status='failed', error='Waiting for author choice'))
    path.write_text(json.dumps(manifest.to_dict()))
    planner, conflicts, questions = _waiting(project)
    decision = {'request_id': questions['request_id'], 'choices': [{'issue_index': 4, 'selected_quote': 1}]}
    return project, manifest, path, planner, conflicts, decision


def track_native_execution(monkeypatch, planner=None, conflicts=None):
    executions = []

    def execute(self, manifest, project, store):
        if planner:
            executions.append(pending_decisions(planner, conflicts)[1])
        else:
            executions.append(manifest.spec.outline_planning_mode)
        manifest.status = 'completed'
        store.save(manifest)
        return manifest

    monkeypatch.setattr(PipelineRunner, '_execute', execute)
    return executions


def test_native_author_choice_follows_verified_complete_request(tmp_path, monkeypatch):
    project, manifest, _, planner, conflicts, decision = waiting_run(tmp_path)
    request, calls = bind_response(project, manifest)
    original = request.path.read_bytes()
    seen = track_native_execution(monkeypatch, planner, conflicts)
    result = PipelineRunner(project).resume(manifest.run_id, author_decisions=decision)
    assert result.status == 'completed'
    assert seen == [{4: {**decision['choices'][0], 'author_quote': conflicts[0]['author_quotes'][1]}}]
    assert len(calls) == 1 and request.path.read_bytes() == original


def test_native_pending_retry_still_blocks_author_choice_without_saving_it(tmp_path, monkeypatch):
    project, manifest, _, planner, conflicts, decision = waiting_run(tmp_path)
    request, calls = bind_response(project, manifest, pending=True)
    original = request.path.read_bytes()
    seen = track_native_execution(monkeypatch, planner, conflicts)
    with pytest.raises(PipelineError, match='先完成当前模型请求'):
        PipelineRunner(project).resume(manifest.run_id, author_decisions=decision)
    assert not list(planner.cache.glob('author-answer-*.json'))
    assert not seen and len(calls) == 1 and request.path.read_bytes() == original


@pytest.mark.parametrize('pending', [False, True])
def test_task_api_author_choice_only_saves_after_current_response_is_complete(tmp_path, monkeypatch, pending):
    project, manifest, _, planner, conflicts, decision = waiting_run(tmp_path)
    request, calls = bind_response(project, manifest, pending=pending)
    original = request.path.read_bytes()
    jobs = JobRunner()
    for module in (project_tasks, project_task_summary):
        monkeypatch.setattr(module, 'runner', jobs)
    seen = track_native_execution(monkeypatch, planner, conflicts)
    client = _client(tmp_path)
    url = f'/api/projects/book/tasks/{manifest.run_id}'
    assert client.get(url).json()['planning']['author_decisions']['request_id'] == decision['request_id']
    response = client.post(url + '/resume', json={'author_decisions': decision})
    if pending:
        assert response.status_code == 409, response.text
        assert not list(planner.cache.glob('author-answer-*.json'))
        assert not seen
    else:
        assert response.status_code == 202, response.text
        assert _wait_job(jobs, response.json()['job_id'])['status'] == 'done'
        assert seen == [{4: {**decision['choices'][0], 'author_quote': conflicts[0]['author_quotes'][1]}}]
    assert len(calls) == 1 and request.path.read_bytes() == original


@pytest.mark.parametrize('explicit', [False, True])
def test_native_legacy_upgrade_remains_available_after_completed_request(tmp_path, monkeypatch, explicit):
    manifest, store = saved_run(tmp_path)
    original_manifest = manifest.to_dict()
    request, calls = bind_response(tmp_path, manifest)
    original = request.path.read_bytes()
    seen = track_native_execution(monkeypatch)
    options = {'outline_planning_mode': 'staged'} if explicit else {}
    result = PipelineRunner(tmp_path).resume(manifest.run_id, **options)
    assert result.status == 'completed' and seen == ['staged']
    assert json.loads((store.path.parent / 'pre-staged-planning-run.json').read_text()) == original_manifest
    assert len(calls) == 1 and request.path.read_bytes() == original


def test_native_pending_retry_blocks_planning_upgrade(tmp_path, monkeypatch):
    manifest, store = saved_run(tmp_path)
    request, calls = bind_response(tmp_path, manifest, pending=True)
    original, original_manifest = request.path.read_bytes(), store.path.read_bytes()
    seen = track_native_execution(monkeypatch)
    with pytest.raises(PipelineError, match='先完成当前模型请求'):
        PipelineRunner(tmp_path).resume(manifest.run_id, outline_planning_mode='staged')
    assert not seen and len(calls) == 1
    assert request.path.read_bytes() == original and store.path.read_bytes() == original_manifest


@pytest.mark.parametrize('pending', [False, True])
def test_task_api_reports_and_executes_upgrade_only_after_response_is_complete(tmp_path, monkeypatch, pending):
    project, manifest, path = _seed(tmp_path, outline_planning_mode='legacy')
    manifest.stages.clear()
    manifest.record(StageResult(phase='outline', status='failed', error='Saved outline review ended'))
    path.write_text(json.dumps(manifest.to_dict()))
    request, calls = bind_response(project, manifest, pending=pending)
    original = request.path.read_bytes()
    jobs = JobRunner()
    for module in (project_tasks, project_task_summary):
        monkeypatch.setattr(module, 'runner', jobs)
    seen = track_native_execution(monkeypatch)
    client = _client(tmp_path)
    url = f'/api/projects/book/tasks/{manifest.run_id}'
    detail = client.get(url)
    assert detail.status_code == 200, detail.text
    assert detail.json()['can_upgrade_planning'] is (not pending)
    response = client.post(url + '/resume', json={'outline_planning_mode': 'staged'})
    if pending:
        assert response.status_code == 409, response.text
        assert not seen
    else:
        assert response.status_code == 202, response.text
        assert _wait_job(jobs, response.json()['job_id'])['status'] == 'done'
        assert seen == ['staged']
    assert len(calls) == 1 and request.path.read_bytes() == original


def test_fresh_run_on_reused_runner_does_not_inherit_capture_only_probe(tmp_path, monkeypatch):
    calls = []

    def execute(self, manifest, project, store):
        assert self._request_recovery_probe is False
        system, user = 'New run rules', 'New complete author brief'
        identity = digest({'system': system, 'user': user})
        path = project / 'outputs/source-evidence/requests' / f'{identity}.json'
        atomic_json(path, {'system': system, 'user': user, 'state': 'prepared'})
        request = SourceRequestSession(path, identity, kind='evidence')
        manifest.current_phase = 'outline'
        with recovery_host.stage_scope(project, manifest, capture_only=self._request_recovery_probe):
            result = request.execute(lambda system, user: calls.append((system, user)) or 'New response')
        assert result == 'New response'
        manifest.status = 'completed'
        return manifest

    monkeypatch.setattr(PipelineRunner, '_execute', execute)
    runner = PipelineRunner(tmp_path)
    runner._request_recovery_probe = True
    result = runner.run(RunSpec(project_path=str(tmp_path), prompt_path='-', method_policy={'mode': 'off'}))
    assert result.status == 'completed'
    assert calls == [('New run rules', 'New complete author brief')]
