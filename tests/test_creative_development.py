from copy import deepcopy
import json

import pytest

from core.creative_baseline import build
from core.creative_development import DevelopmentReview, DevelopmentCapacityError, DevelopmentProtocolError
from core.creative_types import digest
from core.source_request_session import RequestOutcomeUnknown
from core.source_route_context import make_packet


def baseline():
    return build({"id": "book-a", "revision": 4, "input": {"premise": "The protagonist must earn freedom and never return to control."},
                  "messages": [{"role": "user", "content": "Keep the public choice and its real cost."}],
                  "creative_design": {"rounds": [], "decisions": [], "story_state": {"map": None, "actions": []}}})


def passed():
    return {"status": "pass", "issues": [], "evidence_requests": []}


class Model:
    def __init__(self, respond=None, limit=None):
        self.respond, self.calls = respond, []
        self.planning_input_bytes = limit

    def complete(self, system, user):
        payload = json.loads(user)
        self.calls.append(payload)
        value = self.respond(payload, len(self.calls)) if self.respond else passed()
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def conflict(payload, *, path=None, quote="She surrenders her freedom."):
    return {"status": "revise", "issues": [{"commitment_id": payload["core_commitments"][0]["id"],
        "candidate_path": path or ["reply"], "quote": quote, "reason": "Removes the adopted freedom outcome.",
        "repair": "Restore the freely made choice and real cost."}], "evidence_requests": []}


def test_complete_candidate_is_reviewed_without_declared_changed_ids_and_replay_free(tmp_path):
    candidate = {"reply": "She surrenders her freedom.", "changed_ids": []}
    model = Model(lambda payload, _: conflict(payload))
    review = DevelopmentReview(tmp_path, baseline())
    result = review.assess(model, candidate)
    assert result["status"] == "needs_revision" and len(model.calls) == 1
    assert model.calls[0]["candidate_units"][0]["value"] == candidate["reply"]
    assert review.assess(model, candidate) == result and len(model.calls) == 1
    saved = next(tmp_path.rglob("candidate.json"))
    assert json.loads(saved.read_text())["candidate"] == candidate


def test_targeted_repair_changes_only_allowed_path_and_is_independently_reviewed(tmp_path):
    candidate = {"reply": "She surrenders her freedom.", "design_notes": {"story": {"ending": "Freedom"}}}
    model = Model(lambda payload, index: conflict(payload) if index == 1 else passed())
    repairs = []
    def repair(value, issues, context):
        repairs.append(context)
        value["reply"] = "She keeps her freedom and pays a real cost."
        return value
    review = DevelopmentReview(tmp_path, baseline())
    result = review.assess(model, candidate, repair=repair)
    assert result["status"] == "passed" and len(model.calls) == 2 and len(repairs) == 1
    assert result["candidate"]["design_notes"] == candidate["design_notes"]
    assert repairs[0]["allowed_paths"] == [["reply"]]
    assert review.assess(model, candidate, repair=repair) == result
    assert len(model.calls) == 2 and len(repairs) == 1


def test_repair_cannot_expand_write_scope(tmp_path):
    model = Model(lambda payload, _: conflict(payload))
    candidate = {"reply": "She surrenders her freedom.", "design_notes": {"ending": "Freedom"}}
    def repair(value, issues, context):
        value["reply"] = "Her freedom is restored."
        value["design_notes"]["ending"] = "Unexpected unapproved new ending"
        return value
    with pytest.raises(DevelopmentProtocolError, match="允许范围"):
        DevelopmentReview(tmp_path, baseline()).assess(model, candidate, repair=repair)
    assert len(model.calls) == 1
    assert next(tmp_path.rglob("repair-candidate.json")).exists()


def test_identical_failed_repair_does_not_repeat(tmp_path):
    model = Model(lambda payload, _: conflict(payload))
    review = DevelopmentReview(tmp_path, baseline())
    candidate = {"reply": "She surrenders her freedom."}
    result = review.assess(model, candidate, repair=lambda value, *_: value)
    assert result["status"] == "needs_revision" and "未取得新进展" in result["notice"]
    review.assess(model, candidate, repair=lambda *_: pytest.fail("same repair repeated"))
    assert len(model.calls) == 1


def test_unknown_request_is_not_blindly_reissued(tmp_path):
    def timeout(*_):
        raise TimeoutError("remote unknown")
    model = Model(timeout)
    review = DevelopmentReview(tmp_path, baseline())
    with pytest.raises(TimeoutError):
        review.assess(model, {"reply": "Retains the chosen ending."})
    with pytest.raises(RequestOutcomeUnknown):
        review.assess(model, {"reply": "Retains the chosen ending."})
    assert len(model.calls) == 1


def test_corrupt_completed_receipt_never_repurchases(tmp_path):
    model = Model()
    review = DevelopmentReview(tmp_path, baseline())
    review.assess(model, {"reply": "Good ending"})
    path = next(tmp_path.rglob("result.json"))
    result = json.loads(path.read_text())
    result["candidate"]["reply"] = "tampered"
    path.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="完整性"):
        review.assess(model, {"reply": "Good ending"})
    assert len(model.calls) == 1


def test_source_dependency_is_requested_and_supplied_verbatim(tmp_path):
    original = baseline()
    packet = make_packet({"segments": [{"id": "e1", "text": "Original source says she refuses control."}]},
                         source_id="source-1", source_sha256="a" * 64, understanding_sha256="b" * 64)
    original["source_identity"] = {"evidence": {"sha256": packet["sha256"]}}
    original["sha256"] = digest({k: v for k, v in original.items() if k != "sha256"})
    def respond(payload, index):
        if index == 1:
            return {"status": "needs_evidence", "issues": [], "evidence_requests": [{"source": "source", "path": ["content", "segments", 0]}]}
        assert payload["requested_evidence"][0]["value"] == packet["content"]["segments"][0]
        return passed()
    model = Model(respond)
    result = DevelopmentReview(tmp_path, original).assess(model, {"reply": "Her choice follows the source."}, evidence_packet=packet)
    assert result["status"] == "passed" and len(model.calls) == 2
    assert result["receipt"]["coverage"][0]["dependencies"][0]["path"] == ["content", "segments", 0]


def test_missing_evidence_or_repeated_request_cannot_be_called_pass(tmp_path):
    def respond(payload, _):
        return {"status": "needs_evidence", "issues": [], "evidence_requests": [{"source": "history", "path": ["conversation", 0]}]}
    model = Model(respond)
    result = DevelopmentReview(tmp_path, baseline()).assess(model, {"reply": "Candidate"})
    assert result["status"] == "needs_evidence" and len(model.calls) == 2


@pytest.mark.parametrize("kind", ["forged_quote", "unknown_id", "missing_issue"])
def test_conflicts_need_real_candidate_evidence(tmp_path, kind):
    def respond(payload, _):
        result = conflict(payload)
        if kind == "forged_quote":
            result["issues"][0]["quote"] = "Not present anywhere"
        elif kind == "unknown_id":
            result["issues"][0]["commitment_id"] = "invented-author-approval"
        else:
            result["issues"] = []
        return result
    model = Model(respond)
    review = DevelopmentReview(tmp_path, baseline())
    with pytest.raises(DevelopmentProtocolError):
        review.assess(model, {"reply": "She surrenders her freedom."})
    assert len(model.calls) == 2
    with pytest.raises(DevelopmentProtocolError):
        review.assess(model, {"reply": "She surrenders her freedom."})
    assert len(model.calls) == 2


def test_protocol_correction_reuses_original_candidate_and_saved_reply(tmp_path):
    def respond(payload, index):
        if index == 1:
            return "{incomplete"
        assert payload["protocol_correction"]["previous_reply"] == "{incomplete"
        return passed()
    model = Model(respond)
    review = DevelopmentReview(tmp_path, baseline())
    result = review.assess(model, {"reply": "A compliant new scene"})
    assert result["status"] == "passed" and len(model.calls) == 2
    assert len(result["receipt"]["request_ids"]) == 2
    assert review.assess(model, {"reply": "A compliant new scene"}) == result
    assert len(model.calls) == 2


def test_unknown_evidence_path_can_be_corrected_without_repeating_story_generation(tmp_path):
    def respond(payload, index):
        if index == 1:
            return {"status": "needs_evidence", "issues": [], "evidence_requests": [{"source": "history", "path": ["not-a-real-field"]}]}
        assert "protocol_correction" in payload
        return passed()
    model = Model(respond)
    result = DevelopmentReview(tmp_path, baseline()).assess(model, {"reply": "A complete compatible scene."})
    assert result["status"] == "passed" and len(model.calls) == 2


def test_issue_cannot_quote_unseen_page_even_when_text_exists_elsewhere(tmp_path):
    chapters = [{"number": i, "scene": f"Chapter {i}: " + "specific consequence; " * 30} for i in range(1, 30)]
    def respond(payload, _):
        return conflict(payload, path=["chapter_plan", 28], quote=chapters[-1]["scene"])
    model = Model(respond, limit=14000)
    with pytest.raises(DevelopmentProtocolError, match="未读取"):
        DevelopmentReview(tmp_path, baseline()).assess(model, {"chapter_plan": chapters})
    assert len(model.calls) == 2


@pytest.mark.parametrize("path", [[], ["chapter_plan"]])
def test_local_issue_cannot_grant_whole_book_write_scope(tmp_path, path):
    candidate = {"chapter_plan": [{"scene": "She surrenders her freedom."}, {"scene": "Unrelated preserved scene."}]}
    model = Model(lambda payload, _: conflict(payload, path=path) | {"issues": [{
        **conflict(payload)["issues"][0], "candidate_path": path}]})
    with pytest.raises(DevelopmentProtocolError, match="限定具体"):
        DevelopmentReview(tmp_path, baseline()).assess(model, candidate,
            repair=lambda *_: pytest.fail("must not call repair with broad scope"))
    assert len(model.calls) == 2


def test_changed_baseline_cannot_reuse_old_pass(tmp_path):
    model = Model()
    candidate = {"reply": "An ending"}
    DevelopmentReview(tmp_path, baseline()).assess(model, candidate)
    changed = baseline()
    changed["session_revision"] += 1
    changed["sha256"] = digest({k: v for k, v in changed.items() if k != "sha256"})
    DevelopmentReview(tmp_path, changed).assess(model, candidate)
    assert len(model.calls) == 2


def test_hundred_chapters_page_losslessly_and_request_both_dependency_endpoints(tmp_path):
    chapters = [{"number": i, "scene": f"Chapter {i}: " + "specific choice, cost, relationship change; " * 12} for i in range(1, 101)]
    candidate = {"story_design": {"ending": "Freedom"}, "chapter_plan": chapters}
    def respond(payload, _):
        paths = [row["path"] for row in payload["candidate_units"]]
        if ["chapter_plan", 99] in paths and not payload["requested_evidence"]:
            return {"status": "needs_evidence", "issues": [], "evidence_requests": [{"source": "candidate", "path": ["chapter_plan", 0]}]}
        if payload["requested_evidence"]:
            assert payload["requested_evidence"][0]["value"] == chapters[0]
        return passed()
    model = Model(respond, limit=18000)
    result = DevelopmentReview(tmp_path, baseline()).assess(model, candidate)
    assert result["status"] == "passed"
    # An evidence request can legitimately repartition a full first page; only
    # completed coverage counts, not the discarded pre-evidence attempt view.
    actual = [candidate[unit["path"][0]][unit["path"][1]]
              for page in result["receipt"]["coverage"] for unit in page["units"]]
    assert actual == chapters
    assert 2 <= len(model.calls) < 15
    assert all(call["global_design"] == candidate["story_design"] for call in model.calls)
    assert result["receipt"]["coverage"][-1]["dependencies"]


def test_hundred_chapter_repair_reuses_only_unchanged_passed_pages_and_invalidates_actual_dependencies(tmp_path):
    chapters = [{"number": i, "scene": f"Chapter {i}: " + "specific choice, cost, relationship change; " * 12}
                for i in range(1, 101)]
    chapters[0]["scene"] += "She surrenders her freedom."
    candidate = {"story_design": {"ending": "Freedom"}, "chapter_plan": chapters}
    original_sha = digest(candidate)
    def respond(payload, _):
        paths = [row["path"] for row in payload["candidate_units"]]
        if ["chapter_plan", 99] in paths and not payload["requested_evidence"]:
            return {"status": "needs_evidence", "issues": [], "evidence_requests": [
                {"source": "candidate", "path": ["chapter_plan", 0]}]}
        if payload["candidate_sha256"] == original_sha and ["chapter_plan", 0] in paths:
            return conflict(payload, path=["chapter_plan", 0, "scene"])
        return passed()
    def repair(value, issues, context):
        value["chapter_plan"][0]["scene"] = value["chapter_plan"][0]["scene"].replace(
            "She surrenders her freedom.", "She retains her own freedom.")
        return value
    model = Model(respond, limit=18000)
    result = DevelopmentReview(tmp_path, baseline()).assess(model, candidate, repair=repair)
    assert result["status"] == "passed"
    original_calls = [call for call in model.calls if call["candidate_sha256"] == original_sha]
    new_calls = [call for call in model.calls if call["candidate_sha256"] != original_sha]
    assert 2 <= len(new_calls) < len(original_calls)
    coverage = result["receipt"]["coverage"]
    reused = [row for row in coverage if row.get("reused_from_candidate_sha256")]
    assert reused and all(row["reused_from_candidate_sha256"] == original_sha for row in reused)
    # The terminal chapter actually read chapter 1 as evidence; a local hash
    # match of its own unchanged text is insufficient to reuse its old pass.
    terminal = next(row for row in coverage if any(unit["path"] == ["chapter_plan", 99] for unit in row["units"]))
    assert "reused_from_candidate_sha256" not in terminal
    assert terminal["dependencies"][0]["sha256"] == digest(result["candidate"]["chapter_plan"][0])


def test_global_design_change_invalidates_every_page_even_when_chapters_unchanged(tmp_path):
    chapters = [{"number": i, "scene": f"Chapter {i}: " + "concrete narrative choices; " * 20} for i in range(1, 50)]
    candidate = {"story_design": {"ending": "She surrenders her freedom."}, "chapter_plan": chapters}
    def respond(payload, _):
        if payload["global_design"]["ending"] == "She surrenders her freedom.":
            return conflict(payload, path=["story_design", "ending"])
        return passed()
    def repair(value, issues, context):
        value["story_design"]["ending"] = "She keeps freedom and pays the cost."
        return value
    model = Model(respond, limit=15000)
    result = DevelopmentReview(tmp_path, baseline()).assess(model, candidate, repair=repair)
    assert result["status"] == "passed"
    assert not any(row.get("reused_from_candidate_sha256") for row in result["receipt"]["coverage"])


def test_indivisible_capacity_failure_preserves_complete_input_without_model_calls(tmp_path):
    candidate = {"story_design": {"ending": "Freedom"}, "chapter_plan": [{"number": 1, "scene": "critical " * 10000}]}
    model = Model(limit=9000)
    with pytest.raises(DevelopmentCapacityError):
        DevelopmentReview(tmp_path, baseline()).assess(model, candidate)
    assert not model.calls
    assert json.loads(next(tmp_path.rglob("candidate.json")).read_text())["candidate"] == candidate


def test_pause_before_dispatch_preserves_candidate(tmp_path):
    from core.llm_client import LLMCancelledError
    model = Model()
    with pytest.raises(LLMCancelledError):
        DevelopmentReview(tmp_path, baseline(), cancelled=lambda: True).assess(model, {"reply": "Keep everything"})
    assert not model.calls and next(tmp_path.rglob("candidate.json")).exists()


def routed_baseline():
    return build({"id": "route-book", "revision": 1, "input": {}, "messages": [],
        "source_route_selection": {"action_id": "pick", "adoption_scope": "core_and_payoffs"},
        "source_context": {"mode": "original_rewrite", "selected_proposal": {
            "route_id": "route", "title": "Choose freedom", "reader_promise": "Earn freedom through costly choice.",
            "emotional_design": [{"expectation": "Will she refuse control?", "scene": "Public hearing", "choice": "Refuse control",
                "cost": "Lose her position", "change": "Equal power", "payoff": "Self determination"}]}}})


def test_delivery_requires_concrete_placements_but_discussion_can_keep_inherited_items(tmp_path):
    value = routed_baseline()
    candidate = {"story_design": {"ending": "Free"}, "chapter_plan": [{"number": 1, "scene": "She refuses control and loses her position."}]}
    model = Model()
    result = DevelopmentReview(tmp_path, value).assess(model, candidate)
    assert result["status"] == "needs_evidence" and len(model.calls) == 2
    assert set(result["missing_placements"]) == {row["id"] for row in value["items"] if row["category"] in {"reader_promise", "emotion"}}
    discussion = DevelopmentReview(tmp_path, value).assess(model, {"reply": "Discuss her motives before choosing the scene."})
    assert discussion["status"] == "passed" and discussion["missing_placements"] == []


def test_delivery_stores_candidate_bound_placements_without_separate_extraction_call(tmp_path):
    candidate = {"story_design": {"ending": "Free"}, "chapter_plan": [{"number": 1, "scene": "She refuses control and loses her position."}]}
    def respond(payload, _):
        return {**passed(), "placements": [{"commitment_id": key, "candidate_paths": [["chapter_plan", 0]],
            "quote": candidate["chapter_plan"][0]["scene"]} for key in payload["required_commitments"]]}
    model = Model(respond)
    result = DevelopmentReview(tmp_path, routed_baseline()).assess(model, candidate)
    assert result["status"] == "passed" and len(model.calls) == 1
    assert len(result["receipt"]["placements"]) == 2 and result["receipt"]["missing_placements"] == []


def test_placement_ids_in_chapter_metadata_do_not_prove_story_realization(tmp_path):
    value = routed_baseline()
    key = next(item["id"] for item in value["items"] if item["category"] == "reader_promise")
    candidate = {"story_design": {}, "chapter_plan": [{"number": 1, "world_event_ids": [key], "scene": "Nothing happens."}]}
    def respond(payload, _):
        return {**passed(), "placements": [{"commitment_id": key, "candidate_paths": [["chapter_plan", 0]], "quote": key}]}
    with pytest.raises(DevelopmentProtocolError, match="实际章节"):
        DevelopmentReview(tmp_path, value).assess(Model(respond), candidate)
