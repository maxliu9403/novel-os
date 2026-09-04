"""Canon and evidence validation for structured cover directions."""

from __future__ import annotations

from dataclasses import dataclass

try:
    from .cover_models_v2 import ArtDirectionSet, CoverBriefV2
except ImportError:  # pragma: no cover - legacy CLI imports core modules top-level
    from cover_models_v2 import ArtDirectionSet, CoverBriefV2


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
