"""Known rejections are recoverable without turning ambiguous requests into failures."""
import json

import pytest

from core.creative_types import digest
from core.llm_attempt import completion_attempt, recover_terminal_failure
from core.llm_client import LLMError, LLMHTTPError
from core.reference_assets import atomic_json
from core.source_request_recovery import classify_failure, failure_message
from core.source_request_session import SourceRequestSession, RequestOutcomeUnknown
from core.source_routes import VERSION


TERMINAL = json.dumps({'type': 'turn.failed', 'error': {'message':
    'unexpected status 402 Payment Required: {"detail":{"code":"deactivated_workspace"}}, '
    'url: https://private.example?secret=PRIVATE_TOKEN'}})


def saved_attempt(tmp_path, *, kind='design', terminal=TERMINAL, exit_event='cli.failed', bind=True):
    system, user = 'complete original source', 'all author requirements'
    payload = {'source': user}
    identity = digest([VERSION, system, payload]) if kind == 'design' else digest({'system': system, 'user': user})
    path = tmp_path / f'{identity}.json'
    request = SourceRequestSession(path, identity, kind=kind)
    attempt_id = 'a' * 32
    directory = request.directory / 'attempts' / attempt_id
    failure = classify_failure(LLMError('private HTTP 402 log is not proof'))
    record = {'system': system, 'payload': payload, 'user': user, 'request_sha256': identity,
              request.status_key: 'pending_unknown' if kind == 'design' else 'dispatched',
              'active_attempt_id': attempt_id, 'failure': failure,
              'attempts': [{'attempt_id': attempt_id, 'failure': failure}]}
    atomic_json(path, record)
    with completion_attempt(directory, identity) as attempt:
        if bind:
            attempt.bind_request(system, user, provider='codex', model='old-model')
        attempt.capture_cli_output(terminal, '')
        attempt.record_diagnostic({'event': exit_event, 'exit_code': 1})
    return request, path, directory


@pytest.mark.parametrize('kind', ['design', 'evidence'])
def test_legacy_terminal_projection_is_read_only_and_resume_preserves_history(tmp_path, kind):
    request, path, directory = saved_attempt(tmp_path, kind=kind)
    original = path.read_bytes()
    artifacts = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    view = request.recovery()
    assert view['can_resume'] and not view['can_retry_unknown']
    assert view['execution']['http_status'] == 402
    assert view['execution']['provider_code'] == 'deactivated_workspace'
    assert '工作区已停用' in view['message']
    assert 'PRIVATE_TOKEN' not in json.dumps(view)
    assert path.read_bytes() == original
    calls = []
    class CurrentModel:
        model = 'current-model'
        def complete(self, system, user):
            calls.append((system, user))
            return '{"complete":true}'
    assert request.execute(CurrentModel()) == '{"complete":true}'
    assert len(calls) == 1
    record = json.loads(path.read_text())
    assert record['attempts'][0] == json.loads(original)['attempts'][0]
    assert record['attempts'][1]['model']['model'] == 'current-model'
    archive = request.directory / 'history' / (record['terminal_failure_recovery']['receipt_sha256'] + '.json')
    assert archive.read_bytes() == original
    assert all(p.read_bytes() == value for p, value in artifacts.items())
    assert request.execute(CurrentModel()) == '{"complete":true}'
    assert len(calls) == 1


@pytest.mark.parametrize('changes', [
    {'terminal': json.dumps({'type': 'error', 'message': 'HTTP 402 deactivated_workspace'})},
    {'exit_event': 'cli.timeout'}, {'exit_event': 'cli.cancelled'}, {'exit_event': 'cli.completed'},
    {'bind': False},
])
def test_ambiguous_or_unbound_failure_cannot_authorize_ordinary_resume(tmp_path, changes):
    request, _, _ = saved_attempt(tmp_path, **changes)
    assert not request.recovery()['can_resume']
    with pytest.raises(RequestOutcomeUnknown):
        request.execute(lambda *_: pytest.fail('No retry without proof'))


def test_completed_response_and_live_writer_take_precedence_over_failure(tmp_path):
    request, _, directory = saved_attempt(tmp_path)
    with completion_attempt(directory, request.identity) as attempt:
        assert recover_terminal_failure(directory, request.identity) is None
        attempt.save_response('complete original result')
    assert recover_terminal_failure(directory, request.identity) is None
    assert request.execute(lambda *_: pytest.fail('Must reuse response')) == 'complete original result'


def test_uncommitted_final_output_prevents_inferred_failure(tmp_path):
    request, _, directory = saved_attempt(tmp_path)
    (directory / 'cli' / 'last-message.txt').write_text('retained final or partial output')
    assert recover_terminal_failure(directory, request.identity) is None
    with pytest.raises(RequestOutcomeUnknown):
        request.execute(lambda *_: pytest.fail('Keep output and do not guess'))


@pytest.mark.parametrize('artifact', ['attempt.json', 'request.json'])
def test_mismatched_attempt_cannot_dispatch(tmp_path, artifact):
    request, _, directory = saved_attempt(tmp_path)
    data = json.loads((directory / artifact).read_text())
    data['identity'] = 'b' * 64
    atomic_json(directory / artifact, data)
    with pytest.raises(ValueError, match='mismatch'):
        request.execute(lambda *_: pytest.fail('Must not dispatch'))


def test_fresh_http_failure_is_explicit_and_nonretryable(tmp_path):
    error = LLMHTTPError(402, provider_code='deactivated_workspace')
    failure = classify_failure(error)
    assert failure['outcome'] == 'failed' and not failure['retryable']
    assert failure['provider_code'] == 'deactivated_workspace'
    assert '恢复该工作区' in failure_message(failure)
    from api.workshop import _failure
    message = _failure(error, timeout_seconds=900, has_prompt=False)
    assert '工作区已停用' in message and '基础提示与完整对话已保留' in message
    assert classify_failure(LLMError(str(error)))['outcome'] == 'unknown'
    assert 'provider_code' not in classify_failure(LLMHTTPError(402, provider_code='PRIVATE_TOKEN'))


def test_archive_failure_prevents_dispatch_and_keeps_original(tmp_path, monkeypatch):
    import core.source_request_session as module
    request, path, _ = saved_attempt(tmp_path)
    original = path.read_bytes()
    def cannot_save(*args):
        raise OSError('storage unavailable')
    monkeypatch.setattr(module, 'atomic_bytes', cannot_save)
    with pytest.raises(OSError):
        request.execute(lambda *_: pytest.fail('Must archive before resume'))
    assert path.read_bytes() == original
