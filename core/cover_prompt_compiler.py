"""Pure deterministic compiler for canon-bound cinematic cover prompts."""

from __future__ import annotations

import ast
import re
from dataclasses import replace
from typing import Sequence

try:
    from .cover_models_v2 import (
        COVER_REPAIR_CODES,
        CompiledCoverPrompt,
        CoverBriefV2,
        CoverScenePlan,
        PrincipalCharacter,
    )
    from .cover_models import CoverConcept
    from .cover_profiles import (
        resolve_genre_profile,
        resolve_portfolio_treatment,
        resolve_title_typography,
    )
except ImportError:  # pragma: no cover - legacy CLI imports core modules top-level
    from cover_models_v2 import (
        COVER_REPAIR_CODES,
        CompiledCoverPrompt,
        CoverBriefV2,
        CoverScenePlan,
        PrincipalCharacter,
    )
    from cover_models import CoverConcept
    from cover_profiles import (
        resolve_genre_profile,
        resolve_portfolio_treatment,
        resolve_title_typography,
    )


COMPILER_VERSION = "cover-compiler.v7"
MAX_PROMPT_CODEPOINTS = 12_000
_MARKETING_SHORTCUTS = ("viral", "high ctr", "high conversion", "click-through", "masterpiece")
_FORBIDDEN_NEGATIONS = (
    "no",
    "not",
    "without",
    "exclude",
    "excluding",
    "avoid",
    "avoiding",
    "do not include",
    "free of",
)
_NEGATION_SCOPE_BREAKERS = re.compile(r"\b(?:but|however|instead|yet)\b")


def _character_block(character: PrincipalCharacter) -> str:
    age = f"age {character.age}" if character.age is not None else f"visible age phase {character.age_band}"
    appearance = re.sub(
        r"\s*[;,]?\s*ethnicity unspecified\b",
        "",
        character.physical_identity or "only explicitly approved visible traits",
        flags=re.IGNORECASE,
    ).strip(" ;,")
    fields = [
        f"{character.character_id} ({character.name}): {age}.",
        f"Appearance: {appearance}.",
        f"Wardrobe: {character.daily_wardrobe or 'approved everyday clothing'}.",
        (
            f"Expression/action: {character.current_emotional_state or 'approved emotion'}; "
            f"{character.agency_signal}."
        ),
    ]
    return " ".join(fields)


def _gaze_instruction(value: str) -> str:
    """Compact director-produced gaze objects that arrived as JSON-ish text."""
    text = str(value).strip()
    if not (text.startswith("{") and text.endswith("}")):
        return text
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return text
    if not isinstance(parsed, dict):
        return text
    source = str(parsed.get("from_character_id") or "").strip()
    target = str(parsed.get("to_character_id") or "").strip()
    attention = str(parsed.get("visible_attention") or "").strip()
    if not source or not target or not attention:
        return text
    return f"{source} -> {target}: {attention}"


def _evidence_exists(brief: CoverBriefV2, reference: str) -> bool:
    prefix, _, identifier = reference.partition(":")
    if not prefix or not identifier:
        return False
    if prefix == "character":
        return identifier in {item.character_id for item in brief.principal_characters}
    if prefix == "node":
        return identifier in {item.node_id for item in brief.decisive_story_nodes}
    if prefix == "signal":
        return identifier in {item.signal_id for item in brief.secondary_signals}
    return False


def _anchor_uses_forbidden_element(anchor: str, forbidden: str) -> bool:
    text = anchor.casefold()
    target = forbidden.casefold()
    matches = re.finditer(rf"(?<!\w){re.escape(target)}(?!\w)", text)
    for match in matches:
        start = match.start()
        clause_start = max(
            text.rfind(boundary, 0, start)
            for boundary in (".", "!", "?", ";", "\n")
        )
        prefix = text[clause_start + 1:start]
        scope_breakers = tuple(_NEGATION_SCOPE_BREAKERS.finditer(prefix))
        if scope_breakers:
            prefix = prefix[scope_breakers[-1].end():]
        negated = any(
            re.search(rf"\b{re.escape(negation)}\b", prefix)
            for negation in _FORBIDDEN_NEGATIONS
        )
        if not negated:
            return True
    return False


def _centered_title_safe_zone(brief: CoverBriefV2, scene: CoverScenePlan) -> str:
    """Project old free-form title zones onto one unambiguous centered axis."""
    source = str(brief.title_direction.get("preferred_zone") or scene.title_safe_zone)
    folded = source.casefold()
    vertical_zone = next(
        (
            candidate
            for candidate in (
                "upper quarter", "upper third", "top quarter", "top third",
                "middle third", "center third", "lower third", "lower quarter",
                "bottom third", "bottom quarter",
            )
            if candidate in folded
        ),
        "upper third",
    )
    return f"clean {vertical_zone} negative space centered across the horizontal axis"


def _validate_scene(brief: CoverBriefV2, scene: CoverScenePlan) -> None:
    if brief.pending_critical_assumptions():
        raise ValueError("cover prompt cannot compile while critical visual assumptions are pending")
    required = {item.character_id for item in brief.required_characters}
    approved = {item.character_id for item in brief.principal_characters}
    cast = set(scene.cast)
    unapproved = cast - approved
    if unapproved:
        raise ValueError(
            f"cover scene cast contains unapproved characters: {', '.join(sorted(unapproved))}"
        )
    missing = required - cast
    if missing:
        raise ValueError(f"cover scene cast is missing required characters: {', '.join(sorted(missing))}")
    if scene.focal_character_id not in cast:
        raise ValueError("cover scene focal character must be in cast")
    if any(not _evidence_exists(brief, ref) for ref in scene.story_evidence_refs):
        raise ValueError("cover scene contains an unresolved story evidence reference")
    if len(cast) >= 4:
        spatial = f"{scene.blocking} {scene.depth_plan}".casefold()
        if "foreground" not in spatial or not any(
            token in spatial for token in ("middle", "background")
        ):
            raise ValueError(
                "four-plus character covers require foreground and background or middle-plane blocking"
            )
    for forbidden in brief.forbidden_elements:
        if forbidden and any(
            _anchor_uses_forbidden_element(anchor, forbidden)
            for anchor in scene.environment_anchors
        ):
            raise ValueError(f"cover scene uses forbidden element '{forbidden}'")


def _modules(brief: CoverBriefV2, scene: CoverScenePlan) -> dict[str, str]:
    profile = resolve_genre_profile(brief)
    treatment = resolve_portfolio_treatment(scene.visual_hook.hook_type)
    typography = resolve_title_typography(
        profile, hook_type=scene.visual_hook.hook_type,
    )
    title_safe_zone = _centered_title_safe_zone(brief, scene)
    primary_location = scene.location_family or brief.lived_environment.primary_spaces[0]
    relationship_motion = (
        profile.relationship_motion
        if scene.visual_hook.hook_type.casefold() == "relationship_tension"
        else treatment.scene_direction
    )
    cast_ids = set(scene.cast)
    selected_cast = tuple(
        item for item in brief.principal_characters if item.character_id in cast_ids
    )
    focal = next(
        item for item in brief.principal_characters
        if item.character_id == scene.focal_character_id
    )
    cast_lock = "\n".join(_character_block(item) for item in selected_cast)
    if len(selected_cast) >= 4:
        cast_summary = (
            "Use one or two foreground emotional anchors; place the remaining approved cast in "
            "middle/background action that explains the conflict while keeping every selected person distinct."
        )
    else:
        cast_summary = "All selected characters are clear, distinct people in the same continuous scene."
    story_truth = (
        f"Core conflict: {brief.core_conflict}. "
        f"Hero's task: {brief.core_task}. Emotional promise: {brief.emotional_promise}."
    )
    hook = scene.visual_hook
    modules = {
        "ROLE AND OUTPUT": (
            "Create an original live-action theatrical film campaign poster from a believable on-set "
            "publicity still of one decisive scene. Finish as a portrait 2:3 commercial novel cover with "
            f"prestige key-art hierarchy and restraint. This portfolio slot uses {treatment.art_direction} "
            "It looks photographed rather than illustrated, stock-composited, or synthetic."
        ),
        "STORY TRUTH": story_truth,
        "CAST LOCK": f"{cast_lock}\n{cast_summary} Do not infer or invent ethnicity, nationality, age, class, or identity traits.",
        "SINGLE CINEMATIC MOMENT": (
            f"One continuous scene, not a collage. Immediately before: {scene.moment_before}. "
            f"Freeze this action: {scene.frozen_action}. Immediately after: {scene.moment_after}."
        ),
        "HERO SUBJECT AND CORE STORY ATMOSPHERE": (
            f"Hero subject: {focal.name} ({focal.character_id}), whose emotion and choice dominate. "
            "Keep selected principal faces unobstructed, recognizable, and sharply resolved; eyes, hands, "
            "silhouettes, and story gestures remain clear. "
            "Use close, character-led framing rather than tiny people, back-turned principals, covered faces, "
            "fashion poses, or an overpowering environment. Make the core conflict physical through expression, "
            "body distance, unfinished action, motivated light, lived setting, and the primary prop. At mobile "
            "size, principal people read before the setting and relationship tension before secondary detail."
        ),
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY": (
            f"Portfolio slot: {treatment.portfolio_slot}. Composition family: "
            f"{treatment.composition_family}. {treatment.composition_direction} "
            f"Scene family: {treatment.scene_family}. {treatment.scene_direction} "
            "Give the focal character a story-supported action rather than a generic mood pose. Keep one "
            "continuous photographic moment, not a generic sad portrait, equal-weight group portrait, split "
            "composition, or collage; keep every relationship action within the approved evidence."
        ),
        "RELATIONSHIP BLOCKING": (
            f"{scene.blocking}. Gaze and gesture logic: {'; '.join(_gaze_instruction(item) for item in scene.gaze_graph)}. "
            "Make the power relationship understandable from distance, posture, eye line, and one unfinished "
            "action. When multiple depth planes are used, the foreground response and background cause must "
            "remain readable together at thumbnail size."
        ),
        "LIVED ENVIRONMENT AND PRIMARY PROP": (
            f"Environment anchors: {', '.join(scene.environment_anchors)}. "
            f"Primary lived setting for this portfolio slot: {primary_location}. "
            f"Economic signals: {', '.join(brief.lived_environment.economic_signals) or 'none specified'}. "
            f"Scene truths: {'; '.join(brief.lived_environment.environment_truths) or 'none specified'}. "
            f"Show credible material, scale, wear, and physical contact. Dominant story prop: {scene.primary_prop}, "
            "visibly handled or observed."
        ),
        "GENRE EMOTION": (
            f"Profile: {profile.primary_genre}/{profile.submode}. Temperature: {profile.emotional_temperature}. "
            f"Viewer feeling: {profile.desired_viewer_feeling}. Relationship motion: {relationship_motion}. "
            f"Portfolio emotion: {treatment.emotion_register}. {treatment.emotion_direction} "
            f"Avoid: {', '.join(profile.prohibited_shortcuts)}."
        ),
        "CAMERA, DEPTH AND MOTIVATED LIGHTING": (
            f"{scene.shot_scale}, {scene.camera_height}, {scene.lens}. {scene.depth_plan}. "
            f"Light comes from {scene.motivated_lighting}. Color script: {scene.color_script}. "
            f"Art style: {treatment.art_style}. {treatment.camera_direction} Use natural behavior and physically consistent shadows, "
            "reflections, perspective, and contact."
        ),
        "MOBILE COMMERCIAL COVER OBJECTIVE": (
            f"At {brief.commercial_visual_goal.thumbnail_reference_width}x{brief.commercial_visual_goal.thumbnail_reference_height}, "
            f"first glance reveals {hook.first_glance_subject}. Open question: {hook.open_question}. "
            f"Truthfully promise {hook.reader_promise}. "
            f"VISUAL HOOK: {hook.hook_type}; expected thumbnail read: {hook.expected_thumbnail_read}."
        ),
        "TITLE AND SAFE ZONE": (
            f"Render the exact title \"{brief.title}\" exactly once in {brief.language}. No other text. "
            f"Place it on the horizontal center axis within {title_safe_zone}. Keep faces, hands, and the "
            "primary prop outside the lettering area. The title remains readable at mobile thumbnail size."
        ),
        "TITLE ART DIRECTION": (
            f"Typography system: {treatment.typography_style}. Voice: {typography.letterform_voice}. "
            "Build a cinematic centered title lockup "
            f"on the horizontal center axis inside the approved safe zone. Hierarchy: {typography.hierarchy}. Keep supporting words smaller "
            "than story-bearing words while preserving exact spelling, order, and reading path. Use deliberate "
            "line breaks and optical centering, not an equal-size stack or mechanically stacked text box. "
            f"Expressive detail: {typography.expressive_detail}. Use a scene-palette color with strong thumbnail contrast. "
            f"Avoid {', '.join(typography.prohibited_shortcuts)}."
        ),
        "PHOTOREALISM REQUIREMENTS": (
            "Natural skin texture, pores, age-appropriate facial structure and fine lines, subtle asymmetry, "
            "anatomically correct hands, believable hair strands, fabric weight and wrinkles, lived-in surfaces, "
            "realistic lens rendering, and restrained cinematic color grading. Preserve every stated age and social reality."
        ),
        "COMPACT FAILURE EXCLUSIONS": (
            "Exclude illustration, painterly rendering, 3D render, plastic or wax skin, beauty-filter faces, "
            "fashion-catalog posing, duplicated people, merged faces, age mismatch, extra fingers or limbs, "
            "impossible reflections, floating props, unrelated luxury, collage, split-screen, multiple scenes, "
            "real landmarks, logos, watermarks, subtitles, taglines, author text, movie billing blocks, "
            "release dates, studio marks, and any text other than the exact title."
        ),
    }
    return modules


def _render(modules: dict[str, str]) -> str:
    return "\n\n".join(f"{name}\n{body}" for name, body in modules.items())


def _validate_prompt(text: str) -> None:
    if len(text) > MAX_PROMPT_CODEPOINTS:
        raise ValueError(f"cover prompt exceeds {MAX_PROMPT_CODEPOINTS} Unicode code points")
    folded = text.casefold()
    for token in _MARKETING_SHORTCUTS:
        for match in re.finditer(rf"\b{re.escape(token)}\b", folded):
            clause_start = max(
                folded.rfind(boundary, 0, match.start())
                for boundary in (".", "!", "?", ";", "\n")
            )
            prefix = folded[clause_start + 1:match.start()]
            scope_breakers = tuple(_NEGATION_SCOPE_BREAKERS.finditer(prefix))
            if scope_breakers:
                prefix = prefix[scope_breakers[-1].end():]
            if any(
                re.search(rf"\b{re.escape(negation)}\b", prefix)
                for negation in _FORBIDDEN_NEGATIONS
            ):
                continue
            raise ValueError("cover prompt contains a non-executable marketing shortcut")


def compile_cover_prompt(
    brief: CoverBriefV2,
    scene: CoverScenePlan,
    *,
    compiler_version: str = COMPILER_VERSION,
) -> CompiledCoverPrompt:
    _validate_scene(brief, scene)
    modules = _modules(brief, scene)
    text = _render(modules)
    _validate_prompt(text)
    return CompiledCoverPrompt(text=text, compiler_version=compiler_version, modules=modules)


def scene_to_cover_concept(
    brief: CoverBriefV2,
    scene: CoverScenePlan,
    *,
    compiler_version: str = COMPILER_VERSION,
) -> CoverConcept:
    compiled = compile_cover_prompt(brief, scene, compiler_version=compiler_version)
    return CoverConcept(
        concept_id=scene.concept_id,
        visual_strategy=scene.visual_strategy,
        focal_scene=scene.frozen_action,
        composition=scene.blocking,
        palette=scene.color_script,
        secondary_signal=scene.primary_prop,
        title_treatment=scene.title_safe_zone,
        generation_prompt=compiled.text,
        scene_plan=scene.to_dict(),
    )


_REPAIR_MODULES = {
    "age_mismatch": ("CAST LOCK",),
    "missing_character": ("CAST LOCK", "RELATIONSHIP BLOCKING"),
    "generic_ai_face": (
        "HERO SUBJECT AND CORE STORY ATMOSPHERE",
        "PHOTOREALISM REQUIREMENTS",
        "CAMERA, DEPTH AND MOTIVATED LIGHTING",
    ),
    "weak_story_action": (
        "SINGLE CINEMATIC MOMENT",
        "HERO SUBJECT AND CORE STORY ATMOSPHERE",
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY",
    ),
    "genre_drift": ("GENRE EMOTION",),
    "thumbnail_clutter": (
        "HERO SUBJECT AND CORE STORY ATMOSPHERE",
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY",
        "RELATIONSHIP BLOCKING",
        "MOBILE COMMERCIAL COVER OBJECTIVE",
    ),
    "reader_promise_mismatch": (
        "MOBILE COMMERCIAL COVER OBJECTIVE",
        "STORY TRUTH",
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY",
    ),
    "title_failure": ("TITLE AND SAFE ZONE", "TITLE ART DIRECTION"),
}
assert set(_REPAIR_MODULES) == COVER_REPAIR_CODES


def compile_repair_prompt(
    brief: CoverBriefV2,
    scene: CoverScenePlan,
    prior: CompiledCoverPrompt,
    repair_codes: Sequence[str],
) -> CompiledCoverPrompt:
    unknown = [code for code in repair_codes if code not in _REPAIR_MODULES]
    if unknown:
        raise ValueError(f"unknown cover repair code: {', '.join(unknown)}")
    fresh = compile_cover_prompt(brief, scene, compiler_version=prior.compiler_version)
    modules = dict(prior.modules)
    for code in repair_codes:
        for module in _REPAIR_MODULES[code]:
            modules[module] = fresh.modules[module] + f" Repair focus: {code.replace('_', ' ')}."
    text = _render(modules)
    _validate_prompt(text)
    return replace(prior, text=text, revision=prior.revision + 1, modules=modules)
