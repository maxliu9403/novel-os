from __future__ import annotations

import json

import pytest

from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_models_v2 import CoverBriefV2
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


def test_fixture_director_builds_four_distinct_scene_plans() -> None:
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(_brief(), count=4)

    assert [plan.visual_hook.hook_type for plan in direction.plans] == [
        "emotional_identification", "relationship_tension", "evidence_reveal", "irreversible_moment",
    ]
    assert all(set(plan.cast) == {"char_mara", "char_oren"} for plan in direction.plans)
    assert direction.brief_sha256 == "a" * 64
    assert direction.status == "awaiting_approval"


def test_director_rejects_provider_json_that_is_not_an_object() -> None:
    director = CoverArtDirector(complete=lambda _system, _user: "[]", model="fixture-director")

    with pytest.raises(CoverDirectionError, match="object"):
        director.plan(_brief(), count=4)


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
    assert contract["plans"]["required_hook_types"] == [
        "emotional_identification",
        "relationship_tension",
        "evidence_reveal",
        "irreversible_moment",
    ]
    assert set(contract["plans"]["required_fields"]) == {
        "concept_id", "visual_strategy", "story_evidence_refs", "cast",
        "focal_character_id", "moment_before", "frozen_action", "moment_after",
        "gaze_graph", "blocking", "environment_anchors", "primary_prop",
        "shot_scale", "camera_height", "lens", "depth_plan",
        "motivated_lighting", "color_script", "title_safe_zone", "visual_hook",
    }
    assert contract["allowed_character_ids"] == ["char_mara", "char_oren"]
    assert contract["required_character_ids"] == ["char_mara", "char_oren"]
    assert contract["allowed_evidence_refs"] == [
        "character:char_mara", "character:char_oren", "node:door_choice",
        "signal:child_backpack",
    ]
    field_rules = contract["plans"]["field_rules"]
    assert "principal people" in field_rules["blocking"]
    assert "faces" in field_rules["depth_plan"]
    assert "centered title" in field_rules["title_safe_zone"]
    assert "core story atmosphere" in field_rules["motivated_lighting"]


def test_director_contract_makes_causal_conflict_staging_a_general_creative_rule() -> None:
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

    assert contract["optional_conflict_character_ids"] == ["char_pressure"]
    assert "foreground emotional consequence" in contract["plans"]["portfolio_rules"][0]
    assert "background causal relationship action" in contract["plans"]["portfolio_rules"][0]
    assert "not a flat group pose" in rules["blocking"]
    assert "literal words foreground and background" in rules["blocking"]
    assert "cross depth planes" in rules["gaze_graph"]
    assert "emotionally legible action" in rules["frozen_action"]


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

    direction = CoverArtDirector(complete=complete, model="fixture-director").plan(
        brief, count=4
    )

    assert len(calls) == 2
    assert "group_blocking" in calls[1]
    assert "concept-1" in calls[1]
    assert all("foreground" in plan.blocking for plan in direction.plans)


def test_director_count_error_reports_requested_and_received_plans() -> None:
    payload = director_fixture()
    payload["plans"] = payload["plans"][:3]
    director = CoverArtDirector.from_fixture(payload)

    with pytest.raises(
        CoverDirectionError,
        match="requested 4, received 3",
    ):
        director.plan(_brief(), count=4)
