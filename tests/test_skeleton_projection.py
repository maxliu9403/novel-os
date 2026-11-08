import copy

import pytest

from core.skeleton_projection import chapter_locations, chapter_source, RUNTIME_NOTE
from core.reader_investment import snapshot
from api.workshop_assembly import AssemblyPlan
from test_outline_planning import PlanningModel, make_planner
from test_workshop_api import complete_prompt
from workshop_delivery_fixtures import story_design, chapter_plan
from workshop_assembly_fixtures import parts_from_prompt
from api import workshop


def source(count=12):
    return 'Title: Original\n\n' + '\n\n'.join(
        f'## {n}. Section\n\n' + (
            '\n\n'.join(f'### Chapter {c} — Title\n\nExact scene {c}: ' + '限定代价𠀀。' * 80 for c in range(1, count + 1))
            + '\n\n### 完整章节规划补充记录\n\n```json\n{"unknown_global_field":"preserve"}\n```\n'
            if n == 12 else f'Complete global {n}: identity, qualifier, secret, rule, constraint.\n'
        ) for n in range(1, 18)
    )


def test_chapter_projection_preserves_all_globals_and_exact_selected_plans():
    original = source()
    result = chapter_source(original, [5, 6], 12)
    assert result['included_chapters'] == [4, 5, 6, 7]
    assert result['omitted_chapters'] == [1, 2, 3, 8, 9, 10, 11, 12]
    assert result['mode'] == 'chapter_selection'
    assert result['projected_bytes'] < result['source_bytes'] * .6
    for n in range(1, 18):
        if n != 12:
            assert f'Complete global {n}: identity, qualifier, secret, rule, constraint.' in result['text']
    for n in result['included_chapters']:
        assert f'Exact scene {n}: ' + '限定代价𠀀。' * 80 in result['text']
    assert 'unknown_global_field' in result['text']
    assert 'Exact scene 12:' not in result['text']
    assert source() == original


@pytest.mark.parametrize('mutate', [
    lambda s: s.replace('## 8. Section', '## 18. Unrecognized'),
    lambda s: s.replace('### Chapter 12', '### Chapter 11'),
    lambda s: s + '\n```unclosed',
    lambda s: s.replace('## 12. Section', '## Unknown chapter map'),
    lambda s: s + '\n## 18. Private extension\nKEEP ME',
    lambda s: s.replace('## 13. Section', '### Unknown map extension\nKEEP ME\n\n## 13. Section'),
    lambda s: s + '\n## Unnumbered extension\nKEEP ME',
])
def test_unknown_or_ambiguous_source_is_never_lossily_projected(mutate):
    original = mutate(source())
    result = chapter_source(original, [5], 12)
    assert result['mode'] == 'complete'
    assert result['text'] == original


def test_fenced_headings_are_story_data_not_boundaries():
    original = source().replace('Complete global 1:', '```md\n## 12. Pretend\n### Chapter 50\n```\nComplete global 1:')
    result = chapter_source(original, [5], 12)
    assert result['mode'] == 'chapter_selection'
    assert '### Chapter 50' in result['text']
    locations = chapter_locations(original, 12)
    assert sorted(locations) == list(range(1, 13))
    assert 'unknown_global_field' not in original[slice(*locations[12]['source_chars'])]
    assert chapter_locations(original.replace('### Chapter 2 — Title', '### Unexpected'), 12) == {}


def test_empty_source_chapter_cannot_become_a_citation():
    original = source(4)
    a, b = chapter_locations(original, 4)[2]['source_chars']
    empty = original[:a] + '### Chapter 2 — Title\n\n' + original[b:]
    assert chapter_locations(empty, 4) == {}


def test_planning_selects_only_batches_and_old_policy_keeps_full_source(tmp_path):
    original = source()
    policy = snapshot()
    client = PlanningModel()
    planner = make_planner(tmp_path, client, count=12, source=original, reader_investment=policy)
    planner.run()
    batches = [payload for stage, payload in client.calls if stage == 'batch']
    assert batches and all('adopted_source_projection' in p for p in batches)
    assert all('complete_adopted_source' not in p for p in batches)
    assert all(p['complete_adopted_source'] == original for stage, p in client.calls if stage in {'global', 'review'})
    assert len(client.calls) == 5  # 1 global + 3 batches + 1 review; no compression calls
    calls = len(client.calls)
    planner.run()
    assert len(client.calls) == calls
    old = copy.deepcopy(policy)
    old['schema'] = 'reader-investment.v1'
    from core.reader_investment import _digest
    old['sha256'] = _digest({k: v for k, v in old.items() if k != 'sha256'})
    legacy = make_planner(tmp_path / 'old', PlanningModel(), count=12, source=original, reader_investment=old)
    assert legacy._context([5])['complete_adopted_source'] == original


def test_compact_assembly_is_self_contained_and_keeps_saved_story(tmp_path):
    parts = parts_from_prompt(complete_prompt(tmp_path))
    story, chapters = story_design(), chapter_plan()
    plan = AssemblyPlan(story, chapters, parts, compact=True)
    result = plan.assemble()['prompt_md']
    assert result.count(RUNTIME_NOTE) == 1
    assert result.count(story['story_design']['premise']) == 1
    assert all(c['plan'] in result for c in chapters['chapters'])
    assert all(v in result for v in story['story_design'].values())
    assert 'COVER_HANDOFF_BEGIN' in result
    path = tmp_path / 'standalone.md'
    path.write_text(result)
    assert workshop._validate_candidate(path)['chapters'] == 21
    assert RUNTIME_NOTE not in AssemblyPlan(story, chapters, parts).assemble()['prompt_md']
    assert 'Do NOT reproduce generic Agent instructions' in plan.rules('reader_style')
