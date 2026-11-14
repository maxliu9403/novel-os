from __future__ import annotations

import threading
import time

from api.jobs import JobRunner
from core.projection_events import change, notify, subscribe


def wait_done(jobs, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if jobs.get(job_id)["status"] != "running":
            return
        time.sleep(.005)
    raise AssertionError("test job did not finish")


def test_snapshot_is_scoped_detached_and_versions_cancellation_before_completion(tmp_path):
    jobs = JobRunner()
    root, other = tmp_path / "first", tmp_path / "other"
    release, cancel = threading.Event(), threading.Event()
    job = jobs.submit("novel.resume", lambda: release.wait(3),
        {"project_path": str(root / "book"), "project_id": "book", "run_id": "old-run"},
        cancel_event=cancel, defer_cancel_completion=True)
    try:
        before = jobs.library_snapshot(root)
        assert before["revision"] == 1
        assert len(before["jobs"]) == 1
        assert jobs.library_snapshot(other)["revision"] == 0
        assert jobs.library_snapshot(other)["jobs"] == []
        before["jobs"][0]["meta"]["run_id"] = "tampered"
        assert jobs.get(job)["meta"]["run_id"] == "old-run"
        assert jobs.cancel(job)
        cancelling = jobs.library_snapshot(root)
        assert cancelling["revision"] == 2
        assert cancelling["jobs"][0]["status"] == "running"
        assert cancelling["jobs"][0]["cancel_requested"]
        assert before["jobs"][0]["cancel_requested"] is False
    finally:
        release.set()
        wait_done(jobs, job)
    after = jobs.library_snapshot(root)
    assert after["revision"] == 3
    assert [event["revision"] for event in after["changes"]] == [1, 2, 3]
    assert after["oldest_revision"] == 0
    assert JobRunner().library_snapshot(root)["boot_epoch"] != after["boot_epoch"]


def test_id_only_jobs_cannot_leak_into_another_library(tmp_path):
    jobs = JobRunner()
    job = jobs.submit("novel.resume", lambda: None, {"project_id": "book"})
    wait_done(jobs, job)
    assert jobs.library_snapshot(tmp_path)["jobs"] == []
    assert jobs.library_snapshot(tmp_path)["revision"] == 0


def test_projection_intent_and_completion_survive_failed_canonical_write(tmp_path):
    events = []
    unsubscribe = subscribe(lambda path, domains, kind: events.append((path, domains, kind)))
    try:
        try:
            with change(tmp_path / "book", "chapters"):
                raise ValueError("interrupted commit")
        except ValueError:
            pass
        assert [event[2] for event in events] == ["intent", "changed"]
        assert events[0][0] == (tmp_path / "book").resolve()
    finally:
        unsubscribe()
    notify(tmp_path / "book")
    assert len(events) == 2


def test_projection_observer_failure_does_not_change_manuscript_result(tmp_path):
    import pytest
    def unavailable(*args):
        raise RuntimeError("index offline")
    unsubscribe = subscribe(unavailable)
    try:
        with change(tmp_path / "book", "chapters"):
            (tmp_path / "saved.txt").write_text("saved completely")
        assert (tmp_path / "saved.txt").read_text() == "saved completely"
        with pytest.raises(RuntimeError, match="index offline"):
            notify(tmp_path / "book", kind="deleted", required=True)
    finally:
        unsubscribe()
