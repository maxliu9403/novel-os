"""Deterministic customer delivery bundles for compiled novels and covers."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:  # Package import in API/tests; top-level import in the legacy CLI path.
    from .cover_models import CoverSet
except ImportError:  # pragma: no cover - exercised through pipeline integration
    from cover_models import CoverSet


_ARCHIVE_NAME = "book-package.zip"
_MANIFEST_NAME = "package-manifest.json"
_BOOK_FORMATS = {".md", ".epub", ".pdf", ".docx"}
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class PackageResult:
    manifest_path: Path
    archive_path: Path
    archive_sha256: str
    file_count: int


def build_delivery_package(
    project_path: str | Path,
    *,
    cover_set: CoverSet | None = None,
    publication_copy_path: Path | None = None,
    h5_root: Path | None = None,
) -> PackageResult:
    project = Path(project_path).resolve()
    deliverables = project / "outputs" / "deliverables"
    deliverables.mkdir(parents=True, exist_ok=True)

    if cover_set is not None:
        _atomic_write_json(
            deliverables / "covers" / "cover-set.json",
            cover_set.to_dict(),
        )

    include_publication_copy = publication_copy_path is not None
    if publication_copy_path is not None:
        source = Path(publication_copy_path).resolve()
        if source.is_symlink() or not source.is_file() or source.stat().st_size == 0:
            raise ValueError("publication copy path must be a nonempty ordinary file")
        _atomic_copy(source, deliverables / "meta/publication-copy.json")

    current_h5_root: Path | None = None
    if h5_root is not None:
        current_h5_root = Path(h5_root).resolve()
        h5_parent = (deliverables / "h5-publication").resolve()
        if (
            current_h5_root.parent != h5_parent
            or current_h5_root.is_symlink()
            or not current_h5_root.is_dir()
        ):
            raise ValueError("h5_root must be a current ordinary package directory")

    files = list(_payload_files(
        deliverables,
        include_publication_copy=include_publication_copy,
        h5_root=current_h5_root,
    ))
    manifest = {
        "schema_version": 1,
        "cover": {
            "cover_set_id": cover_set.cover_set_id if cover_set else "",
            "status": cover_set.status if cover_set else "absent",
            "selected_candidate_id": cover_set.selected_candidate_id if cover_set else "",
        },
        "files": [_manifest_entry(deliverables, path, cover_set) for path in files],
    }
    manifest_path = deliverables / _MANIFEST_NAME
    _atomic_write_json(manifest_path, manifest)

    archive_path = deliverables / _ARCHIVE_NAME
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{_ARCHIVE_NAME}.", dir=deliverables
    )
    os.close(descriptor)
    try:
        _write_archive(
            Path(temporary),
            deliverables,
            [*files, manifest_path],
        )
        os.replace(temporary, archive_path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    archive_bytes = archive_path.read_bytes()
    return PackageResult(
        manifest_path=manifest_path,
        archive_path=archive_path,
        archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        file_count=len(files),
    )


def _payload_files(
    deliverables: Path,
    *,
    include_publication_copy: bool,
    h5_root: Path | None,
) -> Iterable[Path]:
    values: list[Path] = []
    for path in deliverables.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(deliverables)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.name in {_ARCHIVE_NAME, _MANIFEST_NAME} or path.name.endswith(".tmp"):
            continue
        if relative == Path("meta/publication-copy.json") and not include_publication_copy:
            continue
        if relative.parts and relative.parts[0] == "h5-publication":
            if h5_root is None or not path.resolve().is_relative_to(h5_root):
                continue
        if relative.parent == Path(".") and path.name.startswith("book."):
            if path.suffix.lower() not in _BOOK_FORMATS:
                continue
        values.append(path)
    return iter(sorted(values, key=lambda item: item.relative_to(deliverables).as_posix()))


def _manifest_entry(
    deliverables: Path,
    path: Path,
    cover_set: CoverSet | None,
) -> dict[str, Any]:
    relative = path.relative_to(deliverables).as_posix()
    data = path.read_bytes()
    role = "book_export"
    selection_state = ""
    if relative == "covers/cover-set.json":
        role = "cover_metadata"
    elif relative == "meta/publication-copy.json":
        role = "publication_copy"
    elif relative.startswith("h5-publication/"):
        role = "h5_publication_object"
    elif relative.startswith("covers/pending/"):
        role = "cover_candidate"
        selection_state = "pending"
        if cover_set:
            candidate = next(
                (item for item in cover_set.candidates if item.relative_path.endswith(relative)),
                None,
            )
            if candidate:
                selection_state = candidate.status
    elif relative.startswith("covers/selected-cover."):
        role = "selected_cover"
        selection_state = "selected"
    mime, _ = mimetypes.guess_type(path.name)
    if path.suffix.lower() == ".md":
        mime = "text/markdown"
    return {
        "path": relative,
        "media_type": mime or "application/octet-stream",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "role": role,
        "cover_selection_state": selection_state,
    }


def _write_archive(path: Path, deliverables: Path, files: Iterable[Path]) -> None:
    ordered = sorted(files, key=lambda item: item.relative_to(deliverables).as_posix())
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in ordered:
            relative = source.relative_to(deliverables).as_posix()
            info = zipfile.ZipInfo(relative, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
