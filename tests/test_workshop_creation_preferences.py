"""Shared creation preferences reach ordinary and route-based discussions intact."""
from copy import deepcopy
import json

import pytest

from api import workshop
from api.workshop_source_handoff import RouteHandoffInput
from core.creative_types import digest
from test_creative_types import default_refs
from test_audience_workflow import refs
from test_source_route_handoff import setup_source
from test_workshop_api import client, finish, model_result  # noqa: F401


def selected():
    return {'creative_types': [r.model_dump() for r in default_refs()]}


@pytest.mark.parametrize('route', [False, True])
def test_new_creation_defaults_match_across_both_entrypoints(client, monkeypatch, route):
    observed = []
    monkeypatch.setattr(workshop, '_complete', lambda session, _: observed.append(deepcopy(session['input'])) or model_result())
    if route:
        _, _, _, _, body = setup_source(client)
        body['input'] = {**selected(), 'creation_flow': 'genre_tags.v2'}
        response = client.post('/api/workshop/source-route-handoffs', json=body)
        assert response.status_code == 202, response.text
        data = finish(client, response.json()['session_id'])
        assert observed, data
        assert observed[0] == data['input']
    else:
        response = client.post('/api/workshop/sessions', json={**selected(), 'creation_flow': 'genre_tags.v2'})
        assert response.status_code == 201, response.text
        data = response.json()
    assert {k: data['input'][k] for k in ('language', 'audience', 'market', 'audience_channel')} == {
        'language': 'English', 'audience': '欧美女性读者', 'market': '欧美', 'audience_channel': 'female'}
    assert data['creative_design']['policy']['type_snapshot']['audience_channel'] == 'female'


@pytest.mark.parametrize('route', [False, True])
def test_explicit_settings_and_empty_preferences_are_not_replaced_by_defaults(client, monkeypatch, route):
    fields = {**selected(), 'audience_channel': 'female', 'language': '简体中文',
              'audience': '', 'market': '', 'constraints': '不写怀孕情节。', 'title': '她的决定',
              'author': '笔名', 'premise': '她选择独立经营。', 'chapters': 12,
              'words_per_chapter': 1800, 'target_total_words': 21600, 'mode': 'short_novel',
              'method_mode': 'off'}
    observed = []
    monkeypatch.setattr(workshop, '_complete', lambda session, _: observed.append(deepcopy(session['input'])) or model_result())
    if route:
        _, _, _, _, body = setup_source(client)
        body['input'] = fields
        response = client.post('/api/workshop/source-route-handoffs', json=body)
        assert response.status_code == 202, response.text
        data = finish(client, response.json()['session_id'])
        assert observed, data
        assert observed[0] == data['input']
    else:
        response = client.post('/api/workshop/sessions', json=fields)
        assert response.status_code == 201, response.text
        data = response.json()
    for key, value in fields.items():
        assert data['input'][key] == value


@pytest.mark.parametrize('clear_total', [False, True])
def test_existing_discussion_route_binding_preserves_omitted_preferences(client, monkeypatch, clear_total):
    _, _, _, _, body = setup_source(client)
    custom = {**selected(), 'audience_channel': 'female', 'language': 'French',
              'audience': '成年女性悬疑读者', 'market': 'France', 'constraints': 'No secret children.',
              'title': 'Their Choice', 'author': 'A. Writer', 'premise': 'A missing ledger.',
              'chapters': 16, 'words_per_chapter': 1400, 'target_total_words': 22400,
              'mode': 'short_novel', 'method_mode': 'off'}
    response = client.post('/api/workshop/sessions', json=custom)
    assert response.status_code == 201, response.text
    current = response.json()
    body.update(session_id=current['id'], revision=current['revision'], input={
        'constraints': '', **({'target_total_words': None} if clear_total else {})})
    body.pop('draft_id')
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    response = client.post('/api/workshop/source-route-handoffs', json=body)
    assert response.status_code == 202, response.text
    result = finish(client, current['id'])
    for key, value in custom.items():
        if clear_total and key == 'target_total_words':
            assert key not in result['input']
            continue
        assert result['input'][key] == ('' if key == 'constraints' else value)
    assert result['source_context_history'][-1]['input'] == current['input']


def test_explicit_male_channel_does_not_inherit_female_audience(client):
    response = client.post('/api/workshop/sessions', json={
        'creative_types': refs(client, 'genre.romance', 'tag.action'), 'audience_channel': 'male'})
    assert response.status_code == 201, response.text
    assert response.json()['input']['audience'] == ''
    assert response.json()['input']['language'] == 'English'


@pytest.mark.parametrize('route', [False, True])
def test_explicit_unselected_channel_is_not_a_default(client, route):
    fields = {**selected(), 'creation_flow': 'genre_tags.v2', 'audience_channel': None}
    if route:
        _, _, _, _, body = setup_source(client)
        body['input'] = fields
        response = client.post('/api/workshop/source-route-handoffs', json=body)
    else:
        response = client.post('/api/workshop/sessions', json=fields)
    assert response.status_code == 422


@pytest.mark.parametrize('replay_with_new_operation', [False, True])
def test_handoff_identity_distinguishes_omitted_and_explicit_empty_preferences(client, monkeypatch, replay_with_new_operation):
    _, _, _, _, body = setup_source(client)
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    accepted = client.post('/api/workshop/source-route-handoffs', json=body)
    assert accepted.status_code == 202, accepted.text
    original = finish(client, accepted.json()['session_id'])
    body['input']['language'] = ''
    if replay_with_new_operation:
        body['operation_id'] = 'same-draft-another-operation'
    replay = client.post('/api/workshop/source-route-handoffs', json=body)
    assert replay.status_code == 409
    assert client.get(f'/api/workshop/sessions/{original["id"]}').json()['input'] == original['input']


@pytest.mark.parametrize('new_operation', [False, True])
def test_prepared_handoff_recovers_the_accepted_input_not_new_defaults(client, monkeypatch, new_operation):
    projects, _, _, _, body = setup_source(client)
    original_create = workshop.WorkshopService.create
    monkeypatch.setattr(workshop.WorkshopService, 'create', lambda *_, **__: (_ for _ in ()).throw(OSError('Before create')))
    response = client.post('/api/workshop/source-route-handoffs', json=body)
    assert response.status_code == 409
    path = workshop.WorkshopService(projects).root / 'source-handoffs' / (body['operation_id'] + '.json')
    record = json.loads(path.read_text())
    # Represents an accepted pre-upgrade request, whose omitted form fields
    # were frozen as blank before current defaults existed.
    record['input'].update(language='', audience='', market='')
    record.pop('request_hash_version', None)
    record['request_sha256'] = digest(RouteHandoffInput.model_validate(body).model_dump())
    path.write_text(json.dumps(record))
    draft_path = path.parent / ('draft-' + body['draft_id'] + '.json')
    draft = json.loads(draft_path.read_text())
    draft.pop('request_hash_version', None)
    draft['payload_sha256'] = digest({k: v for k, v in RouteHandoffInput.model_validate(body).model_dump().items()
                                      if k != 'operation_id'})
    draft_path.write_text(json.dumps(draft))
    monkeypatch.setattr(workshop.WorkshopService, 'create', original_create)
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    if new_operation:
        body['operation_id'] = 'resumed-old-draft-operation'
    response = client.post('/api/workshop/source-route-handoffs', json=body)
    assert response.status_code == 202, response.text
    result = finish(client, response.json()['session_id'])
    assert all(result['input'][key] == '' for key in ('language', 'audience', 'market'))


def test_reading_legacy_discussion_does_not_install_creation_defaults(client, tmp_path):
    response = client.post('/api/workshop/sessions', json={**selected(), 'audience_channel': 'female',
        'language': '', 'audience': '', 'market': ''})
    data = response.json()
    path = tmp_path / 'workshop_v2' / data['id'] / 'session.json'
    before = path.read_bytes()
    loaded = client.get(f'/api/workshop/sessions/{data["id"]}')
    assert loaded.status_code == 200
    assert all(loaded.json()['input'][key] == '' for key in ('language', 'audience', 'market'))
    assert path.read_bytes() == before
