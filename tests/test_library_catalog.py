"""The library is a query projection, never a second story authority."""

from pathlib import Path

import pytest

from api.library_catalog import CatalogCursorError, LibraryCatalog


@pytest.fixture
def catalog(tmp_path):
    return LibraryCatalog(tmp_path / "projection.sqlite", tmp_path / "books")


def publish(catalog, project_id="book", **summary):
    record = catalog.discover(project_id, fingerprint=f"fingerprint:{project_id}")
    if record is None:
        record = catalog.invalidate(project_id)
    summary.setdefault("created_at", "2026-01-01T00:00:00+00:00")
    assert catalog.publish(project_id, summary, record["epoch"], record["generation"])
    return catalog.get_record(project_id)


def snapshot(catalog, revision=1, *, boot="boot-a", kind="novel.resume", status="running", project_id="book"):
    job = {"job_id": "job-1", "kind": kind, "status": status,
           "project_id": project_id, "project_path": str(catalog.root / project_id),
           "started_at": "2026-02-01T00:00:00+00:00", "run_id": "run-1"}
    return {"boot_epoch": boot, "revision": revision, "oldest_revision": 0,
            "jobs": [job], "changes": [{"revision": revision, "project_id": project_id, "job": job}]}


def test_catalog_is_persistent_and_does_not_create_project_files(catalog):
    record = publish(catalog, title="An old book", word_count=123, status="paused")
    reopened = LibraryCatalog(catalog.database, catalog.root)
    assert reopened.query()["items"][0]["word_count"] == 123
    assert reopened.get_record("book")["legacy_identity"] == record["legacy_identity"]
    assert not catalog.root.exists()
    assert not reopened.pending()


def test_empty_catalog_completion_changes_etag_once(catalog):
    before = catalog.query()
    assert before["catalog_status"] == "rebuilding"
    catalog.mark_scan_complete()
    completed = catalog.query()
    assert completed["catalog_status"] == "ready"
    assert completed["etag"] != before["etag"]
    catalog.mark_scan_complete()
    assert catalog.query()["etag"] == completed["etag"]


def test_delivery_invalidation_is_visible_before_rebuilding(catalog):
    publish(catalog, delivery={"status": "ready", "updated_at": "original-certificate"})
    catalog.invalidate("book", ("task",))
    assert catalog.query()["items"][0]["delivery"]["status"] == "ready"
    catalog.invalidate("book", ("chapters",))
    card = catalog.query()["items"][0]
    assert card["freshness"] == "stale"
    assert card["delivery"]["status"] == "stale"
    assert card["delivery"]["updated_at"] == "original-certificate"


def test_authorized_scope_includes_storage_root_and_workspace(catalog, tmp_path):
    publish(catalog, title="Root one", instance_id="copied-instance")
    other_root = LibraryCatalog(catalog.database, tmp_path / "different-books")
    other_workspace = LibraryCatalog(catalog.database, catalog.root, "another-workspace")
    publish(other_root, title="Root two", instance_id="copied-instance")
    publish(other_workspace, title="Workspace two", instance_id="copied-instance")
    assert {catalog.scope, other_root.scope, other_workspace.scope}.__len__() == 3
    assert [item["title"] for item in catalog.query()["items"]] == ["Root one"]
    assert [item["title"] for item in other_root.query()["items"]] == ["Root two"]
    assert [item["title"] for item in other_workspace.query()["items"]] == ["Workspace two"]
    with pytest.raises(CatalogCursorError):
        # Scope binding also applies to otherwise valid pagination cursors.
        publish(catalog, "second")
        other_root.query(limit=1, cursor=catalog.query(limit=1)["next_cursor"])


def test_stable_pagination_search_filters_before_materialization(catalog, monkeypatch):
    for i in range(43):
        publish(catalog, f"book-{i:03}", title=f"Title {i:03}", status="completed" if i % 2 else "paused",
                task_started_at="2026-02-01T00:00:00+00:00",
                cover={"status": "selected" if i % 3 == 0 else "not_started"})
    materialized = []
    original = LibraryCatalog._card

    def counted(row, live):
        materialized.append(row["project_id"])
        return original(row, live)

    monkeypatch.setattr(LibraryCatalog, "_card", staticmethod(counted))
    seen, cursor = [], None
    while True:
        page = catalog.query(status="completed", limit=7, cursor=cursor)
        assert page["total"] == 43
        assert page["filtered_total"] == 21
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [f"book-{i:03}" for i in range(43) if i % 2]
    assert materialized == seen
    assert catalog.query(q="title 042", cover="selected")["items"][0]["id"] == "book-042"
    assert catalog.navigation(q="title 042")["items"] == [{"id": "book-042", "title": "Title 042", "genre": ""}]


def test_live_snapshot_changes_filter_count_and_cards_together(catalog):
    publish(catalog, status="paused", task_error="Old pause", task_can_resume=True)
    live = snapshot(catalog)
    page = catalog.query(status="writing", live_snapshot=live)
    assert page["filtered_total"] == len(page["items"]) == 1
    card = page["items"][0]
    assert card["status"] == "running"
    assert card["saved_task_error"] == "Old pause" and card["task_error"] is None
    assert not card["task_can_resume"]
    assert card["task_phase"] == "正在恢复核验"
    assert catalog.query(status="attention", live_snapshot=live)["filtered_total"] == 0
    other_scope = {**live, "jobs": [{**live["jobs"][0], "project_path": str(catalog.root.parent / "other" / "book")} ]}
    assert catalog.query(status="writing", live_snapshot=other_scope)["filtered_total"] == 0


def test_persisted_running_without_local_owner_is_not_claimed_active(catalog):
    publish(catalog, status="running", task_can_resume=True)
    assert catalog.query(status="writing")["filtered_total"] == 0
    card = catalog.query(status="attention")["items"][0]
    assert card["status"] == "interrupted" and not card["task_can_resume"]


def test_live_cover_filters_and_etag_are_same_snapshot(catalog):
    publish(catalog, cover={"status": "selected"}, status="paused", task_can_resume=True)
    before = catalog.query()
    live = snapshot(catalog, kind="cover.generate")
    active = catalog.query(cover="processing", live_snapshot=live)
    assert active["filtered_total"] == 1
    assert active["items"][0]["cover"]["status"] == "processing"
    assert not active["items"][0]["task_can_resume"]
    assert before["etag"] != active["etag"]
    assert catalog.query(cover="selected", live_snapshot=live)["filtered_total"] == 0
    writing = catalog.query(live_snapshot=snapshot(catalog, kind="chapter.write"))["items"][0]
    assert writing["status"] == "paused" and not writing["task_can_resume"]


def test_heartbeats_do_not_invalidate_pagination_but_status_changes_do(catalog):
    publish(catalog, status="paused")
    publish(catalog, "other")
    live = snapshot(catalog)
    page = catalog.query(limit=1, live_snapshot=live)
    heartbeat = {**live, "revision": 2}
    assert catalog.query(limit=1, cursor=page["next_cursor"], live_snapshot=heartbeat)["items"][0]["id"] == "other"
    assert catalog.query(limit=1, live_snapshot=heartbeat)["etag"] != page["etag"]
    done = snapshot(catalog, revision=3, status="done")
    with pytest.raises(CatalogCursorError):
        catalog.query(limit=1, cursor=page["next_cursor"], live_snapshot=done)
    with pytest.raises(CatalogCursorError):
        catalog.query(limit=1, cursor=page["next_cursor"], live_snapshot={**live, "boot_epoch": "restart"})


def test_card_progress_does_not_reset_recent_cursor(catalog):
    publish(catalog, task_started_at="2026-03-01", word_count=10)
    publish(catalog, "other", task_started_at="2026-02-01")
    page = catalog.query(limit=1)
    publish(catalog, task_started_at="2026-03-01", word_count=20, updated_at="2026-04-01")
    assert catalog.query(limit=1, cursor=page["next_cursor"])["items"][0]["id"] == "other"
    delta = catalog.changes(page["changes_cursor"])
    assert not delta["membership_changed"]
    assert delta["items"][0]["word_count"] == 20


def test_legacy_missing_creation_time_does_not_reorder_on_progress(catalog):
    publish(catalog, "a", updated_at="2026-01-01")
    publish(catalog, "b", updated_at="2026-01-02")
    page = catalog.query(limit=1)
    assert page["items"][0]["id"] == "a"
    publish(catalog, "a", word_count=30, updated_at="2026-11-14")
    assert catalog.query(limit=1, cursor=page["next_cursor"])["items"][0]["id"] == "b"
    delta = catalog.changes(page["changes_cursor"])
    assert not delta["membership_changed"]
    assert delta["items"][0]["word_count"] == 30


def test_durable_intents_generation_fence_and_components(catalog):
    token = catalog.invalidate("book", ("chapters",))
    assert token["freshness"] == "rebuilding"
    assert catalog.set_component("book", "chapter:1", {"count": 12}, token["epoch"])
    newer = catalog.invalidate("book", ("metadata",))
    assert set(catalog.pending()[0]["domains"]) == {"chapters", "metadata"}
    assert not catalog.publish("book", {"word_count": 1}, token["epoch"], token["generation"])
    assert catalog.publish("book", {"word_count": 2}, newer["epoch"], newer["generation"])
    assert not catalog.publish("book", {"word_count": 99}, newer["epoch"], newer["generation"])
    assert not catalog.fail(newer, "Late failure from an old attempt")
    assert catalog.query()["items"][0]["word_count"] == 2
    assert catalog.get_component("book", "chapter:1") == {"count": 12}


def test_delete_fence_survives_restart_and_late_indexer_write(catalog):
    token = publish(catalog)
    cursor = catalog.query()["changes_cursor"]
    assert catalog.tombstone("book")
    assert not catalog.publish("book", {"title": "late"}, token["epoch"], token["generation"])
    assert not catalog.set_component("book", "late", {}, token["epoch"])
    restarted = LibraryCatalog(catalog.database, catalog.root)
    assert restarted.discover("book", fingerprint="changed-source") is None
    assert restarted.invalidate("book") is None
    assert restarted.query()["total"] == 0
    delta = restarted.changes(cursor)
    assert delta["items"] == []
    assert delta["deleted"][0]["id"] == "book"
    assert delta["deleted"][0]["epoch"] > token["epoch"]
    assert delta["membership_changed"]
    assert not restarted.tombstone("book")


def test_permanent_delete_scrubs_content_but_retains_identity_fence(catalog):
    secrets = ["private-title-927", "private-premise-318", "private-source-545",
               "private-domain-cache-119", "private-index-error-610"]
    token = publish(catalog, title=secrets[0], premise=secrets[1], source={"excerpt": secrets[2]})
    catalog.set_component("book", "cached-source", {"private": secrets[3]}, token["epoch"])
    pending = catalog.invalidate("book")
    catalog.fail(pending, secrets[4])
    cursor = catalog.query()["changes_cursor"]

    assert catalog.tombstone("book")
    record = catalog.get_record("book")
    assert record["tombstone"] and record["epoch"] > token["epoch"]
    assert record["summary"] == {}
    assert all(record[field] == "" for field in (
        "title", "genre", "search_text", "sort_time", "updated_at", "observed_at", "error",
    ))
    assert catalog.get_component("book", "cached-source") is None
    assert not catalog.pending()
    assert catalog.changes(cursor)["deleted"][0]["id"] == "book"
    with catalog._connection() as connection:
        logical_rows = "\n".join(connection.iterdump())
    assert all(secret not in logical_rows for secret in secrets)
    assert not catalog.publish("book", {"title": secrets[0]}, token["epoch"], token["generation"])
    assert catalog.discover("book", fingerprint="different-fingerprint") is None


def test_repeated_delete_scrubs_legacy_tombstone_without_changing_fence(catalog):
    publish(catalog)
    catalog.tombstone("book")
    before = catalog.get_record("book")
    with catalog._connection(write=True) as connection:
        connection.execute("UPDATE library_projects SET summary_json=?,title=?,error=? WHERE scope=? AND project_id=?",
                           ('{"premise":"legacy-secret"}', "legacy-private-title", "legacy-private-error", catalog.scope, "book"))
    assert not catalog.tombstone("book")
    after = catalog.get_record("book")
    assert (after["epoch"], after["generation"], after["revision"]) == (before["epoch"], before["generation"], before["revision"])
    assert after["summary"] == {} and after["title"] == after["error"] == ""


def test_explicit_new_identity_advances_epoch(catalog):
    old = catalog.discover("book", instance_id="old", fingerprint="v1")
    assert catalog.publish("book", {"title": "Old"}, old["epoch"], old["generation"])
    catalog.tombstone("book")
    assert catalog.discover("book", instance_id="old", fingerprint="v1", allow_recreate=True) is None
    new = catalog.discover("book", instance_id="new", fingerprint="v2", allow_recreate=True)
    assert new["epoch"] > old["epoch"]
    assert new["summary"]["title"] == "book"
    assert not catalog.publish("book", {"title": "Zombie"}, old["epoch"], old["generation"])


def test_one_bad_book_preserves_other_cards_and_retry_is_durable(catalog):
    publish(catalog, "healthy", title="Healthy")
    publish(catalog, title="Last readable title")
    token = catalog.invalidate("book")
    assert catalog.fail(token, "Malformed source")
    reopened = LibraryCatalog(catalog.database, catalog.root)
    cards = {item["id"]: item for item in reopened.query()["items"]}
    assert cards["book"]["title"] == "Last readable title"
    assert cards["book"]["freshness"] == "stale"
    assert cards["healthy"]["freshness"] == "fresh"
    assert not reopened.pending()  # Backoff survives restart.
    first = reopened.invalidate("new-broken")
    reopened.fail(first, "Unparseable state")
    assert reopened.query()["catalog_status"] == "degraded"


def test_deltas_are_bounded_replayable_and_include_jobs(catalog):
    publish(catalog)
    initial_live = {"boot_epoch": "boot-a", "revision": 0, "jobs": [], "changes": [], "oldest_revision": 0}
    cursor = catalog.query(live_snapshot=initial_live)["changes_cursor"]
    for i in range(8):
        publish(catalog, f"new-{i}")
    live = snapshot(catalog)
    seen, count = set(), 0
    while True:
        delta = catalog.changes(cursor, limit=3, live_snapshot=live)
        assert not delta["reset_required"]
        assert len(delta["items"]) <= 3
        seen.update(item["id"] for item in delta["items"])
        cursor, count = delta["changes_cursor"], count + 1
        assert count < 20
        if not delta["has_more"]:
            break
    assert seen == {"book", *(f"new-{i}" for i in range(8))}
    assert catalog.changes(cursor, live_snapshot=live)["items"] == []
    assert catalog.changes(cursor, live_snapshot={**live, "boot_epoch": "after-restart"})["reset_required"]


def test_delta_log_expiration_and_cross_scope_reset(catalog, tmp_path, monkeypatch):
    import api.library_catalog as module
    monkeypatch.setattr(module, "CHANGE_RETENTION", 3)
    publish(catalog)
    cursor = catalog.query()["changes_cursor"]
    for _ in range(5):
        catalog.invalidate("book")
    assert catalog.changes(cursor)["reset_required"]
    other = LibraryCatalog(catalog.database, tmp_path / "other")
    assert other.changes(catalog.query()["changes_cursor"])["reset_required"]
    live = snapshot(catalog, revision=4)
    cursor = catalog.query(live_snapshot=live)["changes_cursor"]
    assert catalog.changes(cursor, live_snapshot={**live, "revision": 8, "oldest_revision": 5})["reset_required"]
    assert catalog.changes(cursor, live_snapshot={**live, "revision": 8, "changes": []})["reset_required"]


def test_read_queries_do_not_touch_story_paths_or_decode_every_card(catalog, monkeypatch):
    for i in range(25):
        publish(catalog, f"book-{i:03}")

    def forbidden(*args, **kwargs):
        raise AssertionError("library request attempted to read canonical files")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "iterdir", forbidden)
    monkeypatch.setattr(Path, "glob", forbidden)
    monkeypatch.setattr(Path, "rglob", forbidden)
    page = catalog.query(limit=3)
    assert len(page["items"]) == 3
    assert catalog.navigation(q="024")["items"][0]["id"] == "book-024"
    assert catalog.changes(page["changes_cursor"])["items"] == []


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 51}, {"status": "invalid"}, {"cover": "invalid"}, {"sort": "invalid"}, {"q": "x" * 501}])
def test_query_rejects_invalid_input(catalog, kwargs):
    with pytest.raises(ValueError):
        catalog.query(**kwargs)


@pytest.mark.parametrize("project_id", ["../escape", "a/b", "", "..", "a\\b"])
def test_discovery_cannot_manufacture_cross_scope_identity(catalog, project_id):
    with pytest.raises(ValueError):
        catalog.discover(project_id)
    with pytest.raises(ValueError):
        catalog.invalidate(project_id)
