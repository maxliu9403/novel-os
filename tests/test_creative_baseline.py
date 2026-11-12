from copy import deepcopy
import json

import pytest

from core.creative_baseline import build, context, review_needed, validate, view, DEVELOPMENT_VERSION


def session():
    return {"id": "story-1", "revision": 3, "input": {"premise": "A woman builds a company.",
        "constraints": "Her freedom must not depend on marriage.", "audience_channel": "female"},
        "messages": [{"role": "user", "content": "Explore two possible endings."},
                     {"role": "assistant", "content": "One ends in reconciliation; another in independence."}],
        "creative_design": {"rounds": [], "decisions": [], "story_state": {"map": None, "actions": []}},
        "development": {"schema": DEVELOPMENT_VERSION}}


def route_context():
    return {"mode": "original_rewrite", "selection": {"action_id": "choose-1", "route_id": "route-1", "scope": "discussion_direction"},
        "selected_proposal": {"route_id": "route-1", "title": "Earn the return", "reader_promise": "Freedom precedes reunion.",
        "causal_arc": {"opening": "She leaves.", "ending": "Reconciliation after real loss."},
        "emotional_design": [{"expectation": "Will he give up control?", "scene": "Public hearing", "choice": "Refuse inherited power",
                              "cost": "Lose his position", "change": "Equal terms", "payoff": "She chooses freely"}],
        "proposed_assumptions": ["The board has hidden documents."]}}


def test_original_partial_baseline_preserves_exact_scope_without_adopting_first_model_route():
    value = session()
    before = deepcopy(value)
    baseline = build(value)
    assert value == before
    assert baseline["route_scope"] is None
    assert {item["category"] for item in baseline["items"]} == {"author_statement"}
    assert [row["text"] for row in baseline["author_requirements"]["entries"]] == [
        value["input"]["premise"], value["input"]["constraints"], "Explore two possible endings."]
    assert baseline["initial_preferences"]["audience_channel"] == "female"
    assert view(value)["phase"] == "exploring"


def test_host_continue_is_not_author_evidence():
    value = session()
    value["messages"].append({"role": "user", "action": "develop_story", "content": "HOST continue all designs", "author_note": "Keep the hearing."})
    entries = build(value)["author_requirements"]["entries"]
    assert entries[-1]["text"] == "Keep the hearing."
    assert "HOST" not in str(entries)


def test_legacy_route_selection_does_not_adopt_every_event():
    value = session()
    value["source_context"] = route_context()
    baseline = build(value)
    assert baseline["route_scope"] == "discussion_direction"
    assert [row["category"] for row in baseline["items"] if row["origin"] == "selected_route"] == ["direction"]
    assert baseline["route_snapshot"] == route_context()["selected_proposal"]
    assert any(row["category"] == "source_mode" for row in baseline["items"])


def test_new_explicit_route_selection_protects_core_and_emotional_function_not_assumptions():
    value = session()
    value["source_context"] = route_context()
    value["source_route_selection"] = {**route_context()["selection"], "adoption_scope": "core_and_payoffs"}
    baseline = build(value)
    assert baseline["route_scope"] == "core_and_payoffs"
    assert {row["category"] for row in baseline["items"]} >= {"direction", "reader_promise", "causal_arc", "emotion"}
    assert not any(row["value"] == "The board has hidden documents." for row in baseline["items"])
    ids = [item["id"] for item in baseline["items"]]
    value["revision"] += 1
    assert [item["id"] for item in build(value)["items"]] == ids
    assert view(value)["emotion_designs"] == route_context()["selected_proposal"]["emotional_design"]


def test_only_latest_explicit_recorded_decision_adopts_and_scoped_delegation_is_separate():
    value = session()
    value["creative_design"]["rounds"] = [{"proposals": [{"id": "a", "story": "One"}, {"id": "b", "story": "Two"}]}]
    value["creative_design"]["decisions"] = [{"proposal_id": "a", "status": "accepted"}, {"proposal_id": "a", "status": "rejected"},
                                            {"proposal_id": "b", "status": "revised", "note": "Only keep the core conflict."}]
    value["creative_design"]["story_state"]["actions"] = [{"mode": "delegate", "question": {"topic": "Opening only"}}]
    items = build(value)["items"]
    adopted = [row for row in items if row["category"] == "adopted_proposal"]
    assert len(adopted) == 1 and adopted[0]["value"]["proposal"]["id"] == "b"
    assert any(row["category"] == "author_action" for row in items)


def test_derived_brief_is_not_promoted_to_author_and_frozen_active_baseline_survives_resume():
    value = session()
    value["input_origins"] = {"premise": "selected_route"}
    baseline = build(value)
    assert not any(row["source"] == "input.premise" for row in baseline["author_requirements"]["entries"])
    assert baseline["dependencies"]["initial_brief"]["premise"] == value["input"]["premise"]
    value["development"]["active"] = {"baseline": baseline}
    value["revision"] += 1
    assert context(value)["creative_baseline"]["session_revision"] == baseline["session_revision"]


def test_integrity_and_operation_scope_cannot_be_model_claims():
    baseline = build(session())
    baseline["items"][0]["text"] = "tampered"
    with pytest.raises(ValueError, match="校验失败"):
        validate(baseline)
    assert review_needed({"reply": "A silently different ending.", "changed_ids": []})
    assert not review_needed({"reply": "Why the ending works"}, operation="explain_only")


def test_request_projection_references_exact_existing_text_and_restores_all_commitments():
    value = session()
    value["input"]["premise"] = "Complete original author premise. " * 150
    value["messages"].append({"role": "user", "action": "develop_story", "content": "host advance",
                              "author_note": "Keep the true cost and earned ending. " * 60})
    value["source_context"] = route_context()
    value["source_route_selection"] = {**route_context()["selection"], "adoption_scope": "core_and_payoffs"}
    value["source_context"]["selected_proposal"]["causal_arc"]["opening"] *= 200
    baseline = build(value)
    projection = context(value)["creative_baseline"]
    request = {"initial_brief": value["input"], "conversation": value["messages"],
               "source_reference": value["source_context"],
               "creative_design": {"story_state": value["creative_design"]["story_state"], "adopted_proposals": []}}
    def restore(record):
        if isinstance(record, list):
            return [restore(child) for child in record]
        if not isinstance(record, dict):
            return record
        result = {}
        for key, child in record.items():
            if key.endswith("_ref") and isinstance(child, list):
                actual = request
                for part in child:
                    actual = actual[part]
                result[key[:-4]] = deepcopy(actual)
            else:
                result[key] = restore(child)
        return result
    restored = restore(projection)
    for item in restored["items"]:
        item["text"] = item["value"] if isinstance(item["value"], str) else json.dumps(item["value"], ensure_ascii=False, separators=(",", ":"))
    assert restored["items"] == baseline["items"]
    assert restored["route_snapshot"] == baseline["route_snapshot"]
    assert restored["author_requirements"] == baseline["author_requirements"]
    assert baseline == build(value)  # projection never mutates the store/source
    assert len(json.dumps(projection)) < len(json.dumps({k: v for k, v in baseline.items() if k != "dependencies"}))


def test_active_snapshot_uses_complete_literal_when_outer_context_differs():
    value = session()
    before = build(value)
    value["development"]["active"] = {"baseline": before}
    value["input"]["premise"] = "A newer premise outside this old request."
    projection = context(value)["creative_baseline"]
    assert projection["items"][0]["value"] == before["items"][0]["value"]
    assert "value_ref" not in projection["items"][0]
