"""Reconcile finished jobs without losing the workshop's durable context."""
import copy
import json
import threading
import time

import pytest

from api import workshop, workshop_titles
from api.jobs import JobRunner
from api.services import ProjectService
from test_workshop_api import complete_prompt, model_result


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(workshop, "runner", JobRunner())
    monkeypatch.setenv("NOVEL_OS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    return workshop.WorkshopService(ProjectService(tmp_path / "projects"))


def stored_session(service, session_id):
    """Read persistence without invoking the recovery behavior under test."""
    with service._locked(session_id) as data:
        return copy.deepcopy(data)


def seed_ready_session(service, tmp_path):
    data = service.create(workshop.SessionInput(title="A Mother's Choice"))
    with service._locked(data["id"]) as stored:
        stored.update(
            status="ready", ready_for_confirmation=True,
            prompt_md=complete_prompt(tmp_path),
            messages=[
                {"role": "user", "content": "保留最初决定：母亲主动选择新生活。"},
                {"role": "assistant", "content": "已保存完整骨架。", "questions": []},
            ],
        )
        service._save(stored)
    return service.get(data["id"])


def start_operation(service, data, operation):
    if operation == "titles":
        return service.titles(data["id"], workshop.TitleInput(
            revision=data["revision"], instructions="简短，保留母亲成长的意味", current_title="Her Own Name",
        ))
    return service.turn(data["id"], workshop.TurnInput(
        revision=data["revision"], message="继续保留早期决定，并加强女儿动机。",
    ))


def wait_for_job(job_id, expected=None):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = workshop.runner.get(job_id)
        if job["status"] != "running":
            if expected is not None:
                assert job["status"] == expected, job
            return job
        time.sleep(0.005)
    pytest.fail("Stub workshop job did not finish within three seconds")


def read_via(service, session_id, read_method):
    if read_method == "get":
        return service.get(session_id)
    listed = service.list_sessions()
    assert listed["total"] == 1
    summary = next(row for row in listed["sessions"] if row["id"] == session_id)
    persisted = stored_session(service, session_id)
    assert summary["status"] == persisted["status"]
    assert summary["message_count"] == len(persisted["messages"])
    assert summary["has_prompt"] == bool(persisted["prompt_md"])
    return persisted


@pytest.mark.parametrize("operation", ["brainstorm", "titles"])
@pytest.mark.parametrize("terminal_status", ["done", "error"])
@pytest.mark.parametrize("read_method", ["get", "list"])
def test_terminal_job_recovers_running_session_and_preserves_context(
    service, tmp_path, monkeypatch, operation, terminal_status, read_method,
):
    data = seed_ready_session(service, tmp_path)

    def exit_without_session_update(*_):
        if terminal_status == "error":
            raise RuntimeError("private-provider-request-body")

    worker = "_run_titles" if operation == "titles" else "_run_turn"
    original_worker = getattr(service, worker)
    monkeypatch.setattr(service, worker, exit_without_session_update)
    job = start_operation(service, data, operation)
    wait_for_job(job["job_id"], terminal_status)
    before = stored_session(service, data["id"])
    assert before["status"] == "running"

    recovered = read_via(service, data["id"], read_method)

    assert recovered["status"] == "error"
    assert recovered["last_error"]
    assert "private-provider-request-body" not in json.dumps(recovered)
    assert recovered["messages"] == before["messages"]
    assert recovered["prompt_md"] == before["prompt_md"]
    assert recovered["questions"] == before["questions"]
    assert recovered["active_operation"] == operation
    if operation == "titles":
        assert recovered["naming"]["candidates"] == before["naming"]["candidates"]
        for key, value in before["naming"]["requests"][-1].items():
            assert recovered["naming"]["requests"][-1][key] == value
    assert len(recovered["logs"]) == len(before["logs"]) + 1
    assert recovered["logs"][-1]["event"] in {"interrupted", "turn_failed"}
    assert stored_session(service, data["id"]) == recovered
    assert service.get(data["id"]) == recovered
    assert read_via(service, data["id"], "list") == recovered

    # Recovery must allow a new turn while retaining all previously saved work.
    monkeypatch.setattr(service, worker, original_worker)
    monkeypatch.setattr(workshop, "_complete", lambda *_: model_result(questions=["女儿下一步怎么选择？"]))
    retry = service.turn(data["id"], workshop.TurnInput(
        revision=recovered["revision"], message="继续处理未完成的意见。",
    ))
    wait_for_job(retry["job_id"], "done")
    finished = service.get(data["id"])
    assert finished["status"] == "draft"
    assert finished["prompt_md"] == before["prompt_md"]
    assert finished["messages"][:-2] == before["messages"]


@pytest.mark.parametrize("operation", ["brainstorm", "titles"])
@pytest.mark.parametrize("read_method", ["get", "list"])
def test_active_job_remains_running_until_model_completes(
    service, tmp_path, monkeypatch, operation, read_method,
):
    data = seed_ready_session(service, tmp_path)
    entered, release = threading.Event(), threading.Event()

    def delayed_complete(*_):
        entered.set()
        assert release.wait(5)
        if operation == "titles":
            return [{"title": "Her Own Name", "reason": "呼应成长"}], {}
        return model_result(questions=["女儿下一步怎么选择？"])

    target, name = (workshop_titles, "complete_titles") if operation == "titles" else (workshop, "_complete")
    monkeypatch.setattr(target, name, delayed_complete)
    job = start_operation(service, data, operation)
    try:
        assert entered.wait(2)
        before = stored_session(service, data["id"])
        current = read_via(service, data["id"], read_method)
        assert current == before
        assert current["status"] == "running"
        assert workshop.runner.get(job["job_id"])["status"] == "running"
    finally:
        release.set()
        wait_for_job(job["job_id"], "done")
    assert service.get(data["id"])["status"] == ("ready" if operation == "titles" else "draft")


@pytest.mark.parametrize("operation", ["brainstorm", "titles"])
@pytest.mark.parametrize("read_method", ["get", "list"])
def test_old_completed_job_cannot_interrupt_newer_running_operation(
    service, tmp_path, monkeypatch, operation, read_method,
):
    data = seed_ready_session(service, tmp_path)
    worker = "_run_titles" if operation == "titles" else "_run_turn"
    original_worker = getattr(service, worker)
    monkeypatch.setattr(service, worker, lambda *_: None)
    old_job = start_operation(service, data, operation)
    wait_for_job(old_job["job_id"], "done")
    before_pause = stored_session(service, data["id"])
    paused = service.pause(data["id"], workshop.PauseInput(
        revision=before_pause["revision"], job_id=old_job["job_id"],
    ))
    monkeypatch.setattr(service, worker, original_worker)
    entered, release = threading.Event(), threading.Event()

    def delayed_complete(*_):
        entered.set()
        assert release.wait(5)
        if operation == "titles":
            return [{"title": "Her Own Name", "reason": "呼应成长"}], {}
        return model_result(questions=["女儿下一步怎么选择？"])

    target, name = (workshop_titles, "complete_titles") if operation == "titles" else (workshop, "_complete")
    monkeypatch.setattr(target, name, delayed_complete)
    new_job = service.resume(data["id"], workshop.RevisionInput(revision=paused["revision"]))
    try:
        assert entered.wait(2)
        assert new_job["job_id"] != old_job["job_id"]
        before_read = stored_session(service, data["id"])
        current = read_via(service, data["id"], read_method)
        assert current == before_read
        assert current["status"] == "running"
        assert current["job_id"] == new_job["job_id"]
        assert workshop.runner.get(old_job["job_id"])["status"] == "done"
    finally:
        release.set()
        wait_for_job(new_job["job_id"], "done")


def test_actual_turn_calls_model_and_commits_result_within_bounded_wait(service, monkeypatch):
    seen = []

    def complete(snapshot, skill):
        seen.append((snapshot, skill))
        return model_result(questions=["结局要保留什么情绪？"])

    monkeypatch.setattr(workshop, "_complete", complete)
    data = service.create(workshop.SessionInput(title="A Mother's Choice"))
    job = service.turn(data["id"], workshop.TurnInput(revision=0, message="开始构思"))
    wait_for_job(job["job_id"], "done")
    finished = service.get(data["id"])
    assert len(seen) == 1
    assert seen[0][0]["messages"] == [{"role": "user", "content": "开始构思"}]
    assert seen[0][1]
    assert finished["status"] == "draft"
    assert finished["revision"] == 2
    assert finished["questions"] == ["结局要保留什么情绪？"]
    assert finished["messages"][-1]["content"] == "本轮完成"
