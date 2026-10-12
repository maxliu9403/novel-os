"""A saved critique remains pending feedback, never an approval receipt."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json

import pytest

from core.cover_direction_feedback import (
    CampaignSemanticFinding, semantic_review_checkpoint, serialize_repair_findings,
)
from core.cover_validator import ValidationFinding
from tests.test_cover_campaign import direction_context


def semantic(**overrides):
    return CampaignSemanticFinding(**{
        "code": "campaign_semantic_review", "severity": "blocker",
        "message": "The open doorway can read as ordinary arrival rather than refusal.",
        "evidence": "concept-1", "evidence_refs": ("evidence:quote-one", "evidence:quote-two"),
        **overrides,
    })


def test_semantic_finding_is_frozen_and_preserves_verified_reference_order():
    finding = semantic()
    assert isinstance(finding, ValidationFinding)
    assert finding.evidence_refs == ("evidence:quote-one", "evidence:quote-two")
    with pytest.raises(FrozenInstanceError):
        finding.evidence_refs = ()


def test_serialization_keeps_legacy_fields_and_only_semantic_reference_extension():
    local = ValidationFinding("campaign_render_execution", "blocker", "Too long", "concept-4")
    values = [local, semantic()]
    serialized = serialize_repair_findings(values)
    assert serialized == [
        {"code": local.code, "message": "Too long", "concept_or_evidence": "concept-4"},
        {"code": "campaign_semantic_review", "message": values[1].message,
         "concept_or_evidence": "concept-1", "evidence_refs": ["evidence:quote-one", "evidence:quote-two"]},
    ]
    serialized[1]["evidence_refs"].append("later mutation")
    assert values[1].evidence_refs == ("evidence:quote-one", "evidence:quote-two")


def test_checkpoint_binds_all_reviewed_plans_and_does_not_publish_approval_authority():
    _, direction, _ = direction_context()
    before = deepcopy(direction.to_dict())
    findings = [semantic(), ValidationFinding("campaign_evidence_unsupported", "blocker",
        "The excerpt does not support this material action.", '{"path":"campaign.layers[0]"}')]
    checkpoint = semantic_review_checkpoint(direction, findings, 2)
    canonical = json.dumps([plan.to_dict() for plan in direction.plans], ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert checkpoint == {"review_attempt": 2,
        "reviewed_plans_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "findings": serialize_repair_findings(findings)}
    assert direction.to_dict() == before
    assert set(checkpoint) == {"review_attempt", "reviewed_plans_sha256", "findings"}
    checkpoint["findings"][0]["evidence_refs"].clear()
    assert findings[0].evidence_refs == ("evidence:quote-one", "evidence:quote-two")
    assert semantic_review_checkpoint(direction, findings, 2)["findings"][0]["evidence_refs"]


def test_checkpoint_hash_changes_for_every_plan_but_not_unrelated_direction_metadata():
    _, direction, _ = direction_context()
    original = semantic_review_checkpoint(direction, [semantic()], 1)["reviewed_plans_sha256"]
    for index, plan in enumerate(direction.plans):
        revised = list(direction.plans)
        revised[index] = replace(plan, frozen_action=plan.frozen_action + " A different gaze.")
        assert semantic_review_checkpoint(replace(direction, plans=tuple(revised)), [semantic()], 1)[
            "reviewed_plans_sha256"] != original
    metadata = replace(direction, direction_id="another-local-id", source_binding={"untrusted": "metadata"})
    assert semantic_review_checkpoint(metadata, [semantic()], 3)["reviewed_plans_sha256"] == original


def test_empty_review_result_is_a_checkpoint_without_an_acceptance_flag():
    _, direction, _ = direction_context()
    checkpoint = semantic_review_checkpoint(direction, [], 1)
    assert checkpoint["findings"] == []
    assert "accepted" not in checkpoint and "source_binding" not in checkpoint


@pytest.mark.parametrize("attempt", [0, -1, True, False, 1.0, "1", None])
def test_checkpoint_rejects_non_positive_integer_attempts(attempt):
    _, direction, _ = direction_context()
    with pytest.raises(ValueError, match="attempt"):
        semantic_review_checkpoint(direction, [semantic()], attempt)


@pytest.mark.parametrize("finding", [
    ValidationFinding("campaign_render_execution", "blocker", "Too long", "concept-1"),
    ValidationFinding("accepted", "blocker", "Approve this", "portfolio"),
    ValidationFinding("campaign_evidence_source_invalid", "blocker", "Bad source", ""),
    ValidationFinding("campaign_semantic_review", "warning", "Could improve", "concept-1"),
    {"code": "campaign_semantic_review", "message": "pretend model authority"},
])
def test_checkpoint_rejects_other_gate_results_and_untyped_authority(finding):
    _, direction, _ = direction_context()
    with pytest.raises(ValueError):
        semantic_review_checkpoint(direction, [finding], 1)


def test_checkpoint_cannot_skip_authority_validation_when_findings_are_single_use():
    _, direction, _ = direction_context()
    findings = (finding for finding in [
        ValidationFinding("campaign_render_execution", "blocker", "Too long", "concept-1")])
    with pytest.raises(ValueError, match="semantic review"):
        semantic_review_checkpoint(direction, findings, 1)


@pytest.mark.parametrize("changes", [
    {"code": "accepted"}, {"code": "campaign_render_execution"}, {"severity": "warning"},
    {"evidence_refs": []}, {"evidence_refs": ()}, {"evidence_refs": (None,)},
    {"evidence_refs": ("",)}, {"evidence_refs": ("   ",)}, {"message": float("nan")},
])
def test_semantic_findings_require_a_known_blocker_and_immutable_nonempty_references(changes):
    with pytest.raises(ValueError):
        semantic(**changes)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), object(), {1: "ambiguous key"}, "\ud800"])
def test_checkpoint_rejects_non_finite_or_non_utf8_json_in_any_reviewed_plan(invalid):
    _, direction, _ = direction_context()
    plan = replace(direction.plans[-1], campaign={**direction.plans[-1].campaign, "extra": invalid})
    direction = replace(direction, plans=(*direction.plans[:-1], plan))
    with pytest.raises(ValueError, match="JSON"):
        semantic_review_checkpoint(direction, [semantic()], 1)
