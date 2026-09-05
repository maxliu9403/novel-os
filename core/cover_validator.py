"""Canon and evidence validation for structured cover directions."""

from __future__ import annotations

from dataclasses import dataclass

try:
    from .cover_models_v2 import ArtDirectionSet, CoverBriefV2
    from .cover_profiles import portfolio_blueprint
except ImportError:  # pragma: no cover - legacy CLI imports core modules top-level
    from cover_models_v2 import ArtDirectionSet, CoverBriefV2
    from cover_profiles import portfolio_blueprint


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    severity: str
    message: str
    evidence: str = ""


def _evidence_exists(brief: CoverBriefV2, reference: str) -> bool:
    prefix, _, identifier = reference.partition(":")
    if prefix == "character":
        return identifier in {item.character_id for item in brief.principal_characters}
    if prefix == "node":
        return identifier in {item.node_id for item in brief.decisive_story_nodes}
    if prefix == "signal":
        return identifier in {item.signal_id for item in brief.secondary_signals}
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


def validate_direction(
    brief: CoverBriefV2,
    direction: ArtDirectionSet,
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
    optional_conflict_cast = {
        item.character_id for item in brief.principal_characters if not item.must_appear
    }
    strategies = [item.visual_strategy.casefold() for item in direction.plans]
    hooks = [item.visual_hook.hook_type.casefold() for item in direction.plans]
    if len(set(strategies)) != len(strategies):
        findings.append(ValidationFinding("duplicate_strategy", "blocker", "Direction strategies must be distinct"))
    if len(set(hooks)) != len(hooks):
        findings.append(ValidationFinding("duplicate_hook", "blocker", "Visual hooks must be distinct"))
    findings.extend(_validate_portfolio_treatments(brief, direction))
    if optional_conflict_cast and not any(
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
        if missing:
            findings.append(ValidationFinding(
                "missing_character", "blocker", "Direction omits a required character", ", ".join(sorted(missing)),
            ))
        if plan.focal_character_id not in plan.cast:
            findings.append(ValidationFinding(
                "focal_character_missing", "blocker", "Focal character is not in the cast", plan.focal_character_id,
            ))
        for reference in plan.story_evidence_refs:
            if not _evidence_exists(brief, reference):
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
        if len(plan.cast) >= 4 and not _has_layered_depth(plan):
            findings.append(ValidationFinding(
                "group_blocking",
                "blocker",
                "Four-plus character direction needs foreground and middle/background depth",
                plan.concept_id,
            ))
    return tuple(findings)
