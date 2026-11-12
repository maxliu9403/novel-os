"""The public naming endpoint uses both bases through the real worker and store."""
from copy import deepcopy
import json

import pytest

from core import llm_client, provider_settings
from api import workshop
from test_title_basis_projection import overview, source_context
from test_workshop_api import client, finish, ready  # noqa: F401


@pytest.mark.parametrize('reference', [True, False])
def test_naming_endpoint_freezes_lightweight_basis_and_preserves_story(client, monkeypatch, tmp_path, reference):
    data = ready(client, monkeypatch, tmp_path)
    path = tmp_path / 'workshop_v2' / data['id'] / 'session.json'
    saved = json.loads(path.read_text())
    # Old and new tasks may already retain considerable discussion/host data.
    saved['creative_design'] = {
        'story_state': {'map': overview(), 'actions': [], 'runtime_details': 'RETAINED_HOST_RECORD' * 10000},
        'rounds': [{'source_revision': 3, 'message_index': len(saved['messages']) - 1}],
    }
    saved['messages'].append({'role': 'user', 'content': 'Keep the heroine independent.'})
    if reference:
        saved['source_context'] = source_context()
    path.write_text(json.dumps(saved))
    before = deepcopy(saved)
    versions = {p.name: p.read_bytes() for p in (path.parent / 'versions').glob('*') if p.is_file()}
    calls = []
    monkeypatch.setattr(provider_settings, 'resolve_text_route', lambda _: {
        'provider': 'stub', 'model': 'naming', 'base_url': '', 'api_key': '', 'max_tokens': 4096})

    class Client:
        def __init__(self, **_):
            pass

        def complete(self, system, user):
            calls.append(json.loads(user))
            return json.dumps({'candidates': [
                {'title': f'She Chose Her Own Future {i}', 'angle': '独立的选择',
                 'reason': '以人物自主回应作者要求。', 'source_ids': ['story']}
                for i in range(5)]})

    monkeypatch.setattr(llm_client, 'LLMClient', Client)
    url = f'/api/workshop/sessions/{data["id"]}'
    response = client.post(url + '/titles', json={
        'revision': data['revision'], 'instructions': 'Use English; emphasize her choice.'})
    assert response.status_code == 202, response.text
    result = finish(client, data['id'])
    assert result['status'] == 'ready', result.get('last_error')
    assert len(calls) == 1
    assert calls[0]['basis_kind'] == ('reference_route' if reference else 'story_overview')
    assert 'Keep the heroine independent.' in json.dumps(calls[0])
    assert 'RETAINED_HOST_RECORD' not in json.dumps(calls[0])
    request = result['naming']['requests'][-1]
    assert request['title_context']['context'] == calls[0]
    assert request['title_context']['context_bytes'] < 10_000
    assert request['metrics']['attempts'] == 1
    for key in ('messages', 'creative_design', 'source_context'):
        assert result.get(key) == before.get(key)
    # The existing endpoint confirms host format metadata, without rewriting story.
    assert result['prompt_md'] == workshop._confirm_format(before['prompt_md'])
    assert {p.name: p.read_bytes() for p in (path.parent / 'versions').glob('*') if p.is_file()} == versions
