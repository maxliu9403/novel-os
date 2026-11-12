"""Only independent, complete discussion checks may preserve adopted state."""
import json

import pytest

from core.creative_baseline import build
from core.creative_development import DevelopmentReview, DevelopmentProtocolError, SYSTEM
from test_creative_development import Model, baseline, conflict, passed
from test_creative_amendments import Model as ChangeModel, session as change_session, candidate as changed_ending


def explanatory():
    return {**passed(), "state_effect": "no_change", "state_effect_reason":
        "The author asks why the adopted ending works; this reply only explains its choices and costs without adopting alternatives."}


def test_independent_explanation_receipt_preserves_state_without_extra_call(tmp_path):
    authority = build({"id": "discussion", "input": {"premise": "Keep the independent ending."},
                       "messages": [{"role": "user", "content": "请解释这个结局为何有情绪回报，暂不修改。"}]})
    model = Model(lambda *_: explanatory())
    candidate = {"reply": "她主动承担失去财产的代价，因此独自登船是挣来的自由。"}
    result = DevelopmentReview(tmp_path, authority).assess(model, candidate)
    assert len(model.calls) == 1
    assert result["state_effect"] == result["receipt"]["state_effect"] == "no_change"
    assert result["receipt"]["candidate_sha256"] and result["receipt"]["state_effect_reasons"]
    assert all(row["state_effect"] == "no_change" for row in result["receipt"]["coverage"])
    assert DevelopmentReview(tmp_path, authority).assess(model, candidate) == result
    assert len(model.calls) == 1


def test_generator_self_report_and_old_judgment_never_grant_no_change_exemption(tmp_path):
    model = Model()
    result = DevelopmentReview(tmp_path, baseline()).assess(model,
        {"reply": "A newly committed ending.", "state_effect": "no_change", "changed_ids": []})
    assert result["state_effect"] == result["receipt"]["state_effect"] == "development"
    assert len(model.calls) == 1


def test_one_developing_page_keeps_whole_discussion_development(tmp_path):
    candidate = {"reply": [f"Comparison {i}: " + "a fully explained but unadopted alternative; " * 25 for i in range(60)]}
    def respond(payload, number):
        return passed() if number == 2 else explanatory()
    model = Model(respond, limit=17000)
    result = DevelopmentReview(tmp_path, baseline()).assess(model, candidate)
    assert 2 < len(model.calls) < 20
    assert result["status"] == "passed" and result["state_effect"] == "development"
    assert any(row["state_effect"] == "development" for row in result["receipt"]["coverage"])


def test_no_change_without_reason_gets_only_one_protocol_correction(tmp_path):
    model = Model(lambda *_: {**passed(), "state_effect": "no_change"})
    with pytest.raises(DevelopmentProtocolError, match="作者意图"):
        DevelopmentReview(tmp_path, baseline()).assess(model, {"reply": "Explanation"})
    assert len(model.calls) == 2


def test_repair_cannot_restore_old_adopted_state_via_no_change_verdict(tmp_path):
    model = Model(lambda payload, number: conflict(payload) if number == 1 else explanatory())
    def repair(candidate, *_):
        candidate["reply"] = "She keeps freedom and pays its real cost."
        return candidate
    result = DevelopmentReview(tmp_path, baseline()).assess(model,
        {"reply": "She surrenders her freedom."}, repair=repair)
    assert result["status"] == "passed" and result["state_effect"] == "development"
    assert result["receipt"]["repair"] and len(model.calls) == 2


def test_delivery_is_always_development_and_still_requires_real_mapping(tmp_path):
    result = DevelopmentReview(tmp_path, baseline()).assess(Model(lambda *_: explanatory()),
        {"story_design": {"ending": "Freedom"}, "chapter_plan": [{"number": 1, "scene": "She makes the choice."}]})
    assert result["status"] == "passed" and result["state_effect"] == "development"
    original = build(change_session())
    missing = DevelopmentReview(tmp_path, original).assess(Model(lambda *_: explanatory()), changed_ending())
    assert missing["status"] == "needs_evidence" and missing["missing_placements"]
    assert missing["state_effect"] == "development"


def test_new_verified_author_change_cannot_be_classified_as_no_change(tmp_path):
    class Contradictory(ChangeModel):
        def complete(self, system, user):
            result = json.loads(super().complete(system, user))
            if "status" in result:
                result.update(state_effect="no_change", state_effect_reason="Claims to only explain the proposed new ending.")
            return json.dumps(result, ensure_ascii=False)
    # Discussion (not delivery): the author actually requests the changed ending,
    # even if the reviewer later labels its explanation as having no state effect.
    candidate = changed_ending()
    candidate.pop("story_design")
    result = DevelopmentReview(tmp_path, build(change_session())).assess(Contradictory(), candidate)
    assert result["status"] == "passed" and result["author_adjustments"]
    assert result["state_effect"] == "development"
    assert "A requested new ending is development" in SYSTEM


def test_failed_review_cannot_preserve_state_even_if_model_labels_it_no_change(tmp_path):
    model = Model(lambda payload, _: {**conflict(payload), "state_effect": "no_change", "state_effect_reason": "Unfounded claim."})
    result = DevelopmentReview(tmp_path, baseline()).assess(model, {"reply": "She surrenders her freedom."})
    assert result["status"] == "needs_revision" and result["state_effect"] == "development"
