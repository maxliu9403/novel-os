"""Small, read-only delivery-package projection for project cards."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Literal
from zipfile import BadZipFile, ZipFile

from .models import ProjectDeliverySummary
from core.delivery_validation import contract_dependencies, current_package_binding_sha
from core.safe_project_io import (
    ProjectPathError,
    open_project_file,
    project_file_stamp,
    read_project_text,
)


_COVER_SET_ID = re.compile(r"^cover-[0-9a-f]{32}$")
_STAT_MISSING = ("missing",)
_GENERIC_READ_ERROR = "交付包状态无法安全读取。"
_INTERRUPTED_ERROR = "交付包生成已中断，请重新生成交付包。"
_INDEX = ("outputs", "covers", "index.json")
_STATUS = ("outputs", "covers", "delivery-status.json")
_ARCHIVE = ("outputs", "deliverables", "book-package.zip")
_DELIVERY_LOCK = ("outputs", ".delivery.lock")


def _active_cover_set_id(project: Path) -> str | None:
    raw = read_project_text(project, _INDEX, missing_ok=True, max_bytes=64_000)
    if raw is None:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("cover index must contain an object")
    cover_set_id = str(value.get("cover_set_id") or "")
    if not cover_set_id:
        return None
    if not _COVER_SET_ID.fullmatch(cover_set_id):
        raise ValueError("cover index has an invalid set id")
    return cover_set_id


def _cover_set_relative(cover_set_id: str) -> tuple[str, ...]:
    return ("outputs", "covers", "sets", f"{cover_set_id}.json")


def _selection_binding(project: Path) -> dict[str, str]:
    active_id = _active_cover_set_id(project)
    if active_id is None:
        return {"cover_set_id": "", "candidate_id": "", "cover_sha256": ""}
    raw = read_project_text(
        project,
        _cover_set_relative(active_id),
        max_bytes=16 * 1024 * 1024,
    )
    value = json.loads(raw or "")
    if not isinstance(value, dict):
        raise ValueError("active cover set must contain an object")
    selected_id = str(value.get("selected_candidate_id") or "")
    selected = None
    candidates = value.get("candidates") or ()
    if not isinstance(candidates, (list, tuple)):
        raise ValueError("active cover candidates must contain a list")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("active cover candidate is invalid")
        if str(candidate.get("candidate_id") or "") == selected_id:
            selected = candidate
            break
    if selected is None:
        return {"cover_set_id": "", "candidate_id": "", "cover_sha256": ""}
    return {
        "cover_set_id": str(value.get("cover_set_id") or ""),
        "candidate_id": str(selected.get("candidate_id") or ""),
        "cover_sha256": str(selected.get("sha256") or ""),
    }


def _stamp(project: Path, relative: tuple[str, ...]) -> tuple[object, ...]:
    value = project_file_stamp(project, relative)
    return value if value is not None else _STAT_MISSING


def _dependencies(project: Path) -> tuple[object, ...]:
    active_id = _active_cover_set_id(project)
    return (
        _stamp(project, _INDEX),
        _stamp(project, _cover_set_relative(active_id))
        if active_id is not None
        else _STAT_MISSING,
        _stamp(project, _STATUS),
        _stamp(project, _ARCHIVE),
        contract_dependencies(project),
    )


def _sha256_file(project: Path) -> str:
    digest = hashlib.sha256()
    with open_project_file(project, _ARCHIVE) as descriptor:
        assert descriptor is not None
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _package_matches(project: Path, expected: dict[str, str]) -> bool | None:
    """Return ``None`` when absent, otherwise whether the ZIP matches selection."""
    with open_project_file(project, _ARCHIVE, missing_ok=True) as descriptor:
        if descriptor is None:
            return None
        try:
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                with ZipFile(stream) as archive:
                    info = archive.getinfo("package-manifest.json")
                    if info.file_size > 4 * 1024 * 1024:
                        return False
                    manifest = json.loads(archive.read(info))
            if not expected["candidate_id"]:
                return True
            cover = manifest.get("cover") or {}
            selected = [
                item
                for item in manifest.get("files", [])
                if item.get("role") == "selected_cover"
            ]
            return (
                cover.get("cover_set_id") == expected["cover_set_id"]
                and cover.get("selected_candidate_id") == expected["candidate_id"]
                and len(selected) == 1
                and selected[0].get("sha256") == expected["cover_sha256"]
            )
        except (BadZipFile, KeyError, ValueError, TypeError, AttributeError, OSError):
            return False


def _saved_status(project: Path, expected: dict[str, str]) -> dict[str, object]:
    value: dict[str, object] = {
        "status": "missing",
        **expected,
        "updated_at": "",
        "error": "",
        "retryable": True,
    }
    raw = read_project_text(project, _STATUS, missing_ok=True, max_bytes=64_000)
    if raw is None:
        return value
    try:
        saved = json.loads(raw)
    except json.JSONDecodeError:
        return value
    if isinstance(saved, dict) and all(
        saved.get(key) == item for key, item in expected.items()
    ):
        for key in (
            "status",
            "updated_at",
            "error",
            "archive_sha256",
            "retryable",
        ):
            if key in saved:
                value[key] = saved[key]
    return value


def _summary_values(project: Path) -> tuple[str, bool, str | None, str | None]:
    expected = _selection_binding(project)
    value = _saved_status(project, expected)
    status = value.get("status")
    updated_at = str(value.get("updated_at") or "") or None

    if status == "pending":
        return "stale", True, _INTERRUPTED_ERROR, updated_at
    if status == "failed":
        error = str(value.get("error") or "")[:1200] or None
        saved_retryable = value.get("retryable")
        retryable = saved_retryable if type(saved_retryable) is bool else True
        return "failed", retryable, error, updated_at
    if status not in {"ready", "missing", "stale"}:
        return "failed", True, "交付包状态无法识别。", updated_at

    matches = _package_matches(project, expected)
    if matches is None:
        return "missing", True, None, updated_at
    if not matches:
        return (
            "stale",
            True,
            "交付包尚未同步当前封面，请重新生成交付包。旧 ZIP 已保留。",
            updated_at,
        )

    expected_sha = value.get("archive_sha256")
    if expected_sha:
        actual_sha = _sha256_file(project)
        if actual_sha != expected_sha:
            return "stale", True, "交付包文件已变化，请重新生成交付包。", updated_at
    try:
        current_package_binding_sha(project)
    except (ValueError, OSError) as exc:
        return "stale", True, f"交付包需要重新校验或生成：{exc}", updated_at
    return "ready", False, None, updated_at


@lru_cache(maxsize=512)
def _cached_summary(
    project_path: str,
    dependencies: tuple[object, ...],
) -> tuple[str, bool, str | None, str | None]:
    del dependencies  # The fd-derived stamps are the cache key.
    return _summary_values(Path(project_path))


@contextmanager
def _delivery_read_guard(
    project: Path,
) -> Iterator[Literal["held", "missing", "busy"]]:
    """Observe the existing writer lock without creating or blocking on it."""
    with open_project_file(project, _DELIVERY_LOCK, missing_ok=True) as descriptor:
        if descriptor is None:
            yield "missing"
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                yield "busy"
                return
            raise
        try:
            yield "held"
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)


def _model(
    values: tuple[str, bool, str | None, str | None],
) -> ProjectDeliverySummary:
    status, retryable, error, updated_at = values
    return ProjectDeliverySummary(
        status=status,
        retryable=retryable,
        error=error,
        updated_at=updated_at,
    )


def project_delivery_summary(project: Path) -> ProjectDeliverySummary:
    """Return download-equivalent status without rebuilding or writing files."""
    project = Path(project).absolute()
    try:
        with _delivery_read_guard(project) as guard:
            if guard == "busy":
                return _model(("pending", False, "交付包正在生成，请稍候。", None))
            dependencies = _dependencies(project)
            values = _cached_summary(str(project), dependencies)
            # Do not retain a result captured across an atomic selection/package
            # update. The shared delivery lock keeps cooperative writers out;
            # this second snapshot also catches uncoordinated file changes.
            current = _dependencies(project)
            if current != dependencies:
                values = _cached_summary(str(project), current)
            return _model(values)
    except (ProjectPathError, OSError, ValueError, TypeError, KeyError):
        return _model(("failed", True, _GENERIC_READ_ERROR, None))
