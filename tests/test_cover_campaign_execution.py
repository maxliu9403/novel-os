"""The image provider must receive the approved executable composition."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from core.cover_prompt_compiler import compile_cover_prompt, compile_direction_recipes
from tests.test_cover_campaign import direction_context


@pytest.mark.parametrize("index", range(4), ids=["scene-a", "composite-a", "scene-b", "composite-b"])
def test_approved_action_eyelines_thumbnail_and_protected_regions_reach_image_recipe(index):
    brief, direction, _ = direction_context()
    scene = direction.plans[index]
    original = deepcopy(scene.to_dict())
    recipe = compile_direction_recipes(brief, direction)[scene.concept_id]
    prompt = recipe["image_prompt"]
    assert scene.frozen_action in prompt
    for gaze in scene.gaze_graph:
        assert gaze in prompt
    assert scene.visual_hook.expected_thumbnail_read in prompt
    assert str(brief.commercial_visual_goal.thumbnail_reference_width) in prompt
    for region in scene.campaign["title_layout"]["safe_zones"]:
        assert str(region) in prompt
    assert "one per layer" in prompt
    assert "not extra simultaneous actions" in prompt
    assert scene.to_dict() == original
    assert recipe["compiler_version"] == "cover-compiler.v17"


def test_long_eyeline_contract_fails_instead_of_silently_disappearing():
    brief, direction, _ = direction_context()
    scene = replace(direction.plans[0], gaze_graph=("Mara looks at Oren. " * 800 + "END OF APPROVED EYELINE",))
    with pytest.raises(ValueError, match="protected story content and visual design"):
        compile_cover_prompt(brief, scene, evidence_ledger=direction.evidence_ledger)


def test_structured_eyeline_preserves_layer_and_temporal_scope():
    brief, direction, _ = direction_context()
    gaze = json.dumps({"from_character_id": "char_mara", "to_character_id": "char_oren",
        "visible_attention": "Looks across the doorway", "layer_id": "moment",
        "temporal_scope": "Only within this event; no eye contact with the separate portrait layer"})
    scene = replace(direction.plans[1], gaze_graph=(gaze,))
    prompt = compile_cover_prompt(brief, scene, evidence_ledger=direction.evidence_ledger).text
    assert gaze in prompt


def test_composite_boundary_and_title_ground_are_executable_without_extra_content():
    brief, direction, _ = direction_context()
    scene = direction.plans[1]
    prompt = compile_direction_recipes(brief, direction)[scene.concept_id]["image_prompt"]
    for link in scene.campaign["layer_relationships"]:
        assert link["truth_boundary"] in prompt
    assert "Do not continue a shared tabletop" in prompt
    assert "Do not draw an additional object" in prompt
    assert "even tonal field across the entire title region" in prompt
    assert "Do not draw boxes" in prompt
    assert brief.title not in prompt  # composed artwork stays title-free


def test_compiling_a_new_recipe_does_not_rewrite_an_old_frozen_recipe():
    brief, direction, _ = direction_context()
    legacy = {"image_prompt": "immutable approved v16 artwork", "compiler_version": "cover-compiler.v16",
              "recipe_sha256": "approved-original"}
    frozen = replace(direction, compiled_recipes={direction.plans[0].concept_id: deepcopy(legacy)})
    current = compile_direction_recipes(brief, frozen)
    assert current[direction.plans[0].concept_id]["compiler_version"] == "cover-compiler.v17"
    assert frozen.compiled_recipes[direction.plans[0].concept_id] == legacy
