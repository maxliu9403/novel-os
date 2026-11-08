"""Lean requests still drive real author choices and evidence-bound navigation."""
from copy import deepcopy
import json

import pytest

from api import workshop
from api.workshop_design import new_design, record_round
from api.workshop_delivery import author_context, checkpoint_fingerprint
from api.workshop_request import discussion_context
from core.creative_types import CreativeTypeStore
from test_creative_types import selected
from test_workshop_api import client, create, finish, model_result  # noqa: F401
from test_workshop_contract_repair import stub_model, submit
from test_workshop_story import story_notes, action, post


def compact_notes(ready=False):
    notes = story_notes()
    notes['guidance'].pop('coverage')
    notes.pop('assumptions', None)
    for proposal in notes['proposals']:
        proposal.pop('mechanisms', None)
        proposal.pop('genre_roles', None)
    if ready:
        notes.update(proposals=[], conflicts=[])
        notes['guidance'].update(groups=[], next_action='generate', missing=[])
        notes['story'].pop('assumptions')
        for node in notes['story']['nodes']:
            node.update(status='settled', evidence=[{'message_index': 0, 'quote': '采用完整设计'}])
    return notes


def ready_session():
    brief = dict(premise='医师救助流亡者', chapters=12, words_per_chapter=600,
                 language='English', mode='short_novel', audience='成年读者', market='美国')
    return dict(id='test', revision=2, input=brief, turn_intent='discuss', prompt_md='',
                messages=[{'role': 'user', 'content': '采用完整设计'}, {'role': 'assistant', 'content': '已协调路线'}],
                creative_design=new_design(brief))


def test_runtime_preserves_full_author_knowledge_reference_and_model_settings(monkeypatch, tmp_path):
    session = ready_session()
    session['input']['length_planning'] = 'story_first'
    snapshot = CreativeTypeStore(tmp_path).snapshot(selected(CreativeTypeStore(tmp_path)))
    session['creative_design'] = new_design(session['input'], snapshot)
    session['source_context'] = {'mode': 'reference_creation', 'analysis': {'story_summary': '全部参考分析' * 1000}}
    session['prompt_md'] = '已有骨架' * 1000
    before = deepcopy(session)
    context = discussion_context(author_context(session))
    fingerprint = checkpoint_fingerprint(session, workshop._skill_bundle()[1])
    calls = stub_model(monkeypatch, lambda *_: json.dumps(model_result()[0]))
    options = []
    from core.llm_client import LLMClient
    monkeypatch.setattr(LLMClient, '__init__', lambda self, **kw: options.append(kw))
    workshop._complete(session, workshop._skill_bundle()[0])
    assert len(calls) == 1
    system, user = calls[0]
    assert len(system.encode()) < 12000  # Includes capacity and all modern UI contracts.
    assert json.loads(user) == context
    assert json.loads(user)['source_reference'] == before['source_context']
    assert json.loads(user)['creative_design']['genre_context']['documents']
    for old_stack in ('GUIDED CHOICES AND CONVERGENCE', 'SCENE_EXECUTION_BRIEF', 'FINAL EXPORT REFERENCES',
                      'STORY WORKSPACE (story-workshop.v1', 'CREATIVE DESIGN RECORD'):
        assert old_stack not in system
    assert system.count('capacity_plan =') == 1
    assert options[0]['reasoning_effort'] == 'medium' and options[0]['max_tokens'] == 16384
    assert 899 < options[0]['timeout_seconds'] <= 900  # Existing deadline subtracts local setup time.
    assert session['creative_design'] == before['creative_design']
    assert checkpoint_fingerprint(session, workshop._skill_bundle()[1]) == fingerprint


def test_compact_options_remain_clickable_and_cannot_be_skipped(client, monkeypatch):  # noqa: F811
    response = model_result()[0]
    response['design_notes'] = compact_notes()
    calls = stub_model(monkeypatch, lambda *_: json.dumps(response))
    data = create(client, premise='医师救助流亡者')
    submit(client, data, '请设计全书', intent='discuss')
    data = finish(client, data['id'])
    latest = data['creative_design']['rounds'][-1]
    assert latest['guidance']['status'] == 'assessed'
    assert latest['guidance']['coverage'] == []
    assert data['creative_design']['story_state']['pending']['option_ids'] == ['r1:p1', 'r1:p2']
    assert post(client, data, {'revision': data['revision'], 'message': '继续', 'intent': 'discuss'}).status_code == 422
    response.pop('design_notes')  # Missing companion on next reply must preserve the choice.
    assert post(client, data, action(data)).status_code == 202
    saved = finish(client, data['id'])
    assert saved['creative_design']['decisions'][0]['proposal_id'] == 'r1:p1'
    assert saved['creative_design']['story_state']['pending'] is None
    assert len(calls) == 2 and not saved['prompt_md']


def test_one_validated_map_can_recommend_convergence_without_duplicate_coverage():
    session = ready_session()
    record_round(session, {'design_notes': compact_notes(True), 'questions': []}, source_revision=1)
    record = session['creative_design']['rounds'][-1]
    assert record['guidance']['next_action'] == 'generate'
    assert record['guidance']['coverage'] == []
    assert not session['prompt_md'] and session['creative_design']['decisions'] == []


@pytest.mark.parametrize('failure', ['missing_map', 'duplicate_area', 'no_evidence', 'invented_quote',
                                      'assistant_quote', 'pending_gap', 'new_options'])
def test_stale_map_or_model_claim_cannot_recommend_convergence(failure):
    session = ready_session()
    record_round(session, {'design_notes': compact_notes(True), 'questions': []}, source_revision=1)
    previous = deepcopy(session['creative_design']['story_state']['map'])
    notes = compact_notes(True)
    if failure == 'missing_map':
        notes.pop('story')
    elif failure == 'duplicate_area':
        notes['story']['nodes'][0]['area'] = 'ending'
    elif failure == 'no_evidence':
        notes['story']['nodes'][0]['evidence'] = []
    elif failure == 'invented_quote':
        notes['story']['nodes'][0]['evidence'][0]['quote'] = '从未确认的话'
    elif failure == 'assistant_quote':
        notes['story']['nodes'][0]['evidence'][0] = {'message_index': 1, 'quote': '已协调路线'}
    elif failure == 'pending_gap':
        notes['story']['assumptions'] = [{'text': '未解释的关键动机', 'kind': 'causal_gap'}]
    else:
        notes['proposals'] = compact_notes()['proposals']
        notes['guidance']['groups'] = compact_notes()['guidance']['groups']
    session['revision'] = 4
    record_round(session, {'design_notes': notes, 'questions': []}, source_revision=3)
    record = session['creative_design']['rounds'][-1]
    assert record['guidance']['next_action'] != 'generate'
    if failure in {'missing_map', 'duplicate_area'}:
        assert session['creative_design']['story_state']['map'] == previous
    assert not session['prompt_md'] and session['creative_design']['decisions'] == []
