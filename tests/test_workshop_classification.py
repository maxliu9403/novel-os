from copy import deepcopy
import json
from pathlib import Path

import pytest

from api.workshop import SessionInput
from api.workshop_classification import BookClassificationSelection, bind_classification
from api.workshop_delivery import author_context
from core.creative_types import CreativeTypeStore, TypeRef, compile_snapshot, digest, validate_profile
from core.novel_classification import catalog_payload, parse_classification_block
from test_workshop_api import client, ready
from test_workshop_handoff import make_prompt, handoff, REPO


def selection(**overrides):
    return {'primary_genre_id': 'fantasy', 'story_type_ids': ['werewolf'], **overrides}


@pytest.mark.parametrize('fields', [
    {}, {'primary_genre_id': 'werewolf'}, {'primary_genre_id': 'invented'},
    selection(secondary_genre_ids=['fantasy']), selection(story_type_ids=['a']),
    selection(story_type_ids=['sweet_romance', 'dark_romance']),
    selection(story_type_ids=['werewolf', 'revenge', 'pregnancy', 'domestic_betrayal']),
    selection(catalog_version='novel-types.2026-09'), selection(tone_ids=['werewolf']),
    selection(story_type_ids=['werewolf', 'werewolf']),
])
def test_invalid_selection_cannot_allocate_session(client, tmp_path, fields):
    response = client.post('/api/workshop/sessions', json={'creation_flow': 'h5_guided.v1', 'publication_classification': fields})
    assert response.status_code == 422
    assert not list((tmp_path / 'workshop_v2').glob('*/session.json'))


def test_guided_flow_requires_selection_and_keeps_subjects_separate_from_guidance(client):
    assert client.post('/api/workshop/sessions', json={'creation_flow': 'h5_guided.v1'}).status_code == 422
    data = client.post('/api/workshop/sessions', json={'creation_flow': 'h5_guided.v1', 'publication_classification': selection(), 'creative_types': []}).json()
    assert data['input']['genres'] == ['奇幻', '狼人']
    assert data['creative_design']['policy']['type_snapshot']['profiles'] == []
    context = author_context(data)
    assert context['initial_brief']['publication_classification']['story_type_ids'] == ['werewolf']
    assert 'AUTHOR-CONFIRMED' in context['publication_selection_policy']
    downgrade = {**data['input'], 'creation_flow': None, 'publication_classification': None}
    assert client.post(f"/api/workshop/sessions/{data['id']}/input", json={'revision': 0, 'input': downgrade}).status_code == 422
    # Old integrations and persisted old discussions retain their explicit legacy path.
    assert client.post('/api/workshop/sessions', json={'premise': 'legacy'}).status_code == 201


def test_model_classification_cannot_override_author_and_handoff_reads_it(tmp_path):
    raw = make_prompt(tmp_path).read_text()
    initial = SessionInput(creation_flow='h5_guided.v1', publication_classification=selection()).model_dump()
    bound = bind_classification(raw, initial)
    classification = parse_classification_block(bound)
    assert classification.primary_genre_id == 'fantasy'
    assert classification.story_type_ids == ('werewolf',)
    assert classification.source == 'author_confirmed'
    assert classification.chapter_band == 'chapters_0_50'
    assert bind_classification(bound, initial) == bound
    assert bind_classification(raw, {}) == raw
    path = tmp_path / 'bound.md'; path.write_text(bound)
    assert handoff.validate(path, REPO)['chapters'] == 21
    # Full source story remains verbatim outside the protocol block.
    assert 'A mother discovers a betrayal and chooses her own future.' in bound
    for invalid in (raw + raw, raw.replace('[NOVEL_CLASSIFICATION_JSON]', '[OTHER]')):
        with pytest.raises(ValueError, match='唯一'):
            bind_classification(invalid, initial)


def test_real_workshop_save_prepare_and_intake_keep_chosen_ids(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path, creation_flow='h5_guided.v1', publication_classification=selection())
    assert data['status'] == 'ready', data.get('last_error')
    assert parse_classification_block(data['prompt_md']).story_type_ids == ('werewolf',)
    response = client.post(f"/api/workshop/sessions/{data['id']}/prepare", json={'revision': data['revision'], 'title': 'Chosen Moon'})
    assert response.status_code == 200, response.text
    assert parse_classification_block(response.json()['prompt_md']).primary_genre_id == 'fantasy'


def test_curated_defaults_keep_old_hashes_and_user_overrides(tmp_path):
    store = CreativeTypeStore(tmp_path / 'types')
    records = store.records()
    active = {id for id, r in records.items() if not r.get('retired')}
    assert active == {'ct_werewolf', 'ct_marriage_betrayal', 'ct_female_awakening'}
    seed = json.loads((REPO / 'resources/creative-types/v1.json').read_text())
    for entry in seed['profiles']:
        p = validate_profile(entry['profile'], enabled=True)
        r = records[p['profile_id']]
        assert r['versions'][0]['profile'] == p
        assert r['versions'][0]['sha256'] == digest(p)
        ref = TypeRef(profile_id=p['profile_id'], version=p['version'], source_sha256=digest(p))
        snapshot = store.snapshot([ref], require_enabled=False)
        assert compile_snapshot(snapshot, 'brainstorm')['profiles'][0]['version'] == p['version']
    # Saved user records take precedence; merely upgrading defaults does not edit them.
    owned = deepcopy(records['ct_marriage_betrayal']); owned.pop('retired'); owned['draft']['summary'] = '作者自己的说明'
    store.root.mkdir(); store.path.write_text(json.dumps({'schema': 'creative-type-library.v1', 'records': {owned['id']: owned}, 'operations': {}}))
    before = store.path.read_bytes()
    assert store.get(owned['id'])['draft']['summary'] == '作者自己的说明'
    assert store.path.read_bytes() == before


def test_h5_catalog_is_unchanged_and_curated_mappings_use_it():
    expected = json.loads((REPO / 'docs/examples/h5-import/novel-classification-catalog.json').read_text())
    assert catalog_payload() == expected
