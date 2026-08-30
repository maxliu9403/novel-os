from __future__ import annotations

import pytest

from core.cover_models_v2 import CoverBriefV2, CoverScenePlan, VisualHook
from core.cover_prompt_compiler import compile_cover_prompt, compile_repair_prompt
from tests.test_cover_models_v2 import two_character_fixture


def brief_fixture(*, pending_required_assumption: bool = False) -> CoverBriefV2:
    payload = two_character_fixture()
    if pending_required_assumption:
        payload["visual_assumptions"] = [{
            "field": "char_mara.age",
            "proposed_value": "mid thirties",
            "reason": "needed for visual continuity",
            "status": "pending_confirmation",
            "critical": True,
        }]
    return CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)


def scene_fixture() -> CoverScenePlan:
    return CoverScenePlan(
        concept_id="concept-relationship-tension",
        visual_strategy="relationship_tension",
        story_evidence_refs=("character:char_mara", "character:char_oren", "node:door_choice"),
        cast=("char_mara", "char_oren"),
        focal_character_id="char_mara",
        moment_before="he asks for the shared key",
        frozen_action="she removes the key while he reaches but stops",
        moment_after="the apartment door will close between them",
        gaze_graph=("char_mara -> key", "char_oren -> char_mara"),
        blocking="she foreground right; he one step behind left; doorway between them",
        environment_anchors=("lived-in apartment entry", "child's backpack"),
        primary_prop="shared brass key",
        shot_scale="medium two-shot",
        camera_height="eye level",
        lens="50mm full-frame equivalent",
        depth_plan="faces and key readable; background recognizably lived in",
        motivated_lighting="warm hall lamp and cool dusk window",
        color_script="warm skin and home light against restrained cool separation",
        title_safe_zone="upper third, no faces or hands",
        visual_hook=VisualHook(
            hook_type="relationship_tension",
            first_glance_subject="two estranged spouses separated by a doorway",
            open_question="Will she close the door before he reaches her?",
            identity_anchor="a partner protecting her home and child",
            genre_signal="contemporary relationship drama",
            reader_promise="the protagonist recognizes the betrayal and acts",
            target_emotion="protective uncertainty",
            misleading_risk="low",
            expected_thumbnail_read="two people and one key at a threshold",
        ),
    )


def test_compile_is_deterministic_and_contains_age_environment_and_hook() -> None:
    first = compile_cover_prompt(brief_fixture(), scene_fixture())
    second = compile_cover_prompt(brief_fixture(), scene_fixture())

    assert first.text == second.text
    assert first.compiler_version == "cover-compiler.v3"
    assert [name for name in first.modules] == [
        "ROLE AND OUTPUT", "STORY TRUTH", "CAST LOCK", "SINGLE CINEMATIC MOMENT",
        "RELATIONSHIP BLOCKING", "LIVED ENVIRONMENT AND PRIMARY PROP", "GENRE EMOTION",
        "CAMERA, DEPTH AND MOTIVATED LIGHTING", "MOBILE COMMERCIAL COVER OBJECTIVE",
        "TITLE AND SAFE ZONE", "TITLE ART DIRECTION", "PHOTOREALISM REQUIREMENTS",
        "COMPACT FAILURE EXCLUSIONS",
    ]
    assert "age 34" in first.text
    assert "late thirties" in first.text
    assert "modest apartment kitchen" in first.text
    assert "the home is actively lived in" in first.modules[
        "LIVED ENVIRONMENT AND PRIMARY PROP"
    ]
    assert "VISUAL HOOK" in first.text
    assert first.text.count('The Door Is Mine') == 1
    assert len(first.text) <= 12000


def test_compile_directs_an_artistic_but_readable_title_lockup() -> None:
    compiled = compile_cover_prompt(brief_fixture(), scene_fixture())
    typography = compiled.modules["TITLE ART DIRECTION"]

    assert "art-directed asymmetric literary title lockup" in typography
    assert "supporting words" in typography
    assert "story-bearing words" in typography
    assert "restrained calligraphic" in typography
    assert "generic Times-like typesetting" in typography
    assert "rigid centered block" in typography
    assert "full-script or cursive title" in typography
    assert compiled.text.count('The Door Is Mine') == 1


def test_compile_keeps_exact_title_once_when_safe_zone_repeats_it() -> None:
    scene = scene_fixture()
    repeated = CoverScenePlan(**{
        **scene.__dict__,
        "title_safe_zone": (
            'clear upper quarter for the exact title “The Door Is Mine,” with simple background'
        ),
    })

    compiled = compile_cover_prompt(brief_fixture(), repeated)

    assert compiled.text.count("The Door Is Mine") == 1
    assert "clear upper quarter" in compiled.modules["TITLE AND SAFE ZONE"]


def test_compile_rejects_unresolved_required_assumption() -> None:
    with pytest.raises(ValueError, match="pending"):
        compile_cover_prompt(brief_fixture(pending_required_assumption=True), scene_fixture())


def test_compile_rejects_unresolved_story_evidence_reference() -> None:
    scene = scene_fixture()
    invalid = CoverScenePlan(**{**scene.__dict__, "story_evidence_refs": ("node:missing",)})

    with pytest.raises(ValueError, match="evidence"):
        compile_cover_prompt(brief_fixture(), invalid)


def test_compile_allows_environment_anchor_that_explicitly_excludes_forbidden_element() -> None:
    payload = two_character_fixture()
    payload["forbidden_elements"] = ["real place names"]
    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)
    scene = scene_fixture()
    constrained = CoverScenePlan(**{
        **scene.__dict__,
        "environment_anchors": (
            "anonymous fictional hospital room",
            "No real place names, hospital branding, or identifiable landmarks",
        ),
    })

    compiled = compile_cover_prompt(brief, constrained)

    assert "No real place names" in compiled.text


def test_compile_rejects_positive_use_of_forbidden_environment_element() -> None:
    payload = two_character_fixture()
    payload["forbidden_elements"] = ["real place names"]
    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)
    scene = scene_fixture()
    invalid = CoverScenePlan(**{
        **scene.__dict__,
        "environment_anchors": ("real place names printed on the wall",),
    })

    with pytest.raises(ValueError, match="forbidden element 'real place names'"):
        compile_cover_prompt(brief, invalid)


def test_repair_compile_changes_only_requested_modules() -> None:
    baseline = compile_cover_prompt(brief_fixture(), scene_fixture())

    repaired = compile_repair_prompt(
        brief_fixture(), scene_fixture(), baseline, ["age_mismatch"],
    )

    assert repaired.revision == baseline.revision + 1
    assert repaired.modules["SINGLE CINEMATIC MOMENT"] == baseline.modules["SINGLE CINEMATIC MOMENT"]
    assert repaired.modules["CAST LOCK"] != baseline.modules["CAST LOCK"]


def test_title_repair_refreshes_safe_zone_and_art_direction() -> None:
    baseline = compile_cover_prompt(brief_fixture(), scene_fixture())

    repaired = compile_repair_prompt(
        brief_fixture(), scene_fixture(), baseline, ["title_failure"],
    )

    assert repaired.modules["TITLE AND SAFE ZONE"] != baseline.modules["TITLE AND SAFE ZONE"]
    assert repaired.modules["TITLE ART DIRECTION"] != baseline.modules["TITLE ART DIRECTION"]
    assert "Repair focus: title failure." in repaired.modules["TITLE ART DIRECTION"]
