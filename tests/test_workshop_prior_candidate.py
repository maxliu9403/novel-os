"""Public delivery/resume must retrieve its exact source after the worker exits."""
import hashlib
import json

import pytest

from api import workshop_checkpoints
from core import llm_client, provider_settings
from test_workshop_api import client, create, edit_session_file, finish  # noqa: F401
from workshop_delivery_fixtures import chapter_plan, story_design


@pytest.mark.parametrize('change', ['newer_candidate', 'changed_original', 'missing_original'])
def test_workshop_resume_binds_prior_candidate_through_chapter_windows(client, tmp_path, monkeypatch, change):
    session = create(client, chapters=100)
    sid = session['id']
    edit_session_file(tmp_path, sid, creative_design={})  # Historical session with a prior failed skeleton.
    directory = tmp_path / 'workshop_v2' / sid
    candidates = directory / 'candidates'
    candidates.mkdir()
    original = 'Only the original chapter 83 carries the condition.\r\nMara promised not to sell.\n'
    source = candidates / '0.md'
    source.write_bytes(original.encode())
    route = {'provider': 'fake', 'model': 'fixture', 'timeout_seconds': 30, 'max_tokens': 1200,
             'base_url': None, 'api_key': None}
    monkeypatch.setattr(provider_settings, 'resolve_text_route', lambda _: dict(route))
    requests = []

    class Model:
        def __init__(self, **kwargs):
            pass

        def complete(self, system, user):
            ctx = json.loads(user)
            assert ctx['previous_unvalidated_skeleton'] == original
            if ctx['delivery_stage'] == 'story_design':
                requests.append('global')
                return json.dumps(story_design(100))
            window = ctx['chapter_window']
            first, last = window['first'], window['last']
            requests.append((first, last))
            if first == 1:
                return json.dumps({**chapter_plan(100), 'chapters': chapter_plan(100)['chapters'][:last]})
            raise TimeoutError('Simulated interrupted delivery')

    monkeypatch.setattr(llm_client, 'LLMClient', Model)
    path = f'/api/workshop/sessions/{sid}'
    response = client.post(path + '/turns', json={'message': 'Continue using the earlier skeleton.',
                                                'revision': 0, 'intent': 'deliver'})
    assert response.status_code == 202, response.text
    result = finish(client, sid)
    assert requests == ['global', (1, 2), (3, 4)], result['last_error']
    checkpoint = workshop_checkpoints.load_checkpoint(directory / 'delivery', result['delivery_run_id'])
    assert checkpoint['prior_candidate'] == {'revision': 0, 'sha256': hashlib.sha256(original.encode()).hexdigest()}
    assert len(checkpoint['chapter_windows']['batches']) == 1
    (candidates / '1.md').write_text('Wrong newer unvalidated draft', encoding='utf-8')
    if change == 'changed_original':
        source.write_text('Modified source', encoding='utf-8')
    elif change == 'missing_original':
        source.unlink()
    requests.clear()
    response = client.post(path + '/resume', json={'revision': result['revision']})
    assert response.status_code == (202 if change == 'newer_candidate' else 409), response.text
    result = finish(client, sid)
    assert requests == ([(3, 4)] if change == 'newer_candidate' else [])
    assert result['status'] == 'error'
    assert result['delivery_progress']['resumable'] is (change == 'newer_candidate')
    assert workshop_checkpoints.load_checkpoint(directory / 'delivery', result['delivery_run_id']) == checkpoint
