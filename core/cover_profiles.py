"""Genre-specific visual grammar for cover prompt compilation."""

from __future__ import annotations

from dataclasses import replace

from .cover_models_v2 import CoverBriefV2, GenreEmotionProfile


_ROMANCE = {
    "tender_slow_burn": GenreEmotionProfile(
        "romance", "tender_slow_burn", "warm but restrained",
        "intimacy mixed with uncertainty", "about to move closer but still blocked",
        ("generic smiling couple", "rose collage", "wedding imagery"),
    ),
    "reconciliation": GenreEmotionProfile(
        "romance", "reconciliation", "warm memory against cool distance",
        "hope tempered by past hurt", "shared space is possible but trust is unfinished",
        ("generic smiling couple", "wedding imagery", "glossy luxury setting"),
    ),
    "forbidden_tension": GenreEmotionProfile(
        "romance", "forbidden_tension", "warm skin against controlled shadow",
        "desire mixed with risk", "bodies move closer while an external boundary holds",
        ("generic smiling couple", "romance collage", "unsupported physical contact"),
    ),
    "betrayal_romance": GenreEmotionProfile(
        "romance", "betrayal_romance", "warm remembered light against cool reality",
        "recognition followed by protective anger", "one person discovers the rupture while another avoids it",
        ("generic smiling couple", "rose collage", "unearned reconciliation"),
    ),
}

_FAMILY = {
    "accusation_triangle": GenreEmotionProfile(
        "family_ethics", "accusation_triangle", "plain domestic light with tense contrast",
        "injustice and protectiveness", "an accusation tightens around a hesitant witness and a defender",
        ("generic sad family portrait", "luxury mansion", "sentimental collage"),
    ),
    "public_exclusion": GenreEmotionProfile(
        "family_ethics", "public_exclusion", "bright public space with a cooler excluded foreground",
        "exclusion and anger", "the protagonist and child are separated from a celebrating relationship; one evidence object makes the exclusion legible",
        ("generic family portrait", "party collage", "unearned celebration"),
    ),
    "domestic_betrayal": GenreEmotionProfile(
        "family_ethics", "domestic_betrayal", "ordinary household light with a visible temperature split",
        "recognition of betrayal", "an everyday room contains an abnormal distance and a revealing object",
        ("generic sad family portrait", "wedding imagery", "decorative luxury"),
    ),
    "boundary_and_departure": GenreEmotionProfile(
        "family_ethics", "boundary_and_departure", "honest practical light with controlled separation",
        "clear-eyed action", "the protagonist controls the door, luggage, or document while pressure recedes",
        ("generic sad family portrait", "victory pose", "unearned wealth"),
    ),
}

_NEUTRAL = GenreEmotionProfile(
    "neutral", "neutral_baseline", "cinematic and restrained",
    "curiosity grounded in the story's conflict", "the approved action is underway and unresolved",
    ("generic posed portrait", "collage", "unsupported spectacle"),
)


def resolve_genre_profile(brief: CoverBriefV2) -> GenreEmotionProfile:
    """Return a canonical profile without changing story facts or identity."""
    genre = brief.genre.casefold()
    requested = brief.genre_emotion_profile.submode.casefold()
    if any(marker in genre for marker in ("romance", "love", "言情", "爱情")):
        profile = _ROMANCE.get(requested) or _ROMANCE["tender_slow_burn"]
    elif any(marker in genre for marker in ("family", "domestic", "ethic", "家庭", "伦理")):
        profile = _FAMILY.get(requested) or _FAMILY["domestic_betrayal"]
    else:
        profile = _NEUTRAL
    # The story contract can provide a more specific emotional temperature and
    # promise; the profile supplies only the visual grammar defaults.
    supplied = brief.genre_emotion_profile
    if supplied.primary_genre.casefold() != profile.primary_genre.casefold():
        return profile
    return replace(
        profile,
        emotional_temperature=supplied.emotional_temperature or profile.emotional_temperature,
        desired_viewer_feeling=supplied.desired_viewer_feeling or profile.desired_viewer_feeling,
        relationship_motion=supplied.relationship_motion or profile.relationship_motion,
    )
