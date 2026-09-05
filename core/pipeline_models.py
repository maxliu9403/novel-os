"""Persistent contracts for a full-book Novel OS run.

The single-stage orchestrator predates a book-level lifecycle.  These small
models give the CLI (and a future API worker) an explicit, JSON-serializable
contract for checkpoints, retries, and resuming without changing StoryState's
existing schema.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


STAGE_STATUSES = {"pending", "running", "done", "retryable", "blocked", "failed", "skipped"}
RUN_STATUSES = {"pending", "running", "paused", "completed", "failed", "cancelled"}
APPROVAL_POLICIES = {"review_required", "auto"}
QUALITY_POLICIES = {"legacy", "evidence_v1"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_tuple(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


@dataclass
class RunSpec:
    """Immutable user intent for one full-book run."""

    project_path: str
    prompt_path: str = "-"
    num_chapters: Optional[int] = None
    target_words: Optional[int] = None
    title: str = ""
    genre: str = ""
    author: str = ""
    pov: str = ""
    edit_mode: str = "line"
    approval_policy: str = "review_required"
    max_retries: int = 5
    max_quality_repairs: int = 2
    retry_backoff_seconds: float = 2.0
    output_formats: Tuple[str, ...] = ("markdown", "epub", "pdf", "docx")
    dry_run: bool = False
    model: str = ""
    quality_policy: str = "legacy"

    def __post_init__(self) -> None:
        self.project_path = str(self.project_path)
        self.prompt_path = str(self.prompt_path)
        self.output_formats = _as_tuple(self.output_formats)
        if self.num_chapters is not None and self.num_chapters < 1:
            raise ValueError("num_chapters must be at least 1")
        if self.target_words is not None and self.target_words < 1:
            raise ValueError("target_words must be at least 1")
        if (
            self.num_chapters is not None
            and self.target_words is not None
            and self.target_words < self.num_chapters
        ):
            raise ValueError("target_words must be at least num_chapters")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.max_quality_repairs < 0:
            raise ValueError("max_quality_repairs cannot be negative")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds cannot be negative")
        if self.approval_policy not in APPROVAL_POLICIES:
            raise ValueError(f"Unknown approval policy '{self.approval_policy}'")
        if self.quality_policy not in QUALITY_POLICIES:
            raise ValueError(f"Unknown quality policy '{self.quality_policy}'")

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["output_formats"] = list(self.output_formats)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunSpec":
        return cls(**data)


@dataclass
class StageResult:
    phase: str
    status: str = "pending"
    chapter: Optional[int] = None
    attempt: int = 1
    artifact_paths: List[str] = field(default_factory=list)
    artifact_hashes: Dict[str, str] = field(default_factory=dict)
    input_hashes: Dict[str, str] = field(default_factory=dict)
    revision_id: str = ""
    story_contract_revision_id: str = ""
    chapter_contract_revision_id: str = ""
    canon_proposal_ids: List[str] = field(default_factory=list)
    evaluation_report_ids: List[str] = field(default_factory=list)
    promotion_receipt_id: str = ""
    state_snapshot_path: str = ""
    state_snapshot_hash: str = ""
    findings: List[Dict[str, Any]] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    started_at: str = ""
    finished_at: str = ""
    error: Optional[str] = None
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.status not in STAGE_STATUSES:
            raise ValueError(f"Unknown stage status '{self.status}'")
        if self.attempt < 1:
            raise ValueError("attempt must be at least 1")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StageResult":
        return cls(**data)


@dataclass
class RunManifest:
    run_id: str
    spec: RunSpec
    status: str = "pending"
    current_phase: str = ""
    current_chapter: Optional[int] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    stages: Dict[str, StageResult] = field(default_factory=dict)
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in RUN_STATUSES:
            raise ValueError(f"Unknown run status '{self.status}'")

    @classmethod
    def new(cls, spec: RunSpec, run_id: Optional[str] = None) -> "RunManifest":
        return cls(run_id=run_id or uuid.uuid4().hex[:12], spec=spec)

    def record(self, result: StageResult) -> None:
        self.stages[self.stage_key(result.phase, result.chapter)] = result
        self.current_phase = result.phase
        self.current_chapter = result.chapter
        self.updated_at = _now()

    def get(self, phase: str, chapter: Optional[int] = None) -> Optional[StageResult]:
        return self.stages.get(self.stage_key(phase, chapter))

    @staticmethod
    def stage_key(phase: str, chapter: Optional[int] = None) -> str:
        return f"{phase}:{chapter}" if chapter is not None else phase

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "spec": self.spec.to_dict(),
            "status": self.status,
            "current_phase": self.current_phase,
            "current_chapter": self.current_chapter,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stages": {key: value.to_dict() for key, value in self.stages.items()},
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunManifest":
        return cls(
            run_id=data["run_id"],
            spec=RunSpec.from_dict(data["spec"]),
            status=data.get("status", "pending"),
            current_phase=data.get("current_phase", ""),
            current_chapter=data.get("current_chapter"),
            created_at=data.get("created_at", _now()),
            updated_at=data.get("updated_at", _now()),
            stages={
                key: StageResult.from_dict(value)
                for key, value in data.get("stages", {}).items()
            },
            error=data.get("error"),
        )


class ManifestStore:
    """Atomic JSON persistence for one run manifest."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def save(self, manifest: RunManifest) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f"{self.path.name}.tmp-", dir=str(self.path.parent), text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(manifest.to_dict(), handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def load(self) -> RunManifest:
        return RunManifest.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
