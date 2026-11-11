from copy import deepcopy
from types import SimpleNamespace

import pytest

from core import planning_residual_identity as identity


SOURCE = 'One role waits outside. 另一个打手走进来，他看见了门。Neither occurrence is explicitly equated with the other.'


def book():
    return {'characters': [
                {'id': 'char_a', 'name': 'first unnamed role', 'gender': '', 'pronouns': '', 'aliases': []},
                {'id': 'char_b', 'name': 'second unnamed role', 'gender': '', 'pronouns': '', 'aliases': []},
                {'id': 'char_bad', 'name': 'unrelated role', 'gender': '', 'pronouns': '', 'aliases': []}],
            'chapters': [{'number': 1, 'character_ids': ['char_a'], 'scenes': []},
                         {'number': 2, 'character_ids': ['char_b'], 'scenes': []},
                         {'number': 3, 'character_ids': ['char_bad'], 'scenes': []}],
            'chapter_map': [{'number': 1}, {'number': 2}, {'number': 3}],
            'ending_contract': {'preserved': 'unaltered'}}


def issue(index=0, **values):
    return {'issue_index': index, 'classification': 'continuity_gap', 'reason': 'Verify the source-supported role metadata.',
            'affected_chapters': [1, 2], 'writable_chapters': [1, 2], 'dependency_chapters': [],
            'source_quotes': ['One role waits outside.', '另一个打手走进来，他看见了门。'],
            'candidate_evidence': [], **values}


def finding():
    return {'message': 'Record the source-supported pronouns for char_b and preserve the unknown relation to char_a.'}


def proposal():
    return {'identity_fields': [{'character_id': 'char_b', 'field': 'pronouns', 'before': '',
                                 'value': 'he/him', 'source_indices': [1]}],
            'identity_relations': [{'character_ids': ['char_b', 'char_a'], 'relation': 'unknown',
                'reason': 'The source establishes two role occurrences but no identity equivalence.',
                'source_evidence': [{'character_id': 'char_a', 'source_index': 0},
                                    {'character_id': 'char_b', 'source_index': 1}]}]}


class Planner:
    source = SOURCE
    client = SimpleNamespace()

    def __init__(self, reply=proposal):
        self.calls, self.saved, self.reply = [], {}, reply

    def _load(self, name, deps, validate, **options):
        assert options['strict_dependencies']
        if name in self.saved:
            expected, value = self.saved[name]
            assert deps == expected
            return validate(deepcopy(value))

    def _save(self, name, deps, value):
        self.saved[name] = (deepcopy(deps), deepcopy(value))

    def _call(self, name, instruction, context, validate, **options):
        assert options['strict_dependencies'] and options['recover_archived']
        assert instruction.startswith('STAGE: coordinated_residual_identity')
        self.calls.append(deepcopy(context))
        return validate(self.reply())


def test_source_pronouns_and_unknown_role_relation_preserve_all_other_metadata_and_checkpoints():
    candidate, p = book(), Planner()
    plan = {'issues': [issue(prior_author_choices=[{'choice_id': 'verified'}])]}
    review = {'issues': [finding()]}
    fixed, audit = identity.repair(p, candidate, review, plan, blocked_indices=set())
    assert candidate == book()
    assert fixed['characters'][0] == candidate['characters'][0]
    assert fixed['characters'][1] == {**candidate['characters'][1], 'pronouns': 'he/him'}
    assert fixed['chapters'] == candidate['chapters'] and fixed['ending_contract'] == candidate['ending_contract']
    constraint, = fixed['identity_constraints']
    assert constraint['character_ids'] == ['char_a', 'char_b'] and constraint['relation'] == 'unknown'
    assert [row['quote'] for row in constraint['source_evidence']] == plan['issues'][0]['source_quotes']
    assert audit[0]['issue_index'] == 0 and audit[0]['source_quotes'] == plan['issues'][0]['source_quotes']
    assert p.calls[0]['prior_author_choices'] == [{'choice_id': 'verified'}]
    assert identity.repair(p, candidate, review, plan, blocked_indices=set()) == (fixed, audit)
    assert len(p.calls) == 1


def test_exact_character_path_targets_a_role_without_english_keyword_classification():
    p, candidate = Planner(lambda: {'identity_fields': [], 'identity_relations': []}), book()
    item = issue(candidate_evidence=[{'path': '/characters/1/id', 'quote': 'char_b'}])
    context = identity.build_context(p, candidate, {'message': '核对这个已登记角色的称谓。'}, item)
    assert [row['id'] for row in context['permitted_characters']] == ['char_b']
    assert identity.build_context(p, candidate, {'message': 'No explicit record requires metadata repair.'}, issue()) is None
    assert [row['id'] for row in identity.build_context(p, candidate, {'message': 'char_bad only.'},
                                                        issue(affected_chapters=[3], writable_chapters=[3]))['permitted_characters']] == ['char_bad']


def test_unmentioned_and_held_roles_cannot_receive_global_changes():
    p, candidate = Planner(), book()
    context = identity.build_context(p, candidate, finding(), issue(affected_chapters=[3], writable_chapters=[3]))
    assert context['scope_discovery']['added_read_only_chapters'] == [1, 2]
    assert context['writable_chapters'] == [3]
    assert [row['id'] for row in context['permitted_characters']] == ['char_a', 'char_b']
    held = issue(1, affected_chapters=[2], writable_chapters=[], classification='source_conflict')
    item = issue(affected_chapters=[2], writable_chapters=[2])
    fixed, audit = identity.repair(p, candidate, {'issues': [{'message': 'char_b'}, {'message': 'char_b dispute'}]},
                                   {'issues': [item, held]}, blocked_indices={1})
    assert fixed == candidate and not audit and not p.calls


def test_metadata_discovers_all_role_occurrences_as_complete_read_only_evidence():
    p, candidate = Planner(lambda: {'identity_fields': [{'character_id': 'char_b', 'field': 'pronouns',
        'before': '', 'value': 'he/him', 'source_indices': [1]}], 'identity_relations': []}), book()
    # A plan identifies chapters 27-28 but overlooks the role's next appearance.
    candidate['chapters'] = [
        {'number': 27, 'scenes': [{'participants': ['char_b']}], 'detail': 'Keep every scene field.'},
        {'number': 28, 'character_ids': ['char_b'], 'scenes': []},
        {'number': 29, 'scenes': [{'contract': {'witness': 'char_b'}}], 'detail': 'Existing death bridge.'},
        {'number': 30, 'scenes': [], 'detail': 'An independent chapter.'}]
    candidate['chapter_map'] = [{'number': n, 'purpose': f'Complete route {n}.'} for n in range(27, 31)]
    item = issue(affected_chapters=[27, 28], writable_chapters=[27, 28],
                 candidate_evidence=[{'path': '/characters/1/pronouns', 'quote': ''}])
    review = {'issues': [{'message': 'Record this role’s source-supported pronouns.'}]}
    original = deepcopy(candidate)
    fixed, audit = identity.repair(p, candidate, review, {'issues': [item]}, blocked_indices=set())
    context, = p.calls
    assert context['affected_chapters'] == context['writable_chapters'] == [27, 28]
    assert context['read_only_chapters'] == [29]
    assert context['role_occurrences'] == {'char_b': [27, 28, 29]}
    assert context['candidate_chapters'] == original['chapters'][:3]
    assert context['chapter_map'] == original['chapter_map'][:3]
    assert context['source_material'] == [[['adopted_source', 0, len(SOURCE)], SOURCE]]
    assert context['scope_discovery']['added_read_only_chapters'] == [29]
    assert audit[0]['scope_discovery'] == context['scope_discovery']
    assert fixed['characters'][1]['pronouns'] == 'he/him'
    assert fixed['chapters'] == original['chapters'] and fixed['chapter_map'] == original['chapter_map']
    assert candidate == original
    assert identity.repair(p, candidate, review, {'issues': [item]}, blocked_indices=set()) == (fixed, audit)
    assert len(p.calls) == 1
    with pytest.raises(ValueError):
        identity.apply(candidate, context, {**p.reply(), 'chapter_replacements': []}, SOURCE)


def test_roadmap_and_nested_references_discover_dependencies_but_id_prefixes_do_not():
    p, candidate = Planner(), book()
    candidate['chapter_map'][2]['ending_obligations'] = ['Preserve char_b’s established uncertainty.']
    candidate['chapters'][0]['scenes'] = [{'contract': {'role_id': 'char_b_variant'}}]
    context = identity.build_context(p, candidate, {'message': 'char_b'},
                                     issue(affected_chapters=[2], writable_chapters=[2]))
    assert context['role_occurrences'] == {'char_b': [2, 3]}
    assert context['scope_discovery']['added_read_only_chapters'] == [3]
    assert context['read_only_chapters'] == [3]


def test_discovered_held_role_is_still_blocked_without_any_model_call():
    p, candidate = Planner(), book()
    candidate['chapters'][2]['scenes'] = [{'contract': {'role_id': 'char_b'}}]
    items = [issue(affected_chapters=[2], writable_chapters=[2]),
             issue(1, affected_chapters=[3], writable_chapters=[], classification='source_conflict')]
    fixed, audit = identity.repair(p, candidate, {'issues': [{'message': 'char_b'}, {'message': 'Resolve a source dispute.'}]},
                                   {'issues': items}, blocked_indices={1})
    assert fixed == candidate and not audit and not p.calls


def test_discovered_chapter_includes_its_full_frozen_source_and_global_qualifiers():
    from core.skeleton_projection import chapter_locations
    from test_planning_repair_context import source

    p, candidate = Planner(), book()
    p.source = source(30)
    candidate['chapters'] = [{'number': n, 'character_ids': ['char_b'] if n in (27, 28, 29) else [],
                              'scenes': [{'detail': f'Entire scene {n}.'}]} for n in range(1, 31)]
    candidate['chapter_map'] = [{'number': n, 'purpose': f'Complete route {n}.'} for n in range(1, 31)]
    context = identity.build_context(p, candidate, {'message': 'char_b'},
        issue(affected_chapters=[27, 28], writable_chapters=[27, 28], source_quotes=['Exact source choice 28:']))
    spans = {tuple(path[1:]): text for path, text in context['source_material']}
    locations = chapter_locations(p.source, 30)
    for number in (27, 28, 29):
        start, end = locations[number]['source_chars']
        assert spans[start, end] == p.source[start:end]
    assert tuple(locations[30]['source_chars']) not in spans
    first = locations[1]['source_chars'][0]
    last = locations[30]['source_chars'][1]
    assert spans[0, first] == p.source[:first]
    assert spans[last, len(p.source)] == p.source[last:]
    assert context['scope_discovery']['added_read_only_chapters'] == [29]


@pytest.mark.parametrize('change', [
    {'character_id': 'char_bad'}, {'field': 'name'}, {'field': 'aliases'}, {'field': 'id'},
    {'before': 'stale'}, {'source_indices': []}, {'source_indices': [False]}, {'source_indices': [2]},
    {'value': None}, {'value': '  he/him'}, {'value': ''},
])
def test_illegal_identity_field_changes_are_rejected_atomically(change):
    p, candidate, reply = Planner(), book(), proposal()
    context = identity.build_context(p, candidate, finding(), issue())
    reply['identity_fields'][0].update(change)
    with pytest.raises(ValueError):
        identity.apply(candidate, context, reply, SOURCE)
    assert candidate == book()


def test_legacy_missing_attribute_uses_exact_absent_value_and_stable_role():
    p, candidate, reply = Planner(), book(), proposal()
    candidate['characters'][1].pop('pronouns')
    reply['identity_fields'][0]['before'] = None
    context = identity.build_context(p, candidate, finding(), issue())
    fixed = identity.apply(candidate, context, reply, SOURCE)
    assert fixed['characters'][1]['pronouns'] == 'he/him'
    assert 'pronouns' not in candidate['characters'][1]


@pytest.mark.parametrize('alter', [
    lambda row: row.update(character_ids=['char_a', 'char_bad']),
    lambda row: row.update(character_ids=['char_a', 'char_a']),
    lambda row: row.update(relation='same_person'),
    lambda row: row.update(relation='distinct'),
    lambda row: row.update(source_evidence=[{'character_id': 'char_a', 'source_index': 0}]),
    lambda row: row['source_evidence'][1].update(source_index=True),
    lambda row: row['source_evidence'][1].update(character_id='char_bad'),
])
def test_role_uncertainty_cannot_merge_roles_or_use_unauthorized_witnesses(alter):
    p, candidate, reply = Planner(), book(), proposal()
    context = identity.build_context(p, candidate, finding(), issue())
    alter(reply['identity_relations'][0])
    with pytest.raises(ValueError):
        identity.apply(candidate, context, reply, SOURCE)
    assert candidate == book()


def test_duplicate_fields_or_pairs_and_fabricated_original_quotes_are_rejected():
    p, candidate = Planner(), book()
    context = identity.build_context(p, candidate, finding(), issue())
    for key in ('identity_fields', 'identity_relations'):
        reply = proposal()
        reply[key].append(deepcopy(reply[key][0]))
        with pytest.raises(ValueError):
            identity.apply(candidate, context, reply, SOURCE)
    context['adopted_passages'][1] = 'This was never stated by the author.'
    with pytest.raises(ValueError, match='exact original'):
        identity.apply(candidate, context, proposal(), SOURCE)


def test_unsupported_or_read_only_findings_make_no_model_calls():
    p, candidate = Planner(), book()
    for item in (issue(classification='interpretation', writable_chapters=[]),
                 issue(classification='source_conflict', writable_chapters=[])):
        fixed, audit = identity.repair(p, candidate, {'issues': [finding()]}, {'issues': [item]}, blocked_indices=set())
        assert fixed == candidate and not audit and not p.calls
