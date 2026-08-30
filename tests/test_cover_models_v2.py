from __future__ import annotations

import pytest

from core.cover_models_v2 import CoverBriefV2


def _character(character_id: str, *, age: int | None = 34, age_band: str = "") -> dict:
    return {
        "character_id": character_id,
        "name": character_id.replace("_", " ").title(),
        "narrative_role": "co_protagonist",
        "must_appear": True,
        "age": age,
        "age_band": age_band,
        "gender_presentation": "woman",
        "physical_identity": "tired eyes and natural skin texture",
        "occupation_and_status": "hospital administrator with stable income",
        "daily_wardrobe": "repeated-use office clothing",
        "lived_environment": "modest apartment kitchen",
        "current_emotional_state": "controlled hurt becoming resolve",
        "agency_signal": "holds the shared brass key",
        "relationships": ["char_oren"],
    }


def two_character_fixture() -> dict:
    return {
        "schema_version": 2,
        "title": "The Door Is Mine",
        "author": "",
        "language": "English",
        "genre": "contemporary romance",
        "target_audience": "women 30-45 seeking relationship drama",
        "market_scope": "United States mobile serialized fiction",
        "core_task": "A partner protects her child and claims a home.",
        "core_conflict": "A damaged relationship threatens the family's home.",
        "emotional_promise": "intimacy mixed with uncertainty",
        "principal_characters": [
            _character("char_mara"),
            {**_character("char_oren", age=None, age_band="late thirties"), "gender_presentation": "man"},
        ],
        "relationship_map": [{
            "from_character_id": "char_mara",
            "to_character_id": "char_oren",
            "relationship": "estranged spouses",
            "power_balance": "he controls public status; she controls the evidence",
            "visible_tension": "he avoids eye contact while she holds the key",
            "shared_risk": "their child witnesses the rupture",
        }],
        "lived_environment": {
            "era": "present day",
            "fictional_place": "invented North American city",
            "primary_spaces": ["lived-in apartment entry"],
            "economic_signals": ["repaired dining chair", "practical school backpack"],
            "cultural_signals": ["confirmed domestic routines"],
            "weather_and_season": "late autumn evening",
            "environment_truths": ["the home is actively lived in"],
        },
        "decisive_story_nodes": [{
            "node_id": "door_choice",
            "description": "She removes the shared key before closing the apartment door.",
            "evidence_refs": ["character:char_mara", "character:char_oren"],
        }],
        "secondary_signals": [{
            "signal_id": "child_backpack",
            "description": "a school backpack near the doorway",
            "story_function": "show the child witnesses the rupture",
        }],
        "genre_emotion_profile": {
            "primary_genre": "romance",
            "submode": "reconciliation",
            "emotional_temperature": "warm but restrained",
            "desired_viewer_feeling": "intimacy mixed with uncertainty",
            "relationship_motion": "about to move closer but still blocked",
            "prohibited_shortcuts": ["generic smiling couple", "wedding imagery"],
        },
        "commercial_visual_goal": {
            "market": "United States",
            "audience_segment": "women 30-45 interested in relationship drama",
            "display_context": "mobile_thumbnail",
            "thumbnail_reference_width": 120,
            "thumbnail_reference_height": 180,
            "first_glance_priority": "relationship rupture",
            "reader_identification": "a partner protecting herself and her child",
            "truthful_story_promise": "the protagonist recognizes the betrayal and acts",
        },
        "title_direction": {
            "hierarchy": "large",
            "preferred_zone": "upper third",
            "readability": "mobile_thumbnail",
        },
        "forbidden_elements": ["real landmarks", "logos", "watermarks"],
        "visual_assumptions": [],
    }


def test_v2_round_trip_preserves_required_characters_and_visual_goal() -> None:
    brief = CoverBriefV2.from_dict(two_character_fixture(), source_prompt_sha256="a" * 64)

    restored = CoverBriefV2.from_dict(
        brief.to_dict(), source_prompt_sha256="a" * 64,
    )

    assert restored == brief
    assert [item.character_id for item in brief.principal_characters] == [
        "char_mara", "char_oren",
    ]
    assert brief.commercial_visual_goal.display_context == "mobile_thumbnail"


def test_v2_rejects_required_character_without_age_or_lived_identity() -> None:
    payload = two_character_fixture()
    payload["principal_characters"][0].pop("age")
    payload["principal_characters"][0].pop("age_band")

    with pytest.raises(ValueError, match="age"):
        CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)


def test_v2_requires_identity_anchor_for_required_character() -> None:
    payload = two_character_fixture()
    payload["principal_characters"][0]["occupation_and_status"] = ""
    payload["principal_characters"][0]["daily_wardrobe"] = ""
    payload["principal_characters"][0]["lived_environment"] = ""

    with pytest.raises(ValueError, match="lived identity"):
        CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)


def test_v2_accepts_four_required_characters_for_group_composition() -> None:
    payload = two_character_fixture()
    payload["principal_characters"].extend([
        _character("char_ivy", age=42),
        _character("char_sam", age=None, age_band="early forties"),
    ])

    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)

    assert len([item for item in brief.principal_characters if item.must_appear]) == 4
