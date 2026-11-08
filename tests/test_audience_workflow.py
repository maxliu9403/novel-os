"""Channel and author choice survive the real handoff without model calls."""
from copy import deepcopy
import hashlib
import json

import pytest

from api.workshop_design import execution_handoff, new_design, record_round
from api.workshop_request import discussion_system
from core.author_requirements import workshop_requirements
from core.creative_execution import make_handoff, validate_handoff
from core.creative_types import CreativeTypeStore, TypeRef, compile_snapshot, summary
from core.knowledge_intake import validate_source_selection
from core.publication_taxonomy import bind_classification
from core.novel_classification import parse_classification_block
from core.reader_investment import snapshot as reader_snapshot
from test_workshop_api import client  # noqa: F401


def refs(client, *ids):
    records = client.get('/api/creative-types').json()['items']
    return [{k: r[k] for k in ('id', 'source_sha256')} | {'version': r['active_version']}
            for r in records if r['id'] in ids]


def test_create_requires_author_channel_and_validates_channel_selection(client):
    selected = refs(client, 'genre.romance', 'tag.action')
    for body in ({'creative_types': selected}, {'creative_types': selected, 'audience_channel': 'general'},
                 {'creative_types': refs(client, 'genre.romance', 'tag.girl_power'), 'audience_channel': 'male'},
                 {'creative_types': refs(client, 'genre.romance', 'tag.action', 'tag.mystery', 'tag.intelligent'), 'audience_channel': 'male'}):
        assert client.post('/api/workshop/sessions', json=body).status_code in (409, 422)
    result = client.post('/api/workshop/sessions', json={'creative_types': selected, 'audience_channel': 'male'})
    assert result.status_code == 201, result.text
    data = result.json()
    assert data['input']['creation_flow'] == 'genre_tags.v2'
    snapshot = data['creative_design']['policy']['type_snapshot']
    assert snapshot['audience_channel'] == 'male'
    compiled = compile_snapshot(snapshot, 'brainstorm')
    assert all(d['audience_channel'] == 'male' for d in compiled['documents'])
    assert 'WHOLE-STORY' in discussion_system(data, '')
    assert 'tag_contributions' in discussion_system(data, '')


def test_channel_edit_is_explicit_restart_and_keeps_old_snapshot(client):
    data = client.post('/api/workshop/sessions', json={
        'creative_types': refs(client, 'genre.romance', 'tag.action'), 'audience_channel': 'female',
        'premise': 'Two rivals repair the bridge together.'}).json()
    original = deepcopy(data['creative_design']['policy']['type_snapshot'])
    body = {k: v for k, v in data['input'].items() if k != 'publication_taxonomy'}
    body['audience_channel'] = 'male'
    response = client.post(f"/api/workshop/sessions/{data['id']}/input", json={'revision': data['revision'], 'input': body})
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited['restart_required'] and edited['status'] == 'draft'
    assert edited['input']['premise'] == data['input']['premise']
    assert edited['creative_design']['policy']['type_snapshot'] == original
    assert edited['pending_type_snapshot']['audience_channel'] == 'male'
    body['audience_channel'] = None
    assert client.post(f"/api/workshop/sessions/{data['id']}/input", json={'revision': edited['revision'], 'input': body}).status_code == 422


def test_legacy_discussion_reads_unchanged_but_reference_upgrade_requires_channel(client, tmp_path):
    """Both settings and direct snapshot endpoints enforce explicit upgrade."""
    selected = refs(client, 'genre.romance', 'tag.action')
    data = client.post('/api/workshop/sessions', json={
        'creative_types': selected, 'audience_channel': 'female', 'premise': 'Keep the earlier story.'}).json()
    # A persisted pre-channel discussion, not a new legacy creation endpoint.
    data['input'].update(creation_flow='genre_tags.v1', audience_channel=None)
    store = CreativeTypeStore(tmp_path / 'creative_types')
    frozen = store.snapshot([TypeRef(**ref) for ref in selected])
    data['creative_design'] = new_design(data['input'], frozen)
    path = tmp_path / 'workshop_v2' / data['id'] / 'session.json'
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    before = path.read_bytes()
    loaded = client.get(f"/api/workshop/sessions/{data['id']}")
    assert loaded.status_code == 200
    assert loaded.json()['creative_design']['policy']['type_snapshot'] == frozen
    replacement = refs(client, 'genre.romance', 'tag.mystery')
    url = f"/api/workshop/sessions/{data['id']}"
    response = client.post(url + '/type-snapshot', json={
        'revision': data['revision'], 'refs': replacement, 'operation_id': 'explicit-legacy-update'})
    assert response.status_code == 422
    body = {k: v for k, v in data['input'].items() if k != 'publication_taxonomy'}
    body['creative_types'] = replacement
    assert client.post(url + '/input', json={'revision': data['revision'], 'input': body}).status_code == 422
    assert path.read_bytes() == before
    body['audience_channel'] = 'female'
    upgraded = client.post(url + '/input', json={'revision': data['revision'], 'input': body})
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()['pending_type_snapshot']['schema'] == 'selection-snapshot.v2'
    assert upgraded.json()['creative_design']['policy']['type_snapshot'] == frozen


@pytest.mark.parametrize('channel', ['male', 'female'])
def test_channel_is_code_bound_and_wrong_skeleton_channel_rejected(tmp_path, channel):
    store = CreativeTypeStore(tmp_path / 'types')
    selected = [TypeRef(id=d['id'], version=d['active_version'], source_sha256=d['source_sha256'])
                for d in map(summary, store.records().values()) if d['id'] in {'genre.romance', 'tag.action'}]
    snapshot = store.snapshot(selected, audience_channel=channel)
    initial = {'creation_flow': 'genre_tags.v2', 'audience_channel': channel,
               'publication_taxonomy': snapshot['publication_taxonomy']}
    source = bind_classification('# Test\nGenre: Unknown\nChapters: 2\n\nA promise.', initial)
    assert parse_classification_block(source).audience_channel == channel
    handoff = {'type_snapshot': snapshot}
    validate_source_selection(source, handoff)
    wrong = bind_classification(source, {**initial, 'audience_channel': 'female' if channel == 'male' else 'male'})
    with pytest.raises(ValueError, match='频道'):
        validate_source_selection(wrong, handoff)
    with pytest.raises(ValueError, match='频道'):
        bind_classification(source, {**initial, 'audience_channel': None})


def test_handoff_carries_exact_later_author_amendments_and_rejects_tampering(tmp_path):
    source = '# Adopted story\nHe leaves the company and they keep their promise.'
    messages = [{'role': 'assistant', 'content': 'He stays at the company.'},
                {'role': 'user', 'content': 'Change that: he leaves the company, but keeps the promise.'}]
    requirements = workshop_requirements(messages, source_sha256=hashlib.sha256(source.encode()).hexdigest())
    value = make_handoff(source=source, origin={'workshop_id': 'test'}, reader_investment=reader_snapshot(),
                         story_engine='story-first.v1', author_requirements=requirements)
    assert value['schema'] == 'creative-handoff.v4'
    assert value['author_requirements']['entries'][0]['text'] == messages[1]['content']
    broken = deepcopy(value)
    broken['author_requirements']['entries'][0]['text'] = 'Stay at the company.'
    with pytest.raises(ValueError):
        validate_handoff(broken)


def test_combination_card_binds_tag_names_and_unknown_ids_are_not_adopted(client):
    data = client.post('/api/workshop/sessions', json={
        'creative_types': refs(client, 'genre.romance', 'tag.action'), 'audience_channel': 'female'}).json()
    data.update(turn_intent='discuss', messages=[{'role': 'assistant', 'content': 'A proposal'}])
    result = {'reply': 'Choose a route.', 'questions': [], 'design_notes': {
        'proposals': [{'title': 'Shared risk', 'story': 'Both take responsibility.', 'tradeoff': 'Safety for trust.',
                       'tag_contributions': [{'tag_id': 'tag.action', 'tag_name': 'Invented', 'contribution': 'Rescue requires mutual trust.'}],
                       'choice': 'Return for the rival', 'cost': 'Lose the safe route', 'payoff': 'Earn mutual trust'}]}}
    record_round(data, result, source_revision=1)
    p = data['creative_design']['rounds'][-1]['proposals'][0]
    assert p['tag_contributions'][0]['tag_name'] == '动作'
    assert p['combination_status'] == 'complete'
    assert data['creative_design']['decisions'] == []
    partial = deepcopy(result)
    partial['design_notes']['proposals'][0].pop('tag_contributions')
    record_round(data, partial, source_revision=2)
    saved = data['creative_design']['rounds'][-1]
    assert saved['status'] == 'recorded'
    assert saved['proposals'][0]['combination_status'] == 'partial'
    assert saved['proposals'][0]['missing_contribution_names'] == ['动作']
    result['design_notes']['proposals'][0]['tag_contributions'][0]['tag_id'] = 'tag.mystery'
    record_round(data, result, source_revision=3)
    assert data['creative_design']['rounds'][-1]['status'] == 'unavailable'
    assert data['creative_design']['decisions'] == []
