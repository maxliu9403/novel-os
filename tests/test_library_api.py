from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest
from fastapi.testclient import TestClient

from api.jobs import JobRunner
from api.main import create_app
from api.services import ProjectService
from core.projection_events import subscribe, notify


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    app = create_app(projects_root=root, db_url=f"sqlite:///{tmp_path / 'app.sqlite'}", media_root=tmp_path / "media")
    service = ProjectService(root)
    catalog, indexer = app.state.library_runtime.for_service(service)
    client = TestClient(app)
    yield client, catalog, indexer, app.state.library_runtime, root
    app.state.library_runtime.stop()
    client.close()


def publish(catalog, project_id, **fields):
    record = catalog.discover(project_id)
    if record is None:
        record = catalog.invalidate(project_id)
    assert catalog.publish(project_id, {
        "id": project_id, "title": f"Story {project_id}", "genre": "Romance",
        "status": "paused", "chapter_count": 100, "word_count": 100_000,
        "drafted_count": 50, "task_id": "run-1", "task_can_resume": True,
        "task_error": "previous pause", "task_started_at": "2026-11-01T00:00:00+00:00",
        **fields,
    }, expected_epoch=record["epoch"], expected_generation=record["generation"])


def test_library_gets_never_load_sources_and_paginate_and_navigation_search_all(library, monkeypatch):
    client, catalog, indexer, _, _ = library
    for i in range(25):
        publish(catalog, f"book-{i:03}")
    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP library queries must not read story sources")
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(ProjectService, "_load", forbidden)
    monkeypatch.setattr(ProjectService, "list_projects", forbidden)
    monkeypatch.setattr(indexer, "scan_once", forbidden)
    monkeypatch.setattr(indexer, "build", forbidden)
    response = client.get("/api/library")
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == page["filtered_total"] == 25
    assert len(page["items"]) == 20
    next_page = client.get("/api/library", params={"cursor": page["next_cursor"]}).json()
    assert len(next_page["items"]) == 5
    assert not set(row["id"] for row in page["items"]) & set(row["id"] for row in next_page["items"])
    navigation = client.get("/api/library/navigation", params={"q": "book-024"}).json()
    assert navigation["items"] == [{"id": "book-024", "title": "Story book-024", "genre": "Romance"}]
    assert client.get("/api/library/context").json()["scope"] == catalog.scope
    assert client.get("/api/library", headers={"If-None-Match": response.headers["etag"]}).status_code == 304
    delta = client.get("/api/library/changes", params={"after": page["changes_cursor"]}).json()
    assert delta["items"] == [] and not delta["reset_required"]


def test_live_operation_changes_filter_counts_etag_and_cursor_atomically(library, monkeypatch):
    from api import library_routes
    client, catalog, _, _, root = library
    for i in range(2):
        publish(catalog, f"book-{i}")
    jobs = JobRunner()
    monkeypatch.setattr(library_routes, "runner", jobs)
    initial = client.get("/api/library", params={"limit": 1})
    assert client.get("/api/library", params={"status": "writing"}).json()["filtered_total"] == 0
    release = threading.Event()
    job_id = jobs.submit("novel.resume", lambda: release.wait(3),
        {"project_id": "book-1", "project_path": str(root / "book-1"), "run_id": "run-1"})
    try:
        current = client.get("/api/library", params={"status": "writing"}).json()
        assert current["filtered_total"] == 1
        assert current["items"][0]["id"] == "book-1"
        assert current["items"][0]["task_error"] is None
        assert current["items"][0]["task_resume_job_id"] == job_id
        assert current["items"][0]["task_phase"] == "正在恢复核验"
        assert client.get("/api/library", params={"limit": 1}, headers={"If-None-Match": initial.headers["etag"]}).status_code == 200
        assert client.get("/api/library", params={"limit": 1, "cursor": initial.json()["next_cursor"]}).status_code == 409
        changes = client.get("/api/library/changes", params={"after": initial.json()["changes_cursor"]}).json()
        assert changes["membership_changed"]
        assert [item["id"] for item in changes["items"]] == ["book-1"]
    finally:
        release.set()
    monkeypatch.setattr(library_routes, "runner", JobRunner())
    assert client.get("/api/library/changes", params={"after": current["changes_cursor"]}).json()["reset_required"]


def test_commits_invalidate_durably_and_deletion_fences_inflight_builder(library):
    client, catalog, _, runtime, root = library
    publish(catalog, "book")
    before = client.get("/api/library").json()
    token = catalog.get_record("book")
    unsubscribe = subscribe(runtime._changed)
    try:
        notify(root / "book", "chapters", kind="intent")
        assert client.get("/api/library").json()["items"][0]["freshness"] == "stale"
        assert not catalog.publish("book", {"title": "outdated"}, token["epoch"], token["generation"])
        notify(root / "book", kind="deleted", required=True)
        assert client.get("/api/library").json()["total"] == 0
        changes = client.get("/api/library/changes", params={"after": before["changes_cursor"]}).json()
        assert changes["deleted"][0]["id"] == "book"
        assert not catalog.publish("book", {"title": "resurrected"}, token["epoch"], token["generation"])
        assert catalog.discover("book", fingerprint="old-scanner-new-stamp") is None
    finally:
        unsubscribe()


def test_empty_index_returns_rebuilding_without_synchronous_discovery(library, monkeypatch):
    client, _, indexer, _, _ = library
    monkeypatch.setattr(indexer, "scan_once", lambda: pytest.fail("GET must not backfill"))
    result = client.get("/api/library").json()
    assert result["items"] == []
    assert result["catalog_status"] == "rebuilding"


def test_failed_mutation_cannot_create_phantom_library_entry(library):
    client, catalog, _, runtime, root = library
    unsubscribe = subscribe(runtime._changed)
    try:
        notify(root / "does-not-exist", kind="intent")
        notify(root / "does-not-exist")
        assert catalog.get_record("does-not-exist") is None
        assert client.patch("/api/projects/does-not-exist", json={"title": "phantom"}).status_code == 404
        assert catalog.query()["total"] == 0
    finally:
        unsubscribe()


def test_api_rejects_unbounded_queries_and_missing_thumbnail_is_404(library):
    client, catalog, _, _, _ = library
    publish(catalog, "book")
    assert client.get("/api/library", params={"limit": 100_000}).status_code == 422
    assert client.get("/api/library", params={"status": "not-a-filter"}).status_code == 422
    assert client.get(f"/api/library/book/thumbnails/{'0' * 64}/256.webp").status_code == 404


def test_background_indexing_preserves_legacy_source_bytes(library):
    client, catalog, indexer, _, root = library
    folder = root / "book" / "outputs"
    (folder / "state").mkdir(parents=True)
    (folder / "manuscript").mkdir()
    state = folder / "state/story_state.json"
    state.write_text(json.dumps({"metadata": {"title": "Old Story", "language": "英文"},
        "chapters": {"1": {"number": 1, "status": "planned", "word_count": 0}}}))
    text = folder / "manuscript/chapter_001_draft.md"
    text.write_text("An old manuscript survives intact.")
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    indexer.scan_once()
    indexer.process_once()
    result = client.get("/api/library").json()["items"][0]
    assert result["title"] == "Old Story" and result["word_count"] == 5
    assert result["drafted_count"] == 1 and result["count_unit"] == "words"
    assert before == {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert not (folder / "state/project_identity.json").exists()


def test_deleted_slug_can_be_explicitly_created_after_process_restart(library, monkeypatch):
    from api import services, routes, project_deletion
    from api.project_operations import ProjectOperationGate
    client, catalog, indexer, runtime, root = library
    unsubscribe = subscribe(runtime._changed)
    try:
        created = client.post("/api/projects", json={"title": "Same Title", "author": "Test"})
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        indexer.scan_once()
        indexer.process_once()
        epoch = catalog.get_record(project_id)["epoch"]
        deleted = client.delete(f"/api/projects/{project_id}", params={"confirm_title": "Same Title"})
        assert deleted.status_code == 200, deleted.text
        assert catalog.get_record(project_id)["tombstone"]
        # Simulate a fresh process: only persistent catalog data survives.
        gate = ProjectOperationGate()
        monkeypatch.setattr(services, "project_operations", gate)
        monkeypatch.setattr(routes, "project_operations", gate)
        monkeypatch.setattr(project_deletion, "project_operations", gate)
        monkeypatch.setattr(routes, "runner", JobRunner())
        recreated = client.post("/api/projects", json={"title": "Same Title", "author": "New Author"})
        assert recreated.status_code == 201, recreated.text
        assert recreated.json()["id"] == project_id
        indexer.scan_once()
        indexer.process_once()
        row = catalog.get_record(project_id)
        assert not row["tombstone"] and row["epoch"] > epoch
        assert row["summary"]["author"] == "New Author"
        assert client.get("/api/library").json()["total"] == 1
    finally:
        unsubscribe()


def test_query_tombstone_failure_leaves_intact_book_runnable(library, monkeypatch):
    from api import routes
    from api.project_operations import project_operations
    client, _, _, _, root = library
    jobs = JobRunner()
    monkeypatch.setattr(routes, "runner", jobs)
    created = client.post("/api/projects", json={"title": "Keep This", "author": "Test"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    source = root / project_id / "outputs/state/story_state.json"
    original = source.read_bytes()
    def offline(path, domains, kind):
        if kind == "deleted":
            raise OSError("catalog disk unavailable")
    unsubscribe = subscribe(offline)
    try:
        response = client.delete(f"/api/projects/{project_id}", params={"confirm_title": "Keep This"})
        assert response.status_code >= 400
        assert source.read_bytes() == original
        assert not project_operations.unavailable(root / project_id)
        # This would throw ProjectJobBlocked if the pre-commit fence leaked.
        assert jobs.submit("test.safe", lambda: None, {"project_path": str(root / project_id), "project_id": project_id})
    finally:
        unsubscribe()


def test_postgres_app_configuration_uses_separate_query_catalog(tmp_path, monkeypatch):
    from api import db
    monkeypatch.setattr(db, "configure", lambda url: None)
    monkeypatch.setenv("NOVEL_OS_LIBRARY_DB", f"sqlite:///{tmp_path / 'read-model.sqlite'}")
    app = create_app(projects_root=tmp_path / "books", db_url="postgresql://localhost/novel_os")
    assert app.state.library_runtime.database == tmp_path / "read-model.sqlite"
    assert not (tmp_path / "books").exists()
    app.state.library_runtime.stop()


def test_creation_never_reuses_incomplete_project_residue(library):
    client, _, _, _, root = library
    residue = root / "same-title" / "outputs" / "manuscript"
    residue.mkdir(parents=True)
    original = residue / "chapter_001_final.md"
    original.write_text("Preserve the previous novel.")
    result = client.post("/api/projects", json={"title": "Same Title", "author": "Test"})
    assert result.status_code == 201, result.text
    assert result.json()["id"] == "same-title-2"
    assert original.read_text() == "Preserve the previous novel."
