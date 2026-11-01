"""Read-only manuscript projection for the in-app book reader.

The reader deliberately bypasses ``ProjectService._load``.  It reads the
persisted story index and manuscript authorities without ingesting legacy
files, advancing artifact heads, or creating artifact locks/directories.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping

import fcntl

from core.artifacts import (
    ArtifactCorruptionError,
    ArtifactHead,
    ArtifactIntegrityError,
    ArtifactRevision,
    ArtifactStore,
    DuplicateArtifactRevision,
    _derive_revision_id,
    _head_from_record,
    _revision_from_record,
    _validate_contract_references,
    _validate_parent_reference,
)
from core.prose_sanitize import extract_html_comments, strip_state_blocks

from core.safe_project_io import (
    ProjectPathError,
    open_project_file,
    read_project_bytes,
    read_project_text,
)


ReaderVersion = Literal["final", "revised", "draft"]
_VERSIONS: tuple[ReaderVersion, ...] = ("final", "revised", "draft")

class ReaderSourceError(RuntimeError):
    """A persisted reader authority is malformed, unsafe, or unreadable."""


@dataclass(frozen=True)
class _ChapterRecord:
    number: int
    title: str
    status: str


@dataclass(frozen=True)
class _ReaderState:
    title: str
    author: str
    chapters: tuple[_ChapterRecord, ...]


@dataclass(frozen=True)
class _ArtifactSnapshot:
    revisions: Mapping[str, ArtifactRevision]
    heads: Mapping[tuple[int, str], ArtifactHead]


@dataclass(frozen=True)
class _SelectedProse:
    version: ReaderVersion | None
    markdown: str
    header_title: str
    raw: str = ""
    source: str = ""


def _load_reader_state(project: Path, project_id: str) -> _ReaderState:
    try:
        raw = read_project_text(
            project,
            ("outputs", "state", "story_state.json"),
        )
        payload = json.loads(raw or "")
    except json.JSONDecodeError as exc:
        raise ReaderSourceError("Story state is invalid JSON.") from exc
    except ProjectPathError as exc:
        raise ReaderSourceError("Story state cannot be read safely.") from exc
    if not isinstance(payload, dict):
        raise ReaderSourceError("Story state must contain an object.")

    metadata = payload.get("metadata", {})
    chapters_payload = payload.get("chapters", {})
    if not isinstance(metadata, dict) or not isinstance(chapters_payload, dict):
        raise ReaderSourceError("Story state has an invalid reader index.")

    chapters: list[_ChapterRecord] = []
    seen: set[int] = set()
    for raw in chapters_payload.values():
        if not isinstance(raw, dict):
            raise ReaderSourceError("Story state has an invalid chapter record.")
        number = raw.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ReaderSourceError("Story state has an invalid chapter number.")
        if number in seen:
            raise ReaderSourceError("Story state has duplicate chapter numbers.")
        seen.add(number)
        chapters.append(
            _ChapterRecord(
                number=number,
                title=str(raw.get("title") or "").strip(),
                status=str(raw.get("status") or "").strip(),
            )
        )

    return _ReaderState(
        title=str(metadata.get("title") or project_id).strip() or project_id,
        author=str(metadata.get("author") or "").strip(),
        chapters=tuple(sorted(chapters, key=lambda chapter: chapter.number)),
    )


@contextmanager
def _existing_artifact_lock(project: Path) -> Iterator[None]:
    """Share an existing artifact lock, without creating one when absent."""
    try:
        with open_project_file(
            project,
            ("outputs", "artifacts", ".artifact.lock"),
            missing_ok=True,
        ) as descriptor:
            if descriptor is None:
                yield
                return
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    except ProjectPathError as exc:
        raise ReaderSourceError("Artifact lock cannot be opened safely.") from exc
    except OSError as exc:
        raise ReaderSourceError("Artifact lock cannot be read.") from exc


def _load_revisions(raw: bytes | None) -> dict[str, ArtifactRevision]:
    if raw is None:
        return {}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactCorruptionError("artifact revisions are not UTF-8") from exc
    revisions: dict[str, ArtifactRevision] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise ArtifactCorruptionError(
                f"artifact revision line {line_number} is blank"
            )
        try:
            revision = _revision_from_record(json.loads(line))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ArtifactCorruptionError(
                f"artifact revision line {line_number} is invalid"
            ) from exc
        expected_id = _derive_revision_id(
            {
                key: value
                for key, value in revision.to_dict().items()
                if key != "revision_id"
            }
        )
        if revision.revision_id != expected_id:
            raise ArtifactCorruptionError("artifact revision ID is invalid")
        if revision.revision_id in revisions:
            raise DuplicateArtifactRevision("duplicate artifact revision ID")
        revisions[revision.revision_id] = revision

    for revision in revisions.values():
        try:
            _validate_parent_reference(
                revisions,
                parent_revision_id=revision.parent_revision_id,
                child_chapter=revision.chapter,
            )
            _validate_contract_references(
                revisions,
                chapter=revision.chapter,
                story_contract_revision_id=revision.story_contract_revision_id,
                chapter_contract_revision_id=revision.chapter_contract_revision_id,
            )
        except (KeyError, ValueError) as exc:
            raise ArtifactCorruptionError(
                "artifact revision references are invalid"
            ) from exc
    return revisions


def _load_heads(
    raw: bytes | None,
    revisions: Mapping[str, ArtifactRevision],
) -> dict[tuple[int, str], ArtifactHead]:
    if raw is None:
        return {}
    try:
        payload: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactCorruptionError("artifact heads are invalid JSON") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "heads"}
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("heads"), list)
    ):
        raise ArtifactCorruptionError("artifact heads have an invalid schema")

    heads: dict[tuple[int, str], ArtifactHead] = {}
    for record in payload["heads"]:
        try:
            head = _head_from_record(record)
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactCorruptionError("artifact head is invalid") from exc
        key = (head.chapter, head.kind)
        if key in heads:
            raise ArtifactCorruptionError("artifact head is duplicated")
        revision = revisions.get(head.revision_id)
        if revision is None:
            raise ArtifactCorruptionError("artifact head references an unknown revision")
        if revision.chapter != head.chapter or revision.kind != head.kind:
            raise ArtifactCorruptionError("artifact head target is invalid")
        heads[key] = head
    return heads


@contextmanager
def _artifact_snapshot(project: Path) -> Iterator[_ArtifactSnapshot | None]:
    store = ArtifactStore(project)
    try:
        with store._process_lock:  # Match writers in this process without I/O.
            with _existing_artifact_lock(project):
                revisions_raw = read_project_bytes(
                    project,
                    ("outputs", "artifacts", "revisions.jsonl"),
                    missing_ok=True,
                )
                heads_raw = read_project_bytes(
                    project,
                    ("outputs", "artifacts", "heads.json"),
                    missing_ok=True,
                )
                if revisions_raw is None and heads_raw is None:
                    yield None
                    return
                revisions = _load_revisions(revisions_raw)
                heads = _load_heads(heads_raw, revisions)
                yield _ArtifactSnapshot(revisions, heads)
    except ProjectPathError as exc:
        raise ReaderSourceError("Canonical manuscript metadata is unsafe.") from exc
    except (ArtifactCorruptionError, ArtifactIntegrityError) as exc:
        raise ReaderSourceError("Canonical manuscript metadata is invalid.") from exc


def _clean_prose(text: str) -> tuple[str, str]:
    without_comments, comments = extract_html_comments(text)
    markdown = strip_state_blocks(without_comments).strip()
    header_title = ""
    for comment in comments:
        title = comment.get("title")
        if isinstance(title, str) and title.strip():
            header_title = title.strip()
    return markdown, header_title


def _canonical_text(
    snapshot: _ArtifactSnapshot | None,
    project: Path,
    chapter: int,
    version: ReaderVersion,
) -> str | None:
    if snapshot is None:
        return None
    head = snapshot.heads.get((chapter, version))
    if head is None:
        return None
    revision = snapshot.revisions[head.revision_id]
    try:
        data = read_project_bytes(
            project,
            ("outputs", "artifacts", "sha256", revision.sha256),
            missing_ok=True,
        )
    except ProjectPathError as exc:
        raise ReaderSourceError(
            "Canonical manuscript content cannot be read safely."
        ) from exc
    if data is None:
        raise ReaderSourceError("Canonical manuscript content is missing.")
    actual_sha = hashlib.sha256(data).hexdigest()
    if len(data) != revision.byte_length or actual_sha != revision.sha256:
        raise ReaderSourceError("Canonical manuscript content failed integrity checks.")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReaderSourceError("Canonical manuscript content is not UTF-8.") from exc


def _selected_prose(
    project: Path,
    chapter: int,
    snapshot: _ArtifactSnapshot | None,
) -> _SelectedProse:
    number = f"{chapter:03d}"
    for version in _VERSIONS:
        canonical = _canonical_text(snapshot, project, chapter, version)
        if canonical is not None:
            markdown, title = _clean_prose(canonical)
            # A promoted head is authoritative even when it records a
            # deliberate deletion. Never resurrect an older legacy projection.
            if not markdown:
                return _SelectedProse(version, "", title, canonical, snapshot.heads[(chapter, version)].revision_id)
            return _SelectedProse(version, markdown, title, canonical, snapshot.heads[(chapter, version)].revision_id)

        try:
            legacy = read_project_text(
                project,
                (
                    "outputs",
                    "manuscript",
                    f"chapter_{number}_{version}.md",
                ),
                missing_ok=True,
            )
        except ProjectPathError as exc:
            raise ReaderSourceError("Legacy manuscript cannot be read safely.") from exc
        if legacy is not None:
            markdown, title = _clean_prose(legacy)
            # An existing higher-stage legacy projection remains authoritative
            # when its readable body was deliberately cleared.
            return _SelectedProse(version, markdown, title, legacy, f"outputs/manuscript/chapter_{number}_{version}.md")

    return _SelectedProse(None, "", "")
