"""Immutable Novel OS projections for the H5 PublicationPackage V3 contract."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # Package import in tests; top-level import in the pipeline CLI.
    from .novel_classification import NovelClassification, infer_classification
    from .narrative_format import (
        NarrativeFormat,
        chapter_binding,
        infer_narrative_format,
        serialization_payload,
        validate_serialization,
    )
    from .publication_copy import PublicationCopy
    from .publication_source import (
        PublicationSourceSet,
        publication_source_input_hash,
    )
except ImportError:  # pragma: no cover - exercised through pipeline integration
    from novel_classification import NovelClassification, infer_classification
    from narrative_format import (
        NarrativeFormat,
        chapter_binding,
        infer_narrative_format,
        serialization_payload,
        validate_serialization,
    )
    from publication_copy import PublicationCopy
    from publication_source import (
        PublicationSourceSet,
        publication_source_input_hash,
    )


H5_PUBLICATION_POLICY_VERSION = "h5-publication-v3"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FINAL_CHECKS = {
    "body_hashes": True,
    "chapter_order": True,
    "delivery": True,
}
_PACKAGE_FIELDS = {
    "version",
    "platform",
    "primary_title",
    "alternate_titles",
    "hook_lead",
    "spoiler_free_blurb",
    "tags",
    "classification",
    "serialization",
    "chapters",
    "total_runes",
    "counting_policy",
    "final_checks",
    "finalization_source",
}
_CHAPTER_FIELDS = {
    "number", "title", "path", "rune_count", "body_sha256",
    "volume_id", "volume_number", "chapter_in_volume", "volume_role",
    "series_id", "series_book_number",
}
_FINALIZATION_FIELDS = {"merged_manuscript_sha256", "finalized_at", "receipts"}
_RECEIPT_FIELDS = {"chapter_number", "body_sha256", "verdict"}
_DELIVERY_FIELDS = {"snapshot_sha256", "delivered_at"}


class H5PublicationError(RuntimeError):
    """An H5 V3 projection failed its immutable byte contract."""


@dataclass(frozen=True)
class H5PublicationResult:
    package_id: str
    root: Path
    publication_package_path: Path
    finalization_path: Path
    delivery_path: Path
    snapshot_sha256: str


def merged_manuscript(chapter_bodies: Sequence[bytes]) -> bytes:
    if isinstance(chapter_bodies, (str, bytes, bytearray)):
        raise H5PublicationError("chapter bodies must be a byte sequence")
    if not chapter_bodies or not all(isinstance(item, bytes) for item in chapter_bodies):
        raise H5PublicationError("chapter bodies must contain nonempty bytes")
    return b"\n\n".join(chapter_bodies) + b"\n"


def delivery_snapshot(package_raw: bytes, finalization_raw: bytes) -> str:
    if not isinstance(package_raw, bytes) or not isinstance(finalization_raw, bytes):
        raise H5PublicationError("delivery snapshot inputs must be bytes")
    return hashlib.sha256(package_raw + finalization_raw).hexdigest()


def project_h5_publication(
    project: Path,
    run_id: str,
    source: PublicationSourceSet,
    publication_copy: PublicationCopy,
    book_check_finished_at: str,
    compile_finished_at: str,
    title: str,
    alternate_titles: Sequence[str],
    tags: Sequence[str],
    classification: Mapping[str, Any] | None = None,
    serialization: Mapping[str, Any] | None = None,
) -> H5PublicationResult | None:
    """Project Final chapter bytes into an immutable H5 V3 delivery root."""

    project = Path(project).resolve()
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise H5PublicationError("run_id is not a canonical package identifier")
    try:
        publication_source_input_hash(source)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise H5PublicationError(f"publication source is invalid: {exc}") from exc
    if source.run_id != run_id:
        raise H5PublicationError("publication source run_id is divergent")
    if len(source.chapters) < 4:
        return None

    primary_title = _trimmed(title, "primary title")
    alternates = _trimmed_sequence(alternate_titles, "alternate title")
    catalog_tags = _catalog_tags(tags)
    try:
        canonical_classification = (
            NovelClassification.from_dict(classification)
            if classification is not None
            else infer_classification(
                genre=" ".join(catalog_tags),
                premise=str(getattr(publication_copy, "spoiler_free_blurb", "") or ""),
                source="legacy_migration",
            )
        )
    except (TypeError, ValueError) as exc:
        raise H5PublicationError(f"novel classification is invalid: {exc}") from exc
    try:
        if serialization is not None:
            canonical_serialization = validate_serialization(serialization)
        else:
            fallback_format = infer_narrative_format(
                canonical_classification,
                chapters=len(source.chapters),
                target_words=max(
                    len(source.chapters),
                    sum(len(chapter.text.split()) for chapter in source.chapters),
                ),
                premise=str(getattr(publication_copy, "spoiler_free_blurb", "") or ""),
                explicit_length=True,
                source="legacy_migration",
            )
            canonical_serialization = serialization_payload(
                fallback_format, None, allow_legacy_defaults=True
            )
        format_contract = NarrativeFormat.from_dict(
            canonical_serialization["format"]
        )
        if format_contract.total_chapters != len(source.chapters):
            raise ValueError("serialization chapter count differs from Final source")
    except (KeyError, TypeError, ValueError) as exc:
        raise H5PublicationError(f"novel serialization is invalid: {exc}") from exc
    hook_lead = _trimmed(
        getattr(publication_copy, "hook_lead", None), "publication hook_lead"
    )
    spoiler_free_blurb = _trimmed(
        getattr(publication_copy, "spoiler_free_blurb", None),
        "publication spoiler_free_blurb",
    )
    copy_source = getattr(publication_copy, "source", None)
    if copy_source is not None and (
        getattr(copy_source, "run_id", None) != run_id
        or getattr(copy_source, "source_set_sha256", None)
        != source.source_set_sha256
    ):
        raise H5PublicationError("publication copy source binding is divergent")
    copy_title = getattr(publication_copy, "title", None)
    if copy_title is not None and copy_title != primary_title:
        raise H5PublicationError("publication copy title binding is divergent")
    finalized_at = _trimmed(book_check_finished_at, "book_check finished_at")
    delivered_at = _trimmed(compile_finished_at, "compile finished_at")

    chapter_bodies = [chapter.text.encode("utf-8") for chapter in source.chapters]
    chapter_records = []
    for chapter in source.chapters:
        record = {
            "number": chapter.number,
            "title": chapter.title,
            "path": f"chapters/{chapter.number:02d}.md",
            "rune_count": len(chapter.text),
            "body_sha256": chapter.sha256,
        }
        record.update(chapter_binding(
            format_contract,
            chapter.number,
            canonical_serialization["volumes"],
        ))
        chapter_records.append(record)
    package_payload = {
        "version": 3,
        "platform": "novel-os",
        "primary_title": primary_title,
        "alternate_titles": alternates,
        "hook_lead": hook_lead,
        "spoiler_free_blurb": spoiler_free_blurb,
        "tags": catalog_tags,
        "classification": canonical_classification.to_dict(),
        "serialization": canonical_serialization,
        "chapters": chapter_records,
        "total_runes": sum(item["rune_count"] for item in chapter_records),
        "counting_policy": "utf8-runes",
        "final_checks": dict(_FINAL_CHECKS),
        "finalization_source": f"novel-os run {run_id} book.check",
    }
    merged = merged_manuscript(chapter_bodies)
    finalization_payload = {
        "merged_manuscript_sha256": hashlib.sha256(merged).hexdigest(),
        "finalized_at": finalized_at,
        "receipts": [
            {
                "chapter_number": chapter.number,
                "body_sha256": chapter.sha256,
                "verdict": "accepted",
            }
            for chapter in source.chapters
        ],
    }
    package_raw = _json_bytes(package_payload)
    finalization_raw = _json_bytes(finalization_payload)
    snapshot_sha256 = delivery_snapshot(package_raw, finalization_raw)
    delivery_raw = _json_bytes({
        "snapshot_sha256": snapshot_sha256,
        "delivered_at": delivered_at,
    })

    package_id = f"pkg-{run_id}-{snapshot_sha256[:12]}"
    parent = project / "outputs/deliverables/h5-publication"
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{package_id}.tmp-", dir=parent))
    target = parent / package_id
    try:
        _write_projection(
            temporary,
            chapter_bodies,
            package_raw,
            finalization_raw,
            delivery_raw,
            merged,
        )
        validated_snapshot = validate_h5_publication_root(temporary)
        if validated_snapshot != snapshot_sha256:
            raise H5PublicationError("self-validation changed the delivery snapshot")
        if not _rename_directory_noreplace(temporary, target):
            if _directory_bytes(target) != _directory_bytes(temporary):
                raise H5PublicationError(
                    f"existing H5 publication root {package_id} is not byte-identical"
                )
            shutil.rmtree(temporary)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise

    return H5PublicationResult(
        package_id=package_id,
        root=target,
        publication_package_path=target / "meta/publication_package.json",
        finalization_path=target / "meta/finalization.json",
        delivery_path=target / "meta/delivery.json",
        snapshot_sha256=snapshot_sha256,
    )


def validate_h5_publication_root(
    root: Path,
    *,
    expected_finalized_at: str | None = None,
    expected_delivered_at: str | None = None,
) -> str:
    """Validate the generated bytes with the H5 V3 importer's rules."""

    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise H5PublicationError("H5 publication root must be an ordinary directory")
    package_raw = _ordinary_bytes(root / "meta/publication_package.json")
    finalization_raw = _ordinary_bytes(root / "meta/finalization.json")
    delivery_raw = _ordinary_bytes(root / "meta/delivery.json")
    package = _json_object(package_raw, "publication package")
    _exact_fields(package, _PACKAGE_FIELDS, "publication package")
    if package["version"] != 3 or package["platform"] != "novel-os":
        raise H5PublicationError("publication package version or platform is invalid")
    _trimmed(package["primary_title"], "primary title")
    _trimmed_sequence(package["alternate_titles"], "alternate title")
    _trimmed(package["hook_lead"], "hook_lead")
    _trimmed(package["spoiler_free_blurb"], "spoiler_free_blurb")
    _catalog_tags(package["tags"])
    try:
        NovelClassification.from_dict(package["classification"])
    except (TypeError, ValueError) as exc:
        raise H5PublicationError(f"publication classification is invalid: {exc}") from exc
    try:
        serialization = validate_serialization(package["serialization"])
        format_contract = NarrativeFormat.from_dict(serialization["format"])
    except (KeyError, TypeError, ValueError) as exc:
        raise H5PublicationError(f"publication serialization is invalid: {exc}") from exc
    if package["counting_policy"] != "utf8-runes":
        raise H5PublicationError("publication counting_policy must be utf8-runes")
    if package["final_checks"] != _FINAL_CHECKS:
        raise H5PublicationError("publication final_checks are not all pass")
    _trimmed(package["finalization_source"], "finalization_source")

    records = package["chapters"]
    if not isinstance(records, list) or len(records) < 4:
        raise H5PublicationError("publication package must contain at least 4 chapters")
    if format_contract.total_chapters != len(records):
        raise H5PublicationError("serialization chapter count differs from package chapters")
    titles: set[str] = set()
    chapter_bodies: list[bytes] = []
    chapter_hashes: list[str] = []
    total_runes = 0
    for expected_number, record in enumerate(records, start=1):
        _exact_fields(record, _CHAPTER_FIELDS, "publication chapter")
        if record["number"] != expected_number:
            raise H5PublicationError("chapter numbers must be contiguous from 1")
        expected_binding = chapter_binding(
            format_contract, expected_number, serialization["volumes"]
        )
        if any(record.get(name) != value for name, value in expected_binding.items()):
            raise H5PublicationError(
                f"chapter {expected_number} serialization binding is divergent"
            )
        expected_path = f"chapters/{expected_number:02d}.md"
        if record["path"] != expected_path:
            raise H5PublicationError(f"chapter {expected_number} path is noncanonical")
        chapter_title = _trimmed(record["title"], f"chapter {expected_number} title")
        if chapter_title in titles:
            raise H5PublicationError(f"chapter title {chapter_title!r} is duplicated")
        titles.add(chapter_title)
        body = _ordinary_bytes(root / expected_path)
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise H5PublicationError(
                f"chapter {expected_number} is not valid UTF-8"
            ) from exc
        actual_hash = hashlib.sha256(body).hexdigest()
        if record["body_sha256"] != actual_hash or not _SHA256.fullmatch(actual_hash):
            raise H5PublicationError(f"chapter {expected_number} body hash is divergent")
        if type(record["rune_count"]) is not int or record["rune_count"] != len(text):
            raise H5PublicationError(f"chapter {expected_number} rune count is divergent")
        total_runes += record["rune_count"]
        chapter_bodies.append(body)
        chapter_hashes.append(actual_hash)
    if package["total_runes"] != total_runes:
        raise H5PublicationError("publication total_runes is divergent")

    merged = merged_manuscript(chapter_bodies)
    if _ordinary_bytes(root / "正文.md") != merged:
        raise H5PublicationError("merged manuscript is divergent")
    finalization = _json_object(finalization_raw, "finalization")
    _exact_fields(finalization, _FINALIZATION_FIELDS, "finalization")
    finalized_at = _trimmed(finalization["finalized_at"], "finalized_at")
    if expected_finalized_at is not None and finalized_at != expected_finalized_at:
        raise H5PublicationError("finalization timestamp is divergent")
    if finalization["merged_manuscript_sha256"] != hashlib.sha256(merged).hexdigest():
        raise H5PublicationError("finalization merged manuscript hash is divergent")
    receipts = finalization["receipts"]
    if not isinstance(receipts, list) or len(receipts) != len(records):
        raise H5PublicationError("finalization receipts do not cover every chapter")
    for number, (receipt, body_sha256) in enumerate(
        zip(receipts, chapter_hashes), start=1
    ):
        _exact_fields(receipt, _RECEIPT_FIELDS, "finalization receipt")
        if (
            receipt["chapter_number"] != number
            or receipt["body_sha256"] != body_sha256
            or receipt["verdict"] != "accepted"
        ):
            raise H5PublicationError(f"chapter {number} receipt is divergent")

    delivery = _json_object(delivery_raw, "delivery")
    _exact_fields(delivery, _DELIVERY_FIELDS, "delivery")
    delivered_at = _trimmed(delivery["delivered_at"], "delivered_at")
    if expected_delivered_at is not None and delivered_at != expected_delivered_at:
        raise H5PublicationError("delivery timestamp is divergent")
    snapshot_sha256 = delivery_snapshot(package_raw, finalization_raw)
    if delivery["snapshot_sha256"] != snapshot_sha256:
        raise H5PublicationError("delivery snapshot is divergent")

    expected_files = {
        "meta/publication_package.json",
        "meta/finalization.json",
        "meta/delivery.json",
        "正文.md",
        *(f"chapters/{number:02d}.md" for number in range(1, len(records) + 1)),
    }
    if set(_directory_bytes(root)) != expected_files:
        raise H5PublicationError("H5 publication root contains an unexpected file set")
    return snapshot_sha256


def _write_projection(
    root: Path,
    chapter_bodies: Sequence[bytes],
    package_raw: bytes,
    finalization_raw: bytes,
    delivery_raw: bytes,
    merged: bytes,
) -> None:
    for number, body in enumerate(chapter_bodies, start=1):
        _write_bytes(root / f"chapters/{number:02d}.md", body)
    _write_bytes(root / "meta/publication_package.json", package_raw)
    _write_bytes(root / "meta/finalization.json", finalization_raw)
    _write_bytes(root / "meta/delivery.json", delivery_raw)
    _write_bytes(root / "正文.md", merged)


def _rename_directory_noreplace(source: Path, target: Path) -> bool:
    """Atomically publish ``source`` without replacing an existing target."""

    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    target_bytes = os.fsencode(target)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(source_bytes, target_bytes, 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, source_bytes, -100, target_bytes, 0x00000001)
    elif os.name == "nt":  # os.rename is no-replace on Windows.
        try:
            os.rename(source, target)
            return True
        except FileExistsError:
            return False
    else:
        raise H5PublicationError(
            f"atomic no-replace directory publication is unsupported on {sys.platform}"
        )
    if result == 0:
        return True
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        return False
    raise OSError(error_number, os.strerror(error_number), str(target))


def _write_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def _ordinary_bytes(path: Path) -> bytes:
    try:
        information = path.lstat()
    except OSError as exc:
        raise H5PublicationError(
            f"H5 publication object is missing: {path.name}"
        ) from exc
    if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1:
        raise H5PublicationError(
            f"H5 publication object must be an ordinary single-link file: {path.name}"
        )
    return path.read_bytes()


def _directory_bytes(root: Path) -> dict[str, bytes]:
    if root.is_symlink() or not root.is_dir():
        raise H5PublicationError("H5 publication root must be an ordinary directory")
    values: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise H5PublicationError("H5 publication root contains a symbolic link")
        if path.is_dir():
            continue
        values[path.relative_to(root).as_posix()] = _ordinary_bytes(path)
    return values


def _json_bytes(payload: dict[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise H5PublicationError(f"H5 publication JSON is invalid: {exc}") from exc


def _json_object(raw: bytes, name: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise H5PublicationError(f"{name} contains duplicate key {key}")
            value[key] = item
        return value

    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise H5PublicationError(f"{name} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise H5PublicationError(f"{name} must be a JSON object")
    return value


def _exact_fields(value: Any, fields: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise H5PublicationError(f"{name} fields are invalid")


def _trimmed(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise H5PublicationError(f"{name} must be a nonempty trimmed string")
    return value


def _trimmed_sequence(value: Any, name: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise H5PublicationError(f"{name} values must be a sequence")
    result = [_trimmed(item, name) for item in value]
    if len(result) != len(set(result)):
        raise H5PublicationError(f"duplicate {name}")
    return result


def _catalog_tags(value: Any) -> list[str]:
    tags = _trimmed_sequence(value, "tag")
    if len(tags) > 12:
        raise H5PublicationError("catalog tags exceed the 12 item bound")
    for tag in tags:
        size = len(tag.encode("utf-8"))
        if not 1 <= size <= 64:
            raise H5PublicationError("catalog tag must be 1..64 UTF-8 bytes")
        if tag != tag.strip(" ") or any(ord(char) < 32 or ord(char) == 127 for char in tag):
            raise H5PublicationError("catalog tag is noncanonical")
    return tags
