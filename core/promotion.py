"""Receipt-based atomic authority for advancing final artifacts and canon."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Optional

from artifacts import (
    ArtifactCommitUncertain,
    ArtifactStore,
    StaleArtifactHead,
)
from canon import apply_canon_proposal
from canon_ledger import (
    CanonCommitUncertain,
    CanonCorruptionError,
    CanonLedger,
    CanonLedgerEntry,
    canonical_canon_sha,
)
from project_lock import ProjectLock
from proposals import ProposalStore
from quality import EvaluationReport
from state_manager import StoryState


SCHEMA_VERSION = 1
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = _SHA_RE
_REQUEST_ID_RE = re.compile(r"^promotion-request-[0-9a-f]{64}$")
_RECEIPT_ID_RE = re.compile(r"^promotion-receipt-[0-9a-f]{64}$")
_PROPOSAL_ID_RE = re.compile(r"^proposal-[0-9a-f]{64}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_JOURNAL_STATES = {"prepared", "state_committed", "head_committed", "committed"}


class PromotionError(Exception):
    """Base promotion failure."""


class PromotionCorruptionError(PromotionError):
    """A journal or receipt failed strict identity/integrity checks."""


class PromotionCommitUncertain(PromotionError):
    """A published transaction file may not be directory-durable."""

    def __init__(self, operation: str, request_id: str) -> None:
        self.operation = operation
        self.request_id = request_id
        super().__init__(
            f"{operation} for {request_id} may be committed; retry the same request"
        )


class IdempotencyConflict(PromotionError):
    """An idempotency key was previously bound to a different request."""


class StaleCanonError(PromotionError):
    """The request's canon base no longer matches canonical StoryState."""


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
        raise ValueError("promotion data must contain only finite JSON values") from exc


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return f"{prefix}-{hashlib.sha256(_json_bytes(value)).hexdigest()}"


def _required(value: Any, name: str, *, allow_blank: bool = False) -> str:
    if not isinstance(value, str) or (not allow_blank and not value.strip()):
        qualifier = "a string" if allow_blank else "a nonblank string"
        raise ValueError(f"{name} must be {qualifier}")
    return value.strip()


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise ValueError(f"{name} must be lowercase 64 hex")
    return value


def _optional_revision(value: Any, name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _REVISION_RE.fullmatch(value):
        raise ValueError(f"{name} must be lowercase 64 hex or null")
    return value


def _revision(value: Any, name: str) -> str:
    value = _optional_revision(value, name)
    if value is None:
        raise ValueError(f"{name} must be lowercase 64 hex")
    return value


def _utc_timestamp(value: Any, name: str) -> str:
    value = _required(value, name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be a UTC ISO-8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be a UTC ISO-8601 string")
    return value


def _json_value(value: Any, name: str = "decision_metadata") -> Any:
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{name} must contain only finite JSON values")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{name} must use string keys")
            result[key] = _json_value(item, name)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item, name) for item in value]
    raise ValueError(f"{name} must contain only JSON-compatible values")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _request_identity(request: "PromotionRequest") -> dict[str, Any]:
    return {
        "project_id": request.project_id,
        "chapter": request.chapter,
        "candidate_revision_id": request.candidate_revision_id,
        "candidate_sha256": request.candidate_sha256,
        "evaluation_report": request.evaluation_report.to_dict(),
        "canon_proposal_id": request.canon_proposal_id,
        "expected_final_revision_id": request.expected_final_revision_id,
        "expected_final_sha256": request.expected_final_sha256,
        "base_canon_sha": request.base_canon_sha,
        "story_contract_revision_id": request.story_contract_revision_id,
        "chapter_contract_revision_id": request.chapter_contract_revision_id,
        "idempotency_key": request.idempotency_key,
        "actor": request.actor,
        "reason": request.reason,
        "decision_metadata": _thaw(request.decision_metadata),
        "schema_version": request.schema_version,
    }


@dataclass(frozen=True)
class PromotionRequest:
    project_id: str
    chapter: int
    candidate_revision_id: str
    candidate_sha256: str
    evaluation_report: EvaluationReport
    canon_proposal_id: str
    expected_final_revision_id: Optional[str]
    expected_final_sha256: Optional[str]
    base_canon_sha: str
    idempotency_key: str
    actor: str = "system"
    reason: str = ""
    decision_metadata: Mapping[str, Any] = field(default_factory=dict)
    story_contract_revision_id: Optional[str] = None
    chapter_contract_revision_id: Optional[str] = None
    schema_version: int = SCHEMA_VERSION
    request_id: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("schema_version must be the integer 1")
        object.__setattr__(self, "project_id", _required(self.project_id, "project_id"))
        if type(self.chapter) is not int or self.chapter < 1:
            raise ValueError("chapter must be a positive integer")
        _revision(self.candidate_revision_id, "candidate_revision_id")
        _sha(self.candidate_sha256, "candidate_sha256")
        if not isinstance(self.evaluation_report, EvaluationReport):
            raise TypeError("evaluation_report must be an EvaluationReport")
        # Roundtrip verifies the report's content-addressed identity before hashing it.
        object.__setattr__(
            self,
            "evaluation_report",
            EvaluationReport.from_dict(self.evaluation_report.to_dict()),
        )
        if not isinstance(self.canon_proposal_id, str) or not _PROPOSAL_ID_RE.fullmatch(
            self.canon_proposal_id
        ):
            raise ValueError("canon_proposal_id has invalid format")
        old_revision = _optional_revision(
            self.expected_final_revision_id, "expected_final_revision_id"
        )
        if self.expected_final_sha256 is not None:
            _sha(self.expected_final_sha256, "expected_final_sha256")
        if (old_revision is None) != (self.expected_final_sha256 is None):
            raise ValueError("expected final revision and sha must both be null or present")
        _sha(self.base_canon_sha, "base_canon_sha")
        for name in ("story_contract_revision_id", "chapter_contract_revision_id"):
            _optional_revision(getattr(self, name), name)
        key = _required(self.idempotency_key, "idempotency_key")
        if not _IDEMPOTENCY_RE.fullmatch(key):
            raise ValueError("idempotency_key contains unsafe characters")
        object.__setattr__(self, "idempotency_key", key)
        object.__setattr__(self, "actor", _required(self.actor, "actor"))
        object.__setattr__(self, "reason", _required(self.reason, "reason", allow_blank=True))
        if not isinstance(self.decision_metadata, Mapping):
            raise ValueError("decision_metadata must be an object")
        object.__setattr__(
            self, "decision_metadata", _freeze(_json_value(self.decision_metadata))
        )
        expected = _content_id("promotion-request", _request_identity(self))
        if self.request_id and (
            not isinstance(self.request_id, str)
            or not _REQUEST_ID_RE.fullmatch(self.request_id)
            or self.request_id != expected
        ):
            raise ValueError("request_id does not match promotion request identity")
        object.__setattr__(self, "request_id", expected)

    def to_dict(self) -> dict[str, Any]:
        return {"request_id": self.request_id, **_request_identity(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PromotionRequest":
        expected = {
            "request_id",
            "project_id",
            "chapter",
            "candidate_revision_id",
            "candidate_sha256",
            "evaluation_report",
            "canon_proposal_id",
            "expected_final_revision_id",
            "expected_final_sha256",
            "base_canon_sha",
            "story_contract_revision_id",
            "chapter_contract_revision_id",
            "idempotency_key",
            "actor",
            "reason",
            "decision_metadata",
            "schema_version",
        }
        if not isinstance(data, Mapping) or set(data) != expected:
            raise ValueError("promotion request has invalid fields")
        values = dict(data)
        values["evaluation_report"] = EvaluationReport.from_dict(
            values["evaluation_report"]
        )
        return cls(**values)


def _receipt_identity(receipt: "PromotionReceipt") -> dict[str, Any]:
    return {
        "project_id": receipt.project_id,
        "chapter": receipt.chapter,
        "request_id": receipt.request_id,
        "idempotency_key": receipt.idempotency_key,
        "old_artifact_revision_id": receipt.old_artifact_revision_id,
        "old_artifact_sha256": receipt.old_artifact_sha256,
        "new_artifact_revision_id": receipt.new_artifact_revision_id,
        "new_artifact_sha256": receipt.new_artifact_sha256,
        "old_canon_sha": receipt.old_canon_sha,
        "new_canon_sha": receipt.new_canon_sha,
        "canon_proposal_id": receipt.canon_proposal_id,
        "evaluation_report_id": receipt.evaluation_report_id,
        "actor": receipt.actor,
        "reason": receipt.reason,
        "decision_metadata": _thaw(receipt.decision_metadata),
        "journal_id": receipt.journal_id,
        "status": receipt.status,
        "committed_at": receipt.committed_at,
        "schema_version": receipt.schema_version,
    }


@dataclass(frozen=True)
class PromotionReceipt:
    project_id: str
    chapter: int
    request_id: str
    idempotency_key: str
    old_artifact_revision_id: Optional[str]
    old_artifact_sha256: Optional[str]
    new_artifact_revision_id: str
    new_artifact_sha256: str
    old_canon_sha: str
    new_canon_sha: str
    canon_proposal_id: str
    evaluation_report_id: str
    actor: str
    reason: str
    decision_metadata: Mapping[str, Any]
    journal_id: str
    committed_at: str
    status: str = "committed"
    schema_version: int = SCHEMA_VERSION
    receipt_id: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("schema_version must be the integer 1")
        object.__setattr__(self, "project_id", _required(self.project_id, "project_id"))
        if type(self.chapter) is not int or self.chapter < 1:
            raise ValueError("chapter must be a positive integer")
        if not isinstance(self.request_id, str) or not _REQUEST_ID_RE.fullmatch(
            self.request_id
        ):
            raise ValueError("request_id has invalid format")
        key = _required(self.idempotency_key, "idempotency_key")
        if not _IDEMPOTENCY_RE.fullmatch(key):
            raise ValueError("idempotency_key contains unsafe characters")
        _optional_revision(self.old_artifact_revision_id, "old_artifact_revision_id")
        if self.old_artifact_sha256 is not None:
            _sha(self.old_artifact_sha256, "old_artifact_sha256")
        if (self.old_artifact_revision_id is None) != (
            self.old_artifact_sha256 is None
        ):
            raise ValueError("old artifact revision and sha must both be null or present")
        _revision(self.new_artifact_revision_id, "new_artifact_revision_id")
        for name in ("new_artifact_sha256", "old_canon_sha", "new_canon_sha"):
            _sha(getattr(self, name), name)
        for name in ("actor", "journal_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if not _PROPOSAL_ID_RE.fullmatch(self.canon_proposal_id):
            raise ValueError("canon_proposal_id has invalid format")
        if not re.fullmatch(r"report-[0-9a-f]{64}", self.evaluation_report_id):
            raise ValueError("evaluation_report_id has invalid format")
        object.__setattr__(
            self, "committed_at", _utc_timestamp(self.committed_at, "committed_at")
        )
        object.__setattr__(self, "reason", _required(self.reason, "reason", allow_blank=True))
        if self.status != "committed":
            raise ValueError("receipt status must be committed")
        if not isinstance(self.decision_metadata, Mapping):
            raise ValueError("decision_metadata must be an object")
        object.__setattr__(
            self, "decision_metadata", _freeze(_json_value(self.decision_metadata))
        )
        expected = _content_id("promotion-receipt", _receipt_identity(self))
        if self.receipt_id and (
            not isinstance(self.receipt_id, str)
            or not _RECEIPT_ID_RE.fullmatch(self.receipt_id)
            or self.receipt_id != expected
        ):
            raise ValueError("receipt_id does not match promotion receipt identity")
        object.__setattr__(self, "receipt_id", expected)

    def to_dict(self) -> dict[str, Any]:
        return {"receipt_id": self.receipt_id, **_receipt_identity(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PromotionReceipt":
        expected = {"receipt_id", *list(_receipt_identity_fields())}
        if not isinstance(data, Mapping) or set(data) != expected:
            raise ValueError("promotion receipt has invalid fields")
        return cls(**dict(data))


def _receipt_identity_fields() -> tuple[str, ...]:
    return (
        "project_id",
        "chapter",
        "request_id",
        "idempotency_key",
        "old_artifact_revision_id",
        "old_artifact_sha256",
        "new_artifact_revision_id",
        "new_artifact_sha256",
        "old_canon_sha",
        "new_canon_sha",
        "canon_proposal_id",
        "evaluation_report_id",
        "actor",
        "reason",
        "decision_metadata",
        "journal_id",
        "status",
        "committed_at",
        "schema_version",
    )


def _state_payload(state: StoryState, saved_at: str) -> dict[str, Any]:
    state.sync_binder()
    return {
        "metadata": state.metadata,
        "story_bible": state.story_bible,
        "characters": {key: item.to_dict() for key, item in state.characters.items()},
        "codex": {key: item.to_dict() for key, item in state.codex.items()},
        "relationships": {
            key: item.to_dict() for key, item in state.relationships.items()
        },
        "collections": {key: item.to_dict() for key, item in state.collections.items()},
        "continuity_exemptions": dict(state.continuity_exemptions),
        "compile_styles": dict(state.compile_styles),
        "plot_threads": {
            key: item.to_dict() for key, item in state.plot_threads.items()
        },
        "chapters": {key: item.to_dict() for key, item in state.chapters.items()},
        "binder": state.binder.to_list(),
        "timeline": [item.to_dict() for item in state.timeline],
        "style_profile": state.style_profile.to_dict(),
        "session_log": list(state.session_log),
        "last_saved": saved_at,
    }


class PromotionService:
    """The sole process-serialized authority for committing artifact and canon."""

    def __init__(
        self,
        project_root: os.PathLike[str] | str,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        fault_injector: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.project_root = Path(os.path.abspath(os.fspath(project_root)))
        self.state_dir = self.project_root / "outputs" / "state"
        self.journal_dir = self.state_dir / "promotion_journal"
        self.receipt_dir = self.state_dir / "promotion_receipts"
        self.state_path = self.state_dir / "story_state.json"
        self.artifacts = ArtifactStore(self.project_root)
        self.proposals = ProposalStore(self.project_root)
        self.ledger = CanonLedger(self.project_root)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._fault_injector = fault_injector

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).isoformat()

    def _fault(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

    @staticmethod
    def _safe_key(key: str) -> str:
        if not isinstance(key, str) or not _IDEMPOTENCY_RE.fullmatch(key):
            raise ValueError("idempotency_key contains unsafe characters")
        return key

    def _ensure_storage_directory(self, path: Path) -> None:
        current = self.project_root
        for component in path.relative_to(self.project_root).parts:
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                current.mkdir(parents=True, exist_ok=True)
                mode = current.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise PromotionCorruptionError(
                    f"promotion storage parent must be a real directory: {current}"
                )
            current /= component
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            path.mkdir(exist_ok=True)
            mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise PromotionCorruptionError(
                f"promotion storage path must be a real directory: {path}"
            )

    def _atomic_write(self, path: Path, data: bytes, operation: str, request_id: str) -> None:
        self._ensure_storage_directory(path.parent)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise PromotionCorruptionError(
                    f"promotion target must be a regular non-symlink file: {path}"
                )
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
        published = False
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            published = True
            temporary = ""
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            if published:
                raise PromotionCommitUncertain(operation, request_id) from exc
            raise
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def _preflight_storage(self) -> None:
        # Validate every transaction-owned root before validation can lead to a
        # state/head commit. Creating empty managed directories is harmless and
        # lets the same no-follow checks cover missing and existing layouts.
        self._ensure_storage_directory(self.journal_dir)
        self._ensure_storage_directory(self.receipt_dir)
        try:
            mode = self.state_path.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise PromotionCorruptionError(
                "canonical story state must be a regular non-symlink file"
            )

    @staticmethod
    def _record(kind: str, payload: Mapping[str, Any]) -> bytes:
        payload_data = dict(payload)
        return _json_bytes(
            {
                kind: payload_data,
                "record_sha256": hashlib.sha256(_json_bytes(payload_data)).hexdigest(),
            }
        ) + b"\n"

    def _read_record(self, path: Path, kind: str) -> Optional[dict[str, Any]]:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise PromotionCorruptionError(
                f"{kind} record must be a regular non-symlink file"
            )
        try:
            record = json.loads(path.read_bytes(), object_pairs_hook=_strict_object)
            if not isinstance(record, dict) or set(record) != {kind, "record_sha256"}:
                raise ValueError("record has invalid fields")
            payload = record[kind]
            if not isinstance(payload, dict):
                raise ValueError("record payload must be an object")
            expected = hashlib.sha256(_json_bytes(payload)).hexdigest()
            if record["record_sha256"] != expected:
                raise ValueError("record hash mismatch")
            return payload
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise PromotionCorruptionError(f"invalid {kind} record: {exc}") from exc

    def _journal_path(self, key: str) -> Path:
        return self.journal_dir / f"{self._safe_key(key)}.json"

    def _receipt_path(self, key: str) -> Path:
        return self.receipt_dir / f"{self._safe_key(key)}.json"

    def _load_receipt_unlocked(self, key: str) -> Optional[PromotionReceipt]:
        payload = self._read_record(self._receipt_path(key), "receipt")
        if payload is None:
            return None
        try:
            receipt = PromotionReceipt.from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise PromotionCorruptionError(f"invalid receipt contract: {exc}") from exc
        if receipt.idempotency_key != key:
            raise PromotionCorruptionError("receipt idempotency key does not match path")
        return receipt

    def load_receipt(self, idempotency_key: str) -> Optional[PromotionReceipt]:
        key = self._safe_key(idempotency_key)
        with ProjectLock(self.project_root):
            self._preflight_storage()
            return self._load_receipt_unlocked(key)

    def _load_journal(self, key: str) -> Optional[dict[str, Any]]:
        payload = self._read_record(self._journal_path(key), "journal")
        if payload is None:
            return None
        expected = {
            "schema_version",
            "state",
            "request",
            "receipt",
            "ledger_entry",
            "state_payload",
        }
        if set(payload) != expected:
            raise PromotionCorruptionError("journal has invalid fields")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise PromotionCorruptionError("journal schema version is invalid")
        if payload["state"] not in _JOURNAL_STATES:
            raise PromotionCorruptionError("journal state is invalid")
        try:
            PromotionRequest.from_dict(payload["request"])
            PromotionReceipt.from_dict(payload["receipt"])
            CanonLedgerEntry.from_dict(payload["ledger_entry"])
            _json_bytes(payload["state_payload"])
        except (TypeError, ValueError) as exc:
            raise PromotionCorruptionError(f"journal contract is invalid: {exc}") from exc
        return payload

    def _write_journal(self, journal: dict[str, Any], state: str) -> dict[str, Any]:
        updated = {**journal, "state": state}
        request_id = updated["request"]["request_id"]
        self._atomic_write(
            self._journal_path(updated["request"]["idempotency_key"]),
            self._record("journal", updated),
            "journal_update",
            request_id,
        )
        return updated

    def _validate_contract_head(
        self, chapter: int, kind: str, expected_revision_id: Optional[str]
    ) -> None:
        if expected_revision_id is None:
            return
        head = self.artifacts.get_head(chapter, kind)
        current = head.revision_id if head else None
        if current != expected_revision_id:
            raise StaleArtifactHead(
                f"stale {kind} head: expected {expected_revision_id!r}, current {current!r}"
            )

    def _prepare(self, request: PromotionRequest) -> dict[str, Any]:
        if request.project_id != self.project_root.name:
            raise ValueError("promotion request project does not match service project")
        candidate = self.artifacts.get_revision(request.candidate_revision_id)
        if (
            candidate.chapter != request.chapter
            or candidate.kind != "final"
            or candidate.sha256 != request.candidate_sha256
        ):
            raise ValueError("candidate revision does not match promotion request")
        candidate_text = self.artifacts.read_text(candidate.revision_id)

        report = EvaluationReport.from_dict(request.evaluation_report.to_dict())
        evaluation = report.request
        if (
            report.status != "pass"
            or not report.hard_gates
            or not all(report.hard_gates.values())
        ):
            raise ValueError("quality report did not pass all hard gates")
        if (
            report.report_id != request.evaluation_report.report_id
            or report.artifact_sha256 != candidate.sha256
            or evaluation.artifact_sha256 != candidate.sha256
            or evaluation.artifact_revision_id != candidate.revision_id
            or evaluation.chapter != request.chapter
            or not evaluation.verify_text(candidate_text)
        ):
            raise ValueError("quality report is not bound to the exact candidate")

        proposal = self.proposals.load(
            request.canon_proposal_id,
            expected_source_artifact_sha=candidate.sha256,
        )
        if proposal.chapter != request.chapter:
            raise ValueError("canon proposal chapter does not match candidate")

        head = self.artifacts.get_head(request.chapter, "final")
        current_revision_id = head.revision_id if head else None
        if current_revision_id != request.expected_final_revision_id:
            raise StaleArtifactHead(
                f"stale final head: expected {request.expected_final_revision_id!r}, "
                f"current {current_revision_id!r}"
            )
        if head is not None:
            old_revision = self.artifacts.get_revision(head.revision_id)
            if old_revision.sha256 != request.expected_final_sha256:
                raise StaleArtifactHead("stale final head artifact sha")

        self._validate_contract_head(
            0, "story_contract", request.story_contract_revision_id
        )
        self._validate_contract_head(
            request.chapter,
            "chapter_contract",
            request.chapter_contract_revision_id,
        )
        if request.story_contract_revision_id is not None and (
            candidate.story_contract_revision_id != request.story_contract_revision_id
            or evaluation.story_contract_id != request.story_contract_revision_id
        ):
            raise StaleArtifactHead("story contract binding changed")
        if request.chapter_contract_revision_id is not None and (
            candidate.chapter_contract_revision_id
            != request.chapter_contract_revision_id
            or evaluation.chapter_contract_id != request.chapter_contract_revision_id
        ):
            raise StaleArtifactHead("chapter contract binding changed")

        state = StoryState(str(self.project_root))
        base_canon_sha = canonical_canon_sha(state)
        if base_canon_sha != request.base_canon_sha:
            raise StaleCanonError(
                f"stale canon base: expected {request.base_canon_sha}, current {base_canon_sha}"
            )
        ledger_head = self.ledger.current()
        if ledger_head is not None and ledger_head.new_canon_sha != base_canon_sha:
            raise CanonCorruptionError("StoryState does not match canon ledger head")

        apply_canon_proposal(state, proposal, candidate.sha256)
        chapter = state.get_chapter(request.chapter)
        if chapter is not None:
            chapter.canonical_revision_id = candidate.revision_id
            chapter.last_evaluation_id = report.evaluation_id
        new_canon_sha = canonical_canon_sha(state)
        committed_at = self._now()
        receipt = PromotionReceipt(
            project_id=request.project_id,
            chapter=request.chapter,
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            old_artifact_revision_id=request.expected_final_revision_id,
            old_artifact_sha256=request.expected_final_sha256,
            new_artifact_revision_id=candidate.revision_id,
            new_artifact_sha256=candidate.sha256,
            old_canon_sha=base_canon_sha,
            new_canon_sha=new_canon_sha,
            canon_proposal_id=proposal.proposal_id,
            evaluation_report_id=report.report_id,
            actor=request.actor,
            reason=request.reason,
            decision_metadata=request.decision_metadata,
            journal_id=request.idempotency_key,
            committed_at=committed_at,
        )
        entry = CanonLedgerEntry(
            previous_entry_id=ledger_head.entry_id if ledger_head else None,
            proposal_id=proposal.proposal_id,
            source_artifact_sha=candidate.sha256,
            base_canon_sha=base_canon_sha,
            new_canon_sha=new_canon_sha,
            chapter=request.chapter,
            receipt_id=receipt.receipt_id,
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            committed_at=committed_at,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "state": "prepared",
            "request": request.to_dict(),
            "receipt": receipt.to_dict(),
            "ledger_entry": entry.to_dict(),
            "state_payload": _state_payload(state, committed_at),
        }

    def _receipt_consistent(self, receipt: PromotionReceipt) -> None:
        state_sha = canonical_canon_sha(StoryState(str(self.project_root)))
        head = self.artifacts.get_head(receipt.chapter, "final")
        if state_sha != receipt.new_canon_sha or head is None or (
            head.revision_id != receipt.new_artifact_revision_id
        ):
            raise PromotionCorruptionError(
                "committed receipt does not match current state and artifact head"
            )
        matching = [
            entry
            for entry in self.ledger.history()
            if entry.idempotency_key == receipt.idempotency_key
        ]
        if len(matching) != 1 or matching[0].receipt_id != receipt.receipt_id:
            raise PromotionCorruptionError("committed receipt does not match canon ledger")

    def _resume(self, journal: dict[str, Any]) -> PromotionReceipt:
        request = PromotionRequest.from_dict(journal["request"])
        receipt = PromotionReceipt.from_dict(journal["receipt"])
        entry = CanonLedgerEntry.from_dict(journal["ledger_entry"])

        if journal["state"] == "prepared":
            current_sha = canonical_canon_sha(StoryState(str(self.project_root)))
            if current_sha == receipt.old_canon_sha:
                self._fault("before_state_commit")
                self._atomic_write(
                    self.state_path,
                    _json_bytes(journal["state_payload"]) + b"\n",
                    "state_commit",
                    request.request_id,
                )
            elif current_sha != receipt.new_canon_sha:
                raise PromotionCorruptionError(
                    "prepared journal matches neither old nor new canon state"
                )
            journal = self._write_journal(journal, "state_committed")
            self._fault("after_state_commit")

        if journal["state"] == "state_committed":
            if canonical_canon_sha(StoryState(str(self.project_root))) != receipt.new_canon_sha:
                raise PromotionCorruptionError("state_committed journal canon is missing")
            self._fault("before_head_commit")
            try:
                self.artifacts.set_head(
                    request.chapter,
                    "final",
                    request.candidate_revision_id,
                    expected_revision_id=request.expected_final_revision_id,
                )
            except ArtifactCommitUncertain:
                head = self.artifacts.get_head(request.chapter, "final")
                if head is None or head.revision_id != request.candidate_revision_id:
                    raise
            journal = self._write_journal(journal, "head_committed")
            self._fault("after_head_commit")

        if journal["state"] == "head_committed":
            head = self.artifacts.get_head(request.chapter, "final")
            if head is None or head.revision_id != request.candidate_revision_id:
                raise PromotionCorruptionError("head_committed journal artifact head is missing")
            try:
                self.ledger.append(entry)
            except CanonCommitUncertain:
                matches = [
                    item
                    for item in self.ledger.history()
                    if item.idempotency_key == request.idempotency_key
                ]
                if matches != [entry]:
                    raise
            self._fault("before_receipt_commit")
            self._atomic_write(
                self._receipt_path(request.idempotency_key),
                self._record("receipt", receipt.to_dict()),
                "receipt_commit",
                request.request_id,
            )
            self._fault("after_receipt_commit")
            journal = self._write_journal(journal, "committed")

        if journal["state"] != "committed":
            raise PromotionCorruptionError("promotion journal did not reach committed")
        stored = self._load_receipt_unlocked(request.idempotency_key)
        if stored != receipt:
            raise PromotionCorruptionError("committed journal receipt is missing or divergent")
        self._receipt_consistent(stored)
        return stored

    def promote(self, request: PromotionRequest) -> PromotionReceipt:
        if not isinstance(request, PromotionRequest):
            raise TypeError("request must be a PromotionRequest")
        # Reconstruct so all content IDs and nested hashes are checked at entry.
        request = PromotionRequest.from_dict(request.to_dict())
        with ProjectLock(self.project_root):
            self._preflight_storage()
            receipt = self._load_receipt_unlocked(request.idempotency_key)
            if receipt is not None:
                if receipt.request_id != request.request_id:
                    raise IdempotencyConflict(
                        "idempotency key is already bound to another request"
                    )
                self._receipt_consistent(receipt)
                journal = self._load_journal(request.idempotency_key)
                if journal is not None and journal["state"] != "committed":
                    self._write_journal(journal, "committed")
                return receipt

            journal = self._load_journal(request.idempotency_key)
            if journal is None:
                journal = self._prepare(request)
                self._write_journal(journal, "prepared")
            elif journal["request"]["request_id"] != request.request_id:
                raise IdempotencyConflict(
                    "idempotency key is already bound to another request"
                )
            return self._resume(journal)


__all__ = [
    "IdempotencyConflict",
    "PromotionCommitUncertain",
    "PromotionCorruptionError",
    "PromotionError",
    "PromotionReceipt",
    "PromotionRequest",
    "PromotionService",
    "StaleCanonError",
]
