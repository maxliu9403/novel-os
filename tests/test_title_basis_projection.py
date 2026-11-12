"""Naming uses the route/overview, independent of chapter count and execution records."""
from copy import deepcopy
import json

import pytest

from core.creative_types import digest
from core.title_context import build_title_context, encode, request_context
from test_title_design_context import compact_story


def overview():
    return {'synopsis': 'She leaves a controlling marriage and chooses her own future.',
            'focus': 'ending', 'source_revision': 3, 'reviewed_revision': 4,
            'nodes': [{'area': area, 'summary': text, 'status': 'proposed',
                       'evidence': [{'quote': 'DO_NOT_TRANSMIT_EVIDENCE'}]}
                      for area, text in zip(('direction', 'opening', 'middle', 'climax', 'ending'),
                          ('independence', 'she leaves', 'she builds her business', 'she refuses a bargain', 'she remains single'))],
            'opening': {'situation': 'He arrives with their marriage certificate.',
                        'reader_experience': 'Will she hold her boundary?', 'preview': 'UNNEEDED_PROSE'}}


def source_context():
    route = {'title': 'Provisional route name', 'reader_promise': 'She must win her freedom.',
             'continuity_basis': 'A marriage certificate changes the confrontation.',
             'causal_arc': {'opening': 'The husband returns.', 'development': 'She leaves.',
                            'turning_point': 'She rejects his money.', 'climax': 'She wins independence.',
                            'ending': 'They reconcile after a year.'},
             'character_relationship_arcs': ['From control to equal partners.'],
             'emotional_design': [{'scene': 'A public refusal', 'cost': 'Her job', 'payoff': 'Agency'}],
             'highlights': [{'scene': 'She hands back his keys.'}],
             'long_hooks_and_payoffs': [{'question': 'Can he earn her trust?', 'payoff': 'She chooses freely.'}],
             'proposed_assumptions': ['A possible inheritance is unconfirmed.'],
             'source_refs': ['PRIVATE_EVIDENCE_ID'], 'type_suggestion': {'reason': 'UNNEEDED_TYPE_GUIDE'}}
    route['sha256'] = digest(route)
    value = {'schema': 'source-route-context.v1', 'source_id': 'source-' + 'a' * 64,
             'source_sha256': 'a' * 64, 'understanding_sha256': 'b' * 64, 'route_set_sha256': 'c' * 64,
             'mode': 'original_rewrite', 'selected_proposal': route,
             'evidence': {'schema': 'source-route-evidence.v1', 'sha256': 'd' * 64},
             'author_feedback': [{'exact_text': 'Give her agency, not just suffering.'}]}
    value['context_sha256'] = digest(value)
    return value


def session(*, reference=False):
    value = {'prompt_md': compact_story(), 'input': {'language': 'English'},
             'creative_design': {'story_state': {'map': overview(), 'assumptions': ['OLD_ALTERNATIVES']},
                                 'rounds': [{'source_revision': 3, 'message_index': 1}]},
             'messages': [{'role': 'user', 'content': 'OLD_INCORPORATED_BRIEF'},
                          {'role': 'assistant', 'content': 'UNNEEDED_DISCUSSION'},
                          {'role': 'user', 'content': 'Keep her single. Do not promise reconciliation.'},
                          {'role': 'user', 'action': 'develop_story', 'content': 'HOST_CONTINUE', 'author_note': 'Keep the quiet hope.'}],
             'naming': {'requests': [{'instructions': 'Use English. No spoilers.'}]}}
    if reference:
        value['source_context'] = source_context()
    return value


@pytest.mark.parametrize('reference', [True, False])
def test_two_paths_keep_emotional_story_and_new_author_changes_without_book_payload(reference):
    value = session(reference=reference)
    before = deepcopy(value)
    result = build_title_context(value)
    text = encode(result['context'])
    assert result['context']['basis_kind'] == ('reference_route' if reference else 'story_overview')
    assert result['context_bytes'] < 10_000
    for keep in ['She leaves a controlling marriage', 'Keep her single.', 'quiet hope', 'Use English. No spoilers.',
                 'OLD_INCORPORATED_BRIEF']:
        assert keep in text
    for omit in ['UNNEEDED_CHAPTER_DETAIL', 'UNNEEDED_DISCUSSION', 'HOST_CONTINUE', 'OLD_ALTERNATIVES',
                 'DO_NOT_TRANSMIT_EVIDENCE', 'UNNEEDED_PROSE']:
        assert omit not in text
    if reference:
        for keep in ['public refusal', 'Her job', 'Agency', 'hands back his keys', 'possible inheritance']:
            assert keep in text
        assert 'PRIVATE_EVIDENCE_ID' not in text and 'UNNEEDED_TYPE_GUIDE' not in text
        assert next(row for row in result['context']['sources'] if row['id'] == 'ending')['purpose'] == 'private_spoiler_boundary'
    assert value == before


def test_legacy_narrative_fallback_excludes_capacity_contract_without_cutting_story():
    source = compact_story().replace('## 2.', '[STORY_CAPACITY_JSON]\n' +
        json.dumps({'scenes': 'HOST_ALLOCATION' * 9000}) + '\n[/STORY_CAPACITY_JSON]\n## 2.', 1)
    result = build_title_context({'prompt_md': source})
    text = encode(result['context'])
    assert result['context_bytes'] < 5000
    assert 'HOST_ALLOCATION' not in text and 'STORY_CAPACITY_JSON' not in text
    assert 'captain is the killer' in text and 'not her husband' in text


def test_route_does_not_require_a_parseable_or_small_skeleton():
    value = session(reference=True)
    value['prompt_md'] = 'custom skeleton ' * 50_000
    assert build_title_context(value)['context']['basis_kind'] == 'reference_route'


def test_route_hash_corruption_is_not_silently_treated_as_original():
    value = session(reference=True)
    value['source_context']['selected_proposal']['reader_promise'] = 'tampered'
    with pytest.raises(ValueError, match='integrity'):
        build_title_context(value)


@pytest.mark.parametrize('change', ['overview', 'author', 'route'])
def test_frozen_basis_detects_changed_route_overview_or_author_even_with_same_skeleton(change):
    value = session(reference=True)
    result = build_title_context(value)
    value['naming']['requests'][-1]['title_context'] = result
    if change == 'overview':
        value['creative_design']['story_state']['map']['synopsis'] = 'Different story.'
    elif change == 'author':
        value['messages'][-1]['author_note'] = 'Different direction.'
    else:
        value['source_context'] = deepcopy(value['source_context'])
        value['source_context']['mode'] = 'source_development'
        value['source_context']['context_sha256'] = digest({k:v for k,v in value['source_context'].items() if k != 'context_sha256'})
    with pytest.raises(ValueError, match='命名依据'):
        request_context(value)


def test_v2_frozen_requests_still_validate_and_resume_without_reprojection():
    # Historical v2 only bound skeleton+context; it must retain that contract.
    value = {'prompt_md': compact_story()}
    projection = build_title_context(value)
    projection['projection'] = 'title-context.v2'
    projection.pop('basis_sha256', None)
    value['naming'] = {'requests': [{'title_context': projection}]}
    assert request_context(value) == projection
    projection['context']['author_requests'].append({'instructions': 'tampered'})
    with pytest.raises(ValueError, match='快照已变化'):
        request_context(value)


def test_v1_frozen_request_keeps_original_context_without_migration():
    source = compact_story()
    import hashlib
    projection = {'projection': 'title-context.v1',
                  'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                  'context': {'sources': [{'id': 'story', 'text': 'Original paid request.'}]}}
    value = session(reference=True)
    value['naming']['requests'][-1]['title_context'] = deepcopy(projection)
    assert request_context(value) == projection


def test_story_revision_uses_current_confirmed_global_story_instead_of_stale_map():
    value = session()
    value['messages'].append({'role': 'user', 'action': 'skeleton_revision', 'content': 'Adopt reviewed revision.'})
    result = build_title_context(value)
    assert result['context']['basis_kind'] == 'legacy_skeleton'
    assert 'captain is the killer' in encode(result['context'])
    assert 'She leaves a controlling marriage' not in encode(result['context'])


def test_repeated_author_update_retains_its_later_chronological_priority():
    value = session(reference=True)
    value['messages'] = value['messages'][:2] + [
        {'role': 'user', 'content': text}
        for text in ['Keep her single.', 'Let them reconcile.', 'Keep her single.']]
    changes = build_title_context(value)['context']['story_updates']
    assert [row['text'] for row in changes][-3:] == [
        'Keep her single.', 'Let them reconcile.', 'Keep her single.']


@pytest.mark.parametrize('rounds', [[], [{'source_revision': 3, 'message_index': 99}]])
def test_legacy_unknown_overview_chronology_keeps_author_words(rounds):
    value = session()
    value['creative_design']['rounds'] = rounds
    text = encode(build_title_context(value)['context'])
    assert 'OLD_INCORPORATED_BRIEF' in text
    assert 'Keep her single.' in text
    assert 'UNNEEDED_DISCUSSION' not in text


@pytest.mark.parametrize('reference', [True, False])
@pytest.mark.parametrize('change', ['choice', 'delegation', 'invalid_map', 'skeleton_revision'])
def test_saved_global_story_wins_when_map_predates_a_delivered_change(reference, change):
    value = session(reference=reference)
    design = value['creative_design']
    if change == 'choice':
        design['decisions'] = [{'proposal_id': 'r3:p2', 'status': 'accepted',
                                'message_index': 2, 'revision': 5}]
        value['messages'][2]['content'] = 'Adopt option r3:p2.'  # Not its full story.
    elif change == 'delegation':
        design['story_state']['actions'] = [{'mode': 'delegate', 'revision': 5}]
    elif change == 'invalid_map':
        from api.workshop_story import record_story
        value.update(revision=6, turn_intent='discuss')
        state = design['story_state']
        state.update(actions=[], assumptions=[], pending=None)
        old_map = deepcopy(state['map'])
        record_story(None, {'source_revision': 5, 'message_index': 3}, value)
        assert state['map'] == old_map
        assert state['validation']['map_status'] == 'preserved'
        value['turn_intent'] = 'deliver'  # Delivery does not regenerate that map.
    else:
        value['messages'].append({'role': 'user', 'action': 'skeleton_revision', 'content': 'Adopt reviewed revision.'})
    result = build_title_context(value)
    assert result['context']['basis_kind'] == ('reference_route' if reference else 'legacy_skeleton')
    sources = {row['id']: row for row in result['context']['sources']}
    text = sources['current_story']['text'] if reference else encode(result['context']['sources'])
    assert 'captain is the killer' in text  # Current saved skeleton in this fixture.
    assert 'She leaves a controlling marriage' not in text  # Retained stale map.


def test_author_form_and_early_restrictions_survive_overview_omission():
    value = session()
    value['input'].update(premise='A quiet story about independence.', constraints='No secret children.')
    value['messages'][0]['content'] = 'Do not make humiliation the emotional hook.'
    context = build_title_context(value)['context']
    text = encode(context)
    for required in ('A quiet story', 'No secret children.', 'Do not make humiliation'):
        assert required in text
    value['input_origins'] = {'premise': 'source'}
    assert 'A quiet story' not in encode(build_title_context(value)['context'])


def test_frozen_context_detects_author_form_change_without_skeleton_change():
    value = session()
    value['input']['constraints'] = 'No secret children.'
    value['naming']['requests'][-1]['title_context'] = build_title_context(value)
    value['input']['constraints'] = 'No revenge.'
    with pytest.raises(ValueError, match='命名依据'):
        request_context(value)


def _verified_route_adjustments(value, replacements, instruction):
    """Use production authority builders for an already verified review receipt."""
    from core.creative_amendments import witness
    from core.creative_baseline import build, DEVELOPMENT_VERSION

    value.update(id='title-author-amendment', revision=5,
                 delivery_run_id='c' * 32, delivery_published_run_id='c' * 32)
    value['source_route_selection'] = {'action_id': 'selected-route-1',
        'adoption_scope': 'core_and_payoffs', 'adopted_at_message_index': 0}
    value['messages'].append({'role': 'user', 'content': instruction})
    baseline = build(value)
    changes = []
    for path, effective in replacements:
        item = next(row for row in baseline['items'] if row['path'] == ['selected_proposal', *path])
        changes.append(witness(baseline, {
            'commitment_id': item['id'],
            'author_requirement_id': f"author-message-{len(value['messages'])}",
            'author_quote': instruction,
            'effective_value': effective,
        }, {'authorized': True, 'reason': 'The author explicitly changes this adopted story obligation.'},
            digest({'task': 'verify_author_adjustments', 'baseline': baseline['sha256'], 'path': path})))
    value['development'] = {'schema': DEVELOPMENT_VERSION, 'accepted': {
        'candidate_sha256': digest('saved accepted story'), 'baseline_sha256': baseline['sha256'],
        'receipt': {'author_adjustments': changes}}}
    # Real baseline validation reuses the saved witness, including exact author
    # text and original commitment identity; naming must not bypass that check.
    assert build(value)['author_adjustments'] == changes
    return changes


@pytest.mark.parametrize('effective', ['She leaves alone and finally belongs to herself.', None])
def test_verified_author_ending_replacement_or_retirement_does_not_restore_old_route(effective):
    value = session(reference=True)
    _verified_route_adjustments(value, [(['causal_arc', 'ending'], effective)],
        'Withdraw the reconciliation ending. She leaves alone and finally belongs to herself.')
    before = deepcopy(value)
    result = build_title_context(value)
    sources = {row['id']: row for row in result['context']['sources']}
    assert result['context']['basis_kind'] == 'reference_route'
    if effective is None:
        assert 'ending' not in sources
    else:
        assert sources['ending']['text'] == effective
        assert sources['ending']['purpose'] == 'private_spoiler_boundary'
    assert 'They reconcile after a year.' not in encode(result['context'])
    assert 'The husband returns.' in sources['story']['text']
    assert value['source_context']['selected_proposal']['causal_arc']['ending'] == 'They reconcile after a year.'
    assert value == before  # Includes the original route, adopted receipt and delivery bindings.


def test_verified_indexed_emotion_retirement_preserves_later_replacement_and_saved_receipt():
    value = session(reference=True)
    route = value['source_context']['selected_proposal']
    route['emotional_design'].append({'scene': 'Their harbor meeting', 'cost': 'Her freedom', 'payoff': 'A wedding'})
    route['sha256'] = digest({key: item for key, item in route.items() if key != 'sha256'})
    value['source_context']['context_sha256'] = digest({key: item for key, item in value['source_context'].items()
                                                      if key != 'context_sha256'})
    revised = {'scene': 'Their harbor meeting', 'cost': 'Their shared future', 'payoff': 'She boards alone'}
    _verified_route_adjustments(value,
        [(['emotional_design', 0], None), (['emotional_design', 1], revised)],
        'Remove the public refusal scene. Keep the harbor meeting, but she gives up their shared future and boards alone.')
    before = deepcopy(value)
    result = build_title_context(value)
    emotions = next(row for row in result['context']['sources'] if row['id'] == 'emotions')
    assert json.loads(emotions['text']) == [revised]
    assert 'A wedding' not in emotions['text'] and 'Her job' not in emotions['text']
    assert value == before


def test_invalid_author_adjustment_cannot_silently_fall_back_to_old_ending():
    value = session(reference=True)
    changes = _verified_route_adjustments(value,
        [(['causal_arc', 'ending'], 'She remains independent.')], 'Keep her single and independent.')
    changes[0]['effective_value'] = 'Unverified different ending.'
    before = deepcopy(value)
    with pytest.raises(ValueError, match='作者改线见证'):
        build_title_context(value)
    assert value == before
