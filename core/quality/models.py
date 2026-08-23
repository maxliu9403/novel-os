"""Immutable contracts for artifact-bound quality evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Dict, Optional, Tuple


SCHEMA_VERSION = 1
DEFAULT_RUBRIC_VERSION = "quality-rubric-v1"
SEVERITIES = frozenset({"critical", "major", "minor", "info"})
REPAIR_CLASSES = frozenset(
    {"chapter_structure", "continuity", "prose", "style"}
)
REPORT_STATUSES = frozenset({"pass", "needs_repair", "fail"})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FINDING_ID_RE = re.compile(r"^finding-[0-9a-f]{64}$")
_CLAIM_SIGNATURE_RE = re.compile(r"^claim-[0-9a-f]{64}$")
_EVALUATION_ID_RE = re.compile(r"^evaluation-[0-9a-f]{64}$")
_REPORT_ID_RE = re.compile(r"^report-[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _content_id(prefix: str, identity: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


def _validate_schema_version(value: Any) -> int:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be the integer {SCHEMA_VERSION}")
    return value


def _validate_sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be lowercase 64 hex")
    return value


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a nonblank string")
    return value.strip()


def _context_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value.strip()


def _validate_exact_fields(
    data: Mapping[str, Any], expected: set[str], model_name: str
) -> None:
    if set(data) != expected:
        raise ValueError(f"{model_name} has invalid fields")


@dataclass(frozen=True)
class EvidenceSpan:
    """A quote or character span bound to one exact UTF-8 artifact."""

    artifact_sha256: str
    quote: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_sha256(self.artifact_sha256, "artifact_sha256")

        if self.quote is not None and (
            not isinstance(self.quote, str) or not self.quote.strip()
        ):
            raise ValueError("quote must be a nonblank string when present")

        has_start = self.start is not None
        has_end = self.end is not None
        if has_start != has_end:
            raise ValueError("start and end must be provided together")
        if has_start:
            if type(self.start) is not int or type(self.end) is not int:
                raise ValueError("start and end must be integer character offsets")
            if self.start < 0 or self.end < 0 or self.start >= self.end:
                raise ValueError("start and end must satisfy 0 <= start < end")
        if self.quote is None and not has_start:
            raise ValueError("evidence requires a quote or character span")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_sha256": self.artifact_sha256,
            "quote": self.quote,
            "start": self.start,
            "end": self.end,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceSpan":
        if not isinstance(data, Mapping):
            raise ValueError("evidence span must be an object")
        expected = {"artifact_sha256", "quote", "start", "end", "schema_version"}
        _validate_exact_fields(data, expected, "evidence span")
        return cls(**dict(data))


def _claim_identity(finding: "QualityFinding") -> Dict[str, Any]:
    return {
        "artifact_sha256": finding.artifact_sha256,
        "category": finding.category,
        "severity": finding.severity,
        "message": finding.message,
        "evidence": [item.to_dict() for item in finding.evidence],
        "suggested_action": finding.suggested_action,
        "repair_class": finding.repair_class,
        "schema_version": finding.schema_version,
    }


def _finding_identity(finding: "QualityFinding") -> Dict[str, Any]:
    return {
        **_claim_identity(finding),
        "evidence_verification_result": finding.evidence_verification_result,
    }


@dataclass(frozen=True)
class QualityFinding:
    """An auditable quality claim whose blocking trust exists only in memory.

    ``evidence_verification_result`` records the last check for audit and stable
    serialization. Only ``with_verified_evidence`` can grant the separate live
    ``evidence_verified`` authority used by ``blocking``. Deserialization keeps
    the result but intentionally does not restore that authority.
    """

    artifact_sha256: str
    category: str
    severity: str
    message: str
    evidence: Tuple[EvidenceSpan, ...]
    suggested_action: str
    repair_class: str
    evidence_verified: bool = field(default=False, init=False)
    evidence_verification_result: bool = False
    schema_version: int = SCHEMA_VERSION
    finding_id: str = ""

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_sha256(self.artifact_sha256, "artifact_sha256")
        object.__setattr__(
            self, "category", _required_string(self.category, "category")
        )
        object.__setattr__(self, "message", _required_string(self.message, "message"))
        object.__setattr__(
            self,
            "suggested_action",
            _required_string(self.suggested_action, "suggested_action"),
        )

        severity = _required_string(self.severity, "severity")
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {sorted(SEVERITIES)}")
        object.__setattr__(self, "severity", severity)

        repair_class = _required_string(self.repair_class, "repair_class")
        if repair_class not in REPAIR_CLASSES:
            raise ValueError(
                f"repair_class must be one of {sorted(REPAIR_CLASSES)}"
            )
        object.__setattr__(self, "repair_class", repair_class)

        if not isinstance(self.evidence, Sequence) or isinstance(
            self.evidence, (str, bytes)
        ):
            raise ValueError("evidence must be a sequence of EvidenceSpan values")
        evidence = tuple(self.evidence)
        for item in evidence:
            if not isinstance(item, EvidenceSpan):
                raise ValueError("evidence must contain only EvidenceSpan values")
            if item.artifact_sha256 != self.artifact_sha256:
                raise ValueError("all evidence must match the finding artifact")
        object.__setattr__(self, "evidence", evidence)

        if type(self.evidence_verification_result) is not bool:
            raise ValueError("evidence_verification_result must be a boolean")

        expected_id = _content_id("finding", _finding_identity(self))
        if not isinstance(self.finding_id, str):
            raise ValueError("finding_id must be a string")
        if self.finding_id and (
            not _FINDING_ID_RE.fullmatch(self.finding_id)
            or self.finding_id != expected_id
        ):
            raise ValueError("finding_id does not match finding identity")
        object.__setattr__(self, "finding_id", expected_id)

    @classmethod
    def create(
        cls,
        *,
        artifact_sha256: str,
        category: str,
        severity: str,
        message: str,
        evidence: Tuple[EvidenceSpan, ...],
        suggested_action: str,
        repair_class: str,
        schema_version: int = SCHEMA_VERSION,
    ) -> "QualityFinding":
        return cls(
            artifact_sha256=artifact_sha256,
            category=category,
            severity=severity,
            message=message,
            evidence=evidence,
            suggested_action=suggested_action,
            repair_class=repair_class,
            schema_version=schema_version,
        )

    @property
    def blocking(self) -> bool:
        return self.evidence_verified and self.severity in {"critical", "major"}

    @property
    def claim_signature(self) -> str:
        """Return the stable claim dedupe key, independent of verification state."""
        signature = _content_id("claim", _claim_identity(self))
        if not _CLAIM_SIGNATURE_RE.fullmatch(signature):
            raise ValueError("claim_signature has invalid format")
        return signature

    def with_verified_evidence(
        self, text: str, actual_artifact_sha: str
    ) -> "QualityFinding":
        from .evidence import verify_evidence

        verified = bool(self.evidence) and all(
            verify_evidence(text, actual_artifact_sha, item) for item in self.evidence
        )
        checked = QualityFinding(
            artifact_sha256=self.artifact_sha256,
            category=self.category,
            severity=self.severity,
            message=self.message,
            evidence=self.evidence,
            suggested_action=self.suggested_action,
            repair_class=self.repair_class,
            evidence_verification_result=verified,
            schema_version=self.schema_version,
        )
        if verified:
            object.__setattr__(checked, "evidence_verified", True)
        return checked

    def to_dict(self) -> Dict[str, Any]:
        return {"finding_id": self.finding_id, **_finding_identity(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QualityFinding":
        if not isinstance(data, Mapping):
            raise ValueError("quality finding must be an object")
        expected = {
            "finding_id",
            "artifact_sha256",
            "category",
            "severity",
            "message",
            "evidence",
            "suggested_action",
            "repair_class",
            "evidence_verification_result",
            "schema_version",
        }
        _validate_exact_fields(data, expected, "quality finding")
        raw_evidence = data["evidence"]
        if not isinstance(raw_evidence, list):
            raise ValueError("evidence must be a JSON array")
        values = dict(data)
        values["evidence"] = tuple(
            EvidenceSpan.from_dict(item) for item in raw_evidence
        )
        return cls(**values)


def _request_identity(request: "EvaluationRequest") -> Dict[str, Any]:
    return {
        "chapter": request.chapter,
        "artifact_sha256": request.artifact_sha256,
        "artifact_revision_id": request.artifact_revision_id,
        "story_contract_id": request.story_contract_id,
        "chapter_contract_id": request.chapter_contract_id,
        "rubric_version": request.rubric_version,
        "schema_version": request.schema_version,
    }


@dataclass(frozen=True)
class EvaluationRequest:
    """A text-free evaluation request bound to one artifact digest."""

    chapter: int
    artifact_sha256: str
    artifact_revision_id: str = ""
    story_contract_id: str = ""
    chapter_contract_id: str = ""
    rubric_version: str = DEFAULT_RUBRIC_VERSION
    schema_version: int = SCHEMA_VERSION
    evaluation_id: str = ""

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        if type(self.chapter) is not int or self.chapter < 1:
            raise ValueError("chapter must be a positive integer")
        _validate_sha256(self.artifact_sha256, "artifact_sha256")
        object.__setattr__(
            self,
            "artifact_revision_id",
            _context_string(self.artifact_revision_id, "artifact_revision_id"),
        )
        object.__setattr__(
            self,
            "story_contract_id",
            _context_string(self.story_contract_id, "story_contract_id"),
        )
        object.__setattr__(
            self,
            "chapter_contract_id",
            _context_string(self.chapter_contract_id, "chapter_contract_id"),
        )
        object.__setattr__(
            self,
            "rubric_version",
            _required_string(self.rubric_version, "rubric_version"),
        )

        expected_id = _content_id("evaluation", _request_identity(self))
        if not isinstance(self.evaluation_id, str):
            raise ValueError("evaluation_id must be a string")
        if self.evaluation_id and (
            not _EVALUATION_ID_RE.fullmatch(self.evaluation_id)
            or self.evaluation_id != expected_id
        ):
            raise ValueError("evaluation_id does not match request identity")
        object.__setattr__(self, "evaluation_id", expected_id)

    @classmethod
    def for_text(
        cls,
        chapter: int,
        text: str,
        *,
        artifact_sha256: Optional[str] = None,
        artifact_revision_id: str = "",
        story_contract_id: str = "",
        chapter_contract_id: str = "",
        rubric_version: str = DEFAULT_RUBRIC_VERSION,
        schema_version: int = SCHEMA_VERSION,
    ) -> "EvaluationRequest":
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        computed_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if artifact_sha256 is not None:
            _validate_sha256(artifact_sha256, "artifact_sha256")
            if artifact_sha256 != computed_sha:
                raise ValueError("artifact_sha256 does not match the exact UTF-8 text")
        return cls(
            chapter=chapter,
            artifact_sha256=computed_sha,
            artifact_revision_id=artifact_revision_id,
            story_contract_id=story_contract_id,
            chapter_contract_id=chapter_contract_id,
            rubric_version=rubric_version,
            schema_version=schema_version,
        )

    @property
    def request_id(self) -> str:
        return self.evaluation_id

    def verify_text(self, text: str) -> bool:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        return hashlib.sha256(text.encode("utf-8")).hexdigest() == self.artifact_sha256

    def to_dict(self) -> Dict[str, Any]:
        return {"evaluation_id": self.evaluation_id, **_request_identity(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvaluationRequest":
        if not isinstance(data, Mapping):
            raise ValueError("evaluation request must be an object")
        allowed = {
            "evaluation_id",
            "chapter",
            "artifact_sha256",
            "artifact_revision_id",
            "story_contract_id",
            "chapter_contract_id",
            "rubric_version",
            "schema_version",
        }
        if not set(data).issubset(allowed) or not {
            "evaluation_id",
            "chapter",
            "artifact_sha256",
        }.issubset(data):
            raise ValueError("evaluation request has invalid fields")
        values = {
            "artifact_revision_id": "",
            "story_contract_id": "",
            "chapter_contract_id": "",
            "rubric_version": DEFAULT_RUBRIC_VERSION,
            "schema_version": SCHEMA_VERSION,
            **dict(data),
        }
        return cls(**values)


def _freeze_bool_map(value: Any) -> Mapping[str, bool]:
    if not isinstance(value, Mapping):
        raise ValueError("hard_gates must be an object")
    normalized: Dict[str, bool] = {}
    for raw_key, item in value.items():
        key = _required_string(raw_key, "hard_gates key")
        if key in normalized:
            raise ValueError("hard_gates keys must be unique after normalization")
        if type(item) is not bool:
            raise ValueError("hard_gates values must be booleans")
        normalized[key] = item
    return MappingProxyType(normalized)


def _freeze_dimension_map(value: Any) -> Mapping[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError("semantic_dimensions must be an object")
    normalized: Dict[str, float] = {}
    for raw_key, item in value.items():
        key = _required_string(raw_key, "semantic_dimensions key")
        if key in normalized:
            raise ValueError(
                "semantic_dimensions keys must be unique after normalization"
            )
        if type(item) not in (int, float):
            raise ValueError("semantic_dimensions values must be numeric")
        numeric = float(item)
        if not math.isfinite(numeric) or not 0 <= numeric <= 10:
            raise ValueError("semantic_dimensions values must be between 0 and 10")
        normalized[key] = numeric
    return MappingProxyType(normalized)


def _report_identity(report: "EvaluationReport") -> Dict[str, Any]:
    return {
        "request": report.request.to_dict(),
        "artifact_sha256": report.artifact_sha256,
        "evaluation_id": report.evaluation_id,
        "rubric_version": report.rubric_version,
        "prompt_sha256": report.prompt_sha256,
        "evaluator_provider": report.evaluator_provider,
        "evaluator_model": report.evaluator_model,
        "hard_gates": dict(report.hard_gates),
        "semantic_dimensions": dict(report.semantic_dimensions),
        "findings": [finding.to_dict() for finding in report.findings],
        "status": report.status,
        "created_at": report.created_at,
        "schema_version": report.schema_version,
    }


@dataclass(frozen=True)
class EvaluationReport:
    """A versioned evaluator result bound to its request and artifact."""

    request: EvaluationRequest
    artifact_sha256: str
    evaluation_id: str
    rubric_version: str
    prompt_sha256: str
    evaluator_provider: str
    evaluator_model: str
    hard_gates: Mapping[str, bool]
    semantic_dimensions: Mapping[str, float]
    findings: Tuple[QualityFinding, ...]
    status: str
    created_at: str
    schema_version: int = SCHEMA_VERSION
    report_id: str = ""

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        if not isinstance(self.request, EvaluationRequest):
            raise ValueError("request must be an EvaluationRequest")
        _validate_sha256(self.artifact_sha256, "artifact_sha256")
        if self.artifact_sha256 != self.request.artifact_sha256:
            raise ValueError("artifact_sha256 must match the bound request")
        object.__setattr__(
            self, "evaluation_id", _required_string(self.evaluation_id, "evaluation_id")
        )
        if self.evaluation_id != self.request.evaluation_id:
            raise ValueError("evaluation_id must match the bound request")
        object.__setattr__(
            self,
            "rubric_version",
            _required_string(self.rubric_version, "rubric_version"),
        )
        if self.rubric_version != self.request.rubric_version:
            raise ValueError("rubric_version must match the bound request")
        _validate_sha256(self.prompt_sha256, "prompt_sha256")
        object.__setattr__(
            self,
            "evaluator_provider",
            _required_string(self.evaluator_provider, "evaluator_provider"),
        )
        object.__setattr__(
            self,
            "evaluator_model",
            _required_string(self.evaluator_model, "evaluator_model"),
        )

        object.__setattr__(self, "hard_gates", _freeze_bool_map(self.hard_gates))
        object.__setattr__(
            self,
            "semantic_dimensions",
            _freeze_dimension_map(self.semantic_dimensions),
        )

        if not isinstance(self.findings, Sequence) or isinstance(
            self.findings, (str, bytes)
        ):
            raise ValueError("findings must be a sequence of QualityFinding values")
        findings = tuple(self.findings)
        for finding in findings:
            if not isinstance(finding, QualityFinding):
                raise ValueError("findings must contain only QualityFinding values")
            if finding.artifact_sha256 != self.artifact_sha256:
                raise ValueError("all findings must match the report artifact")
        object.__setattr__(self, "findings", findings)

        status = _required_string(self.status, "status")
        if status not in REPORT_STATUSES:
            raise ValueError(f"status must be one of {sorted(REPORT_STATUSES)}")
        object.__setattr__(self, "status", status)

        created_at = _required_string(self.created_at, "created_at")
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("created_at must be a UTC ISO-8601 string") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError("created_at must be a UTC ISO-8601 string")
        object.__setattr__(self, "created_at", created_at)

        expected_id = _content_id("report", _report_identity(self))
        if not isinstance(self.report_id, str):
            raise ValueError("report_id must be a string")
        if self.report_id and (
            not _REPORT_ID_RE.fullmatch(self.report_id) or self.report_id != expected_id
        ):
            raise ValueError("report_id does not match report identity")
        object.__setattr__(self, "report_id", expected_id)

    def to_dict(self) -> Dict[str, Any]:
        return {"report_id": self.report_id, **_report_identity(self)}

    @property
    def request_id(self) -> str:
        return self.request.evaluation_id

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvaluationReport":
        if not isinstance(data, Mapping):
            raise ValueError("evaluation report must be an object")
        expected = {
            "report_id",
            "request",
            "artifact_sha256",
            "evaluation_id",
            "rubric_version",
            "prompt_sha256",
            "evaluator_provider",
            "evaluator_model",
            "hard_gates",
            "semantic_dimensions",
            "findings",
            "status",
            "created_at",
            "schema_version",
        }
        _validate_exact_fields(data, expected, "evaluation report")
        request = EvaluationRequest.from_dict(data["request"])
        raw_findings = data["findings"]
        if not isinstance(raw_findings, list):
            raise ValueError("findings must be a JSON array")
        values = dict(data)
        values["request"] = request
        values["findings"] = tuple(
            QualityFinding.from_dict(item) for item in raw_findings
        )
        return cls(**values)


__all__ = [
    "DEFAULT_RUBRIC_VERSION",
    "EvidenceSpan",
    "EvaluationReport",
    "EvaluationRequest",
    "QualityFinding",
    "REPAIR_CLASSES",
    "REPORT_STATUSES",
    "SCHEMA_VERSION",
    "SEVERITIES",
]
