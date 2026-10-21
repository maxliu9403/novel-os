"""A new browser can recover the exact paid batch without starting it again."""
from dataclasses import replace
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
import shutil
import threading
import time

import pytest
from fastapi import HTTPException

from api import cover_workbench
from api.jobs import JobRunner, ProjectJobBlocked
from core.cover_store import CoverStore
from core.cover_models import CoverBrief, CoverConcept, CoverSet
from tests.test_cover_api import _client, _brief, _concepts
from tests.test_cover_workbench import DeferredRunner
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_workflow import workflow, approve  # noqa: F401


@pytest.fixture
def flow(tmp_path):
    project = tmp_path / "book"
    project.mkdir()
    return SimpleNamespace(store=CoverStore(project), context=SimpleNamespace(owner_scope="default:project:book"))


def make_set(flow, status="ready"):
    brief = CoverBrief.from_dict(_brief(), source_prompt_sha256="a" * 64)
    result = CoverSet.new("book", brief, tuple(CoverConcept.from_dict(item) for item in _concepts()))
    result = replace(result, candidates=tuple(replace(item, status=status,
        relative_path=f"fixture/{item.candidate_id}.png", media_id=item.candidate_id, sha256="b" * 64,
        content_type="image/png", provider="fixture", model="fixture-image", generation_prompt="Fixture only",
        width=1024, height=1536) for item in result.candidates), status="generating" if status == "pending" else status)
    return flow.store.create(result)


def event(cover, phase="generating", **changes):
    return {"phase": phase, "cover_set_id": cover.cover_set_id, "total": 4,
            "completed": sum(item.status != "pending" for item in cover.candidates),
            "failed": sum(item.status == "failed" for item in cover.candidates),
            "ready": sum(item.status in {"ready", "selected"} for item in cover.candidates),
            "current_candidate_id": cover.candidates[0].candidate_id, "current_candidate_index": 1, **changes}


def start(flow, runner, operation, **options):
    from api import cover_generation_tasks as tasks
    return tasks.start(flow, runner, operation=operation, preflight=lambda: None,
                       count=4, direction_id="approved-direction", **options)


@pytest.mark.parametrize("owner", ["project", "workshop"])
def test_new_browser_can_discover_generation_without_the_original_post(tmp_path, monkeypatch, flow, owner):
    client, _ = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(cover_workbench, f"{owner}_workflow", lambda *args: flow)
    base = "/api/projects/book/covers" if owner == "project" else "/api/workshop/sessions/discussion/covers"
    response = client.get(base + "/generation-tasks/latest")
    assert response.status_code == 200, response.text
    assert response.json() is None
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    monkeypatch.setattr(cover_workbench, "runner", runner)
    task = start(flow, runner, lambda: make_set(flow))
    recovered = client.get(base + "/generation-tasks/latest").json()
    assert recovered["status"] == "queued"
    assert recovered["task_id"] == task["task_id"]
    assert recovered["job_id"] == task["job_id"]
    assert client.get(base + "/generation-tasks/" + task["task_id"]).json() == recovered
    next(iter(runner.jobs.values()))["fn"]()
    restored = client.get(base + "/generation-tasks/latest").json()
    assert restored["status"] == "completed" and restored["cover_set_id"]
    assert tasks.as_job(restored, runner)["status"] == "done"


def test_duplicate_owner_start_precedes_preflight_and_preserves_reserved_budget(flow):
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    first = start(flow, runner, lambda: make_set(flow), project_id="book")
    def already_reserved():
        raise AssertionError("Duplicate POST must not recheck or replenish reserved approval")
    second = tasks.start(flow, runner, operation=lambda: None, preflight=already_reserved,
                         count=3, direction_id="another-direction", project_id="book")
    assert first == second
    assert len(runner.jobs) == 1
    job = tasks.as_job(second, runner)
    assert job["job_id"] == first["job_id"] and job["task_id"] == first["task_id"]
    assert job["meta"]["generation_task_id"] == first["task_id"]
    assert job["kind"] == "cover.generate" and job["status"] == "running"
    json.dumps(job)


def test_exact_batch_progress_and_terminal_result_survive_restart(flow, monkeypatch):
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    observed = []
    def generate():
        cover = make_set(flow)
        sink = tasks.current_progress_sink()
        for phase in ("preparing", "generating", "composing", "reviewing", "comparing", "saving"):
            sink(event(cover, phase))
            observed.append(tasks.latest(flow, runner))
        return cover
    task = start(flow, runner, generate)
    next(iter(runner.jobs.values()))["fn"]()
    assert [item["progress"]["phase"] for item in observed] == [
        "preparing", "generating", "composing", "reviewing", "comparing", "saving"]
    assert all(item["status"] == "running" and item["cover_set_id"] for item in observed)
    assert len({item["cover_set_id"] for item in observed}) == 1
    monkeypatch.setattr(tasks, "_PROCESS_ID", "restarted")
    done = tasks.get(flow, task["task_id"], JobRunner())
    assert done["status"] == "completed" and done["result_status"] == "ready"
    assert done["progress"]["completed"] == done["progress"]["ready"] == 4
    assert done["progress"]["failed"] == 0
    assert done["started_at"] and done["finished_at"]
    assert tasks.current_progress_sink() is None


@pytest.mark.parametrize("failure", ["exception", "all", "partial"])
def test_failures_retain_exact_batch_and_honest_counts_without_automatic_retry(flow, failure):
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    calls = []
    def generate():
        calls.append(1)
        cover = make_set(flow, "failed" if failure == "all" else "pending" if failure == "exception" else "ready")
        if failure == "partial":
            cover = replace(cover, status="partial", candidates=(replace(cover.candidates[0], status="failed"), *cover.candidates[1:]))
            flow.store._write_json(flow.store.sets_dir / f"{cover.cover_set_id}.json", cover.to_dict())
        tasks.current_progress_sink()(event(cover, "reviewing"))
        if failure == "exception":
            raise RuntimeError("provider unavailable")
        return cover
    task = start(flow, runner, generate)
    if failure == "exception":
        with pytest.raises(RuntimeError, match="provider unavailable"):
            next(iter(runner.jobs.values()))["fn"]()
    else:
        next(iter(runner.jobs.values()))["fn"]()
    result = tasks.get(flow, task["task_id"], JobRunner())
    assert result["status"] == ("completed" if failure == "partial" else "failed")
    assert result["cover_set_id"] and result["finished_at"]
    assert result["progress"]["phase"] == "reviewing"
    assert result["progress"]["failed"] == {"exception": 0, "all": 4, "partial": 1}[failure]
    if failure == "partial":
        assert result["result_status"] == "partial" and result["progress"]["completed"] == 4
    assert calls == [1] and tasks.current_progress_sink() is None


def test_restart_projects_interruption_without_writing_or_restarting(flow):
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    task = start(flow, runner, lambda: pytest.fail("No automatic paid retry"))
    path = flow.store.root / "generation_tasks" / f"{task['task_id']}.json"
    before = path.read_bytes()
    interrupted = tasks.get(flow, task["task_id"], JobRunner())
    assert interrupted["status"] == "interrupted" and interrupted["error"]
    assert path.read_bytes() == before
    assert len(runner.jobs) == 1


def test_owner_isolation_and_reads_do_not_create_deleted_directories(flow):
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    assert tasks.latest(flow, runner) is None and not flow.store.root.exists()
    task = start(flow, runner, lambda: None)
    other = SimpleNamespace(store=flow.store, context=SimpleNamespace(owner_scope="other:project:book"))
    assert tasks.latest(other, runner) is None
    with pytest.raises(FileNotFoundError):
        tasks.get(other, task["task_id"], runner)
    with pytest.raises(ValueError):
        tasks.get(flow, "../../outside", runner)
    shutil.rmtree(flow.store.project_path)
    assert tasks.latest(flow, runner) is None
    with pytest.raises(FileNotFoundError):
        tasks.get(flow, task["task_id"], runner)
    assert not flow.store.project_path.exists()


def test_progress_cannot_reopen_terminal_work(flow):
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    saved = []
    def generate():
        cover = make_set(flow)
        sink = tasks.current_progress_sink()
        saved.append((sink, cover))
        sink(event(cover))
        return cover
    task = start(flow, runner, generate)
    next(iter(runner.jobs.values()))["fn"]()
    done = tasks.get(flow, task["task_id"], runner)
    saved[0][0](event(saved[0][1], "reviewing"))
    assert tasks.get(flow, task["task_id"], runner) == done
    shutil.rmtree(flow.store.project_path)
    saved[0][0](event(saved[0][1], "reviewing"))
    assert not flow.store.project_path.exists()


def test_wrong_batch_progress_stops_generation_and_retains_first_batch(flow):
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    saved = []
    def generate():
        cover = make_set(flow)
        saved.append(cover.cover_set_id)
        sink = tasks.current_progress_sink()
        sink(event(cover))
        sink(event(make_set(flow)))
        pytest.fail("Another batch cannot continue generation")
    task = start(flow, runner, generate)
    with pytest.raises(tasks.GenerationProgressError, match="batch"):
        next(iter(runner.jobs.values()))["fn"]()
    failed = tasks.get(flow, task["task_id"], JobRunner())
    assert failed["status"] == "failed" and failed["cover_set_id"] == saved[0]


def test_dispatch_and_model_work_do_not_block_reads_or_falsely_interrupt(flow):
    from api import cover_generation_tasks as tasks
    entered, release = threading.Event(), threading.Event()
    class Delayed(DeferredRunner):
        def submit(self, *args, **kwargs):
            result = super().submit(*args, **kwargs)
            entered.set()
            assert release.wait(3)
            return result
    runner = Delayed()
    with ThreadPoolExecutor(max_workers=2) as pool:
        submitted = pool.submit(start, flow, runner, lambda: None)
        try:
            assert entered.wait(2)
            read = pool.submit(tasks.latest, flow, runner)
            with pytest.raises(TimeoutError):
                read.result(timeout=.05)
        finally:
            release.set()
        assert submitted.result()["task_id"] == read.result()["task_id"]
        assert read.result()["status"] == "queued"


def test_worker_preflight_failure_is_durable_and_never_calls_provider(flow):
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    checks = []
    def preflight():
        checks.append(1)
        if len(checks) == 2:
            raise ValueError("approval changed while queued")
    task = tasks.start(flow, runner, count=4, direction_id="direction", preflight=preflight,
                       operation=lambda: pytest.fail("provider must not run"))
    with pytest.raises(ValueError, match="approval changed"):
        next(iter(runner.jobs.values()))["fn"]()
    assert tasks.get(flow, task["task_id"], JobRunner())["status"] == "failed"


def test_project_deletion_fence_rejects_dispatch_without_provider_call(flow):
    from api import cover_generation_tasks as tasks
    runner = JobRunner()
    runner.prepare_project_deletion("book", str(flow.store.project_path))
    with pytest.raises(ProjectJobBlocked):
        start(flow, runner, lambda: pytest.fail("provider must not run"), project_id="book")
    assert tasks.latest(flow, runner)["status"] == "failed"


@pytest.mark.parametrize("loss", ["missing", "status", "process"])
def test_losing_live_task_checkpoint_blocks_next_paid_phase_without_recreation(flow, loss):
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    def generate():
        cover = make_set(flow, "pending")
        sink = tasks.current_progress_sink()
        sink(event(cover))
        path = flow.store.root / "generation_tasks" / f"{task['task_id']}.json"
        if loss == "missing":
            shutil.rmtree(flow.store.project_path)
        else:
            value = json.loads(path.read_text())
            value["status" if loss == "status" else "process_id"] = "interrupted" if loss == "status" else "other"
            path.write_text(json.dumps(value))
        sink(event(cover, "reviewing"))
        pytest.fail("A live task without durable ownership must not continue paid work")
    task = start(flow, runner, generate)
    with pytest.raises(tasks.GenerationProgressError):
        next(iter(runner.jobs.values()))["fn"]()
    if loss == "missing":
        assert not flow.store.project_path.exists()


def test_counts_must_match_durable_candidates_before_next_phase(flow):
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    def generate():
        cover = make_set(flow, "pending")
        tasks.current_progress_sink()(event(cover, completed=4, ready=4))
        pytest.fail("Unpersisted completion must not be published")
    task = start(flow, runner, generate)
    with pytest.raises(tasks.GenerationProgressError, match="count"):
        next(iter(runner.jobs.values()))["fn"]()
    assert tasks.get(flow, task["task_id"], runner)["status"] == "failed"


def test_returned_batch_must_exist_in_owner_store(flow):
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    def generate():
        cover = make_set(flow)
        (flow.store.sets_dir / f"{cover.cover_set_id}.json").unlink()
        return cover
    task = start(flow, runner, generate)
    with pytest.raises(FileNotFoundError):
        next(iter(runner.jobs.values()))["fn"]()
    assert tasks.get(flow, task["task_id"], runner)["status"] == "failed"


def test_bound_generation_refresh_and_double_submit_keep_exact_budget_and_batch(workflow, monkeypatch):
    from api import cover_generation_tasks as tasks
    from api.models import CoverGenerateRequest, Job
    flow, analysis, director, _ = workflow
    direction = approve(flow, flow.plan(analysis.analysis_id, director))
    request = CoverGenerateRequest(direction_id=direction.direction_id,
        approved_direction_sha256=direction.direction_sha256)
    runner = JobRunner()
    monkeypatch.setattr(cover_workbench, "runner", runner)
    entered, release = threading.Event(), threading.Event()
    calls = []
    class ExistingService:
        # No progress parameter: existing service adapters retain their signature.
        def generate(self, project_id, project_path, brief, concepts, **kwargs):
            calls.append(kwargs)
            assert flow.approval(direction.direction_id)["batch_reserved"] is True
            result = flow.store.create(CoverSet.new(project_id, brief, concepts))
            tasks.current_progress_sink()(event(result, "preparing"))
            entered.set()
            assert release.wait(4)
            result = replace(result, status="failed", candidates=tuple(
                replace(item, status="failed", error="fixture unavailable") for item in result.candidates))
            return flow.store.save(result, expected_revision=result.revision)
    service = ExistingService()
    try:
        posted = cover_workbench.generate_bound_covers(flow, request, service, project_id="book")
        assert entered.wait(2)
        envelope = Job(**posted).model_dump()
        assert envelope["task_id"] and envelope["job_id"]
        restored = tasks.latest(flow, runner)
        assert restored["status"] == "running" and restored["cover_set_id"]
        duplicate = cover_workbench.generate_bound_covers(flow, request, service, project_id="book")
        assert duplicate["task_id"] == envelope["task_id"] and duplicate["job_id"] == envelope["job_id"]
        assert len(calls) == 1
        # A long model call holds neither the status nor cover mutation lock.
        with ThreadPoolExecutor(max_workers=1) as pool:
            def acquire_storage():
                with flow.store.mutation_lock():
                    return tasks.latest(flow, runner)["cover_set_id"]
            assert pool.submit(acquire_storage).result(timeout=.5) == restored["cover_set_id"]
    finally:
        release.set()
    for _ in range(100):
        final = tasks.latest(flow, runner)
        if final["status"] == "failed":
            break
        time.sleep(.01)
    assert final["status"] == "failed" and final["progress"]["failed"] == 4
    assert final["cover_set_id"] == restored["cover_set_id"]
    assert flow.approval(direction.direction_id)["batch_reserved"] is True
    with pytest.raises(HTTPException) as refused:
        cover_workbench.generate_bound_covers(flow, request, service, project_id="book")
    assert refused.value.status_code == 409
    assert len(calls) == 1


def test_progress_disk_failure_poison_stops_before_next_call(flow, monkeypatch):
    from api import cover_generation_tasks as tasks
    runner = DeferredRunner()
    original_write = tasks._write
    def broken_write(owner, value):
        if value.get("progress"):
            raise OSError("fixture disk full")
        return original_write(owner, value)
    def generate():
        cover = make_set(flow, "pending")
        sink = tasks.current_progress_sink()
        with pytest.raises(tasks.GenerationProgressError, match="disk full"):
            sink(event(cover, "preparing"))
        # Even a misbehaving adapter cannot swallow durability failure and
        # publish another paid stage or claim successful completion.
        sink(event(cover, "generating"))
        pytest.fail("No next paid call after losing progress persistence")
    task = start(flow, runner, generate)
    monkeypatch.setattr(tasks, "_write", broken_write)
    with pytest.raises(tasks.GenerationProgressError, match="disk full"):
        next(iter(runner.jobs.values()))["fn"]()
    assert tasks.get(flow, task["task_id"], runner)["status"] == "failed"
