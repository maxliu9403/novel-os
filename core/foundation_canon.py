"""Durable authority for establishing Architect foundation canon."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from canon_ledger import CanonLedger, CanonReconciliationEntry, canonical_canon_sha
from project_identity import ensure_project_instance_id_unlocked
from project_lock import ProjectLock
from state_codec import state_payload
from state_manager import StoryState
from story_foundation import apply_story_foundation


_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ENTRY_RE = re.compile(r"^canon-entry-[0-9a-f]{64}$")


class FoundationCanonError(Exception):
    """Base error for foundation canon transactions."""


class FoundationIdempotencyConflict(FoundationCanonError):
    """An idempotency key is already bound to another foundation."""


class FoundationReconciliationRequired(FoundationCanonError):
    """Existing canon history requires an explicit reconciliation transaction."""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return f"{prefix}-{hashlib.sha256(_json_bytes(value)).hexdigest()}"


def _record(kind: str, payload: Mapping[str, Any]) -> bytes:
    value = dict(payload)
    return _json_bytes(
        {
            kind: value,
            "record_sha256": hashlib.sha256(_json_bytes(value)).hexdigest(),
        }
    ) + b"\n"


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


@dataclass(frozen=True)
class FoundationCanonReceipt:
    project_instance_id: str
    idempotency_key: str
    foundation_sha256: str
    target_words: int
    old_canon_sha: str
    new_canon_sha: str
    request_id: str
    committed_at: str
    schema_version: int = 1
    receipt_id: str = ""
    reconciliation_entry_id: str = ""

    def _identity(self) -> dict[str, Any]:
        identity = {
            key: value for key, value in asdict(self).items() if key != "receipt_id"
        }
        if self.schema_version == 1:
            identity.pop("reconciliation_entry_id")
        return identity

    def __post_init__(self) -> None:
        if self.schema_version not in {1, 2}:
            raise ValueError("foundation receipt schema_version must be 1 or 2")
        if not _KEY_RE.fullmatch(self.idempotency_key):
            raise ValueError("idempotency_key contains unsafe characters")
        for name in ("foundation_sha256", "old_canon_sha", "new_canon_sha"):
            if not _SHA_RE.fullmatch(str(getattr(self, name))):
                raise ValueError(f"{name} must be lowercase 64 hex")
        if self.target_words < 1:
            raise ValueError("target_words must be positive")
        if self.schema_version == 1 and self.reconciliation_entry_id:
            raise ValueError("schema 1 foundation receipt cannot bind reconciliation")
        if self.schema_version == 2 and not _ENTRY_RE.fullmatch(
            self.reconciliation_entry_id
        ):
            raise ValueError("reconciliation_entry_id has invalid format")
        expected = _content_id("foundation-receipt", self._identity())
        if self.receipt_id and self.receipt_id != expected:
            raise ValueError("receipt_id does not match foundation receipt")
        object.__setattr__(self, "receipt_id", expected)

    def to_dict(self) -> dict[str, Any]:
        return {"receipt_id": self.receipt_id, **self._identity()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FoundationCanonReceipt":
        return cls(**dict(value))


class FoundationCanonService:
    """Commit foundation canon before the first chapter promotion."""

    def __init__(
        self,
        project_root: Path | str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.project_root = Path(os.path.abspath(os.fspath(project_root)))
        self.state_dir = self.project_root / "outputs/state"
        self.state_path = self.state_dir / "story_state.json"
        self.journal_dir = self.state_dir / "foundation_journal"
        self.receipt_dir = self.state_dir / "foundation_receipts"
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _safe_key(value: str) -> str:
        if not isinstance(value, str) or not _KEY_RE.fullmatch(value):
            raise ValueError("idempotency_key contains unsafe characters")
        return value

    def _atomic_write(self, path: Path, data: bytes) -> None:
        self._ensure_storage_directory(path.parent)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise FoundationCanonError(
                    f"foundation target must be a regular non-symlink file: {path}"
                )
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = ""
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def _ensure_storage_directory(self, path: Path) -> None:
        try:
            components = path.relative_to(self.project_root).parts
        except ValueError as exc:
            raise FoundationCanonError(
                f"foundation storage escapes project root: {path}"
            ) from exc
        current = self.project_root
        for component in components:
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                current.mkdir(parents=True, exist_ok=True)
                mode = current.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise FoundationCanonError(
                    f"foundation storage parent must be a real directory: {current}"
                )
            current /= component
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            path.mkdir(exist_ok=True)
            mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise FoundationCanonError(
                f"foundation storage path must be a real directory: {path}"
            )

    def _preflight_storage(self) -> None:
        self._ensure_storage_directory(self.journal_dir)
        self._ensure_storage_directory(self.receipt_dir)
        for directory in (self.journal_dir, self.receipt_dir):
            for path in directory.iterdir():
                try:
                    mode = path.lstat().st_mode
                except FileNotFoundError as exc:
                    raise FoundationCanonError(
                        f"foundation transaction entry disappeared: {path.name}"
                    ) from exc
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    raise FoundationCanonError(
                        "foundation transaction entry must be a regular "
                        f"non-symlink file: {path.name}"
                    )
                if path.suffix != ".json" or not _KEY_RE.fullmatch(path.stem):
                    raise FoundationCanonError(
                        f"unknown foundation transaction entry: {path.name}"
                    )
        try:
            mode = self.state_path.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise FoundationCanonError(
                "canonical story state must be a regular non-symlink file"
            )

    def _read_record(self, path: Path, kind: str) -> dict[str, Any] | None:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise FoundationCanonError(
                f"{kind} record must be a regular non-symlink file"
            )
        try:
            value = json.loads(path.read_bytes(), object_pairs_hook=_strict_object)
            if not isinstance(value, dict) or set(value) != {kind, "record_sha256"}:
                raise ValueError("record has invalid fields")
            payload = value[kind]
            if not isinstance(payload, dict):
                raise ValueError("record payload must be an object")
            expected = hashlib.sha256(_json_bytes(payload)).hexdigest()
            if value["record_sha256"] != expected:
                raise ValueError("record hash mismatch")
            return payload
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise FoundationCanonError(f"invalid {kind} record: {exc}") from exc

    def _write_journal(self, value: Mapping[str, Any], state: str) -> dict[str, Any]:
        payload = {**dict(value), "state": state}
        key = payload["request"]["idempotency_key"]
        self._atomic_write(
            self.journal_dir / f"{key}.json", _record("journal", payload)
        )
        return payload

    def _load_receipt(self, key: str) -> FoundationCanonReceipt | None:
        payload = self._read_record(self.receipt_dir / f"{key}.json", "receipt")
        return FoundationCanonReceipt.from_dict(payload) if payload is not None else None

    def _receipt_tail(self) -> FoundationCanonReceipt | None:
        if not self.receipt_dir.is_dir():
            return None
        receipts = [
            receipt
            for path in self.receipt_dir.glob("*.json")
            if (receipt := self._load_receipt(path.stem)) is not None
        ]
        receipts.sort(key=lambda item: (item.committed_at, item.receipt_id))
        for previous, current in zip(receipts, receipts[1:]):
            if current.old_canon_sha != previous.new_canon_sha:
                raise FoundationCanonError("foundation receipt chain is broken")
        return receipts[-1] if receipts else None

    def _bind_reconciliation_alias(
        self,
        *,
        key: str,
        foundation_sha: str,
        target_words: int,
        history: tuple[Any, ...],
    ) -> FoundationCanonReceipt | None:
        entry_positions = {
            entry.entry_id: index
            for index, entry in enumerate(history)
            if isinstance(entry, CanonReconciliationEntry)
        }
        if not entry_positions or not self.receipt_dir.is_dir():
            return None
        candidates: list[FoundationCanonReceipt] = []
        for path in self.receipt_dir.glob("*.json"):
            receipt = self._load_receipt(path.stem)
            if (
                receipt is not None
                and receipt.schema_version == 2
                and receipt.foundation_sha256 == foundation_sha
                and receipt.target_words == target_words
                and receipt.reconciliation_entry_id in entry_positions
            ):
                candidates.append(receipt)
        if not candidates:
            return None
        source = max(
            candidates,
            key=lambda item: entry_positions[item.reconciliation_entry_id],
        )
        alias = FoundationCanonReceipt(
            project_instance_id=source.project_instance_id,
            idempotency_key=key,
            foundation_sha256=source.foundation_sha256,
            target_words=source.target_words,
            old_canon_sha=source.old_canon_sha,
            new_canon_sha=source.new_canon_sha,
            request_id=source.request_id,
            committed_at=source.committed_at,
            schema_version=2,
            reconciliation_entry_id=source.reconciliation_entry_id,
        )
        journal = {
            "schema_version": 2,
            "request": {
                "idempotency_key": key,
                "foundation_sha256": foundation_sha,
                "target_words": target_words,
                "alias_of_receipt_id": source.receipt_id,
                "reconciliation_entry_id": source.reconciliation_entry_id,
            },
            "receipt": alias.to_dict(),
        }
        journal = self._write_journal(journal, "prepared")
        self._atomic_write(
            self.receipt_dir / f"{key}.json",
            _record("receipt", alias.to_dict()),
        )
        self._write_journal(journal, "committed")
        return alias

    def _restore_reconciliation_projection_if_needed(
        self,
        receipt: FoundationCanonReceipt,
        entry: CanonReconciliationEntry,
        history: tuple[Any, ...],
    ) -> None:
        current_sha = canonical_canon_sha(StoryState(str(self.project_root)))
        if current_sha == entry.new_canon_sha:
            return
        historical_hashes = {
            value
            for item in history
            for value in (item.base_canon_sha, item.new_canon_sha)
        }
        if current_sha not in historical_hashes:
            raise FoundationCanonError(
                "canonical state is not a recognized historical projection"
            )

        directory = self.state_dir / "foundation_reconciliation_journal"
        try:
            mode = directory.lstat().st_mode
        except FileNotFoundError as exc:
            raise FoundationCanonError(
                "reconciliation receipt has no transaction journal directory"
            ) from exc
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise FoundationCanonError(
                "reconciliation journal storage must be a real directory"
            )
        matches: list[dict[str, Any]] = []
        for path in directory.iterdir():
            if path.suffix != ".json" or not _KEY_RE.fullmatch(path.stem):
                raise FoundationCanonError(
                    f"unknown reconciliation journal entry: {path.name}"
                )
            journal = self._read_record(path, "reconciliation_journal")
            if (
                journal is not None
                and journal.get("ledger_entry", {}).get("entry_id")
                == entry.entry_id
            ):
                matches.append(journal)
        if len(matches) != 1:
            raise FoundationCanonError(
                "reconciliation receipt does not identify exactly one journal"
            )
        journal = matches[0]
        if (
            journal.get("state") != "committed"
            or journal.get("receipt", {}).get("reconciliation_entry_id")
            != receipt.reconciliation_entry_id
            or canonical_canon_sha(journal.get("state_payload", {}))
            != entry.new_canon_sha
        ):
            raise FoundationCanonError(
                "reconciliation journal does not match its committed canon entry"
            )
        self._atomic_write(
            self.state_path,
            _json_bytes(journal["state_payload"]) + b"\n",
        )

    def _restore_receipt_tail_if_needed(
        self, receipt: FoundationCanonReceipt
    ) -> None:
        current_sha = canonical_canon_sha(StoryState(str(self.project_root)))
        if current_sha == receipt.new_canon_sha:
            return
        journal = self._read_record(
            self.journal_dir / f"{receipt.idempotency_key}.json", "journal"
        )
        if (
            current_sha != receipt.old_canon_sha
            or journal is None
            or journal.get("state") != "committed"
            or journal.get("receipt", {}).get("receipt_id") != receipt.receipt_id
        ):
            raise FoundationCanonError(
                "canonical StoryState does not match foundation receipt tail"
            )
        self._atomic_write(
            self.state_path,
            _json_bytes(journal["state_payload"]) + b"\n",
        )

    def _resume(self, journal: dict[str, Any]) -> FoundationCanonReceipt:
        request = journal["request"]
        receipt = FoundationCanonReceipt.from_dict(journal["receipt"])
        current_sha = canonical_canon_sha(StoryState(str(self.project_root)))
        if journal["state"] == "prepared":
            if current_sha == receipt.old_canon_sha:
                self._atomic_write(
                    self.state_path,
                    _json_bytes(journal["state_payload"]) + b"\n",
                )
            elif current_sha != receipt.new_canon_sha:
                raise FoundationCanonError(
                    "foundation journal matches neither old nor new canon"
                )
            journal = self._write_journal(journal, "state_committed")
        if journal["state"] == "state_committed":
            self._atomic_write(
                self.receipt_dir / f"{request['idempotency_key']}.json",
                _record("receipt", receipt.to_dict()),
            )
            journal = self._write_journal(journal, "committed")
        if journal["state"] != "committed":
            raise FoundationCanonError("foundation journal did not commit")
        return receipt

    def initialize(
        self,
        foundation: Mapping[str, Any],
        *,
        target_words: int,
        idempotency_key: str,
    ) -> FoundationCanonReceipt:
        key = self._safe_key(idempotency_key)
        normalized = json.loads(_json_bytes(foundation))
        if not normalized.get("characters"):
            raise ValueError("story foundation must define at least one character")
        if not normalized.get("plot_threads"):
            raise ValueError("story foundation must define at least one plot thread")
        if not normalized.get("chapters"):
            raise ValueError("story foundation must define at least one chapter")
        foundation_sha = hashlib.sha256(_json_bytes(normalized)).hexdigest()
        if target_words < 1:
            raise ValueError("target_words must be positive")

        with ProjectLock(self.project_root):
            self._preflight_storage()
            project_instance_id = ensure_project_instance_id_unlocked(self.project_root)
            existing = self._load_receipt(key)
            if existing is not None:
                if (
                    existing.foundation_sha256 != foundation_sha
                    or existing.target_words != target_words
                ):
                    raise FoundationIdempotencyConflict(
                        "idempotency key is already bound to another foundation"
                    )
                history = CanonLedger(self.project_root).history()
                if existing.schema_version == 2:
                    matches = [
                        entry
                        for entry in history
                        if isinstance(entry, CanonReconciliationEntry)
                        and entry.entry_id == existing.reconciliation_entry_id
                    ]
                    if len(matches) != 1 or (
                        matches[0].foundation_sha256 != existing.foundation_sha256
                        or matches[0].base_canon_sha != existing.old_canon_sha
                        or matches[0].new_canon_sha != existing.new_canon_sha
                    ):
                        raise FoundationCanonError(
                            "foundation receipt does not match reconciliation history"
                        )
                    self._restore_reconciliation_projection_if_needed(
                        existing,
                        matches[0],
                        history,
                    )
                elif history:
                    if history[0].base_canon_sha != existing.new_canon_sha:
                        raise FoundationCanonError(
                            "chapter ledger is not based on the committed foundation"
                        )
                else:
                    self._restore_receipt_tail_if_needed(existing)
                return existing

            history = CanonLedger(self.project_root).history()
            if history:
                alias = self._bind_reconciliation_alias(
                    key=key,
                    foundation_sha=foundation_sha,
                    target_words=target_words,
                    history=history,
                )
                if alias is not None:
                    return alias
                raise FoundationReconciliationRequired(
                    "canon history exists without a foundation baseline receipt"
                )
            journal_path = self.journal_dir / f"{key}.json"
            journal = self._read_record(journal_path, "journal")
            if journal is not None:
                request = journal["request"]
                if (
                    request["foundation_sha256"] != foundation_sha
                    or request["target_words"] != target_words
                ):
                    raise FoundationIdempotencyConflict(
                        "idempotency key is already bound to another foundation"
                    )
                return self._resume(journal)

            state = StoryState(str(self.project_root))
            receipt_tail = self._receipt_tail()
            if receipt_tail is not None:
                self._restore_receipt_tail_if_needed(receipt_tail)
                state = StoryState(str(self.project_root))
            elif state.characters or state.plot_threads or state.chapters:
                raise FoundationReconciliationRequired(
                    "existing semantic canon requires foundation reconciliation"
                )
            old_canon_sha = canonical_canon_sha(state)
            committed_at = self._clock().astimezone(timezone.utc).isoformat()
            apply_story_foundation(state, normalized, target_words)
            new_canon_sha = canonical_canon_sha(state)
            request_identity = {
                "project_instance_id": project_instance_id,
                "idempotency_key": key,
                "foundation_sha256": foundation_sha,
                "target_words": target_words,
                "base_canon_sha": old_canon_sha,
                "schema_version": 1,
            }
            request = {
                **request_identity,
                "request_id": _content_id("foundation-request", request_identity),
            }
            receipt = FoundationCanonReceipt(
                project_instance_id=project_instance_id,
                idempotency_key=key,
                foundation_sha256=foundation_sha,
                target_words=target_words,
                old_canon_sha=old_canon_sha,
                new_canon_sha=new_canon_sha,
                request_id=request["request_id"],
                committed_at=committed_at,
            )
            journal = {
                "schema_version": 1,
                "state": "prepared",
                "request": request,
                "receipt": receipt.to_dict(),
                "state_payload": json.loads(
                    _json_bytes(state_payload(state, committed_at))
                ),
            }
            journal = self._write_journal(journal, "prepared")
            return self._resume(journal)


__all__ = [
    "FoundationCanonError",
    "FoundationCanonReceipt",
    "FoundationCanonService",
    "FoundationIdempotencyConflict",
    "FoundationReconciliationRequired",
]
