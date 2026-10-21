"""Single repair preserves the original batch, budget and recovery contract."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from api import cover_repair_tasks as tasks
from api.cover_service import CoverService
from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_workflow import digest
from core.cover_store import CoverConflict
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_workflow import workflow, approve, bound_set  # noqa: F401
from tests.test_cover_workbench import DeferredRunner
from tests.test_cover_service import _service, _jpeg


@pytest.fixture
def repair(workflow):
    flow, analysis, director, project = workflow
    direction = approve(flow, flow.plan(analysis.analysis_id, director))
    parent = bound_set(flow, direction)
    report = {"status": "blocked", "repair_decision": {"kind": "design", "codes": ["core_conflict_missing"]},
              "blockers": ["core_conflict_missing"], "findings": [{"code": "core_conflict_missing", "severity": "blocker", "message": "Source-supported action is unclear"}]}
    parent = flow.store.save(replace(parent, candidates=tuple(replace(x, status="failed", quality_report=report) for x in parent.candidates)), expected_revision=parent.revision)
    # The fixture exercises workflow/storage/approval, not real semantic judgment.
    class Director:
        def plan(self, brief, *, count, evidence_ledger, story_analysis, targeted_repair):
            value = deepcopy(targeted_repair["base"])
            selected = next(x for x in value["plans"] if x["concept_id"] == targeted_repair["concept_id"])
            selected["frozen_action"] += " Revised visible action."
            CoverArtDirector._validate_targeted_repair(targeted_repair, value)
            return CoverArtDirector.from_fixture(value).plan(brief, count=count,
                evidence_ledger=evidence_ledger, story_analysis=story_analysis)
    return flow, parent, Director(), project


def start(repair, runner):
    flow, parent, director, _ = repair
    return tasks.start(flow, runner, director, cover_set_id=parent.cover_set_id,
        candidate_id=parent.candidates[0].candidate_id, expected_revision=parent.revision, project_id="book")


def finish(runner):
    job = list(runner.jobs.values())[-1]
    result = job["fn"]()
    job["status"] = "done"
    return result


def test_repair_requires_a_separate_exact_one_image_approval(repair):
    flow, parent, _, _ = repair
    runner = DeferredRunner()
    first = start(repair, runner)
    assert first["status"] == "planning" and first["image_calls"] == 1
    assert tasks.latest(flow, runner)["task_id"] == first["task_id"]
    assert start(repair, runner)["task_id"] == first["task_id"]
    finish(runner)
    task = tasks.latest(flow, runner)
    assert task["status"] == "awaiting_approval" and task["proposal_sha256"]
    assert flow.store.load(parent.cover_set_id) == parent
    direction = flow.store.load_direction(task["direction_id"])
    with pytest.raises(CoverConflict, match="仅允许"):
        approve(flow, direction)
    approve(flow, direction, single_candidate=parent.concepts[0].concept_id)
    with pytest.raises(CoverConflict, match="整批"):
        flow.generation_arguments(direction.direction_id, direction.direction_sha256)
    args = flow.generation_arguments(direction.direction_id, direction.direction_sha256,
        single_candidate=parent.concepts[0].concept_id, reserve=False)
    assert len(args[1]) == 4
    assert flow.approval(direction.direction_id)["initial_image_calls"] == 1


def test_approval_rejects_wrong_budget_stale_proposal_and_changed_parent(repair):
    flow, parent, _, _ = repair
    runner = DeferredRunner()
    task = start(repair, runner)
    finish(runner)
    task = tasks.get(flow, task["task_id"], runner)
    for sha, count in [("f" * 64, 1), (task["proposal_sha256"], 0), (task["proposal_sha256"], 2)]:
        with pytest.raises(CoverConflict, match="准确"):
            tasks.approve(flow, runner, None, task["task_id"], expected_proposal_sha256=sha, confirm_image_calls=count, project_id="book")
    flow.store.save(replace(parent, status="failed"), expected_revision=parent.revision)
    with pytest.raises(CoverConflict, match="过期"):
        tasks.approve(flow, runner, None, task["task_id"], expected_proposal_sha256=task["proposal_sha256"], confirm_image_calls=1, project_id="book")
    assert len(runner.jobs) == 1


def test_commercial_advice_survives_refresh_and_is_bound_to_proposal_approval(repair, monkeypatch):
    flow, _, _, _ = repair
    original_plan = flow.plan
    advisory = {"category": "thumbnail_readability", "concept_id": "concept_01",
        "message": "Optional: simplify the background.", "evidence_refs": ["evidence:verified-source"]}
    def reviewed_plan(*args, **kwargs):
        direction = original_plan(*args, **kwargs)
        return replace(direction, source_binding={**direction.source_binding,
            "campaign_evidence_review": {"policy_version": "cover-commercial-review.v1", "advisories": [advisory]}})
    monkeypatch.setattr(flow, "plan", reviewed_plan)
    runner = DeferredRunner()
    start(repair, runner)
    finish(runner)
    task = tasks.latest(flow, runner)
    assert task["status"] == "awaiting_approval"
    assert task["proposal"]["commercial_review"]["advisories"] == [advisory]
    assert task["proposal_sha256"] == digest(task["proposal"])
    path = tasks._path(flow, task["task_id"])
    before = path.read_bytes()
    restored = tasks.get(flow, task["task_id"], DeferredRunner())
    assert restored["proposal"] == task["proposal"] and path.read_bytes() == before
    changed = json.loads(before)
    changed["proposal"]["commercial_review"]["advisories"][0]["message"] = "Unapproved advice"
    path.write_text(json.dumps(changed))
    with pytest.raises(CoverConflict, match="完整性"):
        tasks.approve(flow, runner, None, task["task_id"],
            expected_proposal_sha256=task["proposal_sha256"], confirm_image_calls=1, project_id="book")
    assert len(runner.jobs) == 1


def test_repair_restart_is_read_only_and_does_not_replay(repair):
    flow, _, _, _ = repair
    runner = DeferredRunner()
    task = start(repair, runner)
    path = tasks._path(flow, task["task_id"])
    before = path.read_bytes()
    restored = tasks.get(flow, task["task_id"], DeferredRunner())
    assert restored["status"] == "interrupted"
    assert before == path.read_bytes()
    assert len(runner.jobs) == 1


def test_targeted_patch_forbids_unrelated_slots_and_shared_identity():
    base = {"plans": [{"concept_id": "a", "frozen_action": "original"}, {"concept_id": "b", "frozen_action": "retained"}],
            "visual_identity": {"palette": "blue"}}
    repair = {"base": base, "concept_id": "a"}
    for mutate in [lambda x: x["plans"][1].update(frozen_action="changed"),
                   lambda x: x["visual_identity"].update(palette="red"),
                   lambda x: x["plans"].reverse()]:
        payload = deepcopy(base)
        mutate(payload)
        with pytest.raises(CoverDirectionError):
            CoverArtDirector._validate_targeted_repair(repair, payload)
    payload = deepcopy(base)
    payload["plans"][0]["frozen_action"] = "revised"
    CoverArtDirector._validate_targeted_repair(repair, payload)


def test_approved_repair_draws_exactly_one_and_retains_parent_and_sibling_provenance(repair, tmp_path):
    flow, parent, director, project = repair
    service, images, _ = _service(tmp_path, [_jpeg(marker=b"repair")])
    runner = DeferredRunner()
    task = start(repair, runner)
    finish(runner)
    task = tasks.get(flow, task["task_id"], runner)
    original_bytes = flow.store._set_path(parent.cover_set_id).read_bytes()
    approved = tasks.approve(flow, runner, service, task["task_id"],
        expected_proposal_sha256=task["proposal_sha256"], confirm_image_calls=1, project_id="book")
    assert approved["status"] == "generating" and len(images.prompts) == 0
    duplicate = tasks.approve(flow, runner, service, task["task_id"],
        expected_proposal_sha256=task["proposal_sha256"], confirm_image_calls=1, project_id="book")
    assert duplicate["task_id"] == approved["task_id"] and len(runner.jobs) == 2
    finish(runner)
    done = tasks.get(flow, task["task_id"], runner)
    assert done["status"] == "completed", done
    assert done["cover_set_id"] != parent.cover_set_id
    assert len(images.prompts) == 1
    child = flow.store.load(done["cover_set_id"])
    assert child.candidates[1:] == parent.candidates[1:]
    assert child.concepts[1:] == parent.concepts[1:]
    assert child.candidates[0].candidate_id != parent.candidates[0].candidate_id
    assert child.concepts[0].scene_plan["_source_binding"]["direction_id"] == task["direction_id"]
    assert flow.store._set_path(parent.cover_set_id).read_bytes() == original_bytes
    assert not child.selected_candidate_id and flow.store.active().cover_set_id == ""
    assert flow.approval(task["direction_id"])["batch_reserved"] is True
    tasks.approve(flow, runner, service, task["task_id"], expected_proposal_sha256=task["proposal_sha256"], confirm_image_calls=1, project_id="book")
    assert len(runner.jobs) == 2 and len(images.prompts) == 1


def test_durability_failure_before_new_image_stops_the_paid_operation(repair, tmp_path, monkeypatch):
    flow, parent, _, _ = repair
    service, images, _ = _service(tmp_path, [_jpeg()])
    runner = DeferredRunner()
    task = start(repair, runner)
    finish(runner)
    task = tasks.get(flow, task["task_id"], runner)
    tasks.approve(flow, runner, service, task["task_id"], expected_proposal_sha256=task["proposal_sha256"], confirm_image_calls=1, project_id="book")
    write = tasks._write
    def failing(flow, value):
        if value.get("cover_set_id"):
            raise OSError("checkpoint unavailable")
        write(flow, value)
    monkeypatch.setattr(tasks, "_write", failing)
    with pytest.raises(OSError, match="checkpoint"):
        finish(runner)
    assert images.prompts == []
    assert flow.store.load(parent.cover_set_id) == parent


def test_awaiting_approval_is_recovered_without_replanning(repair):
    flow, _, _, _ = repair
    runner = DeferredRunner()
    task = start(repair, runner)
    finish(runner)
    assert start(repair, runner)["task_id"] == task["task_id"]
    assert tasks.latest(flow, DeferredRunner())["status"] == "awaiting_approval"
    assert len(runner.jobs) == 1


@pytest.mark.parametrize("owner", ["project", "workshop"])
def test_repair_status_http_owner_routes(repair, tmp_path, monkeypatch, owner):
    from tests.test_cover_api import _client
    from api import cover_workbench
    flow, _, _, _ = repair
    client, _ = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(cover_workbench, f"{owner}_workflow", lambda *args: flow)
    runner = DeferredRunner()
    monkeypatch.setattr(cover_workbench, "runner", runner)
    base = "/api/projects/book/covers" if owner == "project" else "/api/workshop/sessions/discussion/covers"
    assert client.get(base + "/repair-tasks/latest").json() is None
    task = start(repair, runner)
    assert client.get(base + "/repair-tasks/latest").json()["task_id"] == task["task_id"]
    assert client.get(base + "/repair-tasks/" + task["task_id"]).json()["status"] == "planning"
    with pytest.raises(FileNotFoundError):
        tasks.get(SimpleNamespace(store=flow.store, context=SimpleNamespace(owner_scope="another-owner")), task["task_id"], runner)


def test_repair_lineage_rejects_relabelled_retained_image_and_recipe(repair, tmp_path):
    from core.cover_repair_lineage import validate_lineage
    flow, parent, _, _ = repair
    service, images, _ = _service(tmp_path, [_jpeg()])
    runner = DeferredRunner()
    task = start(repair, runner)
    finish(runner)
    task = tasks.get(flow, task["task_id"], runner)
    tasks.approve(flow, runner, service, task["task_id"], expected_proposal_sha256=task["proposal_sha256"], confirm_image_calls=1, project_id="book")
    finish(runner)
    child = flow.store.load(tasks.get(flow, task["task_id"], runner)["cover_set_id"])
    validate_lineage(flow.store, child)
    assert child.repair_lineage["parent_cover_set_id"] == parent.cover_set_id
    with pytest.raises(CoverConflict, match="provenance"):
        validate_lineage(flow.store, replace(child, candidates=(child.candidates[0],
            replace(child.candidates[1], model="a different model"), *child.candidates[2:])))
    with pytest.raises(CoverConflict, match="provenance"):
        validate_lineage(flow.store, replace(child, concepts=(child.concepts[0],
            replace(child.concepts[1], generation_prompt="another recipe"), *child.concepts[2:])))
    with pytest.raises(CoverConflict, match="allowance"):
        validate_lineage(flow.store, child, candidate_id=child.candidates[1].candidate_id, for_generation=True)
    flow.validate_candidate(child.cover_set_id, child.candidates[0].candidate_id)


@pytest.mark.parametrize("kind", ["evaluation", "typography"])
def test_local_repair_is_explicitly_zero_image_calls_and_preserves_original(repair, kind):
    flow, parent, director, _ = repair
    candidate = replace(parent.candidates[0], status="ready", relative_path="fixture.png", media_id="fixture",
        sha256="b" * 64, content_type="image/png", provider="fixture", model="fixture-image",
        width=1024, height=1536,
        quality_report={"status": "blocked", "repair_decision": {"kind": kind},
                        "human_acceptance": {"reason": "older approval"}})
    parent = flow.store.save(replace(parent, candidates=(candidate, *parent.candidates[1:])), expected_revision=parent.revision)
    local = (flow, parent, director, flow.store.project_path)
    runner = DeferredRunner()
    task = start(local, runner)
    finish(runner)
    task = tasks.get(flow, task["task_id"], runner)
    assert task["image_calls"] == 0
    calls = []
    class LocalService:
        def reevaluate_candidate(self, owner, path, set_id, candidate_id, **kwargs):
            calls.append(kwargs)
            result = flow.store.load(set_id)
            target = next(x for x in result.candidates if x.candidate_id == candidate_id)
            assert "human_acceptance" not in target.quality_report
            return result
        def repair_one(self, *args, **kwargs):
            pytest.fail("Local repair must never invoke image generation")
    tasks.approve(flow, runner, LocalService(), task["task_id"], expected_proposal_sha256=task["proposal_sha256"], confirm_image_calls=0, project_id="book",
        image_service_factory=lambda: pytest.fail("Local repair does not require image credentials"))
    finish(runner)
    done = tasks.get(flow, task["task_id"], runner)
    assert done["status"] == "completed" and done["result_quality_status"] == "blocked"
    assert calls[0]["preserve_siblings"] is True
    assert done["cover_set_id"] != parent.cover_set_id
    assert flow.store.load(parent.cover_set_id) == parent


def test_dispatch_job_checkpoint_failure_never_enters_model_operation(repair, monkeypatch):
    flow, parent, director, _ = repair
    runner = DeferredRunner()
    write = tasks._write
    failures = []
    def fail_once(flow, value):
        if value.get("job_id") and not failures:
            failures.append(True)
            raise OSError("dispatch write failed")
        write(flow, value)
    monkeypatch.setattr(tasks, "_write", fail_once)
    monkeypatch.setattr(director, "plan", lambda *a, **k: pytest.fail("Dispatch failure must stop text calls"))
    with pytest.raises(OSError, match="dispatch"):
        start(repair, runner)
    with pytest.raises(RuntimeError, match="dispatch"):
        finish(runner)
    assert flow.store.load(parent.cover_set_id) == parent


def test_malformed_task_is_skipped_without_breaking_owner_history(repair):
    flow, _, _, _ = repair
    runner = DeferredRunner()
    task = start(repair, runner)
    bad_id = "repair-task-" + "f" * 32
    flow.store._write_json(tasks._path(flow, bad_id), {"owner_scope": flow.context.owner_scope, "task_id": bad_id})
    assert tasks.latest(flow, runner)["task_id"] == task["task_id"]
    with pytest.raises(ValueError, match="Invalid"):
        tasks.get(flow, bad_id, runner)


def test_live_director_targeted_patch_still_runs_full_independent_review():
    from tests.test_cover_campaign import context
    from tests.cover_review_fixtures import supported_review
    from core.cover_direction_patch import make_patch_contract
    brief, ledger, payload, analysis = context()
    base = CoverArtDirector.from_fixture(payload).plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis).to_dict()
    calls = []
    target = base["plans"][0]["concept_id"]
    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        if request["task"] == "review_campaign_semantics":
            assert len(request["plans"]) == 4
            return json.dumps(supported_review(request))
        assert request["single_candidate_repair"]["concept_id"] == target
        return json.dumps({"schema": "cover-direction-patch.v1", "base_sha256": make_patch_contract(base, 4)["base_sha256"],
            "plan_updates": [{"concept_id": target, "changes": {"frozen_action": base["plans"][0]["frozen_action"] + " A visible pause."}}], "shared_updates": {}})
    director = CoverArtDirector(complete=complete, model="fixture", profile_version="cover-profiles.v10")
    revised = director.plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis,
        targeted_repair={"base": base, "concept_id": target, "quality_report": {"blockers": ["protagonist_action_missing"]}})
    assert len(calls) == 2
    assert [x.to_dict() for x in revised.plans[1:]] == base["plans"][1:]
    assert revised.source_binding["campaign_evidence_review"]


def test_live_director_cannot_modify_siblings_even_on_repair_rounds():
    from tests.test_cover_campaign import context
    brief, ledger, payload, analysis = context()
    base = CoverArtDirector.from_fixture(payload).plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis).to_dict()
    response = deepcopy(base)
    response["plans"][1]["frozen_action"] += " Changed unrelated candidate."
    calls = []
    def complete(system, user):
        calls.append(json.loads(user))
        return json.dumps(response)
    with pytest.raises(CoverDirectionError, match="unrelated"):
        CoverArtDirector(complete=complete, model="fixture", profile_version="cover-profiles.v10").plan(
            brief, count=4, evidence_ledger=ledger, story_analysis=analysis,
            targeted_repair={"base": base, "concept_id": base["plans"][0]["concept_id"], "quality_report": {}})
    assert len(calls) == 3
    assert all(x["task"] != "review_campaign_semantics" for x in calls)


def test_returning_to_first_candidate_after_second_plan_replans_superseded_proposal(repair):
    flow, parent, director, _ = repair
    runner = DeferredRunner()
    first = start(repair, runner)
    finish(runner)
    second = tasks.start(flow, runner, director, cover_set_id=parent.cover_set_id,
        candidate_id=parent.candidates[1].candidate_id, expected_revision=parent.revision, project_id="book")
    finish(runner)
    new_first = start(repair, runner)
    assert new_first["task_id"] not in {first["task_id"], second["task_id"]}
    assert new_first["status"] == "planning"
    assert tasks.get(flow, first["task_id"], runner)["status"] == "failed"
    finish(runner)
    assert tasks.get(flow, new_first["task_id"], runner)["status"] == "awaiting_approval"


@pytest.mark.parametrize("field,value", [("status", []), ("kind", {}), ("created_at", None)])
def test_invalid_task_field_types_are_isolated_from_history(repair, field, value):
    flow, _, _, _ = repair
    runner = DeferredRunner()
    task = start(repair, runner)
    payload = tasks._read(flow, task["task_id"])
    bad_id = "repair-task-" + "e" * 32
    payload.update(task_id=bad_id)
    payload[field] = value
    flow.store._write_json(tasks._path(flow, bad_id), payload)
    assert tasks.latest(flow, runner)["task_id"] == task["task_id"]
    with pytest.raises(ValueError, match="Invalid"):
        tasks.get(flow, bad_id, runner)


def test_source_changes_after_approval_stop_worker_before_image_call(repair, tmp_path):
    flow, parent, _, project = repair
    service, images, _ = _service(tmp_path, [_jpeg()])
    runner = DeferredRunner()
    task = start(repair, runner)
    finish(runner)
    task = tasks.get(flow, task["task_id"], runner)
    tasks.approve(flow, runner, service, task["task_id"], expected_proposal_sha256=task["proposal_sha256"], confirm_image_calls=1, project_id="book")
    (project / "outputs/input/prompt.md").write_text("Changed story facts")
    with pytest.raises(CoverConflict, match="骨架已变化"):
        finish(runner)
    assert images.prompts == []
    assert flow.store.load(parent.cover_set_id) == parent
    assert tasks.get(flow, task["task_id"], runner)["status"] == "failed"
