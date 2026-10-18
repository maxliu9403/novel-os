"""Persist cover selection separately from the current downloadable package."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from zipfile import BadZipFile, ZipFile

try:
    from .cover_store import CoverStore
except ImportError:
    from cover_store import CoverStore


class DeliveryUnavailable(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__(status.get("error") or "Delivery package is not synchronized with the selected cover")


def delivery_selection(project_path, fallback=None):
    store = CoverStore(project_path)
    active = store.active()
    return store.load(active.cover_set_id) if active.cover_set_id else fallback


def _binding(cover_set):
    selected = next((item for item in cover_set.candidates
                     if item.candidate_id == cover_set.selected_candidate_id), None) if cover_set else None
    return {"cover_set_id": cover_set.cover_set_id if selected else "",
            "candidate_id": selected.candidate_id if selected else "",
            "cover_sha256": selected.sha256 if selected else ""}


def tracks_delivery(cover_set):
    return bool(cover_set and cover_set.selected_candidate_id and (
        cover_set.compiler_version.startswith("skeleton-poster")
        or any(item.scene_plan.get("_poster_recipe") for item in cover_set.concepts)))


def record_delivery(project_path, cover_set, status, *, error="", archive_sha256=""):
    """Caller holds delivery_lock so selection, projection and status are ordered."""
    value = {"schema_version": 1, **_binding(cover_set), "status": status,
             "updated_at": datetime.now(timezone.utc).isoformat(), "error": str(error)[:1200],
             "archive_sha256": archive_sha256, "retryable": status != "ready"}
    path = Path(project_path) / "outputs/covers/delivery-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".delivery-status.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return value


def _package_matches(path, expected):
    try:
        with ZipFile(path) as archive:
            info = archive.getinfo("package-manifest.json")
            if info.file_size > 4 * 1024 * 1024:
                return False
            manifest = json.loads(archive.read(info))
        if not expected["candidate_id"]:
            return True  # Preserve unselected/legacy export behavior.
        cover = manifest.get("cover") or {}
        selected = [item for item in manifest.get("files", []) if item.get("role") == "selected_cover"]
        return (cover.get("cover_set_id") == expected["cover_set_id"]
                and cover.get("selected_candidate_id") == expected["candidate_id"]
                and len(selected) == 1 and selected[0].get("sha256") == expected["cover_sha256"])
    except (OSError, BadZipFile, KeyError, ValueError, TypeError, AttributeError):
        return False


def delivery_status(project_path):
    """Small public status; stale manifests are detected even after process loss."""
    project = Path(project_path)
    expected = _binding(delivery_selection(project))
    value = {"status": "missing", **expected, "updated_at": "", "error": "", "retryable": True}
    path = project / "outputs/covers/delivery-status.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(saved, dict) and all(saved.get(key) == item for key, item in expected.items()):
            value.update({key: saved[key] for key in ("status", "updated_at", "error", "archive_sha256", "retryable") if key in saved})
    except (OSError, ValueError):
        pass
    if value["status"] in {"failed", "pending"}:
        return value
    archive = project / "outputs/deliverables/book-package.zip"
    if not archive.is_file() or archive.is_symlink():
        value.update(status="missing", retryable=True)
    elif not _package_matches(archive, expected):
        value.update(status="stale", retryable=True,
                     error="交付包尚未同步当前封面，请重新生成交付包。旧 ZIP 已保留。")
    else:
        value.update(status="ready", retryable=False, error="")
    return value


def read_current_package(project_path):
    """Caller holds delivery_lock through this read; never serve a stale ZIP."""
    value = delivery_status(project_path)
    if value["status"] == "missing":
        raise FileNotFoundError("Delivery package not found")
    if value["status"] != "ready":
        raise DeliveryUnavailable(value)
    data = (Path(project_path) / "outputs/deliverables/book-package.zip").read_bytes()
    if value.get("archive_sha256") and sha256(data).hexdigest() != value["archive_sha256"]:
        raise DeliveryUnavailable({**value, "status": "stale", "retryable": True,
            "error": "交付包文件已变化，请重新生成交付包。"})
    return data
