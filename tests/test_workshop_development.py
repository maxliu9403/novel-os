"""Real worker promotion, saved generation, and legacy migration behavior."""
from copy import deepcopy
import json

from api import workshop, workshop_development, workshop_capacity
from api.workshop_delivery import author_context
from test_workshop_api import client, create, finish, model_result, edit_session_file  # noqa: F401
from test_story_capacity import capacity_plan, QUOTE


class PassingJudge:
    def complete(self, system, user):
        return json.dumps({'status': 'pass', 'issues': [], 'evidence_requests': []})


def submit(client, data, message='请继续深化人物选择。'):
    response = client.post(f'/api/workshop/sessions/{data["id"]}/turns',
        json={'revision': data['revision'], 'message': message})
    assert response.status_code == 202, response.text
    return finish(client, data['id'])


def test_unauthorized_change_is_saved_repaired_and_only_then_promoted(client, tmp_path, monkeypatch):
    data = create(client, premise='The heroine remains independent; never reunite the couple.')
    result, route = model_result()
    result['reply'] = 'She abandons her company and reunites with him.'
    generated, calls = [], []
    monkeypatch.setattr(workshop, '_complete', lambda *_: (generated.append(True) or deepcopy(result), route))
    class Judge:
        def complete(self, system, user):
            payload = json.loads(user)
            calls.append(payload)
            if 'Repair a scoped creative candidate' in system:
                return json.dumps({'edits': [{'path': ['reply'], 'value': 'She keeps her company and chooses an independent life.'}]})
            narrative = json.dumps(payload.get('candidate_units', []))
            if 'abandons' in narrative:
                return json.dumps({'status': 'revise', 'issues': [{
                    'commitment_id': payload['core_commitments'][0]['id'], 'candidate_path': ['reply'],
                    'quote': 'She abandons her company', 'reason': 'The new ending removes her required independence.',
                    'repair': 'Preserve her company and independent ending.'}], 'evidence_requests': []})
            return PassingJudge().complete(system, user)
    monkeypatch.setattr(workshop_development, '_client', lambda *_: Judge())
    final = submit(client, data)
    assert final['status'] == 'draft', final.get('last_error')
    assert final['messages'][-1]['content'].endswith('She keeps her company and chooses an independent life.')
    assert final['development']['last_assessment']['status'] == 'passed'
    assert len(generated) == 1 and len(calls) == 3
    saved = list((tmp_path / 'workshop_v2' / data['id'] / 'development-runs').glob('*/generation.json'))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text())['result']['reply'] == result['reply']


def test_resume_reuses_generation_and_typed_capacity_evidence(client, tmp_path, monkeypatch):
    data = create(client, premise=QUOTE, length_planning='story_first', language='English',
                  words_per_chapter=800, mode='short_novel')
    generated = []
    def generate(snapshot, _):
        generated.append(True)
        result, route = model_result()
        result['capacity_plan'] = capacity_plan(2, 1400)
        result['_capacity_source_receipt'] = workshop_capacity.make_source_receipt(snapshot, author_context(snapshot))
        return result, route
    monkeypatch.setattr(workshop, '_complete', generate)
    def interrupted(*_):
        raise TimeoutError('review connection setup timed out')
    monkeypatch.setattr(workshop_development, '_client', interrupted)
    failed = submit(client, data)
    assert failed['status'] == 'error'
    assert not any(m['role'] == 'assistant' for m in failed['messages'])
    old_baseline = deepcopy(failed['development']['active']['baseline'])
    monkeypatch.setattr(workshop_development, '_client', lambda *_: PassingJudge())
    response = client.post(f'/api/workshop/sessions/{data["id"]}/resume', json={'revision': failed['revision']})
    assert response.status_code == 202, response.text
    final = finish(client, data['id'])
    assert final['status'] == 'draft', final.get('last_error')
    assert final['capacity']['status'] == 'proposed', final.get('capacity')
    assert len(generated) == 1
    assert final['development']['active']['baseline'] == old_baseline


def test_legacy_get_does_not_migrate_but_explicit_turn_does(client, tmp_path, monkeypatch):
    data = create(client, premise='An original independent heroine.')
    root = tmp_path / 'workshop_v2' / data['id']
    path = root / 'session.json'
    old = json.loads(path.read_text()); old.pop('development')
    path.write_text(json.dumps(old))
    before = path.read_bytes()
    loaded = client.get(f'/api/workshop/sessions/{data["id"]}').json()
    assert 'development' not in loaded and path.read_bytes() == before
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    final = submit(client, loaded)
    assert final['development']['schema'] == 'creative-development.v1'
    assert final['input'] == old['input']
    assert final['creative_design']['policy_sha256'] == old['creative_design']['policy_sha256']


def test_failed_candidate_does_not_overwrite_map_or_enter_author_history(client, tmp_path, monkeypatch):
    data = create(client, premise='I have two mutually exclusive endings; I have not decided which to keep.')
    result, route = model_result(); result['reply'] = 'A different ending.'
    monkeypatch.setattr(workshop, '_complete', lambda *_: (deepcopy(result), route))
    class UnclearJudge:
        def complete(self, system, user):
            payload = json.loads(user)
            return json.dumps({'status': 'author_choice', 'issues': [{
                'commitment_id': payload['core_commitments'][0]['id'], 'candidate_path': ['reply'],
                'quote': 'A different ending.', 'reason': 'Which of the two incompatible author endings should remain?',
                'author_evidence': [{'author_requirement_id': payload['author_requirements']['entries'][0]['id'],
                                     'quote': 'I have two mutually exclusive endings; I have not decided which to keep.'}],
                'repair': 'Use the author-selected ending.'}], 'evidence_requests': []})
    monkeypatch.setattr(workshop_development, '_client', lambda *_: UnclearJudge())
    final = submit(client, data)
    assert final['status'] == 'draft'
    assert final['development']['status'] == 'needs_author'
    assert final['creative_design']['story_state'] == data['creative_design']['story_state']
    assert not any(m['role'] == 'assistant' for m in final['messages'])
    assert not final['next_step']['can_deliver']


def test_next_explicit_turn_reuses_retained_candidate_without_regenerating(client, monkeypatch):
    data = create(client, premise='She keeps her freedom.')
    result, route = model_result(); result['reply'] = 'She gives up her freedom.'
    generated = []
    monkeypatch.setattr(workshop, '_complete', lambda *_: (generated.append(True) or deepcopy(result), route))
    class StalledJudge:
        def complete(self, system, user):
            payload = json.loads(user)
            if 'Repair a scoped creative candidate' in system:
                return json.dumps({'edits': [{'path': ['reply'], 'value': result['reply']}]})
            return json.dumps({'status': 'revise', 'issues': [{
                'commitment_id': payload['core_commitments'][0]['id'], 'candidate_path': ['reply'],
                'quote': result['reply'], 'reason': 'The required freedom disappears.',
                'repair': 'Restore her independent choice.'}], 'evidence_requests': []})
    monkeypatch.setattr(workshop_development, '_client', lambda *_: StalledJudge())
    blocked = submit(client, data)
    assert blocked['status'] == 'draft' and blocked['development']['status'] == 'needs_revision'
    assert not any(m['role'] == 'assistant' for m in blocked['messages'])
    class RepairingJudge(StalledJudge):
        def complete(self, system, user):
            if 'Repair a scoped creative candidate' in system:
                return json.dumps({'edits': [{'path': ['reply'], 'value': 'She preserves her freedom by declining the offer.'}]})
            if 'preserves her freedom' in user:
                return PassingJudge().complete(system, user)
            return super().complete(system, user)
    monkeypatch.setattr(workshop_development, '_client', lambda *_: RepairingJudge())
    done = submit(client, blocked, '请保留独立选择，修订这个冲突。')
    assert done['status'] == 'draft', done.get('last_error')
    assert done['development']['last_assessment']['status'] == 'passed'
    assert done['messages'][-1]['content'].endswith('She preserves her freedom by declining the offer.')
    assert len(generated) == 1
    assert 'pending_candidate' not in done['development']


def test_verified_actual_delivery_survives_title_packaging_without_new_story_call(client, tmp_path, monkeypatch):
    from test_workshop_api import complete_prompt
    from core.creative_execution import text_hash
    data = create(client, premise='A story of earning independence.')
    prompt = complete_prompt(tmp_path)
    generated = []
    monkeypatch.setattr(workshop, '_complete', lambda *_: (generated.append(True) or model_result(prompt)[0], model_result()[1]))
    response = client.post(f'/api/workshop/sessions/{data["id"]}/turns',
        json={'revision': data['revision'], 'intent': 'deliver', 'message': '请生成完整骨架。'})
    assert response.status_code == 202, response.text
    done = finish(client, data['id'])
    assert done['status'] == 'ready', done.get('last_error')
    verified = deepcopy(done['development']['delivery_verification'])
    assert verified['review']['scope'] == 'delivery'
    assert verified['source_sha256'] == text_hash(done['prompt_md'])
    prepared = client.post(f'/api/workshop/sessions/{data["id"]}/prepare',
        json={'revision': done['revision'], 'title': 'A Choice of Her Own'})
    assert prepared.status_code == 200, prepared.text
    final = prepared.json()
    binding = final['development']['delivery_verification']
    assert binding['source_sha256'] == text_hash(final['prompt_md'])
    assert binding['reviewed_source_sha256'] == verified['source_sha256']
    assert binding['review'] == verified['review']
    assert binding['transformations'][-1]['kind'] == 'title'
    assert len(generated) == 1


def test_retained_capacity_repair_can_cite_new_author_words_without_rebinding_old_receipt(client, monkeypatch):
    data = create(client, premise=QUOTE, length_planning='story_first', language='English', words_per_chapter=800, mode='short_novel')
    generated = []
    def generate(snapshot, _):
        generated.append(True)
        result, route = model_result()
        result['capacity_plan'] = capacity_plan(2, 1400)
        result['_capacity_source_receipt'] = workshop_capacity.make_source_receipt(snapshot, author_context(snapshot))
        return result, route
    monkeypatch.setattr(workshop, '_complete', generate)
    quote = 'The witness risks her position to support the heroine.'
    path = ['capacity_plan', 'scenes', 0, 'source_quote']
    old_quote = capacity_plan(2, 1400)['scenes'][0]['source_quote']
    class Judge:
        updated = False
        def complete(self, system, user):
            payload = json.loads(user)
            if 'Repair a scoped creative candidate' in system:
                return json.dumps({'edits': [{'path': path, 'value': quote if self.updated else old_quote}]})
            if self.updated and quote in json.dumps(payload.get('candidate_units', [])):
                return PassingJudge().complete(system, user)
            return json.dumps({'status': 'revise', 'issues': [{
                'commitment_id': payload['core_commitments'][0]['id'], 'candidate_path': path,
                'quote': old_quote, 'reason': 'The witness scene needs the explicit new author premise.',
                'repair': 'Use the author-defined witness scene.'}], 'evidence_requests': []})
    judge = Judge()
    monkeypatch.setattr(workshop_development, '_client', lambda *_: judge)
    failed = submit(client, data)
    assert failed['development']['status'] == 'needs_revision'
    judge.updated = True
    done = submit(client, failed, quote)
    assert done['status'] == 'draft', done.get('last_error')
    assert done['capacity']['status'] == 'proposed', done.get('capacity')
    assert done['capacity']['plan']['scenes'][0]['source_quote'] == quote
    assert 'reviewed_author_evidence_sha256' in done['capacity']['validation']
    assert len(generated) == 1


def test_independently_verified_explanation_keeps_capacity_and_can_adopt_it(client, tmp_path, monkeypatch):
    from test_workshop_capacity import propose
    from test_workshop_api import complete_prompt
    data = propose(client, monkeypatch)
    old = deepcopy(data['capacity'])
    old_rounds = deepcopy(data['creative_design']['rounds'])
    result, route = model_result()
    result['reply'] = 'The chapter space preserves the cost of her decision.'
    # Even if the generator sends an unwanted companion, reply-only work must
    # not replace the current plan or silently adopt it.
    result['capacity_plan'] = {'invalid_unwanted_companion': True}
    monkeypatch.setattr(workshop, '_complete', lambda *_: (deepcopy(result), route))
    class ExplanationJudge(PassingJudge):
        def complete(self, system, user):
            return json.dumps({'status': 'pass', 'issues': [], 'evidence_requests': [],
                'state_effect': 'no_change', 'state_effect_reason': 'The author asks why; this reply explains existing space without adopting new events.'})
    monkeypatch.setattr(workshop_development, '_client', lambda *_: ExplanationJudge())
    done = submit(client, data, '为什么这个篇幅适合目前的故事？只解释。')
    assert done['status'] == 'draft', done.get('last_error')
    assert done['capacity']['status'] == 'proposed'
    assert done['capacity']['plan'] == old['plan']
    assert done['capacity']['proposal_sha256'] == old['proposal_sha256']
    assert done['capacity']['source_sha256'] == old['source_sha256']
    assert done['creative_design']['rounds'] == old_rounds
    assert done['capacity']['basis_history'][-1]['reason'] == 'verified_reply_only'
    assert done['messages'][-1]['scope'] == 'explanation_or_unadopted_comparison'
    done = submit(client, done, '再解释一下各章的阅读回报，不改变设计。')
    assert done['capacity']['plan'] == old['plan']
    assert len(done['capacity']['basis_history']) == 2
    monkeypatch.setattr(workshop_development, '_client', lambda *_: PassingJudge())
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result(complete_prompt(tmp_path)))
    response = client.post(f'/api/workshop/sessions/{data["id"]}/turns', json={
        'revision': done['revision'], 'intent': 'deliver', 'message': '', 'operation_id': 'after-explanation',
        'capacity_choice': {'proposal_sha256': old['proposal_sha256'], 'option_id': old['plan']['options'][0]['id']}})
    assert response.status_code == 202, response.text
    final = finish(client, data['id'])
    assert final['status'] == 'ready', final.get('last_error')


def test_explicit_question_cannot_publish_model_story_fields_or_call_judge(client, tmp_path, monkeypatch):
    from test_workshop_story import begin, action
    from test_workshop_api import complete_prompt
    data, _, _ = begin(client, monkeypatch)
    original = deepcopy(data['creative_design']['story_state'])
    result, route = model_result(complete_prompt(tmp_path))
    result['reply'] = 'This only explains the differences between the two options.'
    result['design_notes'] = {'story': {'synopsis': 'Silently rewritten ending.'}}
    monkeypatch.setattr(workshop, '_complete', lambda *_: (deepcopy(result), route))
    monkeypatch.setattr(workshop_development, '_client', lambda *_: (_ for _ in ()).throw(AssertionError('Host explanation cannot buy review')))
    response = client.post(f'/api/workshop/sessions/{data["id"]}/turns', json=action(data, 'question'))
    assert response.status_code == 202, response.text
    done = finish(client, data['id'])
    assert done['status'] == 'draft', done.get('last_error')
    assert done['prompt_md'] == data['prompt_md']
    assert done['creative_design']['story_state']['map'] == original['map']
    assert not done['ready_for_confirmation']
    assert done['messages'][-1]['scope'] == 'explanation_or_unadopted_comparison'
