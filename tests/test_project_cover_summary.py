"""Library statuses distinguish writing, image production, review and selection."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api import cover_generation_tasks
from api.main import create_app
from api.project_cover_summary import project_cover_summary
from api.services import ProjectService
from core.cover_models import CoverSet
from core.cover_store import CoverStore
from tests.test_cover_service import _brief, _concepts
from tests.test_project_task_summary import _project


def _cover(project, *, quality="human_review_required", candidate_status="ready", selected=False,
           created="2026-10-10T00:00:00Z", statuses=None):
    cover = CoverSet.new(project.name, _brief(), _concepts())
    candidates = []
    for index, item in enumerate(cover.candidates):
        path = project / "outputs" / "covers" / "images" / f"{cover.cover_set_id}-{index}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"saved-image")
        status = statuses[index] if statuses else "selected" if selected and index == 0 else candidate_status
        candidates.append(replace(item, status=status, relative_path=path.relative_to(project).as_posix(),
            media_id=f"media-{index}", sha256="a" * 64, content_type="image/jpeg", provider="test",
            model="fixture", width=1024, height=1536, generation_prompt="approved fixture",
            quality_report={"status": quality} if quality else None))
    cover = replace(cover, candidates=tuple(candidates), status="selected" if selected else "ready",
                    selected_candidate_id=candidates[0].candidate_id if selected else "",
                    created_at=created, updated_at=created)
    store = CoverStore(project)
    saved = store.create(cover)
    if selected:
        store.set_active(saved.cover_set_id, expected_revision=store.active().revision)
    return saved


def _summary(project):
    return project_cover_summary(project, project.name, workspace_id="default")


def _task(project, **overrides):
    task_id = "generation-task-" + "a" * 32
    value = {"schema_version": 1, "task_id": task_id, "owner_scope": f"default:project:{project.name}",
             "created_at": "2026-10-11T00:00:00Z", "updated_at": "2026-10-11T01:00:00Z",
             "finished_at": None, "status": "running", "job_id": "job-1", "process_id": "previous-process", **overrides}
    path = project / "outputs" / "covers" / "generation_tasks" / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return value


def test_empty_projection_is_read_only_and_independent_of_completed_manuscript(tmp_path):
    project = _project(tmp_path, "book", status="completed")
    before = set(project.rglob("*"))
    summary = ProjectService(tmp_path).list_projects()[0]
    assert summary.status == "completed"
    assert summary.cover.status == "not_started"
    assert summary.cover.image_url is None
    assert set(project.rglob("*")) == before


@pytest.mark.parametrize("quality", [None, "human_review_required", "recommended_for_human_review"])
def test_image_is_only_awaiting_review_until_explicitly_selected(tmp_path, quality):
    project = _project(tmp_path, "book")
    cover = _cover(project, quality=quality)
    result = _summary(project)
    assert result.status == "awaiting_review"
    assert result.image_kind == "candidate"
    assert result.cover_set_id == cover.cover_set_id
    assert result.candidate_count == 4
    assert result.image_url == "/api/projects/book/media/media-0/raw"


@pytest.mark.parametrize("options", [
    {"quality": "blocked"}, {"statuses": ["failed", "ready", "ready", "ready"]},
    {"statuses": ["pending", "ready", "ready", "ready"]},
    {"statuses": ["rejected"] * 4},
])
def test_quality_failure_or_unfinished_attempt_needs_attention(tmp_path, options):
    project = _project(tmp_path, "book")
    _cover(project, **options)
    assert _summary(project).status == "needs_attention"


def test_selected_cover_requires_active_pointer_and_existing_image(tmp_path):
    project = _project(tmp_path, "book")
    cover = _cover(project, selected=True)
    assert _summary(project).status == "selected"
    assert _summary(project).image_kind == "selected"
    (project / cover.candidates[0].relative_path).unlink()
    result = _summary(project)
    assert result.status == "needs_attention"
    assert result.image_kind == "candidate"
    assert result.candidate_id != cover.selected_candidate_id


def test_new_blocked_batch_does_not_replace_formal_selection_preview(tmp_path):
    project = _project(tmp_path, "book")
    selected = _cover(project, selected=True)
    _cover(project, quality="blocked", created="2026-10-11T00:00:00Z")
    result = _summary(project)
    assert result.status == "needs_attention"
    assert result.cover_set_id == selected.cover_set_id
    assert result.image_kind == "selected"
    assert "保留原选定" in result.detail


def test_actual_live_cover_job_overrides_old_completed_images(tmp_path, monkeypatch):
    project = _project(tmp_path, "book")
    _cover(project, selected=True)
    def running(project_id, *, project_path):
        assert project_id == "book" and project_path == str(project.resolve())
        return [{"kind": "cover.repair", "status": "running"}]
    monkeypatch.setattr("api.project_cover_summary.runner", SimpleNamespace(running_for_project=running))
    result = _summary(project)
    assert result.status == "processing"
    assert result.image_kind == "selected"


def test_old_process_running_record_is_interrupted_not_processing(tmp_path):
    project = _project(tmp_path, "book")
    _task(project)
    result = _summary(project)
    assert result.status == "needs_attention"
    assert "中断" in result.detail


def test_completed_generation_record_does_not_turn_blocked_candidates_into_success(tmp_path):
    project = _project(tmp_path, "book")
    _cover(project, quality="blocked")
    _task(project, status="completed")
    assert _summary(project).status == "needs_attention"


def test_completed_direction_task_still_shows_saved_direction_needing_confirmation(tmp_path):
    project = _project(tmp_path, "book")
    _task(project, status="completed")
    path = project / "outputs" / "covers" / "directions" / ("direction-" + "b" * 32 + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"direction_id": path.stem, "status": "awaiting_approval", "created_at": "2026-10-11T00:05:00Z"}))
    assert _summary(project).status == "awaiting_review"


@pytest.mark.parametrize("alias", ["sets", "images"])
def test_never_projects_foreign_images_through_symlinks(tmp_path, alias):
    project = _project(tmp_path, "book")
    _cover(project)
    folder = project / "outputs" / "covers" / alias
    foreign = tmp_path / f"external-{alias}"
    folder.rename(foreign)
    folder.symlink_to(foreign, target_is_directory=True)
    result = _summary(project)
    assert result.status == "needs_attention"
    assert result.image_url is None


def test_broken_cover_file_does_not_hide_the_project_or_other_books(tmp_path):
    project = _project(tmp_path, "book")
    _project(tmp_path, "other")
    path = project / "outputs" / "covers" / "sets" / ("cover-" + "c" * 32 + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken")
    rows = {row.id: row for row in ProjectService(tmp_path).list_projects()}
    assert len(rows) == 2
    assert rows["book"].cover.status == "needs_attention"
    assert rows["other"].cover.status == "not_started"


def test_project_list_and_detail_expose_same_independent_cover_projection(tmp_path):
    project = _project(tmp_path, "book", status="completed")
    _cover(project, quality="blocked")
    app = create_app(projects_root=tmp_path, db_url=f"sqlite:///{tmp_path / 'test.db'}")
    client = TestClient(app)
    listed = client.get("/api/projects").json()[0]
    detailed = client.get("/api/projects/book").json()
    assert listed["status"] == detailed["status"] == "completed"
    assert listed["cover"] == detailed["cover"]
    assert listed["cover"]["status"] == "needs_attention"

@pytest.mark.parametrize("status,expected", [("awaiting_approval", "awaiting_review"), ("failed", "needs_attention"), ("planning", "needs_attention")])
def test_repair_task_is_visible_after_leaving_the_studio(tmp_path, status, expected):
    project = _project(tmp_path, "book")
    parent = _cover(project, selected=True)
    direction = _direction_record(project, repair=True)
    task_id = "repair-task-" + "d" * 32
    path = project / "outputs" / "covers" / "repair_tasks" / f"{task_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "task_id": task_id, "owner_scope": "default:project:book",
        "status": status, "kind": "design", "image_calls": 1, "parent_cover_set_id": parent.cover_set_id,
        "parent_candidate_id": parent.candidates[0].candidate_id, "parent_sha256": "a" * 64, "concept_id": parent.concepts[0].concept_id,
        "direction_id": direction["direction_id"],
        "created_at": "2026-10-11T00:00:00Z", "updated_at": "2026-10-11T01:00:00Z", "process_id": "old-process", "job_id": "old-job"}))
    result = _summary(project)
    assert result.status == expected
    assert result.image_kind == "selected"


def test_failure_after_batch_creation_is_not_hidden_by_saved_images(tmp_path):
    project = _project(tmp_path, "book")
    _cover(project, created="2026-10-11T00:05:00Z")
    _task(project, status="failed", finished_at="2026-10-11T01:05:00Z")
    assert _summary(project).status == "needs_attention"


def _direction_record(project, *, status="awaiting_approval", repair=False):
    path = project / "outputs" / "covers" / "directions" / ("direction-" + "e" * 32 + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {"direction_id": path.stem, "status": status, "created_at": "2026-10-11T02:00:00Z"}
    if repair:
        value["source_binding"] = {"single_candidate_repair": {"concept_id": "concept-1"}}
    path.write_text(json.dumps(value))
    return value


@pytest.mark.parametrize("status", ["stale", "rejected", "unknown", []])
def test_unusable_new_direction_does_not_offer_confirmation(tmp_path, status):
    project = _project(tmp_path, "book")
    _cover(project, selected=True)
    _direction_record(project, status=status)
    result = _summary(project)
    assert result.status == "needs_attention"
    assert result.image_kind == "selected"
    assert "保留原选定" in result.detail


def test_orphan_single_candidate_direction_cannot_be_normally_approved(tmp_path):
    project = _project(tmp_path, "book")
    _cover(project, selected=True)
    _direction_record(project, repair=True)
    result = _summary(project)
    assert result.status == "needs_attention"
    assert "修复任务" in result.detail


@pytest.mark.parametrize("direction_status,expected", [("awaiting_approval", "awaiting_review"), ("stale", "needs_attention"), ("rejected", "needs_attention")])
def test_repair_proposal_uses_actual_direction_availability(tmp_path, direction_status, expected):
    project = _project(tmp_path, "book")
    parent = _cover(project, selected=True)
    direction = _direction_record(project, status=direction_status, repair=True)
    task_id = "repair-task-" + "d" * 32
    path = project / "outputs" / "covers" / "repair_tasks" / f"{task_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "task_id": task_id, "owner_scope": "default:project:book",
        "status": "awaiting_approval", "kind": "design", "image_calls": 1, "parent_cover_set_id": parent.cover_set_id,
        "parent_candidate_id": parent.candidates[0].candidate_id, "parent_sha256": "a" * 64, "concept_id": parent.concepts[0].concept_id,
        "direction_id": direction["direction_id"], "created_at": "2026-10-11T01:00:00Z", "updated_at": "2026-10-11T03:00:00Z", "process_id": "old-process", "job_id": "old-job"}))
    result = _summary(project)
    assert result.status == expected
    if expected == "awaiting_review":
        assert "单张修复" in result.detail
    assert result.image_kind == "selected"


def test_approved_standard_direction_keeps_generation_as_the_next_action(tmp_path):
    project = _project(tmp_path, "book")
    _direction_record(project, status="approved")
    result = _summary(project)
    assert result.status == "awaiting_review"
    assert "可生成封面候选" in result.detail


def test_old_rejected_direction_does_not_override_newer_selected_cover(tmp_path):
    project = _project(tmp_path, "book")
    _direction_record(project, status="rejected")
    _cover(project, selected=True, created="2026-10-11T03:00:00Z")
    assert _summary(project).status == "selected"
