from __future__ import annotations

from core.cover_models import CoverBrief
from core.cover_normalizer import normalize_cover_brief


def _v1_brief() -> CoverBrief:
    return CoverBrief.from_dict({
        "schema_version": 1,
        "title": "Her Name on the Deed",
        "author": "",
        "language": "English",
        "genre": "family drama",
        "target_audience": "women rebuilding after betrayal",
        "market_scope": "English serialized fiction",
        "core_task": "A caregiver claims an independent home.",
        "core_conflict": "Two families demand her labor and income.",
        "emotional_promise": "anger followed by earned independence",
        "protagonist": {
            "role": "caregiver",
            "visual_identity": "composed woman in practical work clothes",
            "agency_signal": "holds her own house key",
        },
        "relationship_or_power_contrast": "one woman facing two demanding households",
        "decisive_story_node": "her name appears alone on the deed",
        "secondary_task": {
            "story_function": "protect child",
            "visual_signal": "small backpack",
        },
        "world_signals": ["fictional commuter district"],
        "title_direction": {"hierarchy": "large", "preferred_zone": "top"},
        "forbidden_elements": ["real cities"],
    }, source_prompt_sha256="a" * 64)


def test_normalizer_marks_v1_missing_identity_as_pending_assumptions() -> None:
    result = normalize_cover_brief(_v1_brief(), source_prompt_sha256="b" * 64)

    assert result.schema_version == 2
    assert result.principal_characters[0].character_id == "protagonist"
    assert any(
        item.status == "pending_confirmation"
        and item.field.startswith("principal_characters[0]")
        for item in result.visual_assumptions
    )


def test_normalizer_does_not_add_market_identity_to_v1_character() -> None:
    result = normalize_cover_brief(_v1_brief(), source_prompt_sha256="b" * 64)

    character = result.principal_characters[0]
    assert "American" not in character.physical_identity
    assert "white" not in character.physical_identity.lower()


def test_normalizer_accepts_v2_mapping_without_rewriting_facts() -> None:
    from tests.test_cover_models_v2 import two_character_fixture

    payload = two_character_fixture()
    result = normalize_cover_brief(payload, source_prompt_sha256="c" * 64)

    assert result.schema_version == 2
    assert result.principal_characters[1].age_band == "late thirties"
    assert result.lived_environment.fictional_place == "invented North American city"
