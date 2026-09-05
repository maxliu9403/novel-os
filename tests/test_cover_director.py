from __future__ import annotations

import json

import pytest

from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_design import evidence_ledger_from_brief
from core.cover_models_v2 import CoverBriefV2
from core.cover_profiles import portfolio_blueprint
from tests.test_cover_models_v2 import two_character_fixture


def _brief(count: int = 2) -> CoverBriefV2:
    payload = two_character_fixture()
    while len(payload["principal_characters"]) < count:
        index = len(payload["principal_characters"]) + 1
        payload["principal_characters"].append({
            "character_id": f"char_{index}",
            "name": f"Character {index}",
            "narrative_role": "co_protagonist",
            "must_appear": True,
            "age": 30 + index,
            "age_band": "",
            "gender_presentation": "person",
            "physical_identity": "natural face",
            "occupation_and_status": "administrative worker",
            "daily_wardrobe": "repeated-use work clothes",
            "lived_environment": "the apartment",
            "current_emotional_state": "watchful",
            "agency_signal": "holds a meaningful object",
            "relationships": [],
        })
    return CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)


def _plan(index: int, hook_type: str, cast: list[str]) -> dict:
    return {
        "concept_id": f"concept-{index}",
        "visual_strategy": hook_type,
        "story_evidence_refs": ["character:char_mara", "character:char_oren", "node:door_choice"],
        "cast": cast,
        "focal_character_id": "char_mara",
        "moment_before": "he asks for the shared key",
        "frozen_action": "she removes the key while he reaches but stops",
        "moment_after": "the door will close between them",
        "gaze_graph": ["char_mara -> key", "char_oren -> char_mara"],
        "blocking": "she foreground right; he one step behind left; doorway between them",
        "environment_anchors": ["lived-in apartment entry", "child backpack"],
        "primary_prop": "shared brass key",
        "shot_scale": "medium two-shot",
        "camera_height": "eye level",
        "lens": "50mm full-frame equivalent",
        "depth_plan": "faces and key readable; lived background",
        "motivated_lighting": "warm hall lamp and cool dusk window",
        "color_script": "warm skin against restrained cool separation",
        "title_safe_zone": "upper third, no faces or hands",
        "visual_hook": {
            "hook_type": hook_type,
            "first_glance_subject": "two spouses at a threshold",
            "open_question": "Will she close the door?",
            "identity_anchor": "a partner protecting her home",
            "genre_signal": "contemporary relationship drama",
            "reader_promise": "the protagonist recognizes the betrayal and acts",
            "target_emotion": "protective uncertainty",
            "misleading_risk": "low",
            "expected_thumbnail_read": "two people and one key at a threshold",
        },
    }


def director_fixture() -> dict:
    return {
        "schema_version": 1,
        "director_model": "fixture-director",
        "profile_version": "cover-profiles.v2",
        "plans": [
            _plan(1, "emotional_identification", ["char_mara", "char_oren"]),
            _plan(2, "relationship_tension", ["char_mara", "char_oren"]),
            _plan(3, "evidence_reveal", ["char_mara", "char_oren"]),
            _plan(4, "irreversible_moment", ["char_mara", "char_oren"]),
        ],
        "visual_assumptions": [],
    }


def v3_director_fixture() -> dict:
    payload = director_fixture()
    payload["profile_version"] = "cover-profiles.v3"
    for plan, treatment in zip(payload["plans"], portfolio_blueprint(4), strict=True):
        plan.update({
            "portfolio_slot": treatment.portfolio_slot,
            "composition_family": treatment.composition_family,
            "scene_family": treatment.scene_family,
            "location_family": "lived-in apartment entry",
            "art_style": treatment.art_style,
            "emotion_register": treatment.emotion_register,
            "typography_style": treatment.typography_style,
        })
        plan["frozen_action"] = f"distinct {treatment.scene_family} story beat"
    return payload


def adaptive_director_fixture() -> dict:
    brief = _brief()
    payload = {
        "schema_version": 1,
        "director_model": "fixture-designer",
        "profile_version": "cover-profiles.v4",
        "visual_identity": {
            "design_thesis": "Turn the shared doorway into a measure of who still belongs.",
            "dominant_emotional_contradiction": "domestic warmth held against irreversible separation",
            "story_signatures": ["the shared brass key", "the child's backpack by the door"],
            "visual_grammar": ["threshold geometry", "handled domestic evidence", "earned negative space"],
            "material_language": ["worn brass", "painted apartment wood", "creased school canvas"],
            "palette_logic": "warm lived-in amber interrupted by a narrow blue separation",
            "lighting_logic": "ordinary hall light makes the decisive object legible",
            "spatial_logic": "belonging is measured by access, distance, and empty space",
            "typography_voice": "doorway-like verticals with one controlled break in the title rhythm",
            "cast_policy": "Keep Mara visibly present in every direction while varying her scale, action, and relationship to the evidence.",
            "cliche_blacklist": ["large crying face", "generic couple embrace", "foreground victim with background lovers"],
            "uniqueness_anchors": ["key leaving the shared ring", "child backpack marking the family threshold"],
            "spoiler_boundary": ["do not reveal the final custody or reconciliation outcome"],
        },
        "evidence_ledger": evidence_ledger_from_brief(brief).to_dict(),
        "plans": [],
        "visual_assumptions": [],
    }
    variants = [
        {
            "focal_strategy": "character-led threshold decision",
            "composition_family": "off-axis doorway interruption",
            "scene_family": "key removal instant",
            "art_style": "natural-light editorial photography",
            "emotion_register": "controlled resolve",
            "typography_style": "broken-threshold literary serif",
            "cast": ["char_mara", "char_oren"],
            "focal_character_id": "char_mara",
            "gaze_graph": ["char_mara -> key", "char_oren -> closing door"],
            "primary_prop": "shared brass key",
            "visual_signature": "a key crossing the doorway axis between two still bodies",
        },
        {
            "focal_strategy": "object-led domestic evidence",
            "composition_family": "overhead ritual still life",
            "scene_family": "family routine interrupted",
            "art_style": "tactile editorial still-life photography",
            "emotion_register": "quiet alarm",
            "typography_style": "measured evidence-label grotesk",
            "cast": ["char_mara"],
            "focal_character_id": "char_mara",
            "gaze_graph": ["char_mara -> gap in the key ring"],
            "primary_prop": "key ring beside the child's backpack",
            "visual_signature": "one removed key leaves a bright gap in an ordinary family arrangement",
        },
        {
            "focal_strategy": "environment-led absence",
            "composition_family": "architectural negative-space corridor",
            "scene_family": "home after the boundary",
            "art_style": "restrained architectural campaign photography",
            "emotion_register": "protective emptiness",
            "typography_style": "quiet spatial modern serif",
            "cast": ["char_mara"],
            "focal_character_id": "char_mara",
            "gaze_graph": ["char_mara -> nearly closed doorway"],
            "primary_prop": "child backpack waiting inside the closed door",
            "visual_signature": "warm occupied interior visible beneath a nearly closed cool doorway",
        },
        {
            "focal_strategy": "typography-led access metaphor",
            "composition_family": "title as closing architectural gap",
            "scene_family": "conceptual ownership statement",
            "art_style": "minimal graphic publishing design with photographed brass texture",
            "emotion_register": "earned authority",
            "typography_style": "custom key-cut display lettering",
            "cast": ["char_mara"],
            "focal_character_id": "char_mara",
            "gaze_graph": ["char_mara -> title-shaped key tooth"],
            "primary_prop": "single brass key tooth integrated with the title field",
            "visual_signature": "title letterforms close around one real brass key tooth",
        },
    ]
    for index, variant in enumerate(variants, start=1):
        plan = _plan(index, f"adaptive_hook_{index}", list(variant["cast"]))
        plan.update(variant)
        plan.update({
            "portfolio_slot": f"book_specific_hypothesis_{index}",
            "location_family": "lived-in apartment entry",
            "moment_before": f"story-specific state before hypothesis {index}",
            "frozen_action": f"distinct designed event for hypothesis {index}",
            "moment_after": f"story-specific consequence after hypothesis {index}",
            "blocking": f"unique hierarchy for hypothesis {index}",
            "depth_plan": f"unique reading path and depth logic for hypothesis {index}",
            "color_script": f"book palette variation {index} tied to the same emotional contradiction",
            "design_rationale": f"hypothesis {index} visualizes a different causal layer of the doorway choice",
            "evidence_summary": "the key, doorway, and backpack are all approved story evidence",
            "typography_rationale": f"lettering system {index} translates access and separation into type",
            "novelty_rationale": f"hypothesis {index} changes subject, topology, medium treatment, and title behavior",
        })
        payload["plans"].append(plan)
    return payload


def test_fixture_director_builds_four_distinct_scene_plans() -> None:
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(_brief(), count=4)

    assert [plan.visual_hook.hook_type for plan in direction.plans] == [
        "emotional_identification", "relationship_tension", "evidence_reveal", "irreversible_moment",
    ]
    assert all(set(plan.cast) == {"char_mara", "char_oren"} for plan in direction.plans)
    assert direction.brief_sha256 == "a" * 64
    assert direction.status == "awaiting_approval"


def test_adaptive_fixture_can_mix_character_object_environment_and_type_led_plans() -> None:
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(_brief(), count=4)

    assert direction.profile_version == "cover-profiles.v4"
    assert direction.visual_identity is not None
    assert direction.evidence_ledger is not None
    assert [plan.focal_strategy.split("-")[0] for plan in direction.plans] == [
        "character", "object", "environment", "typography",
    ]
    assert [len(plan.cast) for plan in direction.plans] == [2, 1, 1, 1]


def test_director_rejects_provider_json_that_is_not_an_object() -> None:
    director = CoverArtDirector(complete=lambda _system, _user: "[]", model="fixture-director")

    with pytest.raises(CoverDirectionError, match="object"):
        director.plan(_brief(), count=4)


def test_live_director_repairs_invalid_json_shape_before_semantic_review() -> None:
    responses = iter(("[]", json.dumps(adaptive_director_fixture())))
    calls: list[str] = []

    def complete(_system: str, user: str) -> str:
        calls.append(user)
        return next(responses)

    direction = CoverArtDirector(complete=complete, model="fixture-designer").plan(
        _brief(), count=4,
    )

    assert len(calls) == 2
    assert "cover director response must be a JSON object" in calls[1]
    assert direction.visual_identity is not None
    assert direction.profile_version == "cover-profiles.v4"


def test_director_rejects_provider_failure_before_image_generation() -> None:
    def fail(_system: str, _user: str) -> str:
        raise RuntimeError("director unavailable")

    director = CoverArtDirector(complete=fail, model="fixture-director")

    with pytest.raises(CoverDirectionError, match="director unavailable"):
        director.plan(_brief(), count=4)


def test_director_prompt_defines_exact_machine_readable_response_contract() -> None:
    brief = _brief()

    payload = json.loads(CoverArtDirector._user_prompt(brief, 4))

    contract = payload["response_contract"]
    assert contract["plans"]["exact_count"] == 4
    assert "required_hook_types" not in contract["plans"]
    assert "portfolio_blueprint" not in contract["plans"]
    assert set(contract["plans"]["required_fields"]) == {
        "concept_id", "visual_strategy", "story_evidence_refs", "cast",
        "focal_character_id", "moment_before", "frozen_action", "moment_after",
        "gaze_graph", "blocking", "environment_anchors", "primary_prop",
        "shot_scale", "camera_height", "lens", "depth_plan",
        "motivated_lighting", "color_script", "title_safe_zone", "visual_hook",
        "portfolio_slot", "composition_family", "scene_family", "location_family", "art_style",
        "emotion_register", "typography_style", "focal_strategy", "design_rationale",
        "evidence_summary", "typography_rationale", "novelty_rationale", "visual_signature",
    }
    assert contract["fixed_values"]["profile_version"] == "cover-profiles.v4"
    assert "visual_identity" in contract["top_level_required_fields"]
    assert contract["allowed_character_ids"] == ["char_mara", "char_oren"]
    assert contract["base_evidence_refs"] == [
        "character:char_mara", "character:char_oren", "node:door_choice",
        "signal:child_backpack",
    ]
    assert contract["allowed_location_families"] == ["lived-in apartment entry"]
    field_rules = contract["plans"]["field_rules"]
    assert "mandatory foreground/background" in field_rules["blocking"]
    assert "one or more" in field_rules["cast"]
    assert "book-specific lettering" in field_rules["typography_style"]


def test_director_contract_does_not_force_causal_foreground_background_staging() -> None:
    payload = two_character_fixture()
    optional = dict(payload["principal_characters"][1])
    optional.update({
        "character_id": "char_pressure",
        "name": "Pressure Character",
        "must_appear": False,
        "relationships": ["char_mara"],
    })
    payload["principal_characters"].append(optional)
    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)

    request = json.loads(CoverArtDirector._user_prompt(brief, 4))
    contract = request["response_contract"]
    rules = contract["plans"]["field_rules"]

    assert contract["allowed_character_ids"] == ["char_mara", "char_oren", "char_pressure"]
    assert "mandatory foreground/background" in rules["blocking"]
    assert "one or more" in rules["cast"]
    assert any("reader-anchor protagonist" in rule for rule in contract["plans"]["portfolio_rules"])
    assert "portfolio_blueprint" not in contract["plans"]


def test_adaptive_contract_requires_a_visible_reader_anchor_without_fixing_composition() -> None:
    brief = _brief()

    contract = json.loads(CoverArtDirector._user_prompt(brief, 4))["response_contract"]

    assert contract["reader_anchor_character_id"] == "char_mara"
    assert "must include reader_anchor_character_id" in contract["plans"]["field_rules"]["cast"]
    assert any(
        "Every plan" in rule and "reader-anchor" in rule
        for rule in contract["plans"]["portfolio_rules"]
    )


def test_live_director_repairs_group_blocking_before_returning_direction() -> None:
    brief = _brief(4)
    invalid = director_fixture()
    invalid["plans"] = [
        {
            **plan,
            "cast": ["char_mara", "char_oren", "char_3", "char_4"],
            "blocking": "four people arranged near the apartment door",
            "depth_plan": "all faces readable around the doorway",
        }
        for plan in invalid["plans"]
    ]
    repaired = json.loads(json.dumps(invalid))
    for plan in repaired["plans"]:
        plan["blocking"] = (
            "char_mara carries the consequence in the foreground while "
            "char_oren, char_3, and char_4 reveal its cause in the background"
        )
    responses = iter((json.dumps(invalid), json.dumps(repaired)))
    calls: list[str] = []

    def complete(_system: str, user: str) -> str:
        calls.append(user)
        return next(responses)

    direction = CoverArtDirector(
        complete=complete, model="fixture-director", profile_version="cover-profiles.v2",
    ).plan(
        brief, count=4
    )

    assert len(calls) == 2
    assert "group_blocking" in calls[1]
    assert "concept-1" in calls[1]
    assert all("foreground" in plan.blocking for plan in direction.plans)


def test_live_v3_director_repairs_repeated_v2_style_plans_into_assigned_portfolio() -> None:
    brief = _brief()
    repeated = director_fixture()
    repaired = v3_director_fixture()
    responses = iter((json.dumps(repeated), json.dumps(repaired)))
    calls: list[str] = []

    def complete(_system: str, user: str) -> str:
        calls.append(user)
        return next(responses)

    direction = CoverArtDirector(
        complete=complete, model="fixture-director", profile_version="cover-profiles.v3",
    ).plan(
        brief, count=4,
    )

    assert len(calls) == 2
    assert "location_family_mismatch" in calls[1]
    assert "duplicate_story_beat" in calls[1]
    assert direction.profile_version == "cover-profiles.v3"
    assert len({plan.composition_family for plan in direction.plans}) == 4
    assert len({plan.scene_family for plan in direction.plans}) == 4
    assert len({plan.art_style for plan in direction.plans}) == 4
    assert len({plan.emotion_register for plan in direction.plans}) == 4
    assert len({plan.typography_style for plan in direction.plans}) == 4


def test_director_count_error_reports_requested_and_received_plans() -> None:
    payload = director_fixture()
    payload["plans"] = payload["plans"][:3]
    director = CoverArtDirector.from_fixture(payload)

    with pytest.raises(
        CoverDirectionError,
        match="requested 4, received 3",
    ):
        director.plan(_brief(), count=4)
