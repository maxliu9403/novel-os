"""Deterministic customer delivery bundles for compiled novels and covers."""

from __future__ import annotations

import hashlib
import fcntl
import json
import mimetypes
import os
import tempfile
import threading
import weakref
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:  # Package import in API/tests; top-level import in the legacy CLI path.
    from .cover_models import CoverCandidate, CoverSet
    from .cover_store import CoverStore
    from .novel_classification import NovelClassification
    from .narrative_format import validate_serialization
    from .h5_import import build_h5_import, RELATIVE_PATH as H5_IMPORT_PATH, object_digest
    from .h5_contract import PROFILE, ContractError, bound, checked_archive, verify_image
    from .safe_project_io import read_project_bytes
except ImportError:  # pragma: no cover - exercised through pipeline integration
    from cover_models import CoverCandidate, CoverSet
    from cover_store import CoverStore
    from novel_classification import NovelClassification
    from narrative_format import validate_serialization
    from h5_import import build_h5_import, RELATIVE_PATH as H5_IMPORT_PATH, object_digest
    from h5_contract import PROFILE, ContractError, bound, checked_archive, verify_image
    from safe_project_io import read_project_bytes


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
_DELIVERY_LOCKS = weakref.WeakValueDictionary()
_DELIVERY_LOCKS_GUARD = threading.Lock()
_DELIVERY_LOCK_STATE = threading.local()


@contextmanager
def delivery_lock(project_path: str | Path):
    """Serialize short publication transactions across threads and processes.

    Selection calls the service projection and then the package builder under
    this same lock. Reentry must reuse the outer file lock, never acquire a
    second flock descriptor that could wait on itself.
    """
    project = Path(project_path).resolve()
    key = str(project)
    with _DELIVERY_LOCKS_GUARD:
        lock = _DELIVERY_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _DELIVERY_LOCKS[key] = lock
    with lock:
        held = getattr(_DELIVERY_LOCK_STATE, "held", None)
        if held is None:
            held = _DELIVERY_LOCK_STATE.held = set()
        if key in held:
            yield
            return
        folder = project / "outputs"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / ".delivery.lock").open("a+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                fcntl.flock(stream, fcntl.LOCK_UN)


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
    with delivery_lock(project_path):
        try:
            from .cover_delivery import delivery_selection, record_delivery
        except ImportError:
            from cover_delivery import delivery_selection, record_delivery
        selected = delivery_selection(project_path, cover_set)
        record_delivery(project_path, selected, "pending")
        try:
            result = _build_delivery_package(
                project_path, cover_set=cover_set,
                publication_copy_path=publication_copy_path,
                classification_path=classification_path,
                serialization_path=serialization_path, h5_root=h5_root,
            )
        except Exception as exc:
            record_delivery(project_path, selected, "failed", error=exc)
            raise
        record_delivery(project_path, selected, "ready", archive_sha256=result.archive_sha256)
        return result


def _build_delivery_package(
    project_path: str | Path, *, cover_set: CoverSet | None = None,
    publication_copy_path: Path | None | object = _AUTO,
    classification_path: Path | None | object = _AUTO,
    serialization_path: Path | None | object = _AUTO,
    h5_root: Path | None | object = _AUTO,
) -> PackageResult:
    """Freeze sources, assemble privately, validate actual ZIP, then publish."""
    try:
        from .h5_acceptance import inspect_package
        from .project_identity import ensure_project_instance_id
    except ImportError:
        from h5_acceptance import inspect_package
        from project_identity import ensure_project_instance_id
    project = Path(project_path).resolve()
    destination = project / "outputs/deliverables"
    destination.mkdir(parents=True, exist_ok=True)
    ensure_project_instance_id(project)
    try:
        from .compiled_source import compilation_binding, COMPILED_SOURCE
    except ImportError:
        from compiled_source import compilation_binding, COMPILED_SOURCE
    compiled = compilation_binding(project)
    store = CoverStore(project)
    active = store.active()
    if active.cover_set_id:
        cover_set = store.load(active.cover_set_id)
    elif cover_set is None:
        raw_cover = read_project_bytes(project, ('outputs', 'deliverables', 'covers', 'cover-set.json'),
                                       missing_ok=True, max_bytes=PROFILE.document_bytes)
        if raw_cover is not None:
            cover_set = CoverSet.from_dict(json.loads(raw_cover))
    paths = {
        "publication_copy_path": _resolve_project_artifact(project, publication_copy_path, "outputs/publication/publication-copy.json"),
        "classification_path": _resolve_project_artifact(project, classification_path, "outputs/publication/novel-classification.json"),
        "serialization_path": _resolve_project_artifact(project, serialization_path, "outputs/publication/novel-serialization.json"),
    }
    root = _discover_current_h5_root(destination) if h5_root is _AUTO else h5_root
    files = list(_payload_files(destination, include_publication_copy=paths['publication_copy_path'] is not None,
                               include_classification=paths['classification_path'] is not None,
                               include_serialization=paths['serialization_path'] is not None,
                               h5_root=root, cover_set=cover_set))
    bound(len(files) + 2, PROFILE.zip_entries, "archive_entries")
    frozen = {}

    def capture(path, *, missing=False, limit=PROFILE.zip_expanded):
        relative = Path(path).absolute().relative_to(project)
        value = read_project_bytes(project, relative.parts, missing_ok=missing, max_bytes=limit)
        frozen[relative] = None if value is None else hashlib.sha256(value).hexdigest()
        return value

    identity_inputs = {relative: capture(project / relative, missing=True) for relative in (
        'outputs/state/story_state.json', 'outputs/state/project_identity.json')}
    capture(project / 'outputs/covers/index.json', missing=True)
    captured_binding = capture(project / COMPILED_SOURCE, missing=True)
    if captured_binding is not None and json.loads(captured_binding) != compiled:
        raise ContractError('compile_source_changed', path=COMPILED_SOURCE)
    if compiled:
        for relative, expected in compiled['sources'].items():
            capture(project / relative, missing=True)
            if frozen[Path(relative)] != expected:
                raise ContractError('compile_source_changed', path=relative)
    if active.cover_set_id:
        capture(project / f"outputs/covers/sets/{active.cover_set_id}.json")
    with tempfile.TemporaryDirectory(prefix=".delivery-", dir=project / "outputs") as folder:
        staging = Path(folder) / "deliverables"
        staging.mkdir()
        authority = Path(folder) / 'authority'
        for relative, raw in identity_inputs.items():
            if raw is not None:
                target = authority / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
        total = 0
        for path in files:
            raw = capture(path)
            total += len(raw)
            bound(total, PROFILE.zip_expanded, "archive_expanded")
            target = staging / path.relative_to(destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        staged_paths = {}
        for key, source in paths.items():
            if source is None:
                staged_paths[key] = None
            else:
                target = Path(folder) / key
                target.write_bytes(capture(source, limit=PROFILE.document_bytes))
                staged_paths[key] = target
        staged_root = staging / Path(root).relative_to(destination) if root is not None else None
        result = _assemble_delivery_package(project, cover_set=cover_set, **staged_paths,
                                            h5_root=staged_root, deliverables=staging, identity_root=authority)
        report = inspect_package(result.archive_path)
        if compiled and (report['versions']['content_sha256'] != compiled['content_sha256']
                         or report['versions']['front_matter_sha256'] != compiled['front_matter_sha256']
                         or report['chapter_count'] != compiled['chapter_count']):
            raise ContractError('compiled_epub_changed: 请重新编译正文')
        # A verified old snapshot must never be presented as the current book.
        for relative, expected in frozen.items():
            raw = read_project_bytes(project, relative.parts, missing_ok=True, max_bytes=PROFILE.zip_expanded)
            actual = None if raw is None else hashlib.sha256(raw).hexdigest()
            if actual != expected:
                raise ContractError("delivery_source_changed", path=relative.as_posix())
        source_hash = object_digest({str(path): value for path, value in frozen.items()})
        receipt = {"schema_version": "novel-h5-export-receipt.v1", "target_profile": report["target_profile"],
                   "validator_revision": report["validator_revision"],
                   "catalog_sha256": report["catalog_sha256"],
                   **({"required_h5_capabilities": report["required_h5_capabilities"]} if "required_h5_capabilities" in report else {}), "source_snapshot_sha256": source_hash,
                   "sources": {str(path): value for path, value in frozen.items()},
                   "package_sha256": result.archive_sha256, "engine_contract_status": "passed",
                   "external_import_status": report["external_import_status"]}
        # Publish metadata projections before the ZIP commit point. The durable
        # pending status prevents downloads throughout this transaction/crash.
        for relative in ("book.epub", "meta/h5-import.json", "meta/novel-classification.json",
                         "meta/novel-serialization.json", "meta/publication-copy.json", "covers/cover-set.json",
                         _MANIFEST_NAME):
            source = staging / relative
            if source.is_file():
                (destination / relative).parent.mkdir(parents=True, exist_ok=True)
                _atomic_copy(source, destination / relative)
                # Track final projections, not their pre-build contents.
                key = (destination / relative).relative_to(project).as_posix()
                receipt['sources'][key] = hashlib.sha256(source.read_bytes()).hexdigest()
        receipt['source_snapshot_sha256'] = object_digest(receipt['sources'])
        if compiled and frozen.get(Path(COMPILED_SOURCE)) is None:
            _atomic_write_json(project / COMPILED_SOURCE, compiled)
            receipt['sources'][COMPILED_SOURCE] = hashlib.sha256((project / COMPILED_SOURCE).read_bytes()).hexdigest()
            receipt['source_snapshot_sha256'] = object_digest(receipt['sources'])
        _atomic_write_json(project / "outputs/publication/export-validation.json", receipt)
        os.replace(result.archive_path, destination / _ARCHIVE_NAME)
        return PackageResult(destination / _MANIFEST_NAME, destination / _ARCHIVE_NAME,
                             result.archive_sha256, result.file_count)


def _assemble_delivery_package(
    project_path: str | Path,
    *,
    cover_set: CoverSet | None = None,
    publication_copy_path: Path | None | object = _AUTO,
    classification_path: Path | None | object = _AUTO,
    serialization_path: Path | None | object = _AUTO,
    h5_root: Path | None | object = _AUTO,
    deliverables: Path,
    identity_root: Path,
) -> PackageResult:
    project = Path(project_path).resolve()
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

    # Cover selection was resolved and frozen by the transaction wrapper.
    store = CoverStore(project)

    if cover_set is not None:
        if cover_set.repair_lineage:
            try:
                from .cover_repair_lineage import validate_lineage
            except ImportError:  # Legacy top-level CLI import.
                from cover_repair_lineage import validate_lineage
            validate_lineage(store, cover_set)
        # A later manuscript recompile must retain the explicitly selected new
        # poster cover before EPUB/outer-package hashes are derived.
        if cover_set.selected_candidate_id and (
                cover_set.compiler_version.startswith("skeleton-poster")
                or any(c.scene_plan.get("_poster_recipe") for c in cover_set.concepts)):
            try:
                from .cover_epub import embed_cover
            except ImportError:  # Legacy compiler imports modules at top level.
                from cover_epub import embed_cover
            selected = next(c for c in cover_set.candidates if c.candidate_id == cover_set.selected_candidate_id)
            candidate_path = project / selected.relative_path
            if candidate_path.is_symlink() or not candidate_path.resolve().is_relative_to(project):
                raise ValueError("Selected cover path escapes its project")
            epub = deliverables / "book.epub"
            if epub.is_file():
                image = deliverables / Path(selected.relative_path).relative_to("outputs/deliverables")
                if not image.is_file():
                    image = next(iter(deliverables.glob("covers/selected-cover.*")), image)
                epub_raw, image_raw = epub.read_bytes(), image.read_bytes()
                with checked_archive(epub_raw, epub=True):
                    pass
                verify_image(image_raw, selected.content_type, image.name)
                epub.write_bytes(embed_cover(epub_raw, image_raw, title=cover_set.brief.title,
                                             expected_sha256=selected.sha256))
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
        cover_set=cover_set,
    ))
    entries = [_manifest_entry(deliverables, path, cover_set) for path in files]
    import_payload = build_h5_import(
        identity_root, classification=classification_payload,
        serialization=serialization_payload, files=entries, cover_set=cover_set,
        deliverables=deliverables,
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
    cover_set: CoverSet | None,
) -> Iterable[Path]:
    values: list[Path] = []
    for path in deliverables.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(deliverables)
        public = (relative.parent == Path('.') and path.name.startswith('book.') and path.suffix.lower() in _BOOK_FORMATS
                  or relative.as_posix() in {'covers/cover-set.json', 'meta/publication-copy.json',
                                             'meta/novel-classification.json', 'meta/novel-serialization.json'}
                  or relative.as_posix().startswith(('covers/pending/', 'covers/selected-cover.', 'h5-publication/')))
        if not public:
            continue
        if cover_set is None and relative.is_relative_to('covers/pending'):
            continue  # No owner metadata means these bytes cannot be candidate evidence.
        if cover_set is not None:
            if relative.is_relative_to("covers/pending"):
                candidate = _pending_candidate(cover_set, relative.as_posix())
                if candidate is None or hashlib.sha256(path.read_bytes()).hexdigest() != candidate.sha256:
                    continue
            elif relative.as_posix().startswith("covers/selected-cover.") and not cover_set.selected_candidate_id:
                continue
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


def _pending_candidate(cover_set: CoverSet, relative: str) -> CoverCandidate | None:
    expected = Path("outputs/deliverables") / relative
    return next((item for item in cover_set.candidates if Path(item.relative_path) == expected), None)


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
            candidate = _pending_candidate(cover_set, relative)
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
