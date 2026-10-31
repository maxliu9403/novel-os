import importlib
import json
import threading
import time

import pytest

from pipeline_runtime import PipelineRuntime, PipelineStopped, check_cancelled, current_runtime, runtime_stage


def test_runtime_import_alias_and_deadline(tmp_path):
    assert importlib.import_module('core.pipeline_runtime').current_runtime is current_runtime
    with PipelineRuntime(tmp_path, 'run', request_timeout=1, stage_timeout=.02) as runtime:
        with pytest.raises(PipelineStopped, match='stage_timeout'):
            with runtime_stage('outline'):
                time.sleep(.03)
                check_cancelled()
    assert json.loads(runtime.path.read_text())['status'] == 'paused'


def test_diagnostics_only_content_is_model_activity_and_late_events_ignored(tmp_path):
    with PipelineRuntime(tmp_path, 'run') as runtime:
        runtime.diagnostic({'event': 'request.start', 'request_id': 'r', 'model': 'fake'})
        runtime.diagnostic({'event': 'heartbeat', 'prompt': 'SECRET'})
        runtime.diagnostic({'event': 'upstream.event', 'event_kind': 'codex.api_request'})
        first = json.loads(runtime.path.read_text())
        assert first['last_model_activity_at'] is None
        assert first['last_upstream_activity_at']
        runtime.diagnostic({'event': 'upstream.activity', 'activity_kind': 'transport', 'metric_count': 3})
        assert json.loads(runtime.path.read_text())['last_model_activity_at'] is None
        runtime.diagnostic({'event': 'upstream.activity', 'activity_kind': 'reasoning_summary', 'metric_count': 2, 'summary': 'SECRET'})
        assert json.loads(runtime.path.read_text())['request']['model_activity_count'] == 2
        runtime.diagnostic({'event': 'cli_event', 'model_output_chars': 12, 'text': 'SECRET'})
        assert json.loads(runtime.path.read_text())['last_model_activity_at']
    before = runtime.path.read_bytes()
    runtime.diagnostic({'event': 'cli_event', 'model_output_chars': 999})
    assert runtime.path.read_bytes() == before
    assert b'SECRET' not in before
    assert 'SECRET' not in runtime.path.with_name('requests.jsonl').read_text()


def test_cached_client_cancelled_output_cannot_escape_scope(tmp_path):
    from llm_client import LLMClient
    client = object.__new__(LLMClient)
    client.provider_name, client.model = 'fake', 'fake'
    client.cancellation_event = client.diagnostic_callback = client.timeout_seconds = None
    cancel = threading.Event()
    def complete(*args):
        assert client.timeout_seconds <= 900
        assert client.cancellation_event is cancel
        cancel.set()
        return 'late output must not be committed'
    client._complete_observed = complete
    with PipelineRuntime(tmp_path, 'run', cancellation_event=cancel):
        with pytest.raises(PipelineStopped):
            client.complete('private prompt', 'private story')
    assert client.cancellation_event is None


def test_timeout_and_cancel_never_retry_whole_stage():
    from pipeline_runner import PipelineRunner
    from llm_client import LLMError, LLMCancelledError
    import subprocess
    error = LLMError('model failed')
    error.__cause__ = subprocess.TimeoutExpired('fake-cli', 900)
    assert not PipelineRunner._is_retryable(error)
    assert not PipelineRunner._is_retryable(LLMCancelledError('cancelled'))
    assert not PipelineRunner._is_retryable(LLMError('Architect output contract failed: suitability dimensions are incomplete'))


@pytest.mark.parametrize('stop', ['timeout', 'cancel'])
def test_run_stops_once_and_resumes_verified_checkpoint(tmp_path, stop):
    from test_pipeline_runner import FakeOrchestrator
    from pipeline_runner import PipelineRunner
    from pipeline_models import RunSpec
    from llm_client import LLMCancelledError
    class Stopping(FakeOrchestrator):
        attempts = 0
        stop_once = True
        def write_chapter(self, number, dry_run=False):
            type(self).attempts += 1
            if type(self).stop_once:
                raise TimeoutError('bounded request') if stop == 'timeout' else LLMCancelledError('user pause')
            return super().write_chapter(number, dry_run)
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('A story.')
    project = tmp_path / 'book'
    runner = PipelineRunner(orchestrator_factory=Stopping)
    manifest = runner.run(RunSpec(project_path=str(project), prompt_path=str(prompt),
        num_chapters=1, target_words=20, approval_policy='auto', retry_backoff_seconds=0))
    assert manifest.status == 'paused', manifest.error
    assert Stopping.attempts == 1
    assert manifest.get('outline').status == 'done'
    assert manifest.get('chapter.write', 1).status == 'blocked'
    Stopping.stop_once = False
    result = runner.resume(manifest.run_id, project)
    assert result.status == 'completed', result.error
    assert Stopping.attempts == 2


def test_http_request_timeout_does_not_accept_late_result(tmp_path):
    from llm_client import LLMClient
    from types import SimpleNamespace
    release, started = threading.Event(), threading.Event()
    client = object.__new__(LLMClient)
    client.provider_name, client.model = 'fake', 'fake'
    client.timeout_seconds = client.cancellation_event = client.diagnostic_callback = None
    client._backend = SimpleNamespace(close=lambda: None)
    calls = []
    def call():
        calls.append(1)
        started.set()
        release.wait(1)
        return 'late'
    client._complete_observed = lambda *a: client._call_http(call)
    try:
        with PipelineRuntime(tmp_path, 'run', request_timeout=.03):
            with pytest.raises(PipelineStopped):
                client.complete('secret', 'secret')
        assert started.is_set() and calls == [1]
    finally:
        release.set()


def test_old_specs_keep_legacy_planning():
    from pipeline_models import RunSpec
    assert RunSpec(project_path='.').outline_planning_mode == 'staged'
    assert RunSpec.from_dict({'project_path': '.'}).outline_planning_mode == 'legacy'


def test_nested_stage_and_heartbeat_cannot_extend_parent_budget(tmp_path):
    with PipelineRuntime(tmp_path, 'run', stage_timeout=.04):
        with pytest.raises(PipelineStopped):
            with runtime_stage('parent'):
                time.sleep(.025)
                with runtime_stage('child'):
                    time.sleep(.025)
                    check_cancelled()


def test_runtime_start_failure_restores_context(tmp_path, monkeypatch):
    runtime = PipelineRuntime(tmp_path, 'run')
    monkeypatch.setattr(runtime, '_save', lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        with runtime:
            pass
    assert current_runtime() is None


def test_invalid_runtime_setting_does_not_leave_running_manifest(tmp_path, monkeypatch):
    from pipeline_runner import PipelineRunner
    from pipeline_models import RunSpec
    prompt = tmp_path / 'source.md'
    prompt.write_text('Story.')
    monkeypatch.setenv('NOVEL_OS_PIPELINE_REQUEST_TIMEOUT_SECONDS', 'invalid')
    result = PipelineRunner().run(RunSpec(project_path=str(tmp_path / 'book'), prompt_path=str(prompt), dry_run=True))
    assert result.status == 'failed'
    assert 'invalid' in result.error


def test_final_progress_write_failure_does_not_reverse_canonical_completion(tmp_path, monkeypatch):
    from pipeline_runner import PipelineRunner
    from pipeline_models import RunSpec
    prompt = tmp_path / 'source.md'
    prompt.write_text('Story.')
    original = PipelineRuntime._save
    def broken_at_finish(self):
        if self.data['status'] == 'completed':
            raise OSError('progress disk error')
        original(self)
    monkeypatch.setattr(PipelineRuntime, '_save', broken_at_finish)
    project = tmp_path / 'book'
    result = PipelineRunner().run(RunSpec(project_path=str(project), prompt_path=str(prompt), dry_run=True))
    assert result.status == 'completed', result.error
    assert json.loads((project / 'outputs/runs' / result.run_id / 'run.json').read_text())['status'] == 'completed'
    assert current_runtime() is None


def test_caught_timeout_remains_sticky_and_cannot_start_second_call(tmp_path):
    from llm_client import LLMClient
    client = object.__new__(LLMClient)
    client.provider_name, client.model = 'fake', 'fake'
    client.cancellation_event = client.diagnostic_callback = client.timeout_seconds = None
    calls = []
    def complete(*args):
        calls.append(1)
        raise TimeoutError('late')
    client._complete_observed = complete
    with PipelineRuntime(tmp_path, 'run'):
        with pytest.raises(PipelineStopped):
            client.complete('role', 'story')
        with pytest.raises(PipelineStopped):
            client.complete('role', 'story')
    assert len(calls) == 1


def test_cached_sdk_binds_timeout_and_disables_its_retries(tmp_path):
    from types import SimpleNamespace
    from llm_client import LLMClient
    calls = []
    bound = SimpleNamespace()
    backend = SimpleNamespace(with_options=lambda **kw: calls.append(kw) or bound)
    client = object.__new__(LLMClient)
    client.provider_name, client.model = 'fake', 'fake'
    client.cancellation_event = client.diagnostic_callback = client.timeout_seconds = None
    client._backend = backend
    def complete(*args):
        assert client._backend is bound
        return 'ok'
    client._complete_observed = complete
    with PipelineRuntime(tmp_path, 'run', request_timeout=20):
        assert client.complete('role', 'story') == 'ok'
    assert calls == [{'timeout': 20, 'max_retries': 0}]
    assert client._backend is backend


def test_safe_api_projection_ignores_private_extra_fields_and_bad_collections(tmp_path):
    from api.project_task_summary import task_sidecar
    directory = tmp_path / 'outputs/runs/run'
    directory.mkdir(parents=True)
    (directory / 'progress.json').write_text(json.dumps({
        'schema_version': 1, 'run_id': 'run', 'status': 'running',
        'prompt': 'PRIVATE', 'milestones': {}, 'request': {'prompt': 'PRIVATE', 'status': 'running'}}))
    result = task_sidecar(tmp_path, 'run', 'progress.json')
    assert result['milestones'] == []
    assert 'PRIVATE' not in json.dumps(result)
    (directory / 'planning').mkdir()
    (directory / 'planning/progress.json').write_text(json.dumps({
        'schema_version': 1, 'run_id': 'run', 'status': 'running', 'raw': 'PRIVATE',
        'steps': [{'id': 'global', 'prompt': 'PRIVATE', 'status': 'completed'}], 'milestones': None}))
    planning = task_sidecar(tmp_path, 'run', 'planning/progress.json')
    assert planning['steps'] == [{'id': 'global', 'status': 'completed'}]
    assert 'PRIVATE' not in json.dumps(planning)


def test_staged_outline_transport_failure_is_not_automatically_replayed(tmp_path):
    from test_pipeline_runner import FakeOrchestrator
    from pipeline_runner import PipelineRunner
    from pipeline_models import RunSpec
    from llm_client import LLMError
    class Failing(FakeOrchestrator):
        calls = 0
        def plan_outline(self, *args, **kwargs):
            type(self).calls += 1
            raise LLMError('upstream 500 EOF; remote result unknown')
    prompt = tmp_path / 'source.md'
    prompt.write_text('Story.')
    result = PipelineRunner(orchestrator_factory=Failing).run(RunSpec(
        project_path=str(tmp_path / 'book'), prompt_path=str(prompt), num_chapters=1,
        target_words=20, max_retries=5, retry_backoff_seconds=0))
    assert result.status == 'failed'
    assert Failing.calls == 1
    assert result.get('outline').retryable is False


def test_staged_chapter_plan_binds_canonical_foundation(tmp_path):
    from pipeline_runner import PipelineRunner
    from pipeline_models import RunManifest, RunSpec
    (tmp_path / 'outputs/input').mkdir(parents=True)
    (tmp_path / 'outputs/outline.md').write_text('same rendered outline')
    foundation = tmp_path / 'outputs/input/foundation.json'
    foundation.write_text('{"title":"original"}')
    manifest = RunManifest.new(RunSpec(project_path=str(tmp_path)))
    first = PipelineRunner._stage_input_hashes(tmp_path, 'chapter.plan', 1, manifest)
    foundation.write_text('{"title":"changed"}')
    second = PipelineRunner._stage_input_hashes(tmp_path, 'chapter.plan', 1, manifest)
    assert first['outputs/outline.md'] == second['outputs/outline.md']
    assert first['outputs/input/foundation.json'] != second['outputs/input/foundation.json']
