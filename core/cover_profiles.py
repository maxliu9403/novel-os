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
        "recognition followed by protective anger",
        "the injured or deciding person carries the foreground while the background relationship action reveals the rupture",
        (
            "generic smiling couple", "generic couple pose", "isolated sad portrait",
            "rose collage", "unearned reconciliation",
        ),
    ),
}

_FAMILY = {
    "accusation_triangle": GenreEmotionProfile(
        "family_ethics", "accusation_triangle", "plain domestic light with tense contrast",
        "injustice and protectiveness",
        "the foreground defender or accused person bears the pressure while a secondary plane exposes the accuser-witness alignment",
        ("generic sad family portrait", "flat group portrait", "luxury mansion", "sentimental collage"),
    ),
    "public_exclusion": GenreEmotionProfile(
        "family_ethics", "public_exclusion", "bright public space with a cooler excluded foreground",
        "exclusion and anger",
        "the foreground shows who bears the exclusion while background relationship alignment and one evidence object make its cause legible",
        (
            "generic family portrait", "isolated sad portrait", "flat group portrait",
            "party collage", "unearned celebration",
        ),
    ),
    "domestic_betrayal": GenreEmotionProfile(
        "family_ethics", "domestic_betrayal", "ordinary household light with a visible temperature split",
        "recognition of betrayal",
        "the foreground reaction or boundary answers a background relationship action, with an everyday object confirming the rupture",
        ("generic sad family portrait", "isolated sad portrait", "flat group portrait", "wedding imagery", "decorative luxury"),
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


@dataclass(frozen=True)
class CoverPortfolioTreatment:
    """One deliberately different visual language inside a cover portfolio."""

    hook_type: str
    portfolio_slot: str
    display_name: str
    composition_family: str
    composition_direction: str
    scene_family: str
    scene_direction: str
    art_style: str
    art_direction: str
    emotion_register: str
    emotion_direction: str
    typography_style: str
    typography: TitleTypographyProfile
    camera_direction: str


_TITLE_PROHIBITED_SHORTCUTS = (
    "generic Times-like typesetting",
    "mechanically centered equal-size line stack",
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
        "an airy, intimate centered 2-4 line composition with supporting words held "
        "smaller and emotional words given graceful scale"
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
        "a controlled centered 2-4 line composition that lets the conflict-bearing "
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
        "a composed centered 2-4 line arrangement with clear scale contrast between "
        "supporting and story-bearing words"
    ),
    expressive_detail=(
        "one restrained calligraphic terminal may provide a memorable signature while "
        "the remaining letters stay precise"
    ),
    prohibited_shortcuts=_TITLE_PROHIBITED_SHORTCUTS,
)


_PORTFOLIO_TREATMENTS = {
    "emotional_identification": CoverPortfolioTreatment(
        hook_type="emotional_identification",
        portfolio_slot="intimate_character_window",
        display_name="Intimate reckoning",
        composition_family="asymmetric_close_plane",
        composition_direction=(
            "Use an asymmetric close or medium-close character window: the protagonist owns the large "
            "clear plane while one reflection, doorway, or distant eyeline carries the source of the hurt."
        ),
        scene_family="private_reckoning",
        scene_direction=(
            "Stage a private recognition or immediate aftermath beat rather than the ensemble confrontation "
            "or departure used by the other portfolio slots."
        ),
        art_style="intimate_editorial_portrait",
        art_direction=(
            "Intimate editorial portrait key art with tactile natural light, restrained grain, and a "
            "photographed prestige-drama finish."
        ),
        emotion_register="wounded_recognition",
        emotion_direction="Make wounded recognition and protective self-command arrive before overt anger.",
        typography_style="airy_literary_serif",
        typography=TitleTypographyProfile(
            letterform_voice="airy high-contrast literary serif with warm humanist curves",
            hierarchy="quiet 2-4 line hierarchy with generous tracking and one emotionally weighted word",
            expressive_detail="one delicate extended terminal echoes a thought left unfinished",
            prohibited_shortcuts=_TITLE_PROHIBITED_SHORTCUTS,
        ),
        camera_direction="Favor an 85mm-feeling intimate perspective and selective but story-legible depth.",
    ),
    "relationship_tension": CoverPortfolioTreatment(
        hook_type="relationship_tension",
        portfolio_slot="relationship_geometry",
        display_name="Divided relationship",
        composition_family="triangular_depth_tableau",
        composition_direction=(
            "Build triangular or diagonal relationship geometry across foreground and background so the "
            "emotional consequence and the causing alignment read together."
        ),
        scene_family="causal_ensemble",
        scene_direction=(
            "Stage the direct relationship choice or confrontation as the portfolio's clearest ensemble scene."
        ),
        art_style="deep_focus_prestige_drama",
        art_direction=(
            "Deep-focus prestige drama campaign photography with controlled warm-cool separation and "
            "crisp interpersonal blocking."
        ),
        emotion_register="divided_loyalty",
        emotion_direction="Make divided loyalty, exclusion, and contained anger simultaneously legible.",
        typography_style="fractured_editorial_serif",
        typography=TitleTypographyProfile(
            letterform_voice="high-contrast editorial serif with a restrained fractured-axis rhythm",
            hierarchy="tense 2-4 line hierarchy with opposing title phrases slightly offset around one axis",
            expressive_detail="one subtle baseline break mirrors the relationship fracture",
            prohibited_shortcuts=_TITLE_PROHIBITED_SHORTCUTS,
        ),
        camera_direction="Favor a 45-55mm deep-focus ensemble perspective with readable cross-plane gazes.",
    ),
    "evidence_reveal": CoverPortfolioTreatment(
        hook_type="evidence_reveal",
        portfolio_slot="evidence_mystery",
        display_name="Evidence revelation",
        composition_family="evidence_led_negative_space",
        composition_direction=(
            "Let one approved evidence object cut into the foreground while a clear human reaction and "
            "purposeful negative space form a graphic triangular reading path."
        ),
        scene_family="evidence_discovery",
        scene_direction=(
            "Stage the instant an approved object or signal changes what the protagonist understands, in a "
            "different narrative beat from confrontation and departure."
        ),
        art_style="graphic_editorial_suspense",
        art_direction=(
            "Crisp editorial-suspense key art with controlled contrast, precise object detail, and magazine-like restraint."
        ),
        emotion_register="shock_and_dread",
        emotion_direction="Lead with curiosity, then let recognition sharpen into quiet shock and dread.",
        typography_style="condensed_evidence_lockup",
        typography=TitleTypographyProfile(
            letterform_voice="narrow editorial display serif with crisp cuts and forensic precision",
            hierarchy="compact 2-4 line lockup whose strongest evidence-bearing word receives dominant scale",
            expressive_detail="one hairline rule or measured gap may echo a missing piece of evidence",
            prohibited_shortcuts=_TITLE_PROHIBITED_SHORTCUTS,
        ),
        camera_direction="Favor an oblique or slightly high camera with close evidence detail and a readable face.",
    ),
    "irreversible_moment": CoverPortfolioTreatment(
        hook_type="irreversible_moment",
        portfolio_slot="kinetic_threshold",
        display_name="Irreversible departure",
        composition_family="diagonal_threshold_motion",
        composition_direction=(
            "Use a medium-wide diagonal with a threshold, leading lines, and directional movement so the "
            "protagonist's decision changes the shape of the frame."
        ),
        scene_family="threshold_departure",
        scene_direction=(
            "Stage the irreversible choice, refusal, or departure after the conflict rather than replaying "
            "the discovery or confrontation."
        ),
        art_style="kinetic_cinematic_key_art",
        art_direction=(
            "Kinetic cinematic key art with harder motivated backlight, restrained motion energy, and a "
            "decisive prestige-drama finish."
        ),
        emotion_register="cathartic_resolve",
        emotion_direction="Turn grief into cathartic resolve without using a triumphant victory pose.",
        typography_style="bold_cinematic_serif",
        typography=TitleTypographyProfile(
            letterform_voice="bold cinematic serif with sculpted contrast and decisive vertical stress",
            hierarchy="confident 2-4 line hierarchy with tighter leading and the action-bearing words largest",
            expressive_detail="one controlled forward offset carries the direction of departure",
            prohibited_shortcuts=_TITLE_PROHIBITED_SHORTCUTS,
        ),
        camera_direction="Favor a 35-45mm medium-wide perspective with strong leading lines and readable motion.",
    ),
    "environmental_pressure": CoverPortfolioTreatment(
        hook_type="environmental_pressure",
        portfolio_slot="social_pressure",
        display_name="World closing in",
        composition_family="compressed_architectural_frame",
        composition_direction=(
            "Frame a clear, readable protagonist through architecture or a crowd edge so ordinary surroundings "
            "become pressure without shrinking the person into scenery."
        ),
        scene_family="public_pressure",
        scene_direction="Stage an approved public or institutional consequence distinct from the private beats.",
        art_style="architectural_social_drama",
        art_direction=(
            "Architectural social-drama key art with disciplined geometry, natural practical light, and sober scale."
        ),
        emotion_register="public_isolation",
        emotion_direction="Make scrutiny and public isolation tighten into controlled defiance.",
        typography_style="institutional_high_contrast_serif",
        typography=TitleTypographyProfile(
            letterform_voice="disciplined high-contrast serif with firm verticals and sober editorial authority",
            hierarchy="structured 2-4 line hierarchy aligned to the architecture while remaining centered overall",
            expressive_detail="one narrow interruption in spacing suggests social pressure",
            prohibited_shortcuts=_TITLE_PROHIBITED_SHORTCUTS,
        ),
        camera_direction="Favor a 40-60mm perspective with architectural compression and a clearly sized face.",
    ),
}


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
        relationship_motion=(
            f"{supplied.relationship_motion}; visual grammar: {profile.relationship_motion}"
            if supplied.relationship_motion and supplied.relationship_motion != profile.relationship_motion
            else profile.relationship_motion
        ),
    )


def resolve_title_typography(
    profile: GenreEmotionProfile,
    *,
    hook_type: str = "",
) -> TitleTypographyProfile:
    """Return a title voice aligned with the genre and portfolio slot."""
    treatment = _PORTFOLIO_TREATMENTS.get(hook_type.casefold())
    if treatment is not None:
        return treatment.typography
    primary_genre = profile.primary_genre.casefold()
    if primary_genre == "romance":
        return _ROMANCE_TITLE
    if primary_genre == "family_ethics":
        return _FAMILY_TITLE
    return _NEUTRAL_TITLE


def resolve_portfolio_treatment(hook_type: str) -> CoverPortfolioTreatment:
    """Return the deterministic visual language assigned to one hook type."""
    key = str(hook_type or "").strip().casefold()
    try:
        return _PORTFOLIO_TREATMENTS[key]
    except KeyError as exc:
        raise ValueError(f"Unknown cover portfolio hook type '{hook_type}'") from exc


def portfolio_blueprint(count: int) -> tuple[CoverPortfolioTreatment, ...]:
    """Return 3-5 intentionally dissimilar treatments in stable review order."""
    if not 3 <= count <= 5:
        raise ValueError("Cover portfolio count must be between 3 and 5")
    hook_types = (
        "emotional_identification",
        "relationship_tension",
        "evidence_reveal",
        "irreversible_moment",
        "environmental_pressure",
    )
    return tuple(_PORTFOLIO_TREATMENTS[item] for item in hook_types[:count])
