"""Genre-specific visual grammar for cover prompt compilation."""

from __future__ import annotations

from dataclasses import dataclass, replace

try:
    from .cover_models_v2 import CoverBriefV2, GenreEmotionProfile
except ImportError:  # pragma: no cover - legacy CLI imports core modules top-level
    from cover_models_v2 import CoverBriefV2, GenreEmotionProfile


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


@dataclass(frozen=True)
class TitleTypographyProfile:
    letterform_voice: str
    hierarchy: str
    expressive_detail: str
    prohibited_shortcuts: tuple[str, ...]


_TITLE_PROHIBITED_SHORTCUTS = (
    "generic Times-like typesetting",
    "rigid centered block",
    "full-script or cursive title",
    "heavy outline, bevel, glow, or drop shadow",
    "distorted or symbol-replaced letters",
)

_ROMANCE_TITLE = TitleTypographyProfile(
    letterform_voice=(
        "elegant high-contrast editorial serif letterforms with warm humanist curves "
        "and lightly hand-finished terminals"
    ),
    hierarchy=(
        "an airy, intimate 2-4 line composition with supporting words held smaller "
        "and emotional words given graceful scale"
    ),
    expressive_detail=(
        "one restrained calligraphic entry stroke or extended terminal may suggest "
        "closeness without turning the title into script"
    ),
    prohibited_shortcuts=_TITLE_PROHIBITED_SHORTCUTS,
)

_FAMILY_TITLE = TitleTypographyProfile(
    letterform_voice=(
        "literary high-contrast editorial serif letterforms with humanist stress, "
        "subtle asymmetry, and slightly hand-finished terminals"
    ),
    hierarchy=(
        "a controlled asymmetric 2-4 line composition that lets the conflict-bearing "
        "words dominate while connector words remain quieter"
    ),
    expressive_detail=(
        "one restrained offset, terminal, or baseline tension may echo the domestic "
        "fracture without damaging dignity or readability"
    ),
    prohibited_shortcuts=_TITLE_PROHIBITED_SHORTCUTS,
)

_NEUTRAL_TITLE = TitleTypographyProfile(
    letterform_voice=(
        "refined cinematic editorial serif letterforms with balanced contrast and "
        "subtle hand-finished character"
    ),
    hierarchy=(
        "a composed asymmetric 2-4 line arrangement with clear scale contrast between "
        "supporting and story-bearing words"
    ),
    expressive_detail=(
        "one restrained calligraphic terminal may provide a memorable signature while "
        "the remaining letters stay precise"
    ),
    prohibited_shortcuts=_TITLE_PROHIBITED_SHORTCUTS,
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


def resolve_title_typography(profile: GenreEmotionProfile) -> TitleTypographyProfile:
    """Return an executable title voice aligned with the resolved cover genre."""
    primary_genre = profile.primary_genre.casefold()
    if primary_genre == "romance":
        return _ROMANCE_TITLE
    if primary_genre == "family_ethics":
        return _FAMILY_TITLE
    return _NEUTRAL_TITLE
