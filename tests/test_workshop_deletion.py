"""Deletion removes access without losing linked books or their usage records."""
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from test_workshop_api import client, create, edit_session_file  # noqa: F401
from test_creative_types import default_refs

from api import workshop
from api.jobs import SessionJobBlocked
from api.services import ProjectService
from core.usage_tracking import record_usage, usage_scope


@pytest.mark.parametrize("status", ["draft", "paused", "error", "ready", "launched"])
def test_delete_is_durable_and_idempotent_and_blocks_all_session_access(client, tmp_path, status):
    data = create(client)
    edit_session_file(tmp_path, data["id"], status=status, prompt_md="# Existing skeleton")
    url = f'/api/workshop/sessions/{data["id"]}'
    response = client.delete(url, params={"revision": data["revision"]})
    assert response.status_code == 204, response.text
    assert response.content == b""
    saved_path = tmp_path / "workshop_v2" / data["id"] / "session.json"
    saved = saved_path.read_bytes()
    assert json.loads(saved)["deleted_at"]
    assert client.delete(url, params={"revision": data["revision"]}).status_code == 204
    assert saved_path.read_bytes() == saved
    assert client.get("/api/workshop/sessions").json() == {"sessions": [], "total": 0}
    for suffix in ("", "/history/0", "/costs"):
        assert client.get(url + suffix).status_code == 404
    assert client.post(url + "/turns", json={"revision": data["revision"], "message": "Continue"}).status_code == 404
    assert client.post(url + "/resume", json={"revision": data["revision"]}).status_code == 404
    fresh_service = workshop.WorkshopService(ProjectService(tmp_path / "projects"))
    with pytest.raises(HTTPException) as error:
        fresh_service.get(data["id"])
    assert error.value.status_code == 404
    invoked = threading.Event()
    with pytest.raises(SessionJobBlocked):
        workshop.runner.submit("cover.retry", invoked.set, {"session_id": data["id"]})
    assert not invoked.is_set()


def test_delete_refuses_stale_revision_and_running_work(client, tmp_path):
    data = create(client)
    url = f'/api/workshop/sessions/{data["id"]}'
    path = tmp_path / "workshop_v2" / data["id"] / "session.json"
    before = path.read_bytes()
    assert client.delete(url).status_code == 422
    assert client.delete(url, params={"revision": -1}).status_code == 422
    assert client.delete(url, params={"revision": 9}).status_code == 409
    assert path.read_bytes() == before
    edit_session_file(tmp_path, data["id"], status="running")
    before = path.read_bytes()
    assert client.delete(url, params={"revision": 0}).status_code == 409
    assert path.read_bytes() == before


def test_delete_does_not_interrupt_a_related_cover_worker(client, tmp_path):
    data = create(client)
    release = threading.Event()
    job = workshop.runner.submit("cover.generate", lambda: release.wait(5),
                                 {"session_id": data["id"]})
    try:
        result = client.delete(f'/api/workshop/sessions/{data["id"]}', params={"revision": 0})
        assert result.status_code == 409
        assert workshop.runner.get(job)["status"] == "running"
        assert client.get(f'/api/workshop/sessions/{data["id"]}').status_code == 200
    finally:
        release.set()


def test_deleted_discussion_keeps_linked_book_and_accounting(client, tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_OS_PRICING_PATH", str(tmp_path / "prices.json"))
    project = client.post("/api/projects", json={"title": "Independent Book", "genre": "Romance"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    data = create(client)
    edit_session_file(tmp_path, data["id"], status="launched", project_id=project_id)
    path = tmp_path / "workshop_v2" / data["id"]
    with usage_scope(path, "brainstorming"):
        record_usage(provider="codex", model="test-model", request_id="test-usage",
                     usage={"input_tokens": 10, "output_tokens": 20})
    book_before = client.get(f"/api/projects/{project_id}").json()
    costs_before = client.get(f"/api/projects/{project_id}/costs").json()
    assert costs_before["totals"]["total_tokens"] == 30
    assert client.delete(f'/api/workshop/sessions/{data["id"]}', params={"revision": 0}).status_code == 204
    assert client.get(f"/api/projects/{project_id}").json() == book_before
    assert client.get(f"/api/projects/{project_id}/costs").json() == costs_before


def test_deletion_respects_workspace_and_rejects_symlinks(client, tmp_path):
    data = create(client)
    service = workshop.WorkshopService(ProjectService(tmp_path / "projects",
        SimpleNamespace(id="other-workspace", slug="other-workspace")))
    other = service.create(workshop.SessionInput(creative_types=default_refs()))
    assert client.delete(f'/api/workshop/sessions/{other["id"]}', params={"revision": 0}).status_code == 404
    assert service.get(other["id"])["status"] == "draft"
    alias = "b" * 32
    root = tmp_path / "workshop_v2"
    (root / alias).symlink_to(root / data["id"], target_is_directory=True)
    assert client.delete(f"/api/workshop/sessions/{alias}", params={"revision": 0}).status_code == 404
    assert client.get(f'/api/workshop/sessions/{data["id"]}').status_code == 200


def test_stale_worker_cannot_republish_a_deleted_session(client, tmp_path):
    data = create(client)
    svc = workshop.WorkshopService(ProjectService(tmp_path / "projects"))
    waiting = threading.Event()
    failures = []

    def delayed_write():
        waiting.set()
        try:
            with svc._locked(data["id"]) as current:
                current["status"] = "ready"
                svc._save(current)
        except HTTPException as exc:
            failures.append(exc.status_code)

    with svc._locked(data["id"]) as current:
        worker = threading.Thread(target=delayed_write)
        worker.start()
        assert waiting.wait(2)
        current["deleted_at"] = workshop._now()
        svc._save(current)
    worker.join(2)
    assert not worker.is_alive()
    assert failures == [404]
    assert client.get("/api/workshop/sessions").json()["total"] == 0


def test_failed_deletion_does_not_block_future_work(client, tmp_path, monkeypatch):
    data = create(client)
    svc = workshop.WorkshopService(ProjectService(tmp_path / "projects"))
    def failed_save(_data):
        raise OSError("Disk unavailable")
    monkeypatch.setattr(svc, "_save", failed_save)
    with pytest.raises(OSError):
        svc.delete(data["id"], 0)
    assert svc.get(data["id"])["status"] == "draft"
    executed = threading.Event()
    workshop.runner.submit("workshop.test", executed.set, {"session_id": data["id"]})
    assert executed.wait(2)
