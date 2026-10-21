"""Actor direction reaches the renderer without displacing approved story/light."""
from dataclasses import replace
import json

from core.cover_director import CoverArtDirector
from core.cover_prompt_compiler import compile_cover_prompt, compile_repair_prompt
from tests.test_cover_director import _brief, adaptive_director_fixture


def test_actor_direction_is_preserved_in_initial_and_face_repair_prompts():
    brief = _brief()
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(brief, count=4)
    scene = replace(direction.plans[0], design_rationale='Long design explanation. ' * 500)
    context = dict(visual_identity=direction.visual_identity, evidence_ledger=direction.evidence_ledger,
                   conflict_contract=direction.core_conflict_visual_contract)
    original = compile_cover_prompt(brief, scene, **context)
    repaired = compile_repair_prompt(brief, scene, original, ['generic_ai_face', 'weak_story_action'], **context)
    for prompt in (original, repaired):
        performance = prompt.modules['HUMAN PERFORMANCE CONTRACT']
        assert 'one captured instant' in performance
        assert 'scene goal' in performance and 'specific eyeline' in performance
        assert 'weight' in performance and 'contact' in performance
        assert 'forced smiles' in performance
        assert 'focus falloff' in prompt.modules['PHOTOGRAPHIC RENDER CONTRACT']
        assert scene.motivated_lighting in prompt.text
        assert scene.color_script in prompt.text
        assert scene.frozen_action in prompt.text
        assert prompt.text.count(brief.title) == 1
        assert len(prompt.text) <= 12000
    assert 'Repair focus: generic ai face.' in repaired.modules['HUMAN PERFORMANCE CONTRACT']
    assert repaired.modules['TITLE AND SAFE ZONE'] == original.modules['TITLE AND SAFE ZONE']


def test_director_uses_existing_scene_fields_for_natural_actor_direction():
    payload = json.loads(CoverArtDirector._user_prompt(_brief(), 4))
    rules = payload['response_contract']['plans']['field_rules']
    assert 'one primary action' in rules['frozen_action']
    assert 'one visible target' in rules['gaze_graph']
    assert 'weight' in rules['blocking']
    assert 'focus falloff' in rules['depth_plan']
    assert 'not mandatory gestures' in payload['task']
