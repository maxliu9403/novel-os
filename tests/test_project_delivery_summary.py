"""Project cards expose the same package state as the download endpoint."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
from pathlib import Path
from zipfile import ZipFile

import pytest

from api.services import ProjectService
from api.project_delivery_summary import project_delivery_summary
from core.cover_delivery import DeliveryUnavailable, read_current_package
from tests.test_project_task_summary import _project


def _hold_delivery_lock(project: str, ready, release) -> None:
    from core.delivery_package import delivery_lock

    with delivery_lock(project):
        ready.set()
        release.wait(timeout=10)


def _package(project: Path, *, marker: str = "first") -> Path:
    path = project / "outputs" / "deliverables" / "book-package.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        archive.writestr("book.md", marker)
        archive.writestr("package-manifest.json", json.dumps({
            "cover": {},
            "files": [],
        }))
    return path


def _status_marker(project: Path, archive: Path, *, status: str = "ready") -> Path:
    path = project / "outputs" / "covers" / "delivery-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "cover_set_id": "",
        "candidate_id": "",
        "cover_sha256": "",
        "status": status,
        "updated_at": "2026-10-24T00:00:00+00:00",
        "error": "" if status == "ready" else "package failed",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "retryable": status != "ready",
    }))
    return path


def test_list_and_detail_expose_downloadable_legacy_package(tmp_path):
    project = _project(tmp_path, "book")
    archive = _package(project)
    before = {path: path.stat().st_mtime_ns for path in project.rglob("*") if path.is_file()}
    service = ProjectService(tmp_path)

    listed = service.list_projects()[0]
    detailed = service.project_detail("book")

    assert listed.delivery == detailed.delivery
    assert listed.delivery.model_dump() == {
        "status": "ready",
        "retryable": False,
        "error": None,
        "updated_at": None,
    }
    assert read_current_package(project) == archive.read_bytes()
    assert {path: path.stat().st_mtime_ns for path in project.rglob("*") if path.is_file()} == before


def test_missing_orphaned_pending_and_failed_are_projected_without_rebuilding(tmp_path):
    project = _project(tmp_path, "book")
    service = ProjectService(tmp_path)
    assert service.list_projects()[0].delivery.status == "missing"
    assert not (project / "outputs/deliverables/book-package.zip").exists()

    archive = _package(project)
    marker = _status_marker(project, archive, status="pending")
    pending = service.list_projects()[0].delivery
    assert pending.status == "stale" and pending.retryable
    assert "中断" in (pending.error or "")

    saved = json.loads(marker.read_text())
    marker.write_text(json.dumps({**saved, "status": "failed", "updated_at": "2026-10-24T00:00:01+00:00"}))
    failed = service.list_projects()[0].delivery
    assert failed.status == "failed" and failed.updated_at == "2026-10-24T00:00:01+00:00"
    assert archive.is_file()


def test_active_rebuild_stays_pending_and_is_not_retryable(tmp_path):
    project = _project(tmp_path, "book")
    archive = _package(project)
    _status_marker(project, archive, status="pending")
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_delivery_lock,
        args=(str(project), ready, release),
    )
    process.start()
    try:
        assert ready.wait(timeout=10)
        active = project_delivery_summary(project)
        assert active.status == "pending"
        assert active.retryable is False
        assert "正在生成" in (active.error or "")
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    orphaned = project_delivery_summary(project)
    assert orphaned.status == "stale"
    assert orphaned.retryable is True
    assert "中断" in (orphaned.error or "")


def test_cached_hash_invalidates_when_archive_changes(tmp_path, monkeypatch):
    project = _project(tmp_path, "book")
    archive = _package(project)
    _status_marker(project, archive)
    from api import project_delivery_summary as module
    original = module._sha256_file
    calls = 0

    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(module, "_sha256_file", counted)
    service = ProjectService(tmp_path)
    assert service.list_projects()[0].delivery.status == "ready"
    assert service.list_projects()[0].delivery.status == "ready"
    assert calls == 1

    _package(project, marker="changed package bytes")
    stale = service.list_projects()[0].delivery

    assert calls == 2
    assert stale.status == "stale" and stale.retryable
    assert "变化" in (stale.error or "")
    with pytest.raises(DeliveryUnavailable) as exc:
        read_current_package(project)
    assert exc.value.status["status"] == "stale"


def test_removing_archive_invalidates_ready_summary(tmp_path):
    project = _project(tmp_path, "book")
    archive = _package(project)
    service = ProjectService(tmp_path)
    assert service.list_projects()[0].delivery.status == "ready"

    archive.unlink()

    missing = service.list_projects()[0].delivery
    assert missing.status == "missing" and missing.retryable


@pytest.mark.parametrize("unsafe_source", ["index", "status", "archive", "active-set"])
def test_unsafe_delivery_sources_fail_without_leaking_external_content(
    tmp_path: Path,
    unsafe_source: str,
):
    project = _project(tmp_path, "book")
    covers = project / "outputs" / "covers"
    covers.mkdir(parents=True, exist_ok=True)
    secret = "SECRET-OUTSIDE-PROJECT"

    if unsafe_source == "index":
        outside = tmp_path / "outside-index.json"
        outside.write_text(json.dumps({"cover_set_id": "", "secret": secret}))
        (covers / "index.json").symlink_to(outside)
    elif unsafe_source == "status":
        outside = tmp_path / "outside-status.json"
        outside.write_text(json.dumps({"status": "failed", "error": secret}))
        (covers / "delivery-status.json").symlink_to(outside)
    elif unsafe_source == "archive":
        outside_project = tmp_path / "outside-project"
        outside_project.mkdir()
        outside = _package(outside_project, marker=secret)
        archive = project / "outputs" / "deliverables" / "book-package.zip"
        archive.parent.mkdir(parents=True)
        archive.symlink_to(outside)
    else:
        cover_set_id = "cover-" + "a" * 32
        (covers / "index.json").write_text(json.dumps({
            "cover_set_id": cover_set_id,
            "revision": 1,
        }))
        outside = tmp_path / "outside-cover-set.json"
        outside.write_text(json.dumps({"cover_set_id": cover_set_id, "secret": secret}))
        target = covers / "sets" / f"{cover_set_id}.json"
        target.parent.mkdir()
        target.symlink_to(outside)

    result = project_delivery_summary(project)

    assert result.status == "failed"
    assert result.retryable is True
    assert result.error == "交付包状态无法安全读取。"
    assert secret not in (result.error or "")


def test_delivery_status_swap_to_symlink_cannot_win_validation_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project = _project(tmp_path, "book")
    archive = _package(project)
    marker = _status_marker(project, archive, status="failed")
    outside = tmp_path / "outside-status.json"
    secret = "SECRET-RACE-CONTENT"
    outside.write_text(json.dumps({"status": "failed", "error": secret}))

    from core import safe_project_io

    original_open = safe_project_io.os.open
    swapped = False

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "delivery-status.json" and not swapped:
            swapped = True
            marker.rename(marker.with_suffix(".safe"))
            marker.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(safe_project_io.os, "open", swap_before_open)

    result = project_delivery_summary(project)

    assert swapped is True
    assert result.status == "failed"
    assert result.error == "交付包状态无法安全读取。"
    assert secret not in (result.error or "")
