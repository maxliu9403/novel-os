"""Review-only transport changes must round-trip every source and candidate byte."""
from copy import deepcopy
import json

import pytest

from core.planning_context import planning_context, review_context, expand_review_context
from core.skeleton_projection import chapter_source, story_parts
from core.story_design import render
from test_story_first_engine import design  # noqa: F401


@pytest.fixture
def inputs(design):
    session, story, chapters = deepcopy(design)
    session['input']['chapters'] = 4
    story['total_chapters'] = chapters['total_chapters'] = 4
    chapters['chapters'] = [dict(deepcopy(chapters['chapters'][0]), number=n, title=f'Choice {n}')
                            for n in range(1, 5)]
    source = render(session, story, chapters)['prompt_md']
    parts = story_parts(source)
    locked = {'narrative_format': parts['narrative_format'],
              'classification': parts['publication_metadata']}
    context = planning_context(source, {}, locked)
    context.pop('complete_adopted_source')
    context['adopted_source_projection'] = chapter_source(source, [1], 4)
    context['context_representation']['source_contracts_in_immutable_contracts'] = []
    candidate = {**deepcopy(locked), 'title': 'The Choice',
                 'chapter_map': [{'number': n, 'purpose': f'Keep exact choice {n}'} for n in range(1, 5)],
                 'chapters': deepcopy(chapters['chapters'][:2]),
                 'relationships': ['Mara and Iris develop trust through costly choices.']}
    return source, context, candidate


def test_review_contracts_are_explicit_exact_and_reversible(inputs):
    source, context, candidate = inputs
    original = deepcopy({**context, 'candidate': candidate})
    result = review_context(context, candidate, complete_source=source)
    assert set(result['candidate']) == set(candidate) - {'classification', 'narrative_format'}
    assert result['candidate']['chapters'] == candidate['chapters']
    assert result['candidate']['chapter_map'] == candidate['chapter_map']
    projection = result['adopted_source_projection']
    assert '[NARRATIVE_FORMAT_JSON]' not in projection['text']
    assert projection['projected_bytes'] == len(projection['text'].encode())
    assert result['context_representation']['source_contracts_in_immutable_contracts']
    assert result['review_representation']['candidate_contract_fields'] == ['narrative_format', 'classification']
    assert expand_review_context(result, complete_source=source) == original
    assert {**context, 'candidate': candidate} == original
    assert review_context(result, result['candidate'], complete_source=source) == result
    size = lambda value: len(json.dumps(value, ensure_ascii=False).encode())
    assert size(result) < size(original)


@pytest.mark.parametrize('change', ['unknown_source', 'different_source_contract', 'fake_marker', 'wrong_tail'])
def test_unrecognized_or_unequal_source_is_not_stripped(inputs, change):
    source, context, candidate = inputs
    if change == 'unknown_source':
        source = 'An unknown but complete story.\n' + source
    elif change == 'different_source_contract':
        context['immutable_contracts']['classification']['extra'] = 'not the same classification'
    elif change == 'fake_marker':
        source = source.replace('## 1.', '```\n[NARRATIVE_FORMAT_JSON]\n```\n## 1.', 1)
    else:
        context['adopted_source_projection']['text'] += '\nDo not omit this story note.'
    text = context['adopted_source_projection']['text']
    result = review_context(context, candidate, complete_source=source)
    assert result['adopted_source_projection']['text'] == text
    assert expand_review_context(result, complete_source=source) == {**context, 'candidate': candidate}


def test_only_known_equal_candidate_contracts_are_shared(inputs):
    source, context, candidate = inputs
    candidate['classification']['extra'] = 'candidate differs, reviewer must see this'
    candidate['custom_contract'] = context['immutable_contracts']['custom_contract'] = {'same': True}
    result = review_context(context, candidate, complete_source=source)
    assert result['candidate']['classification'] == candidate['classification']
    assert result['candidate']['custom_contract'] == candidate['custom_contract']
    assert 'narrative_format' not in result['candidate']
    assert expand_review_context(result, complete_source=source) == {**context, 'candidate': candidate}


def test_non_review_context_and_inputs_never_change(inputs):
    source, context, candidate = inputs
    full = planning_context(source, {}, context['immutable_contracts'])
    before = deepcopy(full)
    review_context(full, candidate, complete_source=source)
    assert planning_context(source, {}, context['immutable_contracts']) == before == full


def test_roundtrip_rejects_changed_metadata_or_wrong_source(inputs):
    source, context, candidate = inputs
    result = review_context(context, candidate, complete_source=source)
    with pytest.raises(ValueError):
        expand_review_context(result, complete_source=source + '\nChanged.')
    result['immutable_contracts']['classification']['extra'] = 'changed'
    with pytest.raises(ValueError):
        expand_review_context(result, complete_source=source)


def test_fenced_fake_marker_is_story_text_and_is_never_removed(inputs):
    source, context, candidate = inputs
    fake = '```text\n[NARRATIVE_FORMAT_JSON]\nThis is a quoted story prop.\n```\n'
    source = source.replace('## 1.', fake + '## 1.', 1)
    assert story_parts(source) is not None
    context['adopted_source_projection'] = chapter_source(source, [1], 4)
    result = review_context(context, candidate, complete_source=source)
    assert fake in result['adopted_source_projection']['text']
    assert result['adopted_source_projection']['text'].count('[NARRATIVE_FORMAT_JSON]') == 1
    assert expand_review_context(result, complete_source=source) == {**context, 'candidate': candidate}


def test_small_contracts_do_not_grow_the_request():
    context = {'immutable_contracts': {'classification_design_requirements': []}}
    candidate = {'classification_design_requirements': [], 'chapters': []}
    assert review_context(context, candidate, complete_source='Unknown story') == {
        **context, 'candidate': candidate}
