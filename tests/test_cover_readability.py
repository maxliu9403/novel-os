"""Shared readability rules reach facts-aware review without waiving failures.

These mocked request-boundary tests verify the review contract, not whether a
real model or an ungenerated image can achieve the intended visual reading.
"""
import importlib
import json

import pytest

from core.cover_director import CoverArtDirector
from core.cover_quality import build_llm_visual_evaluator
from tests.cover_review_fixtures import supported_review
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_campaign import context
from tests.test_cover_director import _brief, director_fixture
from tests.test_cover_director_source_context import bound  # noqa: F401
from tests.test_cover_quality import _blind_observation, _real_image, _scores


def policy():
    return importlib.import_module("core.cover_readability").cover_readability_policy()


class CaptureReview:
    provider = "fixture"
    model = "fixture-vision"

    def __init__(self, response=None):
        self.calls = []
        self.response = response or _scores()

    def complete_with_images(self, system, user, images):
        request = json.loads(user)
        self.calls.append((system, request, images))
        return json.dumps(self.response if "scene_contract" in request else _blind_observation())


def review(client, *, campaign=True):
    if campaign:
        brief, ledger, payload, analysis = context()
        scene = CoverArtDirector.from_fixture(payload).plan(
            brief, count=4, evidence_ledger=ledger, story_analysis=analysis).plans[0]
    else:
        brief = _brief()
        scene = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4).plans[0]
    image = _real_image()
    return build_llm_visual_evaluator(client).review(
        image=image, thumbnail=b"final thumbnail", artwork=image, brief=brief, scene=scene)


def test_shared_rules_distinguish_material_counter_read_from_compatible_emotion():
    shared = policy()
    assert shared["version"] == "cover-readability.v1"
    rules = shared["rules"]
    assert "observable" in rules["visible_promise"]
    assert "withheld" in rules["visible_promise"]
    assert "materially contradicts" in rules["material_competing_read"]
    for protected in ("opposite action", "access", "relationship", "causation"):
        assert protected in rules["material_competing_read"]
    assert "compatible emotional or psychological" in rules["material_competing_read"]
    assert "unique" in rules["material_competing_read"]


def test_narrowing_or_replacing_rejected_slot_keeps_dependent_claims_and_full_review():
    shared = policy()
    assert set(shared["dependent_fields"]) >= {
        "campaign.hook", "campaign.interaction_edges", "visual_hook", "frozen_action",
        "gaze_graph", "blocking", "conflict_delivery", "conflict_read", "cause_signal",
        "consequence_signal", "design_rationale", "story_evidence_refs",
        "core_conflict_visual_contract",
    }
    rules = shared["rules"]
    assert "all members" in rules["narrowing"]
    assert "source-specific visual anchor" in rules["narrowing"]
    assert "generic sadness" in rules["narrowing"]
    assert "portfolio" in rules["portfolio_scope"]
    assert "central encounter" in rules["portfolio_scope"]
    assert "choice" in rules["portfolio_scope"]
    assert "slot identity" in rules["rejected_slot_replacement"]
    for replaceable in ("beat", "cast", "topology", "evidence references"):
        assert replaceable in rules["rejected_slot_replacement"]
    assert "complete portfolio" in rules["approval_boundary"]
    assert "author constraints" in rules["approval_boundary"]
    assert "not acceptance" in rules["approval_boundary"]
    shared["dependent_fields"].clear()
    shared["rules"]["narrowing"] = "mutated by a caller"
    assert policy()["dependent_fields"] and policy()["rules"]["narrowing"] != "mutated by a caller"


def test_campaign_stage_b_uses_shared_named_check_without_context_leaking_into_stage_a():
    client = CaptureReview()
    result = review(client)
    assert len(client.calls) == 2
    _, blind, _ = client.calls[0]
    _, verification, _ = client.calls[1]
    assert verification["readability_policy"] == policy()
    assert any(rule.startswith("cover_readability:") for rule in verification["semantic_rules"])
    assert "readability_policy" not in blind
    assert "scene_contract" not in blind and "story" not in blind
    assert verification["independent_blind_read"] == result.evaluation["blind_read"]
    assert result.evaluation["evaluation_status"] == "complete"


@pytest.mark.parametrize("code, visible_defect", [
    ("core_conflict_missing", "The actual frame reverses the approved access relationship."),
    ("weak_story_action", "The actual frame shows the opposite direction of the approved exchange."),
])
def test_shared_readability_does_not_suppress_material_rendered_failure(code, visible_defect):
    response = {**_scores(), "status": "blocked", "blockers": [code], "repair_codes": [code],
                "findings": [{"code": code, "severity": "blocker", "message": visible_defect,
                              "evidence": "The opposing hand and doorway boundary occupy the image center."}]}
    client = CaptureReview(response)
    result = review(client)
    assert client.calls[-1][1]["readability_policy"] == policy()
    assert result.report.status == "blocked"
    assert result.report.blockers == (code,) and result.report.repair_codes == (code,)
    assert result.report.findings[0].message == visible_defect
    assert result.report.findings[0].evidence == response["findings"][0]["evidence"]


def test_legacy_review_does_not_acquire_new_campaign_policy():
    client = CaptureReview()
    review(client, campaign=False)
    assert all("readability_policy" not in request for _, request, _ in client.calls)
    assert not any(rule.startswith("cover_readability:") for rule in client.calls[-1][1]["semantic_rules"])


@pytest.mark.parametrize("repair_kind", ["semantic_patch", "schema"])
def test_planning_repairs_direction_review_and_image_review_share_the_same_policy(bound, repair_kind):
    _, snapshot, analysis, brief, ledger, payload, _ = bound
    calls, drafts, reviews = [], [], []

    def complete(_system, user):
        request = json.loads(user)
        calls.append(request)
        if request["task"] == "review_campaign_semantics":
            reviews.append(request)
            if repair_kind == "semantic_patch" and len(reviews) == 1:
                return json.dumps(supported_review(request, accepted=False, findings=[{
                    "concept_id": payload["plans"][0]["concept_id"],
                    "message": "State the source-specific visible promise consistently in the design rationale.",
                    "evidence_refs": request["evidence_claims"][0]["evidence_refs"],
                }]))
            return json.dumps(supported_review(request))
        drafts.append(request)
        if len(drafts) == 1:
            return "invalid JSON" if repair_kind == "schema" else json.dumps(payload)
        if repair_kind == "schema":
            assert "schema_error" in request
            return json.dumps(payload)
        assert request["response_contract"]["schema"] == "cover-direction-patch.v1"
        return json.dumps({
            "schema": "cover-direction-patch.v1",
            "base_sha256": request["response_contract"]["base_sha256"],
            "plan_updates": [{"concept_id": payload["plans"][0]["concept_id"], "changes": {
                "design_rationale": payload["plans"][0]["design_rationale"]
                + " The visible promise remains the approved source-specific exchange.",
            }}],
            "shared_updates": {},
        })

    direction = CoverArtDirector(complete=complete).plan(
        brief, count=4, evidence_ledger=ledger,
        story_analysis=analysis.to_dict(), source_snapshot=snapshot)
    assert len(drafts) == 2 and len(reviews) == (2 if repair_kind == "semantic_patch" else 1)
    expected = policy()
    for request in calls:
        if request["task"] == "review_campaign_semantics":
            preflight = request["composition_preflight"]
        else:
            contract = request.get("full_direction_contract", request["response_contract"])
            preflight = contract["composition_preflight"]
        assert preflight["readability_policy"] == expected
        assert preflight["readability_policy"]["version"] == expected["version"]

    client = CaptureReview()
    image = _real_image()
    reviewed = build_llm_visual_evaluator(client).review(
        image=image, thumbnail=b"final thumbnail", artwork=image,
        brief=brief, scene=direction.plans[0])
    assert len(client.calls) == 2
    assert client.calls[-1][1]["readability_policy"] == expected
    assert "readability_policy" not in client.calls[0][1]
    assert reviewed.evaluation["evaluation_status"] == "complete"
