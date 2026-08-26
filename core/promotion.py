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
    ArtifactCorruptionError,
    ArtifactIntegrityError,
    ArtifactRevision,
    ArtifactStore,
    StaleArtifactHead,
)
from canon import CanonDeltaProposal, apply_canon_proposal
from canon_ledger import (
    CanonCommitUncertain,
    CanonCorruptionError,
    CanonLedger,
    CanonLedgerEntry,
    canonical_canon_sha,
)
from document_tree import Binder
from project_lock import ProjectLock
from project_identity import (
    ProjectIdentityError,
    ensure_project_instance_id_unlocked,
    load_project_instance_id_unlocked,
)
from proposals import ProposalStore
from quality import EvaluationReport
from state_manager import (
    ChapterState,
    Character,
    CodexEntry,
    Collection,
    PlotThread,
    RelationshipEdge,
    StoryState,
    StyleProfile,
    TimelineEvent,
)


LEGACY_SCHEMA_VERSION = 1
SCHEMA_VERSION = 2
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = _SHA_RE
_REQUEST_ID_RE = re.compile(r"^promotion-request-[0-9a-f]{64}$")
_RECEIPT_ID_RE = re.compile(r"^promotion-receipt-[0-9a-f]{64}$")
_PROPOSAL_ID_RE = re.compile(r"^proposal-[0-9a-f]{64}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_INSTANCE_RE = re.compile(r"^[0-9a-f]{32}$")
_JOURNAL_STATES = {"prepared", "state_committed", "head_committed", "committed"}
_STATE_PAYLOAD_FIELDS = {
    "metadata",
    "story_bible",
    "characters",
    "codex",
    "relationships",
    "collections",
    "continuity_exemptions",
    "compile_styles",
    "plot_threads",
    "chapters",
    "binder",
    "timeline",
    "style_profile",
    "session_log",
    "last_saved",
}


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
    identity = {
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
    if request.schema_version == SCHEMA_VERSION:
        identity = {
            "project_id": request.project_id,
            "project_instance_id": request.project_instance_id,
            **{key: value for key, value in identity.items() if key != "project_id"},
        }
    return identity


@dataclass(frozen=True)
class PromotionRequest:
    project_id: str
    project_instance_id: str
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
        if type(self.schema_version) is not int or self.schema_version not in {
            LEGACY_SCHEMA_VERSION,
            SCHEMA_VERSION,
        }:
            raise ValueError("schema_version must be the integer 1 or 2")
        object.__setattr__(self, "project_id", _required(self.project_id, "project_id"))
        if self.schema_version == LEGACY_SCHEMA_VERSION:
            if self.project_instance_id != "":
                raise ValueError("legacy promotion request must not have a project identity")
        elif not isinstance(self.project_instance_id, str) or not _INSTANCE_RE.fullmatch(
            self.project_instance_id
        ):
            raise ValueError("project_instance_id has invalid format")
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
        legacy_fields = {
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
        if not isinstance(data, Mapping):
            raise ValueError("promotion request has invalid fields")
        fields = set(data)
        schema_version = data.get("schema_version")
        if schema_version == LEGACY_SCHEMA_VERSION:
            if fields != legacy_fields:
                raise ValueError("promotion request has invalid fields")
            values = {"project_instance_id": "", **dict(data)}
        elif schema_version == SCHEMA_VERSION:
            if fields != legacy_fields | {"project_instance_id"}:
                raise ValueError("promotion request has invalid fields")
            values = dict(data)
        else:
            raise ValueError("promotion request schema version is invalid")
        values["evaluation_report"] = EvaluationReport.from_dict(
            values["evaluation_report"]
        )
        return cls(**values)


def _receipt_identity(receipt: "PromotionReceipt") -> dict[str, Any]:
    identity = {
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
    if receipt.schema_version == SCHEMA_VERSION:
        identity = {
            "project_id": receipt.project_id,
            "project_instance_id": receipt.project_instance_id,
            **{key: value for key, value in identity.items() if key != "project_id"},
        }
    return identity


@dataclass(frozen=True)
class PromotionReceipt:
    project_id: str
    project_instance_id: str
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
        if type(self.schema_version) is not int or self.schema_version not in {
            LEGACY_SCHEMA_VERSION,
            SCHEMA_VERSION,
        }:
            raise ValueError("schema_version must be the integer 1 or 2")
        object.__setattr__(self, "project_id", _required(self.project_id, "project_id"))
        if self.schema_version == LEGACY_SCHEMA_VERSION:
            if self.project_instance_id != "":
                raise ValueError("legacy promotion receipt must not have a project identity")
        elif not isinstance(self.project_instance_id, str) or not _INSTANCE_RE.fullmatch(
            self.project_instance_id
        ):
            raise ValueError("project_instance_id has invalid format")
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
        if not isinstance(data, Mapping):
            raise ValueError("promotion receipt has invalid fields")
        legacy_fields = {"receipt_id", *list(_receipt_identity_fields())}
        fields = set(data)
        schema_version = data.get("schema_version")
        if schema_version == LEGACY_SCHEMA_VERSION:
            if fields != legacy_fields:
                raise ValueError("promotion receipt has invalid fields")
            values = {"project_instance_id": "", **dict(data)}
        elif schema_version == SCHEMA_VERSION:
            if fields != legacy_fields | {"project_instance_id"}:
                raise ValueError("promotion receipt has invalid fields")
            values = dict(data)
        else:
            raise ValueError("promotion receipt schema version is invalid")
        return cls(**values)


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


def _state_from_payload(payload: Mapping[str, Any]) -> StoryState:
    if not isinstance(payload, Mapping) or set(payload) != _STATE_PAYLOAD_FIELDS:
        raise ValueError("state payload has invalid fields")
    try:
        data = json.loads(_json_bytes(payload))
        state = StoryState.__new__(StoryState)
        state.metadata = dict(data["metadata"])
        state.story_bible = dict(data["story_bible"])
        state.characters = {
            key: Character.from_dict(value)
            for key, value in data["characters"].items()
        }
        state.codex = {
            key: CodexEntry.from_dict(value)
            for key, value in data["codex"].items()
        }
        state.relationships = {
            key: RelationshipEdge.from_dict(value)
            for key, value in data["relationships"].items()
        }
        state.collections = {
            key: Collection.from_dict(value)
            for key, value in data["collections"].items()
        }
        state.continuity_exemptions = dict(data["continuity_exemptions"])
        state.compile_styles = dict(data["compile_styles"])
        state.plot_threads = {
            key: PlotThread.from_dict(value)
            for key, value in data["plot_threads"].items()
        }
        state.chapters = {
            int(key): ChapterState.from_dict(value)
            for key, value in data["chapters"].items()
        }
        state.binder = Binder.from_list(data["binder"])
        state.timeline = [TimelineEvent.from_dict(value) for value in data["timeline"]]
        state.style_profile = StyleProfile.from_dict(dict(data["style_profile"]))
        state.session_log = list(data["session_log"])
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"state payload is invalid: {exc}") from exc
    return state


def _apply_canon_proposal_at(
    state: StoryState,
    proposal: CanonDeltaProposal,
    actual_artifact_sha: str,
    committed_at: str,
) -> None:
    session_log_length = len(state.session_log)
    milestone_lengths = {
        thread_id: len(thread.milestones)
        for thread_id, thread in state.plot_threads.items()
    }
    apply_canon_proposal(state, proposal, actual_artifact_sha)

    chapter = state.get_chapter(proposal.chapter)
    if chapter is not None:
        chapter.status = "complete"
        chapter.last_modified = committed_at
        if "status" in chapter.continuity_checks:
            chapter.continuity_checks["validated_at"] = committed_at
    for entry in state.session_log[session_log_length:]:
        entry["timestamp"] = committed_at
    for thread_id, thread in state.plot_threads.items():
        prior_length = milestone_lengths.get(thread_id, 0)
        for milestone in thread.milestones[prior_length:]:
            milestone["timestamp"] = committed_at


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

    def _project_instance_id(self) -> str:
        try:
            return load_project_instance_id_unlocked(self.project_root)
        except ProjectIdentityError as exc:
            raise PromotionCorruptionError(f"invalid project identity: {exc}") from exc

    def _ensure_project_instance_id(self) -> str:
        try:
            return ensure_project_instance_id_unlocked(self.project_root)
        except ProjectIdentityError as exc:
            raise PromotionCorruptionError(f"invalid project identity: {exc}") from exc

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
        try:
            self._ensure_storage_directory(self.artifacts.artifact_root)
            self._ensure_storage_directory(self.artifacts.blob_root)
        except PromotionCorruptionError as exc:
            raise PromotionCorruptionError(f"unsafe artifact storage: {exc}") from exc
        artifact_files = (
            self.artifacts.heads_path,
            self.artifacts.revisions_path,
            *self.artifacts.blob_root.iterdir(),
        )
        for path in artifact_files:
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise PromotionCorruptionError(
                    f"artifact storage entry must be a regular non-symlink file: {path}"
                )
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
        if (
            receipt.schema_version == SCHEMA_VERSION
            and receipt.project_instance_id != self._project_instance_id()
        ):
            raise PromotionCorruptionError(
                "receipt project does not match promotion service project"
            )
        if receipt.idempotency_key != key:
            raise PromotionCorruptionError("receipt idempotency key does not match path")
        return receipt

    def load_receipt(
        self,
        idempotency_key: str,
        *,
        check_current_tail: bool = True,
    ) -> Optional[PromotionReceipt]:
        """Load one durable receipt and validate its own ledger bindings.

        Recovery may inspect a historical receipt while the projected
        StoryState is still at an earlier snapshot.  In that case the receipt
        must remain independently verifiable without requiring the current
        state to match the ledger tail; normal callers retain the strict tail
        check by default.
        """
        key = self._safe_key(idempotency_key)
        with ProjectLock(self.project_root):
            self._ensure_project_instance_id()
            self._validate_existing_transaction_identities()
            self._preflight_storage()
            receipt = self._load_receipt_unlocked(key)
            if receipt is not None:
                self._receipt_consistent(receipt, check_current_tail=check_current_tail)
            return receipt

    def recover_unfinished(self) -> None:
        """Recover durable promotion journals without listing the ledger tail."""
        with ProjectLock(self.project_root):
            self._ensure_project_instance_id()
            self._preflight_storage()
            unfinished = any(
                (journal := self._load_journal(path.stem)) is not None
                and journal["state"] != "committed"
                for path in sorted(
                    self.journal_dir.glob("*.json"), key=lambda item: item.name
                )
            )
            if unfinished:
                self._recover_unfinished_journals()
                self._validate_existing_transaction_identities()

    def list_receipts(self, *, chapter: Optional[int] = None) -> tuple[PromotionReceipt, ...]:
        """Validate durable promotion state once and return committed receipts."""
        if chapter is not None and (
            isinstance(chapter, bool) or not isinstance(chapter, int) or chapter < 1
        ):
            raise ValueError("chapter must be a positive integer")
        with ProjectLock(self.project_root):
            self._ensure_project_instance_id()
            self._validate_existing_transaction_identities()
            self._preflight_storage()
            unfinished = any(
                (journal := self._load_journal(path.stem)) is not None
                and journal["state"] != "committed"
                for path in sorted(
                    self.journal_dir.glob("*.json"), key=lambda item: item.name
                )
            )
            if unfinished:
                self._recover_unfinished_journals()
                self._validate_existing_transaction_identities()

            receipts: list[PromotionReceipt] = []
            for path in sorted(self.receipt_dir.glob("*.json"), key=lambda item: item.name):
                receipt = self._load_receipt_unlocked(path.stem)
                if receipt is None:
                    raise PromotionCorruptionError(
                        f"promotion receipt disappeared during listing: {path.name}"
                    )
                self._validate_receipt_artifact(receipt)
                if chapter is None or receipt.chapter == chapter:
                    receipts.append(receipt)

            history = self.ledger.history()
            if history:
                tail_key = history[-1].idempotency_key
                tail = next(
                    (
                        receipt
                        for receipt in receipts
                        if receipt.idempotency_key == tail_key
                    ),
                    None,
                )
                if tail is None and chapter is not None:
                    tail = self._load_receipt_unlocked(tail_key)
                if tail is None:
                    raise PromotionCorruptionError(
                        "canon ledger tail has no committed promotion receipt"
                    )
                self._receipt_consistent(tail)

            return tuple(sorted(receipts, key=lambda receipt: receipt.committed_at))

    def _load_journal(self, key: str) -> Optional[dict[str, Any]]:
        payload = self._read_record(self._journal_path(key), "journal")
        if payload is None:
            return None
        legacy_fields = {
            "schema_version",
            "state",
            "request",
            "receipt",
            "ledger_entry",
            "state_payload",
        }
        schema_version = payload.get("schema_version")
        if schema_version == LEGACY_SCHEMA_VERSION:
            expected = legacy_fields
        elif schema_version == SCHEMA_VERSION:
            expected = legacy_fields | {"base_state_payload"}
        else:
            raise PromotionCorruptionError("journal schema version is invalid")
        if set(payload) != expected:
            raise PromotionCorruptionError("journal has invalid fields")
        if payload["state"] not in _JOURNAL_STATES:
            raise PromotionCorruptionError("journal state is invalid")
        try:
            request = PromotionRequest.from_dict(payload["request"])
            receipt = PromotionReceipt.from_dict(payload["receipt"])
            entry = CanonLedgerEntry.from_dict(payload["ledger_entry"])
            if schema_version == SCHEMA_VERSION:
                _json_bytes(payload["base_state_payload"])
            _json_bytes(payload["state_payload"])
        except (TypeError, ValueError) as exc:
            raise PromotionCorruptionError(f"journal contract is invalid: {exc}") from exc
        if not (
            request.schema_version
            == receipt.schema_version
            == entry.schema_version
            == schema_version
        ):
            raise PromotionCorruptionError(
                "journal records do not match journal schema version"
            )
        if schema_version == SCHEMA_VERSION and (
            request.project_instance_id != self._project_instance_id()
            or receipt.project_instance_id != self._project_instance_id()
        ):
            raise PromotionCorruptionError(
                "journal project does not match promotion service project"
            )
        self._validate_journal_bindings(
            request, receipt, entry, payload["state_payload"]
        )
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

    def _validate_contract_revision(
        self, chapter: int, kind: str, revision_id: Optional[str]
    ) -> None:
        if revision_id is None:
            return
        revision = self.artifacts.get_revision(revision_id)
        if revision.chapter != chapter or revision.kind != kind:
            raise ValueError(
                f"{kind} revision does not match chapter {chapter}: {revision_id}"
            )
        self.artifacts.read_text(revision_id)

    def _validate_final_head(self, request: PromotionRequest) -> None:
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
            self.artifacts.read_text(old_revision.revision_id)

    def _validate_request_resources(
        self,
        request: PromotionRequest,
        *,
        require_current_contract_heads: bool = True,
    ) -> tuple[ArtifactRevision, EvaluationReport, CanonDeltaProposal]:
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

        if (
            candidate.story_contract_revision_id != request.story_contract_revision_id
            or evaluation.story_contract_id
            != (request.story_contract_revision_id or "")
        ):
            raise StaleArtifactHead("story contract binding changed")
        if (
            candidate.chapter_contract_revision_id
            != request.chapter_contract_revision_id
            or evaluation.chapter_contract_id
            != (request.chapter_contract_revision_id or "")
        ):
            raise StaleArtifactHead("chapter contract binding changed")
        self._validate_contract_revision(
            0, "story_contract", request.story_contract_revision_id
        )
        self._validate_contract_revision(
            request.chapter,
            "chapter_contract",
            request.chapter_contract_revision_id,
        )
        if require_current_contract_heads:
            self._validate_contract_head(
                0, "story_contract", request.story_contract_revision_id
            )
            self._validate_contract_head(
                request.chapter,
                "chapter_contract",
                request.chapter_contract_revision_id,
            )
        return candidate, report, proposal

    def _prepare(self, request: PromotionRequest) -> dict[str, Any]:
        if request.project_instance_id != self._project_instance_id():
            raise ValueError(
                "promotion request project instance does not match service project"
            )
        candidate, report, proposal = self._validate_request_resources(request)

        self._validate_final_head(request)

        state = StoryState(str(self.project_root))
        base_canon_sha = canonical_canon_sha(state)
        if base_canon_sha != request.base_canon_sha:
            raise StaleCanonError(
                f"stale canon base: expected {request.base_canon_sha}, current {base_canon_sha}"
            )
        ledger_head = self.ledger.current()
        if ledger_head is not None and ledger_head.new_canon_sha != base_canon_sha:
            raise CanonCorruptionError("StoryState does not match canon ledger head")

        committed_at = self._now()
        base_state_payload = json.loads(
            _json_bytes(_state_payload(state, committed_at))
        )
        _apply_canon_proposal_at(
            state, proposal, candidate.sha256, committed_at
        )
        chapter = state.get_chapter(request.chapter)
        if chapter is not None:
            chapter.canonical_revision_id = candidate.revision_id
            chapter.last_evaluation_id = report.evaluation_id
        new_canon_sha = canonical_canon_sha(state)
        # Keep the in-memory journal identical to the JSON representation that
        # is written before replay. In particular, StoryState chapter keys are
        # integers in memory but strings after JSON persistence.
        state_payload = json.loads(_json_bytes(_state_payload(state, committed_at)))
        receipt = PromotionReceipt(
            project_id=request.project_id,
            project_instance_id=request.project_instance_id,
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
            project_instance_id=request.project_instance_id,
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
            "base_state_payload": base_state_payload,
            "state_payload": state_payload,
        }

    @staticmethod
    def _entry_matches_receipt(
        entry: CanonLedgerEntry, receipt: PromotionReceipt
    ) -> bool:
        expected = {
            "project_instance_id": receipt.project_instance_id,
            "receipt_id": receipt.receipt_id,
            "request_id": receipt.request_id,
            "idempotency_key": receipt.idempotency_key,
            "proposal_id": receipt.canon_proposal_id,
            "source_artifact_sha": receipt.new_artifact_sha256,
            "base_canon_sha": receipt.old_canon_sha,
            "new_canon_sha": receipt.new_canon_sha,
            "chapter": receipt.chapter,
            "committed_at": receipt.committed_at,
        }
        return all(getattr(entry, field) == value for field, value in expected.items())

    def _validate_receipt_artifact(self, receipt: PromotionReceipt) -> None:
        try:
            revision = self.artifacts.get_revision(receipt.new_artifact_revision_id)
        except (ArtifactCorruptionError, KeyError) as exc:
            raise PromotionCorruptionError(
                "committed receipt artifact revision is missing or corrupt"
            ) from exc
        if (
            revision.chapter != receipt.chapter
            or revision.kind != "final"
            or revision.sha256 != receipt.new_artifact_sha256
        ):
            raise PromotionCorruptionError(
                "committed receipt does not match its artifact revision"
            )
        try:
            self.artifacts.read_text(receipt.new_artifact_revision_id)
        except (ArtifactCorruptionError, ArtifactIntegrityError, KeyError) as exc:
            raise PromotionCorruptionError(
                "committed receipt artifact blob failed integrity validation"
            ) from exc

    def _receipt_consistent(
        self, receipt: PromotionReceipt, *, check_current_tail: bool = True
    ) -> None:
        history = self.ledger.history()
        matching = [
            entry
            for entry in history
            if entry.idempotency_key == receipt.idempotency_key
        ]
        if len(matching) != 1 or not self._entry_matches_receipt(
            matching[0], receipt
        ):
            raise PromotionCorruptionError("committed receipt does not match canon ledger")
        entry = matching[0]
        self._validate_receipt_artifact(receipt)

        # Historical receipts remain valid after later commits. Only the ledger
        # tail is required to agree with the current canonical projections.
        if check_current_tail and entry == history[-1]:
            state_sha = canonical_canon_sha(StoryState(str(self.project_root)))
            head = self.artifacts.get_head(receipt.chapter, "final")
            if state_sha != receipt.new_canon_sha or head is None or (
                head.revision_id != receipt.new_artifact_revision_id
            ):
                raise PromotionCorruptionError(
                    "latest committed receipt does not match current state and artifact head"
                )

    def _validate_resume_ledger_base(
        self,
        journal_state: str,
        entry: CanonLedgerEntry,
        receipt: PromotionReceipt,
    ) -> None:
        if not self._entry_matches_receipt(entry, receipt):
            raise PromotionCorruptionError(
                "promotion journal ledger entry does not match its receipt"
            )
        history = self.ledger.history()
        matching = [
            item
            for item in history
            if item.idempotency_key == entry.idempotency_key
        ]
        if matching:
            if (
                len(matching) != 1
                or matching[0] != entry
                or journal_state not in {"head_committed", "committed"}
            ):
                raise CanonCorruptionError(
                    "promotion journal conflicts with committed canon history"
                )
            return

        tail = history[-1] if history else None
        expected_previous = tail.entry_id if tail else None
        if (
            entry.previous_entry_id != expected_previous
            or entry.base_canon_sha != receipt.old_canon_sha
            or (tail is not None and tail.new_canon_sha != receipt.old_canon_sha)
        ):
            raise CanonCorruptionError(
                "promotion journal no longer extends the canon ledger base"
            )

    def _validate_journal_bindings(
        self,
        request: PromotionRequest,
        receipt: PromotionReceipt,
        entry: CanonLedgerEntry,
        state_payload: Mapping[str, Any],
    ) -> None:
        request_binding = (
            request.project_id == receipt.project_id
            and request.project_instance_id == receipt.project_instance_id
            and request.chapter == receipt.chapter
            and request.request_id == receipt.request_id
            and request.idempotency_key == receipt.idempotency_key
            and request.candidate_revision_id == receipt.new_artifact_revision_id
            and request.candidate_sha256 == receipt.new_artifact_sha256
            and request.expected_final_revision_id
            == receipt.old_artifact_revision_id
            and request.expected_final_sha256 == receipt.old_artifact_sha256
            and request.base_canon_sha == receipt.old_canon_sha
            and request.canon_proposal_id == receipt.canon_proposal_id
            and request.evaluation_report.report_id == receipt.evaluation_report_id
            and request.actor == receipt.actor
            and request.reason == receipt.reason
            and _thaw(request.decision_metadata)
            == _thaw(receipt.decision_metadata)
            and receipt.journal_id == request.idempotency_key
        )
        if not request_binding or not self._entry_matches_receipt(entry, receipt):
            raise PromotionCorruptionError(
                "promotion journal request, receipt, and ledger entry are not bound"
            )
        try:
            payload_canon_sha = canonical_canon_sha(state_payload)
        except (TypeError, ValueError) as exc:
            raise PromotionCorruptionError(
                f"promotion journal state payload is invalid: {exc}"
            ) from exc
        if payload_canon_sha != receipt.new_canon_sha:
            raise PromotionCorruptionError(
                "promotion journal state payload does not match receipt canon"
            )

    def _validate_journal_derivation(self, journal: Mapping[str, Any]) -> None:
        request = PromotionRequest.from_dict(journal["request"])
        receipt = PromotionReceipt.from_dict(journal["receipt"])
        entry = CanonLedgerEntry.from_dict(journal["ledger_entry"])
        if journal["schema_version"] == LEGACY_SCHEMA_VERSION:
            self._validate_legacy_journal_derivation(
                journal, request, receipt, entry
            )
            return
        try:
            base_state = _state_from_payload(journal["base_state_payload"])
            base_payload_sha = canonical_canon_sha(journal["base_state_payload"])
            restored_base_sha = canonical_canon_sha(base_state)
        except (KeyError, TypeError, ValueError) as exc:
            raise PromotionCorruptionError(
                f"promotion journal base state is invalid: {exc}"
            ) from exc
        if (
            base_payload_sha != restored_base_sha
            or base_payload_sha != request.base_canon_sha
            or base_payload_sha != receipt.old_canon_sha
            or base_payload_sha != entry.base_canon_sha
        ):
            raise PromotionCorruptionError(
                "promotion journal base state does not match its anchored canon"
            )

        try:
            candidate, report, proposal = self._validate_request_resources(
                request,
                require_current_contract_heads=False,
            )
            _apply_canon_proposal_at(
                base_state, proposal, candidate.sha256, receipt.committed_at
            )
            chapter = base_state.get_chapter(request.chapter)
            if chapter is not None:
                chapter.canonical_revision_id = candidate.revision_id
                chapter.last_evaluation_id = report.evaluation_id
            # JSON persistence converts integer chapter keys to strings. Normalize
            # the replay projection through the same encoding boundary before the
            # exact payload comparison below.
            expected_state_payload = json.loads(
                _json_bytes(_state_payload(base_state, receipt.committed_at))
            )
            derived_canon_sha = canonical_canon_sha(base_state)
            projected_state = _state_from_payload(journal["state_payload"])
            projected_payload_sha = canonical_canon_sha(journal["state_payload"])
            restored_projection_sha = canonical_canon_sha(projected_state)
        except (
            ArtifactCorruptionError,
            ArtifactIntegrityError,
            KeyError,
            OSError,
            StaleArtifactHead,
            TypeError,
            ValueError,
        ) as exc:
            raise PromotionCorruptionError(
                f"promotion journal artifact replay resources are invalid: {exc}"
            ) from exc
        if (
            _json_bytes(expected_state_payload)
            != _json_bytes(journal["state_payload"])
            or projected_payload_sha != restored_projection_sha
            or derived_canon_sha != projected_payload_sha
            or derived_canon_sha != receipt.new_canon_sha
            or derived_canon_sha != entry.new_canon_sha
        ):
            raise PromotionCorruptionError(
                "promotion journal derived canon does not match persisted proposal replay"
            )

    def _validate_legacy_journal_derivation(
        self,
        journal: Mapping[str, Any],
        request: PromotionRequest,
        receipt: PromotionReceipt,
        entry: CanonLedgerEntry,
    ) -> None:
        """Validate v1 journals at the strongest boundary their format retained.

        V1 did not persist the base state payload. A prepared journal whose live
        state is still at the old canon can therefore be replayed exactly. Once
        state was committed, the old payload is irretrievable; validation is
        limited to immutable resources, cross-record bindings, projected-state
        integrity, and the state-specific recovery checks in ``_resume``.
        """
        try:
            candidate, report, proposal = self._validate_request_resources(
                request,
                require_current_contract_heads=False,
            )
            projected_state = _state_from_payload(journal["state_payload"])
            projected_payload_sha = canonical_canon_sha(journal["state_payload"])
            restored_projection_sha = canonical_canon_sha(projected_state)
        except (
            ArtifactCorruptionError,
            ArtifactIntegrityError,
            KeyError,
            OSError,
            StaleArtifactHead,
            TypeError,
            ValueError,
        ) as exc:
            raise PromotionCorruptionError(
                f"legacy promotion journal resources are invalid: {exc}"
            ) from exc
        if (
            projected_payload_sha != restored_projection_sha
            or (
                journal["state"] != "prepared"
                and (
                    projected_payload_sha != receipt.new_canon_sha
                    or projected_payload_sha != entry.new_canon_sha
                )
            )
        ):
            raise PromotionCorruptionError(
                "legacy promotion journal projected canon is invalid"
            )

        if journal["state"] != "prepared":
            return
        current_state = StoryState(str(self.project_root))
        current_sha = canonical_canon_sha(current_state)
        if current_sha == receipt.new_canon_sha:
            current_payload = _state_payload(current_state, receipt.committed_at)
            if (
                projected_payload_sha != receipt.new_canon_sha
                or projected_payload_sha != entry.new_canon_sha
                or _json_bytes(current_payload)
                != _json_bytes(journal["state_payload"])
            ):
                raise PromotionCorruptionError(
                    "legacy prepared journal projection differs from committed state"
                )
            return
        if current_sha != receipt.old_canon_sha:
            raise PromotionCorruptionError(
                "prepared legacy journal matches neither old nor new canon state"
            )
        base_session_log_length = len(current_state.session_log)
        milestone_lengths = {
            thread_id: len(thread.milestones)
            for thread_id, thread in current_state.plot_threads.items()
        }
        _apply_canon_proposal_at(
            current_state, proposal, candidate.sha256, receipt.committed_at
        )
        chapter = current_state.get_chapter(request.chapter)
        if chapter is not None:
            chapter.canonical_revision_id = candidate.revision_id
            chapter.last_evaluation_id = report.evaluation_id
        expected_payload = _state_payload(current_state, receipt.committed_at)
        persisted_payload = journal["state_payload"]

        def persisted_timestamp(value: Any, field: str) -> str:
            if not isinstance(value, str):
                raise ValueError(f"{field} must be an ISO datetime string")
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"{field} must be an ISO datetime string") from exc
            return value

        try:
            expected_chapter = expected_payload["chapters"][request.chapter]
            persisted_chapter = persisted_payload["chapters"][str(request.chapter)]
            expected_chapter["last_modified"] = persisted_timestamp(
                persisted_chapter["last_modified"],
                "chapter.last_modified",
            )
            if "status" in proposal.delta:
                expected_chapter["continuity_checks"]["validated_at"] = (
                    persisted_timestamp(
                        persisted_chapter["continuity_checks"]["validated_at"],
                        "chapter.continuity_checks.validated_at",
                    )
                )

            expected_log = expected_payload["session_log"]
            persisted_log = persisted_payload["session_log"]
            for index in range(base_session_log_length, len(expected_log)):
                expected_log[index]["timestamp"] = persisted_timestamp(
                    persisted_log[index]["timestamp"],
                    f"session_log[{index}].timestamp",
                )

            for thread_id, thread in current_state.plot_threads.items():
                prior_length = milestone_lengths.get(thread_id, 0)
                expected_milestones = expected_payload["plot_threads"][thread_id][
                    "milestones"
                ]
                persisted_milestones = persisted_payload["plot_threads"][thread_id][
                    "milestones"
                ]
                if len(persisted_milestones) != len(expected_milestones):
                    raise ValueError("plot-thread milestone count differs from replay")
                for index in range(prior_length, len(expected_milestones)):
                    expected_milestones[index]["timestamp"] = persisted_timestamp(
                        persisted_milestones[index]["timestamp"],
                        f"plot_threads.{thread_id}.milestones[{index}].timestamp",
                    )
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise PromotionCorruptionError(
                f"legacy prepared journal timestamp projection is invalid: {exc}"
            ) from exc

        # Every byte outside the four historically nondeterministic timestamp
        # classes above must be derived exactly from the live base and proposal.
        if (
            projected_payload_sha != receipt.new_canon_sha
            or projected_payload_sha != entry.new_canon_sha
            or _json_bytes(expected_payload) != _json_bytes(persisted_payload)
        ):
            raise PromotionCorruptionError(
                "legacy prepared journal full projection does not match proposal replay"
            )

    def _revalidate_prepared_commit_boundary(
        self,
        journal: Mapping[str, Any],
        request: PromotionRequest,
        receipt: PromotionReceipt,
        entry: CanonLedgerEntry,
    ) -> None:
        if (
            request.schema_version == SCHEMA_VERSION
            and request.project_instance_id != self._project_instance_id()
        ):
            raise PromotionCorruptionError(
                "journal project changed before state commit"
            )
        self._validate_journal_derivation(journal)
        self._validate_resume_ledger_base("prepared", entry, receipt)
        current_sha = canonical_canon_sha(StoryState(str(self.project_root)))
        if current_sha != receipt.old_canon_sha:
            raise StaleCanonError(
                f"stale canon base: expected {receipt.old_canon_sha}, current {current_sha}"
            )
        self._validate_final_head(request)
        self._validate_contract_head(
            0, "story_contract", request.story_contract_revision_id
        )
        self._validate_contract_head(
            request.chapter,
            "chapter_contract",
            request.chapter_contract_revision_id,
        )

    def _recover_unfinished_journals(self) -> None:
        journals: list[tuple[str, dict[str, Any]]] = []
        for path in sorted(self.journal_dir.glob("*.json"), key=lambda item: item.name):
            key = path.stem
            try:
                self._safe_key(key)
            except ValueError as exc:
                raise PromotionCorruptionError(
                    f"promotion journal has an unsafe filename: {path.name}"
                ) from exc
            journal = self._load_journal(key)
            if journal is None:
                raise PromotionCorruptionError(
                    f"promotion journal disappeared during recovery: {path.name}"
                )
            request = PromotionRequest.from_dict(journal["request"])
            if request.idempotency_key != key:
                raise PromotionCorruptionError(
                    "promotion journal idempotency key does not match its filename"
                )
            journals.append((key, journal))

        # Validate every derivation before any unfinished transaction can move
        # state, heads, ledger, or receipts.
        for _key, journal in journals:
            self._validate_journal_derivation(journal)

        # Validate every already-committed record before allowing recovery to
        # mutate projections. A later unfinished journal may already have moved
        # state/head, so this first pass checks immutable ledger binding only.
        for key, journal in journals:
            if journal["state"] == "committed":
                expected_receipt = PromotionReceipt.from_dict(journal["receipt"])
                stored_receipt = self._load_receipt_unlocked(key)
                if stored_receipt != expected_receipt:
                    raise PromotionCorruptionError(
                        "committed promotion journal receipt is missing or divergent"
                    )
                self._receipt_consistent(stored_receipt, check_current_tail=False)

        for _key, journal in journals:
            if journal["state"] != "committed":
                self._resume(journal)

    def _validate_existing_transaction_identities(self) -> None:
        history = self.ledger.history()
        history_by_key: dict[str, CanonLedgerEntry] = {}
        duplicate_history_keys: set[str] = set()
        for entry in history:
            if entry.idempotency_key in history_by_key:
                duplicate_history_keys.add(entry.idempotency_key)
            else:
                history_by_key[entry.idempotency_key] = entry
        stores = (
            (self.journal_dir, self._load_journal),
            (self.receipt_dir, self._load_receipt_unlocked),
        )
        for directory, loader in stores:
            try:
                mode = directory.lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise PromotionCorruptionError(
                    f"promotion transaction storage must be a real directory: {directory}"
                )
            try:
                entries = sorted(directory.iterdir(), key=lambda item: item.name)
            except OSError as exc:
                raise PromotionCorruptionError(
                    f"cannot inspect promotion transaction storage: {directory}"
                ) from exc
            for path in entries:
                try:
                    entry_mode = path.lstat().st_mode
                except FileNotFoundError as exc:
                    raise PromotionCorruptionError(
                        f"promotion transaction storage entry disappeared: {path.name}"
                    ) from exc
                except OSError as exc:
                    raise PromotionCorruptionError(
                        f"cannot inspect promotion transaction storage entry: {path.name}"
                    ) from exc
                if stat.S_ISLNK(entry_mode) or not stat.S_ISREG(entry_mode):
                    raise PromotionCorruptionError(
                        "promotion transaction storage entry must be a regular "
                        f"non-symlink file: {path.name}"
                    )
                if path.suffix != ".json":
                    raise PromotionCorruptionError(
                        f"unknown promotion transaction storage entry: {path.name}"
                    )
                key = path.stem
                try:
                    self._safe_key(key)
                except ValueError as exc:
                    raise PromotionCorruptionError(
                        f"promotion transaction has an unsafe filename: {path.name}"
                    ) from exc
                record = loader(key)
                if record is None:
                    raise PromotionCorruptionError(
                        f"promotion transaction record disappeared: {path.name}"
                    )
                if directory == self.receipt_dir:
                    matching = history_by_key.get(record.idempotency_key)
                    if (
                        matching is None
                        or record.idempotency_key in duplicate_history_keys
                        or not self._entry_matches_receipt(matching, record)
                    ):
                        raise PromotionCorruptionError(
                            "persisted receipt does not match exactly one canon ledger entry"
                        )

    def _resume(self, journal: dict[str, Any]) -> PromotionReceipt:
        request = PromotionRequest.from_dict(journal["request"])
        receipt = PromotionReceipt.from_dict(journal["receipt"])
        entry = CanonLedgerEntry.from_dict(journal["ledger_entry"])
        self._validate_receipt_artifact(receipt)
        self._validate_journal_derivation(journal)
        if request.schema_version == SCHEMA_VERSION and (
            request.project_instance_id != self._project_instance_id()
            or receipt.project_instance_id != self._project_instance_id()
        ):
            raise PromotionCorruptionError(
                "journal project does not match promotion service project"
            )
        self._validate_resume_ledger_base(journal["state"], entry, receipt)

        if journal["state"] == "prepared":
            current_sha = canonical_canon_sha(StoryState(str(self.project_root)))
            if current_sha == receipt.old_canon_sha:
                self._validate_final_head(request)
                self._validate_contract_head(
                    0, "story_contract", request.story_contract_revision_id
                )
                self._validate_contract_head(
                    request.chapter,
                    "chapter_contract",
                    request.chapter_contract_revision_id,
                )
                self._fault("before_state_commit")
                self._revalidate_prepared_commit_boundary(
                    journal, request, receipt, entry
                )
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
            if canonical_canon_sha(StoryState(str(self.project_root))) != receipt.new_canon_sha:
                raise PromotionCorruptionError(
                    "head_committed journal canon does not match the transaction"
                )
            head = self.artifacts.get_head(request.chapter, "final")
            if head is None or head.revision_id != request.candidate_revision_id:
                raise PromotionCorruptionError("head_committed journal artifact head is missing")
            try:
                self.ledger.append(
                    entry,
                    _recovering_legacy_journal=(
                        journal["schema_version"] == LEGACY_SCHEMA_VERSION
                    ),
                )
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
            project_instance_id = self._ensure_project_instance_id()
            if (
                request.schema_version == SCHEMA_VERSION
                and request.project_instance_id != project_instance_id
            ):
                raise ValueError(
                    "promotion request project instance does not match service project"
                )
            self._validate_existing_transaction_identities()
            self._preflight_storage()
            self._recover_unfinished_journals()
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
                if request.schema_version == LEGACY_SCHEMA_VERSION:
                    upgraded = request.to_dict()
                    upgraded["schema_version"] = SCHEMA_VERSION
                    upgraded["project_instance_id"] = project_instance_id
                    upgraded["request_id"] = ""
                    request = PromotionRequest.from_dict(upgraded)
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
