"""Fault injection at the source-route handoff and evidence completion seams."""

import json
from types import SimpleNamespace

import pytest

from api import workshop
from core.source_route_context import SourceEvidenceCoordinator, _units
from test_workshop_api import client, finish, model_result  # noqa: F401
from test_source_route_handoff import evidence_fixture, setup_source


def test_evidence_traversal_cannot_complete_after_candidate_changes_back(tmp_path):
    context, _ = evidence_fixture(tmp_path, "Early promise. " + "Complete source. " * 1300)
    starts = []
    coordinator = SourceEvidenceCoordinator(tmp_path, context)
    first_path = next(_units(coordinator.packet))["path"]

    def complete(system, user):
        body = json.loads(user)
        first_page = any(unit["path"] == first_path for unit in body["evidence_units"])
        if first_page:
            starts.append(body["candidate"])
        # On the second traversal, early evidence rejects A in favour of B.
        # Later evidence changes B back to A. A was NOT stable over all evidence.
        candidate = "candidate B" if first_page and len(starts) > 1 else "candidate A"
        return json.dumps({"candidate": candidate, "evidence_requests": []})

    with pytest.raises(ValueError, match="重复|变化|进展"):
        coordinator.complete(SimpleNamespace(complete=complete), "system", "task", limit=2900)
    assert len(starts) >= 2


def test_completed_coverage_rejects_changed_candidate_instead_of_returning_it(tmp_path):
    context, _ = evidence_fixture(tmp_path, "Complete evidence.")
    calls = []
    model = SimpleNamespace(complete=lambda *args: calls.append(args) or "original response")
    coordinator = SourceEvidenceCoordinator(tmp_path, context)
    assert coordinator.complete(model, "system", "task") == "original response"
    path = next((tmp_path / "source-evidence" / "requests").glob("*/coverage.json"))
    saved = json.loads(path.read_text())
    saved["candidate"] = "corrupted response"
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError):
        coordinator.complete(model, "system", "task")
    assert len(calls) == 1


def test_received_response_hash_is_checked_before_reusing_paid_reply(tmp_path):
    context, _ = evidence_fixture(tmp_path, "Complete evidence.")
    calls = []
    model = SimpleNamespace(complete=lambda *args: calls.append(args) or "original response")
    coordinator = SourceEvidenceCoordinator(tmp_path, context)
    assert coordinator.complete(model, "system", "task") == "original response"
    directory = next((tmp_path / "source-evidence" / "requests").iterdir())
    (directory / "coverage.json").unlink()  # Crash before coverage commit.
    path = next(p for p in directory.glob("*.json") if p.name != "coverage.json")
    saved = json.loads(path.read_text())
    saved["response"] = "corrupted response"
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError):
        coordinator.complete(model, "system", "task")
    assert len(calls) == 1


def test_unknown_evidence_call_uses_provider_query_without_resending(tmp_path):
    context, _ = evidence_fixture(tmp_path, "Complete evidence.")
    sent = []
    queried = []

    def interrupted(*args):
        sent.append(args)
        raise TimeoutError("Remote response not yet known")

    coordinator = SourceEvidenceCoordinator(tmp_path, context)
    with pytest.raises(TimeoutError):
        coordinator.complete(SimpleNamespace(complete=interrupted), "system", "task")
    recovered = coordinator.complete(SimpleNamespace(
        complete=lambda *_: pytest.fail("An unknown paid request was resent"),
        recover_response=lambda identity: queried.append(identity) or "recovered original response",
    ), "system", "task")
    assert recovered == "recovered original response"
    assert len(sent) == len(queried) == 1


def test_same_draft_new_operation_recovers_prepared_handoff_after_create_crash(client, monkeypatch):
    projects, library, design, saved, body = setup_source(client)
    original_create = workshop.WorkshopService.create
    calls = []

    def fail_once(self, *args, **kwargs):
        calls.append(kwargs.get("_session_id"))
        if len(calls) == 1:
            raise OSError("Injected crash before creating the session")
        return original_create(self, *args, **kwargs)

    monkeypatch.setattr(workshop.WorkshopService, "create", fail_once)
    observed = []
    monkeypatch.setattr(workshop, "_complete", lambda *_: observed.append(True) or model_result())
    first = client.post("/api/workshop/source-route-handoffs", json=body)
    assert first.status_code == 409
    replay = {**body, "operation_id": "handoff-reloaded-draft-02"}
    resumed = client.post("/api/workshop/source-route-handoffs", json=replay)
    assert resumed.status_code == 202, resumed.text
    session = finish(client, resumed.json()["session_id"])
    assert calls[0] == calls[1] == session["id"]
    assert len(observed) == 1
    assert len(workshop.WorkshopService(projects).list_sessions()["sessions"]) == 1


def test_explicit_new_direction_updates_existing_story_map_selection_evidence(client, monkeypatch):
    projects, library, design, saved, body = setup_source(client)
    monkeypatch.setattr(workshop, "_complete", lambda *_: model_result())
    first = client.post("/api/workshop/source-route-handoffs", json=body)
    assert first.status_code == 202, first.text
    current = finish(client, first.json()["session_id"])
    selection = {
        **body, "operation_id": "adopt-second-route-explicitly",
        "session_id": current["id"], "revision": current["revision"],
        "route_id": saved["routes"][1]["route_id"],
        "author_note": "现在明确采用第二条作为讨论方向，保留此前兼容的作者要求。",
    }
    selection.pop("draft_id")
    second = client.post("/api/workshop/source-route-handoffs", json=selection)
    assert second.status_code == 202, second.text
    updated = finish(client, current["id"])
    assert updated["source_context"]["selected_proposal"]["route_id"] == saved["routes"][1]["route_id"]
    direction = next(node for node in updated["creative_design"]["story_state"]["map"]["nodes"] if node["area"] == "direction")
    assert direction["status"] == "settled"
    assert direction["evidence"] == [{"author_action_ref": selection["operation_id"]}]
    assert updated["source_context_history"][-1]["source_context"]["selection"]["action_id"] == body["operation_id"]


def test_prepared_handoff_retains_frozen_knowledge_if_catalog_changes_before_recovery(client, monkeypatch):
    projects, library, design, saved, body = setup_source(client)
    original_create = workshop.WorkshopService.create
    attempts = []

    def fail_once(self, *args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("Injected crash after validating selection")
        return original_create(self, *args, **kwargs)

    monkeypatch.setattr(workshop.WorkshopService, "create", fail_once)
    monkeypatch.setattr(workshop, "_complete", lambda *_: model_result())
    first = client.post("/api/workshop/source-route-handoffs", json=body)
    assert first.status_code == 409
    tag_id = body["input"]["creative_types"][1]["id"]
    selected = client.get("/api/creative-types/" + tag_id).json()
    disabled = client.post("/api/creative-types/" + tag_id + "/disable", json={
        "operation_id": "disable-after-prepared-handoff", "revision": selected["revision"],
    })
    assert disabled.status_code == 200, disabled.text
    recovered = client.post("/api/workshop/source-route-handoffs", json=body)
    assert recovered.status_code == 202, recovered.text
    final = finish(client, recovered.json()["session_id"])
    snapshot = final["creative_design"]["policy"]["type_snapshot"]
    saved_ref = next(row for row in snapshot["documents"] if row["id"] == tag_id)
    assert saved_ref["source_sha256"] == body["input"]["creative_types"][1]["source_sha256"]
