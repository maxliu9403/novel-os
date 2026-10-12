"""Cards must report durable novel task outcomes, not transport/default status."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.services import ProjectService


def _project(root: Path, name: str, *, chapter_statuses=(), **metadata) -> Path:
    project = root / name
    state = project / "outputs" / "state" / "story_state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({
        "metadata": {"title": name, "genre": "Drama", "author": "A", **metadata},
        "chapters": {str(i): {"number": i, "status": status, "word_count": 500}
                     for i, status in enumerate(chapter_statuses, 1)},
    }))
    return project


def _run(project: Path, run_id: str, status: str, created: str | None, **extra) -> Path:
    manifest = project / "outputs" / "runs" / run_id / "run.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "run_id": run_id, "status": status, "created_at": created,
        "spec": {"dry_run": False}, **extra,
    }))
    return manifest


def test_latest_production_run_supplies_status_id_and_immutable_start(tmp_path):
    project = _project(tmp_path, "book")
    old = _run(project, "old", "failed", "2026-10-01T09:00:00Z",
               updated_at="2026-10-20T09:00:00Z")
    _run(project, "latest", "completed", "2026-10-02T09:00:00Z")
    _run(project, "dry", "completed", "2026-10-03T09:00:00Z", spec={"dry_run": True})
    os.utime(old, (2_000_000_000, 2_000_000_000))
    service = ProjectService(tmp_path)
    for result in (service.list_projects()[0], service.project_detail("book")):
        assert result.status == "completed"
        assert result.task_id == "latest"
        assert result.task_started_at == "2026-10-02T09:00:00+00:00"


def test_completed_chapters_do_not_hide_failed_delivery(tmp_path):
    project = _project(tmp_path, "book", chapter_statuses=("complete", "complete"), status="completed")
    _run(project, "publication-failed", "failed", "2026-10-08T09:09:06Z",
         current_phase="publication.copy")
    result = ProjectService(tmp_path).list_projects()[0]
    assert result.status == "failed"
    assert result.drafted_count == result.chapter_count == 2


def test_api_orders_by_task_start_not_name_mtime_or_update(tmp_path):
    oldest = _project(tmp_path, "a-oldest")
    newer = _project(tmp_path, "z-newer")
    tied = _project(tmp_path, "b-tied")
    _run(oldest, "old", "completed", "2026-10-08T10:00:00+08:00",
         updated_at="2026-11-01T00:00:00Z")
    _run(newer, "new", "failed", "2026-10-08T03:00:00Z")
    _run(tied, "tie", "paused", "2026-10-08T03:00:00Z")
    _project(tmp_path, "a-new-draft", created="2026-12-01T00:00:00")
    _project(tmp_path, "b-old-draft", created="2026-01-01T00:00:00")
    app = create_app(projects_root=tmp_path, db_url=f"sqlite:///{tmp_path / 'test.db'}")
    response = TestClient(app).get("/api/projects")
    assert response.status_code == 200
    rows = response.json()
    assert [row["id"] for row in rows] == ["b-tied", "z-newer", "a-oldest", "a-new-draft", "b-old-draft"]
    assert rows[0]["task_id"] == "tie"
    assert rows[-1]["task_id"] is None
    assert rows[-1]["task_started_at"] is None


@pytest.mark.parametrize(("chapters", "metadata", "expected"), [
    ((), {}, "draft"),
    (("drafted",), {}, "in_progress"),
    (("complete", "complete"), {"target_chapters": 2}, "completed"),
    (("complete",), {"target_chapters": 2}, "in_progress"),
    (("complete",), {"status": "failed"}, "failed"),
])
def test_legacy_project_has_honest_status_without_invented_task(tmp_path, chapters, metadata, expected):
    _project(tmp_path, "book", chapter_statuses=chapters, **metadata)
    result = ProjectService(tmp_path).list_projects()[0]
    assert result.status == expected
    assert result.task_id is None
    assert result.task_started_at is None


def test_invalid_manifest_does_not_break_library_and_dry_run_is_not_completion(tmp_path):
    project = _project(tmp_path, "book")
    broken = _run(project, "broken", "completed", "2026-10-08T00:00:00Z")
    broken.write_text("{partial")
    _run(project, "dry", "completed", "2026-10-09T00:00:00Z", spec={"dry_run": True})
    result = ProjectService(tmp_path).list_projects()[0]
    assert result.status == "draft"
    assert result.task_id is None


def test_missing_start_is_not_replaced_by_update_or_file_mtime(tmp_path):
    project = _project(tmp_path, "book", created="2026-10-01T00:00:00Z")
    _run(project, "legacy", "completed", None, updated_at="2026-10-09T00:00:00Z")
    result = ProjectService(tmp_path).list_projects()[0]
    assert result.task_id == "legacy"
    assert result.task_started_at is None


@pytest.mark.parametrize("invalid_status", [[], {}])
def test_malformed_status_does_not_break_other_projects(tmp_path, invalid_status):
    project = _project(tmp_path, "book", status=invalid_status)
    _run(project, "broken", invalid_status, "2026-10-08T00:00:00Z")
    result = ProjectService(tmp_path).list_projects()[0]
    assert result.status == "draft"
    assert result.task_id is None


def test_reading_running_task_never_probes_or_competes_for_execution_lock(tmp_path, monkeypatch):
    fcntl = pytest.importorskip("fcntl")
    project = _project(tmp_path, "book")
    manifest = _run(project, "live", "running", "2026-10-08T00:00:00Z")
    before = manifest.read_bytes()
    lock = project / "outputs" / ".pipeline-execution.lock"
    lock.touch()

    def reject_lock(*args):
        pytest.fail("Project listing must not compete with pipeline start/resume for its lock")

    monkeypatch.setattr(fcntl, "flock", reject_lock)
    assert ProjectService(tmp_path).list_projects()[0].status == "running"
    assert manifest.read_bytes() == before


def test_lock_unavailable_does_not_invent_an_interruption(tmp_path):
    project = _project(tmp_path, "book")
    _run(project, "legacy", "running", "2026-10-08T00:00:00Z")
    assert ProjectService(tmp_path).list_projects()[0].status == "running"
