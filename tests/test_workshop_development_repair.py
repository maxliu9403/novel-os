from copy import deepcopy
import json

import pytest

from api.workshop_development import _repair
from core.creative_baseline import build
from core.creative_development import DevelopmentCapacityError, DevelopmentProtocolError
from core.creative_types import digest
from core.gemini_gateway import input_bytes
from core.source_request_session import RequestOutcomeUnknown
from core.source_route_context import make_packet


class Model:
    def __init__(self, respond=None, limit=18000):
        self.calls, self.respond = [], respond
        self.planning_input_bytes = limit

    def complete(self, system, user):
        payload = json.loads(user)
        self.calls.append((system, payload))
        assert self.planning_input_bytes is None or input_bytes(system, user) <= self.planning_input_bytes
        value = self.respond(payload, len(self.calls)) if self.respond else {
            "edits": [{"path": payload["allowed_paths"][0], "value": "She pays the real cost and chooses freedom."}],
            "evidence_requests": []}
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def fixture(tmp_path):
    candidate = {"story_design": {"ending": "She earns freedom through a costly public choice."},
                 "chapter_plan": [{"number": i, "scene": f"Chapter {i}: " + "unrelated preserved story; " * 60,
                                   "reader_value": "Concrete emotional payoff"} for i in range(1, 101)],
                 "rendered_skeleton_sha256": "a" * 64}
    candidate["chapter_plan"][49]["scene"] = "She is forgiven without paying any cost."
    candidate["chapter_plan"][3]["scene"] = "The hearing requires her to reject inherited control."
    baseline = build({"id": "repair-book", "revision": 3, "input": {"premise": "Freedom requires a real sacrifice."},
        "messages": [{"role": "assistant", "content": "Historical unadopted alternative. " * 100} for _ in range(80)]})
    context = {"baseline": baseline, "allowed_paths": [["chapter_plan", 49, "scene"]],
               "request_directory": str(tmp_path / "repair"), "binding_sha256": "b" * 64,
               "inspection": [{"units": [{"path": ["chapter_plan", 49], "sha256": digest(candidate["chapter_plan"][49])}],
                   "dependencies": [{"source": "candidate", "path": ["chapter_plan", 3], "sha256": digest(candidate["chapter_plan"][3])}]}]}
    issues = [{"commitment_id": baseline["items"][0]["id"], "candidate_path": ["chapter_plan", 49, "scene"],
               "quote": "without paying any cost", "reason": "Removes the required sacrifice.", "repair": "Restore the costly choice."}]
    return candidate, issues, context


def test_hundred_chapter_repair_contains_complete_affected_chapter_and_actual_dependencies(tmp_path):
    candidate, issues, context = fixture(tmp_path)
    before = deepcopy(candidate)
    model = Model()
    result = _repair(model, candidate, issues, context)
    assert len(model.calls) == 1
    payload = model.calls[0][1]
    assert "candidate" not in payload and "dependencies" not in payload["authority"]
    assert payload["candidate_units"] == [{"path": ["chapter_plan", 49], "value": candidate["chapter_plan"][49]}]
    assert payload["requested_evidence"][0]["value"] == candidate["chapter_plan"][3]
    assert payload["global_design"] == candidate["story_design"]
    assert "Historical unadopted alternative" not in json.dumps(payload)
    assert "Chapter 100:" not in json.dumps(payload)
    assert result["chapter_plan"][49]["scene"] == "She pays the real cost and chooses freedom."
    result["chapter_plan"][49]["scene"] = before["chapter_plan"][49]["scene"]
    assert result == candidate == before
    saved = json.loads((tmp_path / "repair/input.json").read_text())
    assert saved["candidate"] == before and saved["baseline"] == context["baseline"]


def test_unread_dependency_can_be_fetched_exactly_and_completed_reply_replayed(tmp_path):
    candidate, issues, context = fixture(tmp_path)
    def respond(payload, index):
        if index == 1:
            return {"edits": [], "evidence_requests": [{"source": "candidate", "path": ["chapter_plan", 72]}]}
        assert payload["requested_evidence"][-1]["value"] == candidate["chapter_plan"][72]
        return {"edits": [{"path": payload["allowed_paths"][0], "value": "She sacrifices her position and gains freedom."}]}
    model = Model(respond)
    result = _repair(model, candidate, issues, context)
    assert len(model.calls) == 2
    assert _repair(model, candidate, issues, context) == result and len(model.calls) == 2


def test_scope_expansion_is_rejected_and_one_protocol_correction_is_durable(tmp_path):
    candidate, issues, context = fixture(tmp_path)
    def respond(payload, index):
        if index == 1:
            return {"edits": [{"path": ["chapter_plan", 70, "scene"], "value": "unapproved change"}]}
        assert "protocol_correction" in payload
        return {"edits": [{"path": ["chapter_plan", 49, "scene"], "value": "She chooses freely after the loss."}]}
    model = Model(respond)
    result = _repair(model, candidate, issues, context)
    assert result["chapter_plan"][70] == candidate["chapter_plan"][70]
    assert len(model.calls) == 2
    assert _repair(model, candidate, issues, context) == result and len(model.calls) == 2


def test_repeated_invalid_protocol_stops_after_one_correction_without_repurchase(tmp_path):
    candidate, issues, context = fixture(tmp_path)
    model = Model(lambda *_: "not json")
    with pytest.raises(DevelopmentProtocolError):
        _repair(model, candidate, issues, context)
    assert len(model.calls) == 2
    with pytest.raises(DevelopmentProtocolError):
        _repair(model, candidate, issues, context)
    assert len(model.calls) == 2


def test_repeated_evidence_request_cannot_loop_or_drop_dependency(tmp_path):
    candidate, issues, context = fixture(tmp_path)
    model = Model(lambda *_: {"edits": [], "evidence_requests": [{"source": "candidate", "path": ["chapter_plan", 3]}]})
    with pytest.raises(DevelopmentProtocolError, match="重复"):
        _repair(model, candidate, issues, context)
    assert len(model.calls) == 2


def test_unknown_result_is_not_blindly_resent(tmp_path):
    candidate, issues, context = fixture(tmp_path)
    def timeout(*_):
        raise TimeoutError("unknown remote result")
    model = Model(timeout)
    with pytest.raises(TimeoutError):
        _repair(model, candidate, issues, context)
    with pytest.raises(RequestOutcomeUnknown):
        _repair(model, candidate, issues, context)
    assert len(model.calls) == 1


def test_required_evidence_cannot_be_truncated_to_fit(tmp_path):
    candidate, issues, context = fixture(tmp_path)
    candidate["chapter_plan"][3]["scene"] = "CRITICAL " * 12000
    context["inspection"][0]["dependencies"][0]["sha256"] = digest(candidate["chapter_plan"][3])
    model = Model()
    with pytest.raises(DevelopmentCapacityError):
        _repair(model, candidate, issues, context)
    assert not model.calls
    assert json.loads((tmp_path / "repair/input.json").read_text())["candidate"] == candidate


def test_all_allowed_fields_remain_complete_with_nested_object_replacement(tmp_path):
    candidate, issues, context = fixture(tmp_path)
    context["allowed_paths"] = [["chapter_plan", 49], ["chapter_plan", 49, "scene"]]
    def respond(payload, _):
        assert payload["allowed_paths"] == [["chapter_plan", 49]]
        updated = deepcopy(payload["candidate_units"][0]["value"])
        updated["scene"] = "She pays the cost."
        return {"edits": [{"path": ["chapter_plan", 49], "value": updated}]}
    result = _repair(Model(respond), candidate, issues, context)
    assert result["chapter_plan"][49]["reader_value"] == candidate["chapter_plan"][49]["reader_value"]
    assert result["chapter_plan"][:49] == candidate["chapter_plan"][:49]


def test_changed_inspection_dependency_cannot_be_used_as_old_evidence(tmp_path):
    candidate, issues, context = fixture(tmp_path)
    context["inspection"][0]["dependencies"][0]["sha256"] = "0" * 64
    model = Model()
    with pytest.raises(ValueError, match="证据已变化"):
        _repair(model, candidate, issues, context)
    assert not model.calls


def test_source_passage_fetch_preserves_full_evidence_and_source_binding(tmp_path):
    candidate, issues, context = fixture(tmp_path)
    packet = make_packet({"segments": [{"id": "e1", "text": "The original hearing imposed a real sacrifice."}]},
                         source_id="source-book", source_sha256="c" * 64, understanding_sha256="d" * 64)
    context["baseline"]["source_identity"] = {"evidence": {"sha256": packet["sha256"]}}
    context["baseline"]["sha256"] = digest({k: v for k, v in context["baseline"].items() if k != "sha256"})
    context["evidence_packet"] = packet
    def respond(payload, index):
        if index == 1:
            return {"edits": [], "evidence_requests": [{"source": "source", "path": ["content", "segments", 0]}]}
        assert payload["requested_evidence"][-1]["value"] == packet["content"]["segments"][0]
        return {"edits": [{"path": payload["allowed_paths"][0], "value": "The hearing demands a costly public choice."}]}
    result = _repair(Model(respond), candidate, issues, context)
    assert result["chapter_plan"][49]["scene"] == "The hearing demands a costly public choice."
