"""Source-bound correction never replaces the book or certifies a review."""
import copy

import pytest

from core import planning_coordinated as correction
from outline_planning import content_hash


SOURCE = ('Mara refuses the sale. She retrieves her card before entering the locked studio. '
          'Her grief remains real even when she chooses independent work.')
QUOTE = 'She retrieves her card before entering the locked studio.'


def candidate():
    chapters = [{
        'number': number, 'title': f'Chapter {number}', 'target_words': 1000,
        'summary': 'Mara enters the studio.', 'source_anchor_ids': ['source_1'],
        'scenes': [{'id': f'scene_{number}', 'pov': 'Mara', 'goal': 'Keep the studio',
                    'conflict': 'Financial pressure', 'change': 'She accepts a cost',
                    'bespoke_detail': {'gesture': 'She holds the cracked cup.'}}],
        'setups': [{'id': f'setup_{number}', 'description': 'The card is in her coat.'}],
        'payoffs': [], 'reserved_later_payoffs': ['The sale remains refused.'],
        'execution_contract': {'schema_version': 1, 'chapter': number,
            'active_choice': 'She enters the studio.', 'world_event_ids': ['entry'],
            'required_status': 'active', 'hook_type': 'pressure',
            'protagonist_causes_turn': True, 'weight': 1.0},
        'bespoke_detail': {'gesture': 'She keeps the cup.', 'obligations': ['Keep grief real.']},
    } for number in range(1, 61)]
    return {
        'chapters': chapters,
        'chapter_map': [{'number': number, 'purpose': 'Preserve independent work.',
            'incoming_consequence': 'The door is locked.', 'outgoing_commitment': 'She enters.',
            'source_anchor_ids': ['source_1'], 'reserved_reveals': ['The eventual repair.'],
            'ending_obligations': ['The studio remains independent.']}
            for number in range(1, 61)],
        'characters': [{'id': 'mara', 'name': 'Mara'}],
        'ending_contract': {'required_outcome': 'independent_work'},
        'classification': {'genre': 'women_fiction'}, 'source_anchors': [{'id': 'source_1', 'quote': SOURCE}],
    }


def review():
    return {'status': 'blocked', 'issues': [
        {'scope': 'global', 'message': 'Track the card across the entry.', 'source_evidence': QUOTE},
        {'scope': 'chapter', 'chapter': 2, 'message': 'Retain the retrieval before entry.', 'source_evidence': QUOTE},
    ]}


def plan():
    return {'issues': [{'issue_index': index, 'classification': 'continuity_gap',
        'reason': 'Chapter 2 must account for the card before entry.',
        'requested_chapters': [2], 'dependency_chapters': [1, 3, 60], 'source_quotes': [QUOTE]}
        for index in range(2)]}


def proposal(saved=None):
    saved = saved or candidate()
    after = copy.deepcopy(saved['chapters'][1])
    after['summary'] = 'Mara retrieves her card, then enters the locked studio.'
    after['execution_contract']['active_choice'] = 'She retrieves her card before choosing to enter.'
    return {'status': 'corrected', 'reason': 'Restore the missing source-supported bridge.',
        'issues': [{'issue_index': index, 'status': 'corrected', 'reason': 'Card retrieval precedes entry.',
                    'source_quotes': [QUOTE], 'chapter_numbers': [2]} for index in range(2)],
        'chapter_replacements': [{'number': 2, 'before_sha256': correction.chapter_hash(saved['chapters'][1]),
                                 'after': after, 'issue_indices': [0, 1], 'source_quote': QUOTE}],
        'global_edits': [{'path': '/chapter_map/1/incoming_consequence', 'before': 'The door is locked.',
            'after': 'She has retrieved her card before facing the locked door.',
            'issue_indices': [0], 'source_quote': QUOTE}]}


def apply(saved, value, *, requested=None):
    return correction.apply(saved, review(), SOURCE, value, requested_chapters=requested or [2])


def test_plan_accounts_for_all_findings_and_distant_dependencies_without_mutation():
    saved, findings, value = candidate(), review(), plan()
    original = copy.deepcopy((saved, findings, value))
    validated = correction.validate_plan(saved, findings, SOURCE, value)
    assert validated == value and validated is not value
    assert (saved, findings, value) == original
    assert validated['issues'][0]['dependency_chapters'] == [1, 3, 60]


@pytest.mark.parametrize('mutate', [
    lambda p: p['issues'].pop(),
    lambda p: p['issues'][1].update(issue_index=0),
    lambda p: p['issues'][1].update(issue_index=True),
    lambda p: p['issues'][1].update(classification='passed'),
    lambda p: p['issues'][1].update(requested_chapters=[]),
    lambda p: p['issues'][1].update(requested_chapters=[3]),
    lambda p: p['issues'][0].update(dependency_chapters=[2]),
    lambda p: p['issues'][0].update(dependency_chapters=[61]),
    lambda p: p['issues'][0].update(requested_chapters=[True]),
    lambda p: p['issues'][0].update(requested_chapters=[2, 2]),
    lambda p: p['issues'][0].update(source_quotes=['An invented source quotation.']),
    lambda p: p['issues'][0].update(source_quotes=[]),
    lambda p: p['issues'][0].update(classification='interpretation'),
    lambda p: p['issues'][0].update(classification='source_conflict'),
    lambda p: p.update(status='passed'),
])
def test_plan_rejects_unaccounted_findings_bad_scope_or_invented_evidence(mutate):
    value = plan()
    mutate(value)
    with pytest.raises(ValueError):
        correction.validate_plan(candidate(), review(), SOURCE, value)


def test_interpretation_and_source_conflict_are_explicit_and_never_authorize_chapter_edits():
    value = plan()
    value['issues'][0].update(classification='interpretation', requested_chapters=[])
    value['issues'][1].update(classification='source_conflict', requested_chapters=[])
    result = correction.validate_plan(candidate(), review(), SOURCE, value)
    assert result == value
    assert 'status' not in result


def test_coordinated_edits_preserve_all_sixty_chapters_source_contracts_and_inputs():
    saved = candidate()
    value = proposal(saved)
    original = copy.deepcopy((saved, value))
    result = apply(saved, value)
    assert len(result['chapters']) == 60
    assert result['chapters'][1] == value['chapter_replacements'][0]['after']
    assert result['chapters'][:1] == saved['chapters'][:1]
    assert result['chapters'][2:] == saved['chapters'][2:]
    assert result['chapter_map'][:1] == saved['chapter_map'][:1]
    assert result['chapter_map'][2:] == saved['chapter_map'][2:]
    for key in ('characters', 'ending_contract', 'classification', 'source_anchors'):
        assert result[key] == saved[key]
    assert result['chapters'][1]['bespoke_detail'] == saved['chapters'][1]['bespoke_detail']
    assert (saved, value) == original
    assert correction.chapter_hash(saved['chapters'][1]) == content_hash(saved['chapters'][1])
    assert 'status' not in result


def test_allowed_global_fields_exclude_identities_endings_and_nonrequested_chapters():
    allowed = correction.allowed_global_fields(candidate(), [2])
    assert set(allowed) == {'/chapter_map/1/purpose', '/chapter_map/1/incoming_consequence',
                            '/chapter_map/1/outgoing_commitment'}


@pytest.mark.parametrize('mutate', [
    lambda p: p['chapter_replacements'][0].update(before_sha256='stale'),
    lambda p: p['chapter_replacements'][0].update(number=3),
    lambda p: p['chapter_replacements'][0].update(number=True),
    lambda p: p['chapter_replacements'][0].update(issue_indices=[0, 0]),
    lambda p: p['chapter_replacements'][0].update(issue_indices=[0, 2]),
    lambda p: p['chapter_replacements'][0].update(source_quote='Invented.'),
    lambda p: p['chapter_replacements'].append(copy.deepcopy(p['chapter_replacements'][0])),
    lambda p: p['global_edits'][0].update(before='stale'),
    lambda p: p['global_edits'][0].update(path='/chapter_map/2/incoming_consequence'),
    lambda p: p['global_edits'][0].update(path='/chapter_map/1/reserved_reveals'),
    lambda p: p['global_edits'][0].update(path='/chapter_map/1/ending_obligations'),
    lambda p: p['global_edits'][0].update(path='/characters/0/name'),
    lambda p: p['global_edits'][0].update(path='/ending_contract/required_outcome'),
    lambda p: p['global_edits'].append(copy.deepcopy(p['global_edits'][0])),
    lambda p: p['issues'].pop(),
    lambda p: p['issues'][1].update(issue_index=0),
    lambda p: p['issues'][1].update(chapter_numbers=[3]),
    lambda p: p['issues'][1].update(status='recheck'),
    lambda p: p['issues'][1].update(status='passed'),
    lambda p: p['issues'][1].update(source_quotes=['Invented.']),
    lambda p: p['issues'][1].update(chapter_numbers=[]),
    lambda p: p.update(status='passed'),
    lambda p: p.update(candidate={'chapters': []}),
])
def test_repair_rejects_stale_unscoped_fabricated_or_unaccounted_edits_atomically(mutate):
    saved = candidate()
    original = copy.deepcopy(saved)
    value = proposal(saved)
    mutate(value)
    with pytest.raises(ValueError):
        apply(saved, value)
    assert saved == original


@pytest.mark.parametrize('mutate', [
    lambda ch: ch.pop('bespoke_detail'),
    lambda ch: ch['bespoke_detail'].pop('gesture'),
    lambda ch: ch.update(summary=''),
    lambda ch: ch.update(summary=None),
    lambda ch: ch.update(scenes=[]),
    lambda ch: ch['scenes'][0].pop('bespoke_detail'),
    lambda ch: ch['scenes'][0].update(id='another_scene'),
    lambda ch: ch.update(source_anchor_ids=['unknown']),
    lambda ch: ch['setups'][0].update(id='new_setup'),
    lambda ch: ch.update(target_words=50),
    lambda ch: ch.update(number=3),
    lambda ch: ch['execution_contract'].update(chapter=True),
    lambda ch: ch['execution_contract'].update(world_event_ids=['invented']),
    lambda ch: ch['execution_contract'].update(required_status='resolved'),
    lambda ch: ch['execution_contract'].update(hook_type='revelation'),
    lambda ch: ch['execution_contract'].update(weight=2.0),
    lambda ch: ch['execution_contract'].update(protagonist_causes_turn=False),
    lambda ch: ch['execution_contract'].update(extra_field='invented'),
    lambda ch: ch.update(reserved_later_payoffs=[]),
])
def test_full_replacement_cannot_drop_rich_detail_scenes_identity_or_execution_constraints(mutate):
    saved = candidate()
    value = proposal(saved)
    mutate(value['chapter_replacements'][0]['after'])
    with pytest.raises(ValueError):
        apply(saved, value)


def test_recheck_retains_issues_and_returns_unchanged_candidate_without_passing_review():
    saved = candidate()
    value = proposal(saved)
    value.update(chapter_replacements=[], global_edits=[])
    for item in value['issues']:
        item.update(status='recheck', chapter_numbers=[])
    result = correction.apply(saved, review(), SOURCE, value, requested_chapters=[])
    assert result == saved and result is not saved
    assert value['issues'][0]['status'] == 'recheck'
    assert review()['status'] == 'blocked'


def test_requires_author_cannot_partially_apply_other_corrections():
    saved = candidate()
    value = proposal(saved)
    value.update(status='requires_author')
    value['issues'][0]['status'] = 'requires_author'
    with pytest.raises(ValueError, match='unresolved correction'):
        apply(saved, value)
    value.update(chapter_replacements=[], global_edits=[])
    assert apply(saved, value) == saved
    value['status'] = 'corrected'
    with pytest.raises(ValueError, match='contradicts'):
        apply(saved, value)


def test_corrected_issue_cannot_claim_unmodified_chapters_or_noop_changes():
    saved = candidate()
    value = proposal(saved)
    value['issues'][0]['chapter_numbers'] = [2, 3]
    with pytest.raises(ValueError, match='every explicitly affected chapter'):
        apply(saved, value, requested=[2, 3])
    value = proposal(saved)
    value['chapter_replacements'][0]['after'] = copy.deepcopy(saved['chapters'][1])
    with pytest.raises(ValueError, match='actual correction'):
        apply(saved, value)


def test_correction_cannot_operate_on_a_truncated_saved_candidate():
    saved = candidate()
    value = proposal(saved)
    saved['chapters'].pop()
    with pytest.raises(ValueError, match='all saved chapter bodies'):
        apply(saved, value)


def test_chapter_interpretation_requires_reading_its_original_affected_chapter():
    value = plan()
    value['issues'][1].update(classification='interpretation', requested_chapters=[])
    with pytest.raises(ValueError, match='original affected chapter'):
        correction.validate_plan(candidate(), review(), SOURCE, value)
    value['issues'][1]['dependency_chapters'].append(2)
    assert correction.validate_plan(candidate(), review(), SOURCE, value) == value


def test_global_interpretation_can_require_a_complete_fresh_review_without_local_dependencies():
    value = plan()
    value['issues'][0].update(classification='interpretation', requested_chapters=[], dependency_chapters=[])
    assert correction.validate_plan(candidate(), review(), SOURCE, value) == value
