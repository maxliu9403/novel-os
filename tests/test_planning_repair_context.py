"""Repair projections preserve selected data, bindings and actual wire limits."""
from copy import deepcopy
import hashlib
import json

import pytest

from core.gemini_gateway import input_bytes
from core.planning_repair_context import build_plan_context, build_repair_context, repair_contexts
from core.skeleton_projection import chapter_locations
from test_story_first_engine import design  # noqa: F401


def source(count=12):
    return 'Title: Frozen original\n\n' + '\n\n'.join(
        f'## {n}. Section\n\n' + (
            '\n\n'.join(f'### Chapter {c} — Original\n\nExact source choice {c}: '
                        + '限定代价𠀀。' * 80 for c in range(1, count + 1))
            + '\n\n### 完整章节规划补充记录\n\nKeep this unknown global extension.\n'
            if n == 12 else f'Complete global section {n}: exact identity and obligation.\n'
        ) for n in range(1, 18))


@pytest.fixture
def data():
    candidate = {
        'title': 'Frozen title', 'relationships': [{'history': 'Keep every exact qualifier.'}],
        'chapter_map': [{'number': n, 'purpose': f'Promise {n}',
                         'outgoing_commitment': f'Later obligation {n}'} for n in range(1, 13)],
        'unknown_global': {'keep': ['All original values survive.']},
        'chapters': [{'number': n, 'summary': f'Complete chapter {n}: ' + '人物选择。' * 100,
                      'scenes': [{'detail': f'Full scene {n}'}], 'custom_field': ['exact', n]}
                     for n in range(1, 13)],
    }
    issues = [{'scope': 'chapter', 'chapter': n, 'message': f'Keep exact issue {n}.',
               'source_evidence': {'quote': f'Exact source choice {n}:',
                                   'unknown': 'No evidence fields are dropped.'}}
              for n in (5, 10)]
    review = {'status': 'blocked', 'issues': issues, 'coverage': {'all': True},
              'review_scope': {'windows': [{'included_chapters': list(range(1, 13)),
                                            'unused': 'HISTORICAL WINDOW' * 10000}]}}
    return {'candidate': candidate,
            'frozen_global_design': {k: deepcopy(v) for k, v in candidate.items() if k != 'chapters'},
            'review': review, 'source': source(), 'brief': {'author': 'Keep her refusal.'},
            'locked': {}, 'extra_context': {'target_words': 12000}}


def build(data, **kwargs):
    return build_repair_context(**data, requested_chapters=[5], **kwargs)


def test_included_chapters_global_source_and_exact_issue_evidence_are_lossless(data):
    original = deepcopy(data)
    context = build(data, dependency_chapters=[9], issues=[data['review']['issues'][0]])
    scope = context['repair_scope']
    assert scope['included_chapters'] == [4, 5, 6, 9]
    assert scope['readonly_chapters'] == [4, 6, 9]
    assert scope['omitted_candidate_chapters'] == [1, 2, 3, 7, 8, 10, 11, 12]
    assert scope['included_source_chapters'] == scope['included_chapters']
    assert scope['omitted_source_chapters'] == scope['omitted_candidate_chapters']
    assert {k: v for k, v in context['candidate'].items() if k not in {'chapters', 'chapter_map'}} == {
        k: v for k, v in data['frozen_global_design'].items() if k != 'chapter_map'}
    assert context['candidate'] == {**data['frozen_global_design'],
        'chapter_map': [data['candidate']['chapter_map'][n - 1] for n in scope['included_chapters']],
        'chapters': [data['candidate']['chapters'][n - 1]
                                                for n in scope['included_chapters']]}
    assert scope['included_chapter_map_entries'] == scope['included_chapters']
    assert scope['omitted_chapter_map_entries'] == scope['omitted_candidate_chapters']
    assert context['review']['issues'] == [data['review']['issues'][0]]
    assert context['review']['coverage'] == data['review']['coverage']
    assert 'review_scope' not in context['review']
    assert 'HISTORICAL WINDOW' not in json.dumps(context)
    assert scope['original_issue_indices'] == [0]
    projection = context['adopted_source_projection']
    for n, location in chapter_locations(data['source'], 12).items():
        exact_body = data['source'][slice(*location['source_chars'])]
        assert (exact_body in projection['text']) == (n in scope['included_source_chapters'])
    for n in range(1, 18):
        if n != 12:
            assert f'Complete global section {n}: exact identity and obligation.' in projection['text']
    assert 'Keep this unknown global extension.' in projection['text']
    assert context['repair_dependencies']['source_sha256'] == hashlib.sha256(data['source'].encode()).hexdigest()
    assert set(context['candidate_chapter_hashes']) == {'5'}
    assert context['allowed_global_fields'] == {
        '/chapter_map/4/purpose': 'Promise 5', '/chapter_map/4/outgoing_commitment': 'Later obligation 5'}
    # Returned nested values never alias frozen inputs.
    context['candidate']['unknown_global']['keep'].append('output-only change')
    context['candidate']['chapters'][0]['custom_field'].append('output-only change')
    context['review']['issues'][0]['source_evidence']['unknown'] = 'output-only change'
    assert data == original


def test_plan_has_complete_source_global_and_issues_but_no_candidate_bodies(data):
    kwargs = {k: v for k, v in data.items() if k != 'frozen_global_design'}
    original = deepcopy(kwargs)
    context = build_plan_context(**kwargs)
    assert context['candidate'] == {**{k: v for k, v in data['frozen_global_design'].items()
                                       if k != 'chapter_map'}, 'chapters': []}
    assert context['complete_adopted_source'] == data['source']
    assert context['review']['issues'] == data['review']['issues']
    assert 'review_scope' not in context['review']
    scope = context['repair_scope']
    assert scope['included_chapters'] == scope['omitted_source_chapters'] == []
    assert scope['omitted_candidate_chapters'] == scope['included_source_chapters'] == list(range(1, 13))
    assert scope['omitted_global_candidate_fields'] == ['chapter_map']
    assert scope['included_chapter_map_entries'] == []
    assert scope['omitted_chapter_map_entries'] == list(range(1, 13))
    assert context['chapter_count'] == 12 and context['target_words'] == 12000
    assert kwargs == original


def test_unknown_source_is_retained_completely_and_coverage_discloses_it(data):
    data['source'] += '\n## Private unknown extension\nDo not discard this author requirement.'
    context = build(data)
    assert context['adopted_source_projection']['mode'] == 'complete'
    assert context['adopted_source_projection']['text'] == data['source']
    assert context['repair_scope']['included_source_chapters'] == list(range(1, 13))
    assert context['repair_scope']['omitted_source_chapters'] == []
    assert context['repair_scope']['included_chapters'] == [4, 5, 6]


@pytest.mark.parametrize('change, message', [
    (lambda d: d['candidate']['chapters'].reverse(), 'complete ordered chapters'),
    (lambda d: d['candidate']['chapters'][0].update(number=True), 'complete ordered chapters'),
    (lambda d: d['frozen_global_design'].update(title='Changed'), 'differs'),
    (lambda d: d.update(source=''), 'complete frozen source'),
    (lambda d: d['extra_context'].update(candidate={}), 'cannot replace'),
    (lambda d: d['extra_context'].update(chapter_count=13), 'chapter count differs'),
    (lambda d: d['review'].update(status='passed'), 'blocked review'),
])
def test_invalid_frozen_inputs_fail_before_capacity_callback(data, change, message):
    change(data)
    with pytest.raises(ValueError, match=message):
        build(data)


@pytest.mark.parametrize('kwargs', [
    {'dependency_chapters': [5]}, {'dependency_chapters': [13]},
    {'dependency_chapters': [True]}, {'dependency_chapters': [6, 6]},
    {'issues': []}, {'issues': [{'scope': 'chapter', 'chapter': 5, 'message': 'changed'}]},
])
def test_invalid_group_cannot_change_frozen_evidence_or_dependency_scope(data, kwargs):
    with pytest.raises(ValueError):
        build(data, **kwargs)


def groups(data):
    return [{'requested_chapters': [n], 'dependency_chapters': [], 'issues': [issue]}
            for n, issue in zip((5, 10), data['review']['issues'])]


def test_every_group_preflights_full_creative_wire_before_any_dispatch(data):
    checked, dispatched = [], []
    instruction, system = 'STAGE: coordinated repair\n', 'Frozen architect system.'
    creative_injection = '\n[CREATIVE_EXECUTION_CONTEXT]\n' + '保留完整作者要求。' * 200

    def fits(context):
        checked.append(deepcopy(context))
        wire = instruction + json.dumps(context, ensure_ascii=False) + creative_injection
        return input_bytes(system, wire) <= 156000

    contexts = repair_contexts(groups(data), fits=fits, **data)
    assert len(checked) == len(contexts) == 2
    for context in contexts:
        assert len(checked) == 2  # The first model call follows all preflights.
        dispatched.append(context)
    assert len(dispatched) == 2
    assert contexts[0]['repair_scope']['original_issue_indices'] == [0]
    assert contexts[1]['repair_scope']['original_issue_indices'] == [1]


def test_one_oversize_group_refuses_entire_sequence_without_splitting_or_dispatch(data):
    checked, dispatched = [], []

    def fits(context):
        checked.append(context)
        return context['requested_chapters'] == [5]

    with pytest.raises(ValueError, match='no group was split and no repair calls'):
        contexts = repair_contexts(groups(data), fits=fits, **data)
        for context in contexts:
            dispatched.append(context)
    assert [c['requested_chapters'] for c in checked] == [[5], [10]]
    assert not dispatched


def test_single_group_overflow_includes_system_and_full_creative_injection(data):
    checked = []
    system = 'The frozen system and author priorities. ' * 1000
    creative = '[CREATIVE_EXECUTION_CONTEXT]\n' + '原始完整创作指导。' * 6000

    def fits(context):
        payload = 'STAGE: coordinated_repair\n\n' + json.dumps(context, ensure_ascii=False)
        assert input_bytes('', payload) < 156000
        checked.append(input_bytes(system, payload + creative))
        return checked[-1] <= 156000

    with pytest.raises(ValueError, match='Repair group 1.*no repair calls'):
        repair_contexts(groups(data)[:1], fits=fits, **data)
    assert len(checked) == 1 and checked[0] > 156000


def test_capacity_callback_mutation_cannot_mutate_returned_frozen_view(data):
    def fits(context):
        context['candidate']['chapters'].clear()
        return True
    contexts = repair_contexts(groups(data), fits=fits, **data)
    assert all(context['candidate']['chapters'] for context in contexts)
    assert len(data['candidate']['chapters']) == 12


def test_equal_global_metadata_and_terminal_source_contracts_roundtrip_exactly(design):
    from core.planning_context import expand_review_context
    from core.skeleton_projection import chapter_source, story_parts
    from core.story_design import render

    session, story, chapters = deepcopy(design)
    session['input']['chapters'] = story['total_chapters'] = chapters['total_chapters'] = 4
    chapters['chapters'] = [dict(deepcopy(chapters['chapters'][0]), number=n, title=f'Choice {n}')
                            for n in range(1, 5)]
    original_source = render(session, story, chapters)['prompt_md']
    parts = story_parts(original_source)
    locked = {'narrative_format': parts['narrative_format'],
              'classification': parts['publication_metadata']}
    global_design = {**deepcopy(locked), 'title': 'Keep global title',
                     'characters': [{'name': 'Unchanged character'}],
                     'chapter_map': [{'number': n, 'purpose': f'Promise {n}'} for n in range(1, 5)]}
    candidate = {**global_design, 'chapters': chapters['chapters']}
    review = {'status': 'blocked', 'issues': [{'scope': 'chapter', 'chapter': 1,
               'message': 'Preserve exact source.', 'source_evidence': 'An exact source quote.'}]}
    context = build_repair_context(candidate, global_design, review, [1], source=original_source,
                                   brief=deepcopy(locked), locked=locked)
    assert 'narrative_format' not in context['candidate']
    assert 'classification' not in context['candidate']
    expanded = expand_review_context(context, complete_source=original_source)
    assert expanded['candidate'] == {**global_design,
        'chapter_map': global_design['chapter_map'][:2], 'chapters': chapters['chapters'][:2]}
    assert expanded['adopted_source_projection']['text'] == chapter_source(original_source, [1], 4)['text']
    assert expanded['candidate']['chapters'] == candidate['chapters'][:2]
    assert candidate['classification'] == locked['classification']


def test_candidate_binding_changes_for_an_omitted_chapter_without_sending_its_body(data):
    before = build(data)
    data['candidate']['chapters'][11]['custom_field'].append('new omitted detail')
    after = build(data)
    assert before['candidate'] == after['candidate']
    assert before['repair_dependencies']['candidate'] != after['repair_dependencies']['candidate']
    assert 'new omitted detail' not in json.dumps(after)


def test_unsorted_complete_map_retains_original_entry_order_and_patch_paths(data):
    data['candidate']['chapter_map'].reverse()
    data['frozen_global_design']['chapter_map'].reverse()
    context = build(data)
    assert [entry['number'] for entry in context['candidate']['chapter_map']] == [6, 5, 4]
    assert context['allowed_global_fields'] == {
        '/chapter_map/7/purpose': 'Promise 5', '/chapter_map/7/outgoing_commitment': 'Later obligation 5'}
    assert context['repair_scope']['included_chapter_map_entries'] == [4, 5, 6]


@pytest.mark.parametrize('numbers', [[1] * 12, list(range(2, 14)), [True] + list(range(2, 13))])
def test_ambiguous_or_incomplete_chapter_map_is_refused(data, numbers):
    for entry, number in zip(data['candidate']['chapter_map'], numbers):
        entry['number'] = number
    with pytest.raises(ValueError, match='complete unique chapter map'):
        build(data)


def test_identical_original_findings_keep_explicit_distinct_indices_across_groups(data):
    issue = data['review']['issues'][0]
    data['review']['issues'] = [deepcopy(issue), deepcopy(issue)]
    duplicated_groups = [{'requested_chapters': [number], 'dependency_chapters': [],
                          'issues': [deepcopy(issue)], 'original_issue_indices': [index]}
                         for index, number in enumerate((5, 10))]
    contexts = repair_contexts(duplicated_groups, fits=lambda context: True, **data)
    assert [context['repair_scope']['original_issue_indices'] for context in contexts] == [[0], [1]]
    assert contexts[0]['review']['issues'] == contexts[1]['review']['issues']
    assert build(data)['repair_scope']['original_issue_indices'] == [0, 1]
    with pytest.raises(ValueError, match='ambiguous'):
        build(data, issues=[issue])


@pytest.mark.parametrize('indices', [[1], [True], [12], [], [0, 0]])
def test_original_issue_indices_must_match_exact_supplied_evidence(data, indices):
    with pytest.raises(ValueError, match='Original issue indices'):
        build(data, issues=[data['review']['issues'][0]], original_issue_indices=indices)
