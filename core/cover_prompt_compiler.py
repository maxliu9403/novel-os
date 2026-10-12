"""Pure deterministic compiler for canon-bound cinematic cover prompts."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import replace
from typing import Any, Sequence

try:
    from .cover_campaign import CoverCampaignPlan, validate_campaign
    from .cover_story_policy import STORY_POLICY_VERSION, story_cast_policy
    from .cover_render_policy import PHOTOGRAPHIC_RENDER_CONTRACT, HUMAN_PERFORMANCE_CONTRACT, CAMPAIGN_HUMAN_PERFORMANCE_CONTRACT, photographic_medium_issue
    from .cover_design import BookVisualIdentity, VisualEvidenceLedger
    from .cover_models_v2 import (
        COVER_REPAIR_CODES,
        CompiledCoverPrompt,
        CoverBriefV2,
        CoreConflictVisualContract,
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
    from cover_campaign import CoverCampaignPlan, validate_campaign
    from cover_story_policy import STORY_POLICY_VERSION, story_cast_policy
    from cover_render_policy import PHOTOGRAPHIC_RENDER_CONTRACT, HUMAN_PERFORMANCE_CONTRACT, CAMPAIGN_HUMAN_PERFORMANCE_CONTRACT, photographic_medium_issue
    from cover_design import BookVisualIdentity, VisualEvidenceLedger
    from cover_models_v2 import (
        COVER_REPAIR_CODES,
        CompiledCoverPrompt,
        CoverBriefV2,
        CoreConflictVisualContract,
        CoverScenePlan,
        PrincipalCharacter,
    )
    from cover_models import CoverConcept
    from cover_profiles import (
        resolve_genre_profile,
        resolve_portfolio_treatment,
        resolve_title_typography,
    )


COMPILER_VERSION = "cover-compiler.v17"
COMMERCIAL_COMPILER_VERSION = "cover-compiler.v18"
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
    age = (
        f"age {character.age}"
        if character.age is not None
        else f"visible age phase {character.age_band or 'unspecified; infer conservatively for this cover only'}"
    )
    appearance = re.sub(
        r"\s*[;,]?\s*ethnicity unspecified\b",
        "",
        character.physical_identity or "unspecified appearance; propose a conservative story-consistent visual",
        flags=re.IGNORECASE,
    ).strip(" ;,")
    fields = [
        f"{character.character_id} ({character.name}): {age}.",
            f"Appearance: {appearance}.",
        f"Wardrobe: {character.daily_wardrobe or 'unspecified; use conservative story-consistent everyday clothing'}.",
        (
            f"Character context: {character.current_emotional_state or 'approved emotion'}; "
            f"{character.agency_signal}."
        ),
    ]
    return " ".join(fields)


def _visual_inference_instruction(brief: CoverBriefV2) -> str:
    inferred = brief.model_inferred_assumptions()
    if not inferred:
        return (
            "Do not infer or invent ethnicity, nationality, relationships, or core story facts. "
            "Preserve every source-established identity and age."
        )
    fields = ", ".join(item.field for item in inferred[:8])
    return (
        "Do not infer or invent ethnicity, nationality, relationships, or core story facts. "
        "For unspecified appearance-only fields, propose a conservative, age-appropriate visual for this cover "
        "and keep it provisional; these model-inferred details must not be written back as story canon. "
        f"Currently unspecified visual fields: {fields}."
    )


def _story_constraints_instruction(brief: CoverBriefV2) -> str:
    """Render only the approved scene's story boundaries.

    Candidate IDs, free-form preferences, and selection hints belong to the
    director/portfolio request.  They must never be passed to an image model
    as a second design brief.
    """
    constraints = brief.cover_constraints
    if constraints is None:
        return ""
    parts = ["Author-confirmed cover constraints (filter only; do not invent a second cover plan):"]
    if constraints.must_preserve_story_facts:
        parts.append("Preserve story facts: " + "; ".join(constraints.must_preserve_story_facts) + ".")
    if constraints.must_not_show:
        parts.append("Do not depict: " + "; ".join(constraints.must_not_show) + ".")
    if constraints.spoiler_ceiling:
        parts.append("Spoiler ceiling: " + constraints.spoiler_ceiling)
    return " ".join(parts)


def _author_story_boundaries(brief: CoverBriefV2) -> str:
    """Return the non-negotiable source boundary as a protected prompt module."""
    constraints = brief.cover_constraints
    if constraints is None or not (
        constraints.must_preserve_story_facts
        or constraints.must_not_show
        or constraints.spoiler_ceiling
    ):
        return ""
    return (
        "AUTHORITATIVE STORY BOUNDARIES FOR THIS APPROVED SCENE\n"
        "Apply these limits to the selected scene only. They are source constraints, not a second cover design contract. "
        + _story_constraints_instruction(brief)
        + " Do not copy candidate selection hints, preferred IDs, free-form preferences, or alternate directions into the image prompt."
    )


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
    if brief.blocking_pending_critical_assumptions():
        raise ValueError("cover prompt cannot compile while blocking story assumptions are pending")
    required = {item.character_id for item in brief.required_characters}
    approved = {item.character_id for item in brief.principal_characters}
    cast = set(scene.cast)
    unapproved = cast - approved
    if unapproved:
        raise ValueError(
            f"cover scene cast contains unapproved characters: {', '.join(sorted(unapproved))}"
        )
    adaptive = bool(scene.focal_strategy or getattr(scene, "campaign", None))
    # The explicit mode belongs to newly reviewed v11 plans. Legacy campaigns
    # omit it and retain their original compiler contract and recipe bytes.
    commercial = bool(getattr(scene, "campaign", None) and scene.campaign.get("expression_mode"))
    missing = required - cast
    if missing and not adaptive:
        raise ValueError(f"cover scene cast is missing required characters: {', '.join(sorted(missing))}")
    if adaptive:
        medium_issue = photographic_medium_issue(scene.art_style)
        if medium_issue:
            raise ValueError(
                f"cover direction requires photographic replanning: {scene.concept_id}: {medium_issue}; "
                "create a new direction while retaining the original story and character facts"
            )
        reader_anchor_id = brief.reader_anchor_character.character_id
        if not cast and not commercial:
            raise ValueError("adaptive cover scene requires a visible principal human subject")
        if not commercial and scene.story_policy_version == STORY_POLICY_VERSION:
            if scene.focal_character_id not in story_cast_policy(brief, 1)["focal_character_ids"]:
                raise ValueError("story cover scene must keep an approved protagonist or co-lead focal")
        elif not commercial and reader_anchor_id not in cast:
            raise ValueError("adaptive cover scene must include the reader-anchor protagonist")
        if not commercial and scene.story_policy_version != STORY_POLICY_VERSION and scene.focal_character_id != reader_anchor_id:
            raise ValueError("adaptive cover scene must keep the reader-anchor protagonist focal")
        if scene.causal_visibility:
            if scene.causal_visibility not in {"direct", "indirect"}:
                raise ValueError("adaptive cover scene causal visibility must be direct or indirect")
            if not commercial and not all((
                scene.conflict_delivery,
                scene.conflict_read,
                scene.cause_signal,
                scene.consequence_signal,
            )):
                raise ValueError("conflict-led cover scene requires cause-and-consequence fields")
            if not set(scene.conflict_character_ids).issubset(cast):
                raise ValueError("cover scene conflict characters must also appear in cast")
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
    if getattr(scene, "campaign", None):
        campaign = CoverCampaignPlan.from_dict(scene.campaign)
        issues = validate_campaign(campaign, cast=scene.cast, title=brief.title,
                                   allowed_refs=set(evidence_ledger.allowed_refs) if evidence_ledger else set())
        if commercial:
            issues = [item for item in issues if item.code not in {
                "campaign_static_interaction", "campaign_generic_hook", "campaign_missing_visual_trigger",
            }]
        if issues:
            raise ValueError("invalid campaign: " + "; ".join(f"{item.code}: {item.message}" for item in issues))


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
        "CAST LOCK": f"{cast_lock}\n{cast_summary} {_visual_inference_instruction(brief)}",
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


def _blocking_lock(scene: CoverScenePlan) -> str:
    return (
        f"Composition family: {scene.composition_family}. Scene family: {scene.scene_family}. "
        f"Blocking and hierarchy: {scene.blocking}. "
    )


def _identity_design_lock(identity: BookVisualIdentity) -> str:
    return (
        f"Visual grammar: {', '.join(identity.visual_grammar)}. Spatial logic: {identity.spatial_logic}. "
    )


def _focal_design_lock(scene: CoverScenePlan) -> str:
    return f"Focal strategy: {scene.focal_strategy}. Visual signature: {scene.visual_signature}. "


def _palette_design_lock(identity: BookVisualIdentity, scene: CoverScenePlan) -> str:
    return f"Color script for this direction: {scene.color_script}. Palette logic: {identity.palette_logic}. "


def _camera_design_lock(identity: BookVisualIdentity, scene: CoverScenePlan) -> str:
    return (
        f"Chosen viewing system: {scene.shot_scale}, {scene.camera_height}, {scene.lens}. "
        f"Direction-specific light: {scene.motivated_lighting}. Lighting logic: {identity.lighting_logic}. "
    )


def _adaptive_modules(
    brief: CoverBriefV2,
    scene: CoverScenePlan,
    *,
    visual_identity: BookVisualIdentity | None,
    evidence_ledger: VisualEvidenceLedger | None,
    conflict_contract: CoreConflictVisualContract | None,
) -> dict[str, str]:
    identity = visual_identity or BookVisualIdentity.from_brief(brief)
    profile = resolve_genre_profile(brief)
    cast_ids = set(scene.cast)
    selected_cast = tuple(
        item for item in brief.principal_characters if item.character_id in cast_ids
    )
    cast_lock = "\n".join(_character_block(item) for item in selected_cast)
    if selected_cast:
        reader_anchor = (
            next(item for item in selected_cast if item.character_id == scene.focal_character_id)
            if scene.story_policy_version == STORY_POLICY_VERSION else brief.reader_anchor_character
        )
        subject_direction = (
            f"Use exactly this cast; focal: {scene.focal_character_id}. Keep {reader_anchor.name} a clear, emotionally active human anchor. "
            "Face, posture and story gesture must read at mobile size. Props, space or lettering may lead the "
            "design, but people remain recognizable and physically present. Preserve approved identities."
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
    if conflict_contract is not None:
        conflict_contract_text = (
            f"Conflict kind: {conflict_contract.conflict_kind}. Pressure source: {conflict_contract.pressure_source}. "
            f"Required visual signals: {'; '.join(conflict_contract.required_visual_signals)}. "
            f"This plan communicates it through {scene.conflict_delivery}; causal visibility is {scene.causal_visibility}. "
            f"Thumbnail story: {scene.conflict_read}. Cause signal: {scene.cause_signal}. "
            f"Consequence signal: {scene.consequence_signal}. Do not reveal: {conflict_contract.spoiler_boundary}."
        )
    else:
        conflict_contract_text = (
            f"Preserve the approved core conflict: {brief.core_conflict}. Show a readable cause, protagonist "
            "consequence, and active response through this plan's own visual language."
        )
    modules = {
        "ROLE AND OUTPUT": (
            "You are Image2, the lead book-cover designer. Deliver finished portrait 2:3 film campaign key art "
            "unique to this novel. Design camera, light, negative space, performance and title together within canon. "
            f"Photographic treatment: {scene.art_style}."
        ),
        "BOOK VISUAL IDENTITY": (
            _identity_design_lock(identity) + f"Design thesis: {_without_title(identity.design_thesis, brief.title)}. Emotional contradiction: "
            f"{identity.dominant_emotional_contradiction}. Material language: {', '.join(identity.material_language)}. "
            f"Story signatures: {', '.join(identity.story_signatures)}. Uniqueness anchors: {', '.join(identity.uniqueness_anchors)}."
        ),
        "STORY TRUTH": (
            f"Core conflict: {brief.core_conflict}. Hero's task: {brief.core_task}. "
            f"Emotional promise: {brief.emotional_promise}. Evidence interpretation: {scene.evidence_summary}."
        ),
        "CORE CONFLICT VISUAL CONTRACT": conflict_contract_text,
        "EVIDENCE ANCHORS": evidence or "Use the approved brief evidence references exactly as supplied.",
        "CAST LOCK": f"{cast_lock}\n{subject_direction} {_visual_inference_instruction(brief)}",
        "SINGLE CINEMATIC MOMENT": (
            f"One coherent scene. Immediately before: {scene.moment_before}. "
            f"Designed focal event: {scene.frozen_action}. Immediately after: {scene.moment_after}."
        ),
        "HERO SUBJECT AND CORE STORY ATMOSPHERE": (
            _focal_design_lock(scene)
            + f"First read: {scene.visual_hook.first_glance_subject}. Make the core conflict and "
            "emotional contradiction legible without relying on generic sadness, glamour, or a stock genre pose. "
            f"Protagonist action visible: {scene.protagonist_action_visible}."
        ),
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY": (
            _blocking_lock(scene)
            + f"Design rationale: {scene.design_rationale}. "
            "Use the topology best suited to this concept; foreground/background causality, a dominant face, or a "
            "multi-character tableau appears only when this plan specifically calls for it."
        ),
        "RELATIONSHIP BLOCKING": (
            f"Visible attention, gesture, or reading-path logic: {gaze}. Depth and hierarchy: {scene.depth_plan}. "
            f"Approved causal characters visible in this plan: {', '.join(scene.conflict_character_ids) or 'none; use approved evidence instead'}. "
            "Keep one thumbnail-readable causal path."
        ),
        "LIVED ENVIRONMENT AND PRIMARY PROP": (
            f"Primary field or setting: {location}. Environment anchors: {', '.join(scene.environment_anchors)}. "
            f"Primary story-bearing prop or intentional absence: {scene.primary_prop}. "
            "Use credible physical materials and scale; wear only when supported by the story."
        ),
        "GENRE EMOTION": (
            _palette_design_lock(identity, scene)
            + f"Genre: {brief.genre}. Target emotion: {scene.emotion_register}; "
            f"{scene.visual_hook.target_emotion}. Avoid: {', '.join(blacklist)}."
        ),
        "CAMERA, DEPTH AND MOTIVATED LIGHTING": (
            _camera_design_lock(identity, scene)
            + "Control exposure and subject/background separation as finished film campaign key art, not a flat room snapshot."
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
            "spelling, word order, legibility, and clear separation from critical story evidence. "
            "Use one continuous title lockup with one unambiguous reading path: for English, "
            "left-to-right, then top-to-bottom. Line breaks and size contrast may vary, but never "
            "separate clauses into competing columns or reverse them to mirror character positions."
        ),
        "TITLE ART DIRECTION": (
            f"Direction system: {scene.typography_style}. "
            f"Book-level typography voice: {identity.typography_voice}. "
            f"Rationale: {scene.typography_rationale}. Let title semantics affect scale, "
            "spacing, rhythm, material, or placement. Keep the result authored and readable rather than a generic "
            "centered text overlay; do not replace or distort letters."
        ),
        "MEDIUM FIDELITY REQUIREMENTS": (
            f"Photographic fidelity requirement: execute {scene.art_style} with professional publishing craft. "
            "Preserve natural skin, anatomy, lens behavior, material detail, motivated light and physical contact. "
            "Objects and lettering support live human action; never turn the people into painted or sculpted figures."
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


def _campaign_modules(brief: CoverBriefV2, scene: CoverScenePlan) -> dict[str, str]:
    campaign = CoverCampaignPlan.from_dict(scene.campaign)
    composite = campaign.composition_mode == "campaign_composite"
    characters = {item.character_id: item for item in brief.principal_characters}
    # Stable appearance is shared; emotion and action come from the selected
    # epoch/layer, never from a character's end-of-novel emotional summary.
    cast_lock = " ".join(_character_block(characters[identifier]).split("Character context:")[0].strip()
                         for identifier in scene.cast)
    layers = []
    for layer in campaign.layers:
        layers.append(
            f"Layer {layer['layer_id']} ({layer['type']}), region {layer['region']}, visual weight {layer['visual_weight']}: "
            f"cast {', '.join(layer['cast']) or 'none'}; epoch {layer['epoch']}; location {layer['location']}; "
            f"{layer['function']}. Source event: {layer.get('event_id', 'not an event')}.")
    interactions = [
        f"In {edge['layer_id']}: {edge['actor_id']} {edge['action']} toward {edge['target_id']}; "
        f"affected people {', '.join(edge['affected_character_ids'])}; visible response: {edge['visible_reaction']}."
        for edge in campaign.interaction_edges
    ]
    links = [
        f"{link['from_layer']} -> {link['to_layer']}: {link['relationship']}; {link['temporal_relationship']}. "
        f"Truth boundary: {link['truth_boundary']}." for link in campaign.layer_relationships
    ]
    hook = campaign.hook
    modules = {
        "PHOTOGRAPHIC RENDER CONTRACT": PHOTOGRAPHIC_RENDER_CONTRACT,
        "HUMAN PERFORMANCE CONTRACT": (
            CAMPAIGN_HUMAN_PERFORMANCE_CONTRACT
            if composite else HUMAN_PERFORMANCE_CONTRACT
        ),
        "ROLE AND OUTPUT": "Create finished vertical film campaign book artwork in live-action photography. " + (
            "Use an intentional campaign composite; layer boundaries and visual transitions are designed, not a single photographed event."
            if composite else "Depict one continuous scene at one time and place with unified perspective and light."
        ),
        "CAST LOCK": f"{cast_lock} {_visual_inference_instruction(brief)}",
        "CAMPAIGN LAYERS": "\n".join(layers),
        "CAMPAIGN ACTOR EXECUTION": (
            f"Approved captured action: {scene.frozen_action}. "
            # Structured gaze strings may contain layer/epoch restrictions in
            # addition to the legacy from/to/attention fields. Preserve all of it.
            f"Specific visible attention: {'; '.join(str(item).strip() for item in scene.gaze_graph)}. "
            "Capture the approved instant, one per layer. Eyelines and reactions belong to their specified layer; "
            "cross-layer echoes are editorial, not physical contact across time or space. "
            "Action summaries and interaction edges describe the same instant, not extra simultaneous actions. "
            "Make the specified actor's attention and the other person's response visibly different when directed; "
            "do not substitute shared downward concentration for an approved opposing eyeline."
        ),
        "RELATIONSHIP BLOCKING": " ".join(interactions),
        "CAMPAIGN TRUTH BOUNDARIES": (
            (" ".join(links) or "One evidenced event only; do not add encounters or reveal later outcomes.")
        ),
        "CAMPAIGN READ ORDER": f"Read layers in this order: {' -> '.join(campaign.read_order)}. {scene.blocking}. {scene.depth_plan}.",
        "CAMPAIGN MOBILE READ": (
            f"At {brief.commercial_visual_goal.thumbnail_reference_width}x{brief.commercial_visual_goal.thumbnail_reference_height}, "
            f"the approved first read is: {scene.visual_hook.expected_thumbnail_read}. "
            "Preserve that read through the approved posture, eyelines, silhouette, separation and contact; "
            "unreadable prop text, an invisible signature or an imagined before/after cannot establish the action. "
            "Keep the decisive gesture and response clear of competing decoration without inventing a new event."
        ),
        "VISIBLE HOOK CONTRACT": (
            f"Visible fact: {hook['visible_fact']}. Unexpected contradiction: {hook['unexpected_contradiction']}. "
            f"Visible trigger: {hook['visual_trigger']}. Viewer question (not rendered text): {hook['open_question']}. "
            f"Withhold, do not depict or answer: {hook['withheld_answer']}. "
            f"Truthful reader promise: {hook['reader_promise']}. Spoiler ceiling: {hook['spoiler_ceiling']}."
        ),
        "CAMPAIGN CAMERA LIGHT AND COLOR": f"{scene.shot_scale}; {scene.camera_height}; {scene.lens}. Light: {scene.motivated_lighting}. Color: {scene.color_script}.",
        "TITLE AND SAFE ZONE": (
            f"Generate artwork without any title, lettering, captions, labels, logos or explanatory text. "
            f"Reserve negative space in normalized region {campaign.title_layout['region']}. "
            "Maintain an even tonal field across the entire title region; keep high-contrast object edges, "
            "busy texture and decorative detail outside it. "
            f"Protected face/story-action regions: {campaign.title_layout.get('safe_zones', [])}. "
            "Coordinates are layout guides, not visible objects. Do not draw boxes or labels around them. "
            "A separate deterministic typesetter will add typography after image generation."
            if campaign.title_render_mode == "composed" else
            f"Render the exact title {json.dumps(brief.title, ensure_ascii=False)} exactly once in {brief.language}. "
            f"Follow layout {json.dumps(campaign.title_layout, ensure_ascii=False, sort_keys=True)}. No other text."
        ),
        "COMPACT FAILURE EXCLUSIONS": (
            "No merged or duplicated people, invented encounters, posing toward camera, uniformly sad faces, hidden story action, "
            "floating props or exaggerated expressions. Keep key faces and actions outside the title region. "
            f"Source exclusions: {', '.join(brief.forbidden_elements)}."
        ),
    }
    if composite:
        modules["CAMPAIGN LAYER SEPARATION"] = (
            "Implement the approved blocking, depth transitions and each truth boundary visibly. "
            "For layers intended as separate photographs, use their designed boundary rather than implying one room. "
            "Do not continue a shared tabletop, floor or horizon across those independent spaces. "
            "Do not draw an additional object in the scene to satisfy a separate editorial enlargement. "
            "Preserve intentional overlaps only where the approved layer relationship allows them; "
            "do not add panels or physical connections absent from the design."
        )
    if campaign.expression_mode == "editorial":
        modules.update({
            "HUMAN PERFORMANCE CONTRACT": (
                "For any approved human subjects, preserve their distinct identities and believable photographic anatomy. "
                "Promotional portrait poses, directional gaze and emotional stillness are valid. "
                "Do not invent a physical interaction or require every subject to enact a plot decision."
            ),
            "ROLE AND OUTPUT": (
                "Create finished vertical photographic book campaign artwork. This is an editorial expression of the novel, "
                "not a literal screenshot of a narrated event. Portraits, objects, atmosphere and conceptual juxtaposition "
                "may convey its core premise without asserting a shared time, place or physical encounter. "
                "Implement the approved composition and make its intended emotional and genre promise legible."
            ),
            "CAST LOCK": f"{cast_lock or 'No human subjects are planned; do not add people.'} {_visual_inference_instruction(brief)}",
            "CAMPAIGN ACTOR EXECUTION": (
                f"Approved visual treatment: {scene.frozen_action}. "
                f"Attention and pose: {'; '.join(str(item).strip() for item in scene.gaze_graph)}. "
                "Treat promotional staging as editorial design. Only explicitly specified source-bound story moments "
                "or physical interactions depict a literal action; preserve their event participants and truth boundaries."
            ),
            "RELATIONSHIP BLOCKING": " ".join(interactions) or (
                "No physical interaction is asserted. Convey the source-supported relationship or theme through the approved visual arrangement."
            ),
            "CAMPAIGN TRUTH BOUNDARIES": " ".join(links) or (
                "Editorial staging is not a new story event. Do not imply invented kinship, a major plot outcome, "
                "a new causal event or knowledge forbidden by the approved spoiler ceiling."
            ),
            "CAMPAIGN MOBILE READ": (
                f"At {brief.commercial_visual_goal.thumbnail_reference_width}x{brief.commercial_visual_goal.thumbnail_reference_height}, "
                f"aim for this first impression: {scene.visual_hook.expected_thumbnail_read}. "
                "Prioritize a coherent focal hierarchy, title space and recognisable genre/emotion. "
                "A thumbnail need not prove every plot detail or individual gesture; avoid clutter and unreadable explanatory text."
            ),
            "COMMERCIAL STORY PROMISE": (
                f"Core story premise: {brief.core_conflict}. Emotional promise: {brief.emotional_promise}. "
                f"Approved reader promise: {hook['reader_promise']}. "
                "Use the approved symbolic or emotional treatment without changing core identities, relationships, world rules "
                "or explicit author constraints. Do not turn a visual metaphor into an unsupported plot assertion."
            ),
            "COMPACT FAILURE EXCLUSIONS": (
                "No merged or duplicated people, distorted anatomy, invented story relationships or unintended visible lettering. "
                "Keep focal subjects and key motifs outside the approved title region. "
                f"Source exclusions: {', '.join(brief.forbidden_elements)}."
            ),
        })
    # Composed artwork receives no title metadata or title-rendering command.
    # Do not globally scrub the title string from story facts: a book can be
    # named after a character or object, whose name/action must remain intact.
    return modules


def _modules(
    brief: CoverBriefV2,
    scene: CoverScenePlan,
    *,
    visual_identity: BookVisualIdentity | None = None,
    evidence_ledger: VisualEvidenceLedger | None = None,
    conflict_contract: CoreConflictVisualContract | None = None,
) -> dict[str, str]:
    if getattr(scene, "campaign", None):
        modules = _campaign_modules(brief, scene)
    elif scene.focal_strategy:
        modules = _adaptive_modules(
            brief, scene, visual_identity=visual_identity, evidence_ledger=evidence_ledger,
            conflict_contract=conflict_contract,
        )
        if scene.story_policy_version == STORY_POLICY_VERSION:
            relationships = [
                f"{link.from_character_id} / {link.to_character_id}: {link.relationship}; {link.visible_tension}"
                for link in brief.relationship_map
                if {link.from_character_id, link.to_character_id}.issubset(scene.cast)
            ]
            modules["THUMBNAIL STORY READ"] = (
                f"Without reading the title or any small text, the image must communicate: {scene.conflict_read}. "
                f"Visible cause: {scene.cause_signal}. Visible consequence: {scene.consequence_signal}. "
                f"Approved relationship context: {'; '.join(relationships) or 'use the source-approved scene interaction'}. "
                "Show this through distinct people, their action, eyelines, alliance or exclusion in one plausible moment. "
                "Every cast member must be readable as a participant at mobile size; tiny phone portraits, hidden faces, "
                "anonymous silhouettes and decorative background figures cannot substitute for the planned people. "
                "Props support the human event. Do not rely on readable documents, generic grief or a posed portrait to explain it."
            )
    else:
        modules = _legacy_modules(brief, scene)
    boundaries = _author_story_boundaries(brief)
    if boundaries:
        modules["AUTHORITATIVE STORY BOUNDARIES"] = boundaries
    return {
        "PHOTOGRAPHIC RENDER CONTRACT": PHOTOGRAPHIC_RENDER_CONTRACT,
        "HUMAN PERFORMANCE CONTRACT": HUMAN_PERFORMANCE_CONTRACT,
        **modules,
    }


def _render(modules: dict[str, str]) -> str:
    return "\n\n".join(f"{name}\n{body}" for name, body in modules.items())


# Soft body allocations, not independent maxima: headings, separators, story
# locks and repair suffixes are reserved by the shared global fitter below.
_MODULE_BODY_BUDGETS = {
    "ROLE AND OUTPUT": 550,
    "BOOK VISUAL IDENTITY": 850,
    "STORY TRUTH": 600,
    "CORE CONFLICT VISUAL CONTRACT": 950,
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
    "AUTHORITATIVE STORY BOUNDARIES": 900,
}


def _clip_explanation(text: str, limit: int) -> str:
    """Bound optional prose, including punctuation, using Unicode code points."""
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    head = text[:limit - 1]
    boundary = head.rfind(" ")
    if boundary >= len(head) * 0.8:
        head = head[:boundary]
    return head.rstrip(" ,;:") + "…"


def _content_locks(
    modules: dict[str, str], brief: CoverBriefV2, scene: CoverScenePlan,
) -> dict[str, str]:
    # Never cut away a cast member, an exact title, a cause/consequence signal,
    # the depicted action, the primary prop, or a spoiler exclusion to save space.
    whole = {
        "PHOTOGRAPHIC RENDER CONTRACT", "HUMAN PERFORMANCE CONTRACT",
        "ROLE AND OUTPUT", "CAST LOCK", "CORE CONFLICT VISUAL CONTRACT",
        "SINGLE CINEMATIC MOMENT", "RELATIONSHIP BLOCKING", "THUMBNAIL STORY READ",
        "LIVED ENVIRONMENT AND PRIMARY PROP", "TITLE AND SAFE ZONE",
        "COMPACT FAILURE EXCLUSIONS",
        "VISIBLE HOOK CONTRACT", "CAMPAIGN LAYERS", "CAMPAIGN TRUTH BOUNDARIES",
        "CAMPAIGN READ ORDER", "CAMPAIGN CAMERA LIGHT AND COLOR",
        "CAMPAIGN ACTOR EXECUTION", "CAMPAIGN MOBILE READ", "CAMPAIGN LAYER SEPARATION",
        "AUTHORITATIVE STORY BOUNDARIES", "COMMERCIAL STORY PROMISE",
    }
    locks = {name: body for name, body in modules.items() if name in whole}
    prefixes = {"STORY TRUTH": f"Core conflict: {brief.core_conflict}. "}
    # The commercial module's rationale is optional, its functioning question
    # and truthful reader promise are not. Previously both could be truncated.
    objective = modules.get("MOBILE COMMERCIAL COVER OBJECTIVE", "")
    if objective:
        prefixes["MOBILE COMMERCIAL COVER OBJECTIVE"] = objective.partition("Novelty rationale: ")[0]
    if scene.focal_strategy:
        prefixes.update({
            "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY": _blocking_lock(scene),
            "TITLE ART DIRECTION": f"Direction system: {scene.typography_style}. ",
            "HERO SUBJECT AND CORE STORY ATMOSPHERE": _focal_design_lock(scene),
        })
        # These boundaries are compiler-owned labels, after all executable fields.
        for name, boundary in (
            ("BOOK VISUAL IDENTITY", "Design thesis: "),
            ("GENRE EMOTION", "Genre: "),
            ("CAMERA, DEPTH AND MOTIVATED LIGHTING", "Control exposure and subject/background separation"),
        ):
            body = modules.get(name, "")
            # Legacy layouts are kept whole; repairs rebuild old compilers below.
            prefixes[name] = body.rpartition(boundary)[0] if boundary in body else body
    for name, prefix in prefixes.items():
        if name in modules:
            if scene.focal_strategy:
                prefix = _without_title(prefix, brief.title)
            # Historical module layouts remain intact instead of guessing where
            # their required fields end.
            locks[name] = prefix if modules[name].startswith(prefix) else modules[name]
    return locks


def _fit_prompt_modules(
    modules: dict[str, str], brief: CoverBriefV2, scene: CoverScenePlan,
    *, suffixes: dict[str, str] | None = None,
) -> dict[str, str]:
    suffixes = suffixes or {}
    rendered = {name: body + suffixes.get(name, "") for name, body in modules.items()}
    if len(_render(rendered)) <= MAX_PROMPT_CODEPOINTS:
        return rendered
    locks = _content_locks(modules, brief, scene)
    tails = {name: body[len(locks.get(name, "")):] for name, body in modules.items()}
    # Reserve some explanatory content in every unlocked module. The rest is
    # allocated proportionally, with exact integer accounting across the prompt.
    minima = {name: min(80, len(tail)) if not locks.get(name) else 0
              for name, tail in tails.items()}
    overhead = len(_render({name: "" for name in modules}))
    required = overhead + sum(
        len(locks.get(name, "")) + len(suffixes.get(name, "")) + minima[name]
        for name in modules
    )
    if required > MAX_PROMPT_CODEPOINTS:
        raise ValueError(
            f"cover prompt protected story content and visual design require {required} Unicode code points; "
            f"limit is {MAX_PROMPT_CODEPOINTS}; shorten the approved title, cast, scene or visual contract. "
            "Protected modules: " + ", ".join(f"{name}={len(body)}" for name, body in locks.items())
        )
    demands = {
        name: max(0, min(len(tail), _MODULE_BODY_BUDGETS.get(name, 600)
                         - len(locks.get(name, ""))) - minima[name])
        for name, tail in tails.items()
    }
    remaining = min(MAX_PROMPT_CODEPOINTS - required, sum(demands.values()))
    total = sum(demands.values())
    allocations = {name: demand * remaining // total if total else 0
                   for name, demand in demands.items()}
    leftover = remaining - sum(allocations.values())
    for name in modules:
        if leftover and allocations[name] < demands[name]:
            allocations[name] += 1
            leftover -= 1
    return {
        name: locks.get(name, "")
        + _clip_explanation(tails[name], minima[name] + allocations[name])
        + suffixes.get(name, "")
        for name in modules
    }


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
    conflict_contract: CoreConflictVisualContract | None = None,
) -> CompiledCoverPrompt:
    _validate_scene(brief, scene, evidence_ledger)
    modules = _modules(
        brief, scene, visual_identity=visual_identity, evidence_ledger=evidence_ledger,
        conflict_contract=conflict_contract,
    )
    modules = _fit_prompt_modules(modules, brief, scene)
    text = _render(modules)
    _validate_prompt(text)
    if getattr(scene, "campaign", None) and scene.campaign.get("expression_mode") and compiler_version == COMPILER_VERSION:
        compiler_version = COMMERCIAL_COMPILER_VERSION
    return CompiledCoverPrompt(text=text, compiler_version=compiler_version, modules=modules)


def scene_to_cover_concept(
    brief: CoverBriefV2,
    scene: CoverScenePlan,
    *,
    compiler_version: str = COMPILER_VERSION,
    visual_identity: BookVisualIdentity | None = None,
    evidence_ledger: VisualEvidenceLedger | None = None,
    conflict_contract: CoreConflictVisualContract | None = None,
) -> CoverConcept:
    compiled = compile_cover_prompt(
        brief, scene, compiler_version=compiler_version,
        visual_identity=visual_identity, evidence_ledger=evidence_ledger,
        conflict_contract=conflict_contract,
    )
    scene_payload = scene.to_dict()
    if visual_identity is not None:
        scene_payload["_visual_identity"] = visual_identity.to_dict()
    if evidence_ledger is not None:
        scene_payload["_evidence_ledger"] = evidence_ledger.to_dict()
    if conflict_contract is not None:
        scene_payload["_core_conflict_visual_contract"] = conflict_contract.to_dict()
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


def compile_direction_recipes(brief: CoverBriefV2, direction: Any) -> dict[str, dict[str, Any]]:
    """Freeze the exact image input and local title recipe before approval.

    concept_id is the persisted candidate-plan key (there is no scene_id).
    Approval services must store these recipes, not silently recompile them.
    """
    recipes: dict[str, dict[str, Any]] = {}
    for scene in direction.plans:
        if scene.concept_id in recipes:
            raise ValueError("direction concept IDs must be unique")
        compiled = compile_cover_prompt(brief, scene, visual_identity=direction.visual_identity,
            evidence_ledger=direction.evidence_ledger, conflict_contract=direction.core_conflict_visual_contract)
        campaign = CoverCampaignPlan.from_dict(scene.campaign) if getattr(scene, "campaign", None) else None
        layout = dict(campaign.title_layout) if campaign else {}
        if campaign and campaign.title_render_mode == "composed":
            try:
                from .cover_typography import prepare_title_layout
            except ImportError:  # pragma: no cover - legacy CLI
                from cover_typography import prepare_title_layout
            layout = prepare_title_layout(layout, expected_title=brief.title)
        recipe = {"image_prompt": compiled.text, "compiler_version": compiled.compiler_version,
                  "title_render_mode": campaign.title_render_mode if campaign else "native",
                  "title_layout": layout,
                  "modules": dict(compiled.modules)}
        encoded = json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        recipe["recipe_sha256"] = hashlib.sha256(encoded).hexdigest()
        recipes[scene.concept_id] = recipe
    return recipes


_REPAIR_MODULES = {
    "age_mismatch": ("CAST LOCK",),
    "missing_character": ("CAST LOCK", "RELATIONSHIP BLOCKING"),
    "generic_ai_face": (
        "PHOTOGRAPHIC RENDER CONTRACT", "HUMAN PERFORMANCE CONTRACT",
        "HERO SUBJECT AND CORE STORY ATMOSPHERE",
        "PHOTOREALISM REQUIREMENTS",
        "MEDIUM FIDELITY REQUIREMENTS",
        "CAMERA, DEPTH AND MOTIVATED LIGHTING",
    ),
    "weak_story_action": (
        "HUMAN PERFORMANCE CONTRACT",
        "SINGLE CINEMATIC MOMENT",
        "HERO SUBJECT AND CORE STORY ATMOSPHERE",
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY",
        "EVIDENCE ANCHORS",
    ),
    "genre_drift": (
        "PHOTOGRAPHIC RENDER CONTRACT", "MEDIUM FIDELITY REQUIREMENTS",
        "PHOTOREALISM REQUIREMENTS", "BOOK VISUAL IDENTITY", "GENRE EMOTION",
    ),
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
    "core_conflict_missing": (
        "CORE CONFLICT VISUAL CONTRACT",
        "SINGLE CINEMATIC MOMENT",
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY",
        "MOBILE COMMERCIAL COVER OBJECTIVE",
    ),
    "causal_relationship_missing": (
        "CORE CONFLICT VISUAL CONTRACT",
        "CAST LOCK",
        "RELATIONSHIP BLOCKING",
        "CONFLICT TABLEAU AND EMOTIONAL GEOGRAPHY",
    ),
    "protagonist_action_missing": (
        "CORE CONFLICT VISUAL CONTRACT",
        "SINGLE CINEMATIC MOMENT",
        "HERO SUBJECT AND CORE STORY ATMOSPHERE",
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
    conflict_contract: CoreConflictVisualContract | None = None,
) -> CompiledCoverPrompt:
    if getattr(scene, "campaign", None) and set(repair_codes).intersection({
        "weak_story_action", "reader_promise_mismatch", "core_conflict_missing",
        "causal_relationship_missing", "protagonist_action_missing",
    }):
        raise ValueError("campaign semantic failures require a new direction and approval")
    unknown = [code for code in repair_codes if code not in _REPAIR_MODULES]
    if unknown:
        raise ValueError(f"unknown cover repair code: {', '.join(unknown)}")
    fresh = compile_cover_prompt(
        brief, scene,
        visual_identity=visual_identity, evidence_ledger=evidence_ledger,
        conflict_contract=conflict_contract,
    )
    modules = dict(prior.modules)
    # Keep prior repair focus suffixes separate so fitting never clips them.
    notes: dict[str, list[str]] = {}
    for name, body in modules.items():
        match = re.search(r"(?: Repair focus: [a-z ]+\.)+$", body)
        if match:
            notes[name] = re.findall(r" Repair focus: [a-z ]+\.", match.group())
            modules[name] = body[:match.start()]
    if prior.compiler_version != fresh.compiler_version:
        modules = dict(fresh.modules)
    for code in dict.fromkeys(repair_codes):
        for module in _REPAIR_MODULES[code]:
            if module in fresh.modules:
                modules[module] = fresh.modules[module]
                note = f" Repair focus: {code.replace('_', ' ')}."
                if note not in notes.setdefault(module, []):
                    notes[module].append(note)
    suffixes = {name: "".join(values) for name, values in notes.items()}
    if len(_render({name: body + suffixes.get(name, "") for name, body in modules.items()})) > MAX_PROMPT_CODEPOINTS:
        # Rebalance from the approved source, not repeatedly truncated output.
        # Keep existing and newly requested repair focuses across that rebuild.
        modules = _modules(
            brief, scene, visual_identity=visual_identity,
            evidence_ledger=evidence_ledger, conflict_contract=conflict_contract,
        )
    modules = _fit_prompt_modules(modules, brief, scene, suffixes=suffixes)
    text = _render(modules)
    _validate_prompt(text)
    return replace(
        prior, text=text, compiler_version=fresh.compiler_version,
        revision=prior.revision + 1, modules=modules,
    )
