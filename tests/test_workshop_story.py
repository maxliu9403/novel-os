"""New protocol behavior through persisted API sessions, with fake model calls."""
from copy import deepcopy
import json
import pytest
from api import workshop, workshop_story
from api.jobs import JobRunner
from api.workshop_design import new_design, execution_handoff, record_round
from core.h5_acceptance import _public_json
from test_workshop_api import client, create, finish, model_result, edit_session_file  # noqa: F401
from test_workshop_contract_repair import stub_model, submit
from test_workshop_guidance import guided_notes


def story_notes():
    value = guided_notes()
    value['story'] = {
        'synopsis': 'Rin寻求庇护，联盟改变她能付出的代价；结局路线仍待作者选择。',
        'focus': 'ending',
        'nodes': [{'area': area, 'status': 'proposed', 'summary': f'{area}: 有选择、有代价，暂未确认', 'evidence': []} for area in workshop_story.AREAS],
        'changes': ['把局部问题重新联系到结局'],
        'opening': {'situation': '救助者自己被拒之门外', 'anchor': '希望有归处的人', 'reader_experience': '担忧与不平',
                    'genre_cause': '群体规则改变庇护条件', 'first_value': '她付出真实代价争取临时安全', 'next_expectation': '这份联盟是否值得维持'},
        'assumptions': [{'kind': 'major_premise', 'text': '联盟需要服务作为代价', 'option_index': 1},
                        {'kind': 'external_fact', 'text': '现实救助流程尚需核实'},
                        {'kind': 'detail', 'text': '暂定为夜间到达'},
                        {'kind': 'causal_gap', 'text': '尚需解释对方何以答应合作'}],
    }
    return value


def begin(client, monkeypatch):
    result = model_result()[0]
    result['design_notes'] = story_notes()
    replies = [result]
    calls = stub_model(monkeypatch, lambda *_: json.dumps(replies[-1], ensure_ascii=False))
    data = create(client, genres=['Werewolf'], premise='医师救助流亡者', chapters=12,
                  words_per_chapter=600, language='English', mode='short_novel', audience='成年读者', market='美国')
    submit(client, data, '请帮我设计全书', intent='discuss')
    return finish(client, data['id']), calls, replies


def action(data, mode='select', **patch):
    question = data['creative_design']['story_state']['pending']
    return {'revision': data['revision'], 'intent': 'discuss', 'message': '保留姐妹的关心',
            'proposal_ids': question['option_ids'][:1] if mode == 'select' else [],
            'operation_id': f"story-action-{data['revision']}-{mode}",
            'story_action': {'mode': mode, 'question_id': question['id']}, **patch}


@pytest.mark.parametrize('with_focus', [False, True])
def test_valid_options_remain_actionable_when_navigation_companions_are_missing(with_focus):
    data = {'creative_design': new_design({'genres': ['Werewolf']}), 'turn_intent': 'discuss',
            'revision': 2, 'messages': [{'role': 'user', 'content': '设计两个方向'},
                                      {'role': 'assistant', 'content': '选项一与选项二'}], 'input': {}}
    notes = story_notes()
    notes.pop('guidance')
    notes.pop('story')
    if not with_focus:
        notes.pop('next_focus', None)
    record_round(data, {'design_notes': notes, 'questions': []}, source_revision=1)
    state = data['creative_design']['story_state']
    assert state['pending'] is not None, 'Valid saved options must retain selection controls without a map'
    assert state['pending']['option_ids'] == ['r1:p1', 'r1:p2']
    assert state['map'] is None and data['creative_design']['decisions'] == []
    with pytest.raises(ValueError):
        workshop_story.validate_action(data, None, [], '继续', 'discuss')
    workshop_story.validate_action(data, workshop_story.Action(mode='select', question_id=state['pending']['id']),
                                   ['r1:p2'], '保留姐妹关系', 'discuss')


def test_saved_unanswered_options_are_projected_without_writes_and_select_with_notes(client, monkeypatch, tmp_path):
    data, calls, replies = begin(client, monkeypatch)
    design = deepcopy(data['creative_design'])
    design['story_state']['pending'] = None
    design['rounds'][-1]['guidance'] = {'status': 'unavailable'}
    edit_session_file(tmp_path, data['id'], creative_design=design)
    path = tmp_path / 'workshop_v2' / data['id'] / 'session.json'
    before = path.read_bytes()
    restored = client.get(f"/api/workshop/sessions/{data['id']}").json()
    assert restored['creative_design']['story_state']['pending']['option_ids'] == ['r1:p1', 'r1:p2']
    assert restored['revision'] == data['revision'] and path.read_bytes() == before
    assert post(client, restored, {'revision': restored['revision'], 'message': '继续', 'intent': 'discuss'}).status_code == 422
    replies.append(model_result()[0])
    assert post(client, restored, action(restored, message='不要改变姐妹关系')).status_code == 202
    completed = finish(client, data['id'])
    assert completed['creative_design']['story_state']['actions'][-1]['note'] == '不要改变姐妹关系'
    assert '不要改变姐妹关系' in json.loads(calls[-1][1])['conversation'][-1]['content']
    assert len(calls) == 2


@pytest.mark.parametrize('case', ['answered', 'running', 'restart', 'resolved', 'explicit_no_group', 'invalid_proposals', 'existing_question'])
def test_option_recovery_never_reopens_answered_or_unvalidated_alternatives(case):
    data = {'creative_design': new_design({'genres': ['Werewolf']}), 'turn_intent': 'discuss', 'status': 'draft',
            'revision': 2, 'messages': [{'role': 'user', 'content': '设计两个方向'},
                                      {'role': 'assistant', 'content': '两种方案'}], 'input': {}}
    notes = story_notes()
    notes.pop('guidance')
    record_round(data, {'design_notes': notes, 'questions': []}, source_revision=1)
    state = data['creative_design']['story_state']
    question = state['pending']
    state['pending'] = None
    if case == 'answered': data['messages'].append({'role': 'user', 'content': '采用第二个，保留姐妹关系'})
    if case == 'running': data['status'] = 'running'
    if case == 'restart': data['restart_required'] = True
    if case == 'resolved': state['actions'].append({'question': question, 'mode': 'delegate'})
    if case == 'explicit_no_group': data['creative_design']['rounds'][-1]['guidance'] = {'status': 'assessed', 'groups': []}
    if case == 'invalid_proposals': data['creative_design']['rounds'][-1].update(status='unavailable', proposals=[])
    if case == 'existing_question': state['pending'] = {**question, 'id': 'previous-question'}
    before = deepcopy(state['pending'])
    workshop_story.restore_unanswered_options(data)
    assert state['pending'] == before


def post(client, data, body):
    return client.post(f"/api/workshop/sessions/{data['id']}/turns", json=body)


@pytest.mark.parametrize('intent', ['discuss', 'deliver'])
def test_known_major_choice_cannot_be_skipped_even_with_free_text(client, monkeypatch, intent):
    data, calls, _ = begin(client, monkeypatch)
    before = deepcopy(data['creative_design'])
    response = post(client, data, {'revision': data['revision'], 'message': '继续', 'intent': intent})
    assert response.status_code == 422
    restored = client.get(f"/api/workshop/sessions/{data['id']}").json()
    assert restored['revision'] == data['revision'] and restored['creative_design'] == before
    assert len(calls) == 1


def test_atomic_choice_addition_premise_scope_and_durable_replay(client, monkeypatch):
    data, calls, replies = begin(client, monkeypatch)
    replies.append(model_result()[0])  # malformed companion must not discard choice/reply
    body = action(data)
    response = post(client, data, body)
    assert response.status_code == 202, response.text
    result = finish(client, data['id'])
    state = result['creative_design']['story_state']
    assert state['pending'] is None
    assert state['actions'][-1]['note'] == body['message']
    assert state['assumptions'][0]['status'] == 'adopted_premise'
    assert state['assumptions'][1]['status'] == 'unverified'
    assert state['map'] == data['creative_design']['story_state']['map']
    assert state['notice'] and result['messages'][-1]['role'] == 'assistant'
    sent = json.loads(calls[-1][1])['conversation'][-1]['content']
    assert '保留姐妹的关心' in sent and '采用' in sent
    assert post(client, data, body).json()['job_id'] == response.json()['job_id']
    monkeypatch.setattr(workshop, 'runner', JobRunner())
    assert post(client, data, body).status_code == 202
    assert len(calls) == 2
    assert post(client, data, {**body, 'message': '不同请求'}).status_code == 409


@pytest.mark.parametrize('mode', ['question', 'reframe'])
def test_questions_and_bad_reframes_preserve_issue_and_map(client, monkeypatch, mode):
    data, calls, replies = begin(client, monkeypatch)
    replies.append(model_result()[0])
    response = post(client, data, action(data, mode))
    assert response.status_code == 202
    result = finish(client, data['id'])
    state = result['creative_design']['story_state']
    assert state['pending'] == data['creative_design']['story_state']['pending']
    assert result['creative_design']['decisions'] == []
    assert state['assumptions'][0]['status'] == 'pending'
    assert post(client, result, action(result, mode, intent='deliver')).status_code == 422
    assert len(calls) == 2
    # A stale visible group from the valid prior round remains actionable.
    assert post(client, result, action(result)).status_code == 202
    finish(client, data['id'])
    assert len(calls) == 3


@pytest.mark.parametrize('mode', ['custom', 'delegate'])
def test_custom_and_scoped_delegation_do_not_adopt_every_proposal(client, monkeypatch, mode):
    data, calls, replies = begin(client, monkeypatch)
    replies.append(model_result()[0])
    assert post(client, data, action(data, mode, message='' if mode == 'delegate' else '两人离开旧联盟，建立新的合作')).status_code == 202
    result = finish(client, data['id'])
    state = result['creative_design']['story_state']
    assert state['pending'] is None and len(state['actions']) == 1
    assert not result['creative_design']['decisions']
    assert state['assumptions'][1]['status'] == 'unverified'
    assert result['design_needs_revision']
    assert len(calls) == 2


@pytest.mark.parametrize('mutation', ['multiple', 'old_id', 'no_custom_text', 'wrong_question', 'no_operation'])
def test_invalid_decisions_have_no_partial_mutation_or_paid_call(client, monkeypatch, mutation):
    data, calls, _ = begin(client, monkeypatch)
    body = action(data)
    if mutation == 'multiple': body['proposal_ids'] = ['r1:p1', 'r1:p2']
    if mutation == 'old_id': body['proposal_ids'] = ['r0:p1']
    if mutation == 'no_custom_text': body = action(data, 'custom', message='')
    if mutation == 'wrong_question': body['story_action']['question_id'] = 'r0:g1'
    if mutation == 'no_operation': body.pop('operation_id')
    assert post(client, data, body).status_code == 422
    restored = client.get(f"/api/workshop/sessions/{data['id']}").json()
    assert restored['creative_design'] == data['creative_design']
    assert restored['revision'] == data['revision'] and len(calls) == 1


def test_history_mutation_routes_cannot_bypass_decision_and_reopen_is_explicit(client, monkeypatch):
    data, calls, replies = begin(client, monkeypatch)
    base = f"/api/workshop/sessions/{data['id']}"
    assert client.post(base + '/choices', json=action(data)).status_code == 422
    assert client.post(base + '/decisions', json={'revision': data['revision'], 'proposal_id': 'r1:p1', 'status': 'accepted'}).status_code == 422
    replies.append(model_result()[0])
    assert post(client, data, action(data)).status_code == 202
    saved = finish(client, data['id'])
    assert post(client, saved, {'revision': saved['revision'], 'intent': 'discuss', 'operation_id': 'reopen-00001',
                              'story_action': {'mode': 'reopen', 'question_id': 'r1:g1'}}).status_code == 202
    reopened = finish(client, data['id'])
    assert reopened['creative_design']['story_state']['pending']['id'] != 'r1:g1'
    assert reopened['creative_design']['decisions'][0]['status'] == 'accepted'
    assert post(client, reopened, action(reopened, 'custom', message='改为相互独立，只保留定期合作')).status_code == 202
    changed = finish(client, data['id'])
    assert changed['creative_design']['decisions'][-1]['status'] == 'deferred'
    assert len(calls) == 4


def test_model_cannot_forge_author_approval_or_verify_external_facts():
    session = {'creative_design': new_design({'genres': ['Werewolf']}), 'turn_intent': 'discuss',
               'revision': 2, 'messages': [{'role': 'user', 'content': '设计全书'}, {'role': 'assistant', 'content': 'reply'}], 'input': {}}
    raw = story_notes()
    raw['story']['nodes'][0]['status'] = 'settled'
    record_round(session, {'design_notes': raw, 'questions': []}, source_revision=1)
    state = session['creative_design']['story_state']
    assert state['map']['nodes'][0]['status'] == 'proposed'
    old = deepcopy(state['map'])
    raw['story']['resolutions'] = [{'id': state['assumptions'][1]['id'], 'explanation': '作者选择了，所以已核实'}]
    record_round(session, {'design_notes': raw, 'questions': []}, source_revision=3)
    assert state['map']['synopsis'] == old['synopsis'] and state['map']['source_revision'] == 3
    assert state['assumptions'][1]['status'] == 'unverified'
    assert state['validation']['issues'][0]['code'] == 'resolution_not_allowed'
    raw['story']['resolutions'] = [{'id': state['assumptions'][3]['id'], 'explanation': '对方也需要临时避难，两人用相互服务交换帮助'}]
    record_round(session, {'design_notes': raw, 'questions': []}, source_revision=5)
    assert state['assumptions'][3]['status'] == 'model_resolved'
    assert len(session['creative_design']['rounds']) == 3


def test_every_genre_and_custom_type_use_frozen_method_and_private_handoff():
    for genres in [['Werewolf'], ['Mystery'], ['婚姻背叛'], ['Comedy'], ['火星农耕'], []]:
        design = new_design({'genres': genres})
        policy = design['policy']
        assert 'READER INVESTMENT' in policy['rules'] and 'READER INVESTMENT' in policy['delivery_rules']
        session = {'creative_design': design, 'id': 'anybook', 'revision': 0, 'prompt_md': '完整骨架', 'messages': []}
        handoff = execution_handoff(session)
        assert handoff['schema'] == 'creative-handoff.v2'
        assert handoff['reader_investment'] == policy['reader_investment']
        with pytest.raises(ValueError): _public_json({'reader_investment': policy['reader_investment']})
        with pytest.raises(ValueError): _public_json({'schema': workshop_story.VERSION})
    assert 'reader_investment' not in new_design({'genres': []}, story=False)['policy']


def test_reframe_retires_only_prior_option_premises(client, monkeypatch):
    data, calls, _ = begin(client, monkeypatch)
    assert post(client, data, action(data, 'reframe', message='')).status_code == 202
    result = finish(client, data['id'])
    state = result['creative_design']['story_state']
    assert state['pending']['id'] != 'r1:g1'
    assert state['assumptions'][0]['status'] == 'reframed'
    assert state['assumptions'][1]['status'] == 'unverified'
    assert len(calls) == 2


def test_upgrade_is_explicit_idempotent_and_preserves_known_choice(client, monkeypatch, tmp_path):
    data, calls, _ = begin(client, monkeypatch)
    path = tmp_path / 'workshop_v2' / data['id'] / 'session.json'
    old = deepcopy(data)
    old['creative_design'] = new_design(old['input'], story=False)
    old['creative_design']['rounds'] = deepcopy(data['creative_design']['rounds'])
    path.write_text(json.dumps(old))
    base = f"/api/workshop/sessions/{data['id']}"
    assert client.get(base).json()['creative_design'] == old['creative_design']
    body = {'revision': old['revision'], 'operation_id': 'upgrade-new-story', 'protocol': workshop_story.VERSION}
    upgraded = client.post(base + '/guidance', json=body).json()
    assert upgraded['revision'] == old['revision'] + 1
    assert upgraded['messages'] == old['messages']
    assert upgraded['creative_design']['story_state']['pending']['id'] == 'r1:g1'
    assert client.post(base + '/guidance', json=body).json()['revision'] == upgraded['revision']
    assert len(calls) == 1


def test_old_session_without_method_requires_explicit_upgrade(client, monkeypatch, tmp_path):
    data, calls, replies = begin(client, monkeypatch)
    path = tmp_path / 'workshop_v2' / data['id'] / 'session.json'
    data.pop('creative_design')
    path.write_text(json.dumps(data))
    replies.append(model_result()[0])
    assert post(client, data, {'revision': data['revision'], 'message': '继续讨论'}).status_code == 202
    saved = finish(client, data['id'])
    assert not workshop_story.enabled(saved['creative_design'])
    assert len(calls) == 2


def test_legacy_http_request_hashes_stay_identical_after_deploy():
    from core.creative_types import digest
    raw = {'revision': 2, 'message': '继续', 'intent': 'discuss', 'proposal_ids': [], 'operation_id': 'legacy-repeat-001'}
    assert workshop._turn_request_hash(workshop.TurnInput(**raw)) == digest(raw)
