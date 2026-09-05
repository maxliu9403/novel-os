"""Canon and evidence validation for structured cover directions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

try:
    from .cover_models_v2 import ArtDirectionSet, CoverBriefV2
    from .cover_profiles import portfolio_blueprint
    from .cover_novelty import historical_collisions, portfolio_collisions
except ImportError:  # pragma: no cover - legacy CLI imports core modules top-level
    from cover_models_v2 import ArtDirectionSet, CoverBriefV2
    from cover_profiles import portfolio_blueprint
    from cover_novelty import historical_collisions, portfolio_collisions


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    severity: str
    message: str
    evidence: str = ""


def _evidence_exists(
    brief: CoverBriefV2,
    direction: ArtDirectionSet,
    reference: str,
) -> bool:
    prefix, _, identifier = reference.partition(":")
    if prefix == "character":
        return identifier in {item.character_id for item in brief.principal_characters}
    if prefix == "node":
        return identifier in {item.node_id for item in brief.decisive_story_nodes}
    if prefix == "signal":
        return identifier in {item.signal_id for item in brief.secondary_signals}
    if prefix == "evidence" and direction.evidence_ledger is not None:
        return direction.evidence_ledger.by_id(identifier) is not None
    return False


def _has_layered_depth(plan: object) -> bool:
    """Return whether a plan explicitly declares foreground/background staging.

    Blocking describes character placement while depth_plan describes the same
    composition's focus hierarchy.  Treating either field in isolation makes a
    structurally valid plan fail based only on which JSON field the director
    chose for the depth wording.
    """
    spatial_text = " ".join((
        str(getattr(plan, "blocking", "")),
        str(getattr(plan, "depth_plan", "")),
    )).casefold()
    return (
        "foreground" in spatial_text
        and any(marker in spatial_text for marker in ("middle", "background"))
    )


def _validate_portfolio_treatments(
    brief: CoverBriefV2,
    direction: ArtDirectionSet,
) -> list[ValidationFinding]:
    """Validate the v3 cross-concept diversity contract.

    The treatment ids are intentionally structural rather than free-form prose:
    the director can repair them deterministically, the compiler can turn them
    into detailed art instructions, and older persisted v2 directions retain
    their original hashes and behavior.
    """
    if not direction.profile_version.casefold().startswith("cover-profiles.v3"):
        return []

    findings: list[ValidationFinding] = []
    blueprint = portfolio_blueprint(len(direction.plans))
    expected_by_hook = {item.hook_type: item for item in blueprint}
    actual_hooks = [plan.visual_hook.hook_type.casefold() for plan in direction.plans]
    expected_hooks = [item.hook_type for item in blueprint]
    if actual_hooks != expected_hooks:
        findings.append(ValidationFinding(
            "portfolio_order_mismatch",
            "blocker",
            "Portfolio plans must follow the assigned blueprint hook order",
            ", ".join(expected_hooks),
        ))

    treatment_fields = (
        "portfolio_slot",
        "composition_family",
        "scene_family",
        "art_style",
        "emotion_register",
        "typography_style",
    )
    for plan in direction.plans:
        hook_type = plan.visual_hook.hook_type.casefold()
        expected = expected_by_hook.get(hook_type)
        if expected is None:
            findings.append(ValidationFinding(
                "portfolio_hook_mismatch",
                "blocker",
                "Plan uses a hook outside the assigned portfolio blueprint",
                plan.concept_id,
            ))
            continue
        mismatches = [
            f"{field}={getattr(plan, field) or '<missing>'} (expected {getattr(expected, field)})"
            for field in treatment_fields
            if getattr(plan, field).casefold() != getattr(expected, field).casefold()
        ]
        if mismatches:
            findings.append(ValidationFinding(
                "portfolio_treatment_mismatch",
                "blocker",
                "Plan must use its assigned composition, scene, art, emotion, and typography treatment ids",
                f"{plan.concept_id}: {'; '.join(mismatches)}",
            ))

    scene_families = [plan.scene_family.casefold() for plan in direction.plans if plan.scene_family]
    if len(scene_families) != len(direction.plans) or len(set(scene_families)) != len(scene_families):
        findings.append(ValidationFinding(
            "duplicate_scene_family",
            "blocker",
            "Every portfolio plan must stage a distinct scene family rather than a variation of one tableau",
            ", ".join(scene_families) or "all scene families missing",
        ))
    approved_locations = {
        item.casefold(): item for item in brief.lived_environment.primary_spaces
    }
    location_families = [plan.location_family.casefold() for plan in direction.plans]
    for plan, location in zip(direction.plans, location_families, strict=True):
        if not location or location not in approved_locations:
            findings.append(ValidationFinding(
                "location_family_mismatch",
                "blocker",
                "Plan location_family must exactly match an approved lived-environment primary space",
                f"{plan.concept_id}: {plan.location_family or '<missing>'}",
            ))
    required_distinct_locations = min(len(direction.plans), len(approved_locations))
    valid_locations = {item for item in location_families if item in approved_locations}
    if len(valid_locations) < required_distinct_locations:
        findings.append(ValidationFinding(
            "insufficient_location_diversity",
            "blocker",
            "Portfolio must use every approved location once before repeating one",
            ", ".join(plan.location_family or "<missing>" for plan in direction.plans),
        ))
    frozen_actions = [" ".join(plan.frozen_action.casefold().split()) for plan in direction.plans]
    if len(set(frozen_actions)) != len(frozen_actions):
        findings.append(ValidationFinding(
            "duplicate_story_beat",
            "blocker",
            "Every portfolio plan must freeze a different story beat",
        ))
    return findings


def _validate_adaptive_portfolio(
    brief: CoverBriefV2,
    direction: ArtDirectionSet,
    *,
    recent_fingerprints: Sequence[Mapping[str, Any]] = (),
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    identity = direction.visual_identity
    if identity is None or not identity.complete:
        findings.append(ValidationFinding(
            "missing_visual_identity",
            "blocker",
            "Adaptive cover direction requires a complete book-specific visual identity",
        ))
    if direction.evidence_ledger is None:
        findings.append(ValidationFinding(
            "missing_evidence_ledger",
            "blocker",
            "Adaptive cover direction must remain bound to its source evidence ledger",
        ))

    required_reasoning = (
        "focal_strategy", "composition_family", "scene_family", "art_style",
        "emotion_register", "typography_style", "design_rationale",
        "evidence_summary", "typography_rationale", "novelty_rationale",
        "visual_signature",
    )
    reader_anchor_id = brief.reader_anchor_character.character_id
    for plan in direction.plans:
        missing = [field for field in required_reasoning if not str(getattr(plan, field, "") or "").strip()]
        if missing:
            findings.append(ValidationFinding(
                "missing_design_reasoning",
                "blocker",
                "Adaptive plan is missing book-specific design reasoning",
                f"{plan.concept_id}: {', '.join(missing)}",
            ))
        if not plan.cast:
            findings.append(ValidationFinding(
                "missing_human_anchor",
                "blocker",
                "Every adaptive cover plan must contain a clear principal human subject",
                plan.concept_id,
            ))
        elif reader_anchor_id not in plan.cast:
            findings.append(ValidationFinding(
                "missing_reader_anchor",
                "blocker",
                "Every adaptive cover plan must visibly include the reader-anchor protagonist",
                f"{plan.concept_id}: {reader_anchor_id}",
            ))
        elif plan.focal_character_id != reader_anchor_id:
            findings.append(ValidationFinding(
                "reader_anchor_not_focal",
                "blocker",
                "The reader-anchor protagonist must remain the focal human subject",
                f"{plan.concept_id}: {reader_anchor_id}",
            ))
        if plan.cast and not plan.gaze_graph:
            findings.append(ValidationFinding(
                "missing_attention_logic",
                "blocker",
                "A character-bearing plan needs visible gaze, gesture, or attention logic",
                plan.concept_id,
            ))
        if not any(
            reference.partition(":")[0] in {"node", "signal", "evidence"}
            for reference in plan.story_evidence_refs
        ):
            findings.append(ValidationFinding(
                "missing_story_scene_evidence",
                "blocker",
                "Every adaptive plan must stage a concrete source-bound story action",
                plan.concept_id,
            ))

    if len(brief.principal_characters) > 1 and not any(
        len(plan.cast) >= 2 for plan in direction.plans
    ):
        findings.append(ValidationFinding(
            "missing_relationship_scene",
            "blocker",
            "At least one adaptive direction must show the central relationship conflict",
            reader_anchor_id,
        ))

    for collision in portfolio_collisions(direction.plans):
        findings.append(ValidationFinding(
            "portfolio_similarity",
            "blocker",
            "Two cover plans reuse the same structural visual language",
            (
                f"{collision['first_concept_id']} ~ {collision['second_concept_id']} "
                f"({collision['similarity']:.3f})"
            ),
        ))
    for collision in historical_collisions(direction.plans, recent_fingerprints):
        findings.append(ValidationFinding(
            "historical_similarity",
            "blocker",
            "Cover plan is structurally too close to a recent novel's direction",
            (
                f"{collision['concept_id']} ~ {collision['project_id']}/"
                f"{collision['matched_concept_id']} ({collision['similarity']:.3f})"
            ),
        ))
    frozen_actions = [" ".join(plan.frozen_action.casefold().split()) for plan in direction.plans]
    if len(set(frozen_actions)) != len(frozen_actions):
        findings.append(ValidationFinding(
            "duplicate_story_beat", "blocker", "Every plan must use a distinct story beat",
        ))
    return findings


def validate_direction(
    brief: CoverBriefV2,
    direction: ArtDirectionSet,
    *,
    recent_fingerprints: Sequence[Mapping[str, Any]] = (),
) -> tuple[ValidationFinding, ...]:
    findings: list[ValidationFinding] = []
    if direction.brief_sha256 != brief.source_prompt_sha256:
        findings.append(ValidationFinding(
            "brief_hash_mismatch", "blocker", "Direction is bound to a different brief hash",
        ))
    pending_critical = [
        *brief.pending_critical_assumptions(),
        *(
            item for item in direction.visual_assumptions
            if item.critical and item.status == "pending_confirmation"
        ),
    ]
    for assumption in pending_critical:
        findings.append(ValidationFinding(
            "unresolved_critical_assumption",
            "blocker",
            "Critical visual facts must be confirmed before art direction approval",
            assumption.field,
        ))
    required = {item.character_id for item in brief.required_characters}
    known = {item.character_id for item in brief.principal_characters}
    adaptive = direction.profile_version.casefold().startswith("cover-profiles.v4")
    optional_conflict_cast = {
        item.character_id for item in brief.principal_characters if not item.must_appear
    }
    strategies = [item.visual_strategy.casefold() for item in direction.plans]
    hooks = [item.visual_hook.hook_type.casefold() for item in direction.plans]
    if len(set(strategies)) != len(strategies):
        findings.append(ValidationFinding("duplicate_strategy", "blocker", "Direction strategies must be distinct"))
    if not adaptive and len(set(hooks)) != len(hooks):
        findings.append(ValidationFinding("duplicate_hook", "blocker", "Visual hooks must be distinct"))
    if adaptive:
        findings.extend(_validate_adaptive_portfolio(
            brief, direction, recent_fingerprints=recent_fingerprints,
        ))
    else:
        findings.extend(_validate_portfolio_treatments(brief, direction))
    if not adaptive and optional_conflict_cast and not any(
        set(plan.cast) & optional_conflict_cast
        and _has_layered_depth(plan)
        for plan in direction.plans
    ):
        findings.append(ValidationFinding(
            "missing_causal_conflict_tableau",
            "blocker",
            (
                "At least one direction must use approved optional conflict cast in a "
                "foreground-to-background cause-and-consequence tableau"
            ),
            ", ".join(sorted(optional_conflict_cast)),
        ))
    for plan in direction.plans:
        unknown_cast = set(plan.cast) - known
        if unknown_cast:
            findings.append(ValidationFinding(
                "identity_invention", "blocker", "Direction casts an unapproved character", ", ".join(sorted(unknown_cast)),
            ))
        missing = required - set(plan.cast)
        if missing and not adaptive:
            findings.append(ValidationFinding(
                "missing_character", "blocker", "Direction omits a required character", ", ".join(sorted(missing)),
            ))
        if plan.cast and plan.focal_character_id not in plan.cast:
            findings.append(ValidationFinding(
                "focal_character_missing", "blocker", "Focal character is not in the cast", plan.focal_character_id,
            ))
        for reference in plan.story_evidence_refs:
            if not _evidence_exists(brief, direction, reference):
                findings.append(ValidationFinding(
                    "unknown_evidence", "blocker", "Direction references unknown story evidence", reference,
                ))
        text = " ".join((
            plan.moment_before, plan.frozen_action, plan.moment_after, plan.blocking,
            plan.primary_prop, plan.visual_hook.first_glance_subject,
        )).casefold()
        if any(marker in text for marker in ("american white", "becomes twenty", "ethnicity", "nationality")):
            findings.append(ValidationFinding(
                "identity_invention", "blocker", "Direction contains an inferred identity or age claim",
            ))
        if not adaptive and len(plan.cast) >= 4 and not _has_layered_depth(plan):
            findings.append(ValidationFinding(
                "group_blocking",
                "blocker",
                "Four-plus character direction needs foreground and middle/background depth",
                plan.concept_id,
            ))
    return tuple(findings)
