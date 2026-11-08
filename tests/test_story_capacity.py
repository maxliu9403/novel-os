from copy import deepcopy
import json
import pytest

from core.story_capacity import (bind_contract, contract, digest, read_contract,
                                 validate_contract, validate_plan)
from api import workshop_capacity

QUOTE = 'A mother discovers a betrayal and chooses her own future.'


def capacity_plan(count=21, words=550):
    return {'language': 'English', 'count_unit': 'words', 'rationale': 'Give difficult choices and their aftermath space on the page.',
        'scenes': [{'id': f'scene_{i}', 'title': f'Consequential choice {i}', 'source_quote': QUOTE,
                    'presentation': 'on_page', 'reader_value': 'Experience a choice and its consequence.',
                    'minimum': words - 50, 'maximum': words + 50, 'reason': 'An encounter and its immediate emotional aftermath.'} for i in range(1, count + 1)],
        'options': [{'id': 'keep_story', 'title': 'Preserve the complete story', 'tradeoff': 'Longer scenes keep relationship changes visible.',
                    'recommended': True, 'total_words': count * words,
                    'chapters': [{'number': i, 'scene_ids': [f'scene_{i}'], 'target_words': words} for i in range(1, count + 1)]}]}


@pytest.mark.parametrize('mutation', [
    lambda p: p['scenes'][0].update(minimum=700),
    lambda p: p['scenes'][0].update(minimum=True),
    lambda p: p['options'][0].update(total_words=1),
    lambda p: p['options'][0]['chapters'][1].update(number=1),
    lambda p: p['options'][0]['chapters'][1].update(scene_ids=['scene_1']),
    lambda p: p['options'][0]['chapters'][0].update(scene_ids=['absent']),
    lambda p: p['scenes'][0].update(id='scene_2'),
    lambda p: p['options'][0]['chapters'][0].update(target_words=100.5),
    lambda p: p['scenes'][0].update(source_quote='This exact fact was never supplied.'),
    lambda p: p.update(count_unit='characters'),
])
def test_invalid_estimates_cannot_drop_scenes_or_misstate_budgets(mutation):
    value = capacity_plan()
    mutation(value)
    with pytest.raises(ValueError):
        validate_plan(value, QUOTE)


def test_contract_roundtrip_tampering_and_duplicate_blocks():
    value = contract(capacity_plan(), 'keep_story', 'a' * 64)
    source = '# Story\n## 1. Specifications\n\n## 2. Audience\nReaders\n'
    bound = bind_contract(source, value)
    assert read_contract(bound) == value
    assert bind_contract(bound, value) == bound
    bad = deepcopy(value)
    bad['option']['total_words'] += 1
    with pytest.raises(ValueError):
        validate_contract(bad)
    with pytest.raises(ValueError):
        read_contract(bound + bound)
    assert read_contract(source) is None


def test_stage_targets_are_bound_by_code_and_count_is_not_model_owned():
    value = contract(capacity_plan(2), 'keep_story', 'a' * 64)
    session = {'creative_design': {'policy': {'capacity_version': 'story-capacity.v1'}},
               'capacity': {'status': 'confirmed', 'confirmed': value}}
    data = {'narrative_mode': 'short_novel', 'total_chapters': 2,
            'chapters': [{'number': 1, 'plan': 'Retain this full scene.', 'target_words': 1},
                         {'number': 2, 'plan': 'Retain the aftermath.'}]}
    result = workshop_capacity.bind_stage(session, 'chapter_plan', data)
    assert [c['target_words'] for c in result['chapters']] == [550, 550]
    assert result['chapters'][0]['plan'] == 'Retain this full scene.'
    with pytest.raises(ValueError):
        workshop_capacity.bind_stage(session, 'story_design', {'total_chapters': 1})


def test_outline_planner_preserves_author_targets_and_old_path(tmp_path):
    from test_outline_planning import make_planner, PlanningModel, SOURCE
    plan = capacity_plan(4, 1000)
    plan['scenes'][0].update(minimum=800, maximum=1200)
    plan['scenes'][1].update(minimum=800, maximum=1200)
    plan['options'][0]['chapters'][0]['target_words'] = 850
    plan['options'][0]['chapters'][1]['target_words'] = 1150
    value = contract(plan, 'keep_story', digest(SOURCE))
    source = SOURCE + '\n[STORY_CAPACITY_JSON]\n' + json.dumps(value) + '\n[/STORY_CAPACITY_JSON]\n'
    model = PlanningModel()
    planner = make_planner(tmp_path, model, count=4, source=source)
    result = planner.run()
    assert [c['target_words'] for c in result['chapters']] == [850, 1150, 1000, 1000]
    assert all(context['confirmed_story_capacity'] == value for _, context in model.calls)
    replay = PlanningModel()
    assert make_planner(tmp_path, replay, count=4, source=source).run() == result
    assert not replay.calls


@pytest.mark.parametrize('payload', [
    {'story_capacity': {'schema': 'story-capacity.v1'}},
    {'skeleton_review': {'status': 'proposed'}},
    {'unrelated_name': {'schema': 'story-capacity.v1'}},
    {'unrelated_name': {'schema_version': 'skeleton-review.v1'}},
])
def test_private_planning_metadata_cannot_become_public_import_contract(payload):
    from core.h5_acceptance import _public_json, ContractError
    with pytest.raises(ContractError):
        _public_json(payload)
