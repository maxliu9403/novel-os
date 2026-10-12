"""Pause/resume keeps durable context and fences late model callbacks."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from api import workshop, workshop_titles
from api.jobs import JobRunner
from api.services import ProjectService
from test_workshop_api import client, create, finish, model_result, ready  # noqa: F401


@pytest.mark.parametrize("late_failure", [False, True])
def test_pause_resume_retains_context_and_fences_late_result(client, monkeypatch, tmp_path, late_failure):
    data = ready(client, monkeypatch, tmp_path)
    url = f'/api/workshop/sessions/{data["id"]}'
    entered, release, left = threading.Event(), threading.Event(), threading.Event()
    snapshots = []
    def delayed(snapshot, _):
        snapshots.append(snapshot)
        entered.set()
        try:
            assert release.wait(5)
            if late_failure:
                raise RuntimeError("late provider failure")
            return model_result(data["prompt_md"] + "\n旧请求不应生效。")
        finally:
            left.set()
    monkeypatch.setattr(workshop, "_complete", delayed)
    job = client.post(url + "/turns", json={"revision": data["revision"], "message": "加强女儿动机"}).json()
    try:
        assert entered.wait(1)
        before = client.get(url).json()
        paused_response = client.post(url + "/pause", json={"revision": data["revision"], "job_id": job["job_id"]})
        assert paused_response.status_code == 200
        paused = paused_response.json()
        assert paused["status"] == "paused"
        assert paused["messages"] == before["messages"]
        assert paused["prompt_md"] == data["prompt_md"]
        assert paused["logs"][-1]["event"] == "turn_paused"
        summaries = client.get("/api/workshop/sessions").json()["sessions"]
        assert next(row for row in summaries if row["id"] == data["id"])["status"] == "paused"
        assert paused["paused_at"] and not paused["ready_for_confirmation"]
        assert snapshots[0]["_cancellation_event"].is_set()
        assert client.post(url + "/pause", json={"revision": data["revision"], "job_id": job["job_id"]}).json() == paused
        assert client.post(url + "/prepare", json={"revision": paused["revision"]}).status_code == 422
        resumed_snapshots = []
        def completed(snapshot, _):
            resumed_snapshots.append(snapshot)
            result, route = model_result(data["prompt_md"])
            result["reply"] = "恢复后的结果"
            return result, route
        monkeypatch.setattr(workshop, "_complete", completed)
        restarted = client.post(url + "/resume", json={"revision": paused["revision"]})
        assert restarted.status_code == 202
        assert restarted.json()["job_id"] != job["job_id"]
        finished = finish(client, data["id"])
        assert finished["status"] == "ready"
        assert resumed_snapshots[0]["messages"] == before["messages"]
        assert resumed_snapshots[0]["prompt_md"] == data["prompt_md"]
        assert finished["messages"][-1]["content"] == "恢复后的结果"
        release.set()
        assert left.wait(1)
        # Let the old worker pass through its completion/failure handler.
        time.sleep(0.05)
        assert client.get(url).json() == finished
        assert workshop.runner.get(job["job_id"])["error"] == "任务已暂停。"
        assert client.post(url + "/pause", json={"revision": finished["revision"], "job_id": job["job_id"]}).status_code == 409
    finally:
        release.set()


def test_pause_before_worker_reads_session_cannot_execute_resumed_input(client, monkeypatch):
    data = create(client)
    url = f'/api/workshop/sessions/{data["id"]}'
    entered, release, left = threading.Event(), threading.Event(), threading.Event()
    original = workshop.WorkshopService._run_turn
    def delayed(self, *args):
        entered.set()
        assert release.wait(5)
        try:
            return original(self, *args)
        finally:
            left.set()
    calls = []
    monkeypatch.setattr(workshop, "_complete", lambda snapshot, _: calls.append(snapshot) or model_result(questions=["结局？"]))
    monkeypatch.setattr(workshop.WorkshopService, "_run_turn", delayed)
    job = client.post(url + "/turns", json={"revision": 0, "message": "开始"}).json()
    try:
        assert entered.wait(1)
        paused = client.post(url + "/pause", json={"revision": 0, "job_id": job["job_id"]}).json()
        monkeypatch.setattr(workshop.WorkshopService, "_run_turn", original)
        client.post(url + "/resume", json={"revision": paused["revision"]})
        finished = finish(client, data["id"])
        release.set()
        assert left.wait(1)
        assert len(calls) == 1
        assert client.get(url).json() == finished
    finally:
        release.set()


def test_naming_pause_resumes_same_request_and_completed_pause_is_noop(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path)
    url = f'/api/workshop/sessions/{data["id"]}'
    entered, release = threading.Event(), threading.Event()
    def delayed(snapshot, _):
        entered.set()
        assert release.wait(5)
        return [{"title": "Old title", "reason": "obsolete"}], {}
    monkeypatch.setattr(workshop_titles, "complete_titles", delayed)
    job = client.post(url + "/titles", json={"revision": data["revision"], "instructions": "缩短", "current_title": "My Title"}).json()
    try:
        assert entered.wait(1)
        paused = client.post(url + "/pause", json={"revision": data["revision"], "job_id": job["job_id"]}).json()
        assert paused["active_operation"] == "titles"
        requests = paused["naming"]["requests"]
        assert len(requests) == 1 and requests[0]["current_title"] == "My Title"
        received = []
        monkeypatch.setattr(workshop_titles, "complete_titles", lambda snapshot, _: received.append(snapshot) or ([{"title": "New Title", "reason": "new"}], {}))
        new_job = client.post(url + "/resume", json={"revision": paused["revision"]}).json()
        finished = finish(client, data["id"])
        assert received[0]["naming"]["requests"] == requests
        assert len(finished["naming"]["requests"]) == 1
        assert client.post(url + "/pause", json={"revision": paused["revision"], "job_id": new_job["job_id"]}).json() == finished
        release.set()
        time.sleep(0.05)
        assert client.get(url).json() == finished
    finally:
        release.set()


def test_pause_survives_runner_restart_and_duplicate_resume_is_idempotent(client, monkeypatch):
    data = create(client)
    url = f'/api/workshop/sessions/{data["id"]}'
    entered, release = threading.Event(), threading.Event()
    def delayed(*_):
        entered.set()
        assert release.wait(5)
        return model_result(questions=["结局？"])
    monkeypatch.setattr(workshop, "_complete", delayed)
    job = client.post(url + "/turns", json={"revision": 0, "message": "开始"}).json()
    try:
        assert entered.wait(1)
        paused = client.post(url + "/pause", json={"revision": 0, "job_id": job["job_id"]}).json()
        monkeypatch.setattr(workshop, "runner", JobRunner())
        assert client.get(url).json()["status"] == "paused"
        resumed = client.post(url + "/resume", json={"revision": paused["revision"]})
        again = client.post(url + "/resume", json={"revision": paused["revision"]})
        assert resumed.json()["job_id"] == again.json()["job_id"]
    finally:
        release.set()
        finish(client, data["id"])


def test_jobs_cancellation_is_opt_in_and_releases_unique_key():
    runner = JobRunner()
    release, exited = threading.Event(), threading.Event()
    event = threading.Event()
    def delayed():
        try:
            assert release.wait(3)
        finally:
            exited.set()
    old = runner.submit("workshop.turn", delayed, unique_key="one", cancel_event=event)
    try:
        json.dumps(runner.get(old))  # Never expose an Event in the API record.
        assert runner.cancel(old) and event.is_set()
        new = runner.submit("workshop.turn", lambda: None, unique_key="one")
        assert new != old
        assert not runner.cancel(new)
        release.set()
        assert exited.wait(1)
        time.sleep(0.02)
        assert runner.get(old)["error"] == "任务已暂停。"
    finally:
        release.set()


def test_new_turn_is_not_deduplicated_against_job_finishing_after_session_commit(client, monkeypatch):
    data = create(client)
    url = f'/api/workshop/sessions/{data["id"]}'
    pending, release = threading.Event(), threading.Event()
    real_update = workshop.runner._update
    first = True
    def delayed_update(job_id, **fields):
        nonlocal first
        if first:
            first = False
            pending.set()
            assert release.wait(5)
        return real_update(job_id, **fields)
    monkeypatch.setattr(workshop.runner, "_update", delayed_update)
    monkeypatch.setattr(workshop, "_complete", lambda *_: model_result(questions=["下一项设定？"]))
    old = client.post(url + "/turns", json={"revision": 0, "message": "第一轮"}).json()
    try:
        assert pending.wait(1)
        finished = client.get(url).json()
        assert finished["status"] == "draft"
        assert workshop.runner.get(old["job_id"])["status"] == "running"
        new = client.post(url + "/turns", json={"revision": finished["revision"], "message": "第二轮"}).json()
        assert new["job_id"] != old["job_id"]
        current = finish(client, data["id"])
        assert current["messages"][-2]["content"] == "第二轮"
    finally:
        release.set()


def test_pause_is_scoped_to_workspace_even_for_same_session_id(tmp_path, monkeypatch):
    monkeypatch.setattr(workshop, "runner", JobRunner())
    services = [workshop.WorkshopService(ProjectService(tmp_path / "projects", SimpleNamespace(id=name, slug=name)))
                for name in ("a", "b")]
    first = services[0].create(workshop.SessionInput(title="Same name"))
    # Reproduce identical ids in two tenant directories; their jobs remain independent.
    services[1]._save(dict(first))
    snapshots, release = [], threading.Event()
    def delayed(snapshot, _):
        snapshots.append(snapshot)
        assert release.wait(5)
        return model_result(questions=["结局？"])
    monkeypatch.setattr(workshop, "_complete", delayed)
    jobs = [service.turn(first["id"], workshop.TurnInput(revision=0, message="开始")) for service in services]
    try:
        deadline = time.monotonic() + 1
        while len(snapshots) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(snapshots) == 2
        services[0].pause(first["id"], workshop.PauseInput(revision=0, job_id=jobs[0]["job_id"]))
        assert services[0].get(first["id"])["status"] == "paused"
        assert services[1].get(first["id"])["status"] == "running"
        assert sum(snapshot["_cancellation_event"].is_set() for snapshot in snapshots) == 1
    finally:
        release.set()
        for _ in range(100):
            if services[1].get(first["id"])["status"] != "running":
                break
            time.sleep(0.01)
