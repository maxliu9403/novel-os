"""Poster polling is read-only and never invents interruptions during writes."""
from contextlib import contextmanager
import threading
from types import SimpleNamespace

import pytest

from api import poster_tasks
from api.jobs import JobRunner
from core.cover_store import CoverStore


@pytest.mark.parametrize("fails", [False, True], ids=["complete", "failed"])
def test_poll_cannot_compare_stale_running_receipt_with_finished_job(tmp_path, monkeypatch, fails):
    flow = SimpleNamespace(store=CoverStore(tmp_path / "book"),
                           context=SimpleNamespace(owner_scope="owner"))
    entered, release, finished = (threading.Event() for _ in range(3))

    class ObservedRunner(JobRunner):
        def _update(self, job_id, **fields):
            super()._update(job_id, **fields)
            finished.set()

    runner = ObservedRunner()

    def operation(prepared, progress):
        entered.set()
        assert release.wait(5)
        if fails:
            raise ValueError("fixture failure")
        return {"saved": True}

    started = poster_tasks.start(flow, runner, kind="refine", request_id="projection-race",
        inputs={}, preflight=lambda: {}, operation=operation, project_id="book")
    assert entered.wait(3)
    reader_thread = threading.get_ident()
    real_lock, real_records = flow.store.mutation_lock, poster_tasks.records

    @contextmanager
    def observed_lock():
        assert threading.get_ident() != reader_thread, "Polling must not take the mutation lock"
        with real_lock():
            yield

    intercepted = False

    def records_during_completion(current_flow):
        nonlocal intercepted
        snapshot = real_records(current_flow)
        if not intercepted:
            intercepted = True
            assert snapshot[0]["status"] == "running"
            release.set()
            # Complete a real worker inside the read/project gap, so the
            # receipt snapshot says running but its job has already finished.
            assert finished.wait(3)
        return snapshot

    monkeypatch.setattr(flow.store, "mutation_lock", observed_lock)
    monkeypatch.setattr(poster_tasks, "records", records_during_completion)
    try:
        observed = poster_tasks.list_tasks(flow, runner)
        assert observed[0]["status"] == ("failed" if fails else "complete")
        assert finished.wait(3)
        final = poster_tasks.list_tasks(flow, runner)[0]
        assert final["task_id"] == started["task_id"]
        assert final["status"] == ("failed" if fails else "complete")
        assert final["error"] == ("fixture failure" if fails else None)
        if not fails:
            assert final["result"] == {"saved": True}
    finally:
        release.set()
        assert finished.wait(3)


def test_empty_poll_does_not_create_directories_or_probe_file_locks(tmp_path, monkeypatch):
    flow = SimpleNamespace(store=CoverStore(tmp_path / "book"),
                           context=SimpleNamespace(owner_scope="owner"))

    def reject_lock(*args):
        pytest.fail("Polling must not probe execution or mutation locks")

    monkeypatch.setattr("fcntl.flock", reject_lock)
    assert poster_tasks.list_tasks(flow, JobRunner()) == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("fails", [False, True], ids=["complete", "failed"])
def test_publication_then_completion_during_poll_does_not_invent_interruption(tmp_path, monkeypatch, fails):
    flow = SimpleNamespace(store=CoverStore(tmp_path / "book"),
                           context=SimpleNamespace(owner_scope="owner"))
    reserved = {"task_id": "poster-task-" + "a" * 32, "owner_scope": "owner",
                "process_id": poster_tasks._PROCESS, "status": "queued", "job_id": "",
                "created_at": "2026-11-08T00:00:00Z", "error": None}
    running = {**reserved, "status": "running", "job_id": "job-1"}
    terminal = {**running, "status": "failed" if fails else "complete",
                "error": "fixture failure" if fails else None}
    job = {"poster_task_id": reserved["task_id"], "status": "running"}
    runner = SimpleNamespace(get=lambda job_id: dict(job) if job_id else None)
    real_read = poster_tasks.read

    def records_before_publication(current_flow):
        # The reservation snapshot predates publication, but publication has
        # ended by the time list_tasks checks the in-memory submission fence.
        poster_tasks.write(current_flow, running)
        return [dict(reserved)]

    def read_during_completion(current_flow, task_id):
        snapshot = real_read(current_flow, task_id)
        if snapshot["status"] == "running":
            # A second transition follows publication: persist terminal first,
            # then finish its job, after the caller captured the active receipt.
            poster_tasks.write(current_flow, terminal)
            job["status"] = "error" if fails else "done"
        return snapshot

    monkeypatch.setattr(poster_tasks, "records", records_before_publication)
    monkeypatch.setattr(poster_tasks, "read", read_during_completion)
    observed = poster_tasks.list_tasks(flow, runner)[0]
    assert observed["status"] == terminal["status"]
    assert observed["error"] == terminal["error"]


@pytest.mark.parametrize("stage", ["before_registration", "after_registration", "submit_failure",
                                   "aborted_submit", "failed_receipt"])
def test_poll_hides_unpublished_reservation_without_waiting_for_submit(tmp_path, monkeypatch, stage):
    flow = SimpleNamespace(store=CoverStore(tmp_path / "book"),
                           context=SimpleNamespace(owner_scope="owner"))
    submitting, publish, entered, release, finished = (threading.Event() for _ in range(5))

    class PausedRunner(JobRunner):
        def submit(self, *args, **kwargs):
            if stage == "after_registration":
                job_id = super().submit(*args, **kwargs)
            submitting.set()
            assert publish.wait(5)
            if stage == "aborted_submit":
                raise KeyboardInterrupt("fixture aborted submission")
            if stage in {"submit_failure", "failed_receipt"}:
                raise ValueError("fixture submission failure")
            return job_id if stage == "after_registration" else super().submit(*args, **kwargs)

        def _update(self, job_id, **fields):
            super()._update(job_id, **fields)
            finished.set()

    runner = PausedRunner()
    outcomes = []
    if stage == "failed_receipt":
        real_write = poster_tasks.write

        def fail_terminal_write(current_flow, record):
            if record["status"] == "failed":
                raise OSError("fixture unavailable storage")
            real_write(current_flow, record)

        monkeypatch.setattr(poster_tasks, "write", fail_terminal_write)

    def operation(prepared, progress):
        entered.set()
        assert release.wait(5)
        return {"saved": True}

    def start():
        try:
            outcomes.append(poster_tasks.start(flow, runner, kind="refine", request_id="projection-submit",
                inputs={}, preflight=lambda: {}, operation=operation, project_id="book"))
        except BaseException as exc:
            outcomes.append(exc)

    worker = threading.Thread(target=start)
    worker.start()
    try:
        assert submitting.wait(3)
        reserved = poster_tasks.records(flow)[0]
        assert reserved["status"] == "queued" and not reserved["job_id"]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in tmp_path.rglob("*") if path.is_file()}

        def reject_lock():
            pytest.fail("Polling must not wait for a reservation to publish its job id")

        # start() already owns this store's lock; polling must finish while the
        # submitting thread is paused, even after JobRunner registered the job.
        with monkeypatch.context() as patch:
            patch.setattr(flow.store, "mutation_lock", reject_lock)
            assert poster_tasks.list_tasks(flow, runner) == []
        assert {path: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in tmp_path.rglob("*") if path.is_file()} == before
        publish.set()
        worker.join(3)
        assert not worker.is_alive()
        if stage == "submit_failure":
            assert isinstance(outcomes[0], ValueError)
            final = poster_tasks.list_tasks(flow, runner)[0]
            assert final["status"] == "failed"
            assert final["error"] == "fixture submission failure"
        elif stage in {"aborted_submit", "failed_receipt"}:
            assert isinstance(outcomes[0], KeyboardInterrupt if stage == "aborted_submit" else OSError)
            assert poster_tasks.list_tasks(flow, runner)[0]["status"] == "interrupted"
        else:
            assert entered.wait(3)
            assert poster_tasks.list_tasks(flow, runner)[0]["status"] == "running"
            release.set()
            assert finished.wait(3)
            assert poster_tasks.list_tasks(flow, runner)[0]["status"] == "complete"
    finally:
        publish.set()
        release.set()
        worker.join(3)
        if stage in {"before_registration", "after_registration"}:
            assert finished.wait(3)


@pytest.mark.parametrize("fails", [False, True], ids=["complete", "failed"])
def test_terminal_receipt_is_visible_before_runner_finishes(tmp_path, fails):
    flow = SimpleNamespace(store=CoverStore(tmp_path / "book"),
                           context=SimpleNamespace(owner_scope="owner"))
    finishing, release, finished = (threading.Event() for _ in range(3))

    class PausedRunner(JobRunner):
        def _update(self, job_id, **fields):
            finishing.set()
            assert release.wait(5)
            super()._update(job_id, **fields)
            finished.set()

    runner = PausedRunner()

    def operation(prepared, progress):
        if fails:
            raise ValueError("fixture failure")
        return {"saved": True}

    started = poster_tasks.start(flow, runner, kind="refine", request_id="projection-terminal",
        inputs={}, preflight=lambda: {}, operation=operation, project_id="book")
    try:
        assert finishing.wait(3)
        private = poster_tasks.read(flow, started["task_id"])
        assert runner.get(private["job_id"])["status"] == "running"
        observed = poster_tasks.list_tasks(flow, runner)[0]
        assert observed["status"] == ("failed" if fails else "complete")
        assert observed["error"] == ("fixture failure" if fails else None)
    finally:
        release.set()
        assert finished.wait(3)


@pytest.mark.parametrize("case", ["old_process", "old_reservation", "orphan_reservation",
                                  "missing_job", "wrong_task", "finished_job"])
def test_real_interruption_remains_visible_without_mutating_receipt(tmp_path, monkeypatch, case):
    flow = SimpleNamespace(store=CoverStore(tmp_path / "book"),
                           context=SimpleNamespace(owner_scope="owner"))
    record = {"task_id": "poster-task-" + "a" * 32, "owner_scope": "owner",
              "status": "queued" if case.endswith("reservation") else "running",
              "process_id": "previous-process" if case.startswith("old_") else poster_tasks._PROCESS,
              "job_id": "" if case.endswith("reservation") else "job-1", "error": None,
              "created_at": "2026-11-08T00:00:00Z"}
    poster_tasks.write(flow, record)
    job = {"status": "done" if case == "finished_job" else "running",
           "poster_task_id": "different-task" if case == "wrong_task" else record["task_id"]}
    runner = SimpleNamespace(get=lambda job_id: None if case == "missing_job" or not job_id else job)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in tmp_path.rglob("*") if path.is_file()}

    def reject_lock(*args):
        pytest.fail("Polling existing tasks must not acquire filesystem locks")

    monkeypatch.setattr("fcntl.flock", reject_lock)
    observed = poster_tasks.list_tasks(flow, runner)[0]
    assert observed["status"] == "interrupted"
    assert "中断" in observed["error"]
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in tmp_path.rglob("*") if path.is_file()} == before
