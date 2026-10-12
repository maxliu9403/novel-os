"""Explicit retries preserve failed history, frozen inputs and consumed budgets."""
from copy import deepcopy
from dataclasses import replace

import pytest

from api import cover_repair_tasks as tasks
from core.cover_store import CoverConflict
from core.image_client import ImageClientError
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_workflow import workflow  # noqa: F401
from tests.test_cover_repair_tasks import repair, finish  # noqa: F401
from tests.test_cover_refinement import refinement, _proposal, _approve, _editing_service, PROMPT  # noqa: F401
from tests.test_cover_workbench import DeferredRunner


def failed_edit(refinement, tmp_path):
    flow, _, _, _ = refinement
    runner = DeferredRunner()
    task = _proposal(refinement, runner)
    service, images = _editing_service(tmp_path, outcome=ImageClientError("reference image sandbox failed"))
    _approve(flow, runner, service, task)
    finish(runner)
    failed = tasks.get(flow, task["task_id"], runner)
    assert failed["status"] == "failed" and len(images.calls) == 1
    return runner, failed


def test_retry_prepares_frozen_proposal_without_replanning_refund_or_new_image(refinement, tmp_path, monkeypatch):
    flow, parent, director, _ = refinement
    runner, failed = failed_edit(refinement, tmp_path)
    old_task = tasks._path(flow, failed["task_id"]).read_bytes()
    old_approval = flow._approval_path(failed["direction_id"]).read_bytes()
    old_direction = flow.store.load_direction(failed["direction_id"])
    monkeypatch.setattr(director, "plan", lambda *a, **k: pytest.fail("retry must not plan"))
    retried = tasks.retry(flow, runner, failed["task_id"], project_id="book")
    assert retried["status"] == "awaiting_approval" and retried["proposal_reused"]
    assert retried["retry_of_task_id"] == failed["task_id"]
    assert retried["task_id"] != failed["task_id"] and retried["direction_id"] != failed["direction_id"]
    assert retried["image_calls"] == 1 and retried["parent_cover_set_id"] == parent.cover_set_id
    assert retried["parent_candidate_id"] == parent.candidates[0].candidate_id
    assert retried["reference_image"] == failed["reference_image"] and retried["refinement_prompt"] == PROMPT
    assert retried["cover_set_id"] is None and retried["result_candidate_id"] is None
    copied = flow.store.load_direction(retried["direction_id"])
    assert copied.plans == old_direction.plans and copied.compiled_recipes == old_direction.compiled_recipes
    assert copied.evidence_ledger == old_direction.evidence_ledger
    assert flow.approval(retried["direction_id"]) == {}
    assert flow._approval_path(failed["direction_id"]).read_bytes() == old_approval
    assert tasks._path(flow, failed["task_id"]).read_bytes() == old_task
    assert len(runner.jobs) == 2
    assert tasks.retry(flow, runner, failed["task_id"], project_id="book")["task_id"] == retried["task_id"]
    assert len(flow.store.list_directions()) == 3


def test_retry_requires_new_approval_and_uses_original_reference_once(refinement, tmp_path):
    flow, parent, _, project = refinement
    runner, failed = failed_edit(refinement, tmp_path)
    retried = tasks.retry(flow, runner, failed["task_id"], project_id="book")
    service, images = _editing_service(tmp_path)
    assert not images.calls
    _approve(flow, runner, service, retried)
    _approve(flow, runner, service, retried)
    finish(runner)
    done = tasks.get(flow, retried["task_id"], runner)
    assert done["status"] == "completed" and len(images.calls) == 1
    assert images.calls[0]["data"] == (project / failed["reference_image"]["relative_path"]).read_bytes()
    assert tasks.retry(flow, runner, failed["task_id"], project_id="book")["task_id"] == done["task_id"]
    assert len(runner.jobs) == 3
    assert flow.store.load(parent.cover_set_id) == parent


def test_exact_failed_child_resolves_its_task_even_after_later_task(refinement, tmp_path):
    flow, parent, _, _ = refinement
    runner, failed = failed_edit(refinement, tmp_path)
    tasks.retry(flow, runner, failed["task_id"], project_id="book")
    resolved = tasks.for_candidate(flow, runner, failed["cover_set_id"], failed["result_candidate_id"])
    assert resolved["task_id"] == failed["task_id"]
    assert resolved["parent_cover_set_id"] == parent.cover_set_id
    assert tasks.for_candidate(flow, runner, failed["cover_set_id"], parent.candidates[0].candidate_id) is None


def test_retry_after_a_second_failure_requires_the_successor_task_id(refinement, tmp_path):
    flow, _, _, _ = refinement
    runner, failed = failed_edit(refinement, tmp_path)
    retry = tasks.retry(flow, runner, failed["task_id"], project_id="book")
    service, images = _editing_service(tmp_path, outcome=ImageClientError("still unavailable"))
    _approve(flow, runner, service, retry)
    finish(runner)
    assert tasks.get(flow, retry["task_id"], runner)["status"] == "failed"
    repeated = tasks.retry(flow, runner, failed["task_id"], project_id="book")
    assert repeated["task_id"] == retry["task_id"] and repeated["status"] == "failed"
    next_retry = tasks.retry(flow, runner, retry["task_id"], project_id="book")
    assert next_retry["task_id"] != retry["task_id"] and next_retry["retry_of_task_id"] == retry["task_id"]
    assert next_retry["reference_image"] == failed["reference_image"] and len(images.calls) == 1


def test_interrupted_generation_can_prepare_a_new_approval_without_replaying_old_job(refinement, tmp_path):
    flow, _, _, _ = refinement
    runner = DeferredRunner()
    task = _proposal(refinement, runner)
    service, images = _editing_service(tmp_path)
    _approve(flow, runner, service, task)
    restarted = DeferredRunner()
    retried = tasks.retry(flow, restarted, task["task_id"], project_id="book")
    assert retried["status"] == "awaiting_approval" and not restarted.jobs and not images.calls
    assert tasks.get(flow, task["task_id"], restarted)["status"] == "interrupted"


@pytest.mark.parametrize("stage", ["task_write", "brief_write", "direction_create"])
def test_partial_retry_write_can_be_recovered_without_new_ids_or_budget(refinement, tmp_path, monkeypatch, stage):
    flow, _, _, _ = refinement
    runner, failed = failed_edit(refinement, tmp_path)
    write, create = flow.store._write_json, flow.store.create_direction
    broken = {"once": True}
    def write_once(path, value):
        target = ((stage == "task_write" and path.parent.name == "repair_tasks")
                  or (stage == "brief_write" and path.name.endswith(".brief.json")))
        if broken["once"] and target:
            broken["once"] = False
            raise OSError("simulated retry storage failure")
        return write(path, value)
    def create_once(*args, **kwargs):
        if broken["once"] and stage == "direction_create":
            broken["once"] = False
            raise OSError("simulated retry storage failure")
        return create(*args, **kwargs)
    monkeypatch.setattr(flow.store, "_write_json", write_once)
    monkeypatch.setattr(flow.store, "create_direction", create_once)
    original = tasks._path(flow, failed["task_id"]).read_bytes()
    with pytest.raises(OSError, match="storage failure"):
        tasks.retry(flow, runner, failed["task_id"], project_id="book")
    recovered = tasks.retry(flow, runner, failed["task_id"], project_id="book")
    assert recovered["status"] == "awaiting_approval" and len(runner.jobs) == 2
    assert len(flow.store.list_directions()) == 3 and flow.approval(recovered["direction_id"]) == {}
    assert tasks._path(flow, failed["task_id"]).read_bytes() == original


@pytest.mark.parametrize("change", ["latest", "parent", "missing_proposal"])
def test_stale_or_unfinished_plan_returns_explicit_replan_context_without_model(refinement, tmp_path, change):
    flow, parent, _, _ = refinement
    runner, failed = failed_edit(refinement, tmp_path)
    if change == "latest":
        direction = flow.store.load_direction(failed["direction_id"])
        flow.store.create_direction(replace(direction, direction_id=""), brief=flow.store.load_direction_brief(direction.direction_id))
    elif change == "parent":
        flow.store.save(replace(parent, status="failed"), expected_revision=parent.revision)
    else:
        task = tasks._read(flow, failed["task_id"])
        task.update(proposal=None, proposal_sha256=None, direction_id=None)
        tasks._write(flow, task)
    before = len(flow.store.list_directions())
    with pytest.raises(tasks.RefinementRetryConflict) as caught:
        tasks.retry(flow, runner, failed["task_id"], project_id="book")
    detail = caught.value.detail
    assert detail["code"] == "cover_refinement_replan_required"
    assert detail["replan_context"]["parent_cover_set_id"] == parent.cover_set_id
    assert detail["replan_context"]["refinement_prompt"] == PROMPT
    assert len(flow.store.list_directions()) == before and len(runner.jobs) == 2


@pytest.mark.parametrize("change", ["reference", "proposal", "quality", "owner", "busy"])
def test_retry_rejects_changed_inputs_owner_or_concurrent_work(refinement, tmp_path, change):
    flow, parent, _, project = refinement
    runner, failed = failed_edit(refinement, tmp_path)
    if change == "reference":
        (project / failed["reference_image"]["relative_path"]).write_bytes(b"changed")
    elif change in {"proposal", "quality", "owner"}:
        task = tasks._read(flow, failed["task_id"])
        if change == "proposal":
            task["proposal"]["plan"]["frozen_action"] = "tampered"
        elif change == "quality":
            task["quality_report"]["extra"] = "tampered"
        else:
            task["owner_scope"] = "other-owner"
        tasks._write(flow, task)
    else:
        tasks.start(flow, runner, refinement[2], cover_set_id=parent.cover_set_id,
            candidate_id=parent.candidates[1].candidate_id, expected_revision=parent.revision,
            project_id="book", refinement_prompt=PROMPT)
    with pytest.raises((CoverConflict, FileNotFoundError)):
        tasks.retry(flow, runner, failed["task_id"], project_id="book")
    assert len(flow.store.list_directions()) == 2


def test_existing_result_gets_a_view_result_path_instead_of_another_image_allowance(refinement, tmp_path):
    flow, _, _, _ = refinement
    runner = DeferredRunner()
    task = _proposal(refinement, runner)
    service, images = _editing_service(tmp_path)
    _approve(flow, runner, service, task)
    finish(runner)
    stored = tasks._read(flow, task["task_id"])
    stored.update(status="failed", error="late save/review failure")
    tasks._write(flow, stored)
    with pytest.raises(tasks.RefinementRetryConflict) as caught:
        tasks.retry(flow, runner, task["task_id"], project_id="book")
    assert caught.value.detail == {"code": "cover_refinement_result_available",
        "message": "已保存精修图片，请查看结果并重新评估，无需再次出图。",
        "result_context": {"cover_set_id": stored["cover_set_id"], "candidate_id": stored["result_candidate_id"]}}
    assert len(images.calls) == 1 and len(flow.store.list_directions()) == 2


def test_retained_artwork_after_composition_failure_does_not_allocate_another_edit(refinement, tmp_path):
    flow, _, _, _ = refinement
    runner, failed = failed_edit(refinement, tmp_path)
    result = flow.store.load(failed["cover_set_id"])
    target = next(x for x in result.candidates if x.candidate_id == failed["result_candidate_id"])
    report = {"assets": {"artwork_sha256": "c" * 64, "artwork_relative_path": "outputs/covers/artwork/retained.jpg"}}
    flow.store.save(replace(result, candidates=tuple(replace(x, quality_report=report) if x == target else x
        for x in result.candidates)), expected_revision=result.revision)
    with pytest.raises(tasks.RefinementRetryConflict) as caught:
        tasks.retry(flow, runner, failed["task_id"], project_id="book")
    assert caught.value.detail["code"] == "cover_refinement_result_available"
    assert "原画" in caught.value.detail["message"]
    assert len(flow.store.list_directions()) == 2


def test_revoked_approval_cannot_be_reused_when_task_lost_approval_timestamp(refinement, tmp_path):
    flow, _, _, _ = refinement
    runner, failed = failed_edit(refinement, tmp_path)
    stored = tasks._read(flow, failed["task_id"])
    stored.pop("approved_at")
    tasks._write(flow, stored)
    approval = flow.approval(stored["direction_id"])
    flow.store._write_json(flow._approval_path(stored["direction_id"]), {**approval, "revoked": True})
    with pytest.raises(tasks.RefinementRetryConflict) as caught:
        tasks.retry(flow, runner, failed["task_id"], project_id="book")
    assert caught.value.detail["code"] == "cover_refinement_replan_required"
    assert len(flow.store.list_directions()) == 2 and len(runner.jobs) == 2


def test_readonly_candidate_lookup_never_validates_or_rewrites_anything(refinement, tmp_path, monkeypatch):
    flow, _, _, _ = refinement
    runner, failed = failed_edit(refinement, tmp_path)
    paths = {path: path.read_bytes() for path in flow.store.root.rglob("*.json")}
    monkeypatch.setattr(flow, "validate", lambda *args, **kwargs: pytest.fail("GET must not mark stale"))
    assert tasks.for_candidate(flow, DeferredRunner(), failed["cover_set_id"], failed["result_candidate_id"])["task_id"] == failed["task_id"]
    assert all(path.read_bytes() == value for path, value in paths.items())


def test_retained_failed_slot_resolves_via_exact_parent_lineage(refinement, tmp_path):
    flow, _, director, _ = refinement
    runner, failed_a = failed_edit(refinement, tmp_path)
    parent = flow.store.load(failed_a["cover_set_id"])
    second = tasks.start(flow, runner, director, cover_set_id=parent.cover_set_id,
        candidate_id=parent.candidates[1].candidate_id, expected_revision=parent.revision,
        project_id="book", refinement_prompt=PROMPT)
    finish(runner)
    second = tasks.get(flow, second["task_id"], runner)
    service, _ = _editing_service(tmp_path, outcome=ImageClientError("second slot unavailable"))
    _approve(flow, runner, service, second)
    finish(runner)
    failed_b = tasks.get(flow, second["task_id"], runner)
    assert failed_b["status"] == "failed"
    resolved = tasks.for_candidate(flow, runner, failed_b["cover_set_id"], failed_a["result_candidate_id"])
    assert resolved["task_id"] == failed_a["task_id"]
    assert resolved["lookup_cover_set_id"] == failed_b["cover_set_id"]
    assert resolved["lookup_candidate_id"] == failed_a["result_candidate_id"]
    assert resolved["cover_set_id"] == failed_a["cover_set_id"]
    assert "lookup_cover_set_id" not in tasks._read(flow, failed_a["task_id"])
    assert tasks.for_candidate(flow, runner, failed_b["cover_set_id"], failed_b["result_candidate_id"])["task_id"] == failed_b["task_id"]
    copied = flow.store.load(failed_b["cover_set_id"])
    changed = replace(copied.candidates[0], generation_prompt="unverified inherited slot")
    flow.store.save(replace(copied, candidates=(changed, *copied.candidates[1:])), expected_revision=copied.revision)
    with pytest.raises(CoverConflict, match="Retained"):
        tasks.for_candidate(flow, runner, copied.cover_set_id, failed_a["result_candidate_id"])


def test_retry_checks_active_direction_without_inverting_its_task_lock(refinement, tmp_path, monkeypatch):
    from api import cover_direction_tasks
    flow, _, _, _ = refinement
    runner, failed = failed_edit(refinement, tmp_path)
    monkeypatch.setattr(cover_direction_tasks, "_task_lock", lambda *args: pytest.fail("store-to-direction lock inversion"))
    monkeypatch.setattr(cover_direction_tasks, "_records", lambda *args: [{"status": "running"}])
    monkeypatch.setattr(cover_direction_tasks, "_project", lambda value, runner: value)
    with pytest.raises(CoverConflict, match="正在执行"):
        tasks.retry(flow, runner, failed["task_id"], project_id="book")
    assert len(flow.store.list_directions()) == 2


@pytest.mark.parametrize("owner", ["project", "workshop"])
def test_retry_and_exact_result_lookup_http_owner_routes(refinement, tmp_path, monkeypatch, owner):
    from api import cover_workbench
    from tests.test_cover_api import _client
    flow, parent, director, project = refinement
    if owner == "workshop":
        parent = flow.store.save(replace(parent, project_id="workshop-discussion"), expected_revision=parent.revision)
        refinement = (flow, parent, director, project)
        # Helpers fix book ownership; explicitly prepare and fail this owner.
        runner = DeferredRunner()
        task = tasks.start(flow, runner, director, cover_set_id=parent.cover_set_id,
            candidate_id=parent.candidates[0].candidate_id, expected_revision=parent.revision,
            session_id="discussion", refinement_prompt=PROMPT)
        finish(runner)
        task = tasks.get(flow, task["task_id"], runner)
        service, _ = _editing_service(tmp_path, outcome=ImageClientError("failed"))
        tasks.approve(flow, runner, service, task["task_id"], expected_proposal_sha256=task["proposal_sha256"],
            confirm_image_calls=1, session_id="discussion")
        finish(runner)
        failed = tasks.get(flow, task["task_id"], runner)
    else:
        runner, failed = failed_edit(refinement, tmp_path)
    client, _ = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(cover_workbench, f"{owner}_workflow", lambda *args: flow)
    monkeypatch.setattr(cover_workbench, "runner", runner)
    base = "/api/projects/book/covers" if owner == "project" else "/api/workshop/sessions/discussion/covers"
    response = client.get(base + "/repair-tasks/for-candidate", params={
        "cover_set_id": failed["cover_set_id"], "candidate_id": failed["result_candidate_id"]})
    assert response.status_code == 200 and response.json()["task_id"] == failed["task_id"]
    response = client.post(base + f"/repair-tasks/{failed['task_id']}/retry")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_approval"
    assert response.json()["retry_of_task_id"] == failed["task_id"]
    assert len(runner.jobs) == 2
