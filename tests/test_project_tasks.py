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
