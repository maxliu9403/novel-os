"""Canon and evidence validation for structured cover directions."""

from __future__ import annotations

try:
    from .cover_campaign import CoverCampaignPlan, uses_campaign, uses_commercial_expression, validate_campaign, portfolio_findings
    from .cover_campaign_evidence import FrozenCampaignEvidenceError, approved_evidence_claim_paths, build_evidence_claims
    from .cover_story_policy import STORY_POLICY_VERSION, story_cast_policy, uses_story_policy
except ImportError:  # pragma: no cover - legacy CLI imports core modules top-level
    from cover_campaign import CoverCampaignPlan, uses_campaign, uses_commercial_expression, validate_campaign, portfolio_findings
    from cover_campaign_evidence import FrozenCampaignEvidenceError, approved_evidence_claim_paths, build_evidence_claims
    from cover_story_policy import STORY_POLICY_VERSION, story_cast_policy, uses_story_policy

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

try:
    from .cover_render_policy import photographic_medium_issue
    from .cover_models_v2 import ArtDirectionSet, CoverBriefV2
    from .cover_profiles import portfolio_blueprint
    from .cover_novelty import historical_collisions, portfolio_collisions
except ImportError:  # pragma: no cover - legacy CLI imports core modules top-level
    from cover_render_policy import photographic_medium_issue
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


def _constraint_reference_exists(
    brief: CoverBriefV2,
    direction: ArtDirectionSet,
    reference: str,
    *,
    verified_source_refs: set[str] | None = None,
) -> bool:
    """Validate typed source refs while allowing full-skeleton section refs.

    ``section:...`` refs point into the immutable skeleton snapshot and are
    resolved by the analysis owner.  Typed refs can be checked here because
    they are represented directly in ``CoverBriefV2`` or its evidence ledger.
    """
    # Section/snapshot references are opaque offsets into the immutable source
    # snapshot.  A non-empty string is not proof that the reference exists;
    # the analysis owner must pass its verified registry explicitly.
    if reference.startswith(("section:", "snapshot:")):
        return verified_source_refs is not None and reference in verified_source_refs
    return _evidence_exists(brief, direction, reference)


def _constraint_story_text(plan: object) -> str:
    """Collect executable visual fields for conservative exact-match checks."""
    hook = getattr(plan, "visual_hook", None)
    fields = [
        "moment_before", "frozen_action", "moment_after", "blocking",
        "environment_anchors", "primary_prop", "depth_plan", "motivated_lighting",
        "color_script", "conflict_delivery", "conflict_read", "cause_signal",
        "consequence_signal",
    ]
    values: list[str] = [str(getattr(plan, field, "") or "") for field in fields]
    if hook is not None:
        values.extend(str(getattr(hook, field, "") or "") for field in (
            "first_glance_subject", "identity_anchor", "genre_signal",
            "reader_promise", "target_emotion", "expected_thumbnail_read",
        ))
    # v10 stores the executable scene in a nested campaign object.  Looking
    # only at the legacy plan fields lets a forbidden action pass validation.
    campaign = getattr(plan, "campaign", None)
    if isinstance(campaign, Mapping):
        for layer in campaign.get("layers", ()):
            if isinstance(layer, Mapping):
                values.extend(str(layer.get(field, "") or "") for field in (
                    "function", "location", "epoch",
                ))
        for edge in campaign.get("interaction_edges", ()):
            if isinstance(edge, Mapping):
                values.extend(str(edge.get(field, "") or "") for field in (
                    "action", "visible_reaction",
                ))
        campaign_hook = campaign.get("hook")
        if isinstance(campaign_hook, Mapping):
            # Policy fields (spoiler ceiling, withheld answer, open question)
            # are instructions about what must remain unseen, not depictions.
            values.extend(str(campaign_hook.get(field, "") or "") for field in (
                "visible_fact", "unexpected_contradiction", "visual_trigger",
                "reader_promise",
            ))
    # Field boundaries are also clause boundaries. A trailing "do not show"
    # in one field must not negate a depiction supplied by another field.
    return ". ".join(values)


_REFERENCE_PREFIXES = {"character", "node", "signal", "evidence", "section", "snapshot"}
_DEPICTION_VERB = r"(?:show|depict|reveal|include|display|showing|depicting|revealing|including|displaying)"
_DEPICTION_NEGATION = re.compile(
    rf"(?:\b(?:do not|don't|must not|should not|never|avoid)\s+{_DEPICTION_VERB}"
    rf"|\bwithout(?:\s+{_DEPICTION_VERB})?"
    r"|(?:不要|不得|不能|避免|切勿|禁止|不应|不)(?:展示|描绘|呈现|表现|显示|揭示|出现|包含|透露))"
    r"\s*(?:[:：-]\s*)?(?:(?:the|a|an|any)\s+)?$"
)
_NEGATED_NEGATION = re.compile(
    r"(?:\b(?:not|never|without|cannot|can't|don't|isn't|wasn't)\s*"
    r"|(?:不能|不得|不可|不准|不许|不应|不要|没有|并非|不是|不必|无需|未|不))$"
)


def _typed_constraint_reference(value: str) -> tuple[str, str] | None:
    """Parse only an approved typed reference, never arbitrary prose with ':'."""
    prefix, separator, identifier = value.partition(":")
    if not separator or prefix.casefold() not in _REFERENCE_PREFIXES:
        return None
    identifier = identifier.strip()
    if not identifier or not re.fullmatch(r"[A-Za-z0-9_.:-]+", identifier):
        return None
    return prefix.casefold(), identifier


def _contains_forbidden_depiction(text: str, forbidden: str) -> bool:
    """Match a phrase in visual prose while ignoring explicit non-depiction clauses."""
    needle = " ".join(forbidden.casefold().split())
    if not needle:
        return False
    normalized = " ".join(text.casefold().split())
    # Latin words need boundaries ("reconciliationist" is not
    # "reconciliation"); Chinese phrases do not have whitespace boundaries.
    left = r"(?<![a-z0-9_])" if needle[0].isascii() and (needle[0].isalnum() or needle[0] == "_") else ""
    right = r"(?![a-z0-9_])" if needle[-1].isascii() and (needle[-1].isalnum() or needle[-1] == "_") else ""
    pattern = re.compile(left + re.escape(needle) + right)
    for match in pattern.finditer(normalized):
        prefix = normalized[max(0, match.start() - 72):match.start()]
        negation = _DEPICTION_NEGATION.search(prefix)
        if negation is not None and not _NEGATED_NEGATION.search(prefix[:negation.start()]):
            continue
        return True
    return False


def _validate_story_constraints(
    brief: CoverBriefV2,
    direction: ArtDirectionSet,
    *,
    verified_source_refs: set[str] | None = None,
    story_analysis: Mapping[str, Any] | None = None,
) -> list[ValidationFinding]:
    """Apply the machine-checkable portion of the optional handoff limits."""
    constraints = brief.cover_constraints
    if constraints is None:
        return []
    findings: list[ValidationFinding] = []
    for reference in constraints.source_refs:
        if not _constraint_reference_exists(
            brief, direction, reference, verified_source_refs=verified_source_refs,
        ):
            findings.append(ValidationFinding(
                "unknown_constraint_source_ref",
                "blocker",
                "Cover constraint source_refs must resolve to approved story evidence",
                reference,
            ))
    known_nodes = {item.node_id for item in brief.decisive_story_nodes}
    known_characters = {item.character_id for item in brief.principal_characters}
    known_signals = {item.signal_id for item in brief.secondary_signals}
    typed_requests: list[tuple[str, str, str]] = []
    for request in constraints.must_show_if_supported:
        typed = _typed_constraint_reference(request)
        if typed is not None:
            prefix, identifier = typed
            typed_requests.append((request, prefix, identifier))
        elif request in known_nodes:
            typed_requests.append((request, "node", request))
        elif request in known_characters:
            typed_requests.append((request, "character", request))
        elif request in known_signals:
            typed_requests.append((request, "signal", request))
    for request, prefix, identifier in typed_requests:
        reference = f"{prefix}:{identifier}"
        if prefix in {"section", "snapshot"}:
            # A free-form section request cannot be asserted from the brief;
            # verified snapshot references are handled by the analysis owner.
            if verified_source_refs is None or reference not in verified_source_refs:
                continue
        if prefix == "node" and identifier not in known_nodes:
            findings.append(ValidationFinding(
                "unknown_constraint_story_ref", "blocker",
                "must_show_if_supported node reference is not an approved story node", request,
            ))
            continue
        if prefix == "character" and identifier not in known_characters:
            findings.append(ValidationFinding(
                "unknown_constraint_story_ref", "blocker",
                "must_show_if_supported character reference is not an approved character", request,
            ))
            continue
        if prefix == "signal" and identifier not in known_signals:
            findings.append(ValidationFinding(
                "unknown_constraint_story_ref", "blocker",
                "must_show_if_supported signal reference is not an approved story signal", request,
            ))
            continue
        # A character present in the approved principal-character registry is
        # source-supported even when the optional per-character source_refs
        # list is empty.  The registry entry itself is the approved fact; the
        # director still has to put that character in the visible cast.
        character_evidence: set[str] = set()
        if prefix == "character" and story_analysis is not None and direction.evidence_ledger is not None:
            # Full-skeleton ledgers point to snapshot byte spans, not synthetic
            # cover_brief:character aliases. Resolve the exact character id
            # through the independently verified analysis registry instead.
            records = [item for item in story_analysis.get("characters", ())
                       if isinstance(item, Mapping) and item.get("character_id") == identifier]
            if len(records) == 1:
                character_evidence = set(records[0].get("evidence_refs", ())).intersection(
                    direction.evidence_ledger.allowed_refs,
                )

        def plan_cites(plan: object) -> bool:
            refs = getattr(plan, "story_evidence_refs", ())
            if prefix == "character" and identifier not in getattr(plan, "cast", ()):
                return False
            if reference in refs:
                return True
            if character_evidence.intersection(refs):
                return True
            ledger = direction.evidence_ledger
            if ledger is None:
                return False
            for cited in refs:
                ref_prefix, _, evidence_identifier = str(cited).partition(":")
                if ref_prefix != "evidence" or not evidence_identifier:
                    continue
                item = ledger.by_id(evidence_identifier)
                if item is not None and item.source_ref in {
                    reference,
                    f"cover_brief:{prefix}:{identifier}",
                }:
                    return True
            return False

        if not any(plan_cites(plan) for plan in direction.plans):
            findings.append(ValidationFinding(
                "requested_story_evidence_missing",
                "blocker",
                "A source-supported must_show_if_supported item is absent from every cover plan",
                request,
            ))
    story_text = ". ".join(_constraint_story_text(plan) for plan in direction.plans).casefold()
    for forbidden in constraints.must_not_show:
        needle = " ".join(forbidden.casefold().split())
        if _contains_forbidden_depiction(story_text, forbidden):
            findings.append(ValidationFinding(
                "cover_constraint_violation",
                "blocker",
                "A cover plan contains an exact must_not_show phrase",
                forbidden,
            ))
    return findings


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
    if direction.profile_version.casefold().startswith(("cover-profiles.v6", "cover-profiles.v7", "cover-profiles.v8", "cover-profiles.v9")):
        for plan in direction.plans:
            issue = photographic_medium_issue(plan.art_style)
            if issue:
                findings.append(ValidationFinding(
                    "non_photographic_medium", "blocker",
                    "Replan as live-action photography while preserving story, cast and creative diversity",
                    f"{plan.concept_id}: {issue}",
                ))
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

    conflict_contract = direction.core_conflict_visual_contract
    conflict_profile = direction.profile_version.casefold().startswith(("cover-profiles.v5", "cover-profiles.v6", "cover-profiles.v7", "cover-profiles.v8", "cover-profiles.v9"))
    if conflict_profile:
        if conflict_contract is None:
            findings.append(ValidationFinding(
                "missing_core_conflict_contract",
                "blocker",
                "Conflict-led cover direction requires a source-bound core conflict visual contract",
            ))
        else:
            findings.extend(_validate_core_conflict_contract(brief, direction))

    required_reasoning = (
        "focal_strategy", "composition_family", "scene_family", "art_style",
        "emotion_register", "typography_style", "design_rationale",
        "evidence_summary", "typography_rationale", "novelty_rationale",
        "visual_signature",
    )
    reader_anchor_id = brief.reader_anchor_character.character_id
    story_policy = story_cast_policy(brief, len(direction.plans)) if uses_story_policy(direction.profile_version) else None
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
        elif story_policy is not None:
            if plan.story_policy_version != STORY_POLICY_VERSION:
                findings.append(ValidationFinding("story_policy_missing", "blocker", "Story plan must retain its versioned cast policy", plan.concept_id))
            if plan.focal_character_id not in story_policy["focal_character_ids"]:
                findings.append(ValidationFinding("reader_anchor_not_focal", "blocker", "A source-approved protagonist or co-lead must be focal", plan.concept_id))
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

        if conflict_profile:
            missing_conflict_fields = [
                field for field in (
                    "causal_visibility", "conflict_delivery", "conflict_read",
                    "cause_signal", "consequence_signal",
                )
                if not str(getattr(plan, field, "") or "").strip()
            ]
            if missing_conflict_fields:
                findings.append(ValidationFinding(
                    "missing_core_conflict",
                    "blocker",
                    "Every cover plan must state a thumbnail-legible cause and consequence",
                    f"{plan.concept_id}: {', '.join(missing_conflict_fields)}",
                ))
            if plan.causal_visibility not in {"direct", "indirect"}:
                findings.append(ValidationFinding(
                    "invalid_causal_visibility",
                    "blocker",
                    "causal_visibility must be direct or indirect",
                    f"{plan.concept_id}: {plan.causal_visibility or '<missing>'}",
                ))
            if conflict_contract is not None:
                unknown_conflict = (
                    set(plan.conflict_character_ids)
                    - set(conflict_contract.pressure_character_ids)
                )
                if unknown_conflict:
                    findings.append(ValidationFinding(
                        "conflict_actor_mismatch",
                        "blocker",
                        "Plan conflict characters must come from the approved pressure-character contract",
                        f"{plan.concept_id}: {', '.join(sorted(unknown_conflict))}",
                    ))
                if not set(plan.conflict_character_ids).issubset(set(plan.cast)):
                    findings.append(ValidationFinding(
                        "conflict_actor_not_cast",
                        "blocker",
                        "Every declared conflict character must be visibly cast in that plan",
                        plan.concept_id,
                    ))
                if not set(plan.story_evidence_refs).intersection(conflict_contract.evidence_refs):
                    findings.append(ValidationFinding(
                        "missing_conflict_evidence",
                        "blocker",
                        "Every plan must cite evidence used by the book-level core conflict contract",
                        plan.concept_id,
                    ))

    if not conflict_profile and len(brief.principal_characters) > 1 and not any(
        len(plan.cast) >= 2 for plan in direction.plans
    ):
        findings.append(ValidationFinding(
            "missing_relationship_scene",
            "blocker",
            "At least one adaptive direction must show the central relationship conflict",
            reader_anchor_id,
        ))

    if conflict_profile and conflict_contract is not None:
        findings.extend(_validate_core_conflict_coverage(direction))
    if story_policy is not None:
        findings.extend(_validate_story_cast_coverage(brief, direction))

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


def _validate_core_conflict_contract(
    brief: CoverBriefV2,
    direction: ArtDirectionSet,
) -> list[ValidationFinding]:
    contract = direction.core_conflict_visual_contract
    if contract is None:
        return []
    findings: list[ValidationFinding] = []
    known = {item.character_id for item in brief.principal_characters}
    reader_anchor_id = brief.reader_anchor_character.character_id
    if contract.protagonist_character_id != reader_anchor_id:
        findings.append(ValidationFinding(
            "conflict_protagonist_mismatch",
            "blocker",
            "Core conflict contract must anchor the approved reader protagonist",
            f"expected {reader_anchor_id}; received {contract.protagonist_character_id}",
        ))
    unknown_pressure = set(contract.pressure_character_ids) - known
    if unknown_pressure:
        findings.append(ValidationFinding(
            "identity_invention",
            "blocker",
            "Core conflict contract references unapproved pressure characters",
            ", ".join(sorted(unknown_pressure)),
        ))
    if reader_anchor_id in contract.pressure_character_ids:
        findings.append(ValidationFinding(
            "conflict_pressure_self_reference",
            "blocker",
            "The protagonist cannot also be classified as the visible opposing pressure",
            reader_anchor_id,
        ))

    # If the approved core-conflict sentence explicitly names another principal
    # character, the director may not omit that person from the causal contract.
    conflict_text = brief.core_conflict.casefold()
    explicitly_named = {
        item.character_id
        for item in brief.principal_characters
        if item.character_id != reader_anchor_id
        and item.name.strip()
        and re.search(
            rf"(?<!\w){re.escape(item.name.casefold())}(?!\w)", conflict_text
        )
    }
    if uses_story_policy(direction.profile_version):
        policy = story_cast_policy(brief, len(direction.plans))
        explicitly_named = set(policy["pressure_character_ids"])
        if set(policy["focal_character_ids"]).intersection(contract.pressure_character_ids):
            findings.append(ValidationFinding("conflict_pressure_self_reference", "blocker", "Co-leads cannot be substituted for the story's opposing pressure"))
    omitted = explicitly_named - set(contract.pressure_character_ids)
    if omitted:
        findings.append(ValidationFinding(
            "conflict_actor_omitted",
            "blocker",
            "The conflict contract omits source-approved pressure characters",
            ", ".join(sorted(omitted)),
        ))
    for reference in contract.evidence_refs:
        if not _evidence_exists(brief, direction, reference):
            findings.append(ValidationFinding(
                "unknown_conflict_evidence",
                "blocker",
                "Core conflict contract references unknown story evidence",
                reference,
            ))
    return findings


def _validate_core_conflict_coverage(
    direction: ArtDirectionSet,
) -> list[ValidationFinding]:
    contract = direction.core_conflict_visual_contract
    if contract is None:
        return []
    findings: list[ValidationFinding] = []
    plans = direction.plans
    count = len(plans)
    flexible = direction.profile_version.casefold().startswith(("cover-profiles.v7", "cover-profiles.v8", "cover-profiles.v9"))
    direct_minimum = 1 if flexible else math.ceil(count * 0.75)
    action_minimum = 1 if flexible else math.ceil(count * 0.75)
    ensemble_minimum = 1 if flexible else math.ceil(count * 0.5)
    pressure = set(contract.pressure_character_ids)

    direct = [plan for plan in plans if plan.causal_visibility == "direct"]
    if len(direct) < direct_minimum:
        findings.append(ValidationFinding(
            "portfolio_conflict_undercoverage",
            "blocker",
            "The portfolio must include direct causal pressure while preserving different compositions",
            f"direct {len(direct)}/{count}; required {direct_minimum}",
        ))
    actions = [plan for plan in plans if plan.protagonist_action_visible]
    if len(actions) < action_minimum:
        findings.append(ValidationFinding(
            "protagonist_action_undercoverage",
            "blocker",
            "The portfolio must include a protagonist making or enacting a decision",
            f"visible action {len(actions)}/{count}; required {action_minimum}",
        ))
    deliveries = [" ".join(plan.conflict_delivery.casefold().split()) for plan in plans]
    if all(deliveries) and len(set(deliveries)) != len(deliveries):
        findings.append(ValidationFinding(
            "duplicate_conflict_delivery",
            "blocker",
            "Every plan must use a distinct visual method to communicate the same core conflict",
        ))

    if not pressure:
        return findings
    for plan in direct:
        declared = set(plan.conflict_character_ids)
        if not declared or not declared.issubset(set(plan.cast)) or not declared.intersection(pressure):
            findings.append(ValidationFinding(
                "missing_causal_relationship",
                "blocker",
                "A direct-conflict plan must visibly cast and identify an approved source of pressure",
                plan.concept_id,
            ))
    if uses_story_policy(direction.profile_version):
        # v9 requires a readable protagonist/opponent encounter, not all
        # antagonists crowded into one invented scene.
        return findings
    full_ensemble = [
        plan for plan in plans
        if plan.causal_visibility == "direct"
        and pressure.issubset(set(plan.cast))
        and pressure.issubset(set(plan.conflict_character_ids))
    ]
    if len(full_ensemble) < ensemble_minimum:
        findings.append(ValidationFinding(
            "replacement_relationship_undercoverage",
            "blocker",
            "The portfolio must meet its versioned complete causal relationship coverage",
            f"complete relationship {len(full_ensemble)}/{count}; required {ensemble_minimum}",
        ))
    return findings


def _validate_story_cast_coverage(brief: CoverBriefV2, direction: ArtDirectionSet) -> list[ValidationFinding]:
    policy = story_cast_policy(brief, len(direction.plans))
    heroes = set(policy["focal_character_ids"])
    central = set(policy["central_character_ids"])
    pressure = set(policy["primary_pressure_character_ids"])
    relationship_plans = [
        plan for plan in direction.plans
        if len(set(plan.cast) & central) >= 2 and heroes.intersection(plan.cast)
    ]
    findings: list[ValidationFinding] = []
    if len(relationship_plans) < policy["minimum_relationship_plans"]:
        findings.append(ValidationFinding(
            "relationship_scene_undercoverage", "blocker",
            "Relationship stories need readable interactions in all but at most one direction",
            f"{len(relationship_plans)} scenes; required {policy['minimum_relationship_plans']}",
        ))
    cast_sets = {frozenset(plan.cast) & central for plan in relationship_plans}
    if len(cast_sets) < policy["minimum_distinct_cast_sets"]:
        findings.append(ValidationFinding("relationship_cast_repeated", "blocker", "Explore different central relationships, not the same pair with different props"))
    if policy["require_colead_scene"] and not any(len(heroes.intersection(plan.cast)) >= 2 for plan in relationship_plans):
        findings.append(ValidationFinding("colead_relationship_missing", "blocker", "Show the source-approved co-leads acting or reacting together in at least one scene"))
    if pressure and not any(
        plan.causal_visibility == "direct" and pressure.intersection(plan.conflict_character_ids)
        and pressure.intersection(plan.cast) and heroes.intersection(plan.cast)
        for plan in relationship_plans
    ):
        findings.append(ValidationFinding("missing_causal_relationship", "blocker", "At least one direction must visibly stage a lead and the primary opposing person in a source-supported encounter"))
    return findings


def _validate_campaign_direction(
    brief: CoverBriefV2, direction: ArtDirectionSet,
    story_analysis: Mapping[str, Any] | None,
    *, defer_campaign_evidence_review: bool = False,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    commercial = uses_commercial_expression(direction.profile_version)
    approved_paths, receipt_findings = approved_evidence_claim_paths(direction, story_analysis)
    findings.extend(ValidationFinding(item.code, "blocker", item.message, item.evidence)
                    for item in receipt_findings)
    if defer_campaign_evidence_review:
        # Deferral is a pre-review step, never permission to accept unknown
        # evidence, absent quotes or ambiguous event/claim identities.
        try:
            build_evidence_claims(direction, story_analysis)
        except FrozenCampaignEvidenceError as exc:
            findings.append(ValidationFinding("campaign_evidence_source_invalid", "blocker", str(exc)))
        except (TypeError, ValueError) as exc:
            findings.append(ValidationFinding("campaign_evidence_claims_invalid", "blocker", str(exc)))
    if story_analysis is None or story_analysis.get("coverage_status") != "complete":
        findings.append(ValidationFinding("campaign_analysis_required", "blocker", "v10 directions require the complete frozen story analysis"))
    allowed_refs = set(direction.evidence_ledger.allowed_refs) if direction.evidence_ledger else set()
    if not allowed_refs:
        findings.append(ValidationFinding("missing_evidence_ledger", "blocker", "Campaign requires application-owned frozen evidence"))
    policy = story_cast_policy(brief, len(direction.plans))
    heroes = set(policy["focal_character_ids"])
    campaigns = []
    choice_found = False
    for plan in direction.plans:
        issue = photographic_medium_issue(plan.art_style)
        if issue:
            findings.append(ValidationFinding("non_photographic_medium", "blocker", issue, plan.concept_id))
        if not commercial and plan.focal_character_id not in heroes:
            findings.append(ValidationFinding("reader_anchor_not_focal", "blocker", "Campaign must visibly anchor an approved protagonist or co-lead", plan.concept_id))
        missing = [field for field in ("conflict_delivery", "conflict_read", "cause_signal", "consequence_signal")
                   if not str(getattr(plan, field, "") or "").strip()]
        if missing and not commercial:
            findings.append(ValidationFinding("missing_core_conflict", "blocker",
                "Every campaign direction must declare its visible cause and protagonist response",
                f"{plan.concept_id}: {', '.join(missing)}"))
        if plan.causal_visibility not in ({"", "direct", "indirect"} if commercial else {"direct", "indirect"}):
            findings.append(ValidationFinding("invalid_causal_visibility", "blocker",
                "causal_visibility must be direct or indirect", plan.concept_id))
        try:
            campaign = CoverCampaignPlan.from_dict(getattr(plan, "campaign", None))
        except (TypeError, ValueError) as exc:
            findings.append(ValidationFinding("campaign_schema", "blocker", str(exc), plan.concept_id))
            continue
        campaigns.append(campaign)
        if campaign.expression_mode and not commercial:
            findings.append(ValidationFinding("campaign_expression_profile", "blocker",
                "Commercial expression requires a new v11 direction; historical direction semantics cannot change", plan.concept_id))
        findings.extend(ValidationFinding(item.code, "warning" if commercial and item.code in {
                            "campaign_static_interaction", "campaign_generic_hook", "campaign_missing_visual_trigger"
                        } else item.severity, item.message, f"{plan.concept_id}: {item.evidence}")
                        for item in validate_campaign(campaign, cast=plan.cast, title=brief.title,
                            allowed_refs=allowed_refs, story_analysis=story_analysis,
                            approved_claim_paths=approved_paths.get(plan.concept_id, set()),
                            defer_evidence_review=defer_campaign_evidence_review))
        choice_found |= plan.protagonist_action_visible and any(
            edge["actor_id"] in heroes for edge in campaign.interaction_edges)
    # Structural completeness is necessary, not semantic proof. In particular,
    # events.participants includes causal/remote/absent participants; it cannot
    # prove physical co-presence. The independent source review must enforce a
    # relationship direction when the narrative supports a drawable encounter,
    # and must verify the declared cause, response and choice against the source.
    if not choice_found and not commercial:
        findings.append(ValidationFinding("protagonist_action_undercoverage", "blocker",
            "At least one direction must declare a source-supported choice by a visible protagonist or co-lead acting in its event"))
    findings.extend(ValidationFinding(item.code, "warning" if commercial else "blocker", item.message, item.evidence)
                    for item in portfolio_findings(campaigns))
    return findings


def validate_direction(
    brief: CoverBriefV2,
    direction: ArtDirectionSet,
    *,
    recent_fingerprints: Sequence[Mapping[str, Any]] = (),
    story_analysis: Mapping[str, Any] | None = None,
    verified_constraint_source_refs: set[str] | None = None,
    defer_campaign_evidence_review: bool = False,
) -> tuple[ValidationFinding, ...]:
    findings: list[ValidationFinding] = []
    findings.extend(_validate_story_constraints(
        brief, direction, verified_source_refs=verified_constraint_source_refs,
        story_analysis=story_analysis,
    ))
    if direction.brief_sha256 != brief.source_prompt_sha256:
        findings.append(ValidationFinding(
            "brief_hash_mismatch", "blocker", "Direction is bound to a different brief hash",
        ))
    pending_critical = [
        *brief.blocking_pending_critical_assumptions(),
        *(
            item for item in direction.visual_assumptions
            if item.critical
            and item.status == "pending_confirmation"
            and item.provenance != "model_inferred"
        ),
    ]
    for assumption in pending_critical:
        findings.append(ValidationFinding(
            "unresolved_critical_assumption",
            "blocker",
            "Blocking story facts must be confirmed before art direction approval; visual appearance may remain model-inferred",
            assumption.field,
        ))
    required = {item.character_id for item in brief.required_characters}
    known = {item.character_id for item in brief.principal_characters}
    campaign_profile = uses_campaign(direction.profile_version)
    adaptive = campaign_profile or direction.profile_version.casefold().startswith(("cover-profiles.v4", "cover-profiles.v5", "cover-profiles.v6", "cover-profiles.v7", "cover-profiles.v8", "cover-profiles.v9"))
    optional_conflict_cast = {
        item.character_id for item in brief.principal_characters if not item.must_appear
    }
    strategies = [item.visual_strategy.casefold() for item in direction.plans]
    hooks = [item.visual_hook.hook_type.casefold() for item in direction.plans]
    if len(set(strategies)) != len(strategies):
        findings.append(ValidationFinding("duplicate_strategy", "warning" if uses_commercial_expression(direction.profile_version) else "blocker", "Direction strategies must be distinct"))
    if not adaptive and len(set(hooks)) != len(hooks):
        findings.append(ValidationFinding("duplicate_hook", "blocker", "Visual hooks must be distinct"))
    if campaign_profile:
        findings.extend(_validate_campaign_direction(brief, direction, story_analysis,
                        defer_campaign_evidence_review=defer_campaign_evidence_review))
    elif adaptive:
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
