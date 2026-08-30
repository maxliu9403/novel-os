"""Domain contracts for story-derived novel cover candidate sets."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

try:
    from .image_binary import aspect_ratio_matches
except ImportError:  # pragma: no cover - legacy top-level core imports
    from image_binary import aspect_ratio_matches


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REAL_PLACE_MARKERS = {
    "new york", "los angeles", "london", "paris", "beijing", "shanghai",
    "tokyo", "singapore", "hong kong", "sydney", "toronto", "chicago",
}
_CANDIDATE_STATUSES = {"pending", "ready", "failed", "rejected", "selected"}
_SET_STATUSES = {"generating", "partial", "ready", "selected", "failed", "stale"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_text(data: Mapping[str, Any], field_name: str) -> str:
    value = str(data.get(field_name) or "").strip()
    if not value:
        raise ValueError(f"cover_handoff.{field_name} is required")
    return value


def _validate_sha(value: str, field_name: str, *, optional: bool = False) -> str:
    value = str(value or "")
    if optional and not value:
        return ""
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class CoverBrief:
    schema_version: int
    title: str
    author: str
    language: str
    genre: str
    target_audience: str
    market_scope: str
    core_task: str
    core_conflict: str
    emotional_promise: str
    protagonist: dict[str, str]
    relationship_or_power_contrast: str
    decisive_story_node: str
    secondary_task: dict[str, str]
    world_signals: tuple[str, ...]
    title_direction: dict[str, str]
    forbidden_elements: tuple[str, ...]
    source_prompt_sha256: str
    foundation_sha256: str = ""

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        source_prompt_sha256: str | None = None,
        foundation_sha256: str | None = None,
    ) -> "CoverBrief":
        protagonist = {
            str(key): str(value or "").strip()
            for key, value in dict(data.get("protagonist") or {}).items()
        }
        for field_name in ("role", "visual_identity", "agency_signal"):
            if not protagonist.get(field_name):
                raise ValueError(f"cover_handoff.protagonist.{field_name} is required")

        world_signals = tuple(
            str(value).strip() for value in data.get("world_signals") or () if str(value).strip()
        )
        for signal in world_signals:
            folded = signal.casefold()
            if any(place in folded for place in _REAL_PLACE_MARKERS):
                raise ValueError("cover_handoff world signals must use fictional places")

        source_sha = source_prompt_sha256
        if source_sha is None:
            source_sha = str(data.get("source_prompt_sha256") or "")
        foundation_sha = foundation_sha256
        if foundation_sha is None:
            foundation_sha = str(data.get("foundation_sha256") or "")

        return cls(
            schema_version=int(data.get("schema_version") or 1),
            title=_required_text(data, "title"),
            author=str(data.get("author") or "").strip(),
            language=_required_text(data, "language"),
            genre=_required_text(data, "genre"),
            target_audience=_required_text(data, "target_audience"),
            market_scope=_required_text(data, "market_scope"),
            core_task=_required_text(data, "core_task"),
            core_conflict=_required_text(data, "core_conflict"),
            emotional_promise=_required_text(data, "emotional_promise"),
            protagonist=protagonist,
            relationship_or_power_contrast=_required_text(
                data, "relationship_or_power_contrast"
            ),
            decisive_story_node=_required_text(data, "decisive_story_node"),
            secondary_task={
                str(key): str(value or "").strip()
                for key, value in dict(data.get("secondary_task") or {}).items()
            },
            world_signals=world_signals,
            title_direction={
                str(key): str(value or "").strip()
                for key, value in dict(data.get("title_direction") or {}).items()
            },
            forbidden_elements=tuple(
                str(value).strip()
                for value in data.get("forbidden_elements") or ()
                if str(value).strip()
            ),
            source_prompt_sha256=_validate_sha(source_sha, "source_prompt_sha256"),
            foundation_sha256=_validate_sha(
                foundation_sha, "foundation_sha256", optional=True
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["world_signals"] = list(self.world_signals)
        data["forbidden_elements"] = list(self.forbidden_elements)
        return data

    def validate_concepts(self, concepts: Sequence["CoverConcept"]) -> bool:
        if not 3 <= len(concepts) <= 5:
            raise ValueError("Cover concept count must be between 3 and 5")
        strategies = {concept.visual_strategy.casefold() for concept in concepts}
        if len(strategies) != len(concepts):
            raise ValueError("Cover concepts must use distinct visual strategies")
        for concept in concepts:
            if self.title not in concept.generation_prompt:
                raise ValueError("Every cover prompt must contain the exact title")
        return True


@dataclass(frozen=True)
class CoverConcept:
    concept_id: str
    visual_strategy: str
    focal_scene: str
    composition: str
    palette: str
    secondary_signal: str
    title_treatment: str
    generation_prompt: str

    def __post_init__(self) -> None:
        for name in (
            "concept_id", "visual_strategy", "focal_scene", "composition", "palette",
            "title_treatment", "generation_prompt",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"CoverConcept.{name} is required")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverConcept":
        return cls(**{field_name: str(data.get(field_name) or "") for field_name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class CoverCandidate:
    candidate_id: str
    concept_id: str
    status: str = "pending"
    relative_path: str = ""
    media_id: str = ""
    sha256: str = ""
    width: int = 0
    height: int = 0
    content_type: str = ""
    provider: str = ""
    model: str = ""
    request_id: str = ""
    generation_prompt: str = ""
    safe_request_parameters: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.status not in _CANDIDATE_STATUSES:
            raise ValueError(f"Unknown cover candidate status '{self.status}'")
        if self.status in {"ready", "selected", "rejected"}:
            required = (
                self.relative_path, self.media_id, self.sha256, self.content_type,
                self.provider, self.model, self.generation_prompt,
            )
            if not all(required) or not aspect_ratio_matches(self.width, self.height):
                raise ValueError("A ready candidate requires complete portrait 2:3 provenance")
            _validate_sha(self.sha256, "candidate.sha256")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverCandidate":
        values = {
            name: data.get(name, definition.default)
            for name, definition in cls.__dataclass_fields__.items()
            if name != "safe_request_parameters"
        }
        values["safe_request_parameters"] = dict(data.get("safe_request_parameters") or {})
        return cls(**values)


@dataclass(frozen=True)
class CoverSet:
    cover_set_id: str
    project_id: str
    brief: CoverBrief
    concepts: tuple[CoverConcept, ...]
    candidates: tuple[CoverCandidate, ...]
    source_prompt_sha256: str
    foundation_sha256: str = ""
    brief_schema_version: int = 1
    compiler_version: str = ""
    status: str = "generating"
    requested_count: int = 4
    selected_candidate_id: str = ""
    revision: int = 0
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.status not in _SET_STATUSES:
            raise ValueError(f"Unknown cover set status '{self.status}'")
        if not 3 <= self.requested_count <= 5:
            raise ValueError("Cover candidate count must be between 3 and 5")
        if self.brief_schema_version not in {1, 2}:
            raise ValueError("Cover brief schema version must be 1 or 2")
        if len(self.concepts) != self.requested_count or len(self.candidates) != self.requested_count:
            raise ValueError("Cover set concepts and candidates must match requested_count")

    @classmethod
    def new(
        cls,
        project_id: str,
        brief: CoverBrief,
        concepts: Sequence[CoverConcept],
        *,
        compiler_version: str = "",
    ) -> "CoverSet":
        brief.validate_concepts(concepts)
        set_id = f"cover-{uuid.uuid4().hex}"
        now = _now()
        candidates = tuple(
            CoverCandidate(
                candidate_id=f"candidate-{uuid.uuid4().hex}",
                concept_id=concept.concept_id,
                generation_prompt=concept.generation_prompt,
                created_at=now,
            )
            for concept in concepts
        )
        return cls(
            cover_set_id=set_id,
            project_id=project_id,
            brief=brief,
            concepts=tuple(concepts),
            candidates=candidates,
            source_prompt_sha256=brief.source_prompt_sha256,
            foundation_sha256=brief.foundation_sha256,
            brief_schema_version=int(getattr(brief, "schema_version", 1) or 1),
            compiler_version=compiler_version,
            requested_count=len(concepts),
            created_at=now,
            updated_at=now,
        )

    def with_source_hashes(self, source_prompt_sha256: str, foundation_sha256: str) -> "CoverSet":
        changed = (
            source_prompt_sha256 != self.source_prompt_sha256
            or foundation_sha256 != self.foundation_sha256
        )
        return replace(self, status="stale" if changed else self.status, updated_at=_now())

    def to_dict(self) -> dict[str, Any]:
        return {
            "cover_set_id": self.cover_set_id,
            "project_id": self.project_id,
            "brief": self.brief.to_dict(),
            "concepts": [concept.to_dict() for concept in self.concepts],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "source_prompt_sha256": self.source_prompt_sha256,
            "foundation_sha256": self.foundation_sha256,
            "brief_schema_version": self.brief_schema_version,
            "compiler_version": self.compiler_version,
            "status": self.status,
            "requested_count": self.requested_count,
            "selected_candidate_id": self.selected_candidate_id,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverSet":
        brief = CoverBrief.from_dict(dict(data.get("brief") or {}))
        return cls(
            cover_set_id=str(data.get("cover_set_id") or ""),
            project_id=str(data.get("project_id") or ""),
            brief=brief,
            concepts=tuple(CoverConcept.from_dict(item) for item in data.get("concepts") or ()),
            candidates=tuple(
                CoverCandidate.from_dict(item) for item in data.get("candidates") or ()
            ),
            source_prompt_sha256=str(data.get("source_prompt_sha256") or ""),
            foundation_sha256=str(data.get("foundation_sha256") or ""),
            brief_schema_version=int(data.get("brief_schema_version") or 1),
            compiler_version=str(data.get("compiler_version") or ""),
            status=str(data.get("status") or "generating"),
            requested_count=int(data.get("requested_count") or 0),
            selected_candidate_id=str(data.get("selected_candidate_id") or ""),
            revision=int(data.get("revision") or 0),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )
