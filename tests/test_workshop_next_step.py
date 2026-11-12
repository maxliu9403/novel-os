"""A completed design round always offers an honest next operation."""
from copy import deepcopy
import json

import pytest

from api import workshop
from test_workshop_api import client, create, finish, model_result, edit_session_file  # noqa: F401


def completed_round(client, tmp_path):
    data = create(client, premise='A daughter chooses whether to trust her father.', length_planning='story_first')
    design = deepcopy(data['creative_design'])
    design['story_state']['pending'] = None
    edit_session_file(tmp_path, data['id'], status='draft', revision=2, creative_design=design,
                      messages=[{'role':'user','content':'保留父女关系'}, {'role':'assistant','content':'当前没有需要你决定的重大分歧。'}],
                      capacity={'status':'unavailable','confirmed':None,'notice':'本轮篇幅建议尚不能使用。'})
    return client.get(f'/api/workshop/sessions/{data["id"]}').json()


def test_saved_round_exposes_actionable_step_without_modifying_history(client, tmp_path):
    data = completed_round(client, tmp_path)
    path = tmp_path / 'workshop_v2' / data['id'] / 'session.json'
    before = path.read_bytes()
    restored = client.get(f'/api/workshop/sessions/{data["id"]}').json()
    assert restored['next_step']['kind'] == 'plan_capacity'
    assert restored['next_step']['can_continue'] and restored['next_step']['can_plan_capacity']
    assert not restored['next_step']['can_deliver']
    assert restored['next_step']['delivery_blocked_reason']
    assert path.read_bytes() == before


@pytest.mark.parametrize('advance', ['develop_story','plan_capacity'])
def test_explicit_progress_needs_no_new_idea_is_idempotent_and_does_not_adopt(client, tmp_path, monkeypatch, advance):
    data = completed_round(client, tmp_path)
    calls = []
    response, route = model_result()
    def complete(snapshot, _):
        calls.append({'messages':deepcopy(snapshot['messages'])})
        return deepcopy(response), route
    monkeypatch.setattr(workshop, '_complete', complete)
    request = {'revision':data['revision'],'advance':advance,'intent':'discuss','operation_id':'progress-operation','message':''}
    url=f'/api/workshop/sessions/{data["id"]}/turns'
    first=client.post(url,json=request)
    assert first.status_code == 202, first.text
    final=finish(client,data['id'])
    duplicate=client.post(url,json=request)
    assert duplicate.status_code == 202 and duplicate.json()['job_id']==first.json()['job_id']
    assert len(calls)==1 and calls[0]['messages'][:-1]==data['messages']
    assert calls[0]['messages'][-1]['action']==advance
    assert '不代表' in calls[0]['messages'][-1]['content']
    assert final['creative_design']['decisions']==data['creative_design']['decisions']
    assert not final['prompt_md'] and not final['ready_for_confirmation']


def test_progress_cannot_bypass_author_decision_or_deliver_gate(client,tmp_path,monkeypatch):
    data=completed_round(client,tmp_path)
    monkeypatch.setattr(workshop,'_complete',lambda *_:pytest.fail('Invalid actions must not buy a request'))
    url=f'/api/workshop/sessions/{data["id"]}/turns'
    base={'revision':data['revision'],'advance':'plan_capacity','operation_id':'progress-operation','message':''}
    for patch in ({'intent':'deliver'},{'proposal_ids':['other']},{'capacity_choice':{'proposal_sha256':'a'*64,'option_id':'x'}}):
        assert client.post(url,json={**base,**patch}).status_code==422
    design=deepcopy(data['creative_design']);design['story_state']['pending']={'id':'question','option_ids':['a','b']}
    edit_session_file(tmp_path,data['id'],creative_design=design)
    assert client.post(url,json=base).status_code==422


def test_new_optional_action_preserves_legacy_request_identity():
    from core.creative_types import digest
    body=workshop.TurnInput(revision=2,message='Keep the ending',operation_id='previous-operation')
    old={'revision':2,'message':'Keep the ending','intent':'discuss','proposal_ids':[],'operation_id':'previous-operation'}
    assert workshop._turn_request_hash(body)==digest(old)


@pytest.mark.parametrize('status', ['paused', 'error'])
def test_progress_keeps_resumable_delivery_instead_of_starting_another_turn(client, tmp_path, monkeypatch, status):
    data = completed_round(client, tmp_path)
    edit_session_file(tmp_path, data['id'], status=status, active_operation='deliver',
                      delivery_run_id='preserved-delivery', delivery_progress={'resumable': True})
    path = tmp_path / 'workshop_v2' / data['id'] / 'session.json'
    before = path.read_bytes()
    step = client.get(f'/api/workshop/sessions/{data["id"]}').json()['next_step']
    assert step['kind'] == 'recover'
    assert not any(step[key] for key in ('can_continue', 'can_deliver', 'can_plan_capacity'))
    monkeypatch.setattr(workshop, '_complete', lambda *_: pytest.fail('Resume must keep its existing run'))
    for action in ('develop_story', 'plan_capacity'):
        response = client.post(f'/api/workshop/sessions/{data["id"]}/turns', json={
            'revision': data['revision'], 'advance': action, 'operation_id': 'resume-boundary', 'message': ''})
        assert response.status_code == 422
    assert path.read_bytes() == before


def test_source_capacity_flows_through_real_worker_and_repartition(client, tmp_path, monkeypatch):
    from test_source_route_handoff import evidence_fixture
    from test_workshop_contract_repair import stub_model
    from test_story_capacity import capacity_plan, QUOTE
    from test_capacity_partition import partition
    from test_workshop_repartition import request
    from core.llm_client import LLMClient

    data = create(client, premise='Design from the complete source.', length_planning='story_first',
                  language='English', mode='short_novel', words_per_chapter=800)
    root = tmp_path / 'workshop_v2' / data['id']
    context, packet = evidence_fixture(root, QUOTE)
    edit_session_file(tmp_path, data['id'], source_context=context)
    result, _ = model_result()
    result['capacity_plan'] = capacity_plan(2, 1400)
    calls = stub_model(monkeypatch, lambda *_: json.dumps(result))
    monkeypatch.setattr(LLMClient, 'text_input_limit_bytes', lambda _: None)
    url = f'/api/workshop/sessions/{data["id"]}/turns'
    assert client.post(url, json={'revision': data['revision'], 'message': 'Plan the whole story.'}).status_code == 202
    planned = finish(client, data['id'])
    assert planned['status'] == 'draft', planned.get('last_error')
    assert planned['capacity']['status'] == 'proposed', planned['capacity']
    assert planned['capacity']['validation']['source_evidence_sha256'] == packet['sha256']
    assert len(calls) == 1 and QUOTE in calls[0][1]
    assert QUOTE not in json.dumps(planned['messages'])
    assert planned['next_step']['kind'] == 'choose_capacity'
    calls = stub_model(monkeypatch, lambda *_: json.dumps(partition()))
    assert client.post(url, json=request(planned)).status_code == 202
    adjusted = finish(client, data['id'])
    assert adjusted['capacity']['status'] == 'proposed', adjusted.get('capacity')
    assert adjusted['capacity']['validation']['source_evidence_sha256'] == packet['sha256']
    assert len(adjusted['capacity']['plan']['options'][0]['chapters']) == 4
    assert len(calls) == 1
    assert adjusted['creative_design'] == planned['creative_design']
