from __future__ import annotations

from core.cover_models_v2 import CoverBriefV2
from core import cover_profiles
from core.cover_profiles import resolve_genre_profile
from tests.test_cover_models_v2 import two_character_fixture


def _brief(genre: str, submode: str = "") -> CoverBriefV2:
    payload = two_character_fixture()
    payload["genre"] = genre
    payload["genre_emotion_profile"]["submode"] = submode
    return CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)


def test_romance_profile_creates_restrained_warmth_from_behavior() -> None:
    profile = resolve_genre_profile(_brief("contemporary romance", "tender_slow_burn"))

    assert profile.primary_genre == "romance"
    assert profile.submode == "tender_slow_burn"
    assert "warm" in profile.emotional_temperature
    assert "generic smiling couple" in profile.prohibited_shortcuts


def test_family_ethics_profile_uses_relationship_geometry_and_evidence() -> None:
    profile = resolve_genre_profile(_brief("family ethics drama", "public_exclusion"))

    assert profile.primary_genre == "family_ethics"
    assert profile.submode == "public_exclusion"
    assert "evidence" in profile.relationship_motion


def test_unknown_genre_uses_neutral_cinematic_baseline() -> None:
    profile = resolve_genre_profile(_brief("speculative mystery"))

    assert profile.primary_genre == "neutral"
    assert profile.submode == "neutral_baseline"
    assert profile.prohibited_shortcuts


def test_title_typography_changes_voice_between_romance_and_family_ethics() -> None:
    romance = cover_profiles.resolve_title_typography(
        resolve_genre_profile(_brief("contemporary romance", "tender_slow_burn"))
    )
    family = cover_profiles.resolve_title_typography(
        resolve_genre_profile(_brief("family ethics drama", "domestic_betrayal"))
    )

    assert romance.letterform_voice != family.letterform_voice
    assert "calligraphic" in romance.expressive_detail
    assert "tension" in family.expressive_detail
    assert "generic Times-like typesetting" in family.prohibited_shortcuts
