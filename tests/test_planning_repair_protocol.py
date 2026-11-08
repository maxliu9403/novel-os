"""Stable repair IDs distinguish candidate allegations from exact source data."""
from copy import deepcopy
import json

import pytest

from core import planning_coordinated as contract
from core.planning_context import expand_review_context
from core.planning_repair_context import build_repair_context
from core.planning_repair_protocol import VERSION, INSTRUCTION, build, decode
from test_planning_repair_context import data  # noqa: F401


@pytest.fixture
def protocol(data):
    plan = {'issues': [{'issue_index': index, 'classification': 'continuity_gap',
        'reason': f'Keep the exact source events for chapter {number}.',
        'requested_chapters': [number], 'dependency_chapters': [],
        'source_quotes': [f'Exact source choice {number}:']}
        for index, number in enumerate((5, 10))]}
    original = build_repair_context(**{**data, 'extra_context': {
        **data['extra_context'], 'coordinated_issue_plan': plan}}, requested_chapters=[5],
        issues=[data['review']['issues'][0]], original_issue_indices=[0])
    return data, plan, original


def response(wire):
    item = wire['review']['issues'][0]
    identifier = item['issue_id']
    evidence = wire['source_evidence_catalog']['issue_bindings'][0]['source_evidence_ids'][0]
    chapter = deepcopy(next(c for c in wire['candidate']['chapters'] if c['number'] == 5))
    chapter['summary'] += ' An existing consequence receives a minimal transition.'
    return {'status': 'corrected', 'reason': 'Preserve the existing source event.',
        'issues': [{'issue_id': identifier, 'status': 'corrected', 'reason': 'The source event remains exact.',
                    'source_evidence_ids': [evidence], 'chapter_numbers': [5]}],
        'chapter_replacements': [{'number': 5, 'before_sha256': wire['candidate_chapter_hashes']['5'],
                                  'after': chapter, 'issue_ids': [identifier], 'source_evidence_id': evidence}],
        'global_edits': []}


def test_wire_contains_only_scoped_stable_issue_ids_and_preserves_all_story_data(protocol):
    data, plan, context = protocol
    original = deepcopy(context)
    wire = build(context, data['source'])
    assert wire['wire_protocol']['schema'] == VERSION
    assert wire['candidate'] == context['candidate']
    assert wire['adopted_source_projection'] == context['adopted_source_projection']
    assert wire['immutable_contracts'] == context['immutable_contracts']
    assert wire['structured_brief'] == context['structured_brief']
    identifier = wire['review']['issues'][0]['issue_id']
    assert identifier.startswith('i_') and len(identifier) == 14
    assert {k: v for k, v in wire['review']['issues'][0].items() if k != 'issue_id'} == context['review']['issues'][0]
    assert len(wire['coordinated_issue_plan']['issues']) == 1
    exposed = wire['coordinated_issue_plan']['issues'][0]
    assert exposed['issue_id'] == identifier
    assert exposed['reason'] == plan['issues'][0]['reason']
    assert wire['repair_scope']['issue_ids'] == [identifier]
    for field in ('issue_index', 'issue_indices', 'original_issue_indices', 'original_issue_index', 'group_issue_index'):
        assert f'"{field}":' not in json.dumps(wire)
    assert plan['issues'][1]['reason'] not in json.dumps(wire)
    catalog = wire['source_evidence_catalog']
    assert len(catalog['entries']) == 1
    assert catalog['entries'][0]['id'].startswith('s_') and len(catalog['entries'][0]['id']) == 14
    assert len(catalog['entries'][0]['sha256']) == 64
    assert catalog['entries'][0]['quote'] == plan['issues'][0]['source_quotes'][0]
    assert catalog['entries'][0]['chapters'] == [5]
    for start, end in catalog['entries'][0]['source_chars']:
        assert data['source'][start:end] == catalog['entries'][0]['quote']
    assert build(context, data['source']) == wire
    wire['candidate']['chapters'][0]['summary'] += 'output-only mutation'
    assert context == original


def test_decoder_expands_ids_into_exact_old_contract_without_chapter_rewriting(protocol):
    data, _, context = protocol
    wire = build(context, data['source'])
    value = response(wire)
    before = deepcopy(value)
    decoded = decode(context, data['source'], value)
    assert decoded['issues'][0]['issue_index'] == 0
    assert decoded['issues'][0]['source_quotes'] == ['Exact source choice 5:']
    assert decoded['chapter_replacements'][0]['issue_indices'] == [0]
    assert decoded['chapter_replacements'][0]['source_quote'] == 'Exact source choice 5:'
    assert decoded['chapter_replacements'][0]['after'] == value['chapter_replacements'][0]['after']
    corrected = contract.apply(data['candidate'], context['review'], data['source'], decoded, requested_chapters=[5])
    assert corrected['chapters'][4] == value['chapter_replacements'][0]['after']
    assert all(corrected['chapters'][n - 1] == data['candidate']['chapters'][n - 1] for n in range(1, 13) if n != 5)
    assert value == before


def test_original_second_issue_decodes_as_group_local_zero_without_exposing_index(protocol):
    data, plan, _ = protocol
    context = build_repair_context(**{**data, 'extra_context': {'coordinated_issue_plan': plan}},
        requested_chapters=[10], issues=[data['review']['issues'][1]], original_issue_indices=[1])
    wire = build(context, data['source'])
    item = wire['review']['issues'][0]
    evidence = wire['source_evidence_catalog']['issue_bindings'][0]['source_evidence_ids'][0]
    value = {'status': 'corrected', 'reason': 'A finding remains for review.',
        'issues': [{'issue_id': item['issue_id'], 'status': 'recheck', 'reason': 'Inspect the source evidence.',
                    'source_evidence_ids': [evidence], 'chapter_numbers': []}],
        'chapter_replacements': [], 'global_edits': []}
    assert decode(context, data['source'], value)['issues'][0]['issue_index'] == 0
    assert item['issue_id'] != build(protocol[2], data['source'])['review']['issues'][0]['issue_id']
    assert wire['coordinated_issue_plan']['issues'][0]['reason'] == plan['issues'][1]['reason']


@pytest.mark.parametrize('mutate', [
    lambda value: value['issues'][0].update(issue_id=0),
    lambda value: value['issues'][0].update(issue_id='unknown issue'),
    lambda value: value['issues'][0].update(issue_index=1),
    lambda value: value['issues'][0].update(source_quotes=['Candidate sentence, not original source.']),
    lambda value: value['issues'][0].update(source_evidence_ids=['fabricated-source']),
    lambda value: value['chapter_replacements'][0].update(issue_indices=[1]),
    lambda value: value['chapter_replacements'][0].update(issue_ids=[True]),
    lambda value: value['chapter_replacements'][0].update(source_evidence_id='Candidate sentence, not original source.'),
    lambda value: value['issues'].clear(),
    lambda value: value['issues'].append(deepcopy(value['issues'][0])),
])
def test_unknown_legacy_or_unscoped_identifiers_are_refused(protocol, mutate):
    data, _, context = protocol
    value = response(build(context, data['source']))
    mutate(value)
    with pytest.raises(ValueError):
        decode(context, data['source'], value)


def test_evidence_from_another_group_cannot_be_used(protocol):
    data, plan, context = protocol
    other = build_repair_context(**{**data, 'extra_context': {'coordinated_issue_plan': plan}},
        requested_chapters=[10], issues=[data['review']['issues'][1]], original_issue_indices=[1])
    evidence = build(other, data['source'])['source_evidence_catalog']['entries'][0]['id']
    value = response(build(context, data['source']))
    value['issues'][0]['source_evidence_ids'] = [evidence]
    with pytest.raises(ValueError, match='outside its referenced issue scope'):
        decode(context, data['source'], value)


def test_evidence_scopes_are_checked_even_when_both_issues_are_in_one_group(protocol):
    data, plan, _ = protocol
    context = build_repair_context(**{**data, 'extra_context': {'coordinated_issue_plan': plan}},
        requested_chapters=[5, 10], issues=data['review']['issues'], original_issue_indices=[0, 1])
    wire = build(context, data['source'])
    first, second = wire['source_evidence_catalog']['issue_bindings']
    value = {'status': 'corrected', 'reason': 'Independent findings need review.',
        'issues': [{'issue_id': item['issue_id'], 'status': 'recheck', 'reason': 'Retain each original source binding.',
                    'source_evidence_ids': item['source_evidence_ids'], 'chapter_numbers': []}
                   for item in (first, second)], 'chapter_replacements': [], 'global_edits': []}
    assert len(decode(context, data['source'], value)['issues']) == 2
    value['issues'][0]['source_evidence_ids'] = second['source_evidence_ids']
    with pytest.raises(ValueError, match='outside its referenced issue scope'):
        decode(context, data['source'], value)


def test_original_duplicate_issue_text_gets_distinct_stable_ids(protocol):
    data, plan, _ = protocol
    data['review']['issues'][1] = deepcopy(data['review']['issues'][0])
    plan['issues'][1] = {**deepcopy(plan['issues'][0]), 'issue_index': 1}
    context = build_repair_context(**{**data, 'extra_context': {'coordinated_issue_plan': plan}},
        requested_chapters=[5], issues=data['review']['issues'], original_issue_indices=[0, 1])
    wire = build(context, data['source'])
    assert len(set(wire['repair_scope']['issue_ids'])) == 2
    assert len(wire['source_evidence_catalog']['entries']) == 1


def test_catalog_never_translates_or_guesses_a_non_source_plan_quote(protocol):
    data, _, context = protocol
    context['coordinated_issue_plan']['issues'][0]['source_quotes'] = ['An English candidate paraphrase.']
    with pytest.raises(ValueError, match='exact adopted-source plan quotations'):
        build(context, data['source'])
    with pytest.raises(ValueError, match='source differs'):
        build(protocol[2], data['source'] + '\nChanged source')


def test_wire_review_representation_roundtrips_after_metadata_changes(protocol):
    from core.planning_context import review_context
    data, _, context = protocol
    context['immutable_contracts']['classification'] = {'id': 'exact-contract-' * 500}
    context['candidate']['classification'] = deepcopy(context['immutable_contracts']['classification'])
    original = review_context(context, context['candidate'], complete_source=data['source'])
    assert 'review_representation' in original
    wire = build(original, data['source'])
    expanded = expand_review_context(wire, complete_source=data['source'])
    assert expanded['candidate']['classification'] == context['immutable_contracts']['classification']
    assert expanded['candidate']['chapters'] == context['candidate']['chapters']
    assert expanded['review']['issues'][0]['issue_id'] == wire['review']['issues'][0]['issue_id']


def test_repeated_source_quote_records_all_exact_locations_without_guessing(protocol):
    data, _, context = protocol
    context['coordinated_issue_plan']['issues'][0]['source_quotes'] = ['限定代价𠀀。']
    catalog = build(context, data['source'])['source_evidence_catalog']
    entry = catalog['entries'][0]
    assert len(entry['source_chars']) == 12 * 80
    assert entry['chapters'] == list(range(1, 13))
    assert all(data['source'][start:end] == entry['quote'] for start, end in entry['source_chars'])


def test_wire_instructions_protect_source_actor_action_and_mechanism():
    assert 'actors, actions and mechanisms' in INSTRUCTION
    assert 'remote hacking/unlocking is not allowed unless' in INSTRUCTION
    assert 'Return IDs, not quotations' in INSTRUCTION


def test_compact_issue_id_collision_refuses_an_ambiguous_request(protocol, monkeypatch):
    from core import planning_repair_protocol
    data, plan, _ = protocol
    context = build_repair_context(**{**data, 'extra_context': {'coordinated_issue_plan': plan}},
        requested_chapters=[5, 10], issues=data['review']['issues'], original_issue_indices=[0, 1])
    monkeypatch.setattr(planning_repair_protocol, '_hash', lambda value: '0' * 64)
    with pytest.raises(ValueError, match='issue identifier collision'):
        build(context, data['source'])


def test_compact_evidence_id_collision_refuses_distinct_quotes(protocol, monkeypatch):
    from core import planning_repair_protocol
    data, _, context = protocol
    context['coordinated_issue_plan']['issues'][0]['source_quotes'].append('Exact source choice 6:')
    monkeypatch.setattr(planning_repair_protocol, '_hash', lambda value: '0' * 64)
    with pytest.raises(ValueError, match='source evidence identifier collision'):
        build(context, data['source'])
