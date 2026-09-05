"""Permanent project deletion spans files, media, DB projections and jobs."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api import db
from api.jobs import ProjectJobBlocked, runner
from api.main import create_app
from api.media import LocalMediaStore, digest
from api.services import ProjectService


def _seed_project(root: Path, project_id: str = "delete-me") -> Path:
    project = root / project_id
    state_dir = project / "outputs" / "state"
    manuscript = project / "outputs" / "manuscript"
    state_dir.mkdir(parents=True)
    manuscript.mkdir(parents=True)
    state = {
        "metadata": {
            "title": "要删除的小说",
            "genre": "悬疑",
            "author": "Test",
        },
        "characters": {},
        "plot_threads": {},
        "chapters": {
            "1": {
                "number": 1,
                "title": "第一章",
                "status": "complete",
                "word_count": 2,
                "pov_character": "林澈",
            },
            "2": {
                "number": 2,
                "title": "第二章",
                "status": "drafted",
                "word_count": 2,
                "pov_character": "林澈",
            },
        },
        "timeline": [],
        "style_profile": {},
        "session_log": [],
    }
    (state_dir / "story_state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    (project / "outputs" / "chapter_001_outline.md").write_text(
        "# 第一章大纲", encoding="utf-8"
    )
    (manuscript / "chapter_001_draft.md").write_text(
        "draft words", encoding="utf-8"
    )
    (manuscript / "chapter_002_draft.md").write_text(
        "more words", encoding="utf-8"
    )
    (project / "outputs" / "deliverables").mkdir()
    (project / "outputs" / "deliverables" / "book-package.zip").write_bytes(
        b"delivery"
    )
    return project


def _client(tmp_path: Path, project_id: str = "delete-me") -> tuple[TestClient, Path, Path]:
    projects = tmp_path / "projects"
    media = tmp_path / "media"
    _seed_project(projects, project_id)
    app = create_app(
        projects_root=projects,
        media_root=media,
        db_url=f"sqlite:///{(tmp_path / 'delete.db').as_posix()}",
    )
    return TestClient(app), projects, media


def _seed_every_projection(projects: Path, media: Path, project_id: str) -> None:
    db.ingest_project(projects, project_id)
    db.project_claim(project_id, "default")
    db.snapshot_create(project_id, 1, "snapshot", "Before delete", "final")
    db.comment_add(project_id, 1, "editor note")

    blob = b"project media bytes"
    sha = digest(blob)
    LocalMediaStore(media).put(project_id, sha, ".png", blob)
    db.media_add(
        project_id, sha, ".png", "cover.png", "image/png", len(blob), 1, 1,
        "cover",
    )

    # Seed the append-only projections explicitly.  These tables deliberately
    # have no ORM relationships/cascades, which is the condition deletion must
    # handle in both SQLite and Postgres.
    with db._session() as session:  # noqa: SLF001 - verifies DB boundary itself
        session.add(db.ArtifactRevisionProjection(
            id=f"{project_id}:rev-1", project_id=project_id,
            revision_id="rev-1", chapter=1, kind="final", sha256="a" * 64,
            byte_length=8, source="test", created_at="2026-01-01T00:00:00Z",
        ))
        session.add(db.EvaluationReportProjection(
            id=f"{project_id}:report-1", project_id=project_id,
            report_id="report-1", chapter=1, artifact_revision_id="rev-1",
            artifact_sha256="a" * 64, evaluation_id="evaluation-1",
            rubric_version="1", prompt_sha256="b" * 64,
            evaluator_provider="test", evaluator_model="test", status="pass",
            created_at="2026-01-01T00:00:00Z",
        ))
        session.add(db.QualityFindingProjection(
            id=f"{project_id}:finding-1", project_id=project_id,
            report_id="report-1", finding_id="finding-1", chapter=1,
            artifact_sha256="a" * 64, category="continuity", severity="info",
            message="note",
        ))
        session.add(db.PromotionReceiptProjection(
            id=f"{project_id}:receipt-1", project_id=project_id,
            receipt_id="receipt-1", chapter=1, request_id="request-1",
            idempotency_key="key-1", new_artifact_revision_id="rev-1",
            new_artifact_sha256="a" * 64, old_canon_sha="b" * 64,
            new_canon_sha="c" * 64, canon_proposal_id="proposal-1",
            evaluation_report_id="report-1", actor="test", reason="test",
            committed_at="2026-01-01T00:00:00Z",
        ))
        session.commit()


def _delete(
    client: TestClient,
    project_id: str,
    confirm_title: str = "要删除的小说",
) -> Any:
    return client.delete(
        f"/api/projects/{project_id}",
        params={"confirm_title": confirm_title},
    )


def test_preview_and_delete_remove_all_project_data(tmp_path: Path) -> None:
    project_id = "delete-everything"
    client, projects, media = _client(tmp_path, project_id)
    _seed_every_projection(projects, media, project_id)
    ProjectService._consequence_previews.update({
        "owned-preview": {"project_id": project_id, "chapter": 1},
        "other-preview": {"project_id": "another-book", "chapter": 1},
    })

    preview_response = client.get(f"/api/projects/{project_id}/deletion-preview")
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["title"] == "要删除的小说"
    assert preview["chapter_count"] == 2
    assert preview["can_delete"] is True
    assert preview["running_job_ids"] == []
    assert preview["counts"] == {
        "quality_findings": 1,
        "evaluation_reports": 1,
        "promotion_receipts": 1,
        "artifact_revisions": 1,
        "artifacts": 3,
        "snapshots": 1,
        "comments": 1,
        "media": 1,
        "chapters": 2,
        "projects": 1,
        "ownerships": 1,
    }
    assert preview["project_files"] >= 5
    assert preview["project_bytes"] > 0
    assert preview["media_files"] == 1
    assert preview["media_bytes"] == len(b"project media bytes")

    response = _delete(client, project_id)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "deleted"
    assert result["before"] == {
        key: preview[key]
        for key in (
            "project_id", "title", "chapter_count", "exists", "counts",
            "project_files", "project_bytes", "media_files", "media_bytes",
        )
    }
    assert result["after"]["exists"] is False
    assert not any(result["after"]["counts"].values())
    assert result["after"]["project_bytes"] == 0
    assert result["after"]["media_bytes"] == 0
    assert result["cleared_consequence_previews"] == 1
    assert not (projects / project_id).exists()
    assert not (media / project_id).exists()
    assert "owned-preview" not in ProjectService._consequence_previews
    assert "other-preview" in ProjectService._consequence_previews

    # A callable prepared just before deletion cannot be submitted afterward
    # to recreate project files behind the Library's back.
    with pytest.raises(ProjectJobBlocked):
        runner.submit(
            "write", lambda: None,
            meta={
                "project_id": project_id,
                "project_path": str((projects / project_id).resolve()),
            },
        )

    # DELETE is retry-safe even though the canonical state and DB ownership no
    # longer exist.
    repeated = _delete(client, project_id)
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "already_deleted"


def test_delete_rejects_running_project_job_without_removing_data(tmp_path: Path) -> None:
    project_id = "busy-delete"
    client, projects, _media = _client(tmp_path, project_id)
    project_path = str((projects / project_id).resolve())
    started = threading.Event()
    release = threading.Event()

    def work() -> None:
        started.set()
        release.wait(timeout=5)

    job_id = runner.submit(
        "write", work,
        meta={"project_id": project_id, "project_path": project_path},
    )
    assert started.wait(timeout=1)
    try:
        preview = client.get(f"/api/projects/{project_id}/deletion-preview").json()
        assert preview["can_delete"] is False
        assert preview["running_job_ids"] == [job_id]

        response = _delete(client, project_id)
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "project_jobs_running",
            "message": "项目仍有运行中的任务，请等待任务完成后重试。",
            "running_job_ids": [job_id],
        }
        assert (projects / project_id).exists()
    finally:
        release.set()

    deadline = time.monotonic() + 2
    while runner.get(job_id)["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.get(job_id)["status"] == "done"
    assert _delete(client, project_id).status_code == 200


def test_delete_rejects_unsafe_project_id(tmp_path: Path) -> None:
    client, projects, _media = _client(tmp_path, "safe-book")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")

    response = _delete(client, "bad:id")

    assert response.status_code == 404
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (projects / "safe-book").exists()


def test_delete_refuses_symlink_alias_to_another_project(tmp_path: Path) -> None:
    client, projects, _media = _client(tmp_path, "canonical-book")
    alias = projects / "alias-book"
    try:
        alias.symlink_to(projects / "canonical-book", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available on this platform")

    response = _delete(client, "alias-book")

    assert response.status_code == 404
    assert alias.is_symlink()
    assert (projects / "canonical-book" / "outputs" / "state" / "story_state.json").is_file()


def test_delete_enforces_project_workspace_ownership(tmp_path: Path) -> None:
    project_id = "other-workspace-book"
    client, projects, _media = _client(tmp_path, project_id)
    db.project_claim(project_id, "another-workspace")

    response = _delete(client, project_id)

    assert response.status_code == 404
    assert (projects / project_id / "outputs" / "state" / "story_state.json").is_file()
    assert db.project_workspace(project_id) == "another-workspace"


def test_delete_requires_current_canonical_title(tmp_path: Path) -> None:
    project_id = "confirm-title"
    client, projects, _media = _client(tmp_path, project_id)

    missing = client.delete(f"/api/projects/{project_id}")
    assert missing.status_code == 422

    mismatch = _delete(client, project_id, "错误标题")
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"] == {
        "code": "project_title_confirmation_mismatch",
        "message": "小说标题已变化或确认标题不匹配，请重新预览后再删除。",
        "current_title": "要删除的小说",
    }
    assert (projects / project_id).exists()


def test_preview_title_cannot_authorize_delete_after_rename(tmp_path: Path) -> None:
    project_id = "renamed-after-preview"
    client, projects, _media = _client(tmp_path, project_id)

    preview = client.get(f"/api/projects/{project_id}/deletion-preview").json()
    assert preview["title"] == "要删除的小说"
    renamed = client.patch(
        f"/api/projects/{project_id}",
        json={"title": "改名后的小说"},
    )
    assert renamed.status_code == 200

    stale = _delete(client, project_id, preview["title"])
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_title"] == "改名后的小说"
    assert (projects / project_id).exists()

    assert _delete(client, project_id, "改名后的小说").status_code == 200


def test_delete_waits_for_inflight_sync_write_and_rechecks_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "write-before-delete"
    client, projects, _media = _client(tmp_path, project_id)
    write_started = threading.Event()
    release_write = threading.Event()
    original_update = ProjectService.update_project

    def blocking_update(
        self: ProjectService,
        target_id: str,
        **fields: Any,
    ) -> Any:
        write_started.set()
        assert release_write.wait(timeout=5)
        return original_update(self, target_id, **fields)

    monkeypatch.setattr(ProjectService, "update_project", blocking_update)
    results: dict[str, Any] = {}

    def update_in_background() -> None:
        results["update"] = client.patch(
            f"/api/projects/{project_id}",
            json={"title": "并发改名"},
        )

    def delete_in_background() -> None:
        results["delete"] = _delete(client, project_id)

    update_thread = threading.Thread(target=update_in_background)
    update_thread.start()
    assert write_started.wait(timeout=2)
    delete_thread = threading.Thread(target=delete_in_background)
    delete_thread.start()

    # A yield dependency keeps the mutation lease for the complete sync
    # handler execution, so deletion cannot read a mid-update title.
    time.sleep(0.05)
    assert delete_thread.is_alive()
    release_write.set()
    update_thread.join(timeout=5)
    delete_thread.join(timeout=5)

    assert results["update"].status_code == 200
    assert results["delete"].status_code == 409
    assert results["delete"].json()["detail"]["current_title"] == "并发改名"
    assert (projects / project_id).exists()
    assert _delete(client, project_id, "并发改名").status_code == 200


def test_deletion_blocks_sync_writes_and_same_id_recreation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "要删除的小说"
    client, projects, _media = _client(tmp_path, project_id)
    deletion_started = threading.Event()
    release_deletion = threading.Event()
    original_delete_project = LocalMediaStore.delete_project

    def blocking_delete_project(self: LocalMediaStore, target_id: str) -> bool:
        deletion_started.set()
        assert release_deletion.wait(timeout=5)
        return original_delete_project(self, target_id)

    monkeypatch.setattr(LocalMediaStore, "delete_project", blocking_delete_project)
    result: dict[str, Any] = {}

    def delete_in_background() -> None:
        result["response"] = _delete(client, project_id)

    deletion_thread = threading.Thread(target=delete_in_background)
    deletion_thread.start()
    assert deletion_started.wait(timeout=2)

    # One route-level gate covers canonical metadata, chapter prose, snapshots,
    # comments and media instead of requiring a check in every handler.
    mutations = [
        client.patch(
            f"/api/projects/{project_id}", json={"title": "不应写入"}
        ),
        client.put(
            f"/api/projects/{project_id}/chapters/1/final",
            json={"text": "不应写入"},
        ),
        client.post(
            f"/api/projects/{project_id}/chapters/1/snapshots",
            json={"label": "不应创建"},
        ),
        client.post(
            f"/api/projects/{project_id}/chapters/1/comments",
            json={"body": "不应创建"},
        ),
        client.post(
            f"/api/projects/{project_id}/media",
            files={"file": ("blocked.png", b"not an image", "image/png")},
        ),
        # This legacy GET refreshes DB projections as a best-effort side
        # effect, so the centralized gate deliberately treats it as a write.
        client.get(f"/api/projects/{project_id}/chapters/1/stages"),
    ]
    for response in mutations:
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "project_deleting"

    during = client.post(
        "/api/projects",
        json={"title": "要删除的小说", "genre": "悬疑", "author": "Test"},
    )
    assert during.status_code == 201
    assert during.json()["id"] == "要删除的小说-2"

    release_deletion.set()
    deletion_thread.join(timeout=5)
    assert not deletion_thread.is_alive()
    assert result["response"].status_code == 200
    assert not (projects / project_id).exists()

    after = client.post(
        "/api/projects",
        json={"title": "要删除的小说", "genre": "悬疑", "author": "Test"},
    )
    assert after.status_code == 201
    assert after.json()["id"] == "要删除的小说-3"
    assert not (projects / project_id).exists()


def test_delete_reports_residual_inventory_as_retryable_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "residual-data"
    client, projects, media = _client(tmp_path, project_id)
    _seed_every_projection(projects, media, project_id)

    monkeypatch.setattr(db, "project_data_delete", lambda _project_id: {})
    response = _delete(client, project_id)

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "project_deletion_incomplete"
    assert "residual inventory" in response.json()["detail"]["reason"]
    assert any(db.project_data_counts(project_id).values())
