"""Actual staged coordinator + on-disk restart; only model inference is stubbed."""
from copy import deepcopy
import hashlib
import json

import pytest

from api import workshop
from api.workshop_checkpoints import CheckpointError, load_checkpoint, project_checkpoint, save_checkpoint
from core import llm_client, provider_settings
from test_story_first_engine import design  # noqa: F401
from workshop_delivery_fixtures import chapter_plan as legacy_cards, story_design as legacy_story


def setup_book(design, tmp_path, monkeypatch, *, story_first, count=100):
    session, story, chapters = design
    session.update(id="a" * 32, delivery_run_id="b" * 32)
    if story_first:
        session['creative_design'].update(schema='fixture', policy_sha256='fixture', decisions=[], rounds=[])
        session['creative_design']['policy'].update(guidance={}, delivery_rules='')
        story.update(total_chapters=count)
        chapters.update(total_chapters=count)
        chapters['chapters'] = [{**deepcopy(chapters['chapters'][0]), 'number': n,
                                'core_plot': [f'Decision {n} changes the alliance.'],
                                'title': f'Choice {n}'} for n in range(1, count + 1)]
    else:
        session.pop('creative_design')
        story, chapters = legacy_story(count), legacy_cards(count)
    session['input']['chapters'] = count
    session['messages'] = [{'role': 'user', 'content': 'Mara must not sell the key.'}]
    directory = tmp_path / "checkpoints"
    session['_save_delivery_checkpoint'] = lambda r: save_checkpoint(directory, r['run_id'], r)
    route = {'provider': 'fake', 'model': 'test', 'timeout_seconds': 200, 'base_url': None,
             'api_key': None, 'max_tokens': 2400}
    monkeypatch.setattr(provider_settings, 'resolve_text_route', lambda name: dict(route))
    monkeypatch.setattr(workshop, '_skill_bundle', lambda *a: ('Develop concrete choices.', 'fixture'))
    return session, story, chapters, directory, route


@pytest.mark.parametrize("story_first", [True, False])
def test_100_chapters_resume_and_preserve_exact_source(monkeypatch, tmp_path, design, story_first):
    session, story, chapters, directory, route = setup_book(design, tmp_path, monkeypatch, story_first=story_first)
    if story_first:
        route['planning_input_bytes'] = 16384
    else:
        # Historical delivery that already has a candidate must also use the
        # staged route for a long book; never overwrite the candidate.
        session['_previous_candidate'] = 'Existing candidate with the original ending.'
    ranges, stopped, invalid, looked_up = [], False, False, False

    class Client:
        def __init__(self, **kwargs): pass

        def complete(self, system, user):
            nonlocal stopped, invalid, looked_up
            context = json.loads(user)
            assert context['conversation'] == session['messages']
            if context['delivery_stage'] == 'story_design':
                return json.dumps(story)
            if context['delivery_stage'] == 'assembly':
                raise InterruptedError("legacy six-part assembly reached")
            window = context['chapter_window']
            first, last = window['first'], window['last']
            assert last - first < 4 and context['saved_story_stages']['story_design'] == story
            assert [c['number'] for c in window['prior_index']] == list(range(1, first))
            if first > 1:
                assert window['prior_cards'][-1] == chapters['chapters'][first - 2]
            ranges.append((first, last))
            if first == 5 and not stopped:
                stopped = True
                raise InterruptedError("simulate stopped process")
            if first == 9 and not looked_up:
                looked_up = True
                return json.dumps({'read_chapters': [2]})
            if first == 9:
                assert window['prior_cards'][0] == chapters['chapters'][1]
            data = {**chapters, 'chapters': deepcopy(chapters['chapters'][first - 1:last])}
            if first == 13 and not invalid:
                invalid = True
                data['chapters'][-1] = data['chapters'][0]  # both duplicate and missing
            if first == 13 and 'validation_error' in context:
                assert '修' in context['validation_error'] or '缺章' in context['validation_error']
            return json.dumps(data)

    monkeypatch.setattr(llm_client, 'LLMClient', Client)
    with pytest.raises(InterruptedError, match='stopped process'):
        workshop._complete(session, 'Creative guidance')
    saved = load_checkpoint(directory, session['delivery_run_id'])
    progress = project_checkpoint(saved, 'paused')
    assert progress['chapter_windows'] == {'saved_chapters': 4, 'total_chapters': 100, 'saved_batches': 1}
    assert progress['completed_stages'] == ['story_design']
    first_batch = deepcopy(saved['chapter_windows']['batches'][0])
    # Neither a malformed range nor locally rewriting earlier card text can
    # displace a good prefix, even with a new hash.
    altered = deepcopy(saved)
    altered['chapter_windows']['batches'][0]['chapters'][0]['title'] += 'changed'
    altered['chapter_windows']['batches'][0].pop('sha256')
    with pytest.raises(CheckpointError, match='replaced'):
        save_checkpoint(directory, session['delivery_run_id'], altered)
    session = {**session, '_delivery_record': load_checkpoint(directory, session['delivery_run_id'])}
    if story_first:
        result, _ = workshop._complete(session, 'Creative guidance')
        assert result['ready_for_confirmation'] and 'Decision 100 changes the alliance.' in result['prompt_md']
    else:
        with pytest.raises(InterruptedError, match='six-part assembly'):
            workshop._complete(session, 'Creative guidance')
    final = load_checkpoint(directory, session['delivery_run_id'])
    assert final['completed']['chapter_plan']['content'] == chapters
    assert final['chapter_windows']['batches'][0] == first_batch
    assert ranges.count((1, 4)) == 1 and ranges.count((13, 16)) == 2
    assert invalid and looked_up


def test_unrepresentable_author_brief_never_dispatched(monkeypatch, tmp_path, design):
    session, story, chapters, directory, route = setup_book(design, tmp_path, monkeypatch, story_first=True)
    route['planning_input_bytes'] = 2000
    session['messages'][0]['content'] = '完整作者硬约束' * 500
    monkeypatch.setattr(llm_client.LLMClient, '__init__', lambda self, **kw: None)
    monkeypatch.setattr(llm_client.LLMClient, 'complete', lambda *a: pytest.fail("oversized input dispatched"))
    with pytest.raises(llm_client.LLMInputLimitError, match="请求未发送，内容未截断"):
        workshop._complete(session, 'Creative guidance')
    assert session['messages'][0]['content'] == '完整作者硬约束' * 500


def test_incomplete_json_reduces_output_unit_without_losing_cards(tmp_path, design, monkeypatch):
    session, story, chapters, directory, _ = setup_book(design, tmp_path, monkeypatch, story_first=True, count=9)
    spans = []

    class Client:
        def __init__(self, **kw): pass

        def complete(self, system, user):
            context = json.loads(user)
            if context['delivery_stage'] == 'story_design':
                return json.dumps(story)
            first, last = (context['chapter_window'][k] for k in ('first', 'last'))
            spans.append((first, last))
            if (first, last) == (1, 4):
                return '{"chapters":[{"title":"A costly choice"'
            if first == 1:
                assert 'A costly choice' in context['previous_model_response']
            return json.dumps({**chapters, 'chapters': chapters['chapters'][first - 1:last]})

    monkeypatch.setattr(llm_client, 'LLMClient', Client)
    result, _ = workshop._complete(session, 'Guidance')
    assert spans == [(1, 4), (1, 2), (3, 4), (5, 6), (7, 8), (9, 9)]
    assert result['ready_for_confirmation']
    assert load_checkpoint(directory, session['delivery_run_id'])['completed']['chapter_plan']['content'] == chapters


def test_existing_hundred_chapter_amendment_reads_original_future_scene(tmp_path, design, monkeypatch):
    from core.gemini_gateway import input_bytes
    from core.story_design import render

    session, story, chapters, directory, route = setup_book(design, tmp_path, monkeypatch, story_first=True)
    route['planning_input_bytes'] = 110000
    saved = deepcopy(chapters)
    for card in saved['chapters']:
        n = card['number']
        card['core_plot'] = [f'Original scene {n}. ' + 'Repeated bilingual scene 归还钥匙。' * 120]
        card['ending_hook'] = f'Exact original hook {n}.'
        card['setups_payoffs'] = [f'Full original payoff {n}.']
    distant = 'Mara may open the original locker only when Iris is present.'
    saved['chapters'][82]['core_plot'][0] += distant
    original = render(session, story, saved)['prompt_md']
    assert len(original.encode()) > 3 * route['planning_input_bytes']
    session['prompt_md'] = original
    session['messages'].extend([
        {'role': 'assistant', 'content': 'The earlier alternate ending requires both witnesses.'},
        {'role': 'user', 'content': 'Keep Iris present when the locker is opened.'}])
    seen = set()

    class Client:
        def __init__(self, **kw): pass

        def complete(self, system, user):
            assert input_bytes(system, user) <= route['planning_input_bytes']
            data = json.loads(user)
            if data['delivery_stage'] == 'story_design':
                return json.dumps(story)
            window = data['chapter_window']
            first, last = window['first'], window['last']
            source = data['previous_skeleton']['original_chapters']
            index = data['previous_skeleton']['original_chapter_index']
            assert source['source_sha256'] == hashlib.sha256(original.encode()).hexdigest()
            assert [i['number'] for i in index] == list(range(1, 101))
            assert all(f"Full original payoff {i['number']}." in i['promises_and_hook'] and
                       f"Exact original hook {i['number']}." in i['promises_and_hook'] for i in index)
            assert data['conversation'] == session['messages']
            assert story['story_design']['world_rules'] in source['text']
            assert all(saved['chapters'][n - 1]['core_plot'][0] in source['text'] for n in range(first, last + 1))
            if first == 1 and 83 not in source['included_chapters']:
                return json.dumps({'read_original_chapters': [83]})
            result = deepcopy(chapters['chapters'][first - 1:last])
            if first == 1:
                assert distant in source['text']
                result[0]['core_plot'] = [distant]
            seen.update(range(first, last + 1))
            return json.dumps({**chapters, 'chapters': result})

    monkeypatch.setattr(llm_client, 'LLMClient', Client)
    result, _ = workshop._complete(session, 'Guidance')
    assert seen == set(range(1, 101))
    assert distant in result['prompt_md']
    checkpoint = load_checkpoint(directory, session['delivery_run_id'])
    batches = checkpoint['chapter_windows']['batches']
    assert 83 in batches[0]['source_coverage']['included_chapters']
    assert all(set(c['number'] for c in b['chapters']) <= set(b['source_coverage']['included_chapters']) for b in batches)
    assert session['prompt_md'] == original


def test_original_projection_requires_recognizable_unchanged_numbering(design):
    from api.workshop_chapter_windows import OriginalChapters
    from core.story_design import render

    session, story, chapters = design
    source = render(session, story, chapters)['prompt_md']
    assert OriginalChapters.recognize(source, 2) is not None
    assert OriginalChapters.recognize(source, 3) is None
    assert OriginalChapters.recognize(source.replace('- 核心剧情：', '- Custom chapter structure'), 2) is None


def test_window_preserves_extra_narrative_by_requiring_it_inside_a_card():
    from api.workshop_delivery import parse_stage, DeliveryError

    data = legacy_cards(8)
    data['chapters'] = data['chapters'][:2]
    data['secret_constraint'] = 'A previously undisclosed promise.'
    with pytest.raises(DeliveryError, match='额外故事资料'):
        parse_stage(json.dumps(data), 'chapter_plan', data['narrative_mode'], 8, chapter_range=(1, 2))
