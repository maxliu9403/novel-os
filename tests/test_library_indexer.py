"""Projection construction may read sources, but may never mutate them."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import threading
import time

from PIL import Image
import pytest

from api.library_catalog import LibraryCatalog
from api.library_indexer import LibraryIndexer, SourceSnapshot, display_count
from core.artifacts import ArtifactStore


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _project(root, project_id="book", *, chapters=3, language="English"):
    project = root / project_id
    _write(project / "outputs/state/story_state.json", {
        "metadata": {"title": project_id, "language": language, "genre": "Romance", "created": "2026-11-01T00:00:00Z"},
        "chapters": {str(i): {"number": i, "status": "planned", "word_count": 0} for i in range(1, chapters + 1)},
    })
    return project


def _index(tmp_path, *, budget=32):
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    catalog = LibraryCatalog(tmp_path / "catalog.sqlite", root)
    indexer = LibraryIndexer(catalog, root, tmp_path / "thumbs", scan_budget=budget)
    return root, catalog, indexer


def _files(root):
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def _drain(indexer):
    indexer.scan_once()
    for _ in range(20):
        if not indexer.process_once(limit=100):
            return
    raise AssertionError("Indexer did not drain")


def _prose(project, number, kind, text):
    path = project / f"outputs/manuscript/chapter_{number:03d}_{kind}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _head(project, number, kind, text, previous=None):
    store = ArtifactStore(project)
    value = store.put_text(chapter=number, kind=kind, text=text, source="test",
                           parent_revision_id=previous.revision_id if previous else None)
    store.set_head(number, kind, value.revision_id, expected_revision_id=previous.revision_id if previous else None)
    return value


def test_initial_discovery_creates_all_placeholders_before_any_manuscript_read(tmp_path, monkeypatch):
    root, catalog, indexer = _index(tmp_path, budget=2)
    for i in range(8):
        _project(root, f"book-{i}")
    monkeypatch.setattr(SourceSnapshot, "read", lambda *a, **k: pytest.fail("discovery must not read manuscript"))
    # Identity is the only small JSON discovery may read. Missing identities do
    # not create source files or need a JSON document.
    monkeypatch.setattr(SourceSnapshot, "document", lambda *a, **k: None)
    indexer.scan_once()
    assert catalog.query()["total"] == 8
    assert all(row["freshness"] == "rebuilding" for row in catalog.query()["items"])
    assert not list(root.rglob("project_identity.json"))


def test_readonly_legacy_final_revised_draft_and_policy_counts(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root)
    _prose(project, 1, "draft", "not this draft")
    _prose(project, 1, "final", "one two")
    _prose(project, 2, "revised", "three four five")
    _prose(project, 3, "draft", "six")
    before = _files(root)
    _drain(indexer)
    row = catalog.query()["items"][0]
    assert row["word_count"] == 6
    assert row["drafted_count"] == row["chapter_count"] == 3
    assert row["final_count"] == 1
    assert row["count_unit"] == "words"
    assert row["status"] == "in_progress"
    assert row["freshness"] == "fresh"
    assert _files(root) == before
    assert not list(root.rglob(".artifact.lock"))
    assert not list(root.rglob("project_identity.json"))


def test_persistent_counts_only_read_changed_effective_chapter(tmp_path, monkeypatch):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root)
    first = _head(project, 1, "final", "one two")
    _head(project, 2, "revised", "three four five")
    _head(project, 3, "draft", "six")
    _drain(indexer)
    assert catalog.query()["items"][0]["word_count"] == 6
    _head(project, 1, "final", "replacement now has five words", first)
    original = SourceSnapshot.read
    reads = []
    def track(self, relative, **kwargs):
        if relative.startswith("outputs/artifacts/sha256/"):
            reads.append(relative)
        return original(self, relative, **kwargs)
    monkeypatch.setattr(SourceSnapshot, "read", track)
    # Reconstruct process adapters to prove the cache is durable, not LRU.
    restarted = LibraryIndexer(LibraryCatalog(catalog.database, root), root, tmp_path / "thumbs")
    _drain(restarted)
    assert catalog.query()["items"][0]["word_count"] == 9
    assert len(reads) == 1
    catalog.invalidate("book", domains=("task",))
    restarted.process_once()
    assert len(reads) == 1


def test_external_draft_edits_become_visible_after_background_reconcile(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    path = _prose(project, 1, "draft", "first version")
    _drain(indexer)
    path.write_text("replacement has four words")
    assert catalog.query()["items"][0]["word_count"] == 2
    indexer.scan_once()
    assert catalog.query()["items"][0]["freshness"] == "stale"
    indexer.process_once()
    assert catalog.query()["items"][0]["word_count"] == 4


def test_chinese_display_count_and_language_change_invalidate_counts(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1, language="简体中文")
    _prose(project, 1, "draft", "她推开门。Hello Ryan！")
    _drain(indexer)
    row = catalog.query()["items"][0]
    assert row["count_unit"] == "characters"
    assert row["word_count"] == 6
    assert display_count("one two", "words") == 2
    state_path = project / "outputs/state/story_state.json"
    state = json.loads(state_path.read_text())
    state["metadata"]["language"] = "English"
    _write(state_path, state)
    _drain(indexer)
    assert catalog.query()["items"][0]["count_unit"] == "words"


def test_corrupt_book_does_not_hide_good_book_or_mutate_source(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    good = _project(root, "good")
    bad = _project(root, "bad")
    _prose(good, 1, "draft", "saved words")
    (bad / "outputs/state/story_state.json").write_text("{broken")
    before = _files(root)
    _drain(indexer)
    rows = {r["id"]: r for r in catalog.query()["items"]}
    assert rows["good"]["word_count"] == 2
    assert rows["bad"]["freshness"] == "unavailable"
    assert _files(root) == before


def test_corrupt_blob_preserves_previous_counts_and_marks_only_chapter_domain(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    final = _head(project, 1, "final", "good final words")
    _drain(indexer)
    (project / f"outputs/artifacts/sha256/{final.sha256}").write_text("corrupt")
    before = _files(root)
    indexer.scan_once()
    indexer.process_once()
    row = catalog.query()["items"][0]
    assert row["word_count"] == 3
    assert row["freshness"] == "stale"
    assert row["index_diagnostics"][0]["domain"] == "chapters"
    assert row["title"] == "book"
    assert _files(root) == before


def test_snapshot_race_never_publishes_stale_data(tmp_path, monkeypatch):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    _prose(project, 1, "draft", "initial words")
    _drain(indexer)
    catalog.invalidate("book")
    _prose(project, 1, "draft", "changed before race")
    original = indexer._chapters
    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        _prose(project, 1, "draft", "new words concurrently saved")
        return result
    monkeypatch.setattr(indexer, "_chapters", racing)
    indexer.process_once()
    row = catalog.query()["items"][0]
    assert row["word_count"] == 2
    assert row["freshness"] == "stale"


def test_no_live_owner_does_not_claim_running_and_saved_error_remains(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    _write(project / "outputs/runs/current/run.json", {
        "run_id": "current", "status": "running", "created_at": "2026-11-01T01:00:00Z",
        "current_phase": "chapter.write", "spec": {"dry_run": False}, "error": None,
    })
    _write(project / "outputs/runs/dry/run.json", {
        "run_id": "dry", "status": "completed", "created_at": "2026-12-01T01:00:00Z", "spec": {"dry_run": True},
    })
    _drain(indexer)
    row = catalog.query()["items"][0]
    assert row["status"] == "interrupted"
    assert row["task_id"] == "current"
    assert not row["task_can_resume"]


def test_source_and_project_symlinks_are_never_followed(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    outside = _project(tmp_path / "outside", "foreign")
    (root / "alias").symlink_to(outside, target_is_directory=True)
    project = _project(root, "book", chapters=1)
    (project / "outputs/manuscript").symlink_to(outside / "outputs/state", target_is_directory=True)
    _drain(indexer)
    rows = catalog.query()["items"]
    assert len(rows) == 1
    assert rows[0]["id"] == "book"
    assert rows[0]["freshness"] == "unavailable"


def _cover(project):
    buffer = BytesIO()
    Image.new("RGB", (1024, 1536), "navy").save(buffer, "PNG")
    image = project / "outputs/covers/images/image.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(buffer.getvalue())
    cover_id = "cover-" + "a" * 32
    candidate_id = "candidate-a"
    _write(project / "outputs/covers/index.json", {"cover_set_id": cover_id, "revision": 1})
    _write(project / f"outputs/covers/sets/{cover_id}.json", {
        "cover_set_id": cover_id, "project_id": project.name, "status": "selected",
        "selected_candidate_id": candidate_id, "created_at": "2026-11-01T00:00:00Z",
        "candidates": [{"candidate_id": candidate_id, "status": "selected", "relative_path": image.relative_to(project).as_posix(),
                        "sha256": sha256(image.read_bytes()).hexdigest(), "media_id": "saved-image"}],
    })
    return image


def test_background_thumbnails_have_bounded_size_and_preserve_original(tmp_path, monkeypatch):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    image = _cover(project)
    before = _files(root)
    _drain(indexer)
    cover = catalog.query()["items"][0]["cover"]
    assert cover["status"] == "selected"
    assert set(cover["thumbnail_urls"]) == {"160", "256", "384"}
    digest = cover["thumbnail_url"].split("/")[-2]
    for width in (160, 256, 384):
        path = indexer.thumbnail_path("book", digest, width)
        with Image.open(path) as thumb:
            assert thumb.size == (width, width * 3 // 2)
        assert path.stat().st_size < 60_000
    assert _files(root) == before
    monkeypatch.setattr(Image, "open", lambda *a, **k: pytest.fail("thumbnail GET resolver must not decode"))
    assert indexer.thumbnail_path("book", digest, 256).exists()
    catalog.invalidate("book", ("metadata",))
    indexer.process_once()
    assert _files(root) == before
    image.write_bytes(b"tampered original")
    _drain(indexer)
    assert catalog.query()["items"][0]["cover"]["status"] == "needs_attention"


def _selected_cover_projection(project, *, content_type="image/png", image_format="PNG", extension=".png"):
    image = _cover(project)
    selected_raw = BytesIO()
    Image.new("RGB", (600, 900), "navy").save(selected_raw, image_format)
    image.unlink()
    pending = project / f"outputs/deliverables/covers/pending/cover-03{extension}"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_bytes(selected_raw.getvalue())
    selected = pending.parent.parent / f"selected-cover{extension}"
    selected.write_bytes(selected_raw.getvalue())
    metadata = project / f"outputs/covers/sets/cover-{'a' * 32}.json"
    record = json.loads(metadata.read_text())
    record["candidates"][0].update(relative_path=pending.relative_to(project).as_posix(),
                                   content_type=content_type, sha256=sha256(selected.read_bytes()).hexdigest())
    _write(metadata, record)
    return pending, selected, metadata


@pytest.mark.parametrize("content_type,image_format,extension", [
    ("image/png", "PNG", ".png"), ("image/jpeg", "JPEG", ".jpg"), ("image/webp", "WEBP", ".webp"),
])
def test_selected_cover_survives_reused_historical_pending_path(tmp_path, content_type, image_format, extension):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    pending, selected, _ = _selected_cover_projection(project, content_type=content_type,
                                                     image_format=image_format, extension=extension)
    newer = BytesIO()
    Image.new("RGB", (600, 900), "red").save(newer, image_format)
    pending.write_bytes(newer.getvalue())
    latest_id = "cover-" + "b" * 32
    _write(project / f"outputs/covers/sets/{latest_id}.json", {
        "cover_set_id": latest_id, "project_id": "book", "status": "ready", "selected_candidate_id": "",
        "created_at": "2026-11-02T00:00:00Z", "candidates": [{"candidate_id": "candidate-b", "status": "ready",
            "relative_path": pending.relative_to(project).as_posix(), "sha256": sha256(pending.read_bytes()).hexdigest(),
            "content_type": content_type}],
    })
    before = _files(root)
    _drain(indexer)
    row = catalog.query()["items"][0]
    assert row["freshness"] == "fresh" and row["index_diagnostics"] == []
    assert catalog.query()["catalog_status"] == "ready" and not catalog.pending()
    cover = row["cover"]
    assert cover["image_kind"] == "selected" and cover["candidate_id"] == "candidate-a"
    assert cover["cover_set_id"] == "cover-" + "a" * 32
    digest = cover["thumbnail_url"].split("/")[-2]
    with Image.open(indexer.thumbnail_path("book", digest, 256)) as thumbnail:
        red, _, blue = thumbnail.getpixel((128, 192))
        assert blue > 100 and red < 20  # Selected navy art, never the newer red candidate.
    assert _files(root) == before
    assert selected.relative_to(project).as_posix() in catalog.get_component("book", "dependencies")["paths"]


def test_selected_projection_never_bypasses_the_active_candidate_digest(tmp_path, monkeypatch):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    pending, selected, _ = _selected_cover_projection(project)
    pending.write_bytes(b"overwritten candidate")
    selected.write_bytes(b"another selection")
    before = _files(root)
    monkeypatch.setattr(indexer, "_render_thumbnails", lambda *_: pytest.fail("Unverified artwork must not render"))
    _drain(indexer)
    row = catalog.query()["items"][0]
    assert row["freshness"] == "stale" and row["cover"]["status"] == "needs_attention"
    assert row["index_diagnostics"] == [{"domain": "cover", "message": "Cover image does not match its saved digest"}]
    assert _files(root) == before


@pytest.mark.parametrize("projection", ["missing", "old"])
def test_selected_candidate_is_usable_before_projection_is_available(tmp_path, projection):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    _, selected, _ = _selected_cover_projection(project)
    if projection == "missing":
        selected.unlink()
    else:
        selected.write_bytes(b"previous selection")
    before = _files(root)
    _drain(indexer)
    row = catalog.query()["items"][0]
    assert row["freshness"] == "fresh" and row["cover"]["image_kind"] == "selected"
    assert row["cover"]["thumbnail_url"]
    assert _files(root) == before


def test_selected_projection_changes_invalidate_cached_preview(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    pending, selected, _ = _selected_cover_projection(project)
    pending.write_bytes(b"historical path was reused")
    _drain(indexer)
    assert catalog.query()["items"][0]["freshness"] == "fresh"
    selected.write_bytes(b"tampered selected image")
    before = _files(root)
    _drain(indexer)
    row = catalog.query()["items"][0]
    assert row["freshness"] == "stale" and row["cover"]["status"] == "needs_attention"
    assert row["index_diagnostics"][0]["domain"] == "cover"
    assert _files(root) == before


def test_selected_projection_symlink_is_rejected_without_fallback(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    pending, selected, _ = _selected_cover_projection(project)
    selected.unlink()
    selected.symlink_to(pending)
    _drain(indexer)
    row = catalog.query()["items"][0]
    assert row["freshness"] == "stale" and row["index_diagnostics"][0]["domain"] == "cover"
    assert row["cover"]["status"] == "needs_attention"


def test_cover_source_policy_rebuilds_cover_without_rereading_manuscript(tmp_path, monkeypatch):
    from api import library_indexer
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    _prose(project, 1, "draft", "saved manuscript words")
    _selected_cover_projection(project)
    current_policy = library_indexer.COVER_SOURCE_POLICY
    monkeypatch.setattr(library_indexer, "COVER_SOURCE_POLICY", "previous-cover-source-policy")
    _drain(indexer)
    old_fingerprint = catalog.get_component("book", "domain:cover")["fingerprint"]
    old_revision = catalog.get_record("book")["revision"]
    before = _files(root)
    reads = []
    original = SourceSnapshot.read
    def observed(self, relative, **kwargs):
        reads.append(relative)
        return original(self, relative, **kwargs)
    monkeypatch.setattr(SourceSnapshot, "read", observed)
    monkeypatch.setattr(library_indexer, "COVER_SOURCE_POLICY", current_policy)
    _drain(indexer)
    assert catalog.get_record("book")["revision"] > old_revision
    assert catalog.get_component("book", "domain:cover")["fingerprint"] != old_fingerprint
    assert "outputs/covers/index.json" in reads
    assert not any(path.startswith("outputs/manuscript/") for path in reads)
    assert catalog.query()["items"][0]["word_count"] == 3
    assert catalog.query()["catalog_status"] == "ready"
    assert _files(root) == before


def test_unselected_candidate_cannot_borrow_selected_projection(tmp_path, monkeypatch):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    pending, _, metadata = _selected_cover_projection(project)
    record = json.loads(metadata.read_text())
    record.update(status="ready", selected_candidate_id="")
    record["candidates"][0]["status"] = "ready"
    _write(metadata, record)
    pending.write_bytes(b"candidate is missing its original bytes")
    monkeypatch.setattr(indexer, "_render_thumbnails", lambda *_: pytest.fail("Unselected candidate used a selected projection"))
    _drain(indexer)
    row = catalog.query()["items"][0]
    assert row["freshness"] == "stale" and row["index_diagnostics"][0]["domain"] == "cover"


def test_copied_instance_is_scoped_and_does_not_rewrite_identity(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    for project_id in ("one", "two"):
        project = _project(root, project_id)
        from core.project_identity import _record
        (project / "outputs/state/project_identity.json").write_bytes(_record("a" * 32))
    before = _files(root)
    _drain(indexer)
    assert catalog.query()["total"] == 2
    assert catalog.get_record("one")["instance_id"] == catalog.get_record("two")["instance_id"] == "a" * 32
    assert _files(root) == before


def test_missing_project_tombstone_cannot_be_revived_by_late_builder(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    _project(root)
    indexer.scan_once()
    token = catalog.pending()[0]
    summary, fingerprint = indexer.build("book", token)
    catalog.tombstone("book")
    assert not catalog.publish("book", summary, token["epoch"], token["generation"], fingerprint)
    indexer.scan_once()
    assert catalog.query()["total"] == 0


def test_delivery_validation_only_runs_in_background_and_reuses_dependency_snapshot(tmp_path, monkeypatch):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root)
    archive = project / "outputs/deliverables/book-package.zip"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"test-archive")
    source = _prose(project, 1, "final", "finished text")
    _write(project / "outputs/publication/export-validation.json", {
        "schema_version": "novel-h5-export-receipt.v1", "engine_contract_status": "passed",
        "sources": {source.relative_to(project).as_posix(): sha256(source.read_bytes()).hexdigest()},
    })
    calls = []
    def validate(path):
        calls.append(path)
        return "a" * 64
    monkeypatch.setattr("core.delivery_validation.current_package_binding_sha", validate)
    _drain(indexer)
    row = catalog.query()["items"][0]
    assert row["delivery"]["status"] == "ready"
    assert row["delivery"]["validation_state"] == "verified_snapshot"
    assert len(calls) == 1
    original = SourceSnapshot.read
    reads = []
    def observed(self, relative, **kwargs):
        reads.append(relative)
        return original(self, relative, **kwargs)
    monkeypatch.setattr(SourceSnapshot, "read", observed)
    catalog.invalidate("book", ("task",))
    indexer.process_once()
    assert len(calls) == 1
    assert "outputs/publication/export-validation.json" not in reads
    assert not any(path.startswith("outputs/manuscript/") for path in reads)
    source.write_text("changed manuscript")
    def reject(path):
        raise ValueError("delivery_source_changed")
    monkeypatch.setattr("core.delivery_validation.current_package_binding_sha", reject)
    _drain(indexer)
    assert catalog.query()["items"][0]["delivery"]["status"] == "stale"


def test_start_is_idempotent_and_stop_is_cooperative(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    _project(root)
    indexer.start()
    worker = indexer._thread
    indexer.start()
    assert indexer._thread is worker
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not catalog.query()["items"]:
        time.sleep(.01)
    indexer.stop()
    assert not worker.is_alive()


def test_one_changed_chapter_in_120_chapter_book_reads_one_text(tmp_path, monkeypatch):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=120)
    for chapter in range(1, 121):
        _prose(project, chapter, "draft", "A saved chapter with six words.")
    _drain(indexer)
    original = SourceSnapshot.read
    reads = []
    def observed(self, relative, **kwargs):
        if relative.startswith("outputs/manuscript/"):
            reads.append(relative)
        return original(self, relative, **kwargs)
    monkeypatch.setattr(SourceSnapshot, "read", observed)
    catalog.invalidate("book", domains=("task",))
    indexer.process_once()
    assert reads == []
    _prose(project, 98, "draft", "One changed chapter.")
    _drain(indexer)
    assert reads == ["outputs/manuscript/chapter_098_draft.md"]
    assert catalog.query()["items"][0]["word_count"] == 119 * 6 + 3


def test_readonly_source_permissions_and_execution_constructors_are_not_needed(tmp_path, monkeypatch):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    _prose(project, 1, "draft", "Preserved chapter text.")
    before = _files(root)
    def forbidden(*args, **kwargs):
        pytest.fail("Indexing cannot invoke a canonical read-write constructor or recovery authority")
    monkeypatch.setattr("api.services.StoryState.__init__", forbidden)
    monkeypatch.setattr("core.artifacts.ArtifactStore.__init__", forbidden)
    monkeypatch.setattr("core.pipeline_request_recovery.projection", forbidden)
    monkeypatch.setattr("api.project_delivery_summary.project_delivery_summary", forbidden)
    original_modes = {path: path.stat().st_mode & 0o777 for path in [project, *project.rglob("*")]}
    try:
        for path in original_modes:
            path.chmod(0o555 if path.is_dir() else 0o444)
        _drain(indexer)
        assert catalog.query()["items"][0]["freshness"] == "fresh"
        assert _files(root) == before
    finally:
        for path, mode in original_modes.items():
            path.chmod(mode)


def test_corrupt_identity_is_not_silently_treated_as_legacy(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root)
    _write(project / "outputs/state/project_identity.json", {
        "identity": {"schema_version": 1, "project_instance_id": "a" * 32},
        "record_sha256": "wrong",
    })
    before = _files(root)
    _drain(indexer)
    assert catalog.query()["items"][0]["freshness"] == "unavailable"
    assert _files(root) == before


def test_task_only_event_does_not_reread_cover_state_or_manuscripts(tmp_path, monkeypatch):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=2)
    _cover(project)
    _prose(project, 1, "final", "Final words.")
    _prose(project, 2, "draft", "Draft words.")
    _write(project / "outputs/runs/current/run.json", {
        "run_id": "current", "status": "paused", "created_at": "2026-11-01T01:00:00Z", "spec": {"dry_run": False},
    })
    _drain(indexer)
    original = SourceSnapshot.read
    reads = []
    def observed(self, relative, **kwargs):
        reads.append(relative)
        return original(self, relative, **kwargs)
    monkeypatch.setattr(SourceSnapshot, "read", observed)
    _write(project / "outputs/runs/current/run.json", {
        "run_id": "current", "status": "completed", "created_at": "2026-11-01T01:00:00Z", "spec": {"dry_run": False},
    })
    catalog.invalidate("book", domains=("task",))
    indexer.process_once()
    assert catalog.query()["items"][0]["status"] == "completed"
    assert "outputs/runs/current/run.json" in reads
    assert "outputs/state/story_state.json" not in reads
    assert not any(p.startswith(("outputs/covers/", "outputs/manuscript/")) for p in reads)


def test_deleted_thumbnail_cache_is_rebuilt_in_background(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root)
    _cover(project)
    _drain(indexer)
    cover = catalog.query()["items"][0]["cover"]
    digest = cover["thumbnail_url"].split("/")[-2]
    path = indexer.thumbnail_path("book", digest, 256)
    path.unlink()
    catalog.invalidate("book", ("cover",))
    indexer.process_once()
    assert indexer.thumbnail_path("book", digest, 256) == path


def test_edit_after_snapshot_verification_is_not_hidden_by_a_new_fingerprint(tmp_path, monkeypatch):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root, chapters=1)
    prose = _prose(project, 1, "draft", "old text")
    indexer.scan_once()
    original = indexer._cache
    def interleaved(project_id, key, value, token):
        original(project_id, key, value, token)
        if key == "dependencies":
            prose.write_text("now four new words")
    monkeypatch.setattr(indexer, "_cache", interleaved)
    indexer.process_once()
    assert catalog.query()["items"][0]["word_count"] == 2
    monkeypatch.setattr(indexer, "_cache", original)
    indexer.scan_once()
    assert catalog.query()["items"][0]["freshness"] == "stale"
    indexer.process_once()
    assert catalog.query()["items"][0]["word_count"] == 4


def test_purge_fences_thumbnail_render_already_in_flight(tmp_path, monkeypatch):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root)
    _cover(project)
    indexer.scan_once()
    entered = threading.Event()
    release = threading.Event()
    original = indexer._render_thumbnails
    def blocked(raw):
        entered.set()
        assert release.wait(5)
        return original(raw)
    monkeypatch.setattr(indexer, "_render_thumbnails", blocked)
    worker = threading.Thread(target=indexer.process_once)
    worker.start()
    try:
        assert entered.wait(5)
        catalog.tombstone("book")
        # A new process-local adapter shares the deletion fence with the
        # already-running indexer for the same cache/scope/project.
        replacement = LibraryIndexer(catalog, root, tmp_path / "thumbs")
        replacement.purge_project("book")
        replacement.purge_project("book")
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        assert not (tmp_path / "thumbs" / catalog.scope / "book").exists()
        assert catalog.query()["total"] == 0
    finally:
        release.set()
        worker.join(5)


def test_purge_removes_existing_derivatives_without_touching_source(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root)
    _cover(project)
    before = _files(root)
    _drain(indexer)
    cache = tmp_path / "thumbs" / catalog.scope / "book"
    assert cache.exists()
    with pytest.raises(ValueError, match="tombstone"):
        indexer.purge_project("book")
    catalog.tombstone("book")
    indexer.purge_project("book")
    indexer.purge_project("book")
    assert not cache.exists()
    assert _files(root) == before


def test_restart_reconciles_tombstone_when_post_delete_purge_never_ran(tmp_path):
    import shutil
    root, catalog, indexer = _index(tmp_path)
    project = _project(root)
    _cover(project)
    _drain(indexer)
    cache = tmp_path / "thumbs" / catalog.scope / "book"
    assert cache.exists()
    catalog.tombstone("book")
    shutil.rmtree(project)
    restarted = LibraryIndexer(catalog, root, tmp_path / "thumbs")
    restarted.scan_once()
    assert not cache.exists()
    assert catalog.query()["total"] == 0


def test_nonfinite_json_does_not_poison_library_response(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    bad = _project(root, "bad")
    _project(root, "good")
    path = bad / "outputs/state/story_state.json"
    state = json.loads(path.read_text())
    state["metadata"]["classification"] = {"score": float("nan")}
    _write(path, state)
    _drain(indexer)
    rows = {r["id"]: r for r in catalog.query()["items"]}
    assert rows["bad"]["freshness"] == "unavailable"
    assert rows["good"]["freshness"] == "fresh"
    json.dumps(rows, allow_nan=False)


def test_old_derivative_is_not_served_for_recreated_book_epoch(tmp_path):
    root, catalog, indexer = _index(tmp_path)
    project = _project(root)
    _cover(project)
    _drain(indexer)
    cover = catalog.query()["items"][0]["cover"]
    digest = cover["thumbnail_url"].split("/")[-2]
    old_image = indexer.thumbnail_path("book", digest, 256)
    catalog.tombstone("book")
    recreated = catalog.discover("book", fingerprint="new-project-identity", allow_recreate=True)
    catalog.publish("book", {"id": "book", "title": "New owner content", "cover": {"status": "not_started"}},
                    recreated["epoch"], recreated["generation"])
    assert old_image.exists()  # Simulate interrupted derivative cleanup.
    with pytest.raises(FileNotFoundError):
        indexer.thumbnail_path("book", digest, 256)
