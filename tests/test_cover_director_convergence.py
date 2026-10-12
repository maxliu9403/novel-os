"""Repair rounds retain evidence and audit a complete, structurally valid portfolio.

Completion doubles exercise transport and acceptance boundaries, not visual quality.
All source snapshots and diagnostics belong to pytest's temporary directory.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_sources import canonical_hash
from core.cover_store import CoverStore
from core.cover_workflow import CoverWorkflow
from tests.cover_review_fixtures import supported_review
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_director_source_context import bound  # noqa: F401


def _patch(base, updates):
    return {"schema": "cover-direction-patch.v1", "base_sha256": canonical_hash(base),
            "plan_updates": [{"concept_id": base["plans"][index]["concept_id"], "changes": changes}
                             for index, changes in updates.items()], "shared_updates": {}}


def _issue(request, index, message):
    identifier = request["plans"][index]["concept_id"]
    claim = next(item for item in request["evidence_claims"] if item["concept_id"] == identifier)
    return {"concept_id": identifier, "message": message, "evidence_refs": list(claim["evidence_refs"])}


def _plan(bound, complete, **kwargs):
    _, snapshot, analysis, brief, ledger, _, _ = bound
    return CoverArtDirector(complete=complete).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis.to_dict(),
        source_snapshot=snapshot, **kwargs)


def _assert_full_source_and_portfolio(request, snapshot, ids):
    assert request["frozen_source_context"]["documents"][0]["text"].encode() == snapshot.primary_bytes
    assert [plan["concept_id"] for plan in request["plans"]] == ids
    assert {claim["concept_id"] for claim in request["evidence_claims"]} == set(ids)


def test_execution_budget_failure_still_collects_current_whole_portfolio_review_before_last_patch(bound):
    _, snapshot, _, _, _, payload, _ = bound
    ids = [plan["concept_id"] for plan in payload["plans"]]
    original = deepcopy(payload)
    drafts, reviews, events = [], [], []
    first_repair = {
        0: {"blocking": "Mara faces Oren, keeping her hand and the key distinct in the first revised layout."},
        2: {"blocking": "The first revised third layout separates the two wrists in profile."},
        3: {"gaze_graph": ["Mara watches Oren's stopped reaching hand. " * 800]},
    }
    final_repair = {
        0: {"blocking": "Mara's key-holding hand is at her own hip, separated from Oren's open hand by clear space.",
            "gaze_graph": ["Mara watches Oren's empty palm; Oren looks at the key at her hip."]},
        2: {"blocking": "The closed door fills the space between Mara's retained key and Oren's stopped reach.",
            "gaze_graph": ["Mara watches the stopped reaching hand from a balanced side-on stance."]},
        3: {"gaze_graph": deepcopy(payload["plans"][3]["gaze_graph"])},
    }

    def complete(system, user):
        request = json.loads(user)
        if request["task"] == "review_campaign_semantics":
            reviews.append(request)
            _assert_full_source_and_portfolio(request, snapshot, ids)
            if len(reviews) <= 2:
                if len(reviews) == 2:
                    assert request["plans"][0]["blocking"] == first_repair[0]["blocking"]
                    assert len(request["plans"][3]["gaze_graph"][0]) > 12000
                return json.dumps(supported_review(request, accepted=False, findings=[
                    _issue(request, 0, f"Review {len(reviews)}: the first plan's exchange still has an incompatible reading."),
                    _issue(request, 2, f"Review {len(reviews)}: the third plan's causal response needs clearer separation."),
                ]))
            for index, changes in final_repair.items():
                for field, value in changes.items():
                    assert request["plans"][index][field] == value
            return json.dumps(supported_review(request))
        drafts.append(request)
        if len(drafts) == 1:
            return json.dumps(payload)
        if len(drafts) == 2:
            return json.dumps(_patch(request["previous_response"], first_repair))
        findings = request["validation_findings"]
        assert {item["code"] for item in findings} >= {"campaign_render_execution", "campaign_semantic_review"}
        semantic = [item for item in findings if item["code"] == "campaign_semantic_review"]
        assert {item["concept_or_evidence"] for item in semantic} == {ids[0], ids[2]}
        assert all("Review 2:" in item["message"] for item in semantic)
        return json.dumps(_patch(request["previous_response"], final_repair))

    result = _plan(bound, complete, diagnostic_sink=events.append)
    assert len(drafts) == len(reviews) == 3
    assert [plan.concept_id for plan in result.plans] == ids
    assert result.source_binding["campaign_evidence_review"]
    assert sum(event["stage"] == "draft_response" for event in events) == 3
    assert sum(event["stage"] == "semantic_response" for event in events) == 3
    assert payload == original


def test_title_failure_does_not_hide_another_plans_render_budget_failure(bound):
    payload = deepcopy(bound[5])
    payload["plans"][0]["campaign"]["title_render_mode"] = "composed"
    invalid = deepcopy(payload)
    invalid["plans"][0]["campaign"]["title_layout"]["letter_spacing"] = -.01
    invalid["plans"][3]["gaze_graph"] = ["Mara watches Oren's stopped reaching hand. " * 800]
    drafts, reviews = [], []

    def complete(system, user):
        request = json.loads(user)
        if request["task"] == "review_campaign_semantics":
            reviews.append(request)
            assert len(request["plans"]) == 4
            return json.dumps(supported_review(request))
        drafts.append(request)
        if len(drafts) == 1:
            return json.dumps(invalid)
        findings = request["validation_findings"]
        assert {item["code"] for item in findings} >= {
            "campaign_title_layout", "campaign_render_execution"}
        title = next(item for item in findings if item["code"] == "campaign_title_layout")
        render = next(item for item in findings if item["code"] == "campaign_render_execution")
        assert payload["plans"][0]["concept_id"] in title["concept_or_evidence"]
        assert payload["plans"][3]["concept_id"] in render["concept_or_evidence"]
        return json.dumps(_patch(request["previous_response"], {
            0: {"campaign": payload["plans"][0]["campaign"]},
            3: {"gaze_graph": payload["plans"][3]["gaze_graph"]},
        }))

    result = _plan(bound, complete)
    assert len(drafts) == len(reviews) == 2
    assert result.source_binding["campaign_evidence_review"]
    assert result.plans[0].campaign["title_layout"] == payload["plans"][0]["campaign"]["title_layout"]
    assert result.plans[3].gaze_graph == tuple(payload["plans"][3]["gaze_graph"])


@pytest.mark.parametrize("invalid_kind", ["schema", "unknown_evidence", "unknown_event"])
def test_structurally_invalid_campaign_never_reaches_semantic_reviewer(bound, invalid_kind):
    payload = deepcopy(bound[5])
    if invalid_kind == "schema":
        del payload["plans"][0]["campaign"]["hook"]
    elif invalid_kind == "unknown_evidence":
        payload["plans"][0]["campaign"]["interaction_edges"][0]["evidence_refs"] = ["evidence:not-in-source"]
    else:
        payload["plans"][0]["campaign"]["layers"][0]["event_id"] = "not-a-source-event"
    drafts = []

    def complete(system, user):
        request = json.loads(user)
        assert request["task"] != "review_campaign_semantics"
        drafts.append(request)
        return json.dumps(payload)

    with pytest.raises(CoverDirectionError):
        _plan(bound, complete)
    assert len(drafts) == 3


def test_reviewer_evidence_refs_survive_into_targeted_repair_findings(bound):
    payload = bound[5]
    reviews, drafts, issue = [], [], None

    def complete(system, user):
        nonlocal issue
        request = json.loads(user)
        if request["task"] == "review_campaign_semantics":
            reviews.append(request)
            if len(reviews) == 1:
                issue = _issue(request, 0, "Separate the source-supported hands without changing their action.")
                return json.dumps(supported_review(request, accepted=False, findings=[issue]))
            assert request["plans"][0]["blocking"] == "The key hand and stopped reach have separate silhouettes."
            return json.dumps(supported_review(request))
        drafts.append(request)
        if len(drafts) == 1:
            return json.dumps(payload)
        finding = next(item for item in request["validation_findings"] if item["code"] == "campaign_semantic_review")
        assert finding["evidence_refs"] == issue["evidence_refs"]
        assert finding["concept_or_evidence"] == issue["concept_id"]
        assert finding["message"] == issue["message"]
        return json.dumps(_patch(request["previous_response"], {
            0: {"blocking": "The key hand and stopped reach have separate silhouettes."}}))

    assert _plan(bound, complete).source_binding["campaign_evidence_review"]
    assert len(drafts) == len(reviews) == 2


@pytest.mark.parametrize("malformed", ["json", "patch"])
def test_schema_repair_retains_last_unresolved_semantic_review_and_its_exact_plan_digest(bound, malformed):
    payload = bound[5]
    reviews, drafts, issue = [], [], None

    def complete(system, user):
        nonlocal issue
        request = json.loads(user)
        if request["task"] == "review_campaign_semantics":
            reviews.append(request)
            if len(reviews) == 1:
                issue = _issue(request, 2, "The third plan needs an executable visible distinction, not another rationale.")
                return json.dumps(supported_review(request, accepted=False, findings=[issue]))
            assert request["plans"][2]["blocking"] == "A clear gap separates the retained key and stopped palm."
            return json.dumps(supported_review(request))
        drafts.append(request)
        if len(drafts) == 1:
            return json.dumps(payload)
        if len(drafts) == 2:
            if malformed == "json":
                return "not JSON"
            invalid = _patch(request["previous_response"], {2: {"blocking": "incomplete attempt"}})
            invalid["base_sha256"] = "0" * 64
            return json.dumps(invalid)
        assert "schema_error" in request
        pending = request["pending_semantic_review"]
        assert pending["review_attempt"] == 1
        assert pending["reviewed_plans_sha256"] == canonical_hash(reviews[0]["plans"])
        assert len(pending["findings"]) == 1
        finding = pending["findings"][0]
        assert finding["message"] == issue["message"]
        assert finding["evidence_refs"] == issue["evidence_refs"]
        assert issue["concept_id"] in json.dumps(finding)
        assert request["repair_base"] == payload
        return json.dumps(_patch(request["repair_base"], {
            2: {"blocking": "A clear gap separates the retained key and stopped palm."}}))

    assert _plan(bound, complete).source_binding["campaign_evidence_review"]
    assert len(drafts) == 3 and len(reviews) == 2


def test_supported_review_never_overrides_unresolved_execution_budget_or_saves_a_direction(bound):
    context, _, analysis, _, _, payload, project = bound
    invalid = deepcopy(payload)
    invalid["plans"][3]["gaze_graph"] = ["Mara watches Oren's stopped reaching hand. " * 800]
    drafts, reviews = [], []

    def complete(system, user):
        request = json.loads(user)
        if request["task"] == "review_campaign_semantics":
            reviews.append(request)
            return json.dumps(supported_review(request))
        drafts.append(request)
        return json.dumps(invalid)

    workflow = CoverWorkflow(context, CoverStore(project))
    with pytest.raises(CoverDirectionError, match="campaign_render_execution"):
        workflow.plan(analysis.analysis_id, CoverArtDirector(complete=complete))
    assert len(drafts) == len(reviews) == 3
    assert not list(workflow.store.directions_dir.glob("*.json"))


def test_patch_can_replace_supported_beat_cast_epoch_and_refs_without_renaming_slot(bound):
    _, snapshot, analysis, brief, ledger, payload, _ = bound
    data = analysis.to_dict()
    quote = "她关上了门。"
    start = snapshot.primary_bytes.index(quote.encode())
    evidence = {**data["evidence"][0], "evidence_id": "closed-door-beat", "exact_quote": quote,
                "byte_start": start, "byte_end": start + len(quote.encode()),
                "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(), "statement": "Mara closes the door."}
    data["evidence"].append(evidence)
    ref = "evidence:closed-door-beat"
    ledger = replace(ledger, items=(*ledger.items, replace(ledger.items[0], evidence_id="closed-door-beat",
                                                        summary="Mara closes the door.")))
    event = {"event_id": "event-closed-door", "participants": ["char_mara"],
             "epoch": "opening after the key removal", "location": "apartment entry",
             "action": "Mara closes the door", "reaction": "She stands inside the closed threshold",
             "consequence": "The opening is closed", "evidence_refs": [ref]}
    data["events"].append(event)
    replacement = deepcopy(payload["plans"][0])
    campaign = replacement["campaign"]
    campaign.update(cover_epoch=event["epoch"], event_ids=[event["event_id"]])
    campaign["layers"][0].update(event_id=event["event_id"], epoch=event["epoch"],
        cast=["char_mara"], evidence_refs=[ref], function="Mara closes the door after taking the key.")
    campaign["interaction_edges"] = [{"layer_id": "moment", "actor_id": "char_mara", "target_id": "door",
        "affected_character_ids": ["char_mara"], "action": "Close the door with a balanced hand on its edge",
        "visible_reaction": "The door meets its frame while Mara remains inside", "evidence_refs": [ref]}]
    campaign["hook"].update(visible_fact="Mara's palm rests against a door seated in its frame",
        visual_trigger="The closed threshold interrupts the previously open entry",
        unexpected_contradiction="A shared home now has a deliberately closed boundary",
        evidence_refs=[ref])
    replacement.update(cast=["char_mara"], conflict_character_ids=[], story_evidence_refs=[ref],
        frozen_action="Mara stands inside with a balanced palm on the closed door.",
        blocking="Only Mara is visible, side-on beside the door seated in its frame.",
        gaze_graph=["Mara looks at the door edge beneath her hand."],
        cause_signal="The previously open household entry is closed by Mara's hand.",
        consequence_signal="The door now separates the interior from the unseen exterior.",
        conflict_delivery="Her hand on the closed boundary carries the sourced action.",
        conflict_read="Mara has closed the household door; the later relationship outcome remains unknown.",
        design_rationale="Choose the supported closed-door beat instead of asserting an invisible transfer direction.")
    replacement["visual_hook"].update(first_glance_subject="Mara beside the closed door",
        expected_thumbnail_read="Mara's palm and the door seated in its frame remain large and distinct.")
    changes = {key: value for key, value in replacement.items()
               if key != "concept_id" and value != payload["plans"][0].get(key)}
    drafts, reviews = [], []

    def complete(system, user):
        request = json.loads(user)
        if request["task"] == "review_campaign_semantics":
            reviews.append(request)
            _assert_full_source_and_portfolio(request, snapshot, [p["concept_id"] for p in payload["plans"]])
            if len(reviews) == 1:
                return json.dumps(supported_review(request, accepted=False, findings=[
                    _issue(request, 0, "Select the other sourced visible beat and update executable fields and bindings together.")]))
            final = request["plans"][0]
            # A rationale-only patch cannot make this reviewer return supported.
            for field in ("cast", "campaign", "frozen_action", "blocking", "gaze_graph", "story_evidence_refs"):
                assert final[field] == replacement[field]
            own_claims = [claim for claim in request["evidence_claims"] if claim["concept_id"] == final["concept_id"]]
            assert own_claims and all(claim["event_id"] == event["event_id"] and claim["evidence_refs"] == [ref]
                                      for claim in own_claims)
            return json.dumps(supported_review(request))
        drafts.append(request)
        return json.dumps(payload if len(drafts) == 1 else _patch(request["previous_response"], {0: changes}))

    result = CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger,
        story_analysis=data, source_snapshot=snapshot)
    assert len(drafts) == len(reviews) == 2
    assert result.plans[0].concept_id == payload["plans"][0]["concept_id"]
    assert result.plans[0].cast == ("char_mara",)
    assert result.plans[0].campaign["cover_epoch"] == event["epoch"]
    assert result.source_binding["campaign_evidence_review"]
