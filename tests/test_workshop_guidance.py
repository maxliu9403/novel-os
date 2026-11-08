"""Guided decisions, advisory readiness and atomic author actions through the real API."""
from copy import deepcopy
import json

import pytest

from api import workshop
from api.workshop_guidance import record_guidance, enabled
from api.workshop_design import new_design
from api.workshop_delivery import author_context
from api.jobs import JobRunner
from core.h5_acceptance import _public_json
from test_workshop_api import client, create, finish, model_result  # noqa: F401
from test_workshop_contract_repair import stub_model, submit
from test_workshop_design import notes


def guided_notes(ready=False):
    value = notes()
    value['conflicts'] = []
    value['proposals'].append({**deepcopy(value['proposals'][0]), 'title': '独立重建', 'recommended': False,
                              'story': 'Rin接受失去旧居的代价，与愿意同行的人建立新家，允许成员自由离开。',
                              'tradeoff': '以更不稳定的生活换取自主，保留与旧族群有限往来的可能。'})
    if ready:
        value['proposals'] = []
        value.pop('next_focus')
    value['guidance'] = {
        'groups': [] if ready else [{'topic': '如何争取庇护', 'why': '改变联盟和结局的代价',
                                    'option_indexes': [1, 2], 'recommended_index': 1}],
        'coverage': [{'area': area, 'status': 'settled' if ready else 'proposed',
                      'summary': f'{area} 已形成因果路线',
                      'evidence': [{'message_index': 0, 'quote': '采用完整设计'}] if ready else []}
                     for area in ('premise', 'characters', 'plot', 'ending')],
        'next_action': 'generate' if ready else 'choose', 'reason': '当前最有价值的下一步', 'missing': []}
    return value


def discussed(client, monkeypatch, ready=False):
    original_new_design = workshop.new_design
    monkeypatch.setattr(workshop, 'new_design', lambda *a, **kw: original_new_design(*a, **{**kw, 'story': False}))
    def reply(*_):
        result = model_result()[0]
        result['design_notes'] = guided_notes(ready)
        return json.dumps(result, ensure_ascii=False)
    calls = stub_model(monkeypatch, reply)
    data = create(client, genres=['Werewolf'], premise='医师救助流亡者', chapters=12,
                  words_per_chapter=600, language='English', mode='short_novel', audience='成年读者', market='美国')
    submit(client, data, '采用完整设计', intent='discuss')
    return finish(client, data['id']), calls


def payload(data, ids=None, operation='guided-test-001', **kwargs):
    return {'revision': data['revision'], 'message': '选择后仍保留姐妹关心彼此的关系', 'intent': 'discuss',
            'proposal_ids': ids if ids is not None else ['r1:p1'], 'operation_id': operation, **kwargs}


def test_new_advice_is_optional_and_does_not_authorize_delivery(client, monkeypatch):
    data, calls = discussed(client, monkeypatch, ready=True)
    guidance = data['creative_design']['rounds'][-1]['guidance']
    assert guidance['next_action'] == 'generate'
    assert guidance['reviewed_revision'] == data['revision']
    assert not data['ready_for_confirmation'] and data['prompt_md'] == '' and data['project_id'] is None
    assert data['status'] == 'draft' and len(calls) == 1
    assert client.post(f"/api/workshop/sessions/{data['id']}/prepare", json={'revision': data['revision']}).status_code == 422


@pytest.mark.parametrize('mutation', ['assistant_evidence', 'nonexistent_quote', 'missing_evidence', 'duplicate_area', 'unknown_option', 'overlapping_group'])
def test_bad_or_unsupported_advice_cannot_discard_story_or_claim_ready(client, monkeypatch, mutation):
    raw = guided_notes(ready=True)
    if mutation == 'assistant_evidence':
        raw['guidance']['coverage'][0]['evidence'][0]['message_index'] = 1
    elif mutation == 'nonexistent_quote':
        raw['guidance']['coverage'][0]['evidence'][0]['quote'] = '作者没有说过的话'
    elif mutation == 'missing_evidence':
        raw['guidance']['coverage'][0]['evidence'] = []
    elif mutation == 'duplicate_area':
        raw['guidance']['coverage'][0]['area'] = 'ending'
    else:
        raw = guided_notes()
        if mutation == 'unknown_option':
            raw['guidance']['groups'][0]['option_indexes'] = [1, 9]
        else:
            raw['guidance']['groups'] *= 2
    result = model_result()[0]
    result['design_notes'] = raw
    calls = stub_model(monkeypatch, lambda *_: json.dumps(result))
    data = create(client, genres=['Werewolf'])
    submit(client, data, '采用完整设计', intent='discuss')
    data = finish(client, data['id'])
    record = data['creative_design']['rounds'][-1]
    assert record['status'] == 'recorded'
    assert record['guidance'].get('next_action') != 'generate'
    assert '本轮完成' in data['messages'][-1]['content'] and len(calls) == 1


def test_choice_and_addition_are_atomic_and_retries_are_durable(client, monkeypatch):
    data, calls = discussed(client, monkeypatch)
    path = f"/api/workshop/sessions/{data['id']}/turns"
    body = payload(data)
    first = client.post(path, json=body)
    assert first.status_code == 202, first.text
    completed = finish(client, data['id'])
    repeated = client.post(path, json=body)
    assert repeated.json()['job_id'] == first.json()['job_id']
    assert len(calls) == 2
    assert completed['creative_design']['decisions'][0]['proposal_id'] == 'r1:p1'
    sent = json.loads(calls[-1][1])['conversation'][-1]['content']
    assert 'r1:p1' in sent and body['message'] in sent
    assert client.post(path, json={**body, 'message': '不同请求'}).status_code == 409
    monkeypatch.setattr(workshop, 'runner', JobRunner())
    assert client.post(path, json=body).json()['status'] == 'error'
    assert len(calls) == 2


@pytest.mark.parametrize('ids', [['r1:p1', 'r1:p2'], ['r1:p1', 'r1:p1'], ['r999:p1']])
def test_invalid_choice_set_does_not_partially_save(client, monkeypatch, ids):
    data, calls = discussed(client, monkeypatch)
    result = client.post(f"/api/workshop/sessions/{data['id']}/turns", json=payload(data, ids))
    assert result.status_code == 422
    current = client.get(f"/api/workshop/sessions/{data['id']}").json()
    assert current['revision'] == data['revision'] and current['creative_design']['decisions'] == []
    assert len(calls) == 1


def test_save_only_keeps_additions_and_replaces_exclusive_choice_without_model(client, monkeypatch):
    data, calls = discussed(client, monkeypatch)
    path = f"/api/workshop/sessions/{data['id']}/choices"
    body = payload(data)
    first = client.post(path, json=body).json()
    assert client.post(path, json=body).json()['revision'] == first['revision']
    assert body['message'] in first['messages'][-1]['content']
    second = client.post(path, json=payload(first, ['r1:p2'], operation='guided-test-002')).json()
    current = {d['proposal_id']: d for d in second['creative_design']['decisions']}
    assert current['r1:p1']['status'] == 'deferred' and current['r1:p2']['status'] == 'accepted'
    assert len(calls) == 1
    assert second['creative_design']['rounds'][-1]['guidance']['reviewed_revision'] != second['revision']
    adopted = author_context(second)['creative_design']['adopted_proposals']
    assert [p['proposal']['id'] for p in adopted] == ['r1:p2']


def test_upgrade_is_explicit_preserves_history_and_does_not_infer(client, monkeypatch):
    data, calls = discussed(client, monkeypatch)
    service = workshop.WorkshopService(client.app.dependency_overrides[workshop.get_service]())
    with service._locked(data['id']) as stored:
        legacy = new_design(stored['input'], guided=False)
        legacy.update(rounds=stored['creative_design']['rounds'], decisions=stored['creative_design']['decisions'])
        stored['creative_design'] = legacy
        service._save(stored)
    before = client.get(f"/api/workshop/sessions/{data['id']}").json()
    assert not enabled(before['creative_design'])
    body = {'revision': before['revision'], 'operation_id': 'upgrade-test-001'}
    path = f"/api/workshop/sessions/{data['id']}/guidance"
    upgraded = client.post(path, json=body).json()
    assert enabled(upgraded['creative_design'])
    assert upgraded['messages'] == before['messages']
    assert upgraded['creative_design']['rounds'] == before['creative_design']['rounds']
    assert client.post(path, json=body).json()['revision'] == upgraded['revision']
    assert len(calls) == 1


def test_continuing_a_saved_revision_does_not_restore_original_proposal(client, monkeypatch):
    data, calls = discussed(client, monkeypatch)
    path = f"/api/workshop/sessions/{data['id']}"
    revised = client.post(path + '/decisions', json={'revision': data['revision'], 'proposal_id': 'r1:p1',
        'status': 'revised', 'note': '盟友改为姐姐，不改变她的真实关心。'}).json()
    response = client.post(path + '/turns', json=payload(revised))
    assert response.status_code == 202
    final = finish(client, data['id'])
    decisions = final['creative_design']['decisions']
    assert len(decisions) == 1 and decisions[0]['status'] == 'revised'
    assert '盟友改为姐姐' in json.loads(calls[-1][1])['conversation'][-1]['content']


@pytest.mark.parametrize('value', [{'creative_design': {}}, {'schema': 'guided-choices.v1'}])
def test_guidance_cannot_leak_into_publication_metadata(value):
    with pytest.raises(ValueError):
        _public_json(value)
