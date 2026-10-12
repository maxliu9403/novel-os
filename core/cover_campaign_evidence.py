"""Source-bound claim coverage for the independent campaign evidence review.

Reference overlap is a structural default, not proof that an action is true.
Only a complete application-owned review receipt can supersede that default.
Receipts bind the full analysis and serialized plans, never the model's hash.
The director must strip model-supplied source_binding before creating a receipt.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

try:
    from .cover_campaign import CampaignFinding, CoverCampaignPlan, uses_commercial_expression
except ImportError:  # pragma: no cover - legacy CLI imports
    from cover_campaign import CampaignFinding, CoverCampaignPlan, uses_commercial_expression


_SCHEMA = "campaign-evidence-review.v1"


class FrozenCampaignEvidenceError(ValueError):
    """Frozen source data is invalid; a paid design repair cannot fix it."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _copy(value: Any) -> Any:
    return json.loads(_json(value))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _registry(items: Any, key: str, *, allow_empty: bool = False) -> dict[str, Mapping[str, Any]]:
    if not isinstance(items, (list, tuple)) or (not items and not allow_empty):
        raise FrozenCampaignEvidenceError(f"Frozen analysis {key} registry must be a nonempty array")
    result = {}
    for item in items:
        identifier = item.get(key) if isinstance(item, Mapping) else None
        if not isinstance(identifier, str) or not identifier.strip():
            raise FrozenCampaignEvidenceError(f"Frozen analysis requires an exact {key}")
        if identifier in result:
            raise FrozenCampaignEvidenceError(f"Duplicate frozen {key}: {identifier}")
        result[identifier] = item
    return result


def _refs(value: Any, registry: Mapping[str, Mapping[str, Any]], label: str, *, frozen: bool = False) -> list[str]:
    error = FrozenCampaignEvidenceError if frozen else ValueError
    if not isinstance(value, (list, tuple)) or not value:
        raise error(f"{label} requires nonempty exact references in the frozen evidence registry")
    for ref in value:
        if not isinstance(ref, str) or ref not in registry:
            continue
        quote = registry[ref].get("exact_quote")
        if not isinstance(quote, str) or not quote.strip():
            raise FrozenCampaignEvidenceError(f"{label} requires complete cited source quotes; missing quote for {ref}")
    if any(not isinstance(ref, str) or ref not in registry for ref in value):
        raise error(f"{label} requires nonempty exact references in the frozen evidence registry")
    return list(value)


def build_evidence_claims(direction: Any, analysis: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Enumerate every story layer and interaction, including shared anchors.

    The existing frozen-source loader verifies quote identity and provenance.
    This boundary additionally rejects missing/ambiguous IDs and missing quotes.
    Quote bytes remain in the one evidence registry, never repeated per claim.
    """
    if not isinstance(analysis, Mapping) or analysis.get("coverage_status") != "complete":
        raise FrozenCampaignEvidenceError("Evidence review requires complete frozen analysis")
    evidence = {"evidence:" + key: value for key, value in
                _registry(analysis.get("evidence"), "evidence_id").items()}
    commercial = uses_commercial_expression(direction.profile_version)
    events = _registry(analysis.get("events"), "event_id", allow_empty=commercial)
    claims = []
    concepts = set()
    for plan in direction.plans:
        if not isinstance(plan.concept_id, str) or not plan.concept_id.strip():
            raise ValueError("Evidence review requires exact concept IDs")
        if plan.concept_id in concepts:
            raise ValueError(f"Duplicate concept_id: {plan.concept_id}")
        concepts.add(plan.concept_id)
        campaign = CoverCampaignPlan.from_dict(plan.campaign)
        if campaign.expression_mode and not commercial:
            raise ValueError("campaign.expression_mode requires cover-profiles.v11")
        layers = {layer["layer_id"]: layer for layer in campaign.layers}
        if len(layers) != len(campaign.layers):
            raise ValueError(f"Duplicate layer_id in {plan.concept_id}")
        if campaign.expression_mode == "editorial":
            # These are factual anchors for the overall commercial meaning, not
            # proof obligations for every pose or promotional visual metaphor.
            # Declared literal events/physical interactions are still enumerated below.
            refs = []
            for label, values in [("story_evidence_refs", plan.story_evidence_refs),
                                  ("hook", campaign.hook["evidence_refs"]),
                                  *((f"layer {layer['layer_id']}", layer["evidence_refs"]) for layer in campaign.layers),
                                  *((f"interaction {i}", edge["evidence_refs"]) for i, edge in enumerate(campaign.interaction_edges)),
                                  *((f"relationship {i}", link["evidence_refs"]) for i, link in enumerate(campaign.layer_relationships)
                                    if link["evidence_refs"])]:
                refs.extend(_refs(values, evidence, f"{plan.concept_id}:{label}"))
            claims.append({"claim_id": f"{plan.concept_id}:campaign", "concept_id": plan.concept_id,
                "path": "campaign", "kind": "editorial_expression", "layer_id": None, "event_id": None,
                "claimed_content": _copy(plan.to_dict()), "source_event": None,
                "evidence_refs": list(dict.fromkeys(refs)), "event_evidence_refs": []})

        def add(path: str, kind: str, value: Mapping[str, Any], layer: Mapping[str, Any]) -> None:
            event_id = layer.get("event_id")
            if layer.get("type") != "story_moment" or event_id not in events:
                raise ValueError(f"{plan.concept_id}:{path} requires a known source story event")
            event = events[event_id]
            own_refs = _refs(value.get("evidence_refs"), evidence, f"{plan.concept_id}:{path}")
            event_refs = _refs(event.get("evidence_refs"), evidence, f"source event {event_id}", frozen=True)
            claims.append({"claim_id": f"{plan.concept_id}:{path}", "concept_id": plan.concept_id,
                "path": path, "kind": kind, "layer_id": layer["layer_id"], "event_id": event_id,
                "claimed_content": _copy(value), "source_event": _copy(event),
                "evidence_refs": own_refs, "event_evidence_refs": event_refs})

        for index, layer in enumerate(campaign.layers):
            if layer["type"] == "story_moment":
                add(f"campaign.layers[{index}]", "story_moment", layer, layer)
        for index, edge in enumerate(campaign.interaction_edges):
            layer = layers.get(edge["layer_id"])
            if layer is None:
                raise ValueError(f"{plan.concept_id}:campaign.interaction_edges[{index}] has an unknown layer")
            add(f"campaign.interaction_edges[{index}]", "interaction", edge, layer)
    if not claims:
        raise ValueError("Evidence review requires story claims")
    claim_ids = [claim["claim_id"] for claim in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("Duplicate evidence claim IDs")
    return tuple(claims)


def _finding(code: str, message: str, **detail: Any) -> CampaignFinding:
    return CampaignFinding(code, message, _json(detail))


def validate_evidence_reviews(direction: Any, analysis: Mapping[str, Any], reviews: Any, *,
                              policy_version: str = "", advisories: Any = ()
                              ) -> tuple[dict[str, Any] | None, tuple[CampaignFinding, ...]]:
    """Fail closed on missing coverage, ambiguous verdicts or borrowed citations."""
    try:
        claims = {claim["claim_id"]: claim for claim in build_evidence_claims(direction, analysis)}
    except FrozenCampaignEvidenceError as exc:
        return None, (_finding("campaign_evidence_source_invalid", str(exc), path="story_analysis"),)
    except (TypeError, ValueError) as exc:
        return None, (_finding("campaign_evidence_claims_invalid", str(exc), path="evidence_claims"),)
    if not isinstance(policy_version, str) or policy_version not in {"", "cover-commercial-review.v1"}:
        return None, (_finding("campaign_evidence_review_invalid", "Unknown evidence review policy"),)
    if not isinstance(advisories, (list, tuple)) or any(not isinstance(item, Mapping) for item in advisories):
        return None, (_finding("campaign_evidence_review_invalid", "Review advisories must be objects"),)
    if not isinstance(reviews, list):
        return None, (_finding("campaign_evidence_review_incomplete", "Independent review must cover every exact claim",
                               path="evidence_reviews", missing_claim_ids=list(claims)),)
    findings = []
    seen = set()
    clean = []
    for index, review in enumerate(reviews):
        identifier = review.get("claim_id") if isinstance(review, Mapping) else None
        if not isinstance(identifier, str) or identifier not in claims or identifier in seen:
            findings.append(_finding("campaign_evidence_review_incomplete", "Review claim ID is unknown, missing or duplicated",
                                     path=f"evidence_reviews[{index}]", claim_id=identifier))
            continue
        seen.add(identifier)
        claim = claims[identifier]
        verdict, reasoning, refs = review.get("verdict"), review.get("reasoning"), review.get("evidence_refs")
        valid_refs = (isinstance(refs, list) and bool(refs)
                      and all(isinstance(ref, str) and ref in claim["evidence_refs"] for ref in refs))
        if (not isinstance(verdict, str) or verdict not in {"supported", "unsupported", "uncertain"}
                or not isinstance(reasoning, str) or not reasoning.strip() or not valid_refs):
            findings.append(_finding("campaign_evidence_review_invalid", "Each verdict needs reasoning and exact evidence belonging to this claim",
                claim_id=identifier, path=claim["path"], layer_id=claim["layer_id"], event_id=claim["event_id"],
                claim_evidence_refs=claim["evidence_refs"], event_evidence_refs=claim["event_evidence_refs"],
                review_evidence_refs=refs, verdict=verdict))
            continue
        if verdict != "supported":
            findings.append(_finding("campaign_evidence_unsupported", "Independent review did not establish support for this exact source-bound claim",
                claim_id=identifier, path=claim["path"], layer_id=claim["layer_id"], event_id=claim["event_id"],
                verdict=verdict, reasoning=reasoning.strip(), evidence_refs=refs,
                required="Repair this claim against its own complete source quotes without borrowing an unrelated event anchor or inventing an action."))
        clean.append({"claim_id": identifier, "verdict": verdict, "reasoning": reasoning.strip(), "evidence_refs": list(refs)})
    missing = sorted(set(claims) - seen)
    if missing:
        findings.append(_finding("campaign_evidence_review_incomplete", "Independent evidence review omitted claims",
                                 path="evidence_reviews", missing_claim_ids=missing))
    if findings:
        return None, tuple(findings)
    return {"schema": _SCHEMA, "analysis_sha256": _digest(analysis),
            "plans_sha256": _digest([plan.to_dict() for plan in direction.plans]),
            "evidence_reviews": clean,
            **({"policy_version": policy_version, "advisories": _copy(advisories)} if policy_version else {})}, ()


def build_evidence_review_receipt(direction: Any, analysis: Mapping[str, Any], reviews: Any, *,
                                  policy_version: str = "", advisories: Any = ()) -> dict[str, Any]:
    """Create a full-plan receipt only after exact claim coverage is verified."""
    receipt, findings = validate_evidence_reviews(direction, analysis, reviews,
                                                policy_version=policy_version, advisories=advisories)
    if findings or receipt is None:
        raise ValueError("; ".join(f"{finding.code}: {finding.message}" for finding in findings))
    return receipt


def approved_evidence_claim_paths(direction: Any, analysis: Mapping[str, Any] | None
                                  ) -> tuple[dict[str, set[str]], tuple[CampaignFinding, ...]]:
    """Validate an application-owned receipt before bypassing overlap checks."""
    binding = direction.source_binding
    if not isinstance(binding, Mapping) or "campaign_evidence_review" not in binding:
        return {}, ()
    receipt = binding["campaign_evidence_review"]
    invalid = (_finding("campaign_evidence_review_invalid", "Stored evidence review is incomplete or bound to different analysis or plans",
                        path="source_binding.campaign_evidence_review"),)
    if (not isinstance(receipt, Mapping) or receipt.get("schema") != _SCHEMA
            or not isinstance(analysis, Mapping)):
        return {}, invalid
    try:
        if (receipt.get("analysis_sha256") != _digest(analysis)
                or receipt.get("plans_sha256") != _digest([plan.to_dict() for plan in direction.plans])):
            return {}, invalid
        validated, findings = validate_evidence_reviews(direction, analysis, receipt.get("evidence_reviews"),
            policy_version=receipt.get("policy_version", ""), advisories=receipt.get("advisories", ()))
        if findings or validated is None:
            return {}, invalid + findings
        paths: dict[str, set[str]] = {}
        for claim in build_evidence_claims(direction, analysis):
            paths.setdefault(claim["concept_id"], set()).add(claim["path"])
        return paths, ()
    except (TypeError, ValueError):
        return {}, invalid


def approved_reusable_claim_reviews(prior_direction: Any, current_direction: Any,
                                   analysis: Mapping[str, Any], unchanged_concept_ids: Any
                                   ) -> list[dict[str, Any]] | None:
    """Reuse only app-loaded, fully bound reviews for byte-identical unchanged plans.

    The caller owns loading the real saved direction. An arbitrary model response
    or embedded repair-base receipt is not a source of review authority.
    """
    try:
        if not isinstance(unchanged_concept_ids, (list, tuple, set, frozenset)):
            return None
        requested = set(unchanged_concept_ids)
        if any(not isinstance(value, str) or not value for value in requested):
            return None
        _, findings = approved_evidence_claim_paths(prior_direction, analysis)
        receipt = (prior_direction.source_binding or {}).get("campaign_evidence_review")
        if findings or not isinstance(receipt, Mapping):
            return None
        previous, current = prior_direction.to_dict(), current_direction.to_dict()
        for key in ("brief_sha256", "visual_identity", "visual_assumptions", "core_conflict_visual_contract", "evidence_ledger"):
            if _json(previous.get(key)) != _json(current.get(key)):
                return None
        prior_plans = {plan.concept_id: plan for plan in prior_direction.plans}
        current_plans = {plan.concept_id: plan for plan in current_direction.plans}
        for concept_id in requested:
            if (concept_id not in prior_plans or concept_id not in current_plans
                    or _json(prior_plans[concept_id].to_dict()) != _json(current_plans[concept_id].to_dict())):
                return None
        old_claims = {claim["claim_id"]: claim for claim in build_evidence_claims(prior_direction, analysis)}
        expected = {claim["claim_id"]: claim for claim in build_evidence_claims(current_direction, analysis)
                    if claim["concept_id"] in requested}
        if any(identifier not in old_claims or _json(claim) != _json(old_claims[identifier])
               for identifier, claim in expected.items()):
            return None
        reviews = {review["claim_id"]: review for review in receipt["evidence_reviews"]}
        if any(identifier not in reviews for identifier in expected):
            return None
        return [_copy(reviews[identifier]) for identifier in expected]
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
