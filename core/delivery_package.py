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
    from .novel_classification import NovelClassification
    from .narrative_format import validate_serialization
    from .h5_import import build_h5_import, RELATIVE_PATH as H5_IMPORT_PATH, object_digest
except ImportError:  # pragma: no cover - exercised through pipeline integration
    from cover_models import CoverSet
    from novel_classification import NovelClassification
    from narrative_format import validate_serialization
    from h5_import import build_h5_import, RELATIVE_PATH as H5_IMPORT_PATH, object_digest


_ARCHIVE_NAME = "book-package.zip"
_MANIFEST_NAME = "package-manifest.json"
_BOOK_FORMATS = {".md", ".epub", ".pdf", ".docx"}
# Delivery types are an import contract, not a host MIME-database preference.
# Minimal Docker images may not know .epub; a workstation can also override it.
_DELIVERY_MEDIA_TYPES = {
    ".epub": "application/epub+zip",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_AUTO = object()


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
    publication_copy_path: Path | None | object = _AUTO,
    classification_path: Path | None | object = _AUTO,
    serialization_path: Path | None | object = _AUTO,
    h5_root: Path | None | object = _AUTO,
) -> PackageResult:
    project = Path(project_path).resolve()
    deliverables = project / "outputs" / "deliverables"
    deliverables.mkdir(parents=True, exist_ok=True)

    # Cover generation and selection rebuild the same archive after publication.
    # Discover the engine-owned source artifacts in that path so a cover-only
    # change cannot silently strip H5 metadata from an otherwise complete book.
    publication_copy_path = _resolve_project_artifact(
        project,
        publication_copy_path,
        "outputs/publication/publication-copy.json",
    )
    classification_path = _resolve_project_artifact(
        project,
        classification_path,
        "outputs/publication/novel-classification.json",
    )
    serialization_path = _resolve_project_artifact(
        project,
        serialization_path,
        "outputs/publication/novel-serialization.json",
    )
    if h5_root is _AUTO:
        h5_root = _discover_current_h5_root(deliverables)

    if cover_set is None and (deliverables / "covers/cover-set.json").is_file():
        cover_set = CoverSet.from_dict(json.loads(
            (deliverables / "covers/cover-set.json").read_text(encoding="utf-8")
        ))

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

    include_classification = classification_path is not None
    classification_payload: dict[str, Any] | None = None
    if classification_path is not None:
        source = Path(classification_path).resolve()
        if source.is_symlink() or not source.is_file() or source.stat().st_size == 0:
            raise ValueError("classification path must be a nonempty ordinary file")
        try:
            raw_classification = json.loads(source.read_text(encoding="utf-8"))
            classification_payload = NovelClassification.from_dict(
                raw_classification
            ).to_dict()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("classification path must contain valid JSON") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError("classification path must contain a canonical classification") from exc
        _atomic_write_json(deliverables / "meta/novel-classification.json", classification_payload)

    include_serialization = serialization_path is not None
    serialization_payload: dict[str, Any] | None = None
    if serialization_path is not None:
        source = Path(serialization_path).resolve()
        if source.is_symlink() or not source.is_file() or source.stat().st_size == 0:
            raise ValueError("serialization path must be a nonempty ordinary file")
        try:
            serialization_payload = validate_serialization(
                json.loads(source.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("serialization path must contain valid JSON") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("serialization path must contain canonical serialization") from exc
        _atomic_copy(source, deliverables / "meta/novel-serialization.json")

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
        legacy_metadata = current_h5_root / "meta/publication_package.json"
        if legacy_metadata.is_file():
            legacy_payload = json.loads(legacy_metadata.read_text(encoding="utf-8"))
            for key, expected in (("classification", classification_payload), ("serialization", serialization_payload)):
                if expected is not None and legacy_payload.get(key) is not None and legacy_payload[key] != expected:
                    raise ValueError(f"H5 evidence {key} conflicts with current package JSON")

    files = list(_payload_files(
        deliverables,
        include_publication_copy=include_publication_copy,
        include_classification=include_classification,
        include_serialization=include_serialization,
        h5_root=current_h5_root,
    ))
    entries = [_manifest_entry(deliverables, path, cover_set) for path in files]
    import_payload = build_h5_import(
        project, classification=classification_payload,
        serialization=serialization_payload, files=entries, cover_set=cover_set,
    )
    import_path = deliverables / H5_IMPORT_PATH
    _atomic_write_json(import_path, import_payload)
    files.append(import_path)
    entries.append(_manifest_entry(deliverables, import_path, cover_set))
    entries.sort(key=lambda item: item["path"])
    manifest = {
        "schema_version": 4,
        "book_id": import_payload["book_id"],
        "author": import_payload["author"],
        "h5_import": {"path": H5_IMPORT_PATH, "schema_version": import_payload["schema_version"]},
        "package_revision_sha256": object_digest(entries),
        "cover": {
            "cover_set_id": cover_set.cover_set_id if cover_set else "",
            "status": cover_set.status if cover_set else "absent",
            "selected_candidate_id": cover_set.selected_candidate_id if cover_set else "",
        },
        "classification": {
            "classification_id": str(
                (classification_payload or {}).get("classification_id") or ""
            ),
            "path": (
                "meta/novel-classification.json" if include_classification else ""
            ),
        },
        "serialization": {
            "serialization_id": str(
                (serialization_payload or {}).get("serialization_id") or ""
            ),
            "path": (
                "meta/novel-serialization.json" if include_serialization else ""
            ),
        },
        "files": entries,
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


def _resolve_project_artifact(
    project: Path,
    supplied: Path | None | object,
    relative: str,
) -> Path | None:
    if supplied is not _AUTO:
        return Path(supplied) if supplied is not None else None
    candidate = project / relative
    return candidate if candidate.is_file() else None


def _discover_current_h5_root(deliverables: Path) -> Path | None:
    """Recover only the H5 root named by the current package manifest."""

    manifest_path = deliverables / _MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    files = manifest.get("files")
    if not isinstance(files, list):
        return None
    root_names: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict) or entry.get("role") != "h5_publication_object":
            continue
        raw = entry.get("path")
        if not isinstance(raw, str):
            continue
        parts = Path(raw).parts
        if len(parts) >= 3 and parts[0] == "h5-publication" and parts[1] not in {".", ".."}:
            root_names.add(parts[1])
    if len(root_names) != 1:
        return None
    candidate = (deliverables / "h5-publication" / next(iter(root_names))).resolve()
    parent = (deliverables / "h5-publication").resolve()
    if candidate.parent != parent or candidate.is_symlink() or not candidate.is_dir():
        return None
    return candidate


def _payload_files(
    deliverables: Path,
    *,
    include_publication_copy: bool,
    include_classification: bool,
    include_serialization: bool,
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
        if relative.as_posix() == H5_IMPORT_PATH:
            continue
        if relative == Path("meta/publication-copy.json") and not include_publication_copy:
            continue
        if (
            relative == Path("meta/novel-classification.json")
            and not include_classification
        ):
            continue
        if (
            relative == Path("meta/novel-serialization.json")
            and not include_serialization
        ):
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
    candidate_id = ""
    display_order = None
    if relative == "covers/cover-set.json":
        role = "cover_metadata"
    elif relative == H5_IMPORT_PATH:
        role = "h5_import"
    elif relative == "meta/publication-copy.json":
        role = "publication_copy"
    elif relative == "meta/novel-classification.json":
        role = "novel_classification"
    elif relative == "meta/novel-serialization.json":
        role = "novel_serialization"
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
                candidate_id = candidate.candidate_id
                display_order = next(
                    index for index, item in enumerate(cover_set.candidates, 1)
                    if item.candidate_id == candidate_id
                )
    elif relative.startswith("covers/selected-cover."):
        role = "selected_cover"
        selection_state = "selected"
    mime = _DELIVERY_MEDIA_TYPES.get(path.suffix.lower())
    if mime is None:
        mime, _ = mimetypes.guess_type(path.name)
    result = {
        "path": relative,
        "media_type": mime or "application/octet-stream",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "role": role,
        "cover_selection_state": selection_state,
    }
    if role == "cover_candidate":
        result.update(candidate_id=candidate_id, display_order=display_order)
    return result


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
