"""Native resume exposed by project scope, without starting a replacement run."""

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.jobs import JobRunner
from api.services import ProjectService
from pipeline_models import RunManifest, RunSpec, StageResult


def _seed(root, name="book", run_id="b1bd6c12ef6b", **spec):
    project = root / name
    state = project / "outputs/state/story_state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"metadata": {"title": name}, "chapters": {}, "characters": {},
                                "plot_threads": {}, "timeline": [], "style_profile": {}, "session_log": []}))
    manifest = RunManifest.new(RunSpec(project_path=str(project), approval_policy="review_required", **spec), run_id)
    manifest.status = "failed"
    manifest.error = "LLMError: Style Curator conflict extractor model call failed (LLMError)"
    manifest.record(StageResult(phase="publication.copy", status="failed", attempt=6,
                                error=manifest.error, started_at="2026-10-08T10:00:00Z", finished_at="2026-10-08T11:00:00Z"))
    path = project / "outputs/runs" / run_id / "run.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest.to_dict()))
    return project, manifest, path


def _client(root):
    return TestClient(create_app(projects_root=root, db_url=f"sqlite:///{root / 'test.db'}"))


def _wait_job(jobs, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = jobs.get(job_id)
        if job["status"] != "running":
            return job
        time.sleep(.005)
    pytest.fail("test resume worker did not finish")


def test_pause_requires_exact_native_run_and_waits_for_worker_exit(tmp_path, monkeypatch):
    from api import project_tasks, project_task_summary
    project, manifest, path = _seed(tmp_path)
    manifest.status = 'running'
    path.write_text(json.dumps(manifest.to_dict()))
    jobs = JobRunner()
    monkeypatch.setattr(project_tasks, 'runner', jobs)
    monkeypatch.setattr(project_task_summary, 'runner', jobs)
    cancel, release = threading.Event(), threading.Event()
    def worker():
        release.wait(3)
        manifest.status = 'paused'
        path.write_text(json.dumps(manifest.to_dict()))
    job_id = jobs.submit('novel.run', worker,
        {'project_id': 'book', 'project_path': str(project.resolve()), 'run_id': manifest.run_id},
        cancel_event=cancel, defer_cancel_completion=True)
    client = _client(tmp_path)
    url = f'/api/projects/book/tasks/{manifest.run_id}'
    try:
        assert client.get(url).json()['can_pause'] is True
        assert client.post(url + '/pause', json={'job_id': 'stale-job'}).status_code == 409
        assert not cancel.is_set()
        paused = client.post(url + '/pause', json={'job_id': job_id})
        assert paused.status_code == 202
        assert paused.json()['status'] == 'running'
        assert paused.json()['cancel_requested'] is True
        assert paused.json()['can_resume'] is False
        assert jobs.get(job_id)['status'] == 'running'
        assert cancel.is_set()
    finally:
        release.set()
    _wait_job(jobs, job_id)
    assert client.get(url).json()['status'] == 'paused'
    assert client.get(url).json()['can_resume'] is True


def test_old_running_job_does_not_claim_native_pause(tmp_path, monkeypatch):
    from api import project_tasks, project_task_summary
    project, manifest, _ = _seed(tmp_path)
    jobs = JobRunner()
    monkeypatch.setattr(project_tasks, 'runner', jobs)
    monkeypatch.setattr(project_task_summary, 'runner', jobs)
    release = threading.Event()
    job_id = jobs.submit('novel.run', lambda: release.wait(3),
        {'project_id': 'book', 'project_path': str(project.resolve()), 'run_id': manifest.run_id})
    try:
        client = _client(tmp_path)
        url = f'/api/projects/book/tasks/{manifest.run_id}'
        assert client.get(url).json()['can_pause'] is False
        assert client.post(url + '/pause', json={'job_id': job_id}).status_code == 409
    finally:
        release.set()


def test_task_details_and_cards_expose_actual_failure(tmp_path):
    project, manifest, _ = _seed(tmp_path)
    client = _client(tmp_path)
    response = client.get(f"/api/projects/book/tasks/{manifest.run_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["error"] == manifest.error
    assert detail["current_phase"] == "publication.copy"
    assert detail["failed_stages"][0]["error"] == manifest.error
    assert detail["failed_stages"][0]["phase"] == "publication.copy"
    assert detail["can_resume"] is True
    for row in [client.get("/api/projects").json()[0], client.get("/api/projects/book").json()]:
        assert row["task_can_resume"] is True
        assert row["task_error"] == manifest.error
        assert row["task_phase"] == "publication.copy"


def test_exhausted_planning_correction_is_not_advertised_as_retryable(tmp_path):
    project, manifest, path = _seed(tmp_path)
    manifest.current_phase = 'outline'
    path.write_text(json.dumps(manifest.to_dict()))
    sidecar = path.parent / 'planning/progress.json'
    sidecar.parent.mkdir()
    sidecar.write_text(json.dumps({'schema_version': 1, 'run_id': manifest.run_id, 'status': 'failed',
        'next_action': '需要检查保存的设计；不会自动重试。',
        'recovery': {'retryable': False, 'action': 'inspect_saved_design', 'reason': 'Needs a story choice'}}))
    runtime_sidecar = path.parent / 'progress.json'
    runtime_sidecar.write_text(json.dumps({'schema_version': 1, 'run_id': manifest.run_id,
        'status': 'failed', 'phase': 'outline', 'next_action': '可从已验证的检查点恢复。'}))
    before = {p: p.read_bytes() for p in (path, sidecar, runtime_sidecar)}
    client = _client(tmp_path)
    url = f'/api/projects/book/tasks/{manifest.run_id}'
    detail = client.get(url).json()
    assert detail['can_resume'] is False
    assert detail['planning']['recovery']['retryable'] is False
    assert detail['resume_blocked_reason'] == detail['progress']['next_action'] == 'Needs a story choice'
    assert client.get('/api/projects/book').json()['task_can_resume'] is False
    rejected = client.post(url + '/resume')
    assert rejected.status_code == 409
    assert rejected.json()['detail'] == detail['resume_blocked_reason']
    assert all(p.read_bytes() == value for p, value in before.items())
    # A previous outline advisory cannot block a different current stage.
    manifest.current_phase = 'chapter.1'
    path.write_text(json.dumps(manifest.to_dict()))
    assert client.get(url).json()['can_resume'] is True


def test_available_source_restoration_projects_explicit_recovery_without_writing(tmp_path, monkeypatch):
    from core import planning_recovery
    project, manifest, path = _seed(tmp_path)
    manifest.current_phase = 'outline'
    path.write_text(json.dumps(manifest.to_dict()))
    sidecar = path.parent / 'planning/progress.json'
    sidecar.parent.mkdir()
    sidecar.write_text(json.dumps({'schema_version': 1, 'run_id': manifest.run_id, 'status': 'failed',
        'recovery': {'retryable': False, 'action': 'inspect_saved_design', 'reason': 'Old scope'}}))
    before = sidecar.read_bytes()
    monkeypatch.setattr(planning_recovery, 'source_restoration_available', lambda *args: True)
    client = _client(tmp_path)
    detail = client.get(f'/api/projects/book/tasks/{manifest.run_id}').json()
    assert detail['can_resume'] is True
    assert detail['planning']['recovery']['action'] == 'restore_source_omissions'
    assert '模型用量' in detail['planning']['next_action']
    assert client.get('/api/projects/book').json()['task_can_resume'] is True
    assert sidecar.read_bytes() == before


def test_verified_coordinated_upgrade_projects_same_recovery_for_detail_and_cards(tmp_path):
    from test_planning_recovery import seed_mixed
    project, manifest, path = _seed(tmp_path, run_id='test-run')
    manifest.current_phase = 'outline'
    path.write_text(json.dumps(manifest.to_dict()))
    planner = seed_mixed(project)
    runtime_sidecar = path.parent / 'progress.json'
    runtime_sidecar.write_text(json.dumps({'schema_version': 1, 'run_id': manifest.run_id,
        'status': 'failed', 'phase': 'outline', 'next_action': '可从已验证的检查点恢复。'}))
    before = {p: p.read_bytes() for p in project.rglob('*') if p.is_file()}
    client = _client(tmp_path)
    url = f'/api/projects/book/tasks/{manifest.run_id}'
    detail = client.get(url).json()
    assert detail['can_resume'] is True and detail['resume_blocked_reason'] is None
    assert detail['planning']['recovery']['action'] == 'repair_saved_design'
    assert '一次有界协调修订' in detail['progress']['next_action']
    assert detail['progress']['next_action'] == detail['planning']['recovery']['reason']
    assert client.get('/api/projects/book').json()['task_can_resume'] is True
    assert all(p.read_bytes() == value for p, value in before.items())
    # A terminal attempt consumes the upgrade even if the old progress survives.
    planner._write_json(planner.cache / 'coordinated-repair-v1-plan-response.json', {})
    detail = client.get(url).json()
    assert detail['can_resume'] is False
    assert detail['progress']['next_action'] == detail['resume_blocked_reason']
    assert client.post(url + '/resume').json()['detail'] == detail['resume_blocked_reason']


def test_duplicate_resume_uses_same_native_run_and_preserves_policy(tmp_path, monkeypatch):
    from api import project_tasks, project_task_summary
    from pipeline_runner import PipelineRunner
    project, manifest, path = _seed(tmp_path)
    jobs = JobRunner()
    monkeypatch.setattr(project_tasks, "runner", jobs)
    monkeypatch.setattr(project_task_summary, "runner", jobs)
    started, release = threading.Event(), threading.Event()
    calls = []
    def resume(self, run_id, project_path=None, **kwargs):
        calls.append((run_id, Path(project_path), kwargs))
        started.set()
        assert release.wait(3)
        manifest.status = "completed"
        manifest.error = None
        path.write_text(json.dumps(manifest.to_dict()))
        return manifest
    monkeypatch.setattr(PipelineRunner, "resume", resume)
    client = _client(tmp_path)
    try:
        first = client.post(f"/api/projects/book/tasks/{manifest.run_id}/resume")
        assert first.status_code == 202
        assert started.wait(1)
        second = client.post(f"/api/projects/book/tasks/{manifest.run_id}/resume")
        assert second.json()["job_id"] == first.json()["job_id"]
        detail = client.get(f"/api/projects/book/tasks/{manifest.run_id}").json()
        assert detail["status"] == "running" and detail["can_resume"] is False
        assert detail["resume_job_id"] == first.json()["job_id"]
        card = client.get("/api/projects").json()[0]
        assert card["status"] == "running" and card["task_can_resume"] is False
    finally:
        release.set()
    assert _wait_job(jobs, first.json()["job_id"])["status"] == "done"
    assert calls == [(manifest.run_id, project.resolve(), {})]
    assert manifest.spec.approval_policy == "review_required"
    assert client.get(f"/api/projects/book/tasks/{manifest.run_id}").json()["status"] == "completed"


@pytest.mark.parametrize("native_status", ["failed", "paused"])
def test_native_failure_is_not_successful_background_job(tmp_path, monkeypatch, native_status):
    from api import project_tasks, project_task_summary
    from pipeline_runner import PipelineRunner
    _, manifest, _ = _seed(tmp_path)
    jobs = JobRunner()
    monkeypatch.setattr(project_tasks, "runner", jobs)
    monkeypatch.setattr(project_task_summary, "runner", jobs)
    manifest.status = native_status
    monkeypatch.setattr(PipelineRunner, "resume", lambda *a, **kw: manifest)
    client = _client(tmp_path)
    response = client.post(f"/api/projects/book/tasks/{manifest.run_id}/resume")
    assert response.status_code == 202
    job = _wait_job(jobs, response.json()["job_id"])
    assert job["status"] == "error"
    assert manifest.error in job["error"]
    detail = client.get(f"/api/projects/book/tasks/{manifest.run_id}").json()
    assert detail["resume_job_status"] == "error"
    assert manifest.error in detail["resume_job_error"]


def test_task_lookup_never_crosses_project_or_follows_symlinks(tmp_path):
    project, manifest, _ = _seed(tmp_path)
    other, _, _ = _seed(tmp_path, "other", "separate")
    client = _client(tmp_path)
    assert client.get(f"/api/projects/other/tasks/{manifest.run_id}").status_code == 404
    (other / "outputs/runs" / manifest.run_id).symlink_to(project / "outputs/runs" / manifest.run_id)
    assert client.get(f"/api/projects/other/tasks/{manifest.run_id}").status_code == 404
    assert client.post(f"/api/projects/other/tasks/{manifest.run_id}/resume").status_code == 404
    assert client.post("/api/projects/book/tasks/bad:run/resume").status_code == 404


@pytest.mark.parametrize("status,dry_run", [("completed", False), ("running", False), ("cancelled", False), ("failed", True)])
def test_resume_rejects_non_resumable_tasks(tmp_path, status, dry_run):
    _, manifest, path = _seed(tmp_path, dry_run=dry_run)
    manifest.status = status
    path.write_text(json.dumps(manifest.to_dict()))
    response = _client(tmp_path).post(f"/api/projects/book/tasks/{manifest.run_id}/resume")
    assert response.status_code == 409


def test_other_project_job_blocks_resume(tmp_path, monkeypatch):
    from api import project_tasks, project_task_summary
    project, manifest, _ = _seed(tmp_path)
    jobs = JobRunner()
    monkeypatch.setattr(project_tasks, "runner", jobs)
    monkeypatch.setattr(project_task_summary, "runner", jobs)
    release = threading.Event()
    jobs.submit("write", lambda: release.wait(3), {"project_id": "book", "project_path": str(project.resolve())})
    try:
        client = _client(tmp_path)
        assert client.post(f"/api/projects/book/tasks/{manifest.run_id}/resume").status_code == 409
        assert client.get(f"/api/projects/book/tasks/{manifest.run_id}").json()["can_resume"] is False
    finally:
        release.set()


def test_resume_never_presents_crashed_execution_as_current_activity(tmp_path, monkeypatch):
    from api import project_tasks, project_task_summary
    project, manifest, path = _seed(tmp_path)
    progress = path.parent / "progress.json"
    progress.write_text(json.dumps({"schema_version": 1, "run_id": manifest.run_id,
        "execution_id": "old-execution", "status": "running", "started_at": "2020-01-01T00:00:00Z",
        "request": {"request_id": "old-request", "status": "running", "output_chars": 999}}))
    jobs = JobRunner()
    monkeypatch.setattr(project_tasks, "runner", jobs)
    monkeypatch.setattr(project_task_summary, "runner", jobs)
    release = threading.Event()
    jobs.submit("novel.resume", lambda: release.wait(3), {"project_id": "book",
        "project_path": str(project.resolve()), "run_id": manifest.run_id})
    try:
        result = _client(tmp_path).get(f"/api/projects/book/tasks/{manifest.run_id}").json()
        assert result["status"] == "running"
        assert result["progress"] is None
        assert json.loads(progress.read_text())["request"]["output_chars"] == 999
    finally:
        release.set()


def test_resume_start_error_is_visible_without_rewriting_original_failure(tmp_path, monkeypatch):
    from api import project_tasks, project_task_summary
    from pipeline_runner import PipelineRunner
    _, manifest, path = _seed(tmp_path)
    before = path.read_bytes()
    jobs = JobRunner()
    monkeypatch.setattr(project_tasks, "runner", jobs)
    monkeypatch.setattr(project_task_summary, "runner", jobs)
    def locked(*args, **kwargs):
        raise RuntimeError("Project pipeline is already active; cannot acquire execution lock")
    monkeypatch.setattr(PipelineRunner, "resume", locked)
    client = _client(tmp_path)
    response = client.post(f"/api/projects/book/tasks/{manifest.run_id}/resume")
    assert response.status_code == 202
    _wait_job(jobs, response.json()["job_id"])
    detail = client.get(f"/api/projects/book/tasks/{manifest.run_id}").json()
    assert detail["resume_job_status"] == "error"
    assert "cannot acquire execution lock" in detail["resume_job_error"]
    assert detail["error"] == manifest.error
    assert path.read_bytes() == before
    # If a later native CLI resume completes this run, its authoritative result
    # must not keep displaying the older failed HTTP attempt.
    manifest.status = "completed"
    manifest.error = None
    path.write_text(json.dumps(manifest.to_dict()))
    completed = client.get(f"/api/projects/book/tasks/{manifest.run_id}").json()
    assert completed["status"] == "completed"
    assert completed["resume_job_error"] is None


def test_historical_run_cannot_replace_latest_story(tmp_path):
    project, manifest, path = _seed(tmp_path)
    historical = manifest.to_dict()
    historical.update(run_id="older", created_at="2020-01-01T00:00:00Z")
    old = project / "outputs/runs/older/run.json"
    old.parent.mkdir()
    old.write_text(json.dumps(historical))
    before = path.read_bytes()
    client = _client(tmp_path)
    assert client.get("/api/projects/book/tasks/older").json()["can_resume"] is False
    assert client.post("/api/projects/book/tasks/older/resume").status_code == 409
    assert path.read_bytes() == before


def test_workspace_resolution_and_jobs_remain_isolated(tmp_path, monkeypatch):
    from api import db, project_tasks, project_task_summary
    from api.routes import get_service
    first, _, _ = _seed(tmp_path, run_id="default-run")
    second, manifest, _ = _seed(tmp_path / "ws-second", run_id="workspace-run")
    app = create_app(projects_root=tmp_path, db_url=f"sqlite:///{tmp_path / 'test.db'}")
    workspace = db.workspace_create(id="second", name="Second", slug="second")
    app.dependency_overrides[get_service] = lambda: ProjectService(tmp_path, workspace=workspace)
    jobs = JobRunner()
    monkeypatch.setattr(project_tasks, "runner", jobs)
    monkeypatch.setattr(project_task_summary, "runner", jobs)
    release = threading.Event()
    jobs.submit("write", lambda: release.wait(3), {"project_id": "book", "project_path": str(first.resolve())})
    try:
        client = TestClient(app)
        assert client.get("/api/projects/book/tasks/default-run").status_code == 404
        assert client.post("/api/projects/book/tasks/default-run/resume").status_code == 404
        detail = client.get(f"/api/projects/book/tasks/{manifest.run_id}")
        assert detail.status_code == 200
        assert detail.json()["can_resume"] is True
    finally:
        release.set()


@pytest.mark.parametrize("field,value", [("error", {"broken": True}), ("current_phase", [])])
def test_corrupt_failure_fields_do_not_break_library(tmp_path, field, value):
    _, manifest, path = _seed(tmp_path)
    data = manifest.to_dict()
    data[field] = value
    path.write_text(json.dumps(data))
    client = _client(tmp_path)
    row = client.get("/api/projects").json()[0]
    assert row["task_can_resume"] is False
    assert row["task_error" if field == "error" else "task_phase"] is None
    assert client.get(f"/api/projects/book/tasks/{manifest.run_id}").status_code == 409
    assert client.post(f"/api/projects/book/tasks/{manifest.run_id}/resume").status_code == 409
