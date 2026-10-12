"""Session transactions remain atomic when flock does not exclude local threads."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from api import workshop
from api.jobs import JobRunner
from api.services import ProjectService


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(workshop, "runner", JobRunner())
    monkeypatch.setattr(workshop, "_skill_bundle", lambda *_: ("test skill", "test version"))
    monkeypatch.setattr(workshop, "_timeout", lambda: 900)
    # Some Docker host mounts do not exclude threads with blocking flock.
    monkeypatch.setattr(workshop.fcntl, "flock", lambda *_: None)
    return workshop.WorkshopService(ProjectService(tmp_path / "projects"))


def _lock_reader(service, session_id):
    attempting, acquired = threading.Event(), threading.Event()
    errors = []

    def read():
        attempting.set()
        try:
            with service._locked(session_id):
                acquired.set()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    assert attempting.wait(2)
    return thread, acquired, errors


def _join_reader(thread, acquired, errors):
    thread.join(2)
    assert not thread.is_alive(), "session lock was not released"
    assert not errors
    assert acquired.is_set()


def test_turn_worker_reads_only_after_running_state_is_persisted(service, monkeypatch):
    session = service.create(workshop.SessionInput(premise="A disputed inheritance"))
    save_pending, worker_started, worker_finished = (threading.Event() for _ in range(3))
    original_save, original_run = service._save, service._run_turn
    calls = []

    def delayed_save(data):
        if data["status"] == "running" and data["revision"] == 1:
            save_pending.set()
            assert worker_started.wait(2)
            # Without a local lock, the worker reads the old draft and exits
            # here. With the lock it must wait until this transaction commits.
            worker_finished.wait(0.2)
        return original_save(data)

    def observed_run(*args):
        worker_started.set()
        try:
            assert save_pending.wait(2)
            return original_run(*args)
        finally:
            worker_finished.set()

    def complete(snapshot, _skill):
        calls.append(snapshot)
        return ({"reply": "The heir refuses the deal.", "questions": [], "prompt_md": None,
                 "ready_for_confirmation": False},
                {"provider": "stub", "model": "stub", "timeout_seconds": 900})

    monkeypatch.setattr(service, "_save", delayed_save)
    monkeypatch.setattr(service, "_run_turn", observed_run)
    monkeypatch.setattr(workshop, "_complete", complete)
    job = service.turn(session["id"], workshop.TurnInput(revision=0, message="Develop the conflict"))
    assert worker_finished.wait(2)
    deadline = time.monotonic() + 2
    while workshop.runner.get(job["job_id"])["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.005)

    assert workshop.runner.get(job["job_id"])["status"] == "done"
    # Inspect durable state directly: get() must not hide a stranded session.
    saved = json.loads((service._path(session["id"]) / "session.json").read_text())
    assert len(calls) == 1
    assert calls[0]["revision"] == 1
    assert calls[0]["job_id"] == job["job_id"]
    assert saved["status"] == "draft"
    assert saved["revision"] == 2
    assert [message["role"] for message in saved["messages"]] == ["user", "assistant"]
    assert saved["messages"][-1]["content"] == "The heir refuses the deal."


@pytest.mark.parametrize("alias", [False, True])
def test_same_session_lock_is_shared_by_service_instances(service, tmp_path, alias):
    session = service.create(workshop.SessionInput())
    other = workshop.WorkshopService(service.projects)
    if alias:
        linked_root = tmp_path / "linked-workshop"
        linked_root.symlink_to(service.root, target_is_directory=True)
        other.root = linked_root
    assert other._path(session["id"]).resolve() == service._path(session["id"]).resolve()

    reader = None
    try:
        with service._locked(session["id"]):
            reader = _lock_reader(other, session["id"])
            assert not reader[1].wait(0.1), "another service entered an active session transaction"
    finally:
        if reader is not None:
            _join_reader(*reader)


@pytest.mark.parametrize("scope", ["session", "workspace", "project_root"])
def test_independent_session_paths_do_not_share_lock(service, tmp_path, scope):
    session = service.create(workshop.SessionInput())
    if scope == "session":
        other = workshop.WorkshopService(service.projects)
        other_session = other.create(workshop.SessionInput())
    else:
        workspace = SimpleNamespace(id="other-workspace", slug="other-workspace") if scope == "workspace" else None
        projects_root = service.projects.root if scope == "workspace" else tmp_path / "other" / "projects"
        other = workshop.WorkshopService(ProjectService(projects_root, workspace))
        other_session = dict(session)
        other._save(other_session)
    assert other._path(other_session["id"]).resolve() != service._path(session["id"]).resolve()

    reader = None
    try:
        with service._locked(session["id"]):
            reader = _lock_reader(other, other_session["id"])
            assert reader[1].wait(2), "an unrelated session was blocked"
    finally:
        if reader is not None:
            _join_reader(*reader)


@pytest.mark.parametrize("failure", ["body", "read", "flock"])
def test_session_lock_is_released_after_exception(service, monkeypatch, failure):
    session = service.create(workshop.SessionInput())
    path = service._path(session["id"]) / "session.json"
    if failure == "read":
        path.write_text("invalid JSON")
    elif failure == "flock":
        def fail_lock(*_):
            raise OSError("cannot acquire file lock")
        monkeypatch.setattr(workshop.fcntl, "flock", fail_lock)

    with pytest.raises((RuntimeError, ValueError, OSError)):
        with service._locked(session["id"]):
            raise RuntimeError("transaction failed")

    monkeypatch.setattr(workshop.fcntl, "flock", lambda *_: None)
    service._save(session)
    other = workshop.WorkshopService(service.projects)
    _join_reader(*_lock_reader(other, session["id"]))
