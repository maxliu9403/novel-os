from copy import deepcopy
import json
import pytest

from api import workshop
from api.workshop_capacity import source
from core.story_capacity import digest, read_contract
from core.skeleton_review import CATEGORIES
from test_story_capacity import capacity_plan, QUOTE
from test_workshop_api import client, create, finish, model_result, complete_prompt


def review():
    return {'summary': 'The opening choice needs a consequence on the page.', 'checked': list(CATEGORIES),
            'issues': [{'chapter': 1, 'category': 'reader_value',
                        'quote': '母亲通过新证据改变下一步选择，保留回应空间。',
                        'problem': '目前只说明选择结果，读者还没经历它的代价。',
                        'replacement': '母亲出示新证据后，对方拒绝回应。她仍收回已答应的支持，并面对关系立刻改变的后果。'}], 'notes': []}


def test_capacity_accepts_only_bound_supplied_source_packet_and_selected_route(tmp_path):
    from api import workshop_capacity
    from core.source_route_context import read_packet
    from test_source_route_handoff import evidence_fixture
    context, _ = evidence_fixture(tmp_path, QUOTE)
    initial = {'premise': '来源故事续写', 'language': 'English', 'mode': 'short_novel'}
    snapshot = {'id': 'source-capacity', 'revision': 1, 'input': initial, 'messages': [],
                'creative_design': {'policy': {'capacity_version': 'story-capacity.v1'}}, 'source_context': context}
    session = deepcopy(snapshot)
    result = {'capacity_plan': capacity_plan()}
    original_basis = digest(source(snapshot))
    packet = read_packet(tmp_path, context)
    receipt = workshop_capacity.make_source_receipt(snapshot, {'source_reference': context}, evidence_packet=packet)
    workshop_capacity.record(session, result, snapshot, source_receipt=receipt)
    assert session['capacity']['status'] == 'proposed'
    assert session['capacity']['source_sha256'] == original_basis
    assert session['capacity']['validation']['source_evidence_sha256'] == packet['sha256']
    assert snapshot.get('capacity') is None

    forged = deepcopy(packet)
    forged['content']['unread_extra'] = QUOTE
    rejected = deepcopy(snapshot)
    receipt = workshop_capacity.make_source_receipt(snapshot, {'source_reference': context}, evidence_packet=forged)
    workshop_capacity.record(rejected, result, snapshot, source_receipt=receipt)
    assert rejected['capacity']['status'] == 'unavailable'
    assert rejected['capacity']['validation']['issues'][0]['code'] == 'source_evidence_invalid'
    assert rejected['capacity']['candidate'] == result['capacity_plan']

    # A stored packet is not proof that this request supplied it.
    not_supplied = deepcopy(snapshot)
    receipt = workshop_capacity.make_source_receipt(snapshot, {'source_reference': context})
    workshop_capacity.record(not_supplied, result, snapshot, source_receipt=receipt)
    assert not_supplied['capacity']['status'] == 'unavailable'
    assert not_supplied['capacity']['validation']['issues'][0]['code'] == 'source_quote_not_found'


def test_capacity_selected_direction_is_quoteable_only_when_actually_supplied(tmp_path):
    from api import workshop_capacity
    from test_source_route_handoff import evidence_fixture
    context, _ = evidence_fixture(tmp_path, 'A different source event.')
    context['selected_proposal'] = {'story': QUOTE, 'sha256': digest({'story': QUOTE})}
    context['context_sha256'] = digest({k: v for k, v in context.items() if k != 'context_sha256'})
    snapshot = {'id': 'selected-direction', 'revision': 2, 'input': {}, 'messages': [],
                'creative_design': {'policy': {'capacity_version': 'story-capacity.v1'}, 'decisions': []},
                'source_context': context}
    receipt = workshop_capacity.make_source_receipt(snapshot, {'source_reference': context})
    session = deepcopy(snapshot)
    workshop_capacity.record(session, {'capacity_plan': capacity_plan()}, snapshot, source_receipt=receipt)
    assert session['capacity']['status'] == 'proposed'
    assert session['capacity']['confirmed'] is None
    assert session['creative_design']['decisions'] == []  # quote scope is not author approval

    omitted = workshop_capacity.make_source_receipt(snapshot, {})
    session = deepcopy(snapshot)
    workshop_capacity.record(session, {'capacity_plan': capacity_plan()}, snapshot, source_receipt=omitted)
    assert session['capacity']['validation']['issues'][0]['code'] == 'source_evidence_invalid'


@pytest.mark.parametrize('mutation', ['another_packet', 'other_revision', 'model_json', 'changed_reference'])
def test_capacity_source_receipt_rejects_unbound_or_forged_scope(tmp_path, mutation):
    from dataclasses import asdict
    from api import workshop_capacity
    from core.source_route_context import make_packet
    from test_source_route_handoff import evidence_fixture
    context, packet = evidence_fixture(tmp_path, QUOTE)
    snapshot = {'id': 'bound-request', 'revision': 3, 'input': {}, 'messages': [],
                'creative_design': {'policy': {'capacity_version': 'story-capacity.v1'}}, 'source_context': context}
    supplied = {'source_reference': deepcopy(context)}
    if mutation == 'another_packet':
        packet = make_packet({'text': QUOTE}, source_id='source-' + 'f' * 64,
                             source_sha256='e' * 64, understanding_sha256='c' * 64)
    elif mutation == 'changed_reference':
        supplied['source_reference']['selected_proposal']['story'] = QUOTE
    receipt = workshop_capacity.make_source_receipt(snapshot, supplied, evidence_packet=packet)
    if mutation == 'other_revision':
        snapshot['revision'] += 1
    elif mutation == 'model_json':
        receipt = asdict(receipt)
    session = deepcopy(snapshot)
    workshop_capacity.record(session, {'capacity_plan': capacity_plan()}, snapshot, source_receipt=receipt)
    assert session['capacity']['status'] == 'unavailable'
    assert session['capacity']['validation']['issues'][0]['code'] == 'source_evidence_invalid'


def test_capacity_preserves_unusable_candidate_and_explains_missing_exact_source():
    from api import workshop_capacity
    snapshot = {'id': 'no-source', 'revision': 1, 'input': {'premise': 'This is another story.'},
                'messages': [], 'creative_design': {'policy': {'capacity_version': 'story-capacity.v1'}}}
    session = deepcopy(snapshot)
    raw = capacity_plan()
    workshop_capacity.record(session, {'capacity_plan': raw}, snapshot)
    assert session['capacity']['status'] == 'unavailable'
    assert session['capacity']['candidate'] == raw
    issues = session['capacity']['validation']['issues']
    assert issues and issues[0]['code'] == 'source_quote_not_found'
    assert issues[0]['scene_id'] == raw['scenes'][0]['id']
    correction = workshop_capacity.context(session)['story_capacity']
    assert correction['candidate_to_repair'] == raw
    assert correction['validation']['issues'] == issues
    # Starting the next discussion invalidates the old execution proposal. It
    # must still supply the failed candidate and diagnostics for repair.
    workshop_capacity.invalidate(session)
    correction = workshop_capacity.context(session)['story_capacity']
    assert correction['candidate_to_repair'] == raw
    assert correction['validation']['issues'] == issues


def test_capacity_cannot_cite_unsent_candidate_history_or_joined_fields():
    from api import workshop_capacity
    raw = capacity_plan()
    split = len(QUOTE) // 2
    raw['scenes'][0]['source_quote'] = QUOTE[:split] + '\n' + QUOTE[split:]
    snapshot = {'id': 'no-expanded-scope', 'revision': 1,
                'input': {'premise': QUOTE[:split], 'constraints': QUOTE[split:]}, 'messages': [],
                'capacity_history': [{'candidate': {'full_scene': QUOTE}}],
                'creative_design': {'policy': {'capacity_version': 'story-capacity.v1'}}}
    session = deepcopy(snapshot)
    workshop_capacity.record(session, {'capacity_plan': raw}, snapshot)
    issues = session['capacity']['validation']['issues']
    assert len(issues) == len(raw['scenes'])
    assert all(issue['code'] == 'source_quote_not_found' for issue in issues)


def test_capacity_retains_structurally_invalid_companion_without_raw_error_inputs():
    from api import workshop_capacity
    snapshot = {'id': 'invalid-candidate', 'revision': 1, 'input': {}, 'messages': [],
                'creative_design': {'policy': {'capacity_version': 'story-capacity.v1'}}}
    raw = {'language': 'Never discard this literary material.', 'scenes': [{'id': 'one'}]}
    session = deepcopy(snapshot)
    workshop_capacity.record(session, {'capacity_plan': raw}, snapshot)
    assert session['capacity']['candidate'] == raw
    assert session['capacity']['validation']['candidate_sha256'] == digest(raw)
    assert all(issue['code'] == 'invalid_plan' for issue in session['capacity']['validation']['issues'])
    assert 'Never discard' not in json.dumps(session['capacity']['validation'])


def propose(client, monkeypatch, *, legacy_root=None):
    response, route = model_result()
    response['capacity_plan'] = capacity_plan()
    monkeypatch.setattr(workshop, '_complete', lambda *_: (deepcopy(response), route))
    data = create(client, premise=QUOTE, length_planning='story_first', chapters=12, words_per_chapter=600,
                  mode='short_novel', language='English')
    if legacy_root is not None:
        from test_story_first_engine import freeze_legacy_policy
        freeze_legacy_policy(legacy_root, data['id'])
    r = client.post(f'/api/workshop/sessions/{data["id"]}/turns', json={'message': '规划完整故事与篇幅', 'revision': 0})
    assert r.status_code == 202
    data = finish(client, data['id'])
    assert data['capacity']['status'] == 'proposed'
    assert data['input']['chapters'] == 12  # advice is not author acceptance
    return data


def deliver(client, monkeypatch, tmp_path):
    data = propose(client, monkeypatch)
    result, route = model_result(complete_prompt(tmp_path))
    result['skeleton_review'] = review()
    calls = []
    def complete(session, _):
        calls.append(session)
        return deepcopy(result), route
    monkeypatch.setattr(workshop, '_complete', complete)
    request = {'revision': data['revision'], 'message': '', 'intent': 'deliver', 'operation_id': 'capacity-adopt-1',
               'capacity_choice': {'proposal_sha256': data['capacity']['proposal_sha256'], 'option_id': 'keep_story'}}
    url = f'/api/workshop/sessions/{data["id"]}/turns'
    first = client.post(url, json=request)
    assert first.status_code == 202, first.text
    second = client.post(url, json=request)
    assert second.status_code == 202
    assert second.json()['job_id'] == first.json()['job_id']
    done = finish(client, data['id'])
    assert done['status'] == 'ready', done.get('last_error')
    assert len(calls) == 1
    return done


def test_adoption_freezes_allocation_and_is_idempotent(client, monkeypatch, tmp_path):
    done = deliver(client, monkeypatch, tmp_path)
    assert done['input']['chapters'] == 21
    assert done['capacity']['confirmed']['option']['total_words'] == 11550
    assert read_contract(done['prompt_md']) == done['capacity']['confirmed']
    assert done['skeleton_review']['status'] == 'proposed'
    assert done['prepared'] is None and done['project_id'] is None
    assert client.get('/api/projects').json() == []


def test_edit_basics_archives_original_capacity_before_invalidating(client, monkeypatch, tmp_path):
    data = propose(client, monkeypatch)
    monkeypatch.setattr(workshop, '_complete', lambda *_: pytest.fail('Editing basics cannot call a model'))
    result = client.post(f'/api/workshop/sessions/{data["id"]}/input', json={
        'revision': data['revision'], 'input': {**data['input'], 'premise': 'A changed story premise.'}})
    assert result.status_code == 200, result.text
    assert result.json()['capacity']['status'] == 'stale'
    path = tmp_path / 'workshop_v2' / data['id'] / 'history' / f'before-edit-{data["revision"]}.json'
    archived = json.loads(path.read_text())
    assert archived['capacity'] == data['capacity']
    assert archived['input'] == data['input']


def test_missing_stale_and_changed_story_choices_do_not_launch(client, monkeypatch):
    data = propose(client, monkeypatch)
    monkeypatch.setattr(workshop, '_complete', lambda *_: pytest.fail('Rejected requests cannot call a model'))
    url = f'/api/workshop/sessions/{data["id"]}/turns'
    base = {'revision': data['revision'], 'message': '请根据已确认的故事设计生成完整小说骨架。保留既有设定和决定。', 'intent': 'deliver'}
    assert client.post(url, json=base).status_code == 422
    base.update(capacity_choice={'proposal_sha256': '0' * 64, 'option_id': 'keep_story'}, operation_id='adoption-stale')
    assert client.post(url, json=base).status_code == 422
    base['capacity_choice']['proposal_sha256'] = data['capacity']['proposal_sha256']
    base['message'] = '同时把结局换成另一种。'
    assert client.post(url, json=base).status_code == 422
    current = client.get(f'/api/workshop/sessions/{data["id"]}').json()
    assert current['revision'] == data['revision'] and current['capacity']['status'] == 'proposed'


def test_malformed_companion_preserves_discussion_and_invalidates_old_advice(client, monkeypatch):
    data = propose(client, monkeypatch)
    result, route = model_result()
    result.update(reply='保留这次有用的故事讨论。', capacity_plan={'broken': True})
    monkeypatch.setattr(workshop, '_complete', lambda *_: (result, route))
    assert client.post(f'/api/workshop/sessions/{data["id"]}/turns', json={'revision': data['revision'], 'message': '改变主人公目标'}).status_code == 202
    done = finish(client, data['id'])
    assert done['status'] == 'draft' and done['last_error'] is None
    assert done['messages'][-1]['content'] == result['reply']
    assert done['capacity']['status'] == 'unavailable'
    assert done['capacity_history'][0]['proposal_sha256'] == data['capacity']['proposal_sha256']


def test_revision_applies_exact_patch_preserves_source_and_checks_prepare_gate(client, monkeypatch, tmp_path):
    done = deliver(client, monkeypatch, tmp_path)
    root = f'/api/workshop/sessions/{done["id"]}'
    assert client.post(root + '/prepare', json={'revision': done['revision']}).status_code == 422
    monkeypatch.setattr(workshop, '_complete', lambda *_: pytest.fail('Editing cannot call a model'))
    body = {'revision': done['revision'], 'review_sha256': done['skeleton_review']['review_sha256'],
            'selected_ids': ['issue_1'], 'operation_id': 'revise-skeleton-1'}
    r = client.post(root + '/skeleton-revision', json=body)
    assert r.status_code == 200, r.text
    new = r.json()
    old_excerpt = review()['issues'][0]['quote']
    replacement = review()['issues'][0]['replacement']
    assert new['prompt_md'].split('## 17.')[0] == done['prompt_md'].replace(old_excerpt, replacement, 1).split('## 17.')[0]
    assert '### 当前修订结果' in new['prompt_md'] and '### 修订前复核记录（历史）' in new['prompt_md']
    assert new['revision'] == done['revision'] + 1
    assert new['skeleton_review']['reader_validation'] == 'not_run'
    assert new['skeleton_review']['verification'] == 'exact_edits_and_structure_validated'
    assert new['prepared'] is None
    repeated = client.post(root + '/skeleton-revision', json=body).json()
    assert repeated['job_id'] == new['job_id']
    assert client.post(root + '/skeleton-revision', json=body | {'selected_ids': []}).status_code == 409
    versions = tmp_path / 'workshop_v2' / done['id'] / 'versions'
    assert (versions / f'{done["revision"]}.md').read_text() == done['prompt_md']
    checked = finish(client, done['id'])
    assert checked['status'] == 'ready', checked.get('last_error')
    assert checked['development']['delivery_verification']['source_sha256'] != done['development']['delivery_verification']['source_sha256']
    assert checked['development']['delivery_history'][-1] == done['development']['delivery_verification']
    assert client.post(root + '/prepare', json={'revision': checked['revision']}).status_code == 200


def test_author_can_keep_original_without_extra_inference(client, monkeypatch, tmp_path):
    done = deliver(client, monkeypatch, tmp_path)
    root = f'/api/workshop/sessions/{done["id"]}'
    result = client.post(root + '/skeleton-revision', json={'revision': done['revision'],
        'review_sha256': done['skeleton_review']['review_sha256'], 'selected_ids': [], 'operation_id': 'keep-original-1'})
    assert result.status_code == 200
    assert result.json()['prompt_md'] == done['prompt_md']
    assert result.json()['skeleton_review']['decision'] == 'keep_original'


def test_omitted_opt_in_preserves_legacy_hash_and_policy(client):
    data = create(client)
    assert 'capacity_version' not in data['creative_design']['policy']
    body = workshop.TurnInput(revision=0, message='Continue')
    assert workshop._turn_request_hash(body) == digest({'revision': 0, 'message': 'Continue', 'intent': 'discuss', 'proposal_ids': [], 'operation_id': None})


def test_unsupported_multivolume_is_explicit(client):
    result = client.post('/api/workshop/sessions', json={'length_planning': 'story_first', 'mode': 'multi_volume'})
    assert result.status_code == 422


def test_failed_delivery_keeps_allocation_for_explicit_retry(client, monkeypatch, tmp_path):
    data = propose(client, monkeypatch)
    def fail(*_):
        raise RuntimeError('Temporary model failure')
    monkeypatch.setattr(workshop, '_complete', fail)
    url = f'/api/workshop/sessions/{data["id"]}/turns'
    body = {'revision': data['revision'], 'message': '', 'intent': 'deliver', 'operation_id': 'fail-after-adopt',
            'capacity_choice': {'proposal_sha256': data['capacity']['proposal_sha256'], 'option_id': 'keep_story'}}
    assert client.post(url, json=body).status_code == 202
    failed = finish(client, data['id'])
    assert failed['status'] == 'error' and failed['capacity']['status'] == 'confirmed'
    result, route = model_result(complete_prompt(tmp_path))
    result['skeleton_review'] = review()
    monkeypatch.setattr(workshop, '_complete', lambda *_: (deepcopy(result), route))
    assert client.post(url, json={'revision': failed['revision'], 'intent': 'deliver',
        'message': '请根据已确认的故事设计生成完整小说骨架。保留既有设定和决定。'}).status_code == 202
    done = finish(client, data['id'])
    assert done['status'] == 'ready', done.get('last_error')
    assert read_contract(done['prompt_md']) == failed['capacity']['confirmed']


def test_real_staged_delivery_binds_capacity_and_collects_review_without_extra_call(client, monkeypatch, tmp_path):
    from test_workshop_contract_repair import stub_model
    from test_workshop_assembly_runtime import start_reply, checkpoint
    from workshop_assembly_fixtures import parts_from_prompt
    from workshop_delivery_fixtures import chapter_plan
    actual_complete = workshop._complete
    data = propose(client, monkeypatch, legacy_root=tmp_path)
    monkeypatch.setattr(workshop, '_complete', actual_complete)
    parts = parts_from_prompt(complete_prompt(tmp_path))
    editorial = review()
    editorial['issues'][0]['quote'] = chapter_plan()['chapters'][0]['plan']
    parts['delivery_review']['review'] = editorial
    def respond(attempt, system, user):
        context = json.loads(user)
        assert context['story_capacity']['confirmed']['option']['total_words'] == 11550
        if context.get('assembly_part') == 'delivery_review':
            assert 'EVIDENCE-BOUND EDITORIAL REVIEW' in system
            assert context['saved_story_stages']['chapter_plan']['chapters'][0]['target_words'] == 550
        return start_reply(parts, user)
    calls = stub_model(monkeypatch, respond)
    body = {'revision': data['revision'], 'message': '', 'intent': 'deliver', 'operation_id': 'staged-adoption-1',
            'capacity_choice': {'proposal_sha256': data['capacity']['proposal_sha256'], 'option_id': 'keep_story'}}
    assert client.post(f'/api/workshop/sessions/{data["id"]}/turns', json=body).status_code == 202
    done = finish(client, data['id'])
    assert done['status'] == 'ready', done.get('last_error')
    assert len(calls) == 8  # 2 story stages + the existing 6 parts; no extra reviewer
    assert done['skeleton_review']['status'] == 'proposed'
    assert read_contract(done['prompt_md']) == done['capacity']['confirmed']
    assert checkpoint(tmp_path, done)['completed']['assembly']['content']['prompt_md'] == done['prompt_md']


def test_failed_structure_check_never_publishes_editorial_patch(client, monkeypatch, tmp_path):
    data = deliver(client, monkeypatch, tmp_path)
    def fail(_):
        raise workshop.ModelResponseError('Injected structural regression')
    monkeypatch.setattr(workshop, '_validate_candidate', fail)
    root = f'/api/workshop/sessions/{data["id"]}'
    response = client.post(root + '/skeleton-revision', json={'revision': data['revision'],
        'review_sha256': data['skeleton_review']['review_sha256'], 'selected_ids': ['issue_1'], 'operation_id': 'failed-structure-1'})
    assert response.status_code == 422
    current = client.get(root).json()
    assert current['revision'] == data['revision'] and current['prompt_md'] == data['prompt_md']
    assert current['skeleton_review']['status'] == 'proposed'
