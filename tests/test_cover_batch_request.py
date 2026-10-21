"""Manual batch intents remain idempotent after failure, completion and restart."""
from dataclasses import replace

import pytest
from fastapi import HTTPException

from api import cover_generation_tasks as tasks, cover_workbench
from api.models import CoverGenerateRequest
from core.image_client import ImageClientError
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_workflow import workflow, approve  # noqa: F401
from tests.test_cover_workbench import DeferredRunner
from tests.test_cover_generation_tasks import flow, make_set  # noqa: F401
from tests.test_cover_service import _service


@pytest.mark.parametrize("owner", ["project", "workshop"])
def test_explicit_api_retry_after_all_failed_preserves_old_batch_and_replays_once(workflow, tmp_path, monkeypatch, owner):
    flow, analysis, director, _ = workflow
    direction = approve(flow, flow.plan(analysis.analysis_id, director))
    runner = DeferredRunner()
    monkeypatch.setattr(cover_workbench, "runner", runner)
    service, image, _ = _service(tmp_path, [ImageClientError("fixture HTTP 429")] * 12)
    fields = dict(direction_id=direction.direction_id, approved_direction_sha256=direction.direction_sha256)
    owner_args = {"project_id": "book"} if owner == "project" else {"session_id": "discussion"}
    def post(key):
        return cover_workbench.generate_bound_covers(flow, CoverGenerateRequest(**fields, request_id=key), service, **owner_args)
    # Compatibility with an already consumed pre-fix batch is essential.
    first = post("")
    runner.jobs[first["job_id"]]["fn"]()
    first_task = tasks.get(flow, first["task_id"], runner)
    assert first_task["status"] == "failed" and first_task["progress"]["failed"] == 4
    old_path = flow.store.sets_dir / f"{first_task['cover_set_id']}.json"
    old = old_path.read_bytes()
    second = post("a" * 32)
    assert second["task_id"] != first["task_id"]
    assert post("a" * 32)["task_id"] == second["task_id"]
    assert post("b" * 32)["task_id"] == second["task_id"]  # another tab while active
    assert len(runner.jobs) == 2 and len(image.prompts) == 4
    runner.jobs[second["job_id"]]["fn"]()
    second_task = tasks.get(flow, second["task_id"], runner)
    assert second_task["status"] == "failed" and second_task["progress"]["failed"] == 4
    assert len(image.prompts) == 8 and old_path.read_bytes() == old
    for key in ("a" * 32, "b" * 32):
        assert post(key)["task_id"] == second["task_id"]  # delayed retransmission, even terminal
    assert len(runner.jobs) == 2 and len(image.prompts) == 8
    # Source validation is not bypassed by a new intent; replay of an old intent
    # only returns its history and cannot start calls on the changed source.
    source_path = flow.store.project_path / "outputs/input/prompt.md"
    source_path.write_text("Changed source")
    assert post("a" * 32)["task_id"] == second["task_id"]
    with pytest.raises(HTTPException) as stale:
        post("c" * 32)
    assert stale.value.status_code == 409
    assert len(runner.jobs) == 2 and len(image.prompts) == 8
    assert tasks.current_generation_batch_id() == ""


@pytest.mark.parametrize("result_status", ["ready", "failed", "partial", "interrupted"])
def test_request_key_survives_terminal_outcome_and_process_restart(flow, monkeypatch, result_status):
    runner = DeferredRunner()
    calls = []
    def generate():
        calls.append(tasks.current_generation_batch_id())
        cover = make_set(flow, "failed" if result_status == "failed" else "ready")
        if result_status == "partial":
            cover = flow.store.save(replace(cover, status="partial", candidates=(
                replace(cover.candidates[0], status="failed"), *cover.candidates[1:])), expected_revision=cover.revision)
        return cover
    def start(key, **changes):
        options = dict(count=4, direction_id="approved", approved_direction_sha256="a" * 64)
        options.update(changes)
        return tasks.start(flow, runner, operation=generate, preflight=lambda: None, request_id=key, **options)
    first = start("1" * 32)
    if result_status != "interrupted":
        runner.jobs[first["job_id"]]["fn"]()
        assert calls == [first["task_id"]]
    monkeypatch.setattr(tasks, "_PROCESS_ID", "new-process")
    assert start("1" * 32)["task_id"] == first["task_id"]
    assert len(runner.jobs) == 1
    with pytest.raises(ValueError, match="different approved inputs"):
        start("1" * 32, approved_direction_sha256="b" * 64)
    second = start("2" * 32)
    assert second["task_id"] != first["task_id"] and len(runner.jobs) == 2
    assert tasks.current_generation_batch_id() == ""


def test_new_batch_id_preflight_is_scoped_and_dispatch_failure_is_durable(flow):
    runner = DeferredRunner()
    seen = []
    def fail(*args, **kwargs):
        raise RuntimeError("dispatch unavailable")
    runner.submit = fail
    def start():
        return tasks.start(flow, runner, operation=lambda: pytest.fail("No model call"),
            preflight=lambda: seen.append(tasks.current_generation_batch_id()),
            count=4, direction_id="approved", request_id="3" * 32)
    with pytest.raises(RuntimeError, match="dispatch unavailable"):
        start()
    saved = tasks.latest(flow, runner)
    assert saved["status"] == "failed" and saved["task_id"] == seen[0]
    assert start()["task_id"] == saved["task_id"] and len(seen) == 1
    assert tasks.current_generation_batch_id() == ""
