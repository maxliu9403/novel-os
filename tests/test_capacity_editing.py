"""Author length edits preserve story obligations and never imply adoption."""
from copy import deepcopy
import pytest

from api import workshop_capacity, workshop_repartition
from core.story_capacity import digest
from core.capacity_editing import reestimate, within_estimates
from test_story_capacity import capacity_plan, QUOTE


def session():
    value = {'id': 'capacity-edits', 'revision': 4, 'status': 'draft', 'input': {
        'premise': QUOTE, 'words_per_chapter': 600, 'language': 'English', 'mode': 'short_novel'},
        'messages': [], 'creative_design': {'policy': {'capacity_version': 'story-capacity.v1'}}, 'prompt_md': ''}
    workshop_capacity.record(value, {'capacity_plan': capacity_plan(2, 550)}, deepcopy(value))
    return value


def test_edit_total_and_per_chapter_without_rewriting_story_or_confirmation():
    value = session()
    before = deepcopy(value)
    request = workshop_repartition.Request.model_validate({
        'proposal_sha256': value['capacity']['proposal_sha256'], 'option_id': 'keep_story',
        'chapters': 2, 'total_words': 1160, 'words_per_chapter': 650,
        'chapter_targets': [{'number': 1, 'target_words': 590}, {'number': 2, 'target_words': 570}]})
    prepared = workshop_repartition.prepare(value, request, intent='discuss', mixed=False)
    assert prepared['local_result']['plan']['options'][0]['total_words'] == 1160
    assert prepared['local_result']['plan']['scenes'] == before['capacity']['plan']['scenes']
    assert value == before
    candidate, receipt = workshop_repartition.finish({**value, 'capacity_replan': prepared}, None)
    assert candidate['capacity_plan']['options'][0]['chapters'][0]['target_words'] == 590
    assert candidate['ready_for_confirmation'] is False and candidate['prompt_md'] is None
    assert receipt['requested_total_words'] == 1160
    assert receipt['author_request'] == request.model_dump()


def test_legacy_repartition_request_serializes_without_new_empty_fields():
    raw = {'proposal_sha256': 'a' * 64, 'option_id': 'keep_story', 'chapters': 4}
    request = workshop_repartition.Request.model_validate(raw)
    assert request.model_dump() == raw


def test_expanding_beyond_estimate_requires_full_reestimation_not_numeric_rescale():
    value = session()
    request = workshop_repartition.Request.model_validate({
        'proposal_sha256': value['capacity']['proposal_sha256'], 'option_id': 'keep_story',
        'chapters': 4, 'total_words': 4000, 'words_per_chapter': 1000})
    prepared = workshop_repartition.prepare(value, request, intent='discuss', mixed=False)
    assert 'local_result' not in prepared
    assert prepared['requires_reestimate'] is True
    assert prepared['plan'] == value['capacity']['plan']
    assert prepared['total_words'] == 4000
    assert prepared['source_sha256'] == value['capacity']['source_sha256']


def expansion():
    return {'status': 'adjusted', 'reply': '为两次重要抉择保留现场冲突、当事人反应和后果。',
            'scene_estimates': [{'scene_id': f'scene_{n}', 'minimum': 1900, 'maximum': 2100,
                                 'reason': f'第 {n} 次选择需要铺垫压力、展开交锋并保留代价。'} for n in (1, 2)],
            'chapters': [{'number': n, 'segments': [{'scene_id': f'scene_{(n + 1) // 2}',
                         'title': '抉择前的压力' if n % 2 else '抉择后的代价', 'target_words': 1000}]}
                         for n in range(1, 5)]}


def test_reestimate_preserves_exact_source_and_emotional_obligations():
    original = capacity_plan(2, 550)
    before = deepcopy(original)
    result = reestimate(original, 'keep_story', 4, 1000, 4000, expansion())
    assert original == before
    assert result['plan']['options'][0]['total_words'] == 4000
    for scene, provenance in zip(result['plan']['scenes'], result['provenance'], strict=True):
        source = next(s for s in original['scenes'] if s['id'] == provenance['source_scene_id'])
        for key in ('source_quote', 'reader_value', 'presentation', 'title', 'reason'):
            assert scene[key] == source[key]
        assert scene['estimate_reason']


@pytest.mark.parametrize('mutate', [
    lambda raw: raw['scene_estimates'].pop(),
    lambda raw: raw['scene_estimates'].append(deepcopy(raw['scene_estimates'][0])),
    lambda raw: raw['scene_estimates'][0].update(scene_id='imagined'),
    lambda raw: raw['scene_estimates'][0].update(reader_value='Delete the aftermath'),
    lambda raw: raw['chapters'][0]['segments'][0].update(target_words=900),
    lambda raw: raw['chapters'][0]['segments'][0].update(scene_id='scene_2'),
    lambda raw: raw['scene_estimates'][0].update(minimum=3000),
])
def test_reestimation_cannot_drop_reorder_rewrite_or_silently_change_total(mutate):
    raw = expansion(); mutate(raw)
    with pytest.raises(ValueError):
        reestimate(capacity_plan(2, 550), 'keep_story', 4, 1000, 4000, raw)


def test_explicit_chapter_targets_are_bound_during_reestimation():
    targets = [{'number': n, 'target_words': 900 if n % 2 else 1100} for n in range(1, 5)]
    with pytest.raises(ValueError, match='各章字数'):
        reestimate(capacity_plan(2, 550), 'keep_story', 4, 1200, 4000, expansion(), targets)


def test_local_numeric_allocation_cannot_exceed_ranges_or_silently_clamp():
    plan = capacity_plan(2, 550)
    assert within_estimates(plan, 'keep_story', 2, 600, 1300) is None
    assert within_estimates(plan, 'keep_story', 2, 400, 800) is None
    value = within_estimates(plan, 'keep_story', 2, 600, 1101)
    assert value['plan']['options'][0]['total_words'] == 1101
    assert sum(c['target_words'] for c in value['plan']['options'][0]['chapters']) == 1101
    assert value['plan']['scenes'] == plan['scenes']


def test_confirmed_capacity_remains_editable_without_mutating_old_contract():
    value = session()
    choice = workshop_capacity.Choice(proposal_sha256=value['capacity']['proposal_sha256'], option_id='keep_story')
    allocation = workshop_capacity.validate_choice(value, choice, 'deliver', '', False)
    workshop_capacity.adopt(value, allocation, 'now')
    value['revision'] += 1
    before = deepcopy(value)
    request = workshop_repartition.Request(proposal_sha256=value['capacity']['proposal_sha256'],
        option_id='keep_story', chapters=2, total_words=1160, words_per_chapter=650)
    prepared = workshop_repartition.prepare(value, request, intent='discuss', mixed=False)
    assert prepared['local_result'] and value == before
    workshop_capacity.invalidate(value)
    workshop_repartition.apply_preferences(value, prepared)
    assert value['capacity_history'][-1]['confirmed'] == allocation
    assert value['capacity']['confirmed'] is None
    assert value['input']['words_per_chapter'] == 650 and value['input']['target_total_words'] == 1160


def test_insufficient_total_keeps_original_and_allows_scoped_numeric_retry_only():
    value = session()
    request = workshop_repartition.Request(proposal_sha256=value['capacity']['proposal_sha256'],
        option_id='keep_story', chapters=2, total_words=400, words_per_chapter=200)
    prepared = workshop_repartition.prepare(value, request, intent='discuss', mixed=False)
    original = deepcopy(value['capacity']['plan'])
    workshop_capacity.invalidate(value)
    workshop_repartition.apply_preferences(value, prepared)
    value['capacity_replan'] = prepared
    candidate, receipt = workshop_repartition.finish(value, {
        'status': 'needs_more_space', 'reply': '两场人物抉择的压力与代价不能在各 200 词内完整呈现。',
        'minimum_total_words': 1000})
    assert 'capacity_plan' not in candidate and candidate['ready_for_confirmation'] is False
    value['messages'].append({'role': 'assistant', 'content': candidate['reply']})
    workshop_repartition.complete_edit(value, receipt)
    assert value['capacity']['plan'] == original and value['capacity']['status'] == 'stale'
    amended = request.model_copy(update={'total_words': 1100, 'words_per_chapter': 600})
    retry = workshop_repartition.prepare(value, amended, intent='discuss', mixed=False)
    assert retry['local_result']
    value['messages'].append({'role': 'user', 'content': 'Change the ending.'})
    with pytest.raises(ValueError, match='篇幅方案已变化'):
        workshop_repartition.prepare(value, amended, intent='discuss', mixed=False)


@pytest.mark.parametrize('mutation', [
    {'total_words': True}, {'words_per_chapter': 0}, {'total_words': 400, 'chapters': 1, 'words_per_chapter': 200},
    {'total_words': 1100, 'chapter_targets': [{'number': 1, 'target_words': 1100}]},
    {'total_words': 1100, 'chapter_targets': [{'number': 1, 'target_words': 500}, {'number': 2, 'target_words': 500}]},
])
def test_invalid_numeric_edits_do_not_change_session(mutation):
    value = session(); before = deepcopy(value)
    with pytest.raises(ValueError):
        raw = {'proposal_sha256': value['capacity']['proposal_sha256'], 'option_id': 'keep_story',
               'chapters': 2, 'total_words': 1100, 'words_per_chapter': 600, **mutation}
        request = workshop_repartition.Request.model_validate(raw)
        workshop_repartition.prepare(value, request, intent='discuss', mixed=False)
    assert value == before


def test_local_reuse_cites_only_verified_original_scene_fields():
    value = session()
    # The quote was supplied through an earlier source packet and is not repeated
    # in the brief. Local numeric editing must preserve that proven lineage.
    value['input']['premise'] = 'A newly imported reference route.'
    value['capacity']['basis_sha256'] = digest(workshop_capacity.source(value))
    value['capacity']['proposal_sha256'] = digest({'plan': value['capacity']['plan'],
                                                   'basis_sha256': value['capacity']['basis_sha256']})
    request = workshop_repartition.Request(proposal_sha256=value['capacity']['proposal_sha256'],
        option_id='keep_story', chapters=2, total_words=1160, words_per_chapter=650)
    prepared = workshop_repartition.prepare(value, request, intent='discuss', mixed=False)
    workshop_capacity.invalidate(value)
    workshop_repartition.apply_preferences(value, prepared)
    value['capacity_replan'] = prepared
    prepared['request_basis'] = digest(workshop_capacity.source(value))
    receipt = workshop_capacity.make_retained_plan_receipt(value)
    candidate, _ = workshop_repartition.finish(value, None)
    destination = deepcopy(value)
    workshop_capacity.record(destination, candidate, value, source_receipt=receipt)
    assert destination['capacity']['status'] == 'proposed'
    # A tampered plan cannot grant citation scope just by retaining its request ID.
    value['capacity_replan']['plan']['scenes'][0]['source_quote'] = 'This was never supplied or validated.'
    rejected = workshop_capacity.make_retained_plan_receipt(value)
    assert rejected.valid is False
