from copy import deepcopy
import json

import pytest

from core.creative_amendments import validate_adjustments, witness
from core.creative_baseline import build, view, DEVELOPMENT_VERSION
from core.creative_development import DevelopmentReview, DevelopmentProtocolError, required_placement_ids
from core.creative_types import digest


def session(instruction="结局改为 Amelia 独自远行，不与 Ryan 复合，保留公开拒绝和失去财产的代价。"):
    return {"id": "mutable-route", "revision": 3, "input": {},
        "messages": [{"role": "user", "content": instruction}],
        "source_route_selection": {"adoption_scope": "core_and_payoffs", "action_id": "pick",
                                   "adopted_at_message_index": 0},
        "source_context": {"selected_proposal": {"route_id": "route-a", "title": "自由地重逢",
            "causal_arc": {"ending": "Amelia公开拒绝财产控制，付出失去财产的代价后与Ryan复合。"}}},
        "creative_design": {"rounds": [], "decisions": [], "story_state": {}}}


def candidate():
    return {"story_design": {"ending": "独立离开"}, "chapter_plan": [
        {"number": 1, "text": "Amelia当面撕掉财产和解书，承担失去继承权的代价，独自登船离开，拒绝复合。"}]}


class Model:
    def __init__(self, *, authorized=True, retire=False):
        self.calls, self.authorized, self.retire = [], authorized, retire

    def complete(self, system, user):
        value = json.loads(user)
        self.calls.append(value)
        if value.get("task") == "verify_author_adjustments":
            assert "Amelia公开拒绝" in json.dumps(value["original_commitments"], ensure_ascii=False)
            result = {"decisions": [{"commitment_id": row["commitment_id"], "authorized": self.authorized,
                "reason": "作者明确改结局，公开拒绝与财产代价保留。" if self.authorized else "这句话是在询问备选可能，并未指示替换当前结局。"}
                for row in value["proposed_adjustments"]]}
        else:
            target = next(row["id"] for row in value["core_commitments"] if row["category"] == "causal_arc")
            result = {"status": "pass", "issues": [], "evidence_requests": [], "placements": []}
            if not value["author_adjustments"]:
                result["author_adjustments"] = [{"commitment_id": target,
                    "author_requirement_id": "author-message-1",
                    "author_quote": value["author_requirements"]["entries"][0]["text"],
                    "effective_value": None if self.retire else "Amelia公开拒绝财产控制，付出失去财产的代价，独自远行且不复合。",
                    "candidate_path": ["chapter_plan", 0, "text"], "quote": "独自登船离开，拒绝复合。"}]
            elif not self.retire:
                result["placements"] = [{"commitment_id": target, "candidate_paths": [["chapter_plan", 0]],
                                          "quote": "承担失去继承权的代价，独自登船离开，拒绝复合。"}]
        return json.dumps(result, ensure_ascii=False)


def test_explicit_author_reversal_replaces_effective_obligation_without_locking_old_ending(tmp_path):
    original = build(session())
    saved = deepcopy(original)
    model = Model()
    result = DevelopmentReview(tmp_path, original).assess(model, candidate())
    assert result["status"] == "passed" and len(model.calls) == 3
    assert original == saved and result["receipt"]["baseline_sha256"] == original["sha256"]
    assert result["missing_placements"] == []
    assert result["receipt"]["required_placements"] == required_placement_ids(original)
    changes = validate_adjustments(original, result["receipt"]["author_adjustments"])
    assert "不复合" in changes[0]["effective_value"]
    assert changes[0]["verification"]["request_sha256"] in result["receipt"]["request_ids"]
    assert DevelopmentReview(tmp_path, original).assess(model, candidate()) == result
    assert len(model.calls) == 3


def test_author_question_cannot_retire_commitment_by_reviewers_self_report(tmp_path):
    original = build(session("如果不复合会如何？我还没决定，先看看另一种可能。"))
    model = Model(authorized=False)
    result = DevelopmentReview(tmp_path, original).assess(model, candidate())
    assert result["status"] == "needs_revision"
    assert result["receipt"]["author_adjustments"] == []
    assert result["receipt"]["required_placements"] == required_placement_ids(original)
    assert len(model.calls) == 2
    assert "询问" in result["issues"][0]["reason"]


def test_explicit_whole_function_withdrawal_removes_only_verified_old_placement(tmp_path):
    original = build(session("删除这条复合结局及其财产交换情节，改为独自离开的开放结局。"))
    result = DevelopmentReview(tmp_path, original).assess(Model(retire=True), candidate())
    assert result["status"] == "passed"
    assert result["receipt"]["required_placements"] == []
    assert len(required_placement_ids(original)) == 1
    assert required_placement_ids(original, result["receipt"]["author_adjustments"]) == []


def test_passed_author_change_persists_in_next_baseline_without_changing_original_route(tmp_path):
    state = session()
    original_route = deepcopy(state["source_context"])
    result = DevelopmentReview(tmp_path, build(state)).assess(Model(), candidate())
    state["development"] = {"accepted": {"receipt": result["receipt"]}}
    state["revision"] += 1
    state["messages"].append({"role": "user", "content": "保留新的结局，继续完善登船前的两难选择。"})
    following = build(state)
    assert following["author_adjustments"] == result["receipt"]["author_adjustments"]
    assert state["source_context"] == original_route
    model = Model()
    assert DevelopmentReview(tmp_path, following).assess(model, candidate())["status"] == "passed"
    assert len(model.calls) == 1


def test_author_change_receipt_rejects_substituted_author_words_and_old_obligation(tmp_path):
    state = session()
    result = DevelopmentReview(tmp_path, build(state)).assess(Model(), candidate())
    rows = result["receipt"]["author_adjustments"]
    changed = session("继续，按原路线结局。")
    with pytest.raises(ValueError, match="作者"):
        validate_adjustments(build(changed), rows)
    state["source_context"]["selected_proposal"]["causal_arc"]["ending"] += "新补充必须支付的其他代价。"
    with pytest.raises(ValueError, match="作者"):
        validate_adjustments(build(state), rows)


def test_older_instruction_cannot_claim_to_override_later_route_adoption(tmp_path):
    state = session()
    state["source_route_selection"]["adopted_at_message_index"] = 1
    state["messages"].append({"role": "user", "content": "现在采用这条路线。"})
    model = Model()
    with pytest.raises(DevelopmentProtocolError, match="较早"):
        DevelopmentReview(tmp_path, build(state)).assess(model, candidate())
    assert len(model.calls) == 2  # one protocol correction, no authority override
    assert not any(row.get("task") == "verify_author_adjustments" for row in model.calls)


def test_needs_author_requires_exact_author_evidence_not_only_candidate_conflict(tmp_path):
    class SelfReported:
        def complete(self, system, user):
            value = json.loads(user)
            return json.dumps({"status": "author_choice", "issues": [{
                "commitment_id": value["core_commitments"][0]["id"],
                "candidate_path": ["chapter_plan", 0], "quote": "独自登船离开，拒绝复合。",
                "reason": "模型觉得需要作者选择", "repair": "请作者决定"}]}, ensure_ascii=False)
    with pytest.raises(DevelopmentProtocolError, match="作者原话"):
        DevelopmentReview(tmp_path, build(session())).assess(SelfReported(), candidate())


def test_titles_and_host_hashes_are_not_narrative_coverage_units(tmp_path):
    state = {"id": "original", "input": {}, "messages": [], "creative_design": {}}
    class Check:
        def complete(self, system, user):
            value = json.loads(user)
            assert [row["path"] for row in value["candidate_units"]] == [["chapter_plan", 0]]
            return json.dumps({"status": "pass", "issues": [], "placements": []})
    value = {"story_design": {}, "chapter_plan": [{"heading": "结局", "text": "她走了。"}],
             "origin_source_sha256": digest("original")}
    assert DevelopmentReview(tmp_path, build(state)).assess(Check(), value)["status"] == "passed"


def test_composite_proposal_cannot_be_entirely_withdrawn_for_one_changed_ending(tmp_path):
    state = session("只改复合结局，公开拒绝与财产代价仍要保留。")
    state.pop("source_context")
    state.pop("source_route_selection")
    state["creative_design"] = {"rounds": [{"proposals": [{"id": "p1", "ending": "复合",
        "public_choice": "公开拒绝控制", "cost": "失去继承权"}]}],
        "decisions": [{"proposal_id": "p1", "status": "accepted", "message_index": 0}], "story_state": {}}
    seen = []
    class Composite:
        def complete(self, system, user):
            payload = json.loads(user)
            seen.append(payload)
            if payload.get("task") == "verify_author_adjustments":
                complete = payload["original_commitments"][0]["value"]["proposal"]
                assert complete == state["creative_design"]["rounds"][0]["proposals"][0]
                return json.dumps({"decisions": [{"commitment_id": payload["proposed_adjustments"][0]["commitment_id"],
                    "authorized": False, "reason": "作者只改结局；整项撤销会丢掉明确保留的选择与代价。"}]})
            target = next(row["id"] for row in payload["core_commitments"] if row["category"] == "adopted_proposal")
            return json.dumps({"status": "pass", "author_adjustments": [{"commitment_id": target,
                "author_requirement_id": "author-message-1", "author_quote": state["messages"][0]["content"],
                "effective_value": None, "candidate_path": ["chapter_plan", 0],
                "quote": "拒绝复合。"}]}, ensure_ascii=False)
    result = DevelopmentReview(tmp_path, build(state)).assess(Composite(), candidate())
    assert result["status"] == "needs_revision" and result["receipt"]["author_adjustments"] == []
    assert len(seen) == 2


def test_unknown_authority_judgment_is_not_blindly_reissued(tmp_path):
    from core.source_request_session import RequestOutcomeUnknown
    class Interrupted(Model):
        def complete(self, system, user):
            if json.loads(user).get("task") == "verify_author_adjustments":
                self.calls.append(json.loads(user))
                raise TimeoutError("authority reply unknown")
            return super().complete(system, user)
    model, original = Interrupted(), build(session())
    with pytest.raises(TimeoutError):
        DevelopmentReview(tmp_path, original).assess(model, candidate())
    with pytest.raises(RequestOutcomeUnknown):
        DevelopmentReview(tmp_path, original).assess(model, candidate())
    assert len(model.calls) == 2


def test_heading_alone_cannot_establish_payoff(tmp_path):
    original = build(session())
    class TitleOnly:
        def complete(self, system, user):
            payload = json.loads(user)
            target = next(row["id"] for row in payload["core_commitments"] if row["category"] == "causal_arc")
            return json.dumps({"status": "pass", "placements": [{"commitment_id": target,
                "candidate_paths": [["chapter_plan", 0]], "quote": "复合结局"}]})
    with pytest.raises(DevelopmentProtocolError, match="引文"):
        DevelopmentReview(tmp_path, original).assess(TitleOnly(), {"story_design": {}, "chapter_plan": [
            {"number": 1, "heading": "复合结局", "text": "人物尚未行动。"}]})


def test_view_uses_verified_current_emotion_instead_of_old_route_snapshot():
    state = session()
    old = {"expectation": "自由复合", "scene": "码头重逢", "choice": "公开拒绝控制",
           "cost": "失去财产", "change": "自由的恋人", "payoff": "重新牵手"}
    state["source_context"]["selected_proposal"]["emotional_design"] = [old]
    original = build(state)
    target = next(row for row in original["items"] if row["category"] == "emotion")
    effective = {**old, "expectation": "自由远行", "change": "独立的人", "payoff": "独自登船"}
    change = witness(original, {"commitment_id": target["id"], "author_requirement_id": "author-message-1",
        "author_quote": state["messages"][0]["content"], "effective_value": effective},
        {"authorized": True, "reason": "作者明确不复合并保留公开拒绝和财产代价。"}, digest("authority verification request"))
    state["development"] = {"schema": DEVELOPMENT_VERSION, "accepted": {"receipt": {"author_adjustments": [change]}}}
    assert view(state)["emotion_designs"] == [effective]
    assert state["source_context"]["selected_proposal"]["emotional_design"] == [old]


def test_new_structured_author_decision_does_not_inherit_old_proposal_amendment():
    state = session()
    state.pop("source_context")
    state.pop("source_route_selection")
    proposal = {"id": "p1", "ending": "复合", "cost": "失去继承权"}
    decision = {"proposal_id": "p1", "status": "accepted", "message_index": 0}
    state["creative_design"] = {"rounds": [{"proposals": [proposal]}], "decisions": [decision], "story_state": {}}
    original = build(state)
    target = next(row for row in original["items"] if row["category"] == "adopted_proposal")
    effective = deepcopy(target["value"])
    effective["proposal"]["ending"] = "独自远行"
    change = witness(original, {"commitment_id": target["id"], "author_requirement_id": "author-message-1",
        "author_quote": state["messages"][0]["content"], "effective_value": effective},
        {"authorized": True, "reason": "作者改结局，保留其他代价。"}, digest("recorded verification"))
    receipt = {"author_adjustments": [change]}
    state["development"] = {"accepted": {"receipt": deepcopy(receipt)}}
    state["messages"].append({"role": "user", "content": "我现在重新选择和解方案，但放弃财产争夺。"})
    state["creative_design"]["decisions"].append({**decision, "status": "revised", "message_index": 1,
                                               "revision_text": "重新选择和解方案，但放弃财产争夺。"})
    changed = build(state)
    assert changed["author_adjustments"] == []
    assert state["development"]["accepted"]["receipt"] == receipt
