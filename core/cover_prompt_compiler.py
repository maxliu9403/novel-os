"""Pure deterministic compiler for canon-bound cinematic cover prompts."""

from __future__ import annotations

import ast
import re
from dataclasses import replace
from typing import Sequence

try:
    from .cover_design import BookVisualIdentity, VisualEvidenceLedger
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
    from cover_design import BookVisualIdentity, VisualEvidenceLedger
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


COMPILER_VERSION = "cover-compiler.v8"
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


def _evidence_exists(
    brief: CoverBriefV2,
    reference: str,
    evidence_ledger: VisualEvidenceLedger | None = None,
) -> bool:
    prefix, _, identifier = reference.partition(":")
    if not prefix or not identifier:
        return False
    if prefix == "character":
        return identifier in {item.character_id for item in brief.principal_characters}
    if prefix == "node":
        return identifier in {item.node_id for item in brief.decisive_story_nodes}
    if prefix == "signal":
        return identifier in {item.signal_id for item in brief.secondary_signals}
    if prefix == "evidence" and evidence_ledger is not None:
        return evidence_ledger.by_id(identifier) is not None
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


def _validate_scene(
    brief: CoverBriefV2,
    scene: CoverScenePlan,
    evidence_ledger: VisualEvidenceLedger | None = None,
) -> None:
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
    adaptive = bool(scene.focal_strategy)
    missing = required - cast
    if missing and not adaptive:
        raise ValueError(f"cover scene cast is missing required characters: {', '.join(sorted(missing))}")
    if adaptive:
        reader_anchor_id = brief.reader_anchor_character.character_id
        if not cast:
            raise ValueError("adaptive cover scene requires a visible principal human subject")
        if reader_anchor_id not in cast:
            raise ValueError("adaptive cover scene must include the reader-anchor protagonist")
        if scene.focal_character_id != reader_anchor_id:
            raise ValueError("adaptive cover scene must keep the reader-anchor protagonist focal")
    if cast and scene.focal_character_id not in cast:
        raise ValueError("cover scene focal character must be in cast")
    if any(
        not _evidence_exists(brief, ref, evidence_ledger)
        for ref in scene.story_evidence_refs
    ):
        raise ValueError("cover scene contains an unresolved story evidence reference")
    if not adaptive and len(cast) >= 4:
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


def _legacy_modules(brief: CoverBriefV2, scene: CoverScenePlan) -> dict[str, str]:
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


def _without_title(value: str, title: str) -> str:
    if not title:
        return value
    return re.sub(re.escape(title), "the novel", value, flags=re.IGNORECASE)


def _selected_evidence(
    brief: CoverBriefV2,
    scene: CoverScenePlan,
    evidence_ledger: VisualEvidenceLedger | None,
) -> str:
    values: list[str] = []
    for reference in scene.story_evidence_refs:
        prefix, _, identifier = reference.partition(":")
        if prefix == "character":
            item = next((item for item in brief.principal_characters if item.character_id == identifier), None)
            if item is not None:
                values.append(f"{reference}: {item.name}, {item.agency_signal}")
        elif prefix == "node":
            item = next((item for item in brief.decisive_story_nodes if item.node_id == identifier), None)
            if item is not None:
                values.append(f"{reference}: {item.description}")
        elif prefix == "signal":
            item = next((item for item in brief.secondary_signals if item.signal_id == identifier), None)
            if item is not None:
                values.append(f"{reference}: {item.description}; {item.story_function}")
        elif prefix == "evidence" and evidence_ledger is not None:
            item = evidence_ledger.by_id(identifier)
            if item is not None:
                values.append(
                    f"{reference} [{item.source_type}/{item.spoiler_level}]: {item.summary[:300]}"
                )
    return " | ".join(_without_title(value, brief.title) for value in values[:5])


def _adaptive_modules(
    brief: CoverBriefV2,
    scene: CoverScenePlan,
    *,
    visual_identity: BookVisualIdentity | None,
    evidence_ledger: VisualEvidenceLedger | None,
) -> dict[str, str]:
    identity = visual_identity or BookVisualIdentity.from_brief(brief)
    profile = resolve_genre_profile(brief)
    cast_ids = set(scene.cast)
    selected_cast = tuple(
        item for item in brief.principal_characters if item.character_id in cast_ids
    )
    cast_lock = "\n".join(_character_block(item) for item in selected_cast)
    if selected_cast:
        reader_anchor = brief.reader_anchor_character
        subject_direction = (
            f"Use exactly the approved cast above. The focal character is {scene.focal_character_id}. "
            f"Keep {reader_anchor.name} ({reader_anchor.character_id}) as a clear, emotionally active human anchor. "
            "Choose character scale from the design hypothesis, but keep the protagonist's face, posture, emotion, "
            "and story action readable at mobile size rather than tiny, decorative, hidden, or anonymous. Objects, "
            "environment, or typography may lead the concept while remaining causally connected to that action. "
            "Every visible face, hand, clothing, age, and gesture remains clear and canon-faithful."
        )
    else:
        cast_lock = "This direction intentionally contains no visible characters."
        subject_direction = (
            "Carry human consequence through the evidence-supported object, absence, spatial trace, or title "
            "interaction. Do not add silhouettes, faces, hands, or anonymous figures merely to make the cover busy."
        )
    title_zone = scene.title_safe_zone
    location = scene.location_family or brief.lived_environment.primary_spaces[0]
    evidence = _selected_evidence(brief, scene, evidence_ledger)
    gaze = "; ".join(_gaze_instruction(item) for item in scene.gaze_graph) or "not applicable to this character-free design"
    blacklist = tuple(dict.fromkeys((*identity.cliche_blacklist, *profile.prohibited_shortcuts)))
    modules = {
        "ROLE AND OUTPUT": (
            "You are Image2 acting as the lead book-cover designer, not a scene-rendering operator. Produce a "
            "finished portrait 2:3 "
            "commercial novel cover whose visual solution could belong only to this story. Interpret the evidence "
            f"through this direction's chosen medium: {scene.art_style}. Make expert composition, material, camera, "
            "negative-space, color, and lettering decisions inside the canon locks below."
        ),
        "BOOK VISUAL IDENTITY": (
            f"Design thesis: {_without_title(identity.design_thesis, brief.title)}. Emotional contradiction: "
            f"{identity.dominant_emotional_contradiction}. Story signatures: {', '.join(identity.story_signatures)}. "
            f"Visual grammar: {', '.join(identity.visual_grammar)}. Material language: "
            f"{', '.join(identity.material_language)}. Spatial logic: {identity.spatial_logic}. "
            f"Uniqueness anchors: {', '.join(identity.uniqueness_anchors)}."
        ),
        "STORY TRUTH": (
            f"Core conflict: {brief.core_conflict}. Hero's task: {brief.core_task}. "
            f"Emotional promise: {brief.emotional_promise}. Evidence interpretation: {scene.evidence_summary}."
        ),
        "EVIDENCE ANCHORS": evidence or "Use the approved brief evidence references exactly as supplied.",
        "CAST LOCK": f"{cast_lock}\n{identity.cast_policy} {subject_direction} Never invent identity traits.",
        "SINGLE CINEMATIC MOMENT": (
            f"This may be a literal instant or one coherent conceptual cover, never a collage. Immediately before: "
            f"{scene.moment_before}. Designed focal event: {scene.frozen_action}. Immediately after: {scene.moment_after}. "
            "Show the approved protagonist physically performing or reacting within this specific story moment; "
            "the person, setting, evidence object, and emotional consequence must read as one scene."
        ),
        "HERO SUBJECT AND CORE STORY ATMOSPHERE": (
            f"Focal strategy: {scene.focal_strategy}. Visual signature: {scene.visual_signature}. "
            f"First read: {scene.visual_hook.first_glance_subject}. {subject_direction} Make the core conflict and "
            "emotional contradiction legible without relying on generic sadness, glamour, or a stock genre pose."
        ),
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY": (
            f"Composition family: {scene.composition_family}. Scene family: {scene.scene_family}. "
            f"Design rationale: {scene.design_rationale}. Blocking and hierarchy: {scene.blocking}. "
            "Use the topology best suited to this concept; foreground/background causality, a dominant face, or a "
            "multi-character tableau appears only when this plan specifically calls for it."
        ),
        "RELATIONSHIP BLOCKING": (
            f"Visible attention, gesture, or reading-path logic: {gaze}. Depth and hierarchy: {scene.depth_plan}. "
            "Connect all elements through one deliberate reading path at thumbnail size."
        ),
        "LIVED ENVIRONMENT AND PRIMARY PROP": (
            f"Primary field or setting: {location}. Environment anchors: {', '.join(scene.environment_anchors)}. "
            f"Primary story-bearing prop or intentional absence: {scene.primary_prop}. Show credible material, wear, "
            "scale, and story function rather than decorative symbolism."
        ),
        "GENRE EMOTION": (
            f"Genre: {brief.genre}. Target emotion: {scene.emotion_register}; "
            f"{scene.visual_hook.target_emotion}. Palette logic: {identity.palette_logic}. "
            f"Color script for this direction: {scene.color_script}. Avoid: {', '.join(blacklist)}."
        ),
        "CAMERA, DEPTH AND MOTIVATED LIGHTING": (
            f"Chosen viewing system: {scene.shot_scale}, {scene.camera_height}, {scene.lens}. "
            f"Lighting logic: {identity.lighting_logic}. Direction-specific light: {scene.motivated_lighting}. "
            f"Execute {scene.art_style} consistently rather than drifting into a generic synthetic composite."
        ),
        "MOBILE COMMERCIAL COVER OBJECTIVE": (
            f"At {brief.commercial_visual_goal.thumbnail_reference_width}x{brief.commercial_visual_goal.thumbnail_reference_height}, "
            f"the first read is {scene.visual_hook.expected_thumbnail_read}. Open question: "
            f"{scene.visual_hook.open_question}. Truthful promise: {scene.visual_hook.reader_promise}. "
            f"Novelty rationale: {scene.novelty_rationale}."
        ),
        "TITLE AND SAFE ZONE": (
            f"Render the exact title \"{brief.title}\" exactly once in {brief.language}. No other text. "
            f"Use this title field: {title_zone}. Integrate lettering with the composition while preserving exact "
            "spelling, word order, legibility, and clear separation from critical story evidence."
        ),
        "TITLE ART DIRECTION": (
            f"Book-level typography voice: {identity.typography_voice}. Direction system: "
            f"{scene.typography_style}. Rationale: {scene.typography_rationale}. Let title semantics affect scale, "
            "spacing, rhythm, material, or placement. Keep the result authored and readable rather than a generic "
            "centered text overlay; do not replace or distort letters."
        ),
        "MEDIUM FIDELITY REQUIREMENTS": (
            f"Medium fidelity requirement: execute {scene.art_style} with professional publishing craft. "
            "For photography, preserve natural skin, anatomy, lens behavior, light, and contact. For illustration, "
            "graphic, object-led, or typographic work, preserve intentional edges, materials, perspective, print "
            "texture, and coherent human anatomy wherever people appear."
        ),
        "COMPACT FAILURE EXCLUSIONS": (
            "Exclude duplicated or merged people, anatomy errors, age mismatch, impossible perspective, floating "
            "props, unmotivated luxury, unsupported symbols, accidental collage, unrelated spectacle, real landmarks, "
            "logos, watermarks, subtitles, taglines, author text, billing blocks, and any text except the exact title. "
            f"Spoiler boundary: {', '.join(identity.spoiler_boundary)}."
        ),
    }
    return {
        name: _without_title(body, brief.title) if name != "TITLE AND SAFE ZONE" else body
        for name, body in modules.items()
    }


def _modules(
    brief: CoverBriefV2,
    scene: CoverScenePlan,
    *,
    visual_identity: BookVisualIdentity | None = None,
    evidence_ledger: VisualEvidenceLedger | None = None,
) -> dict[str, str]:
    if scene.focal_strategy:
        return _adaptive_modules(
            brief, scene, visual_identity=visual_identity, evidence_ledger=evidence_ledger,
        )
    return _legacy_modules(brief, scene)


def _render(modules: dict[str, str]) -> str:
    return "\n\n".join(f"{name}\n{body}" for name, body in modules.items())


_ADAPTIVE_MODULE_BUDGETS = {
    "ROLE AND OUTPUT": 550,
    "BOOK VISUAL IDENTITY": 850,
    "STORY TRUTH": 600,
    "EVIDENCE ANCHORS": 900,
    "CAST LOCK": 1100,
    "SINGLE CINEMATIC MOMENT": 600,
    "HERO SUBJECT AND CORE STORY ATMOSPHERE": 700,
    "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY": 750,
    "RELATIONSHIP BLOCKING": 500,
    "LIVED ENVIRONMENT AND PRIMARY PROP": 600,
    "GENRE EMOTION": 500,
    "CAMERA, DEPTH AND MOTIVATED LIGHTING": 550,
    "MOBILE COMMERCIAL COVER OBJECTIVE": 500,
    "TITLE AND SAFE ZONE": 600,
    "TITLE ART DIRECTION": 650,
    "PHOTOREALISM REQUIREMENTS": 550,
    "MEDIUM FIDELITY REQUIREMENTS": 550,
    "COMPACT FAILURE EXCLUSIONS": 550,
}


def _compact_adaptive_modules(modules: dict[str, str]) -> dict[str, str]:
    compacted: dict[str, str] = {}
    for name, body in modules.items():
        limit = _ADAPTIVE_MODULE_BUDGETS.get(name, 600)
        if len(body) <= limit:
            compacted[name] = body
            continue
        clipped = body[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
        compacted[name] = f"{clipped}."
    return compacted


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
    visual_identity: BookVisualIdentity | None = None,
    evidence_ledger: VisualEvidenceLedger | None = None,
) -> CompiledCoverPrompt:
    _validate_scene(brief, scene, evidence_ledger)
    modules = _modules(
        brief, scene, visual_identity=visual_identity, evidence_ledger=evidence_ledger,
    )
    text = _render(modules)
    if scene.focal_strategy and len(text) > MAX_PROMPT_CODEPOINTS:
        modules = _compact_adaptive_modules(modules)
        text = _render(modules)
    _validate_prompt(text)
    return CompiledCoverPrompt(text=text, compiler_version=compiler_version, modules=modules)


def scene_to_cover_concept(
    brief: CoverBriefV2,
    scene: CoverScenePlan,
    *,
    compiler_version: str = COMPILER_VERSION,
    visual_identity: BookVisualIdentity | None = None,
    evidence_ledger: VisualEvidenceLedger | None = None,
) -> CoverConcept:
    compiled = compile_cover_prompt(
        brief, scene, compiler_version=compiler_version,
        visual_identity=visual_identity, evidence_ledger=evidence_ledger,
    )
    scene_payload = scene.to_dict()
    if visual_identity is not None:
        scene_payload["_visual_identity"] = visual_identity.to_dict()
    if evidence_ledger is not None:
        scene_payload["_evidence_ledger"] = evidence_ledger.to_dict()
    return CoverConcept(
        concept_id=scene.concept_id,
        visual_strategy=scene.visual_strategy,
        focal_scene=scene.frozen_action,
        composition=scene.blocking,
        palette=scene.color_script,
        secondary_signal=scene.primary_prop,
        title_treatment=scene.title_safe_zone,
        generation_prompt=compiled.text,
        scene_plan=scene_payload,
    )


_REPAIR_MODULES = {
    "age_mismatch": ("CAST LOCK",),
    "missing_character": ("CAST LOCK", "RELATIONSHIP BLOCKING"),
    "generic_ai_face": (
        "HERO SUBJECT AND CORE STORY ATMOSPHERE",
        "PHOTOREALISM REQUIREMENTS",
        "MEDIUM FIDELITY REQUIREMENTS",
        "CAMERA, DEPTH AND MOTIVATED LIGHTING",
    ),
    "weak_story_action": (
        "SINGLE CINEMATIC MOMENT",
        "HERO SUBJECT AND CORE STORY ATMOSPHERE",
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY",
        "EVIDENCE ANCHORS",
    ),
    "genre_drift": ("BOOK VISUAL IDENTITY", "GENRE EMOTION"),
    "thumbnail_clutter": (
        "HERO SUBJECT AND CORE STORY ATMOSPHERE",
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY",
        "RELATIONSHIP BLOCKING",
        "MOBILE COMMERCIAL COVER OBJECTIVE",
    ),
    "reader_promise_mismatch": (
        "MOBILE COMMERCIAL COVER OBJECTIVE",
        "STORY TRUTH",
        "BOOK VISUAL IDENTITY",
        "EVIDENCE ANCHORS",
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
    *,
    visual_identity: BookVisualIdentity | None = None,
    evidence_ledger: VisualEvidenceLedger | None = None,
) -> CompiledCoverPrompt:
    unknown = [code for code in repair_codes if code not in _REPAIR_MODULES]
    if unknown:
        raise ValueError(f"unknown cover repair code: {', '.join(unknown)}")
    fresh = compile_cover_prompt(
        brief, scene, compiler_version=prior.compiler_version,
        visual_identity=visual_identity, evidence_ledger=evidence_ledger,
    )
    modules = dict(prior.modules)
    for code in repair_codes:
        for module in _REPAIR_MODULES[code]:
            if module in fresh.modules:
                modules[module] = fresh.modules[module] + f" Repair focus: {code.replace('_', ' ')}."
    text = _render(modules)
    _validate_prompt(text)
    return replace(prior, text=text, revision=prior.revision + 1, modules=modules)
