"""Metadata recovery never substitutes story content or guesses source evidence."""
from copy import deepcopy
import hashlib

import pytest

from core import planning_coordinated as contract
from core.planning_reference_recovery import VERSION, recover
from core.planning_repair_context import build_repair_context
from test_planning_coordinated import candidate


CANDIDATE_QUOTE = 'Mara enters the studio.'


def source_quote(number):
    return f'第{number}章的既定事件与行动顺序必须保留。'


def source():
    return 'Title: 已确认骨架\n\n' + '\n\n'.join(
        f'## {section}. 原始章节\n\n' + (
            '\n\n'.join(f'### Chapter {number} · 原定章节\n{source_quote(number)}\n额外限定第{number}项。'
                        for number in range(1, 61))
            if section == 12 else f'保留全局约束第{section}项。')
        for section in range(1, 18))


@pytest.fixture
def data():
    saved = candidate()
    findings = {'status': 'blocked', 'issues': [
        {'scope': 'global', 'message': 'Other issue', 'source_evidence': source_quote(4)},
        {'scope': 'chapter', 'chapter': 2, 'message': 'Clarify the existing transition.',
         'source_evidence': source_quote(2)},
    ]}
    plan = {'issues': [
        {'issue_index': 0, 'classification': 'continuity_gap', 'reason': 'Other issue',
         'requested_chapters': [4], 'dependency_chapters': [], 'source_quotes': [source_quote(4)]},
        {'issue_index': 1, 'classification': 'continuity_gap', 'reason': 'Preserve established events.',
         'requested_chapters': [2], 'dependency_chapters': [1],
         'source_quotes': [source_quote(1), source_quote(2)]},
    ]}
    original_source = source()
    context = build_repair_context(saved, {k: v for k, v in saved.items() if k != 'chapters'},
        findings, [2], source=original_source, brief={}, locked={}, dependency_chapters=[1],
        issues=[findings['issues'][1]], extra_context={'coordinated_issue_plan': plan})
    after = deepcopy(saved['chapters'][1])
    after['summary'] = 'Mara retrieves her card before entering the studio.'
    patch = {'status': 'corrected', 'reason': 'Clarify the existing sequence.',
        'issues': [{'issue_index': 0, 'status': 'corrected', 'reason': 'Retain the original events.',
                    'source_quotes': [CANDIDATE_QUOTE], 'chapter_numbers': [2]}],
        'chapter_replacements': [{'number': 2, 'before_sha256': contract.chapter_hash(saved['chapters'][1]),
                                 'after': after, 'issue_indices': [0], 'source_quote': CANDIDATE_QUOTE}],
        'global_edits': []}
    return {'context': context, 'source': original_source, 'patch': patch, 'candidate': saved}


def test_recognizable_candidate_quote_inherits_exact_plan_evidence_without_chapter_changes(data):
    original = deepcopy(data)
    result, audit = recover(**data)
    assert result['issues'][0]['source_quotes'] == [source_quote(1), source_quote(2)]
    assert result['chapter_replacements'][0]['source_quote'] == source_quote(2)
    assert result['chapter_replacements'][0]['after'] == data['patch']['chapter_replacements'][0]['after']
    assert result['reason'] == data['patch']['reason']
    assert result['issues'][0]['reason'] == data['patch']['issues'][0]['reason']
    assert result['chapter_replacements'][0]['before_sha256'] == data['patch']['chapter_replacements'][0]['before_sha256']
    assert audit['schema'] == VERSION and audit['evidence_origin'] == 'validated_plan'
    assert audit['input_patch_sha256'] == contract.chapter_hash(data['patch'])
    assert audit['output_patch_sha256'] == contract.chapter_hash(result)
    assert audit['source_sha256'] == hashlib.sha256(data['source'].encode()).hexdigest()
    assert audit['model_selected_source_evidence'] is False
    assert audit['semantic_review_required'] is True
    assert [change['path'] for change in audit['changes']] == [
        '/issues/0/source_quotes', '/chapter_replacements/0/source_quote']
    assert all(change['invalid_candidate_quotes'] == [CANDIDATE_QUOTE] for change in audit['changes'])
    assert all(change['original_issue_indices'] == [1] for change in audit['changes'])
    fixed = contract.apply(data['candidate'], data['context']['review'], data['source'], result,
                           requested_chapters=[2])
    assert fixed['chapters'][1] == result['chapter_replacements'][0]['after']
    assert fixed['chapters'][:1] == data['candidate']['chapters'][:1]
    assert fixed['chapters'][2:] == data['candidate']['chapters'][2:]
    assert data == original


def test_global_map_citation_uses_the_original_map_chapter_and_preserves_patch_values(data):
    data['patch']['global_edits'] = [{'path': '/chapter_map/1/incoming_consequence',
        'before': 'The door is locked.', 'after': 'The card was retrieved before facing the door.',
        'issue_indices': [0], 'source_quote': CANDIDATE_QUOTE}]
    result, audit = recover(**data)
    expected = {**data['patch']['global_edits'][0], 'source_quote': source_quote(2)}
    assert result['global_edits'] == [expected]
    assert audit['changes'][-1]['path'] == '/global_edits/0/source_quote'


def test_valid_source_quotations_are_not_rewritten_or_recovered_twice(data):
    result, _ = recover(**data)
    data['patch'] = result
    assert recover(**data) is None


@pytest.mark.parametrize('mutate', [
    lambda d: d['patch']['issues'][0].update(issue_index=1),
    lambda d: d['patch']['chapter_replacements'][0].update(issue_indices=[1]),
    lambda d: d['patch']['issues'][0].update(source_quotes=['A fabricated nonexistent quotation.']),
    lambda d: d['patch']['chapter_replacements'][0].update(source_quote='A fabricated nonexistent quotation.'),
    lambda d: d['patch']['issues'][0].update(source_quotes=[]),
    lambda d: d['patch']['issues'][0].update(source_quotes=[None]),
    lambda d: d['patch']['chapter_replacements'][0].update(source_quote=''),
    lambda d: d['patch']['chapter_replacements'][0].update(before_sha256='stale'),
    lambda d: d['patch']['chapter_replacements'][0]['after'].pop('bespoke_detail'),
    lambda d: d['patch']['chapter_replacements'][0]['after'].update(target_words=1),
    lambda d: d['patch']['chapter_replacements'][0]['after'].update(source_anchor_ids=['invented']),
    lambda d: d['patch']['chapter_replacements'][0].update(number=3),
    lambda d: d['patch']['issues'][0].update(chapter_numbers=[3]),
    lambda d: d['patch'].update(unexpected='A relaxed protocol would accept this.'),
    lambda d: d['patch'].update(status='requires_author'),
    lambda d: d['context']['repair_dependencies'].update(candidate='stale'),
    lambda d: d['context']['repair_dependencies'].update(source_sha256='stale'),
    lambda d: d['context']['candidate_chapter_hashes'].update({'2': 'stale'}),
    lambda d: d['context']['candidate']['chapters'][1].update(summary='An injected candidate quotation.'),
    lambda d: d['context']['candidate'].update(injected_global='An injected candidate quotation.'),
    lambda d: d['context']['repair_scope'].update(original_issue_indices=[0]),
    lambda d: d['context']['repair_scope'].update(original_issue_indices=[True]),
    lambda d: d['context']['coordinated_issue_plan']['issues'][1].update(source_quotes=['Unknown source facts.']),
    lambda d: d['context']['coordinated_issue_plan']['issues'][1].update(classification='source_conflict'),
])
def test_fabrication_structural_errors_stale_bindings_and_index_changes_cannot_be_recovered(data, mutate):
    mutate(data)
    original = deepcopy(data)
    assert recover(**data) is None
    assert data == original


def test_ambiguous_same_chapter_plan_evidence_refuses_to_choose_a_quote(data):
    data['context']['coordinated_issue_plan']['issues'][1]['source_quotes'].append('额外限定第2项。')
    assert recover(**data) is None


def test_no_same_chapter_plan_evidence_refuses_cross_chapter_substitution(data):
    data['context']['coordinated_issue_plan']['issues'][1]['source_quotes'] = [source_quote(1)]
    assert recover(**data) is None


def test_unrecognized_source_layout_cannot_use_a_placeholder_chapter_location(data):
    data['source'] = 'No chapter layout. ' + source_quote(1) + source_quote(2)
    data['context']['repair_dependencies']['source_sha256'] = hashlib.sha256(data['source'].encode()).hexdigest()
    assert recover(**data) is None


def test_quote_only_in_new_proposed_text_is_not_original_candidate_evidence(data):
    new_quote = data['patch']['chapter_replacements'][0]['after']['summary']
    data['patch']['issues'][0]['source_quotes'] = [new_quote]
    data['patch']['chapter_replacements'][0]['source_quote'] = new_quote
    assert recover(**data) is None


def test_another_issue_with_its_own_valid_source_quotes_is_not_rewritten(data):
    data['patch']['issues'][0]['source_quotes'] = [source_quote(2)]
    result, audit = recover(**data)
    assert result['issues'][0]['source_quotes'] == [source_quote(2)]
    assert len(audit['changes']) == 1
    assert audit['changes'][0]['path'] == '/chapter_replacements/0/source_quote'


@pytest.mark.parametrize('field,value', [('context', None), ('candidate', None), ('source', None),
                                        ('patch', None), ('source', '')])
def test_malformed_top_level_inputs_are_not_recoverable(data, field, value):
    data[field] = value
    assert recover(**data) is None
