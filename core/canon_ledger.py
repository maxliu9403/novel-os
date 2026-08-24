"""Canonical StoryState hashing and an append-only canon commit ledger."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional


SCHEMA_VERSION = 1
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ENTRY_RE = re.compile(r"^canon-entry-[0-9a-f]{64}$")
_PROPOSAL_RE = re.compile(r"^proposal-[0-9a-f]{64}$")
_REQUEST_RE = re.compile(r"^promotion-request-[0-9a-f]{64}$")
_RECEIPT_RE = re.compile(r"^promotion-receipt-[0-9a-f]{64}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SEMANTIC_FIELDS = (
    "metadata",
    "story_bible",
    "characters",
    "codex",
    "relationships",
    "collections",
    "continuity_exemptions",
    "plot_threads",
    "chapters",
    "timeline",
    "style_profile",
)
_CHAPTER_OPERATIONAL = {
    "canonical_revision_id",
    "continuity_checks",
    "contract_id",
    "last_evaluation_id",
    "last_modified",
    "quality_scores",
    "status",
    "target_word_count",
    "word_count",
}
_METADATA_OPERATIONAL = {
    "created",
    "created_at",
    "file_path",
    "last_saved",
    "output_path",
    "project_path",
    "timestamp",
    "updated",
    "updated_at",
}


class CanonCorruptionError(Exception):
    """Raised when the canon ledger is malformed, truncated, or tampered."""


class CanonCommitUncertain(Exception):
    """Raised when an appended entry may not be directory-durable."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        super().__init__(f"canon entry {entry_id} may already be committed")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("canon must contain only finite JSON-compatible values") from exc


def _normalize(value: Any, path: str) -> Any:
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON values")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            if type(raw_key) is str:
                key = raw_key
            elif type(raw_key) is int:
                key = str(raw_key)
            else:
                raise ValueError(f"{path} has a non-JSON object key")
            if key in result:
                raise ValueError(f"{path} has colliding normalized JSON keys")
            result[key] = _normalize(item, f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [_normalize(item, f"{path}[]") for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _normalize(to_dict(), path)
    raise ValueError(f"{path} must contain only JSON-compatible values")


def _semantic_value(state: Any, name: str) -> Any:
    if isinstance(state, Mapping):
        value = state.get(name, [] if name == "timeline" else {})
    else:
        if not hasattr(state, name):
            raise TypeError(f"state is missing semantic field {name!r}")
        value = getattr(state, name)
    value = _normalize(value, name)
    if name == "metadata":
        for field in tuple(value):
            if field in _METADATA_OPERATIONAL or field.endswith("_path"):
                value.pop(field, None)
    elif name in {"characters", "codex"}:
        for record in value.values():
            if isinstance(record, dict):
                record.pop("portrait_media_id", None)
    elif name == "chapters":
        for record in value.values():
            if not isinstance(record, dict):
                continue
            for field in _CHAPTER_OPERATIONAL:
                record.pop(field, None)
    elif name == "continuity_exemptions":
        for record in value.values():
            if isinstance(record, dict):
                record.pop("at", None)
    return value


def canonical_canon_bytes(state: Any) -> bytes:
    """Serialize semantic canon as compact, sorted, schema-versioned UTF-8 JSON."""
    payload = {"schema_version": SCHEMA_VERSION}
    payload.update({name: _semantic_value(state, name) for name in _SEMANTIC_FIELDS})
    return _json_bytes(payload)


def canonical_canon_sha(state: Any) -> str:
    return hashlib.sha256(canonical_canon_bytes(state)).hexdigest()


def _valid_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise ValueError(f"{field} must be lowercase 64 hex")
    return value


def _required(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonblank string")
    return value.strip()


def _utc_timestamp(value: Any, field: str) -> str:
    value = _required(value, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a UTC ISO-8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be a UTC ISO-8601 string")
    return value


_ENTRY_FIELDS = {
    "entry_id",
    "previous_entry_id",
    "proposal_id",
    "source_artifact_sha",
    "base_canon_sha",
    "new_canon_sha",
    "chapter",
    "receipt_id",
    "request_id",
    "idempotency_key",
    "committed_at",
    "schema_version",
}


@dataclass(frozen=True)
class CanonLedgerEntry:
    previous_entry_id: Optional[str]
    proposal_id: str
    source_artifact_sha: str
    base_canon_sha: str
    new_canon_sha: str
    chapter: int
    receipt_id: str
    request_id: str
    idempotency_key: str
    committed_at: str
    schema_version: int = SCHEMA_VERSION
    entry_id: str = ""

    def _identity(self) -> dict[str, Any]:
        return {
            "previous_entry_id": self.previous_entry_id,
            "proposal_id": self.proposal_id,
            "source_artifact_sha": self.source_artifact_sha,
            "base_canon_sha": self.base_canon_sha,
            "new_canon_sha": self.new_canon_sha,
            "chapter": self.chapter,
            "receipt_id": self.receipt_id,
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "committed_at": self.committed_at,
            "schema_version": self.schema_version,
        }

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("schema_version must be the integer 1")
        if self.previous_entry_id is not None and (
            not isinstance(self.previous_entry_id, str)
            or not _ENTRY_RE.fullmatch(self.previous_entry_id)
        ):
            raise ValueError("previous_entry_id has invalid format")
        for name in ("source_artifact_sha", "base_canon_sha", "new_canon_sha"):
            _valid_sha(getattr(self, name), name)
        if type(self.chapter) is not int or self.chapter < 1:
            raise ValueError("chapter must be a positive integer")
        formats = {
            "proposal_id": _PROPOSAL_RE,
            "receipt_id": _RECEIPT_RE,
            "request_id": _REQUEST_RE,
            "idempotency_key": _IDEMPOTENCY_RE,
        }
        for name, pattern in formats.items():
            value = _required(getattr(self, name), name)
            if not pattern.fullmatch(value):
                raise ValueError(f"{name} has invalid format")
            object.__setattr__(self, name, value)
        object.__setattr__(
            self, "committed_at", _utc_timestamp(self.committed_at, "committed_at")
        )
        expected = "canon-entry-" + hashlib.sha256(_json_bytes(self._identity())).hexdigest()
        if self.entry_id and (
            not isinstance(self.entry_id, str)
            or not _ENTRY_RE.fullmatch(self.entry_id)
            or self.entry_id != expected
        ):
            raise ValueError("entry_id does not match canon entry identity")
        object.__setattr__(self, "entry_id", expected)

    def to_dict(self) -> dict[str, Any]:
        return {"entry_id": self.entry_id, **self._identity()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CanonLedgerEntry":
        if not isinstance(data, Mapping) or set(data) != _ENTRY_FIELDS:
            raise ValueError("canon entry has invalid fields")
        return cls(**dict(data))


class CanonLedger:
    """Append-only ledger; callers serialize writers with the project lock."""

    def __init__(self, project_root: os.PathLike[str] | str) -> None:
        self.project_root = Path(os.path.abspath(os.fspath(project_root)))
        self.state_dir = self.project_root / "outputs" / "state"
        self.path = self.state_dir / "canon_ledger.jsonl"

    def _safe_layout(self) -> None:
        current = self.project_root
        for component in (None, "outputs", "state"):
            if component:
                current /= component
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise CanonCorruptionError(f"unsafe canon ledger parent: {current}")
        try:
            mode = self.path.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise CanonCorruptionError("canon ledger must be a regular non-symlink file")

    def history(self) -> tuple[CanonLedgerEntry, ...]:
        self._safe_layout()
        if not self.path.exists():
            return ()
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise CanonCorruptionError(f"cannot read canon ledger: {exc}") from exc
        if raw and not raw.endswith(b"\n"):
            raise CanonCorruptionError("canon ledger has a truncated final record")
        entries: list[CanonLedgerEntry] = []
        seen_ids: set[str] = set()
        seen_keys: set[str] = set()
        for index, line in enumerate(raw.splitlines(), 1):
            try:
                record = json.loads(line, object_pairs_hook=_strict_object)
                if not isinstance(record, dict) or set(record) != {
                    "entry",
                    "record_sha256",
                }:
                    raise ValueError("record has invalid fields")
                if record["record_sha256"] != hashlib.sha256(
                    _json_bytes(record["entry"])
                ).hexdigest():
                    raise ValueError("record hash mismatch")
                entry = CanonLedgerEntry.from_dict(record["entry"])
            except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise CanonCorruptionError(
                    f"canon ledger line {index} is invalid: {exc}"
                ) from exc
            previous = entries[-1] if entries else None
            if entry.previous_entry_id != (previous.entry_id if previous else None):
                raise CanonCorruptionError("canon ledger entry chain is broken")
            if previous and entry.base_canon_sha != previous.new_canon_sha:
                raise CanonCorruptionError("canon ledger hash chain is broken")
            if entry.entry_id in seen_ids or entry.idempotency_key in seen_keys:
                raise CanonCorruptionError("canon ledger contains a duplicate commit")
            seen_ids.add(entry.entry_id)
            seen_keys.add(entry.idempotency_key)
            entries.append(entry)
        return tuple(entries)

    def current(self) -> Optional[CanonLedgerEntry]:
        history = self.history()
        return history[-1] if history else None

    def append(self, entry: CanonLedgerEntry) -> CanonLedgerEntry:
        if not isinstance(entry, CanonLedgerEntry):
            raise TypeError("entry must be a CanonLedgerEntry")
        history = self.history()
        for existing in history:
            if existing.idempotency_key == entry.idempotency_key:
                if existing == entry:
                    return existing
                raise CanonCorruptionError("idempotency key has a different canon entry")
        previous = history[-1] if history else None
        if entry.previous_entry_id != (previous.entry_id if previous else None):
            raise CanonCorruptionError("new canon entry does not extend the ledger")
        if previous and entry.base_canon_sha != previous.new_canon_sha:
            raise CanonCorruptionError("new canon entry has a stale base hash")
        self._safe_layout()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = entry.to_dict()
        line = _json_bytes(
            {
                "entry": data,
                "record_sha256": hashlib.sha256(_json_bytes(data)).hexdigest(),
            }
        ) + b"\n"
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o644)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CanonCorruptionError("canon ledger is not a regular file")
            view = memoryview(line)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            directory_fd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise CanonCommitUncertain(entry.entry_id) from exc
        return entry


__all__ = [
    "CanonCommitUncertain",
    "CanonCorruptionError",
    "CanonLedger",
    "CanonLedgerEntry",
    "canonical_canon_bytes",
    "canonical_canon_sha",
]
