"""Canonical StoryState hashing and an append-only canon commit ledger."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from project_identity import ProjectIdentityError, load_project_instance_id_unlocked


CANON_SCHEMA_VERSION = 1
LEGACY_ENTRY_SCHEMA_VERSION = 1
ENTRY_SCHEMA_VERSION = 2
RECONCILIATION_SCHEMA_VERSION = 3
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ENTRY_RE = re.compile(r"^canon-entry-[0-9a-f]{64}$")
_PROPOSAL_RE = re.compile(r"^proposal-[0-9a-f]{64}$")
_REQUEST_RE = re.compile(r"^promotion-request-[0-9a-f]{64}$")
_RECEIPT_RE = re.compile(r"^promotion-receipt-[0-9a-f]{64}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_INSTANCE_RE = re.compile(r"^[0-9a-f]{32}$")
_RECONCILIATION_REQUEST_RE = re.compile(
    r"^foundation-reconciliation-request-[0-9a-f]{64}$"
)
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
    payload = {"schema_version": CANON_SCHEMA_VERSION}
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


_LEGACY_ENTRY_FIELDS = {
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
_ENTRY_FIELDS = _LEGACY_ENTRY_FIELDS | {"project_instance_id"}


@dataclass(frozen=True)
class CanonLedgerEntry:
    project_instance_id: str
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
    schema_version: int = ENTRY_SCHEMA_VERSION
    entry_id: str = ""

    def _identity(self) -> dict[str, Any]:
        identity = {
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
        if self.schema_version == ENTRY_SCHEMA_VERSION:
            identity = {"project_instance_id": self.project_instance_id, **identity}
        return identity

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {
            LEGACY_ENTRY_SCHEMA_VERSION,
            ENTRY_SCHEMA_VERSION,
        }:
            raise ValueError("schema_version must be the integer 1 or 2")
        if self.schema_version == LEGACY_ENTRY_SCHEMA_VERSION:
            if self.project_instance_id != "":
                raise ValueError("legacy canon entry must not have a project identity")
        elif not isinstance(self.project_instance_id, str) or not _INSTANCE_RE.fullmatch(
            self.project_instance_id
        ):
            raise ValueError("project_instance_id has invalid format")
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
        if not isinstance(data, Mapping):
            raise ValueError("canon entry has invalid fields")
        fields = set(data)
        schema_version = data.get("schema_version")
        if schema_version == LEGACY_ENTRY_SCHEMA_VERSION:
            if fields != _LEGACY_ENTRY_FIELDS:
                raise ValueError("canon entry has invalid fields")
            values = {"project_instance_id": "", **dict(data)}
        elif schema_version == ENTRY_SCHEMA_VERSION:
            if fields != _ENTRY_FIELDS:
                raise ValueError("canon entry has invalid fields")
            values = dict(data)
        else:
            raise ValueError("canon entry schema version is invalid")
        return cls(**values)


_RECONCILIATION_FIELDS = {
    "entry_id",
    "entry_kind",
    "project_instance_id",
    "previous_entry_id",
    "base_canon_sha",
    "new_canon_sha",
    "foundation_sha256",
    "artifact_heads_sha256",
    "replayed_entry_ids",
    "outcomes",
    "reason",
    "request_id",
    "idempotency_key",
    "committed_at",
    "schema_version",
}


@dataclass(frozen=True)
class CanonReconciliationEntry:
    """Auditable canon repair that does not impersonate a chapter promotion."""

    project_instance_id: str
    previous_entry_id: str
    base_canon_sha: str
    new_canon_sha: str
    foundation_sha256: str
    artifact_heads_sha256: str
    replayed_entry_ids: tuple[str, ...]
    outcomes: Mapping[str, Mapping[str, str]]
    reason: str
    request_id: str
    idempotency_key: str
    committed_at: str
    entry_kind: str = "foundation_reconciliation"
    schema_version: int = RECONCILIATION_SCHEMA_VERSION
    entry_id: str = ""

    def _identity(self) -> dict[str, Any]:
        return {
            "entry_kind": self.entry_kind,
            "project_instance_id": self.project_instance_id,
            "previous_entry_id": self.previous_entry_id,
            "base_canon_sha": self.base_canon_sha,
            "new_canon_sha": self.new_canon_sha,
            "foundation_sha256": self.foundation_sha256,
            "artifact_heads_sha256": self.artifact_heads_sha256,
            "replayed_entry_ids": list(self.replayed_entry_ids),
            "outcomes": _normalize(self.outcomes, "outcomes"),
            "reason": self.reason,
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "committed_at": self.committed_at,
            "schema_version": self.schema_version,
        }

    def __post_init__(self) -> None:
        if self.entry_kind != "foundation_reconciliation":
            raise ValueError("entry_kind must be foundation_reconciliation")
        if self.schema_version != RECONCILIATION_SCHEMA_VERSION:
            raise ValueError("reconciliation schema_version must be 3")
        if not _INSTANCE_RE.fullmatch(str(self.project_instance_id)):
            raise ValueError("project_instance_id has invalid format")
        if not _ENTRY_RE.fullmatch(str(self.previous_entry_id)):
            raise ValueError("previous_entry_id has invalid format")
        for name in (
            "base_canon_sha",
            "new_canon_sha",
            "foundation_sha256",
            "artifact_heads_sha256",
        ):
            _valid_sha(getattr(self, name), name)
        replayed = tuple(self.replayed_entry_ids)
        if not replayed or any(not _ENTRY_RE.fullmatch(str(value)) for value in replayed):
            raise ValueError("replayed_entry_ids must contain canon entry ids")
        if len(set(replayed)) != len(replayed):
            raise ValueError("replayed_entry_ids must not contain duplicates")
        object.__setattr__(self, "replayed_entry_ids", replayed)
        normalized_outcomes = _normalize(self.outcomes, "outcomes")
        if not isinstance(normalized_outcomes, dict):
            raise ValueError("outcomes must be an object")
        for character_id, outcome in normalized_outcomes.items():
            if not character_id.strip() or not isinstance(outcome, dict):
                raise ValueError("outcomes must map character ids to objects")
            if set(outcome) != {"outcome_state", "outcome_evidence"} or not all(
                isinstance(outcome[field], str) and outcome[field].strip()
                for field in ("outcome_state", "outcome_evidence")
            ):
                raise ValueError("each outcome requires state and evidence")
        object.__setattr__(self, "outcomes", normalized_outcomes)
        object.__setattr__(self, "reason", _required(self.reason, "reason"))
        if not _RECONCILIATION_REQUEST_RE.fullmatch(str(self.request_id)):
            raise ValueError("request_id has invalid format")
        if not _IDEMPOTENCY_RE.fullmatch(str(self.idempotency_key)):
            raise ValueError("idempotency_key has invalid format")
        object.__setattr__(
            self, "committed_at", _utc_timestamp(self.committed_at, "committed_at")
        )
        expected = "canon-entry-" + hashlib.sha256(
            _json_bytes(self._identity())
        ).hexdigest()
        if self.entry_id and self.entry_id != expected:
            raise ValueError("entry_id does not match reconciliation identity")
        object.__setattr__(self, "entry_id", expected)

    def to_dict(self) -> dict[str, Any]:
        return {"entry_id": self.entry_id, **self._identity()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CanonReconciliationEntry":
        if not isinstance(data, Mapping) or set(data) != _RECONCILIATION_FIELDS:
            raise ValueError("reconciliation entry has invalid fields")
        return cls(**dict(data))


CanonEntry = CanonLedgerEntry | CanonReconciliationEntry


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

    def _project_instance_id(self) -> str:
        try:
            return load_project_instance_id_unlocked(self.project_root)
        except ProjectIdentityError as exc:
            raise CanonCorruptionError(f"invalid project identity: {exc}") from exc

    def history(self) -> tuple[CanonEntry, ...]:
        self._safe_layout()
        if not self.path.exists():
            return ()
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise CanonCorruptionError(f"cannot read canon ledger: {exc}") from exc
        if raw and not raw.endswith(b"\n"):
            raise CanonCorruptionError("canon ledger has a truncated final record")
        entries: list[CanonEntry] = []
        seen_ids: set[str] = set()
        seen_keys: set[str] = set()
        project_instance_id: Optional[str] = None
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
                if record["entry"].get("schema_version") == RECONCILIATION_SCHEMA_VERSION:
                    entry = CanonReconciliationEntry.from_dict(record["entry"])
                else:
                    entry = CanonLedgerEntry.from_dict(record["entry"])
            except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise CanonCorruptionError(
                    f"canon ledger line {index} is invalid: {exc}"
                ) from exc
            if entry.schema_version in {
                ENTRY_SCHEMA_VERSION,
                RECONCILIATION_SCHEMA_VERSION,
            }:
                if project_instance_id is None:
                    project_instance_id = self._project_instance_id()
                if entry.project_instance_id != project_instance_id:
                    raise CanonCorruptionError(
                        "canon ledger entry belongs to another project instance"
                    )
            previous = entries[-1] if entries else None
            if entry.previous_entry_id != (previous.entry_id if previous else None):
                raise CanonCorruptionError("canon ledger entry chain is broken")
            if (
                previous is not None
                and previous.schema_version in {
                    ENTRY_SCHEMA_VERSION,
                    RECONCILIATION_SCHEMA_VERSION,
                }
                and entry.schema_version == LEGACY_ENTRY_SCHEMA_VERSION
            ):
                raise CanonCorruptionError(
                    "legacy canon entry cannot follow an identity-bound entry"
                )
            if previous and entry.base_canon_sha != previous.new_canon_sha:
                raise CanonCorruptionError("canon ledger hash chain is broken")
            if entry.entry_id in seen_ids or entry.idempotency_key in seen_keys:
                raise CanonCorruptionError("canon ledger contains a duplicate commit")
            seen_ids.add(entry.entry_id)
            seen_keys.add(entry.idempotency_key)
            entries.append(entry)
        return tuple(entries)

    def current(self) -> Optional[CanonEntry]:
        history = self.history()
        return history[-1] if history else None

    def append(
        self,
        entry: CanonEntry,
        *,
        _recovering_legacy_journal: bool = False,
    ) -> CanonEntry:
        if not isinstance(entry, (CanonLedgerEntry, CanonReconciliationEntry)):
            raise TypeError("entry must be a canon ledger entry")
        if (
            isinstance(entry, CanonLedgerEntry)
            and entry.schema_version == LEGACY_ENTRY_SCHEMA_VERSION
            and not _recovering_legacy_journal
        ):
            raise CanonCorruptionError(
                "new canon entries must be bound to the project identity"
            )
        if (
            entry.schema_version
            in {ENTRY_SCHEMA_VERSION, RECONCILIATION_SCHEMA_VERSION}
            and entry.project_instance_id != self._project_instance_id()
        ):
            raise CanonCorruptionError(
                "canon ledger entry belongs to another project instance"
            )
        history = self.history()
        for existing in history:
            if existing.idempotency_key == entry.idempotency_key:
                if existing == entry:
                    return existing
                raise CanonCorruptionError("idempotency key has a different canon entry")
        previous = history[-1] if history else None
        if (
            previous is not None
            and previous.schema_version
            in {ENTRY_SCHEMA_VERSION, RECONCILIATION_SCHEMA_VERSION}
            and isinstance(entry, CanonLedgerEntry)
            and entry.schema_version == LEGACY_ENTRY_SCHEMA_VERSION
        ):
            raise CanonCorruptionError(
                "legacy canon entry cannot follow an identity-bound entry"
            )
        if entry.previous_entry_id != (previous.entry_id if previous else None):
            raise CanonCorruptionError("new canon entry does not extend the ledger")
        if previous and entry.base_canon_sha != previous.new_canon_sha:
            raise CanonCorruptionError("new canon entry has a stale base hash")
        self._safe_layout()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        existing_bytes = self.path.read_bytes() if self.path.exists() else b""
        data = entry.to_dict()
        line = _json_bytes(
            {
                "entry": data,
                "record_sha256": hashlib.sha256(_json_bytes(data)).hexdigest(),
            }
        ) + b"\n"
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.tmp-", dir=self.state_dir
        )
        published = False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CanonCorruptionError("canon ledger is not a regular file")
            view = memoryview(existing_bytes + line)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("canon ledger temporary write made no progress")
                view = view[written:]
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.replace(temporary, self.path)
            published = True
            temporary = ""
            directory_fd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            if published:
                raise CanonCommitUncertain(entry.entry_id) from exc
            raise
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        return entry


__all__ = [
    "CanonCommitUncertain",
    "CanonCorruptionError",
    "CanonEntry",
    "CanonLedger",
    "CanonLedgerEntry",
    "CanonReconciliationEntry",
    "canonical_canon_bytes",
    "canonical_canon_sha",
]
