from copy import deepcopy
from dataclasses import replace

from core.cover_director import CoverArtDirector
from core.cover_models_v2 import CoverBriefV2, CoverScenePlan
from core.cover_prompt_compiler import compile_cover_prompt
from core.cover_validator import validate_direction
from tests.test_cover_director import adaptive_director_fixture, _brief


def story_fixture():
    data = _brief().to_dict()
    data['principal_characters'][0]['narrative_role'] = 'protagonist'
    data['principal_characters'][1]['narrative_role'] = 'antagonist'
    child = deepcopy(data['principal_characters'][0])
    child.update(character_id='char_child', name='Ada', narrative_role='co-protagonist', must_appear=False)
    data['principal_characters'].append(child)
    # Neither antagonist nor co-lead is named in this sentence.
    data['core_conflict'] = 'A mother and daughter protect their home after the father abandons them.'
    brief = CoverBriefV2.from_dict(data, source_prompt_sha256='a' * 64)
    payload = adaptive_director_fixture()
    payload['profile_version'] = 'cover-profiles.v9'
    casts = [('char_mara', 'char_child'), ('char_mara', 'char_oren'), ('char_child', 'char_oren'), ('char_mara',)]
    for i, (plan, cast) in enumerate(zip(payload['plans'], casts)):
        plan['cast'] = list(cast)
        plan['focal_character_id'] = 'char_child' if i == 2 else 'char_mara'
        plan['art_style'] = 'live-action cinematic photography'
        plan['causal_visibility'] = 'direct' if 'char_oren' in cast else 'indirect'
        plan['conflict_character_ids'] = ['char_oren'] if 'char_oren' in cast else []
    return brief, payload


def codes(brief, payload):
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4)
    return {x.code for x in validate_direction(brief, direction)}


def test_v9_contract_finds_pressure_and_colead_without_full_names():
    brief, _ = story_fixture()
    contract = CoverArtDirector._response_contract(brief, 4, profile_version='cover-profiles.v9')
    policy = contract['story_cast_policy']
    assert policy['focal_character_ids'] == ['char_mara', 'char_child']
    assert policy['pressure_character_ids'] == ['char_oren']
    assert policy['minimum_relationship_plans'] == 3
    assert policy['minimum_distinct_cast_sets'] == 2


def test_v9_accepts_relationship_portfolio_and_roundtrips_colead_focal():
    brief, payload = story_fixture()
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4)
    assert validate_direction(brief, direction) == ()
    scene = CoverScenePlan.from_dict(direction.plans[2].to_dict())
    assert scene.story_policy_version == 'cover-story.v1'
    compiled = compile_cover_prompt(brief, scene, visual_identity=direction.visual_identity,
                                   evidence_ledger=direction.evidence_ledger,
                                   conflict_contract=direction.core_conflict_visual_contract)
    assert 'THUMBNAIL STORY READ' in compiled.text
    assert 'Ada' in compiled.modules['CAST LOCK']
    assert 'Keep Mara' not in compiled.modules['CAST LOCK']
    assert 'without reading the title' in compiled.text.casefold()


def test_v9_rejects_solo_portfolio_even_when_pressure_contract_is_emptied():
    brief, payload = story_fixture()
    payload['core_conflict_visual_contract']['pressure_character_ids'] = []
    for plan in payload['plans']:
        plan.update(cast=['char_mara'], focal_character_id='char_mara',
                    causal_visibility='indirect', conflict_character_ids=[])
    assert codes(brief, payload) >= {'conflict_actor_omitted', 'relationship_scene_undercoverage', 'colead_relationship_missing'}


def test_v9_requires_cast_variety_and_actual_human_pressure():
    brief, payload = story_fixture()
    for plan in payload['plans'][:3]:
        plan.update(cast=['char_mara', 'char_child'], focal_character_id='char_mara', conflict_character_ids=[])
    assert codes(brief, payload) >= {'relationship_cast_repeated', 'missing_causal_relationship'}


def test_v9_nonhuman_pressure_does_not_fabricate_people():
    brief, payload = story_fixture()
    brief = replace(brief, principal_characters=(brief.principal_characters[0],), relationship_map=(),
                    core_conflict='A stranded woman must survive a storm.')
    payload['core_conflict_visual_contract']['pressure_character_ids'] = []
    payload['core_conflict_visual_contract']['evidence_refs'] = ['character:char_mara', 'node:door_choice']
    for plan in payload['plans']:
        plan.update(cast=['char_mara'], focal_character_id='char_mara', conflict_character_ids=[],
                    story_evidence_refs=['character:char_mara', 'node:door_choice'])
    assert codes(brief, payload) == set()


def test_v8_retains_single_reader_anchor_contract():
    brief, payload = story_fixture()
    payload['profile_version'] = 'cover-profiles.v8'
    assert 'missing_reader_anchor' in codes(brief, payload)


def test_v9_reads_explicit_pressure_subject_without_treating_every_named_person_as_opponent():
    from core.cover_story_policy import story_cast_policy
    brief, _ = story_fixture()
    neutral = replace(brief.principal_characters[1], narrative_role='spouse')
    brief = replace(brief, principal_characters=(brief.principal_characters[0], neutral, brief.principal_characters[2]),
                    core_conflict=f'{neutral.name} abandons Mara and Ada, who protect their home together.')
    assert story_cast_policy(brief, 4)['pressure_character_ids'] == ['char_oren']
    brief = replace(brief, core_conflict='Mara and Oren protect Ada from a storm.')
    assert story_cast_policy(brief, 4)['pressure_character_ids'] == []


def test_v9_does_not_require_every_opponent_in_one_invented_encounter():
    brief, payload = story_fixture()
    second = replace(brief.principal_characters[1], character_id='char_rival', name='Rival', narrative_role='secondary antagonist', must_appear=False)
    brief = replace(brief, principal_characters=(*brief.principal_characters, second))
    payload['core_conflict_visual_contract']['pressure_character_ids'].append('char_rival')
    plan = payload['plans'][2]
    plan.update(cast=['char_child', 'char_rival'], conflict_character_ids=['char_rival'])
    assert codes(brief, payload) == set()


def test_default_live_director_uses_v9_and_hydrates_policy_after_model_response():
    import json
    brief, payload = story_fixture()
    director = CoverArtDirector(complete=lambda _system, _user: json.dumps(payload), model='fixture')
    direction = director.plan(brief, count=4)
    assert direction.profile_version == 'cover-profiles.v9'
    assert all(plan.story_policy_version == 'cover-story.v1' for plan in direction.plans)
