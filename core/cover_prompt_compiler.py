"""Pure deterministic compiler for canon-bound cinematic cover prompts."""

from __future__ import annotations

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
    from .cover_profiles import resolve_genre_profile
except ImportError:  # pragma: no cover - legacy CLI imports core modules top-level
    from cover_models_v2 import (
        COVER_REPAIR_CODES,
        CompiledCoverPrompt,
        CoverBriefV2,
        CoverScenePlan,
        PrincipalCharacter,
    )
    from cover_models import CoverConcept
    from cover_profiles import resolve_genre_profile


COMPILER_VERSION = "cover-compiler.v2"
MAX_PROMPT_CODEPOINTS = 12_000
_MARKETING_SHORTCUTS = ("viral", "high ctr", "high conversion", "click-through", "masterpiece")


def _character_block(character: PrincipalCharacter) -> str:
    age = f"age {character.age}" if character.age is not None else f"visible age phase {character.age_band}"
    fields = [
        f"{character.character_id} ({character.name}): {age}.",
        f"Identity: {character.physical_identity or 'only the explicitly approved physical traits'}.",
        f"Occupation/status: {character.occupation_and_status}.",
        f"Daily wardrobe: {character.daily_wardrobe or 'consistent with the approved lived environment'}.",
        f"Lived environment: {character.lived_environment or 'the approved story setting'}.",
        f"Current emotion: {character.current_emotional_state or 'the approved emotional state'}.",
        f"Visible agency: {character.agency_signal}.",
    ]
    return " ".join(fields)


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


def _validate_scene(brief: CoverBriefV2, scene: CoverScenePlan) -> None:
    if brief.pending_critical_assumptions():
        raise ValueError("cover prompt cannot compile while critical visual assumptions are pending")
    required = {item.character_id for item in brief.required_characters}
    cast = set(scene.cast)
    missing = required - cast
    if missing:
        raise ValueError(f"cover scene cast is missing required characters: {', '.join(sorted(missing))}")
    if scene.focal_character_id not in cast:
        raise ValueError("cover scene focal character must be in cast")
    if any(not _evidence_exists(brief, ref) for ref in scene.story_evidence_refs):
        raise ValueError("cover scene contains an unresolved story evidence reference")
    if len(required) <= 3:
        if cast != required:
            raise ValueError("two or three protagonist covers may not add unapproved cast members")
    elif len(required) >= 4 and not any(
        token in scene.blocking.casefold() for token in ("foreground", "middle", "background")
    ):
        raise ValueError("four-plus protagonist covers require foreground and middle/background blocking")
    for forbidden in brief.forbidden_elements:
        if forbidden and forbidden.casefold() in scene.environment_anchors.__repr__().casefold():
            raise ValueError(f"cover scene uses forbidden element '{forbidden}'")


def _modules(brief: CoverBriefV2, scene: CoverScenePlan) -> dict[str, str]:
    profile = resolve_genre_profile(brief)
    required = brief.required_characters
    cast_lock = "\n".join(_character_block(item) for item in required if item.character_id in scene.cast)
    if len(required) >= 4:
        cast_summary = (
            "Foreground visual axis uses two or three required characters; every remaining "
            "required character stays in the same continuous scene through middle/background action."
        )
    else:
        cast_summary = "All required characters are clear, distinct people in the same continuous scene."
    story_truth = (
        f"Genre: {brief.genre}. Audience: {brief.target_audience}. Core conflict: {brief.core_conflict}. "
        f"Use only approved fictional story facts: {brief.core_task}; {brief.emotional_promise}."
    )
    hook = scene.visual_hook
    modules = {
        "ROLE AND OUTPUT": (
            "Create an original live-action cinematic editorial photograph as a finished portrait "
            "2:3 commercial novel cover. The final image must look photographed, not illustrated "
            "or synthetically rendered."
        ),
        "STORY TRUTH": story_truth,
        "CAST LOCK": f"{cast_lock}\n{cast_summary} Do not infer or invent ethnicity, nationality, age, class, or identity traits.",
        "SINGLE CINEMATIC MOMENT": (
            f"One continuous scene, not a collage. Immediately before: {scene.moment_before}. "
            f"Freeze this action: {scene.frozen_action}. Immediately after: {scene.moment_after}."
        ),
        "RELATIONSHIP BLOCKING": (
            f"{scene.blocking}. Gaze and gesture logic: {'; '.join(scene.gaze_graph)}. "
            "Make the power relationship understandable from distance, posture, eye line, and one unfinished action."
        ),
        "LIVED ENVIRONMENT AND PRIMARY PROP": (
            f"Environment anchors: {', '.join(scene.environment_anchors)}. "
            f"The approved lived spaces are {', '.join(brief.lived_environment.primary_spaces)}; "
            f"character lived environments: {', '.join(item.lived_environment for item in required if item.lived_environment) or 'none specified'}. "
            f"economic signals: {', '.join(brief.lived_environment.economic_signals) or 'none specified'}. "
            f"Show lived material, scale, economic reality, and physical contact. The only dominant story prop is {scene.primary_prop}; it is handled or observed."
        ),
        "GENRE EMOTION": (
            f"Profile: {profile.primary_genre}/{profile.submode}. Temperature: {profile.emotional_temperature}. "
            f"Viewer feeling: {profile.desired_viewer_feeling}. Relationship motion: {profile.relationship_motion}. "
            f"Avoid: {', '.join(profile.prohibited_shortcuts)}."
        ),
        "CAMERA, DEPTH AND MOTIVATED LIGHTING": (
            f"{scene.shot_scale}, {scene.camera_height}, {scene.lens}. {scene.depth_plan}. "
            f"Light comes from {scene.motivated_lighting}. Color script: {scene.color_script}. "
            "Use physically consistent shadows, reflections, perspective, and contact between hands, clothing, props, and furniture."
        ),
        "MOBILE COMMERCIAL COVER OBJECTIVE": (
            f"At {brief.commercial_visual_goal.thumbnail_reference_width}x{brief.commercial_visual_goal.thumbnail_reference_height}, "
            f"first glance reveals {hook.first_glance_subject}. Open question: {hook.open_question}. "
            f"Recognition anchor: {hook.identity_anchor}. Truthfully promise {hook.reader_promise}. "
            f"VISUAL HOOK: {hook.hook_type}; expected thumbnail read: {hook.expected_thumbnail_read}."
        ),
        "TITLE AND SAFE ZONE": (
            f"Render the exact title \"{brief.title}\" exactly once in {brief.language}. No other text. "
            f"Keep faces, hands, and the primary prop outside {scene.title_safe_zone}. The title remains readable at mobile thumbnail size."
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
            "real landmarks, logos, watermarks, subtitles, taglines, author text, and any text other than the exact title."
        ),
    }
    return modules


def _render(modules: dict[str, str]) -> str:
    return "\n\n".join(f"{name}\n{body}" for name, body in modules.items())


def _validate_prompt(text: str) -> None:
    if len(text) > MAX_PROMPT_CODEPOINTS:
        raise ValueError(f"cover prompt exceeds {MAX_PROMPT_CODEPOINTS} Unicode code points")
    folded = text.casefold()
    if any(token in folded for token in _MARKETING_SHORTCUTS):
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
    "generic_ai_face": ("PHOTOREALISM REQUIREMENTS", "CAMERA, DEPTH AND MOTIVATED LIGHTING"),
    "weak_story_action": ("SINGLE CINEMATIC MOMENT",),
    "genre_drift": ("GENRE EMOTION",),
    "thumbnail_clutter": ("RELATIONSHIP BLOCKING", "MOBILE COMMERCIAL COVER OBJECTIVE"),
    "reader_promise_mismatch": ("MOBILE COMMERCIAL COVER OBJECTIVE", "STORY TRUTH"),
    "title_failure": ("TITLE AND SAFE ZONE",),
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
