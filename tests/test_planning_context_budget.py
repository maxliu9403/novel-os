"""Exercise the actual planner -> frozen execution -> model request boundary."""
import copy
import json

import pytest

from core.gemini_gateway import INPUT_LIMIT_BYTES, input_bytes
from core.story_design import render
from core.skeleton_projection import story_parts
from creative_execution import CreativeExecution, encoded, text_hash
from outline_planning import StagedOutlinePlanner
from test_story_first_engine import design  # noqa: F401


class Captured(BaseException):
    pass


def expand_guidance(value):
    result = copy.deepcopy(value)
    shared = result.pop('shared_guidance', {})
    for doc in result.get('documents', []):
        for name, text in doc.get('sections', {}).items():
            for identifier, paragraph in shared.items():
                text = text.replace(f'[Apply shared_guidance.{identifier} here verbatim.]', paragraph)
            doc['sections'][name] = text
    return result


def test_full_story_request_fits_without_changing_frozen_inputs(design, tmp_path):
    session, story, chapters = design
    source = render(session, story, chapters)['prompt_md']
    parts = story_parts(source)
    contracts = {'narrative_format': parts['narrative_format'],
                 'classification': parts['publication_metadata']}
    shared = 'Every choice must retain its cost, and every promise its intended payoff. ' * 12
    guidance = {'documents': [{'id': f'tag.test_{n}', 'sections': {
        '核心承诺': shared + '\n\n' + f'Distinct promise {n}.',
        '故事机制': f'Unique mechanism {n}: uncertainty changes trust.'}}
        for n in range(7)]}
    handoff = {'source_sha256': text_hash(source), 'origin': {}, 'adopted_proposals': [],
               'legacy_guidance': {}, 'stage_contexts': {'story_design': guidance}}
    lock = {'handoff': handoff, 'rules': '', 'system_prompts': {'outline_planner': 'Architect.'}}
    project = tmp_path / 'book'
    inputs = project / 'outputs/input'
    inputs.mkdir(parents=True)
    (inputs / 'prompt.md').write_text(source)
    directory = project / 'outputs/runs/test/creative-design'
    directory.mkdir(parents=True)
    (directory / 'lock.json').write_bytes(encoded(lock))
    runtime = CreativeExecution(project, directory, lock)
    original_lock = (directory / 'lock.json').read_bytes()

    class Model:
        def complete(self, *, system, user):
            # This was rejected before dispatch, regardless of provider output.
            assert input_bytes(system, user) <= INPUT_LIMIT_BYTES
            context = json.JSONDecoder().raw_decode(user[user.index('{"'):])[0]
            assert context['complete_adopted_source'] == source.split('[NARRATIVE_FORMAT_JSON]')[0]
            assert context['immutable_contracts'] == contracts
            assert not set(contracts) & context['structured_brief'].keys()
            creative = json.loads(user.split('[CREATIVE_EXECUTION_CONTEXT]\n')[1].split('\n[/CREATIVE_EXECUTION_CONTEXT]')[0])
            assert expand_guidance(creative['genre_context']) == guidance
            assert 'OUTSIDE_STORY' in context['structured_brief']['premise']
            raise Captured()

    planner = StagedOutlinePlanner(directory=tmp_path / 'planning', source=source,
        brief={**contracts, 'premise': 'OUTSIDE_STORY'}, chapter_count=2, target_words=1600,
        client=Model(), narrative_format=contracts['narrative_format'], commercial_contract=None,
        commercial_contract_id=None, volume_template=[], final_validator=lambda x: x,
        creative_execution=runtime)
    # Reproduce a just-over-budget request without committing a private novel.
    original_context = {'complete_adopted_source': source, 'structured_brief': planner.brief,
                        'immutable_contracts': planner.locked, 'chapter_count': 2, 'target_words': 1600}
    base = planner.global_prompt + '\n\n' + json.dumps(original_context, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    # Compute the OLD wire size from the unprojected references, even after the fix.
    frozen_context = runtime.design_context('story_design')
    frozen_context['genre_context'] = copy.deepcopy(guidance)
    wire = base + '\n\n[CREATIVE_EXECUTION_CONTEXT]\n' + encoded(frozen_context).decode() + '\n[/CREATIVE_EXECUTION_CONTEXT]\n'
    padding = 'p' * (133531 - input_bytes('Architect.', wire))
    planner.brief['premise'] += padding
    with pytest.raises(Captured):
        planner._call('global', planner.global_prompt, planner._context(), lambda x: x)
    assert (directory / 'lock.json').read_bytes() == original_lock
    assert (inputs / 'prompt.md').read_text() == source


def test_unequal_or_unknown_source_metadata_is_not_removed(design):
    from core.planning_context import planning_context
    session, story, chapters = design
    source = render(session, story, chapters)['prompt_md']
    parts = story_parts(source)
    locked = {'narrative_format': parts['narrative_format'], 'classification': parts['publication_metadata']}
    brief = {**copy.deepcopy(locked), 'unknown_story_requirement': 'Keep the lost letter.'}
    for current in (source.replace('## 6.', '## Extra.'), source + '\nKEEP THIS EXTENSION'):
        result = planning_context(current, brief, locked)
        assert result['complete_adopted_source'] == current
        assert result['structured_brief']['unknown_story_requirement'] == 'Keep the lost letter.'
    brief['narrative_format'] = {'different': 'author constraint'}
    result = planning_context(source, brief, locked)
    assert result['structured_brief']['narrative_format'] == brief['narrative_format']
    changed = copy.deepcopy(locked)
    changed['classification']['extra'] = 'a different contract'
    assert planning_context(source, brief, changed)['complete_adopted_source'] == source


def test_guidance_deduplication_is_exact_reversible_and_scoped():
    from core.planning_context import compact_guidance
    paragraph = 'Preserve this shared requirement exactly. ' * 12
    value = {'stage': 'story_design', 'documents': [
        {'id': 'genre.test', 'sections': {'promise': paragraph + '\n\nUNIQUE A'}},
        {'id': 'tag.one', 'sections': {'promise': paragraph + '\n\nUNIQUE B'}},
        {'id': 'tag.different', 'sections': {'promise': paragraph + '!'}},
        {'id': 'tag.freeform', 'markdown': paragraph + '\n\nFree Markdown remains indivisible.'},
    ]}
    original = copy.deepcopy(value)
    projected = compact_guidance(value)
    assert len(encoded(projected)) < len(encoded(value))
    assert expand_guidance(projected) == original == value
    assert projected['documents'][2:] == value['documents'][2:]
    assert compact_guidance(projected) == projected
    assert compact_guidance(None) is None


def test_large_review_covers_every_chapter_and_resumes_saved_windows(tmp_path):
    from test_outline_planning import make_planner, review
    from test_skeleton_projection import source
    from core.reader_investment import snapshot
    candidate = {'title': 'A bounded alliance', 'relationships': ['An uneasy alliance'],
        'chapter_map': [{'number': n, 'purpose': f'Promise {n}'} for n in range(1, 13)],
        'chapters': [{'number': n, 'summary': f'Chapter {n}: ' + '事件与情绪改变。' * 650}
                     for n in range(1, 13)]}
    calls = []
    class Model:
        provider = 'gemini_openai'
        model = 'fixture'
        fail = True
        def complete(self, *, system, user):
            assert input_bytes(system, user) <= INPUT_LIMIT_BYTES
            context = json.JSONDecoder().raw_decode(user[user.index('{"'):])[0]
            calls.append(context)
            if self.fail and len(calls) == 2:
                raise TimeoutError('fixture interruption')
            return json.dumps(review())
    model = Model()
    def planner():
        return make_planner(tmp_path, model, count=12, source=source(), reader_investment=snapshot())
    with pytest.raises(TimeoutError):
        planner()._semantic_review(candidate, 'review')
    first = calls[0]['review_window']['requested_chapters']
    model.fail = False
    result = planner()._semantic_review(candidate, 'review')
    assert result['status'] == 'passed'
    coverage = result['review_scope']['windows']
    assert sorted(n for w in coverage for n in w['requested_chapters']) == list(range(1, 13))
    assert all(any(n in w['included_chapters'] and n+1 in w['included_chapters']
                   for w in coverage) for n in range(1, 12))
    assert sum(c['review_window']['requested_chapters'] == first for c in calls) == 1
    assert 1 < len(coverage) < 12
    for context in calls:
        assert context['candidate']['chapter_map'] == candidate['chapter_map']
        assert context['candidate']['relationships'] == candidate['relationships']
        for chapter in context['candidate']['chapters']:
            assert chapter == candidate['chapters'][chapter['number'] - 1]
    count = len(calls)
    assert planner()._semantic_review(candidate, 'review') == result
    assert len(calls) == count


def test_review_preflight_never_calls_model_if_indivisible_context_does_not_fit(tmp_path):
    from test_outline_planning import make_planner
    from outline_planning import OutlinePlanningError
    class Model:
        provider = 'gemini_openai'
        def complete(self, **_):
            pytest.fail('No model call should occur during capacity planning')
    planner = make_planner(tmp_path, Model(), count=1, source='unrecognized complete source ' * 7000)
    with pytest.raises(OutlinePlanningError, match='no review calls'):
        planner._semantic_review({'chapters': [{'number': 1, 'summary': 'Full chapter'}]}, 'review')


def test_format_correction_keeps_completed_response_and_all_story_facts(tmp_path):
    from test_outline_planning import make_planner
    source = 'Author-approved scene and consequence. ' * 1800
    handoff = {'source_sha256': text_hash(source), 'origin': {}, 'adopted_proposals': ['Keep this decision'],
               'legacy_guidance': {}, 'stage_contexts': {'story_design': {
                   'documents': [{'id': 'tag.test', 'markdown': 'Distinct writing guidance. ' * 1300}]}}}
    lock = {'handoff': handoff, 'rules': '', 'system_prompts': {'outline_planner': 'Architect.'}}
    project = tmp_path / 'book'
    (project / 'outputs/input').mkdir(parents=True)
    (project / 'outputs/input/prompt.md').write_text(source)
    directory = project / 'outputs/runs/test/creative-design'
    directory.mkdir(parents=True)
    (directory / 'lock.json').write_bytes(encoded(lock))
    runtime = CreativeExecution(project, directory, lock)
    completed = json.dumps({'existing_design': 'Already returned design. ' * 1100})
    calls = []
    class Model:
        def complete(self, *, system, user):
            assert input_bytes(system, user) <= INPUT_LIMIT_BYTES
            calls.append(user)
            if len(calls) == 1:
                return completed
            context = json.JSONDecoder().raw_decode(user[user.index('{"'):])[0]
            assert context['original_context']['complete_adopted_source'] == source
            assert context['completed_response'] == completed
            assert 'Keep this decision' in user
            assert 'Distinct writing guidance.' not in user
            return '{"ok":true}'
    planner = make_planner(tmp_path, Model(), count=2, source=source, creative_execution=runtime)
    def validate(value):
        if value.get('ok') is not True:
            raise ValueError('missing ok')
        return value
    assert planner._call('global', planner.global_prompt, planner._context(), validate) == {'ok': True}
    assert len(calls) == 2


def test_window_review_does_not_turn_a_blocker_into_a_pass(tmp_path, monkeypatch):
    from test_outline_planning import make_planner, review, issue
    planner = make_planner(tmp_path, object(), count=2)
    candidate = {'chapters': [{'number': n, 'summary': f'Complete chapter {n}'} for n in (1, 2)]}
    monkeypatch.setattr(planner, '_review_fits', lambda instruction, context: 'review_window' in context)
    def blocked_review(name, instruction, context, validate, *, strict_dependencies=False):
        assert strict_dependencies is False  # Ordinary initial review keeps its existing cache policy.
        return validate(review(issue(number=2)))
    monkeypatch.setattr(planner, '_call', blocked_review)
    result = planner._semantic_review(candidate, 'review')
    assert result['status'] == 'blocked'
    assert result['issues'] == [issue(number=2)]
