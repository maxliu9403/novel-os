from copy import deepcopy

import pytest

from core.capacity_partition import repartition
from core.story_capacity import contract, validate_contract
from test_story_capacity import capacity_plan


def partition():
    return {'reply': '在人物回应后自然换章，完整保留选择与代价。', 'chapters': [
        {'number': i, 'segments': [{'scene_id': f'scene_{(i + 1) // 2}',
                                  'title': f'自然段落 {i}', 'target_words': 700}]} for i in range(1, 5)]}


def test_split_keeps_original_obligations_and_exact_total_with_short_chapters():
    plan = capacity_plan(2, 1400)
    before = deepcopy(plan)
    result = repartition(plan, 'keep_story', 4, 800, partition())
    assert plan == before
    value = result['plan']
    assert len(value['options'][0]['chapters']) == 4
    assert value['options'][0]['total_words'] == 2800
    assert all(c['target_words'] <= 800 for c in value['options'][0]['chapters'])
    assert [p['source_scene_id'] for p in result['provenance']] == ['scene_1', 'scene_1', 'scene_2', 'scene_2']
    assert all(s['source_quote'] == plan['scenes'][0]['source_quote'] for s in value['scenes'])
    assert all(s['reader_value'] == plan['scenes'][0]['reader_value'] for s in value['scenes'])
    assert value['scenes'][0]['title'] == plan['scenes'][0]['title']
    assert value['scenes'][0]['partition_label'] == '自然段落 1'
    assert validate_contract(contract(value, 'repartitioned', 'a' * 64))


@pytest.mark.parametrize('mutate', [
    lambda p: p['chapters'].pop(),
    lambda p: p['chapters'][0].update(number=2),
    lambda p: p['chapters'][0]['segments'][0].update(scene_id='invented'),
    lambda p: p['chapters'][2]['segments'][0].update(scene_id='scene_1'),
    lambda p: p['chapters'][0]['segments'][0].update(target_words=850),
    lambda p: p['chapters'][0]['segments'][0].update(target_words=True),
    lambda p: p['chapters'][0]['segments'][0].update(target_words=650),
    lambda p: (p['chapters'][0]['segments'][0].update(target_words=600), p['chapters'][2]['segments'][0].update(target_words=800)),
    lambda p: p.update(prompt_md='An unauthorized skeleton'),
])
def test_cannot_hide_missing_reordered_scenes_overlong_chapters_or_changed_totals(mutate):
    raw = partition()
    mutate(raw)
    with pytest.raises(ValueError):
        repartition(capacity_plan(2, 1400), 'keep_story', 4, 800, raw)
