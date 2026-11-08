"""Map recovery exercises the persisted discussion boundary, without a model."""
from copy import deepcopy
import json

from api.workshop_design import design_context
from test_workshop_story import begin, action, post
from test_workshop_api import client, finish  # noqa: F401


def test_bad_assumption_does_not_discard_current_map_and_good_assumptions(client, monkeypatch, tmp_path):
    data, calls, replies = begin(client, monkeypatch)
    updated = deepcopy(replies[-1])
    updated['design_notes']['story']['synopsis'] = '新方案已改变中段与结局，保留完整关系变化。'
    updated['design_notes']['story']['assumptions'] = [
        {'kind': 'major_premise', 'text': '不能悄悄采用的前提'},
        {'kind': 'detail', 'text': '有效的新细节'},
    ]
    replies.append(updated)
    assert post(client, data, action(data)).status_code == 202
    saved = finish(client, data['id'])
    state = saved['creative_design']['story_state']
    assert state['map']['synopsis'] == updated['design_notes']['story']['synopsis']
    assert state['map']['reviewed_revision'] == saved['revision']
    assert any(a['text'] == '有效的新细节' for a in state['assumptions'])
    assert not any(a['text'] == '不能悄悄采用的前提' for a in state['assumptions'])
    check = state['validation']
    assert check['map_status'] == 'updated' and check['status'] == 'partial'
    assert any(i['code'] == 'major_premise_requires_option' for i in check['issues'])
    assert len(calls) == 2
    path = tmp_path / 'workshop_v2' / saved['id'] / check['candidate_path']
    artifact = json.loads(path.read_text())
    assert artifact['raw_story'] == updated['design_notes']['story']
    assert artifact['source']['revision'] == check['source_revision']
    assert artifact['source']['session_id'] == saved['id']
    assert artifact['source']['job_id'] == saved['job_id']
    assert artifact['validation']['issues'] == check['issues']
    # Private diagnostics and rejected candidates never become extra model input.
    context = design_context(saved)['creative_design']
    assert 'validation' not in context['story_state']
    assert 'story_validation' not in context['rounds'][-1]
    assert 'raw_story' not in json.dumps(context)


def test_bad_evidence_downgrades_only_its_node_and_keeps_new_map(client, monkeypatch):
    data, calls, replies = begin(client, monkeypatch)
    updated = deepcopy(replies[-1]); story = updated['design_notes']['story']
    story['synopsis'] = '本轮新的完整故事走线。'
    story['nodes'][0].update(status='settled', evidence=[{'message_index': 0, 'quote': '并不存在的作者原话'}])
    replies.append(updated)
    assert post(client, data, action(data)).status_code == 202
    saved = finish(client, data['id']); state = saved['creative_design']['story_state']
    assert state['map']['synopsis'] == story['synopsis']
    assert state['map']['nodes'][0]['status'] == 'proposed'
    assert state['map']['nodes'][0]['evidence'] == []
    assert any(i['code'] == 'unverifiable_evidence' for i in state['validation']['issues'])
    assert len(calls) == 2


def test_bad_map_retains_old_version_but_saves_valid_current_assumption(client, monkeypatch):
    data, calls, replies = begin(client, monkeypatch)
    old = deepcopy(data['creative_design']['story_state']['map'])
    updated = deepcopy(replies[-1]); story = updated['design_notes']['story']
    story['nodes'] = story['nodes'][:-1]
    story['assumptions'] = [{'kind': 'causal_gap', 'text': '仍需解释的本轮因果缺口'}]
    replies.append(updated)
    assert post(client, data, action(data)).status_code == 202
    saved = finish(client, data['id']); state = saved['creative_design']['story_state']
    assert state['map'] == old
    assert any(a['text'] == '仍需解释的本轮因果缺口' for a in state['assumptions'])
    assert state['validation']['map_status'] == 'preserved'
    assert any(i['path'] == 'nodes' for i in state['validation']['issues'])
    assert '讨论已保存' in state['notice'] and '故事地图未更新' in state['notice']
    assert len(calls) == 2


def test_bad_opening_does_not_contaminate_new_map_with_old_opening(client, monkeypatch):
    data, _, replies = begin(client, monkeypatch)
    updated = deepcopy(replies[-1]); story = updated['design_notes']['story']
    story['opening'] = {'situation': '不完整的新开篇'}
    replies.append(updated)
    assert post(client, data, action(data)).status_code == 202
    saved = finish(client, data['id']); state = saved['creative_design']['story_state']
    assert state['map']['reviewed_revision'] == saved['revision']
    assert 'opening' not in state['map']
    assert state['validation']['opening_status'] == 'unavailable'
    assert any(i['component'] == 'opening' for i in state['validation']['issues'])


def test_invalid_resolution_cannot_verify_external_fact_or_block_valid_map(client, monkeypatch):
    data, _, replies = begin(client, monkeypatch)
    external = next(a for a in data['creative_design']['story_state']['assumptions'] if a['kind']=='external_fact')
    updated = deepcopy(replies[-1]); story = updated['design_notes']['story']
    story['synopsis'] = '更新后的完整故事概述。'
    story['resolutions'] = [{'id': external['id'], 'explanation': '作者采用即为真实'}]
    replies.append(updated)
    assert post(client, data, action(data)).status_code == 202
    saved = finish(client, data['id']); state = saved['creative_design']['story_state']
    assert state['map']['synopsis'] == story['synopsis']
    assert next(a for a in state['assumptions'] if a['id']==external['id'])['status']=='unverified'
    assert any(i['code']=='resolution_not_allowed' for i in state['validation']['issues'])


def test_oversized_candidate_is_explicitly_omitted_without_discarding_reply(client, monkeypatch, tmp_path):
    data, calls, replies = begin(client, monkeypatch)
    updated = deepcopy(replies[-1])
    updated['design_notes']['story']['synopsis'] = '大' * 50000
    replies.append(updated)
    assert post(client, data, action(data)).status_code == 202
    saved = finish(client, data['id'])
    check = saved['creative_design']['story_state']['validation']
    artifact = json.loads((tmp_path / 'workshop_v2' / saved['id'] / check['candidate_path']).read_text())
    assert artifact['omitted'] is True and artifact['raw_story'] is None
    assert saved['status'] == 'draft' and saved['messages'][-1]['role'] == 'assistant'
    assert len(calls) == 2


def test_candidate_write_failure_does_not_lose_reply_or_map(client, monkeypatch):
    from api import workshop
    data, calls, replies = begin(client, monkeypatch)
    original = workshop._atomic
    def fail_candidate(path, text):
        if 'story-candidates' in str(path):
            raise OSError('fake disk failure')
        return original(path, text)
    monkeypatch.setattr(workshop, '_atomic', fail_candidate)
    assert post(client, data, action(data)).status_code == 202
    saved = finish(client, data['id']); state = saved['creative_design']['story_state']
    assert saved['status'] == 'draft'
    assert state['map']['reviewed_revision'] == saved['revision']
    assert state['validation']['candidate_save_failed'] is True
    assert 'candidate_path' not in state['validation']
    assert len(calls) == 2


def test_malformed_public_companions_are_bounded_and_never_throw():
    from api.workshop_story_result import validate_candidate
    from test_workshop_story import story_notes
    session = {'revision':2, 'input':{}, 'messages':[{'role':'user','content':'测试'}, {'role':'assistant','content':'reply'}],
               'creative_design':{'story_state':{'map':None,'assumptions':[]}}}
    record = {'source_revision':1,'message_index':1,'proposals':[]}
    baseline = story_notes()['story']
    for field in ['synopsis','focus','nodes','changes','opening','assumptions','resolutions']:
        for value in [None, True, 7, '', {}, [None], ['broken']*7]:
            raw = deepcopy(baseline);raw[field]=value
            result=validate_candidate(raw,record,session)
            assert len(result['validation']['issues'])<=24
    raw = deepcopy(baseline);raw['opening']={'not_a_field_secret_value':'must_not_be_in_safe_errors'}
    result=validate_candidate(raw,record,session)
    errors=json.dumps(result['validation']['issues'])
    assert 'not_a_field_secret_value' not in errors and 'must_not_be_in_safe_errors' not in errors
    assert any(i['path']=='opening.anchor' for i in result['validation']['issues'])
