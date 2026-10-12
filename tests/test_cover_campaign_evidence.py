"""Exact references locate claims; independent evidence review establishes support."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from core.cover_campaign import CoverCampaignPlan, validate_campaign
from core.cover_campaign_evidence import (
    FrozenCampaignEvidenceError, approved_evidence_claim_paths, build_evidence_claims, validate_evidence_reviews,
)
from core.cover_director import CoverArtDirector
from core.cover_validator import validate_direction
from tests.test_cover_campaign import context


def example():
    brief, ledger, payload, analysis = context()
    for event in analysis["events"]:
        event["evidence_refs"] = [ledger.allowed_refs[0]]
    campaign = payload["plans"][0]["campaign"]
    campaign["layers"][0]["evidence_refs"] = [ledger.allowed_refs[1]]
    campaign["interaction_edges"][0]["evidence_refs"] = [ledger.allowed_refs[2]]
    direction = CoverArtDirector.from_fixture(payload).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    return brief, direction, analysis


def supported(direction, analysis):
    return [{"claim_id": claim["claim_id"], "verdict": "supported",
             "reasoning": "The cited full source establishes this action within the specified event.",
             "evidence_refs": claim["evidence_refs"]}
            for claim in build_evidence_claims(direction, analysis)]


def test_claims_cover_every_story_layer_and_edge_with_exact_context_without_duplicate_quotes():
    _, direction, analysis = example()
    claims = build_evidence_claims(direction, analysis)
    assert len(claims) == 8
    assert len({claim["claim_id"] for claim in claims}) == len(claims)
    assert claims[0]["claim_id"] == "concept-1:campaign.layers[0]"
    assert claims[1]["claim_id"] == "concept-1:campaign.interaction_edges[0]"
    assert claims[1]["source_event"] == analysis["events"][0]
    assert claims[1]["claimed_content"] == direction.plans[0].campaign["interaction_edges"][0]
    assert claims[1]["evidence_refs"] != claims[1]["event_evidence_refs"]
    assert "exact_quote" not in json.dumps(claims)


def test_disjoint_refs_remain_strict_until_exact_supported_application_receipt_exists():
    brief, direction, analysis = example()
    original = deepcopy(direction.to_dict())
    assert {f.code for f in validate_direction(brief, direction, story_analysis=analysis)} >= {
        "campaign_event_evidence", "campaign_interaction_evidence"}
    assert validate_direction(brief, direction, story_analysis=analysis,
                              defer_campaign_evidence_review=True) == ()
    receipt, findings = validate_evidence_reviews(direction, analysis, supported(direction, analysis))
    assert not findings and receipt
    reviewed = replace(direction, source_binding={"campaign_evidence_review": receipt})
    assert validate_direction(brief, reviewed, story_analysis=analysis) == ()
    assert direction.to_dict() == original  # No automatic reference repair or historical serialization change.
    paths, findings = approved_evidence_claim_paths(reviewed, analysis)
    assert not findings and paths["concept-1"] == {
        "campaign.layers[0]", "campaign.interaction_edges[0]"}


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "uncertain", "unsupported",
                                      "empty_reason", "empty_refs", "unrelated_anchor", "unknown_ref", "malformed"])
def test_incomplete_or_unsupported_review_never_produces_receipt(mutation):
    _, direction, analysis = example()
    reviews = supported(direction, analysis)
    if mutation == "missing": reviews.pop()
    elif mutation == "duplicate": reviews.append(deepcopy(reviews[0]))
    elif mutation == "unknown": reviews[0]["claim_id"] = "foreign:campaign.layers[0]"
    elif mutation in {"uncertain", "unsupported"}: reviews[0]["verdict"] = mutation
    elif mutation == "empty_reason": reviews[0]["reasoning"] = " "
    elif mutation == "empty_refs": reviews[0]["evidence_refs"] = []
    elif mutation == "unrelated_anchor": reviews[1]["evidence_refs"] = analysis["events"][0]["evidence_refs"]
    elif mutation == "unknown_ref": reviews[0]["evidence_refs"] = ["evidence:missing"]
    else: reviews = {"accepted": True}
    receipt, findings = validate_evidence_reviews(direction, analysis, reviews)
    assert receipt is None and findings


@pytest.mark.parametrize("mutation", ["plan", "analysis", "review", "schema"])
def test_receipt_is_bound_to_full_analysis_plans_and_complete_review(mutation):
    brief, direction, analysis = example()
    receipt, _ = validate_evidence_reviews(direction, analysis, supported(direction, analysis))
    reviewed = replace(direction, source_binding={"campaign_evidence_review": receipt})
    if mutation == "plan":
        reviewed = replace(reviewed, plans=(replace(reviewed.plans[0], design_rationale="changed"), *reviewed.plans[1:]))
    elif mutation == "analysis":
        analysis = deepcopy(analysis); analysis["story"]["core_conflict"] += " changed"
    elif mutation == "review": receipt["evidence_reviews"][0]["verdict"] = "uncertain"
    else: receipt["schema"] = "foreign"
    assert "campaign_evidence_review_invalid" in {
        f.code for f in validate_direction(brief, reviewed, story_analysis=analysis)}


@pytest.mark.parametrize("mutation,code", [
    ("unknown_ref", "campaign_unknown_evidence"),
    ("missing_registry_quote", "campaign_evidence_source_invalid"),
    ("unknown_event", "campaign_unknown_event"),
    ("cast", "campaign_interaction_cast"),
    ("time", "campaign_event_time_place"),
])
def test_deferral_never_bypasses_source_cast_event_or_time_errors(mutation, code):
    brief, direction, analysis = example()
    campaign = deepcopy(direction.plans[0].campaign)
    if mutation == "unknown_ref": campaign["interaction_edges"][0]["evidence_refs"] = ["evidence:missing"]
    elif mutation == "missing_registry_quote": analysis["evidence"][0]["exact_quote"] = ""
    elif mutation == "unknown_event":
        campaign["event_ids"] = ["missing"]; campaign["layers"][0]["event_id"] = "missing"
    elif mutation == "cast": campaign["interaction_edges"][0]["actor_id"] = "char_absent"
    else: campaign["layers"][0]["epoch"] = "another time"
    direction = replace(direction, plans=(replace(direction.plans[0], campaign=campaign), *direction.plans[1:]))
    assert code in {f.code for f in validate_direction(brief, direction, story_analysis=analysis,
                                                     defer_campaign_evidence_review=True)}


def test_three_edges_in_one_layer_have_distinguishable_repair_diagnostics():
    _, direction, analysis = example()
    campaign = deepcopy(direction.plans[0].campaign)
    campaign["interaction_edges"] *= 3
    findings = validate_campaign(CoverCampaignPlan.from_dict(campaign), cast=direction.plans[0].cast,
        title=campaign["title_layout"]["title"], allowed_refs=set(direction.evidence_ledger.allowed_refs),
        story_analysis=analysis)
    details = [json.loads(f.evidence) for f in findings if f.code == "campaign_interaction_evidence"]
    assert [row["path"] for row in details] == [f"campaign.interaction_edges[{i}].evidence_refs" for i in range(3)]
    assert [row["edge_index"] for row in details] == [0, 1, 2]
    assert all(row["edge_evidence_refs"] == campaign["interaction_edges"][0]["evidence_refs"] for row in details)
    assert all(row["event_evidence_refs"] == analysis["events"][0]["evidence_refs"] for row in details)


@pytest.mark.parametrize("duplicate", ["concept", "event", "evidence"])
def test_ambiguous_identifiers_cannot_make_review_coverage_look_complete(duplicate):
    _, direction, analysis = example()
    if duplicate == "concept": direction = replace(direction, plans=(*direction.plans, direction.plans[0]))
    elif duplicate == "event": analysis["events"].append(deepcopy(analysis["events"][0]))
    else: analysis["evidence"].append(deepcopy(analysis["evidence"][0]))
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        build_evidence_claims(direction, analysis)


@pytest.mark.parametrize("mutation", ["missing_registry", "empty_registry", "duplicate_evidence",
                                      "duplicate_event", "unknown_event_anchor", "empty_quote"])
def test_frozen_source_failures_are_not_repairable_model_claim_errors(mutation):
    brief, direction, analysis = example()
    if mutation == "missing_registry": analysis.pop("evidence")
    elif mutation == "empty_registry": analysis["evidence"] = []
    elif mutation == "duplicate_evidence": analysis["evidence"].append(deepcopy(analysis["evidence"][0]))
    elif mutation == "duplicate_event": analysis["events"].append(deepcopy(analysis["events"][0]))
    elif mutation == "unknown_event_anchor": analysis["events"][0]["evidence_refs"] = ["evidence:unknown"]
    else: analysis["evidence"][1]["exact_quote"] = ""
    with pytest.raises(FrozenCampaignEvidenceError):
        build_evidence_claims(direction, analysis)
    findings = validate_direction(brief, direction, story_analysis=analysis, defer_campaign_evidence_review=True)
    assert "campaign_evidence_source_invalid" in {f.code for f in findings}
    if mutation == "empty_quote":
        assert any("complete cited source quotes" in f.message for f in findings)


def test_unknown_plan_reference_remains_a_repairable_claim_error():
    brief, direction, analysis = example()
    campaign = deepcopy(direction.plans[0].campaign)
    campaign["interaction_edges"][0]["evidence_refs"] = ["evidence:invented"]
    direction = replace(direction, plans=(replace(direction.plans[0], campaign=campaign), *direction.plans[1:]))
    findings = validate_direction(brief, direction, story_analysis=analysis, defer_campaign_evidence_review=True)
    assert "campaign_evidence_claims_invalid" in {f.code for f in findings}
    assert "campaign_evidence_source_invalid" not in {f.code for f in findings}
