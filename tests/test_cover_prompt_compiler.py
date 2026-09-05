from __future__ import annotations

from dataclasses import replace

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


def brief_with_optional_conflict_cast() -> CoverBriefV2:
    payload = two_character_fixture()
    payload["principal_characters"].extend([
        {
            "character_id": "char_pressure_a",
            "name": "Pressure A",
            "narrative_role": "cover-relevant opposing force",
            "must_appear": False,
            "age": 43,
            "age_band": "",
            "gender_presentation": "person",
            "physical_identity": "natural age-appropriate features",
            "occupation_and_status": "ordinary working adult",
            "daily_wardrobe": "credible repeated-use day clothing",
            "lived_environment": "the approved story environment",
            "current_emotional_state": "invested in the opposing choice",
            "agency_signal": "participates in the action causing the visible rupture",
            "relationships": ["char_mara", "char_pressure_b"],
        },
        {
            "character_id": "char_pressure_b",
            "name": "Pressure B",
            "narrative_role": "cover-relevant relationship catalyst",
            "must_appear": False,
            "age": 41,
            "age_band": "",
            "gender_presentation": "person",
            "physical_identity": "natural age-appropriate features",
            "occupation_and_status": "ordinary working adult",
            "daily_wardrobe": "credible repeated-use day clothing",
            "lived_environment": "the approved story environment",
            "current_emotional_state": "aligned with the opposing choice",
            "agency_signal": "makes the relationship shift visible through story-supported behavior",
            "relationships": ["char_pressure_a"],
        },
    ])
    return CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)


def layered_conflict_scene() -> CoverScenePlan:
    return replace(
        scene_fixture(),
        cast=("char_mara", "char_oren", "char_pressure_a", "char_pressure_b"),
        story_evidence_refs=(
            "character:char_mara",
            "character:char_oren",
            "character:char_pressure_a",
            "character:char_pressure_b",
            "node:door_choice",
        ),
        gaze_graph=(
            "char_mara watches char_oren choose the opposing relationship",
            "char_oren and char_pressure_a direct their attention toward each other",
            "char_pressure_b notices char_mara's departure",
        ),
        blocking=(
            "char_mara and char_oren carry the emotional consequence in the foreground; "
            "char_pressure_a and char_pressure_b reveal the causing relationship action in the background"
        ),
        shot_scale="readable four-person ensemble",
        depth_plan=(
            "foreground reaction and background relationship cause remain simultaneously legible"
        ),
    )


def test_compile_is_deterministic_and_contains_age_environment_and_hook() -> None:
    first = compile_cover_prompt(brief_fixture(), scene_fixture())
    second = compile_cover_prompt(brief_fixture(), scene_fixture())

    assert first.text == second.text
    assert first.compiler_version == "cover-compiler.v7"
    assert [name for name in first.modules] == [
        "ROLE AND OUTPUT", "STORY TRUTH", "CAST LOCK", "SINGLE CINEMATIC MOMENT",
        "HERO SUBJECT AND CORE STORY ATMOSPHERE",
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY", "RELATIONSHIP BLOCKING",
        "LIVED ENVIRONMENT AND PRIMARY PROP", "GENRE EMOTION",
        "CAMERA, DEPTH AND MOTIVATED LIGHTING", "MOBILE COMMERCIAL COVER OBJECTIVE",
        "TITLE AND SAFE ZONE", "TITLE ART DIRECTION", "PHOTOREALISM REQUIREMENTS",
        "COMPACT FAILURE EXCLUSIONS",
    ]
    assert "age 34" in first.text
    assert "late thirties" in first.text
    assert "lived-in apartment entry" in first.text
    assert "the home is actively lived in" in first.modules[
        "LIVED ENVIRONMENT AND PRIMARY PROP"
    ]
    assert "VISUAL HOOK" in first.text
    assert first.text.count('The Door Is Mine') == 1
    assert len(first.text) <= 12000


def test_compile_directs_a_centered_cinematic_but_readable_title_lockup() -> None:
    compiled = compile_cover_prompt(brief_fixture(), scene_fixture())
    typography = compiled.modules["TITLE ART DIRECTION"]

    assert "cinematic centered title lockup" in typography
    assert "horizontal center axis" in typography
    assert "optical centering" in typography
    assert "supporting words" in typography
    assert "story-bearing words" in typography
    assert "fractured-axis" in typography
    assert "fractured_editorial_serif" in typography
    assert "generic Times-like typesetting" in typography
    assert "equal-size line stack" in typography
    assert "full-script or cursive title" in typography
    assert compiled.text.count('The Door Is Mine') == 1


def test_compile_uses_film_publicity_key_art_and_clear_story_bearing_people() -> None:
    compiled = compile_cover_prompt(brief_fixture(), scene_fixture())

    role = compiled.modules["ROLE AND OUTPUT"]
    subject = compiled.modules["HERO SUBJECT AND CORE STORY ATMOSPHERE"]
    camera = compiled.modules["CAMERA, DEPTH AND MOTIVATED LIGHTING"]

    assert "live-action theatrical film campaign poster" in role
    assert "believable on-set publicity still" in role
    assert "faces unobstructed, recognizable, and sharply resolved" in subject
    assert "core conflict" in subject
    assert "expression, body distance, unfinished action, motivated light" in subject
    assert "principal people read before the setting" in subject
    assert "deep_focus_prestige_drama" in camera


def test_compile_turns_optional_approved_cast_into_layered_conflict_not_a_group_pose() -> None:
    compiled = compile_cover_prompt(
        brief_with_optional_conflict_cast(), layered_conflict_scene(),
    )

    cast_lock = compiled.modules["CAST LOCK"]
    conflict = compiled.modules["CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY"]
    assert "char_pressure_a" in cast_lock
    assert "char_pressure_b" in cast_lock
    assert "triangular_depth_tableau" in conflict
    assert "emotional consequence and the causing alignment" in conflict
    assert "causal_ensemble" in conflict
    assert "generic sad portrait" in conflict
    assert "equal-weight group portrait" in conflict
    assert "approved evidence" in conflict


def test_four_hooks_compile_to_four_different_compositions_art_emotions_and_typography() -> None:
    hook_types = (
        "emotional_identification",
        "relationship_tension",
        "evidence_reveal",
        "irreversible_moment",
    )
    compiled = [
        compile_cover_prompt(
            brief_fixture(),
            replace(
                scene_fixture(),
                visual_hook=replace(scene_fixture().visual_hook, hook_type=hook_type),
            ),
        )
        for hook_type in hook_types
    ]

    assert len({item.modules["CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY"] for item in compiled}) == 4
    assert len({item.modules["CAMERA, DEPTH AND MOTIVATED LIGHTING"] for item in compiled}) == 4
    assert len({item.modules["GENRE EMOTION"] for item in compiled}) == 4
    assert len({item.modules["TITLE ART DIRECTION"] for item in compiled}) == 4
    assert [
        token in item.text
        for token, item in zip(
            (
                "asymmetric_close_plane",
                "triangular_depth_tableau",
                "evidence_led_negative_space",
                "diagonal_threshold_motion",
            ),
            compiled,
            strict=True,
        )
    ] == [True, True, True, True]
    assert "private recognition or immediate aftermath" in compiled[0].modules["GENRE EMOTION"]
    assert "private recognition or immediate aftermath" not in compiled[1].modules["GENRE EMOTION"]
    assert "causal_ensemble" in compiled[1].modules["CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY"]


def test_compiler_uses_each_plan_location_instead_of_repeating_first_brief_space() -> None:
    payload = two_character_fixture()
    payload["lived_environment"]["primary_spaces"] = [
        "lived-in apartment entry", "ordinary clinic corridor",
    ]
    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)
    scene = replace(scene_fixture(), location_family="ordinary clinic corridor")

    compiled = compile_cover_prompt(brief, scene)

    assert "Primary lived setting for this portfolio slot: ordinary clinic corridor" in compiled.modules[
        "LIVED ENVIRONMENT AND PRIMARY PROP"
    ]


def test_compile_compacts_verbose_four_person_direction_below_provider_limit() -> None:
    payload = brief_with_optional_conflict_cast().to_dict()
    for character in payload["principal_characters"]:
        character["physical_identity"] = (
            "mature natural features, fine expression lines, clear observant eyes, "
            "understated makeup, realistic skin texture, and age-appropriate posture"
        )
        character["occupation_and_status"] = (
            "ordinary working adult whose family role creates visible pressure in this decisive encounter"
        )
        character["daily_wardrobe"] = (
            "practical repeated-use winter layers over ordinary work clothing with realistic fabric wear"
        )
        character["lived_environment"] = (
            "ordinary shared home, regional workplace, community relationships, medical appointments, "
            "and practical caregiving routes"
        )
        character["current_emotional_state"] = (
            "controlled shock and divided loyalty becoming a deliberate visible choice under pressure"
        )
        character["agency_signal"] = (
            "uses gaze, hand placement, body direction, and interrupted movement to reveal the choice"
        )
    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)
    scene = replace(
        layered_conflict_scene(),
        moment_before=(
            "The heroine recognizes the three-person relationship group across an ordinary clinic corridor."
        ),
        frozen_action=(
            "She walks toward the exit protecting a private envelope as one background figure turns "
            "but remains physically aligned with the others."
        ),
        moment_after=(
            "Her next step widens the gap and makes the former family arrangement visibly untenable."
        ),
        gaze_graph=tuple(str({
            "from_character_id": source,
            "to_character_id": "char_mara",
            "visible_attention": (
                "The look crosses from the background relationship group toward the departing "
                "foreground heroine."
            ),
        }) for source in ("char_oren", "char_pressure_a", "char_pressure_b")),
        blocking=(
            "The heroine dominates the left foreground with face, hands, and protected envelope clear. "
            "In the right background, the other three form an uneasy causal group across an expanding "
            "corridor gap while every principal face remains readable."
        ),
        environment_anchors=(
            "ordinary modern clinic corridor with practical seating and pale unbranded walls",
            "cold window light and a clearly visible exit",
        ),
        depth_plan=(
            "the heroine stays critically sharp in the foreground while all three background faces, "
            "hands, and story gestures remain recognizable instead of dissolving into blur"
        ),
    )

    compiled = compile_cover_prompt(brief, scene)

    assert len(compiled.text) <= 12_000
    assert "'from_character_id'" not in compiled.modules["RELATIONSHIP BLOCKING"]
    assert "char_oren -> char_mara" in compiled.modules["RELATIONSHIP BLOCKING"]


def test_compile_rejects_four_person_scene_without_foreground_background_causality() -> None:
    scene = replace(
        layered_conflict_scene(),
        blocking="four characters stand together in one flat row",
        depth_plan="all four characters share one flat plane",
    )

    with pytest.raises(ValueError, match="foreground and background"):
        compile_cover_prompt(brief_with_optional_conflict_cast(), scene)


def test_compile_rejects_cast_outside_the_approved_character_pool() -> None:
    scene = replace(scene_fixture(), cast=("char_mara", "char_oren", "char_unknown"))

    with pytest.raises(ValueError, match="unapproved"):
        compile_cover_prompt(brief_fixture(), scene)


def test_compile_keeps_exact_title_once_and_overrides_legacy_left_title_zone() -> None:
    scene = scene_fixture()
    repeated = CoverScenePlan(**{
        **scene.__dict__,
        "title_safe_zone": (
            'upper-left space for the exact title “The Door Is Mine,” with simple background'
        ),
    })

    compiled = compile_cover_prompt(brief_fixture(), repeated)

    assert compiled.text.count("The Door Is Mine") == 1
    assert "clean upper third negative space centered across the horizontal axis" in compiled.modules[
        "TITLE AND SAFE ZONE"
    ]
    assert "upper-left" not in compiled.modules["TITLE AND SAFE ZONE"]


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


def test_compile_allows_forbidden_element_later_in_negated_environment_list() -> None:
    payload = two_character_fixture()
    payload["forbidden_elements"] = ["logos"]
    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)
    scene = scene_fixture()
    constrained = replace(
        scene,
        environment_anchors=(
            "generic school grounds without landmarks, names, or logos",
        ),
    )

    compiled = compile_cover_prompt(brief, constrained)

    assert "without landmarks, names, or logos" in compiled.text


@pytest.mark.parametrize(
    "anchor",
    (
        "generic school grounds without landmarks, but logos on the banners",
        "no logos on the chairs; corporate logos on the stage",
        "without logos near the chairs, yet logos remain on the podium",
    ),
)
def test_compile_rejects_positive_forbidden_element_after_negated_use(anchor: str) -> None:
    payload = two_character_fixture()
    payload["forbidden_elements"] = ["logos"]
    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)
    scene = scene_fixture()
    invalid = replace(scene, environment_anchors=(anchor,))

    with pytest.raises(ValueError, match="forbidden element 'logos'"):
        compile_cover_prompt(brief, invalid)


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


def test_compile_allows_marketing_term_inside_negative_story_rule() -> None:
    brief = brief_fixture()
    rule = (
        "No replacement romance, inheritance, windfall, celebrity intervention, "
        "viral revenge, or sudden professional empire appears."
    )
    constrained = replace(
        brief,
        lived_environment=replace(
            brief.lived_environment,
            environment_truths=brief.lived_environment.environment_truths + (rule,),
        ),
    )

    compiled = compile_cover_prompt(constrained, scene_fixture())

    assert rule in compiled.modules["LIVED ENVIRONMENT AND PRIMARY PROP"]


@pytest.mark.parametrize("shortcut", ("Make it viral", "Promise high conversion"))
def test_compile_rejects_positive_marketing_shortcut(shortcut: str) -> None:
    scene = scene_fixture()
    invalid = replace(
        scene,
        visual_hook=replace(scene.visual_hook, reader_promise=shortcut),
    )

    with pytest.raises(ValueError, match="non-executable marketing shortcut"):
        compile_cover_prompt(brief_fixture(), invalid)


def test_repair_compile_changes_only_requested_modules() -> None:
    baseline = compile_cover_prompt(brief_fixture(), scene_fixture())

    repaired = compile_repair_prompt(
        brief_fixture(), scene_fixture(), baseline, ["age_mismatch"],
    )

    assert repaired.revision == baseline.revision + 1
    assert repaired.modules["SINGLE CINEMATIC MOMENT"] == baseline.modules["SINGLE CINEMATIC MOMENT"]
    assert repaired.modules["CAST LOCK"] != baseline.modules["CAST LOCK"]


def test_weak_story_action_repair_refreshes_causal_conflict_tableau() -> None:
    baseline = compile_cover_prompt(brief_fixture(), scene_fixture())

    repaired = compile_repair_prompt(
        brief_fixture(), scene_fixture(), baseline, ["weak_story_action"],
    )

    assert repaired.modules["CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY"] != baseline.modules[
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY"
    ]
    assert "Repair focus: weak story action." in repaired.modules[
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY"
    ]


def test_title_repair_refreshes_safe_zone_and_art_direction() -> None:
    baseline = compile_cover_prompt(brief_fixture(), scene_fixture())

    repaired = compile_repair_prompt(
        brief_fixture(), scene_fixture(), baseline, ["title_failure"],
    )

    assert repaired.modules["TITLE AND SAFE ZONE"] != baseline.modules["TITLE AND SAFE ZONE"]
    assert repaired.modules["TITLE ART DIRECTION"] != baseline.modules["TITLE ART DIRECTION"]
    assert "Repair focus: title failure." in repaired.modules["TITLE ART DIRECTION"]
