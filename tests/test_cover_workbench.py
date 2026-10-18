from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import replace
import json
import threading
from types import SimpleNamespace

import pytest

from api import cover_workbench, routes
from api.cover_workbench import AnalysisRequest
from core.cover_analysis import load_analysis, reserve_analysis
from core.cover_sources import load_snapshot
from core.cover_store import CoverStore
from tests.test_cover_api import _client, _wait
from tests.test_cover_analysis_snapshot import source
from tests.test_cover_workflow import FullStoryFixture, workflow, approve, bound_set


class DeferredRunner:
    def __init__(self):
        self.jobs = {}

    def submit(self, kind, fn, meta=None, **kwargs):
        job_id = str(len(self.jobs) + 1)
        self.jobs[job_id] = {"job_id": job_id, "status": "running", **(meta or {}),
                             "fn": fn, "cancel_event": kwargs.get("cancel_event")}
        return job_id

    def get(self, job_id):
        return self.jobs.get(job_id)

    def cancel(self, job_id):
        job = self.jobs[job_id]
        job["cancel_event"].set()
        job["status"] = "error"
        return True


def test_http_source_analysis_approval_generation_and_source_change(tmp_path, monkeypatch, source):
    fixture = FullStoryFixture()
    client, image_client = _client(tmp_path, monkeypatch, director=fixture)
    project_id = client.post("/api/projects", json={"title": "Skeleton API", "genre": "Drama"}).json()["id"]
    project = tmp_path / "projects" / project_id
    prompt_path = project / "outputs/input/prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_bytes(source[1].primary_bytes)
    base = f"/api/projects/{project_id}/covers"
    preview = client.get(base + "/source")
    assert preview.status_code == 200, preview.text
    assert preview.json()["primary_text"] == source[1].primary_text
    started = client.post(base + "/analyses", json={"source_selector": {
        "source_type": "project_input", "project_id": project_id,
        "expected_content_sha256": preview.json()["source_content_sha256"]}})
    assert started.status_code == 202, started.text
    analysis_id = started.json()["analysis_id"]
    assert _wait(client, started.json()["job_id"])["status"] == "done"
    analysis = client.get(base + f"/analyses/{analysis_id}").json()
    assert analysis["coverage_status"] == "complete"
    pending = client.post(base + "/directions", json={"analysis_id": analysis_id})
    assert pending.status_code == 201, pending.text
    direction = pending.json()
    approval = {"expected_brief_sha256": direction["brief_sha256"],
        "approved_direction_sha256": direction["direction_sha256"], "confirm_source": True}
    assert client.post(base + f"/directions/{direction['direction_id']}/approve", json={
        **approval, "confirm_source": False}).status_code == 409
    approved = client.post(base + f"/directions/{direction['direction_id']}/approve", json=approval)
    assert approved.status_code == 200, approved.text
    body = {"direction_id": direction["direction_id"], "approved_direction_sha256": direction["direction_sha256"]}
    generated = client.post(base + "/generate", json=body)
    assert generated.status_code == 202, generated.text
    done = _wait(client, generated.json()["job_id"])
    assert done["status"] == "done", done
    assert image_client.calls == 4
    assert client.post(base + "/generate", json=body).status_code == 409
    # A new source cannot be covered by confirm_stale or the old approval.
    prompt_path.write_text("A changed skeleton")
    refused = client.post(base + "/generate", json=body)
    assert refused.status_code == 409 and "骨架" in refused.text
    assert image_client.calls == 4
    assert client.get(base + "/source?source_type=unrecognized").status_code in {400, 409}


def test_analysis_dispatch_coalesces_before_allocating_and_cancel_resume_use_same_id(workflow, monkeypatch):
    flow, previous, director, _ = workflow
    deferred = DeferredRunner()
    monkeypatch.setattr(cover_workbench, "runner", deferred)
    monkeypatch.setattr(cover_workbench, "_ANALYSIS_DISPATCHES", {})
    monkeypatch.setattr(cover_workbench, "_ANALYSIS_JOBS", {})
    initial_count = len(list((flow.store.root / "analyses").glob("analysis-*")))
    first = cover_workbench._start_analysis(flow, AnalysisRequest(snapshot_id=previous.snapshot_id), director, project_id="book")
    second = cover_workbench._start_analysis(flow, AnalysisRequest(snapshot_id=previous.snapshot_id), director, project_id="book")
    assert first["job_id"] == second["job_id"]
    assert len(list((flow.store.root / "analyses").glob("analysis-*"))) == initial_count + 1
    assert load_analysis(flow.context, first["analysis_id"]).coverage_status == "pending"
    assert cover_workbench._cancel_analysis(flow, first["analysis_id"])["cancel_requested"]
    with pytest.raises(ValueError):
        first["fn"]()
    paused = load_analysis(flow.context, first["analysis_id"])
    assert paused.phase == "cancelled"
    resumed = cover_workbench._start_analysis(flow, AnalysisRequest(analysis_id=first["analysis_id"]), director, project_id="book")
    assert resumed["analysis_id"] == first["analysis_id"]
    result = resumed["fn"]()
    assert result.analysis_id == first["analysis_id"] and result.coverage_status == "complete"


def test_dispatch_rejection_is_recorded_in_allocated_analysis(workflow, monkeypatch):
    flow, previous, director, _ = workflow
    deferred = DeferredRunner()
    def reject(*args, **kwargs):
        raise RuntimeError("queue unavailable")
    deferred.submit = reject
    monkeypatch.setattr(cover_workbench, "runner", deferred)
    with pytest.raises(RuntimeError, match="queue unavailable"):
        cover_workbench._start_analysis(flow, AnalysisRequest(snapshot_id=previous.snapshot_id, force=True), director, project_id="book")
    records = [load_analysis(flow.context, path.name) for path in (flow.store.root / "analyses").glob("analysis-*")]
    failed = [record for record in records if record.coverage_status == "failed"]
    assert len(failed) == 1 and "queue unavailable" in str(failed[0].last_error)


def test_exhausted_source_recovery_http_is_read_only_and_rejects_before_job(tmp_path, monkeypatch, source):
    from core.cover_workflow import CoverWorkflow
    from tests.test_cover_analysis_corrections import failed_parent, prepare
    from tests.test_cover_analysis_snapshot import ModelFixture

    context, _, project = source
    parent = failed_parent(source)
    flow = CoverWorkflow(context, CoverStore(project, cover_root=context.cover_root))
    model = ModelFixture()
    director = SimpleNamespace(_complete=model, model='fixture-analyzer',
                               analysis_configuration={'max_repair_attempts': 0})
    deferred = DeferredRunner()
    deferred.get = lambda job_id: ({key: value for key, value in deferred.jobs[job_id].items()
                                   if key not in {'fn', 'cancel_event'}} if job_id in deferred.jobs else None)
    monkeypatch.setattr(cover_workbench, 'runner', deferred)
    monkeypatch.setattr(cover_workbench, '_ANALYSIS_DISPATCHES', {})
    monkeypatch.setattr(cover_workbench, '_ANALYSIS_JOBS', {})
    monkeypatch.setattr(cover_workbench, 'project_workflow', lambda svc, project_id: flow)
    client, _ = _client(tmp_path, monkeypatch, director=director)
    base = '/api/projects/book/covers'

    def files():
        return {p.relative_to(context.cover_root): p.read_bytes()
                for p in context.cover_root.rglob('*') if p.is_file()}

    original = files()
    response = client.get(base + '/analyses/' + parent.analysis_id)
    assert response.status_code == 200, response.text
    assert response.json()['recovery']['kind'] == 'source_correction_required'
    preview = client.get(base + '/source')
    assert preview.status_code == 200, preview.text
    assert preview.json()['analyses'][0]['recovery'] == response.json()['recovery']
    blocked = client.post(base + '/analyses', json={'analysis_id': parent.analysis_id})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['detail']['code'] == 'analysis_semantic_repair_exhausted'
    assert blocked.json()['detail']['recovery']['can_resume'] is False
    assert not deferred.jobs and not model.calls
    assert files() == original

    child = prepare(context, parent)
    original = files()
    parent_response = client.get(base + '/analyses/' + parent.analysis_id)
    assert parent_response.json()['recovery']['kind'] == 'amendment_available'
    assert parent_response.json()['recovery']['analysis_id'] == child.analysis_id
    child_response = client.get(base + '/analyses/' + child.analysis_id)
    assert child_response.json()['recovery']['kind'] == 'amendment_pending'
    assert child_response.json()['recovery']['can_resume'] is True
    assert child_response.json()['coverage_status'] == 'partial'
    blocked = client.post(base + '/analyses', json={'analysis_id': parent.analysis_id})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['detail']['code'] == 'analysis_amendment_required'
    assert not deferred.jobs and not model.calls
    assert files() == original

    started = client.post(base + '/analyses', json={'analysis_id': child.analysis_id})
    assert started.status_code == 202, started.text
    assert started.json()['analysis_id'] == child.analysis_id
    assert len(deferred.jobs) == 1 and not model.calls
    result = deferred.jobs[started.json()['job_id']]['fn']()
    assert result.coverage_status == 'complete'
    assert all(request['phase'] == 'verify' for request in model.calls)


def test_exhausted_legacy_policy_can_still_explicitly_upgrade_via_api(source, monkeypatch):
    from core.cover_workflow import CoverWorkflow
    from tests.test_cover_analysis_corrections import failed_parent
    from tests.test_cover_analysis_snapshot import ModelFixture

    context, _, project = source
    parent = failed_parent(source, policy='cover-story-verification.v2')
    flow = CoverWorkflow(context, CoverStore(project, cover_root=context.cover_root))
    model = ModelFixture()
    director = SimpleNamespace(_complete=model, model='fixture-analyzer',
                               analysis_configuration={'max_repair_attempts': 0})
    deferred = DeferredRunner()
    monkeypatch.setattr(cover_workbench, 'runner', deferred)
    monkeypatch.setattr(cover_workbench, '_ANALYSIS_DISPATCHES', {})
    monkeypatch.setattr(cover_workbench, '_ANALYSIS_JOBS', {})
    assert cover_workbench._analysis_runtime(flow, parent.to_dict())['recovery'] is None
    started = cover_workbench._start_analysis(flow, AnalysisRequest(analysis_id=parent.analysis_id),
                                              director, project_id='book')
    result = started['fn']()
    assert result.coverage_status == 'complete'
    assert result.semantic_revision['round'] == 1
    assert result.semantic_revision['first_attempt_id'] == parent.semantic_revision['first_attempt_id']
    assert model.calls and all(request['phase'] == 'verify' for request in model.calls)


def test_workshop_artwork_and_attempt_urls_are_scoped(workflow):
    flow, analysis, director, _ = workflow
    direction = approve(flow, flow.plan(analysis.analysis_id, director))
    cover_set = bound_set(flow, direction)
    candidate = replace(cover_set.candidates[0], quality_report={"assets": {"artwork_media_id": "art"}},
                        attempt_history=({"media_id": "attempt", "quality_report": {"assets": {"artwork_media_id": "oldart"}}},))
    cover_set = replace(cover_set, candidates=(candidate, *cover_set.candidates[1:]))
    out = cover_workbench._workshop_set_out("session", cover_set)["candidates"][0]
    assert out["quality_report"]["assets"]["artwork_url"] == "/api/workshop/sessions/session/covers/media/art"
    assert out["attempt_history"][0]["url"] == "/api/workshop/sessions/session/covers/media/attempt"
    assert out["attempt_history"][0]["quality_report"]["assets"]["artwork_url"].endswith("/covers/media/oldart")


def test_cli_uses_same_snapshot_analysis_approval_contract(tmp_path, monkeypatch, capsys, source):
    from core import cover_cli
    project = tmp_path / "book"
    (project / "outputs/state").mkdir(parents=True)
    (project / "outputs/state/story_state.json").write_text("{}")
    (project / "outputs/input").mkdir()
    (project / "outputs/input/prompt.md").write_bytes(source[1].primary_bytes)
    monkeypatch.setattr(routes, "get_cover_art_director", lambda: FullStoryFixture())
    parser = argparse.ArgumentParser()
    cover_cli.configure_cover_parser(parser.add_subparsers(dest="command"))
    def run(*args):
        code = cover_cli.run_cover_command(parser.parse_args(["cover", *args, "--project", str(project)]))
        output = capsys.readouterr()
        assert code == 0, output.err
        return json.loads(output.out)
    preview = run("inspect-source")
    analyzed = run("analyze", "--expected-content-sha256", preview["source_content_sha256"])
    direction = run("plan", "--analysis-id", analyzed["analysis_id"])
    approved = run("approve", "--direction-id", direction["direction_id"], "--expected-brief-sha256", direction["brief_sha256"],
                  "--approved-direction-sha256", direction["direction_sha256"], "--confirm-source")
    assert approved["status"] == "approved" and approved["source_binding"]["owner_scope"] == "default:project:book"
    flow = cover_cli._workflow(project)
    _, _, kwargs = flow.generation_arguments(direction["direction_id"], direction["direction_sha256"], reserve=False)
    assert kwargs["compiled_recipes"] == approved["compiled_recipes"]


def test_http_get_restores_running_analysis_without_dispatch_or_record_changes(tmp_path, monkeypatch, workflow):
    flow, previous, director, _ = workflow
    deferred = DeferredRunner()
    monkeypatch.setattr(cover_workbench, "runner", deferred)
    monkeypatch.setattr(cover_workbench, "_ANALYSIS_DISPATCHES", {})
    monkeypatch.setattr(cover_workbench, "_ANALYSIS_JOBS", {})
    monkeypatch.setattr(cover_workbench, "project_workflow", lambda svc, project_id: flow)
    client, _ = _client(tmp_path, monkeypatch, director=director)
    started = cover_workbench._start_analysis(flow, AnalysisRequest(snapshot_id=previous.snapshot_id), director, project_id="book")
    analysis_id = started["analysis_id"]
    record_path = flow.store.root / "analyses" / analysis_id / "analysis.json"
    original_bytes = record_path.read_bytes()
    snapshot = load_snapshot(flow.context, previous.snapshot_id)

    # A newly mounted page has neither a job id nor an analysis id in memory.
    source_response = client.get("/api/projects/book/covers/source")
    assert source_response.status_code == 200, source_response.text
    summary = next(item for item in source_response.json()["analyses"] if item["analysis_id"] == analysis_id)
    response = client.get(f"/api/projects/book/covers/analyses/{analysis_id}")
    assert response.status_code == 200, response.text
    for item in (summary, response.json()):
        assert item["job_id"] == started["job_id"]
        assert item["job_status"] == "running"
        assert item["execution_status"] == "running"
        assert item["job_error"] is None
        assert item["source_selector"] == snapshot.selector.to_dict()
        assert item["source_bundle_sha256"] == snapshot.source_bundle_sha256
    assert len(deferred.jobs) == 1
    assert record_path.read_bytes() == original_bytes


@pytest.mark.parametrize("coverage,phase,error,job_status,job_error,expected", [
    ("pending", "pending", None, None, None, "interrupted"),
    ("partial", "segment", None, None, None, "interrupted"),
    ("partial", "verify", None, "done", None, "interrupted"),
    ("partial", "cancelled", {"code": "analysis_cancelled", "message": "cancelled"}, None, None, "paused"),
    ("partial", "failed", {"code": "analysis_failed", "message": "invalid facts"}, None, None, "failed"),
    ("failed", "failed", {"code": "analysis_dispatch_failed", "message": "queue error"}, None, None, "failed"),
    ("partial", "segment", None, "error", "provider unavailable", "failed"),
    ("partial", "segment", None, "error", "任务已暂停。", "paused"),
    ("partial", "failed", {"code": "analysis_failed", "message": "old failure"}, "running", None, "running"),
])
def test_analysis_execution_projection_distinguishes_terminal_and_interrupted_records(
        workflow, monkeypatch, coverage, phase, error, job_status, job_error, expected):
    flow, previous, _, _ = workflow
    snapshot = load_snapshot(flow.context, previous.snapshot_id)
    analysis_id = reserve_analysis(flow.context, snapshot)
    record_path = flow.store.root / "analyses" / analysis_id / "analysis.json"
    payload = json.loads(record_path.read_text("utf-8"))
    payload.update(coverage_status=coverage, phase=phase, last_error=error)
    record_path.write_text(json.dumps(payload), encoding="utf-8")
    original_bytes = record_path.read_bytes()
    deferred = DeferredRunner()
    key = (str(flow.context.cover_root.resolve()), flow.context.owner_scope, analysis_id)
    # After process restart the index itself can be absent; after job eviction
    # it can retain an id whose runner record has disappeared.
    monkeypatch.setattr(cover_workbench, "_ANALYSIS_JOBS", {key: "remembered-job"})
    monkeypatch.setattr(cover_workbench, "runner", deferred)
    if job_status:
        deferred.jobs["remembered-job"] = {"job_id": "remembered-job", "kind": "cover.analyze",
            "analysis_id": analysis_id, "snapshot_id": snapshot.snapshot_id,
            "status": job_status, "error": job_error}

    response = cover_workbench._analysis(flow, analysis_id)
    summary = next(item for item in cover_workbench._list_analyses(flow) if item["analysis_id"] == analysis_id)

    for item in (response, summary):
        assert item["execution_status"] == expected
        assert item["job_id"] == ("remembered-job" if job_status else None)
        assert item["job_status"] == job_status
        assert item["job_error"] == job_error
        assert item["coverage_status"] == coverage and item["phase"] == phase
    assert record_path.read_bytes() == original_bytes


def test_completed_analysis_keeps_immutable_hash_after_job_index_is_lost(workflow, monkeypatch):
    flow, complete, _, _ = workflow
    record_path = flow.store.root / "analyses" / complete.analysis_id / "analysis.json"
    original_bytes = record_path.read_bytes()
    monkeypatch.setattr(cover_workbench, "_ANALYSIS_JOBS", {})
    monkeypatch.setattr(cover_workbench, "runner", DeferredRunner())

    result = cover_workbench._analysis(flow, complete.analysis_id)

    assert result["execution_status"] == "complete"
    assert result["job_id"] is None and result["job_status"] is None
    assert result["analysis_sha256"] == complete.analysis_sha256
    assert record_path.read_bytes() == original_bytes
    assert load_analysis(flow.context, complete.analysis_id).to_dict() == complete.to_dict()


def test_runtime_projection_does_not_recover_a_different_analysis_job(workflow, monkeypatch):
    flow, previous, _, _ = workflow
    snapshot = load_snapshot(flow.context, previous.snapshot_id)
    analysis_id = reserve_analysis(flow.context, snapshot)
    key = (str(flow.context.cover_root.resolve()), flow.context.owner_scope, analysis_id)
    monkeypatch.setattr(cover_workbench, "_ANALYSIS_JOBS", {key: "foreign-job"})
    deferred = DeferredRunner()
    deferred.jobs["foreign-job"] = {"job_id": "foreign-job", "kind": "cover.analyze",
                                    "analysis_id": "analysis-" + "f" * 32, "status": "running"}
    monkeypatch.setattr(cover_workbench, "runner", deferred)

    result = cover_workbench._analysis(flow, analysis_id)

    assert result["execution_status"] == "interrupted"
    assert result["job_id"] is None and result["job_status"] is None


def test_http_cancel_keeps_cover_worker_running_until_inflight_results_are_saved(tmp_path, monkeypatch, workflow):
    from api.jobs import JobRunner
    flow, previous, director, _ = workflow
    entered, release = threading.Event(), threading.Event()
    original_complete = director._complete

    def blocking_complete(system, user):
        if json.loads(user)["phase"] == "segment":
            entered.set()
            assert release.wait(5)
        return original_complete(system, user)

    director._complete = blocking_complete
    jobs = JobRunner()
    monkeypatch.setattr(cover_workbench, "runner", jobs)
    monkeypatch.setattr(routes, "runner", jobs)
    monkeypatch.setattr(cover_workbench, "_ANALYSIS_DISPATCHES", {})
    monkeypatch.setattr(cover_workbench, "_ANALYSIS_JOBS", {})
    monkeypatch.setattr(cover_workbench, "project_workflow", lambda svc, project_id: flow)
    client, _ = _client(tmp_path, monkeypatch, director=director)
    base = "/api/projects/book/covers"
    started = client.post(base + "/analyses", json={"snapshot_id": previous.snapshot_id, "force": True})
    assert started.status_code == 202, started.text
    job_id, analysis_id = started.json()["job_id"], started.json()["analysis_id"]
    try:
        assert entered.wait(1)
        # Old and current adapters can place identity either at top level or in meta.
        with jobs._lock:
            jobs._jobs[job_id].pop("analysis_id")
        cancelled = client.post(base + f"/analyses/{analysis_id}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["cancel_requested"] is True
        stopping_job = client.get(f"/api/jobs/{job_id}").json()
        assert stopping_job["status"] == "running" and stopping_job["cancel_requested"] is True
        assert stopping_job["finished_at"] is None
        detail = client.get(base + f"/analyses/{analysis_id}").json()
        summary = next(item for item in client.get(base + "/source").json()["analyses"] if item["analysis_id"] == analysis_id)
        for payload in (detail, summary):
            assert payload["execution_status"] == "running" and payload["cancel_requested"] is True
        resumed_early = client.post(base + "/analyses", json={"analysis_id": analysis_id})
        assert resumed_early.status_code == 202 and resumed_early.json()["job_id"] == job_id
        assert len(jobs._jobs) == 1
        release.set()
        done = _wait(client, job_id)
        assert done["status"] == "error" and done["error"] == "任务已暂停。"
        durable = load_analysis(flow.context, analysis_id)
        assert durable.phase == "cancelled" and durable.coverage["processed_segment_ids"]
        restored = client.get(base + f"/analyses/{analysis_id}").json()
        assert restored["execution_status"] == "paused"
        assert restored["coverage"]["processed_segment_ids"] == durable.coverage["processed_segment_ids"]
    finally:
        release.set()
