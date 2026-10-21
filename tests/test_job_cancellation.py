from __future__ import annotations

import json
import threading
import time

import pytest

from api.jobs import JobRunner, ProjectJobsRunning


def _finished(runner, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = runner.get(job_id)
        if job["status"] != "running":
            return job
        time.sleep(.005)
    raise AssertionError("worker did not exit")


@pytest.mark.parametrize("raises", [False, True])
def test_deferred_cancel_keeps_uniqueness_and_project_fence_until_worker_exits(raises):
    runner = JobRunner()
    entered, release, saved = threading.Event(), threading.Event(), threading.Event()
    cancellation = threading.Event()

    def worker():
        entered.set()
        assert release.wait(3)
        saved.set()
        if raises:
            raise ValueError("worker observed cancellation after saving")
        return None

    job_id = runner.submit("cover.analyze", worker,
        meta={"project_id": "book", "project_path": "/books/book"},
        unique_key="cover-analysis:one", cancel_event=cancellation,
        defer_cancel_completion=True)
    try:
        assert entered.wait(1)
        assert runner.cancel(job_id) and cancellation.is_set()
        assert runner.cancel(job_id)  # Repeated requests cannot release the fence.
        stopping = runner.get(job_id)
        assert stopping["status"] == "running" and stopping["cancel_requested"] is True
        assert stopping["finished_at"] is None and stopping["error"] is None
        assert not saved.is_set()
        json.dumps(stopping)
        assert not any(key.startswith("_") for key in stopping)
        assert runner.submit("cover.analyze", lambda: pytest.fail("duplicate worker started"),
            unique_key="cover-analysis:one") == job_id
        assert [job["job_id"] for job in runner.running_for_project("book", project_path="/books/book")] == [job_id]
        with pytest.raises(ProjectJobsRunning):
            runner.prepare_project_deletion("book", "/books/book")
        release.set()
        finished = _finished(runner, job_id)
        assert saved.is_set()
        assert finished["status"] == "error" and finished["error"] == "任务已暂停。"
        assert finished["cancel_requested"] is True and finished["finished_at"]
        replacement = runner.submit("cover.analyze", lambda: None, unique_key="cover-analysis:one")
        assert replacement != job_id
        assert _finished(runner, replacement)["status"] == "done"
    finally:
        release.set()


def test_default_cancellation_still_releases_unique_key_immediately():
    runner = JobRunner()
    release = threading.Event()
    job_id = runner.submit("workshop.turn", lambda: release.wait(3),
        unique_key="turn", cancel_event=threading.Event())
    try:
        assert runner.cancel(job_id)
        assert runner.get(job_id)["status"] == "error"
        replacement = runner.submit("workshop.turn", lambda: None, unique_key="turn")
        assert replacement != job_id
        assert _finished(runner, replacement)["status"] == "done"
    finally:
        release.set()
