from __future__ import annotations

import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from api import cover_workbench, cover_direction_tasks
from api.jobs import JobRunner, ProjectJobBlocked
from api.models import CoverDirectionCreate
from core.cover_store import CoverStore
from core.cover_workflow import CoverWorkflow
from tests.test_cover_api import _client
from tests.test_cover_analysis_snapshot import source
from tests.test_cover_workflow import workflow
from tests.test_cover_workbench import DeferredRunner


def test_running_direction_is_discoverable_by_new_page_without_original_response(
        tmp_path, monkeypatch, workflow):
    flow, analysis, director, _ = workflow
    entered, release = threading.Event(), threading.Event()
    original_plan = director.plan
    calls = []

    def blocked_plan(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5), "test must release model double"
        return original_plan(*args, **kwargs)

    director.plan = blocked_plan
    monkeypatch.setattr(cover_workbench, "project_workflow", lambda svc, project_id: flow)
    client, _ = _client(tmp_path, monkeypatch, director=director)
    base = "/api/projects/book/covers"
    try:
        started = client.post(base + "/direction-tasks", json={"analysis_id": analysis.analysis_id})
        assert started.status_code == 202, started.text
        assert entered.wait(2)
        # A new page has lost all component state, including the POST response.
        recovered = client.get(base + "/direction-tasks/latest")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["status"] == "running"
        assert recovered.json()["task_id"] == started.json()["task_id"]
        duplicate = client.post(base + "/direction-tasks", json={"analysis_id": analysis.analysis_id})
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json()["task_id"] == recovered.json()["task_id"]
        assert len(calls) == 1
    finally:
        release.set()
    for _ in range(100):
        done = client.get(base + "/direction-tasks/latest").json()
        if done["status"] != "running":
            break
        time.sleep(.01)
    assert done["status"] == "completed", done
    assert done["direction_id"] == flow.store.list_directions()[-1].direction_id
    assert client.get(base + f"/direction-tasks/{done['task_id']}").json() == done


@pytest.mark.parametrize("route_kind", ["project", "workshop"])
def test_task_failure_survives_runner_restart_and_does_not_automatically_retry(
        tmp_path, monkeypatch, workflow, route_kind):
    flow, analysis, director, _ = workflow
    deferred = DeferredRunner()
    monkeypatch.setattr(cover_workbench, "runner", deferred)
    monkeypatch.setattr(cover_workbench, f"{route_kind}_workflow", lambda svc, owner_id: flow)
    client, _ = _client(tmp_path, monkeypatch, director=director)
    base = ("/api/projects/book/covers" if route_kind == "project"
            else "/api/workshop/sessions/discussion/covers")
    assert client.get(base + "/direction-tasks/latest").json() is None
    called = []

    def fail(*args, **kwargs):
        called.append(1)
        raise RuntimeError("fixture provider unavailable")

    director.plan = fail
    started = client.post(base + "/direction-tasks", json={"analysis_id": analysis.analysis_id})
    assert started.status_code == 202, started.text
    job = next(iter(deferred.jobs.values()))
    assert job["direction_task_id"] == started.json()["task_id"]
    if route_kind == "project":
        assert job["project_path"] == str(flow.store.project_path)
    else:
        assert job["session_id"] == "discussion"
    with pytest.raises(RuntimeError, match="fixture provider unavailable"):
        job["fn"]()
    monkeypatch.setattr(cover_workbench, "runner", JobRunner())
    restored = client.get(base + "/direction-tasks/latest").json()
    assert restored["status"] == "failed"
    assert restored["error"] == "fixture provider unavailable"
    assert restored["finished_at"]
    assert len(called) == 1
    assert flow.store.list_directions() == []


def test_lost_worker_projects_interruption_and_explicit_retry_allocates_new_task(workflow, monkeypatch):
    flow, analysis, _, _ = workflow
    deferred = DeferredRunner()
    body = CoverDirectionCreate(analysis_id=analysis.analysis_id)
    calls = []
    started = cover_direction_tasks.start(flow, deferred, body, lambda: calls.append(1), preflight=lambda: None)
    # Equivalent to an app process restart: durable files remain; jobs do not.
    restarted = DeferredRunner()
    recovered = cover_direction_tasks.latest(flow, restarted)
    assert recovered["status"] == "interrupted"
    assert recovered["task_id"] == started["task_id"]
    assert calls == []
    retry = cover_direction_tasks.start(flow, restarted, body, lambda: calls.append(1), preflight=lambda: None)
    assert retry["task_id"] != started["task_id"]
    assert cover_direction_tasks.get(flow, started["task_id"], restarted)["status"] == "interrupted"
    assert len(restarted.jobs) == 1 and calls == []


def test_current_task_coalesces_different_settings_and_survives_concurrent_start(workflow):
    from concurrent.futures import ThreadPoolExecutor
    flow, analysis, _, _ = workflow
    deferred = DeferredRunner()
    calls = []

    def start(count):
        return cover_direction_tasks.start(flow, deferred, CoverDirectionCreate(
            analysis_id=analysis.analysis_id, count=count), lambda: None,
            preflight=lambda: calls.append(1))

    with ThreadPoolExecutor(max_workers=8) as executor:
        records = list(executor.map(start, [3, 4, 5, 3, 4, 5, 3, 4]))
    assert len({record["task_id"] for record in records}) == 1
    assert len(deferred.jobs) == 1
    assert len(calls) == 1


def test_task_identity_and_owner_are_checked_without_leaking_other_owner(workflow):
    flow, analysis, _, project = workflow
    deferred = DeferredRunner()
    started = cover_direction_tasks.start(flow, deferred, CoverDirectionCreate(
        analysis_id=analysis.analysis_id), lambda: None, preflight=lambda: None)
    other = CoverWorkflow(replace(flow.context, owner_scope="another:project:book"), CoverStore(project))
    assert cover_direction_tasks.latest(other, deferred) is None
    with pytest.raises(FileNotFoundError, match="owner"):
        cover_direction_tasks.get(other, started["task_id"], deferred)
    with pytest.raises(ValueError, match="task id"):
        cover_direction_tasks.get(flow, "../../state", deferred)


def test_worker_rechecks_source_before_model_and_posting_stale_source_does_not_allocate_task(
        tmp_path, monkeypatch, workflow):
    flow, analysis, director, project = workflow
    deferred = DeferredRunner()
    monkeypatch.setattr(cover_workbench, "runner", deferred)
    monkeypatch.setattr(cover_workbench, "project_workflow", lambda svc, project_id: flow)
    client, _ = _client(tmp_path, monkeypatch, director=director)
    base = "/api/projects/book/covers"
    started = client.post(base + "/direction-tasks", json={"analysis_id": analysis.analysis_id})
    assert started.status_code == 202, started.text
    (project / "outputs/input/prompt.md").write_text("Changed skeleton")
    calls = []
    director.plan = lambda *args, **kwargs: calls.append(1)
    with pytest.raises(ValueError, match="head changed"):
        next(iter(deferred.jobs.values()))["fn"]()
    assert calls == []
    assert client.get(base + "/direction-tasks/latest").json()["status"] == "failed"
    refused = client.post(base + "/direction-tasks", json={"analysis_id": analysis.analysis_id})
    assert refused.status_code == 409, refused.text
    assert len(deferred.jobs) == 1


def test_source_change_during_plan_marks_saved_direction_stale_and_task_failed(
        tmp_path, monkeypatch, workflow):
    flow, analysis, director, project = workflow
    deferred = DeferredRunner()
    original_plan = director.plan

    def change_source(*args, **kwargs):
        result = original_plan(*args, **kwargs)
        (project / "outputs/input/prompt.md").write_text("Changed while planning")
        return result

    director.plan = change_source
    monkeypatch.setattr(cover_workbench, "runner", deferred)
    started = cover_workbench._start_direction_task(flow, CoverDirectionCreate(
        analysis_id=analysis.analysis_id), director, None, project_id="book")
    with pytest.raises(ValueError, match="骨架已变化"):
        next(iter(deferred.jobs.values()))["fn"]()
    assert cover_direction_tasks.get(flow, started["task_id"], deferred)["status"] == "failed"
    assert flow.store.list_directions()[0].status == "stale"


def test_dispatch_failure_is_durable_and_project_deletion_fence_is_preserved(workflow):
    flow, analysis, _, _ = workflow
    runner = JobRunner()
    runner.prepare_project_deletion("book", str(flow.store.project_path))
    with pytest.raises(ProjectJobBlocked):
        cover_direction_tasks.start(flow, runner, CoverDirectionCreate(analysis_id=analysis.analysis_id),
            lambda: None, preflight=lambda: None, project_id="book")
    assert cover_direction_tasks.latest(flow, runner)["status"] == "failed"


def test_legacy_direction_creation_uses_durable_task_without_changing_sync_contract(
        tmp_path, monkeypatch):
    from tests.test_cover_director import adaptive_director_fixture
    from core.cover_director import CoverArtDirector
    from api import routes

    director = CoverArtDirector.from_fixture(adaptive_director_fixture())
    client, _ = _client(tmp_path, monkeypatch, director=director)
    project_id = client.post("/api/projects", json={"title": "Legacy", "genre": "Drama"}).json()["id"]
    # Use the existing tested synchronous function as the execution adapter;
    # intercept its dependencies rather than starting a real model request.
    base = f"/api/projects/{project_id}/covers"
    deferred = DeferredRunner()
    monkeypatch.setattr(cover_workbench, "runner", deferred)
    monkeypatch.setattr("core.cover_handoff.resolve_cover_brief_v2", lambda _: SimpleNamespace(
        pending_critical_assumptions=lambda: []))
    called = []
    monkeypatch.setattr(routes, "create_cover_direction", lambda project_id, body, svc, director: (
        called.append((project_id, body.analysis_id)) or {"direction_id": "saved-legacy-direction"}))
    started = client.post(base + "/direction-tasks", json={"count": 4})
    assert started.status_code == 202, started.text
    assert called == []
    next(iter(deferred.jobs.values()))["fn"]()
    restored = client.get(base + "/direction-tasks/latest").json()
    assert restored["status"] == "completed"
    assert restored["direction_id"] == "saved-legacy-direction"
    assert called == [(project_id, "")]


def test_legacy_missing_facts_still_returns_preflight_conflict(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    project_id = client.post("/api/projects", json={"title": "Legacy", "genre": "Drama"}).json()["id"]
    monkeypatch.setattr("core.cover_handoff.resolve_cover_brief_v2", lambda _: SimpleNamespace(
        pending_critical_assumptions=lambda: [SimpleNamespace(field="characters.age")]))
    client.app.dependency_overrides[cover_workbench.get_cover_art_director] = lambda: SimpleNamespace()
    base = f"/api/projects/{project_id}/covers"
    response = client.post(base + "/direction-tasks", json={})
    assert response.status_code == 409, response.text
    assert "characters.age" in response.text
    assert client.get(base + "/direction-tasks/latest").json() is None


@pytest.mark.parametrize("owner_kind", ["project", "workshop"])
def test_background_direction_retains_cover_usage_owner_and_completed_result_after_restart(
        workflow, monkeypatch, owner_kind):
    from core.cover_analysis import CoverStoryAnalyzer
    from core.cover_sources import CoverSourceSelector, capture_source
    from core.usage_tracking import read_records, record_usage
    flow, analysis, director, project = workflow
    if owner_kind == "workshop":
        owner = project.parent / "discussion"
        store = CoverStore(owner, cover_root=owner / "covers")
        context = replace(flow.context, cover_root=store.root, owner_scope="default:workshop:discussion")
        flow = CoverWorkflow(context, store)
        snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
        analysis = CoverStoryAnalyzer(complete=director._complete, model=director.model,
                                      max_repair_attempts=0).analyze(context, snapshot)
    else:
        owner = project
    original_plan = director.plan

    def plan_with_usage(*args, **kwargs):
        record_usage(provider="fixture", model="fixture-direction", request_id="direction-test",
                     usage={"input_tokens": 100, "output_tokens": 20})
        return original_plan(*args, **kwargs)

    director.plan = plan_with_usage
    deferred = DeferredRunner()
    monkeypatch.setattr(cover_workbench, "runner", deferred)
    task = cover_workbench._start_direction_task(flow, CoverDirectionCreate(
        analysis_id=analysis.analysis_id), director, None,
        project_id="book" if owner_kind == "project" else "",
        session_id="discussion" if owner_kind == "workshop" else "")
    next(iter(deferred.jobs.values()))["fn"]()
    records = read_records(owner)
    recorded = next(item for item in records.values() if item["request_id"] == "direction-test")
    assert recorded["stage"] == "cover"
    assert recorded["usage"]["total_tokens"] == 120
    monkeypatch.setattr(cover_direction_tasks, "_PROCESS_ID", "new-process")
    restored = cover_direction_tasks.get(flow, task["task_id"], JobRunner())
    assert restored["status"] == "completed"
    assert flow.store.load_direction(restored["direction_id"]).status == "awaiting_approval"


def test_symlinked_task_directory_is_rejected_and_invalid_records_are_skipped(workflow, tmp_path):
    flow, _, _, _ = workflow
    root = flow.store.root / "direction_tasks"
    root.mkdir()
    (root / ("direction-task-" + "a" * 32 + ".json")).write_text("[]")
    assert cover_direction_tasks.latest(flow, JobRunner()) is None
    (root / ("direction-task-" + "a" * 32 + ".json")).unlink()
    root.rmdir()
    root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="directory"):
        cover_direction_tasks.latest(flow, JobRunner())


def test_reads_never_create_or_recreate_deleted_project_directories(tmp_path):
    import shutil
    flow = CoverWorkflow(SimpleNamespace(owner_scope="default:project:gone"), CoverStore(tmp_path / "gone"))
    runner = JobRunner()
    assert cover_direction_tasks.latest(flow, runner) is None
    assert not flow.store.project_path.exists()
    body = CoverDirectionCreate()
    deferred = DeferredRunner()
    started = cover_direction_tasks.start(flow, deferred, body, lambda: None, preflight=lambda: None)
    shutil.rmtree(flow.store.project_path)
    assert cover_direction_tasks.latest(flow, runner) is None
    with pytest.raises(FileNotFoundError):
        cover_direction_tasks.get(flow, started["task_id"], runner)
    assert not flow.store.project_path.exists()


def test_recovery_during_dispatch_waits_for_job_identity_without_false_interruption(workflow):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    flow, _, _, _ = workflow
    entered, release = threading.Event(), threading.Event()

    class SlowDispatchRunner(DeferredRunner):
        def submit(self, *args, **kwargs):
            job_id = super().submit(*args, **kwargs)
            entered.set()
            assert release.wait(3)
            return job_id

    runner = SlowDispatchRunner()
    with ThreadPoolExecutor(max_workers=2) as pool:
        submit = pool.submit(cover_direction_tasks.start, flow, runner, CoverDirectionCreate(),
                             lambda: None, preflight=lambda: None)
        try:
            assert entered.wait(2)
            read = pool.submit(cover_direction_tasks.latest, flow, runner)
            with pytest.raises(TimeoutError):
                read.result(timeout=.1)
        finally:
            release.set()
        started, recovered = submit.result(), read.result()
    assert recovered["status"] == "queued"
    assert recovered["task_id"] == started["task_id"]


def test_read_racing_completion_never_projects_a_false_interruption(workflow, monkeypatch):
    flow, _, _, _ = workflow
    runner = JobRunner()
    entered, release, model_returned = threading.Event(), threading.Event(), threading.Event()

    def operation():
        entered.set()
        assert release.wait(3)
        model_returned.set()
        return {"direction_id": "saved-result"}

    started = cover_direction_tasks.start(flow, runner, CoverDirectionCreate(), operation, preflight=lambda: None)
    assert entered.wait(2)
    original_get = runner.get

    def read_at_completion(job_id):
        release.set()
        assert model_returned.wait(2)
        # Hold this projection open while the worker tries to publish its
        # terminal checkpoint. Without the shared read/checkpoint lock, the
        # old running file + newly done runner used to look interrupted.
        deadline = time.monotonic() + .1
        while original_get(job_id)["status"] == "running" and time.monotonic() < deadline:
            time.sleep(.001)
        return original_get(job_id)

    monkeypatch.setattr(runner, "get", read_at_completion)
    try:
        observed = cover_direction_tasks.get(flow, started["task_id"], runner)
        assert observed["status"] in {"running", "completed"}, observed
    finally:
        release.set()
        monkeypatch.setattr(runner, "get", original_get)
    for _ in range(100):
        done = cover_direction_tasks.get(flow, started["task_id"], runner)
        if done["status"] == "completed":
            break
        time.sleep(.01)
    assert done["status"] == "completed"


@pytest.mark.parametrize("owner_kind", ["project", "workshop"])
def test_exact_task_progress_is_durable_and_director_completion_is_not_task_completion(
        workflow, monkeypatch, owner_kind):
    import json
    flow, analysis, legacy, _ = workflow
    deferred = DeferredRunner()
    monkeypatch.setattr(cover_workbench, "runner", deferred)
    observed = []

    class Director:
        model = "fixture-progress"

        def plan(self, brief, *, count, evidence_ledger, story_analysis, diagnostic_sink):
            for stage, attempt in [("draft_started", 1), ("draft_response", 1),
                                   ("draft_validation", 1), ("semantic_started", 1),
                                   ("semantic_response", 1), ("semantic_validation", 1),
                                   ("draft_started", 2), ("draft_response", 2), ("completed", 2)]:
                event = {"schema_version": 1, "stage": stage, "attempt": attempt}
                if stage.endswith("_response"):
                    event["raw_response"] = "private draft content"
                if stage.endswith("_validation"):
                    event["findings"] = [{"evidence": "private source evidence"}]
                diagnostic_sink(event)
                current = cover_direction_tasks.get(flow, task["task_id"], deferred)
                # Progress is emitted only after the owner-private record is durable.
                records = list(flow.store.root.glob("direction_attempts/*/[0-9]*.json"))
                assert len(records) == len(observed) + 1
                observed.append(current)
            return legacy.plan(brief, count=count, evidence_ledger=evidence_ledger,
                               story_analysis=story_analysis)

    task = cover_workbench._start_direction_task(flow, CoverDirectionCreate(
        analysis_id=analysis.analysis_id), Director(), None,
        project_id="book" if owner_kind == "project" else "",
        session_id="discussion" if owner_kind == "workshop" else "")
    assert "progress" not in task
    next(iter(deferred.jobs.values()))["fn"]()
    assert [row["progress"]["completed_model_calls"] for row in observed] == [0, 1, 1, 1, 2, 2, 2, 3, 3]
    assert observed[-1]["status"] == "running"
    assert observed[-1]["direction_id"] is None
    assert observed[-1]["progress"]["phase"] == "completed"
    assert observed[-1]["progress"]["attempt"] == 2
    restored = cover_direction_tasks.get(flow, task["task_id"], JobRunner())
    assert restored["status"] == "completed" and restored["direction_id"]
    assert restored["progress"] == observed[-1]["progress"]
    assert set(restored["progress"]) == {"phase", "attempt", "phase_started_at", "last_event_at", "completed_model_calls"}
    assert "private" not in json.dumps(restored)
    assert "diagnostic_attempt_id" not in json.dumps(restored)


def test_progress_callbacks_never_cross_owners_or_update_later_tasks(workflow):
    import shutil
    flow, analysis, _, project = workflow
    other_root = project.parent / "another-book"
    other_store = CoverStore(other_root)
    other = CoverWorkflow(replace(flow.context, owner_scope="default:project:other",
                                  cover_root=other_store.root), other_store)
    runner = DeferredRunner()
    callbacks = []

    def operation():
        callback = cover_direction_tasks.current_progress_sink()
        callbacks.append(callback)
        callback({"phase": "draft_started", "attempt": 1, "diagnostic_attempt_id": "a" * 32})
        callback({"phase": "draft_response", "attempt": 1, "diagnostic_attempt_id": "a" * 32})
        callback({"phase": "draft_response", "attempt": 1, "diagnostic_attempt_id": "a" * 32})
        return {"direction_id": "saved-fixture"}

    one = cover_direction_tasks.start(flow, runner, CoverDirectionCreate(analysis_id=analysis.analysis_id),
                                     operation, preflight=lambda: None)
    two = cover_direction_tasks.start(other, runner, CoverDirectionCreate(analysis_id=analysis.analysis_id),
                                     lambda: {"direction_id": "other-fixture"}, preflight=lambda: None)
    jobs = list(runner.jobs.values())
    jobs[0]["fn"]()
    first = cover_direction_tasks.get(flow, one["task_id"], runner)
    assert first["progress"]["completed_model_calls"] == 1
    assert "progress" not in cover_direction_tasks.get(other, two["task_id"], runner)
    assert cover_direction_tasks.current_progress_sink() is None
    three = cover_direction_tasks.start(flow, runner, CoverDirectionCreate(),
                                       lambda: None, preflight=lambda: None)
    callbacks[0]({"phase": "semantic_started", "attempt": 2, "diagnostic_attempt_id": "a" * 32})
    assert cover_direction_tasks.get(flow, one["task_id"], runner) == first
    assert "progress" not in cover_direction_tasks.get(flow, three["task_id"], runner)
    interrupted = cover_direction_tasks.get(flow, three["task_id"], JobRunner())
    assert interrupted["status"] == "interrupted"
    shutil.rmtree(project)
    callbacks[0]({"phase": "semantic_started", "attempt": 2, "diagnostic_attempt_id": "a" * 32})
    assert not project.exists()


def test_failed_progress_survives_restart_and_context_is_released(workflow):
    flow, _, _, _ = workflow
    runner = DeferredRunner()

    def fail():
        cover_direction_tasks.current_progress_sink()({
            "phase": "semantic_started", "attempt": 2, "diagnostic_attempt_id": "b" * 32})
        interrupted = cover_direction_tasks.get(flow, task["task_id"], JobRunner())
        assert interrupted["status"] == "interrupted"
        assert interrupted["progress"]["phase"] == "semantic_started"
        cover_direction_tasks.current_progress_sink()({
            "phase": "failure", "attempt": 2, "diagnostic_attempt_id": "b" * 32})
        raise RuntimeError("fixture unavailable")

    task = cover_direction_tasks.start(flow, runner, CoverDirectionCreate(), fail, preflight=lambda: None)
    with pytest.raises(RuntimeError, match="fixture unavailable"):
        next(iter(runner.jobs.values()))["fn"]()
    restored = cover_direction_tasks.get(flow, task["task_id"], JobRunner())
    assert restored["status"] == "failed"
    assert restored["progress"]["phase"] == "semantic_started"
    assert restored["progress"]["completed_model_calls"] == 0
    assert cover_direction_tasks.current_progress_sink() is None
