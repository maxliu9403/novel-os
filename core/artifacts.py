"""Immutable artifact revisions with content-addressed UTF-8 blobs.

Story contracts use ``chapter=0``. Chapter contracts use their positive chapter
number. Other artifact kinds also belong to a positive chapter. Persisting a
revision never advances a head or writes a legacy manuscript projection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_FIELDS = {
    "revision_id",
    "chapter",
    "kind",
    "sha256",
    "byte_length",
    "parent_revision_id",
    "source",
    "provider",
    "model",
    "prompt_sha256",
    "story_contract_revision_id",
    "chapter_contract_revision_id",
    "timestamp",
}
_HEAD_FIELDS = {"chapter", "kind", "revision_id", "updated_at"}
_PROJECT_LOCKS: Dict[str, threading.RLock] = {}
_PROJECT_LOCKS_GUARD = threading.Lock()


class ArtifactError(Exception):
    """Base class for artifact persistence failures."""


class ArtifactCommitUncertain(ArtifactError):
    """Raised when a replaced artifact file may not be durably committed.

    The current file may already contain the revision or head update. Callers
    must reconcile by ``revision_id`` before retrying and must not blindly issue
    a new write.
    """

    def __init__(self, *, operation: str, revision_id: str) -> None:
        self.operation = operation
        self.revision_id = revision_id
        super().__init__(
            f"{operation} for revision {revision_id} may already be committed; "
            "reconcile the persisted revision/head by revision_id before retrying"
        )


class ArtifactCorruptionError(ArtifactError):
    """Raised when an artifact metadata or head file is malformed."""


class ArtifactIntegrityError(ArtifactError):
    """Raised when blob bytes no longer match their immutable revision."""


class DuplicateArtifactRevision(ArtifactCorruptionError):
    """Raised instead of accepting two records with one revision ID."""


class StaleArtifactHead(ArtifactError):
    """Raised when a compare-and-swap head expectation is stale."""


@dataclass(frozen=True)
class ArtifactRevision:
    revision_id: str
    chapter: int
    kind: str
    sha256: str
    byte_length: int
    parent_revision_id: Optional[str]
    source: str
    provider: Optional[str]
    model: Optional[str]
    prompt_sha256: Optional[str]
    story_contract_revision_id: Optional[str]
    chapter_contract_revision_id: Optional[str]
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "chapter": self.chapter,
            "kind": self.kind,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "parent_revision_id": self.parent_revision_id,
            "source": self.source,
            "provider": self.provider,
            "model": self.model,
            "prompt_sha256": self.prompt_sha256,
            "story_contract_revision_id": self.story_contract_revision_id,
            "chapter_contract_revision_id": self.chapter_contract_revision_id,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class ArtifactHead:
    chapter: int
    kind: str
    revision_id: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chapter": self.chapter,
            "kind": self.kind,
            "revision_id": self.revision_id,
            "updated_at": self.updated_at,
        }


class ArtifactStore:
    """File-backed store for immutable artifact revisions and CAS heads.

    ``clock`` is a controlled time boundary for deterministic tests and imports.
    The normalized UTC timestamp participates in the revision ID, so a persisted
    record can be verified later and an exact repeated record is a duplicate.

    This store provides atomic file replacement and single-process CAS semantics.
    Cross-process writers must hold the higher-level per-project promotion lock.
    """

    def __init__(
        self,
        project_root: Path | str,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.artifact_root = self.project_root / "outputs" / "artifacts"
        self.blob_root = self.artifact_root / "sha256"
        self.revisions_path = self.artifact_root / "revisions.jsonl"
        self.heads_path = self.artifact_root / "heads.json"
        self._process_lock = _project_process_lock(self.artifact_root)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def put_text(
        self,
        *,
        chapter: int,
        kind: str,
        text: str,
        source: str,
        parent_revision_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        prompt_sha256: Optional[str] = None,
        story_contract_revision_id: Optional[str] = None,
        chapter_contract_revision_id: Optional[str] = None,
    ) -> ArtifactRevision:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        return self._put_bytes(
            chapter=chapter,
            kind=kind,
            data=text.encode("utf-8"),
            source=source,
            parent_revision_id=parent_revision_id,
            provider=provider,
            model=model,
            prompt_sha256=prompt_sha256,
            story_contract_revision_id=story_contract_revision_id,
            chapter_contract_revision_id=chapter_contract_revision_id,
        )

    def put_json(
        self,
        *,
        chapter: int,
        kind: str,
        value: Any,
        source: str,
        parent_revision_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        prompt_sha256: Optional[str] = None,
        story_contract_revision_id: Optional[str] = None,
        chapter_contract_revision_id: Optional[str] = None,
    ) -> ArtifactRevision:
        data = _canonical_json(value)
        return self._put_bytes(
            chapter=chapter,
            kind=kind,
            data=data,
            source=source,
            parent_revision_id=parent_revision_id,
            provider=provider,
            model=model,
            prompt_sha256=prompt_sha256,
            story_contract_revision_id=story_contract_revision_id,
            chapter_contract_revision_id=chapter_contract_revision_id,
        )

    def get_revision(self, revision_id: str) -> ArtifactRevision:
        revisions = self._load_revisions()
        try:
            return revisions[revision_id]
        except KeyError:
            raise KeyError(f"unknown artifact revision: {revision_id}") from None

    def read_text(self, revision_id: str) -> str:
        revision = self.get_revision(revision_id)
        return self._read_verified_text(revision)

    def _read_verified_text(self, revision: ArtifactRevision) -> str:
        path = self.blob_root / revision.sha256
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ArtifactIntegrityError(
                f"cannot read blob for revision {revision.revision_id}: {exc}"
            ) from exc

        actual_sha = hashlib.sha256(data).hexdigest()
        if len(data) != revision.byte_length or actual_sha != revision.sha256:
            raise ArtifactIntegrityError(
                f"blob integrity check failed for revision {revision.revision_id}: "
                f"expected {revision.byte_length} bytes/{revision.sha256}, "
                f"found {len(data)} bytes/{actual_sha}"
            )
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactIntegrityError(
                f"blob for revision {revision.revision_id} is not valid UTF-8"
            ) from exc

    def get_head(self, chapter: int, kind: str) -> Optional[ArtifactHead]:
        _validate_chapter_kind(chapter, kind)
        revisions = self._load_revisions()
        return self._load_heads(revisions).get((chapter, kind))

    def set_head(
        self,
        chapter: int,
        kind: str,
        revision_id: str,
        *,
        expected_revision_id: Optional[str],
    ) -> ArtifactHead:
        with self._process_lock:
            return self._set_head_locked(
                chapter,
                kind,
                revision_id,
                expected_revision_id=expected_revision_id,
            )

    def _set_head_locked(
        self,
        chapter: int,
        kind: str,
        revision_id: str,
        *,
        expected_revision_id: Optional[str],
    ) -> ArtifactHead:
        _validate_chapter_kind(chapter, kind)
        revisions = self._load_revisions()
        try:
            revision = revisions[revision_id]
        except KeyError:
            raise KeyError(f"unknown artifact revision: {revision_id}") from None
        if revision.chapter != chapter or revision.kind != kind:
            raise ValueError(
                f"revision {revision_id} ({revision.chapter}, {revision.kind!r}) "
                f"does not match head target ({chapter}, {kind!r})"
            )
        self._read_verified_text(revision)

        heads = self._load_heads(revisions)
        key = (chapter, kind)
        current = heads.get(key)

        # A retried successful promotion remains successful with its old CAS token.
        if current is not None and current.revision_id == revision_id:
            return current

        current_revision_id = current.revision_id if current is not None else None
        if current_revision_id != expected_revision_id:
            raise StaleArtifactHead(
                f"stale head for chapter {chapter} kind {kind!r}: "
                f"expected {expected_revision_id!r}, current {current_revision_id!r}"
            )

        head = ArtifactHead(
            chapter=chapter,
            kind=kind,
            revision_id=revision_id,
            updated_at=self._utc_timestamp(),
        )
        heads[key] = head
        self._write_heads(heads, revision_id=revision_id)
        return head

    def _put_bytes(
        self,
        *,
        chapter: int,
        kind: str,
        data: bytes,
        source: str,
        parent_revision_id: Optional[str],
        provider: Optional[str],
        model: Optional[str],
        prompt_sha256: Optional[str],
        story_contract_revision_id: Optional[str],
        chapter_contract_revision_id: Optional[str],
    ) -> ArtifactRevision:
        with self._process_lock:
            return self._put_bytes_locked(
                chapter=chapter,
                kind=kind,
                data=data,
                source=source,
                parent_revision_id=parent_revision_id,
                provider=provider,
                model=model,
                prompt_sha256=prompt_sha256,
                story_contract_revision_id=story_contract_revision_id,
                chapter_contract_revision_id=chapter_contract_revision_id,
            )

    def _put_bytes_locked(
        self,
        *,
        chapter: int,
        kind: str,
        data: bytes,
        source: str,
        parent_revision_id: Optional[str],
        provider: Optional[str],
        model: Optional[str],
        prompt_sha256: Optional[str],
        story_contract_revision_id: Optional[str],
        chapter_contract_revision_id: Optional[str],
    ) -> ArtifactRevision:
        _validate_chapter_kind(chapter, kind)
        source = _required_string(source, "source")
        provider = _optional_string(provider, "provider")
        model = _optional_string(model, "model")
        _validate_optional_sha(parent_revision_id, "parent_revision_id")
        _validate_optional_sha(prompt_sha256, "prompt_sha256")
        _validate_optional_sha(
            story_contract_revision_id,
            "story_contract_revision_id",
        )
        _validate_optional_sha(
            chapter_contract_revision_id,
            "chapter_contract_revision_id",
        )

        revisions = self._load_revisions()
        _validate_contract_references(
            revisions,
            chapter=chapter,
            story_contract_revision_id=story_contract_revision_id,
            chapter_contract_revision_id=chapter_contract_revision_id,
        )
        _validate_parent_reference(
            revisions,
            parent_revision_id=parent_revision_id,
            child_chapter=chapter,
        )

        digest = hashlib.sha256(data).hexdigest()
        metadata: Dict[str, Any] = {
            "chapter": chapter,
            "kind": kind,
            "sha256": digest,
            "byte_length": len(data),
            "parent_revision_id": parent_revision_id,
            "source": source,
            "provider": provider,
            "model": model,
            "prompt_sha256": prompt_sha256,
            "story_contract_revision_id": story_contract_revision_id,
            "chapter_contract_revision_id": chapter_contract_revision_id,
            "timestamp": self._utc_timestamp(),
        }
        revision_id = _derive_revision_id(metadata)
        if revision_id in revisions:
            raise DuplicateArtifactRevision(
                f"duplicate artifact revision ID: {revision_id}"
            )

        revision = ArtifactRevision(revision_id=revision_id, **metadata)
        self._write_blob(data, digest)
        self._append_revision(revision)
        return revision

    def _write_blob(self, data: bytes, digest: str) -> None:
        self.blob_root.mkdir(parents=True, exist_ok=True)
        target = self.blob_root / digest
        if target.exists():
            self._verify_existing_blob(target, data, digest)
            return

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{digest}.tmp-",
            dir=str(self.blob_root),
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if target.exists():
                self._verify_existing_blob(target, data, digest)
            else:
                os.replace(temp_name, target)
                temp_name = ""
                # Revision commit has not started; failure leaves a safe orphan blob.
                _fsync_directory(self.blob_root)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _verify_existing_blob(target: Path, expected: bytes, digest: str) -> None:
        try:
            existing = target.read_bytes()
        except OSError as exc:
            raise ArtifactIntegrityError(
                f"cannot verify existing artifact blob {target}: {exc}"
            ) from exc
        actual_sha = hashlib.sha256(existing).hexdigest()
        if existing != expected or actual_sha != digest:
            raise ArtifactIntegrityError(
                f"existing artifact blob {target} is corrupt; refusing to overwrite it"
            )

    def _append_revision(self, revision: ArtifactRevision) -> None:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        line = _canonical_json(revision.to_dict()) + b"\n"
        temp_name = ""
        try:
            existing = (
                self.revisions_path.read_bytes()
                if self.revisions_path.exists()
                else b""
            )
            separator = b"" if not existing or existing.endswith(b"\n") else b"\n"
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{self.revisions_path.name}.tmp-",
                dir=str(self.artifact_root),
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(existing + separator + line)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.revisions_path)
            temp_name = ""
            try:
                _fsync_directory(self.artifact_root)
            except OSError as exc:
                raise ArtifactCommitUncertain(
                    operation="revision_log_append",
                    revision_id=revision.revision_id,
                ) from exc
        except OSError as exc:
            raise ArtifactCorruptionError(
                f"cannot atomically append {self.revisions_path}: {exc}"
            ) from exc
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def _load_revisions(self) -> Dict[str, ArtifactRevision]:
        if not self.revisions_path.exists():
            return {}
        try:
            raw = self.revisions_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ArtifactCorruptionError(
                f"cannot read {self.revisions_path}: {exc}"
            ) from exc

        revisions: Dict[str, ArtifactRevision] = {}
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                raise ArtifactCorruptionError(
                    f"{self.revisions_path}:{line_number} is blank"
                )
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ArtifactCorruptionError(
                    f"{self.revisions_path}:{line_number} is invalid JSON: {exc}"
                ) from exc
            try:
                revision = _revision_from_record(record)
            except (KeyError, TypeError, ValueError) as exc:
                raise ArtifactCorruptionError(
                    f"{self.revisions_path}:{line_number} is invalid: {exc}"
                ) from exc

            expected_revision_id = _derive_revision_id(
                {
                    key: value
                    for key, value in revision.to_dict().items()
                    if key != "revision_id"
                }
            )
            if revision.revision_id != expected_revision_id:
                raise ArtifactCorruptionError(
                    f"{self.revisions_path}:{line_number} revision ID does not "
                    "match its metadata"
                )
            if revision.revision_id in revisions:
                raise DuplicateArtifactRevision(
                    f"duplicate artifact revision ID {revision.revision_id} in "
                    f"{self.revisions_path}:{line_number}"
                )
            revisions[revision.revision_id] = revision

        for revision in revisions.values():
            try:
                _validate_parent_reference(
                    revisions,
                    parent_revision_id=revision.parent_revision_id,
                    child_chapter=revision.chapter,
                )
            except (KeyError, ValueError) as exc:
                raise ArtifactCorruptionError(
                    f"{self.revisions_path} revision {revision.revision_id} has "
                    f"an invalid parent chain: {exc}"
                ) from exc

        for revision in revisions.values():
            try:
                _validate_contract_references(
                    revisions,
                    chapter=revision.chapter,
                    story_contract_revision_id=revision.story_contract_revision_id,
                    chapter_contract_revision_id=revision.chapter_contract_revision_id,
                )
            except (KeyError, ValueError) as exc:
                raise ArtifactCorruptionError(
                    f"{self.revisions_path} revision {revision.revision_id} has "
                    f"invalid contract references: {exc}"
                ) from exc
        return revisions

    def _load_heads(
        self,
        revisions: Mapping[str, ArtifactRevision],
    ) -> Dict[Tuple[int, str], ArtifactHead]:
        if not self.heads_path.exists():
            return {}
        try:
            payload = json.loads(self.heads_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ArtifactCorruptionError(
                f"cannot read {self.heads_path}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ArtifactCorruptionError(f"{self.heads_path} must contain an object")
        if set(payload) != {"schema_version", "heads"}:
            raise ArtifactCorruptionError(
                f"{self.heads_path} must contain schema_version and heads"
            )
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ArtifactCorruptionError(
                f"{self.heads_path} schema_version must be the integer 1"
            )
        if not isinstance(payload["heads"], list):
            raise ArtifactCorruptionError(f"{self.heads_path} heads must be a list")

        heads: Dict[Tuple[int, str], ArtifactHead] = {}
        for index, record in enumerate(payload["heads"]):
            try:
                head = _head_from_record(record)
            except (KeyError, TypeError, ValueError) as exc:
                raise ArtifactCorruptionError(
                    f"{self.heads_path} head {index} is invalid: {exc}"
                ) from exc
            key = (head.chapter, head.kind)
            if key in heads:
                raise ArtifactCorruptionError(
                    f"{self.heads_path} has duplicate head for chapter "
                    f"{head.chapter} kind {head.kind!r}"
                )
            revision = revisions.get(head.revision_id)
            if revision is None:
                raise ArtifactCorruptionError(
                    f"{self.heads_path} references unknown revision {head.revision_id}"
                )
            if revision.chapter != head.chapter or revision.kind != head.kind:
                raise ArtifactCorruptionError(
                    f"{self.heads_path} head target does not match revision "
                    f"{head.revision_id}"
                )
            heads[key] = head
        return heads

    def _write_heads(
        self,
        heads: Mapping[Tuple[int, str], ArtifactHead],
        *,
        revision_id: str,
    ) -> None:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "heads": [
                head.to_dict()
                for _, head in sorted(
                    heads.items(),
                    key=lambda item: (item[0][0], item[0][1]),
                )
            ],
        }
        data = _canonical_json(payload) + b"\n"
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.heads_path.name}.tmp-",
            dir=str(self.artifact_root),
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.heads_path)
            temp_name = ""
            try:
                _fsync_directory(self.artifact_root)
            except OSError as exc:
                raise ArtifactCommitUncertain(
                    operation="head_update",
                    revision_id=revision_id,
                ) from exc
        except OSError as exc:
            raise ArtifactCorruptionError(
                f"cannot atomically write {self.heads_path}: {exc}"
            ) from exc
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def _utc_timestamp(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime):
            raise TypeError("clock must return a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _project_process_lock(artifact_root: Path) -> threading.RLock:
    key = str(artifact_root.resolve())
    with _PROJECT_LOCKS_GUARD:
        lock = _PROJECT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROJECT_LOCKS[key] = lock
        return lock


def _derive_revision_id(metadata: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(metadata))).hexdigest()


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a nonblank string")
    return value.strip()


def _optional_string(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    return _required_string(value, field_name)


def _validate_sha(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


def _validate_optional_sha(value: Any, field_name: str) -> None:
    if value is not None:
        _validate_sha(value, field_name)


def _validate_contract_references(
    revisions: Mapping[str, ArtifactRevision],
    *,
    chapter: int,
    story_contract_revision_id: Optional[str],
    chapter_contract_revision_id: Optional[str],
) -> None:
    _validate_contract_reference(
        revisions,
        revision_id=story_contract_revision_id,
        field_name="story_contract_revision_id",
        expected_kind="story_contract",
        expected_chapter=0,
    )
    _validate_contract_reference(
        revisions,
        revision_id=chapter_contract_revision_id,
        field_name="chapter_contract_revision_id",
        expected_kind="chapter_contract",
        expected_chapter=chapter,
    )


def _validate_parent_reference(
    revisions: Mapping[str, ArtifactRevision],
    *,
    parent_revision_id: Optional[str],
    child_chapter: int,
) -> None:
    if parent_revision_id is None:
        return
    try:
        parent = revisions[parent_revision_id]
    except KeyError:
        raise KeyError(
            f"unknown parent artifact revision: {parent_revision_id}"
        ) from None
    if parent.chapter != child_chapter:
        raise ValueError(
            f"parent revision {parent_revision_id} belongs to chapter "
            f"{parent.chapter}, not chapter {child_chapter}"
        )


def _validate_contract_reference(
    revisions: Mapping[str, ArtifactRevision],
    *,
    revision_id: Optional[str],
    field_name: str,
    expected_kind: str,
    expected_chapter: int,
) -> None:
    if revision_id is None:
        return
    revision = revisions.get(revision_id)
    if revision is None:
        raise KeyError(f"unknown {field_name}: {revision_id}")
    if revision.kind != expected_kind:
        raise ValueError(
            f"{field_name} must reference a {expected_kind} artifact, "
            f"not {revision.kind!r}"
        )
    if revision.chapter != expected_chapter:
        raise ValueError(
            f"{field_name} must reference chapter {expected_chapter}, "
            f"not chapter {revision.chapter}"
        )


def _validate_chapter_kind(chapter: Any, kind: Any) -> None:
    if type(chapter) is not int or chapter < 0:
        raise ValueError("chapter must be a non-negative integer")
    _required_string(kind, "kind")
    if kind == "story_contract":
        if chapter != 0:
            raise ValueError("story_contract artifacts must use chapter 0")
    elif kind == "chapter_contract":
        if chapter < 1:
            raise ValueError("chapter_contract artifacts must use a positive chapter")
    elif chapter < 1:
        raise ValueError(f"{kind} artifacts must use a positive chapter")


def _validate_utc_timestamp(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a UTC timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field_name} must be a UTC timestamp")
    return value


def _revision_from_record(record: Any) -> ArtifactRevision:
    if not isinstance(record, dict):
        raise ValueError("revision record must be an object")
    if set(record) != _REVISION_FIELDS:
        missing = sorted(_REVISION_FIELDS - set(record))
        extra = sorted(set(record) - _REVISION_FIELDS)
        raise ValueError(f"revision fields mismatch; missing={missing}, extra={extra}")
    _validate_sha(record["revision_id"], "revision_id")
    _validate_chapter_kind(record["chapter"], record["kind"])
    _validate_sha(record["sha256"], "sha256")
    if type(record["byte_length"]) is not int or record["byte_length"] < 0:
        raise ValueError("byte_length must be a non-negative integer")
    _validate_optional_sha(record["parent_revision_id"], "parent_revision_id")
    _required_string(record["source"], "source")
    _optional_string(record["provider"], "provider")
    _optional_string(record["model"], "model")
    _validate_optional_sha(record["prompt_sha256"], "prompt_sha256")
    _validate_optional_sha(
        record["story_contract_revision_id"],
        "story_contract_revision_id",
    )
    _validate_optional_sha(
        record["chapter_contract_revision_id"],
        "chapter_contract_revision_id",
    )
    _validate_utc_timestamp(record["timestamp"], "timestamp")
    return ArtifactRevision(**record)


def _head_from_record(record: Any) -> ArtifactHead:
    if not isinstance(record, dict):
        raise ValueError("head record must be an object")
    if set(record) != _HEAD_FIELDS:
        missing = sorted(_HEAD_FIELDS - set(record))
        extra = sorted(set(record) - _HEAD_FIELDS)
        raise ValueError(f"head fields mismatch; missing={missing}, extra={extra}")
    _validate_chapter_kind(record["chapter"], record["kind"])
    _validate_sha(record["revision_id"], "revision_id")
    _validate_utc_timestamp(record["updated_at"], "updated_at")
    return ArtifactHead(**record)


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


__all__ = [
    "ArtifactCommitUncertain",
    "ArtifactCorruptionError",
    "ArtifactError",
    "ArtifactHead",
    "ArtifactIntegrityError",
    "ArtifactRevision",
    "ArtifactStore",
    "DuplicateArtifactRevision",
    "StaleArtifactHead",
]
