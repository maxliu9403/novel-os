"""An explicit fresh start preserves history without reusing any previous model context."""
import json
import threading
import pytest

from api import workshop
from test_workshop_api import client, create, finish, model_result, ready


def test_restart_archives_old_round_and_excludes_old_candidate_context(client, monkeypatch, tmp_path):
    current = ready(client, monkeypatch, tmp_path, premise="Old story", chapters=21)
    session_id = current["id"]
    root = tmp_path / "workshop_v2" / session_id
    (root / "candidates" / "0.md").write_text("old rejected candidate", encoding="utf-8")
    usage = root / "outputs" / "usage" / "prior-request.json"
    usage.parent.mkdir(parents=True, exist_ok=True)
    usage.write_text('{"input_tokens": 300}\n', encoding="utf-8")
    old_messages = current["messages"]
    old_prompt = current["prompt_md"]
    changed = client.post(f"/api/workshop/sessions/{session_id}/input", json={
        "revision": current["revision"], "input": {**current["input"], "premise": "A completely new story"},
    }).json()
    calls, entered, release = [], threading.Event(), threading.Event()
    def complete(snapshot, _skill):
        calls.append(snapshot)
        entered.set()
        assert release.wait(3)
        return model_result(questions=["New question"])
    monkeypatch.setattr(workshop, "_complete", complete)
    endpoint = f"/api/workshop/sessions/{session_id}/restart"
    try:
        started = client.post(endpoint, json={"revision": changed["revision"]})
        assert started.status_code == 202
        assert entered.wait(1)
        duplicate = client.post(endpoint, json={"revision": changed["revision"]})
        assert duplicate.status_code == 202
        assert duplicate.json()["job_id"] == started.json()["job_id"]
        snapshot = calls[0]
        assert snapshot["input"]["premise"] == "A completely new story"
        assert snapshot["prompt_md"] == ""
        assert snapshot["questions"] == []
        assert len(snapshot["messages"]) == 1
        assert "_previous_candidate" not in snapshot
        assert snapshot.get("naming") is None
        assert snapshot["prepared"] is None
    finally:
        release.set()
    result = finish(client, session_id)
    assert result["questions"] == ["New question"]
    history = client.get(f"/api/workshop/sessions/{session_id}/history/{changed['revision']}")
    assert history.status_code == 200
    assert history.json()["prompt_md"] == old_prompt
    assert history.json()["input"]["premise"] == "Old story"
    assert history.json()["messages"][:len(old_messages)] == old_messages
    assert usage.read_text() == '{"input_tokens": 300}\n'
    assert result["history"][0]["revision"] == changed["revision"]
    assert client.post(endpoint, json={"revision": changed["revision"]}).json()["job_id"] == started.json()["job_id"]
    assert len(calls) == 1


def test_restart_requires_saved_input_changes_and_current_revision(client):
    data = create(client)
    assert client.post(f"/api/workshop/sessions/{data['id']}/restart", json={"revision": 9}).status_code == 409
    # A restart requires a saved input change, not an accidental duplicate of "start".
    assert client.post(f"/api/workshop/sessions/{data['id']}/restart", json={"revision": 0}).status_code == 409


def test_history_list_filters_across_all_pages(client, tmp_path):
    first = create(client, title="First")
    second = create(client, title="Second")
    path = tmp_path / "workshop_v2" / first["id"] / "session.json"
    data = json.loads(path.read_text())
    data.update(status="launched", project_id="first-project")
    path.write_text(json.dumps(data))
    result = client.get("/api/workshop/sessions?status=launched&limit=1").json()
    assert result["total"] == 1
    assert result["sessions"][0]["id"] == first["id"]
    assert result["sessions"][0]["project_id"] == "first-project"
    assert second["id"] != first["id"]


@pytest.mark.parametrize("status", ["running", "launched"])
def test_restart_rejects_active_and_launched_owners(client, tmp_path, status):
    data = create(client)
    path = tmp_path / "workshop_v2" / data["id"] / "session.json"
    data.update(status=status, restart_required=True)
    path.write_text(json.dumps(data))
    response = client.post(f"/api/workshop/sessions/{data['id']}/restart", json={"revision": data["revision"]})
    assert response.status_code == 409
    assert not (path.parent / "history").exists()


def test_archive_failure_never_submits_a_model_request(client, monkeypatch, tmp_path):
    data = create(client, premise="Before")
    root = tmp_path / "workshop_v2" / data["id"]
    changed = client.post(f"/api/workshop/sessions/{data['id']}/input", json={
        "revision": data["revision"], "input": {**data["input"], "premise": "After"},
    }).json()
    before = (root / "session.json").read_bytes()
    original = workshop._atomic
    def fail_archive(path, content):
        if path.parent.name == "history":
            raise OSError("archive unavailable")
        original(path, content)
    monkeypatch.setattr(workshop, "_atomic", fail_archive)
    monkeypatch.setattr(workshop, "_complete", lambda *_: pytest.fail("Must not invoke a model"))
    with pytest.raises(OSError, match="archive unavailable"):
        client.post(f"/api/workshop/sessions/{data['id']}/restart", json={"revision": changed["revision"]})
    assert (root / "session.json").read_bytes() == before


def test_repeated_edits_archive_the_original_round_not_an_intermediate_draft(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path, premise="Original")
    endpoint = f"/api/workshop/sessions/{data['id']}"
    changed = client.post(endpoint + "/input", json={"revision": data["revision"], "input": {**data["input"], "premise": "Draft 1"}}).json()
    changed = client.post(endpoint + "/input", json={"revision": changed["revision"], "input": {**changed["input"], "premise": "Final"}}).json()
    received = []
    monkeypatch.setattr(workshop, "_complete", lambda snapshot, _: (received.append(snapshot) or model_result()))
    assert client.post(endpoint + "/restart", json={"revision": changed["revision"]}).status_code == 202
    finish(client, data["id"])
    archived = client.get(endpoint + f"/history/{changed['revision']}").json()
    assert archived["input"]["premise"] == "Original"
    assert archived["messages"] == data["messages"]
    assert archived["replacement_input"]["premise"] == "Final"
    assert received[0]["input"]["premise"] == "Final"
    assert len(received[0]["messages"]) == 1


def test_history_identity_and_owner_are_verified(client, monkeypatch, tmp_path):
    data = create(client, premise="Original")
    endpoint = f"/api/workshop/sessions/{data['id']}"
    changed = client.post(endpoint + "/input", json={"revision": 0, "input": {**data["input"], "premise": "Final"}}).json()
    monkeypatch.setattr(workshop, "_complete", lambda *_: model_result())
    client.post(endpoint + "/restart", json={"revision": changed["revision"]})
    finish(client, data["id"])
    other = create(client)
    assert client.get(f"/api/workshop/sessions/{other['id']}/history/{changed['revision']}").status_code == 404
    assert client.get(endpoint + "/history/-1").status_code == 404
    path = tmp_path / "workshop_v2" / data["id"] / "history" / f"{changed['revision']}.json"
    archived = json.loads(path.read_text())
    archived["id"] = other["id"]
    path.write_text(json.dumps(archived))
    assert client.get(endpoint + f"/history/{changed['revision']}").status_code == 409


def test_history_symlink_cannot_redirect_snapshot_writes(client, tmp_path):
    data = create(client, premise="Original")
    external = tmp_path / "unrelated"
    external.mkdir()
    (tmp_path / "workshop_v2" / data["id"] / "history").symlink_to(external, target_is_directory=True)
    response = client.post(f"/api/workshop/sessions/{data['id']}/input", json={"revision": 0, "input": {**data["input"], "premise": "Final"}})
    assert response.status_code == 409
    assert list(external.iterdir()) == []
