"""Direction checks share the bounded draft and independent review loop.

These are prompt-contract and orchestration checks, not visual-quality proof.
The injected completion adapter never calls a model or an image provider.
"""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from core.cover_director import CoverArtDirector, CoverDirectionError
from tests.cover_review_fixtures import supported_review
from tests.test_cover_campaign import context


def test_new_campaign_separates_book_conflict_from_each_drawable_promise():
    brief, ledger, payload, analysis = context()
    original = deepcopy(payload)
    requests = []

    def complete(system, user):
        request = json.loads(user)
        requests.append((system, request))
        return json.dumps(supported_review(request)
                          if request["task"] == "review_campaign_semantics" else payload)

    result = CoverArtDirector(complete=complete).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis,
    )
    contract = requests[0][1]["response_contract"]
    preflight = contract["composition_preflight"]
    assert "book-level" in preflight["conflict_scope"]
    assert "same prop or micro-action" in preflight["conflict_scope"]
    assert "one still image" in preflight["single_frame"]
    assert "unreadable text" in preflight["single_frame"]
    assert "strongest plausible alternative" in preflight["competing_read"]
    assert "design_rationale" in preflight["competing_read"]
    assert "expected_thumbnail_read" in preflight["thumbnail"]
    assert "tiny marks or finger positions" in preflight["thumbnail"]
    assert "depth_plan" in preflight["composite_boundary"]
    assert "physical duplicate" in preflight["composite_boundary"]
    assert "composition_preflight" not in contract["top_level_required_fields"]
    assert "composition_preflight" not in contract["plans"]["required_fields"]
    assert len(requests) == 2  # Existing design + semantic review; no added call.
    assert len(result.plans) == 4
    assert payload == original


def test_ambiguous_composition_is_rejected_and_repaired_within_existing_call_limit():
    brief, ledger, payload, analysis = context()
    requests = []
    problem = "The intended exclusion and ordinary arrival read equally well; the only separating cue disappears at thumbnail size."

    def complete(system, user):
        request = json.loads(user)
        requests.append(request)
        if request["task"] == "review_campaign_semantics":
            return json.dumps(supported_review(request, accepted=False, findings=[{
                "concept_id": "concept-1", "message": problem,
                "evidence_refs": [ledger.allowed_refs[0]],
            }]))
        return json.dumps(payload)

    with pytest.raises(CoverDirectionError, match="failed review") as caught:
        CoverArtDirector(complete=complete).plan(
            brief, count=4, evidence_ledger=ledger, story_analysis=analysis,
        )
    assert problem in str(caught.value)
    assert len(requests) == 6  # Three design attempts and their existing reviews.
    reviews = [r for r in requests if r["task"] == "review_campaign_semantics"]
    assert len(reviews) == 3
    for review in reviews:
        preflight = review["composition_preflight"]
        assert "strongest plausible alternative" in preflight["competing_read"]
        assert "physical duplicate" in preflight["composite_boundary"]
        checks = " ".join(review["checks"])
        assert "equally plausible" in checks
        assert "specific fields" in checks
        assert "thumbnail dimensions" in checks
        assert review["plans"]  # Audit the complete proposed portfolio.
    repairs = [r for r in requests if "validation_findings" in r]
    assert len(repairs) == 2
    for repair in repairs:
        finding = repair["validation_findings"][0]
        assert finding["code"] == "campaign_semantic_review"
        assert finding["message"] == problem
        assert finding["concept_or_evidence"] == "concept-1"
        rules = " ".join(repair["repair_rules"])
        assert "competing reading" in rules
        assert "executable scene fields" in rules
        assert "preserve source truth" in rules
        assert repair["previous_response"] == payload


def test_schema_repair_keeps_preflight_and_review_uses_the_books_thumbnail_size():
    brief, ledger, payload, analysis = context()
    brief = replace(brief, commercial_visual_goal=replace(
        brief.commercial_visual_goal, thumbnail_reference_width=144,
        thumbnail_reference_height=216,
    ))
    requests = []

    def complete(system, user):
        request = json.loads(user)
        requests.append((system, request))
        if len(requests) == 1:
            return "invalid JSON"
        if request["task"] == "review_campaign_semantics":
            return json.dumps(supported_review(request))
        return json.dumps(payload)

    CoverArtDirector(complete=complete).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis,
    )
    initial, repair, review = [r for _, r in requests]
    assert len(requests) == 3
    assert repair["previous_response"] == "invalid JSON"
    assert repair["response_contract"]["composition_preflight"] == initial["response_contract"]["composition_preflight"]
    assert review["thumbnail_context"] == {
        "width": 144, "height": 216,
        "display_context": brief.commercial_visual_goal.display_context,
    }
    assert "each plan's evidenced cause and response" in requests[0][0]
    assert "Every plan must communicate the same source-bound cause-and-consequence story" not in requests[0][0]


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_uncompilable_actor_instructions_and_review_share_bounded_repair(repair_succeeds):
    from core.cover_prompt_compiler import compile_direction_recipes

    brief, ledger, valid, analysis = context()
    invalid = deepcopy(valid)
    invalid["plans"][0]["gaze_graph"] = [("Mara watches Oren's stopped reaching hand. " * 800).strip()]
    calls, drafts, reviews = [], [], []
    problem = "The reaching gesture is still ambiguous at thumbnail size."

    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        if request["task"] == "review_campaign_semantics":
            reviews.append(request)
            assert len(request["plans"]) == 4
            if len(reviews) == 1:
                return json.dumps(supported_review(request, accepted=False, findings=[{
                    "concept_id": "concept-2", "message": problem,
                    "evidence_refs": [ledger.allowed_refs[0]],
                }]))
            return json.dumps(supported_review(request))
        drafts.append(request)
        return json.dumps(valid if repair_succeeds and len(drafts) > 1 else invalid)

    director = CoverArtDirector(complete=complete)
    if repair_succeeds:
        direction = director.plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
        recipes = compile_direction_recipes(brief, direction)
        assert all(len(recipe["image_prompt"]) <= 12000 for recipe in recipes.values())
        assert calls[-1]["task"] == "review_campaign_semantics"
    else:
        with pytest.raises(CoverDirectionError, match="campaign_render_execution"):
            director.plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert len(drafts) == len(reviews) == (2 if repair_succeeds else 3)
    assert len(calls) == (4 if repair_succeeds else 6)
    assert reviews[0]["plans"][0]["gaze_graph"] == invalid["plans"][0]["gaze_graph"]
    assert [call["task"] == "review_campaign_semantics" for call in calls] == [False, True] * len(drafts)
    repair = drafts[1]
    finding = next(item for item in repair["validation_findings"]
                   if item["code"] == "campaign_render_execution")
    assert "concept-1" in finding["concept_or_evidence"]
    assert "gaze_graph" in finding["concept_or_evidence"]
    assert "ACTOR EXECUTION" in finding["message"]
    semantic = next(item for item in repair["validation_findings"]
                    if item["code"] == "campaign_semantic_review")
    assert semantic["message"] == problem
    assert semantic["concept_or_evidence"] == "concept-2"
    assert semantic["evidence_refs"] == [ledger.allowed_refs[0]]
    rules = " ".join(repair["repair_rules"])
    assert "campaign_render_execution" in rules
    assert "do not truncate" in rules
    if not repair_succeeds:
        # The later independent reviews accept the story; unresolved execution
        # must still block return and keep the compiler feedback in the repair.
        assert any(item["code"] == "campaign_render_execution"
                   for item in drafts[2]["validation_findings"])


def test_historical_fixture_is_not_recompiled_by_direction_preflight():
    brief, ledger, payload, analysis = context()
    payload["plans"][0]["gaze_graph"] = [("Preserved historical instruction. " * 800).strip()]
    direction = CoverArtDirector.from_fixture(payload).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis,
    )
    assert direction.plans[0].gaze_graph == tuple(payload["plans"][0]["gaze_graph"])
