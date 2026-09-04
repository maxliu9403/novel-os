"""Deterministic chapter design and commercial quality contracts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from commercial_story import (
    BELONGING_ANCHORS,
    CommercialStoryContract,
    READER_JOBS,
    RESOURCE_DIMENSIONS,
    SATISFACTION_TYPES,
)
from contracts import ChapterContract
from artifacts import ArtifactStore
from quality import EvidenceSpan, QualityFinding


_COMMERCIAL_REPORT_STATUSES = frozenset({"pass", "warning", "blocked"})
_DELIVERY_STATUSES = frozenset({"present", "missing", "not_applicable"})
_GUARDIAN_FIELDS = frozenset(
    {
        "agency",
        "resource_change",
        "local_payoff",
        "ending_hook",
        "reader_jobs",
        "belonging_anchors",
        "free_trial_beats",
        "child_voice",
        "institutional_plausibility",
        "findings",
    }
)
_DELIVERY_FIELDS = frozenset({"status", "quote", "start", "end"})
_FREE_TRIAL_BEATS = (
    "recognition_event",
    "pattern_proof",
    "first_boundary_test",
    "local_payoff",
    "irreversible_choice",
    "visible_cost",
    "next_concrete_expectation",
)
_REPORT_BINDING_FIELDS = frozenset(
    {
        "chapter",
        "final_revision_id",
        "final_sha256",
        "chapter_contract_revision_id",
        "commercial_report_id",
        "promotion_receipt_id",
    }
)


@dataclass(frozen=True)
class CommercialFinding:
    code: str
    severity: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("commercial finding code must be nonblank")
        if self.severity not in {"blocked", "warning"}:
            raise ValueError("commercial finding severity must be blocked or warning")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("commercial finding message must be nonblank")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclass(frozen=True)
class ChapterDesignReport:
    status: str
    chapter: int
    story_contract_id: str
    chapter_contract_id: str
    prior_contract_ids: tuple[str, ...]
    findings: tuple[CommercialFinding, ...]
    schema_version: int = 1

    @property
    def blockers(self) -> tuple[CommercialFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "blocked")

    @property
    def warnings(self) -> tuple[CommercialFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "warning")

    @property
    def report_id(self) -> str:
        encoded = json.dumps(
            self._payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "commercial-chapter-design:" + hashlib.sha256(encoded).hexdigest()

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "chapter": self.chapter,
            "story_contract_id": self.story_contract_id,
            "chapter_contract_id": self.chapter_contract_id,
            "prior_contract_ids": list(self.prior_contract_ids),
            "findings": [item.to_dict() for item in self.findings],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "report_id": self.report_id}


@dataclass(frozen=True)
class CommercialChapterReport:
    """Candidate-bound Guardian review for commercial reader-value delivery."""

    status: str
    chapter: int
    artifact_sha256: str
    story_contract_revision_id: str
    chapter_contract_revision_id: str
    findings: tuple[CommercialFinding, ...]
    quality_findings: tuple[QualityFinding, ...] = ()
    reader_value_update: Mapping[str, Any] | None = None
    # The labels are kept in the immutable report rather than mutable canon.
    # They are populated from the Guardian's free-window extraction and are
    # intentionally not copied into the nine-field reader-value update.
    free_trial_beats: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.status not in _COMMERCIAL_REPORT_STATUSES:
            raise ValueError("commercial chapter report has invalid status")
        if not isinstance(self.chapter, int) or self.chapter < 1:
            raise ValueError("commercial chapter report chapter must be positive")
        if not isinstance(self.artifact_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.artifact_sha256
        ):
            raise ValueError("commercial chapter report artifact sha is invalid")
        if any(not isinstance(item, CommercialFinding) for item in self.findings):
            raise ValueError("commercial chapter report findings are invalid")
        if any(not isinstance(item, QualityFinding) for item in self.quality_findings):
            raise ValueError("commercial chapter report quality findings are invalid")
        if not isinstance(self.free_trial_beats, Sequence) or isinstance(
            self.free_trial_beats, (str, bytes)
        ):
            raise ValueError("commercial chapter report free-trial beats are invalid")
        beats = tuple(str(item) for item in self.free_trial_beats)
        if len(set(beats)) != len(beats) or any(item not in _FREE_TRIAL_BEATS for item in beats):
            raise ValueError("commercial chapter report free-trial beats are invalid")
        object.__setattr__(self, "free_trial_beats", beats)
        if self.reader_value_update is not None:
            update = dict(self.reader_value_update)
            if update.get("candidate_sha256") != self.artifact_sha256:
                raise ValueError("reader-value update is not bound to report artifact")
            object.__setattr__(self, "reader_value_update", update)

    @property
    def blockers(self) -> tuple[CommercialFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "blocked")

    @property
    def warnings(self) -> tuple[CommercialFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "warning")

    @property
    def report_id(self) -> str:
        # State parser binds reader-value records to this stable candidate id.
        return "commercial-chapter-report:" + self.artifact_sha256

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "chapter": self.chapter,
            "artifact_sha256": self.artifact_sha256,
            "story_contract_revision_id": self.story_contract_revision_id,
            "chapter_contract_revision_id": self.chapter_contract_revision_id,
            "findings": [item.to_dict() for item in self.findings],
            "quality_findings": [item.to_dict() for item in self.quality_findings],
            "reader_value_update": dict(self.reader_value_update)
            if self.reader_value_update is not None
            else None,
            "free_trial_beats": list(self.free_trial_beats),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "report_id": self.report_id}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommercialChapterReport":
        if not isinstance(data, Mapping):
            raise ValueError("commercial chapter report must be an object")
        expected = {
            "report_id",
            "schema_version",
            "status",
            "chapter",
            "artifact_sha256",
            "story_contract_revision_id",
            "chapter_contract_revision_id",
            "findings",
            "quality_findings",
            "reader_value_update",
        }
        extended = expected | {"free_trial_beats"}
        if set(data) != expected and set(data) != extended:
            raise ValueError("commercial chapter report has invalid fields")
        if not isinstance(data["findings"], list) or not isinstance(
            data["quality_findings"], list
        ):
            raise ValueError("commercial chapter report findings must be arrays")
        if "free_trial_beats" in data and not isinstance(data["free_trial_beats"], list):
            raise ValueError("commercial chapter report free-trial beats must be an array")
        try:
            findings = tuple(
                CommercialFinding(
                    code=item["code"], severity=item["severity"], message=item["message"]
                )
                for item in data["findings"]
            )
            quality_findings = tuple(
                QualityFinding.from_dict(item) for item in data["quality_findings"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("commercial chapter report contains invalid findings") from exc
        report = cls(
            schema_version=data["schema_version"],
            status=data["status"],
            chapter=data["chapter"],
            artifact_sha256=data["artifact_sha256"],
            story_contract_revision_id=data["story_contract_revision_id"],
            chapter_contract_revision_id=data["chapter_contract_revision_id"],
            findings=findings,
            quality_findings=quality_findings,
            reader_value_update=data["reader_value_update"],
            free_trial_beats=tuple(data.get("free_trial_beats") or ()),
        )
        if data["report_id"] != report.report_id:
            raise ValueError("commercial chapter report id does not match artifact")
        return report


def _finding(code: str, message: str, severity: str = "blocked") -> CommercialFinding:
    return CommercialFinding(code=code, severity=severity, message=message)


def _resolve_evidence_span(
    candidate_text: str,
    artifact_sha256: str,
    *,
    quote: Any,
    start: Any,
    end: Any,
) -> EvidenceSpan | None:
    """Verify a quote and deterministically repair unreliable model offsets."""
    if (
        not isinstance(quote, str)
        or not quote.strip()
        or type(start) is not int
        or type(end) is not int
        or start < 0
        or end <= start
    ):
        return None
    if end <= len(candidate_text) and candidate_text[start:end] == quote:
        resolved_start = start
    else:
        resolved_start = candidate_text.find(quote)
        if resolved_start < 0:
            return None
        if candidate_text.find(quote, resolved_start + 1) >= 0:
            return None
    return EvidenceSpan(
        artifact_sha256=artifact_sha256,
        quote=quote,
        start=resolved_start,
        end=resolved_start + len(quote),
    )


def _verify_delivery(
    candidate_text: str, artifact_sha256: str, value: Any, label: str, *, required: bool = True
) -> tuple[list[CommercialFinding], EvidenceSpan | None]:
    findings: list[CommercialFinding] = []
    if not isinstance(value, Mapping) or set(value) != _DELIVERY_FIELDS:
        findings.append(_finding(f"invalid_{label}_evidence", f"{label} delivery has invalid fields"))
        return findings, None
    status = value.get("status")
    if status not in _DELIVERY_STATUSES:
        findings.append(_finding(f"invalid_{label}_status", f"{label} delivery has invalid status"))
        return findings, None
    if status == "not_applicable":
        if required:
            findings.append(_finding(f"{label}_not_applicable", f"{label} is required for this chapter"))
        return findings, None
    if status == "missing":
        findings.append(
            _finding(
                f"{label}_missing",
                f"{label} was not delivered",
                "blocked" if required else "warning",
            )
        )
        return findings, None
    evidence = _resolve_evidence_span(
        candidate_text,
        artifact_sha256,
        quote=value.get("quote"),
        start=value.get("start"),
        end=value.get("end"),
    )
    if evidence is None:
        findings.append(_finding(f"invalid_{label}_evidence", f"{label} evidence does not match the exact candidate"))
        return findings, None
    return findings, evidence


def _strict_guardian_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _GUARDIAN_FIELDS:
        raise ValueError("commercial Guardian payload has invalid fields")
    if not isinstance(payload["findings"], list):
        raise ValueError("commercial Guardian findings must be a list")
    if not isinstance(payload["free_trial_beats"], (list, Mapping)):
        raise ValueError("commercial Guardian free_trial_beats must be an object or array")
    return dict(payload)


def _mapping_deliveries(value: Any, expected: Sequence[str], label: str) -> tuple[dict[str, Any], list[CommercialFinding]]:
    if not isinstance(value, Mapping):
        return {}, [_finding(f"invalid_{label}", f"{label} deliveries must be an object")]
    expected_set = set(expected)
    actual_set = set(value)
    findings: list[CommercialFinding] = []
    for key in sorted(expected_set - actual_set):
        findings.append(_finding(f"{label[:-1] if label.endswith('s') else label}_missing", f"{label[:-1] if label.endswith('s') else label} {key} was not delivered"))
    for key in sorted(actual_set - expected_set):
        findings.append(_finding(f"unexpected_{label[:-1] if label.endswith('s') else label}", f"Unexpected {label[:-1] if label.endswith('s') else label} {key}"))
    return {str(key): value[key] for key in expected_set if key in value}, findings


def _guardian_quality_findings(
    candidate_text: str, artifact_sha256: str, raw_findings: Sequence[Any]
) -> tuple[list[CommercialFinding], list[QualityFinding]]:
    commercial: list[CommercialFinding] = []
    quality: list[QualityFinding] = []
    for index, raw in enumerate(raw_findings):
        if not isinstance(raw, Mapping):
            commercial.append(_finding("invalid_finding", f"Guardian finding {index + 1} is not an object", "warning"))
            continue
        category = raw.get("category", raw.get("code"))
        severity = raw.get("severity")
        message = raw.get("message")
        evidence = raw.get("evidence")
        if not isinstance(category, str) or not category.strip() or severity not in {"critical", "major", "minor", "info", "warning"} or not isinstance(message, str) or not message.strip():
            commercial.append(_finding("invalid_finding", f"Guardian finding {index + 1} is malformed", "warning"))
            continue
        candidates = evidence if isinstance(evidence, list) else [evidence]
        spans: list[EvidenceSpan] = []
        valid = True
        for item in candidates:
            if not isinstance(item, Mapping):
                valid = False
                break
            try:
                evidence_span = _resolve_evidence_span(
                    candidate_text,
                    artifact_sha256,
                    quote=item["quote"],
                    start=item["start"],
                    end=item["end"],
                )
                if evidence_span is None:
                    valid = False
                    break
                spans.append(evidence_span)
            except (KeyError, TypeError, ValueError):
                valid = False
                break
        if not valid or not spans:
            # An unsupported evaluator claim is advisory and never blocking.
            commercial.append(_finding(str(category), str(message), "warning"))
            continue
        mapped_severity = "major" if severity == "warning" else str(severity)
        finding = QualityFinding.create(
            artifact_sha256=artifact_sha256,
            category=str(category),
            severity=mapped_severity,
            message=str(message),
            evidence=tuple(spans),
            suggested_action=str(raw.get("suggested_action") or "Review the cited candidate passage."),
            repair_class="chapter_structure",
        ).with_verified_evidence(candidate_text, artifact_sha256)
        quality.append(finding)
        if finding.blocking:
            commercial.append(_finding(str(category), str(message), "blocked"))
        elif mapped_severity in {"major", "minor"}:
            commercial.append(_finding(str(category), str(message), "warning"))
    return commercial, quality


def _verify_free_trial_beats(
    candidate_text: str,
    artifact_sha256: str,
    value: Any,
    expected: Sequence[str],
) -> tuple[tuple[str, ...], list[CommercialFinding]]:
    expected_tuple = tuple(expected)
    if any(item not in _FREE_TRIAL_BEATS for item in expected_tuple) or len(
        set(expected_tuple)
    ) != len(expected_tuple):
        raise ValueError("expected free-trial beats are invalid")
    if not expected_tuple:
        if value not in ([], {}):
            return (), [
                _finding(
                    "unexpected_free_trial_beat",
                    "This chapter has no assigned free-trial beat",
                )
            ]
        return (), []
    if not isinstance(value, Mapping):
        return (), [
            _finding(
                "invalid_free_trial_beats",
                "Assigned free-trial beats require exact evidence deliveries",
            )
        ]
    actual = set(value)
    expected_set = set(expected_tuple)
    findings: list[CommercialFinding] = []
    for beat in sorted(expected_set - actual):
        findings.append(
            _finding(
                f"free_trial_{beat}_missing",
                f"Assigned free-trial beat {beat} was not delivered",
            )
        )
    for beat in sorted(actual - expected_set):
        findings.append(
            _finding(
                "unexpected_free_trial_beat",
                f"Free-trial beat {beat} belongs to a different chapter",
            )
        )
    delivered: list[str] = []
    for beat in expected_tuple:
        if beat not in value:
            continue
        current, evidence = _verify_delivery(
            candidate_text,
            artifact_sha256,
            value[beat],
            f"free_trial_{beat}",
        )
        findings.extend(current)
        if evidence is not None:
            delivered.append(beat)
    return tuple(delivered), findings


def review_commercial_chapter(
    candidate_text: str,
    chapter_contract: ChapterContract,
    guardian_payload: Mapping[str, Any],
    recent_updates: Sequence[Mapping[str, Any]],
    *,
    story_contract_revision_id: str = "",
    chapter_contract_revision_id: str = "",
    expected_free_trial_beats: Sequence[str] = (),
) -> CommercialChapterReport:
    """Validate Guardian's exact delivery claims against one candidate artifact."""
    if not isinstance(candidate_text, str) or not candidate_text.strip():
        raise ValueError("candidate_text must be nonblank")
    if not isinstance(chapter_contract, ChapterContract) or chapter_contract.schema_version != 2:
        raise ValueError("commercial review requires a schema-v2 chapter contract")
    payload = _strict_guardian_payload(guardian_payload)
    artifact_sha256 = hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()
    findings: list[CommercialFinding] = []
    free_trial_beats, beat_findings = _verify_free_trial_beats(
        candidate_text,
        artifact_sha256,
        payload["free_trial_beats"],
        expected_free_trial_beats,
    )
    findings.extend(beat_findings)

    for name in ("agency", "resource_change", "local_payoff", "ending_hook"):
        delivery_label = "hook" if name == "ending_hook" else name
        current, evidence = _verify_delivery(
            candidate_text, artifact_sha256, payload[name], delivery_label
        )
        findings.extend(current)
        del evidence

    job_deliveries, current = _mapping_deliveries(payload["reader_jobs"], chapter_contract.reader_jobs, "reader_jobs")
    findings.extend(current)
    for job, value in job_deliveries.items():
        current, evidence = _verify_delivery(candidate_text, artifact_sha256, value, f"reader_job_{job}")
        findings.extend(current)
        del evidence

    anchor_deliveries, current = _mapping_deliveries(payload["belonging_anchors"], chapter_contract.belonging_anchors, "belonging_anchors")
    findings.extend(current)
    for anchor, value in anchor_deliveries.items():
        current, evidence = _verify_delivery(candidate_text, artifact_sha256, value, f"belonging_anchor_{anchor}")
        findings.extend(current)
        del evidence

    for name in ("child_voice", "institutional_plausibility"):
        current, evidence = _verify_delivery(candidate_text, artifact_sha256, payload[name], name, required=False)
        findings.extend(current)
        del evidence

    quality_commercial, quality_findings = _guardian_quality_findings(
        candidate_text, artifact_sha256, payload["findings"]
    )
    findings.extend(quality_commercial)
    # ``recent_updates`` is intentionally data-only input; it is used by the
    # caller to construct the prompt and never replaces exact candidate checks.
    del recent_updates

    status = "blocked" if any(item.severity == "blocked" for item in findings) else "warning" if findings else "pass"
    report_id = "commercial-chapter-report:" + artifact_sha256
    reader_value_update = {
        "report_id": report_id,
        "candidate_sha256": artifact_sha256,
        "reader_jobs": list(chapter_contract.reader_jobs),
        "belonging_anchors": list(chapter_contract.belonging_anchors),
        "resource_dimension": chapter_contract.resource_dimension,
        "resource_change": chapter_contract.resource_change,
        "satisfaction_type": chapter_contract.satisfaction_type,
        "hook_type": chapter_contract.hook_type,
        "protagonist_caused_turn": chapter_contract.protagonist_causes_turn,
    } if status != "blocked" else None
    return CommercialChapterReport(
        status=status,
        chapter=chapter_contract.chapter,
        artifact_sha256=artifact_sha256,
        story_contract_revision_id=story_contract_revision_id,
        chapter_contract_revision_id=chapter_contract_revision_id or chapter_contract.contract_id,
        findings=tuple(findings),
        quality_findings=tuple(quality_findings),
        reader_value_update=reader_value_update,
        free_trial_beats=free_trial_beats,
    )


def write_commercial_report(project: Path, report: CommercialChapterReport) -> Path:
    path = project / "outputs/quality/commercial" / f"chapter_{report.chapter:03d}_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f"{path.name}.tmp-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def _report_identity(payload: Mapping[str, Any], prefix: str) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(encoded).hexdigest()}"


def _normalize_bindings(value: Sequence[Mapping[str, Any]]) -> tuple[dict[str, str], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("commercial review artifact bindings must be an array")
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _REPORT_BINDING_FIELDS:
            raise ValueError("commercial review artifact binding has invalid fields")
        if any(not isinstance(item[key], str) for key in _REPORT_BINDING_FIELDS):
            raise ValueError("commercial review artifact binding values must be strings")
        normalized.append({key: item[key].strip() for key in sorted(_REPORT_BINDING_FIELDS)})
    return tuple(normalized)


def _strict_string_array(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{name} must be an array of nonblank strings")
    return tuple(value)


@dataclass(frozen=True)
class CommercialFreeTrialReport:
    """Deterministic review of the configured three/four-chapter free window."""

    status: str
    chapter_count: int
    story_contract_revision_id: str = ""
    chapter_contract_revision_ids: tuple[str, ...] = ()
    final_artifact_revision_ids: tuple[str, ...] = ()
    commercial_report_ids: tuple[str, ...] = ()
    promotion_receipt_ids: tuple[str, ...] = ()
    findings: tuple[CommercialFinding, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)
    artifact_bindings: tuple[Mapping[str, str], ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.status not in _COMMERCIAL_REPORT_STATUSES:
            raise ValueError("commercial free-trial report has invalid status")
        if type(self.chapter_count) is not int or self.chapter_count not in {3, 4}:
            raise ValueError("commercial free-trial report chapter_count must be 3 or 4")
        if any(not isinstance(item, CommercialFinding) for item in self.findings):
            raise ValueError("commercial free-trial report findings are invalid")
        if not isinstance(self.metrics, Mapping):
            raise ValueError("commercial free-trial report metrics are invalid")
        object.__setattr__(self, "metrics", dict(self.metrics))
        object.__setattr__(self, "artifact_bindings", _normalize_bindings(self.artifact_bindings))

    @property
    def blockers(self) -> tuple[CommercialFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "blocked")

    @property
    def warnings(self) -> tuple[CommercialFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "warning")

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "chapter_count": self.chapter_count,
            "story_contract_revision_id": self.story_contract_revision_id,
            "chapter_contract_revision_ids": list(self.chapter_contract_revision_ids),
            "final_artifact_revision_ids": list(self.final_artifact_revision_ids),
            "commercial_report_ids": list(self.commercial_report_ids),
            "promotion_receipt_ids": list(self.promotion_receipt_ids),
            "findings": [item.to_dict() for item in self.findings],
            "metrics": dict(self.metrics),
            "artifact_bindings": [dict(item) for item in self.artifact_bindings],
        }

    @property
    def report_id(self) -> str:
        return _report_identity(self._payload(), "commercial-free-trial-report")

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "report_id": self.report_id}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommercialFreeTrialReport":
        if not isinstance(data, Mapping):
            raise ValueError("commercial free-trial report must be an object")
        expected = {
            "report_id", "schema_version", "status", "chapter_count",
            "story_contract_revision_id", "chapter_contract_revision_ids",
            "final_artifact_revision_ids", "commercial_report_ids",
            "promotion_receipt_ids", "findings", "metrics", "artifact_bindings",
        }
        if set(data) != expected:
            raise ValueError("commercial free-trial report has invalid fields")
        for name in (
            "chapter_contract_revision_ids",
            "final_artifact_revision_ids",
            "commercial_report_ids",
            "promotion_receipt_ids",
        ):
            _strict_string_array(data[name], name)
        if not isinstance(data["findings"], list) or any(
            not isinstance(item, Mapping) for item in data["findings"]
        ):
            raise ValueError("commercial free-trial report findings must be an array")
        report = cls(
            schema_version=data["schema_version"],
            status=data["status"],
            chapter_count=data["chapter_count"],
            story_contract_revision_id=data["story_contract_revision_id"],
            chapter_contract_revision_ids=_strict_string_array(
                data["chapter_contract_revision_ids"], "chapter_contract_revision_ids"
            ),
            final_artifact_revision_ids=_strict_string_array(
                data["final_artifact_revision_ids"], "final_artifact_revision_ids"
            ),
            commercial_report_ids=_strict_string_array(
                data["commercial_report_ids"], "commercial_report_ids"
            ),
            promotion_receipt_ids=_strict_string_array(
                data["promotion_receipt_ids"], "promotion_receipt_ids"
            ),
            findings=tuple(
                CommercialFinding(**item) for item in data["findings"]
            ),
            metrics=data["metrics"],
            artifact_bindings=tuple(data["artifact_bindings"]),
        )
        if data["report_id"] != report.report_id:
            raise ValueError("commercial free-trial report id does not match content")
        return report


@dataclass(frozen=True)
class CommercialBookReport:
    """Deterministic whole-book reader-value and progression review."""

    status: str
    chapter_count: int
    story_contract_revision_id: str = ""
    free_trial_report_id: str = ""
    chapter_contract_revision_ids: tuple[str, ...] = ()
    final_artifact_revision_ids: tuple[str, ...] = ()
    commercial_report_ids: tuple[str, ...] = ()
    promotion_receipt_ids: tuple[str, ...] = ()
    findings: tuple[CommercialFinding, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)
    artifact_bindings: tuple[Mapping[str, str], ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.status not in _COMMERCIAL_REPORT_STATUSES:
            raise ValueError("commercial book report has invalid status")
        if type(self.chapter_count) is not int or self.chapter_count < 1:
            raise ValueError("commercial book report chapter_count must be positive")
        if any(not isinstance(item, CommercialFinding) for item in self.findings):
            raise ValueError("commercial book report findings are invalid")
        if not isinstance(self.metrics, Mapping):
            raise ValueError("commercial book report metrics are invalid")
        object.__setattr__(self, "metrics", dict(self.metrics))
        object.__setattr__(self, "artifact_bindings", _normalize_bindings(self.artifact_bindings))

    @property
    def blockers(self) -> tuple[CommercialFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "blocked")

    @property
    def warnings(self) -> tuple[CommercialFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "warning")

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "chapter_count": self.chapter_count,
            "story_contract_revision_id": self.story_contract_revision_id,
            "free_trial_report_id": self.free_trial_report_id,
            "chapter_contract_revision_ids": list(self.chapter_contract_revision_ids),
            "final_artifact_revision_ids": list(self.final_artifact_revision_ids),
            "commercial_report_ids": list(self.commercial_report_ids),
            "promotion_receipt_ids": list(self.promotion_receipt_ids),
            "findings": [item.to_dict() for item in self.findings],
            "metrics": dict(self.metrics),
            "artifact_bindings": [dict(item) for item in self.artifact_bindings],
        }

    @property
    def report_id(self) -> str:
        return _report_identity(self._payload(), "commercial-book-report")

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "report_id": self.report_id}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommercialBookReport":
        if not isinstance(data, Mapping):
            raise ValueError("commercial book report must be an object")
        expected = {
            "report_id", "schema_version", "status", "chapter_count",
            "story_contract_revision_id", "free_trial_report_id",
            "chapter_contract_revision_ids", "final_artifact_revision_ids",
            "commercial_report_ids", "promotion_receipt_ids", "findings",
            "metrics", "artifact_bindings",
        }
        if set(data) != expected:
            raise ValueError("commercial book report has invalid fields")
        for name in (
            "chapter_contract_revision_ids",
            "final_artifact_revision_ids",
            "commercial_report_ids",
            "promotion_receipt_ids",
        ):
            _strict_string_array(data[name], name)
        if not isinstance(data["findings"], list) or any(
            not isinstance(item, Mapping) for item in data["findings"]
        ):
            raise ValueError("commercial book report findings must be an array")
        report = cls(
            schema_version=data["schema_version"],
            status=data["status"],
            chapter_count=data["chapter_count"],
            story_contract_revision_id=data["story_contract_revision_id"],
            free_trial_report_id=data["free_trial_report_id"],
            chapter_contract_revision_ids=_strict_string_array(
                data["chapter_contract_revision_ids"], "chapter_contract_revision_ids"
            ),
            final_artifact_revision_ids=_strict_string_array(
                data["final_artifact_revision_ids"], "final_artifact_revision_ids"
            ),
            commercial_report_ids=_strict_string_array(
                data["commercial_report_ids"], "commercial_report_ids"
            ),
            promotion_receipt_ids=_strict_string_array(
                data["promotion_receipt_ids"], "promotion_receipt_ids"
            ),
            findings=tuple(CommercialFinding(**item) for item in data["findings"]),
            metrics=data["metrics"],
            artifact_bindings=tuple(data["artifact_bindings"]),
        )
        if data["report_id"] != report.report_id:
            raise ValueError("commercial book report id does not match content")
        return report


@dataclass(frozen=True)
class _ChapterCommercialEvidence:
    chapter: int
    contract: ChapterContract
    contract_revision_id: str
    final_revision_id: str
    final_sha256: str
    report: CommercialChapterReport
    promotion_receipt_id: str

    @property
    def binding(self) -> dict[str, str]:
        return {
            "chapter": str(self.chapter),
            "final_revision_id": self.final_revision_id,
            "final_sha256": self.final_sha256,
            "chapter_contract_revision_id": self.contract_revision_id,
            "commercial_report_id": self.report.report_id,
            "promotion_receipt_id": self.promotion_receipt_id,
        }


def _load_story_contract_for_review(project: Path) -> tuple[CommercialStoryContract, str]:
    brief_path = project / "outputs/input/brief.json"
    foundation_path = project / "outputs/input/foundation.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8")) if brief_path.is_file() else {}
    foundation = (
        json.loads(foundation_path.read_text(encoding="utf-8"))
        if foundation_path.is_file()
        else {}
    )
    payload = brief.get("commercial_story_contract") or foundation.get("commercial_story_contract")
    if payload is None:
        raise ValueError("approved commercial story contract is missing")
    contract = CommercialStoryContract.from_dict(payload)
    approved_id = brief.get("commercial_story_contract_id") or foundation.get("commercial_story_contract_id")
    if approved_id and approved_id != contract.contract_id:
        raise ValueError("approved commercial story contract id is invalid")
    story_head = ArtifactStore(project).get_head(0, "story_contract")
    if story_head is None:
        raise ValueError("story contract artifact head is missing")
    return contract, story_head.revision_id


def _promotion_receipt_for_revision(project: Path, revision_id: str, sha256: str) -> Any:
    from promotion import PromotionService

    service = PromotionService(project)
    for path in sorted(service.receipt_dir.glob("*.json"), key=lambda item: item.name):
        try:
            receipt = service.load_receipt(path.stem, check_current_tail=False)
        except Exception:  # noqa: BLE001 - malformed receipts become review findings
            continue
        if (
            receipt is not None
            and receipt.new_artifact_revision_id == revision_id
            and receipt.new_artifact_sha256 == sha256
        ):
            return receipt
    return None


def _collect_chapter_evidence(
    project: Path, chapter_count: int, story_revision_id: str
) -> tuple[list[_ChapterCommercialEvidence], list[CommercialFinding]]:
    artifacts = ArtifactStore(project)
    findings: list[CommercialFinding] = []
    evidence: list[_ChapterCommercialEvidence] = []
    for number in range(1, chapter_count + 1):
        contract_head = artifacts.get_head(number, "chapter_contract")
        final_head = artifacts.get_head(number, "final")
        report_path = project / "outputs/quality/commercial" / f"chapter_{number:03d}_report.json"
        if contract_head is None:
            findings.append(_finding("missing_chapter_contract", f"Chapter {number} contract head is missing"))
            continue
        if final_head is None:
            findings.append(_finding("missing_final_artifact", f"Chapter {number} final artifact head is missing"))
            continue
        try:
            contract = ChapterContract.from_dict(
                json.loads(artifacts.read_text(contract_head.revision_id))
            )
            final_revision = artifacts.get_revision(final_head.revision_id)
            artifacts.read_text(final_head.revision_id)
            if final_revision.chapter != number or final_revision.kind != "final":
                raise ValueError("final artifact revision has the wrong chapter or kind")
            if not report_path.is_file():
                raise ValueError("commercial chapter report is missing")
            report = CommercialChapterReport.from_dict(
                json.loads(report_path.read_text(encoding="utf-8"))
            )
        except Exception as exc:  # noqa: BLE001 - report all durable binding failures
            findings.append(_finding("invalid_chapter_binding", f"Chapter {number} commercial binding is invalid: {exc}"))
            continue
        if report.chapter != number:
            findings.append(_finding("chapter_report_number_mismatch", f"Chapter {number} report number is inconsistent"))
        if report.status == "blocked" or report.blockers:
            findings.append(_finding("chapter_commercial_report_blocked", f"Chapter {number} commercial report is blocked"))
        if report.artifact_sha256 != final_revision.sha256:
            findings.append(_finding("chapter_report_artifact_mismatch", f"Chapter {number} report is not bound to the final artifact"))
        if report.story_contract_revision_id != story_revision_id:
            findings.append(_finding("chapter_report_story_contract_mismatch", f"Chapter {number} report uses a different story contract"))
        if report.chapter_contract_revision_id != contract_head.revision_id:
            findings.append(_finding("chapter_report_contract_mismatch", f"Chapter {number} report uses a different chapter contract"))
        receipt = _promotion_receipt_for_revision(project, final_revision.revision_id, final_revision.sha256)
        if receipt is None:
            findings.append(_finding("missing_promotion_receipt", f"Chapter {number} has no promotion receipt bound to its final artifact"))
            receipt_id = ""
        else:
            metadata = dict(receipt.decision_metadata)
            if metadata.get("commercial_report_id") != report.report_id:
                findings.append(_finding("promotion_report_mismatch", f"Chapter {number} promotion does not bind its commercial report"))
            receipt_id = receipt.receipt_id
        evidence.append(
            _ChapterCommercialEvidence(
                chapter=number,
                contract=contract,
                contract_revision_id=contract_head.revision_id,
                final_revision_id=final_revision.revision_id,
                final_sha256=final_revision.sha256,
                report=report,
                promotion_receipt_id=receipt_id,
            )
        )
    return evidence, findings


def _distribution(values: Sequence[str], categories: Sequence[str]) -> dict[str, int]:
    result = {category: 0 for category in sorted(categories)}
    for value in values:
        if value in result:
            result[value] += 1
    return result


def _quality_metrics(evidence: Sequence[_ChapterCommercialEvidence]) -> dict[str, Any]:
    contracts = [item.contract for item in evidence]
    humiliation_streak = maximum_humiliation_streak = 0
    for contract in contracts:
        if contract.humiliation_scene:
            humiliation_streak += 1
            maximum_humiliation_streak = max(maximum_humiliation_streak, humiliation_streak)
        else:
            humiliation_streak = 0
    known_resources: set[str] = set()
    unseeded_rescue_count = 0
    for contract in contracts:
        known_resources.update(contract.seeded_resource_ids)
        unseeded_rescue_count += sum(
            resource not in known_resources for resource in contract.used_resource_ids
        )
    return {
        "satisfaction_type_distribution": _distribution(
            [contract.satisfaction_type for contract in contracts], SATISFACTION_TYPES
        ),
        "reader_job_distribution": _distribution(
            [job for contract in contracts for job in contract.reader_jobs], READER_JOBS
        ),
        "hook_type_distribution": _distribution(
            [contract.hook_type for contract in contracts],
            ("decision", "consequence", "evidence", "relationship_shift", "deadline", "arrival"),
        ),
        "resource_dimension_distribution": _distribution(
            [contract.resource_dimension for contract in contracts], RESOURCE_DIMENSIONS
        ),
        "maximum_humiliation_streak": maximum_humiliation_streak,
        "unseeded_rescue_count": unseeded_rescue_count,
        "protagonist_caused_turn_count": sum(
            1 for contract in contracts if contract.protagonist_causes_turn
        ),
    }


def _write_review_report(project: Path, relative: str, report: Any) -> Path:
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f"{path.name}.tmp-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def write_commercial_free_trial_report(project: Path, report: CommercialFreeTrialReport) -> Path:
    return _write_review_report(project, "outputs/quality/commercial-free-trial-report.json", report)


def write_commercial_book_report(project: Path, report: CommercialBookReport) -> Path:
    return _write_review_report(project, "outputs/quality/commercial-book-report.json", report)


def _free_trial_assignment(chapter_count: int) -> dict[int, tuple[str, ...]]:
    if chapter_count == 3:
        return {
            1: ("recognition_event",),
            2: ("pattern_proof", "first_boundary_test"),
            3: ("local_payoff", "irreversible_choice", "visible_cost", "next_concrete_expectation"),
        }
    return {
        1: ("recognition_event",),
        2: ("pattern_proof",),
        3: ("first_boundary_test", "local_payoff"),
        4: ("irreversible_choice", "visible_cost", "next_concrete_expectation"),
    }


def free_trial_beats_for_chapter(
    story_contract: CommercialStoryContract, chapter: int
) -> tuple[str, ...]:
    if not isinstance(story_contract, CommercialStoryContract):
        raise TypeError("story_contract must be CommercialStoryContract")
    if type(chapter) is not int or chapter < 1:
        raise ValueError("chapter must be positive")
    return _free_trial_assignment(story_contract.free_trial_arc.chapter_count).get(
        chapter, ()
    )


def evaluate_commercial_free_trial(project: Path) -> CommercialFreeTrialReport:
    project = Path(project)
    findings: list[CommercialFinding] = []
    story_revision_id = ""
    chapter_count = 3
    evidence: list[_ChapterCommercialEvidence] = []
    try:
        story, story_revision_id = _load_story_contract_for_review(project)
        chapter_count = story.free_trial_arc.chapter_count
        evidence, collection_findings = _collect_chapter_evidence(project, chapter_count, story_revision_id)
        findings.extend(collection_findings)
        assignment = _free_trial_assignment(chapter_count)
        by_chapter = {item.chapter: item for item in evidence}
        for chapter, beats in assignment.items():
            item = by_chapter.get(chapter)
            delivered = set(item.report.free_trial_beats) if item is not None else set()
            for beat in beats:
                if beat not in delivered:
                    label = beat[:-6] if beat.endswith("_event") else beat
                    findings.append(_finding(f"free_trial_{label}_missing", f"Free-trial beat {beat} is missing from chapter {chapter}"))
            for extra in delivered - set(beats):
                findings.append(_finding("free_trial_beat_misplaced", f"Free-trial beat {extra} is assigned to the wrong chapter"))
    except Exception as exc:  # noqa: BLE001 - callers receive an auditable blocked report
        findings.append(_finding("free_trial_review_inputs_invalid", str(exc)))
    metrics = _quality_metrics(evidence)
    metrics["free_trial_beats_required"] = [
        beat for beats in _free_trial_assignment(chapter_count).values() for beat in beats
    ]
    metrics["free_trial_beats_delivered"] = [
        beat for item in evidence for beat in item.report.free_trial_beats
    ]
    status = "blocked" if any(item.severity == "blocked" for item in findings) else "warning" if findings else "pass"
    return CommercialFreeTrialReport(
        status=status,
        chapter_count=chapter_count,
        story_contract_revision_id=story_revision_id,
        chapter_contract_revision_ids=tuple(item.contract_revision_id for item in evidence),
        final_artifact_revision_ids=tuple(item.final_revision_id for item in evidence),
        commercial_report_ids=tuple(item.report.report_id for item in evidence),
        promotion_receipt_ids=tuple(item.promotion_receipt_id for item in evidence),
        findings=tuple(findings),
        metrics=metrics,
        artifact_bindings=tuple(item.binding for item in evidence),
    )


def evaluate_commercial_book(project: Path, chapter_count: int) -> CommercialBookReport:
    project = Path(project)
    findings: list[CommercialFinding] = []
    story_revision_id = ""
    story: CommercialStoryContract | None = None
    evidence: list[_ChapterCommercialEvidence] = []
    free_trial_report: CommercialFreeTrialReport | None = None
    try:
        if type(chapter_count) is not int or chapter_count < 1:
            raise ValueError("chapter_count must be positive")
        story, story_revision_id = _load_story_contract_for_review(project)
        evidence, collection_findings = _collect_chapter_evidence(project, chapter_count, story_revision_id)
        findings.extend(collection_findings)
        free_trial_path = project / "outputs/quality/commercial-free-trial-report.json"
        if free_trial_path.is_file():
            free_trial_report = CommercialFreeTrialReport.from_dict(
                json.loads(free_trial_path.read_text(encoding="utf-8"))
            )
            expected = story.free_trial_arc.chapter_count
            if free_trial_report.chapter_count != expected:
                findings.append(_finding("free_trial_report_binding_invalid", "Free-trial report chapter window differs from the story contract"))
        else:
            # Direct evaluation remains useful for imported fixtures; the
            # pipeline always persists this report before invoking this gate.
            free_trial_report = evaluate_commercial_free_trial(project)
        if free_trial_report.status == "blocked":
            findings.append(_finding("free_trial_review_blocked", "The free-trial review is blocked"))

        approved_anchors = set(story.premise_engine.belonging_anchors)
        anchor_payoffs: dict[str, list[int]] = {anchor: [] for anchor in sorted(approved_anchors)}
        for item in evidence:
            update = item.report.reader_value_update or {}
            for anchor in update.get("belonging_anchors", ()):  # evidence lives in the chapter report
                if anchor not in approved_anchors:
                    findings.append(_finding("unapproved_belonging_payoff", f"Chapter {item.chapter} uses an unapproved belonging anchor"))
                elif item.chapter not in anchor_payoffs.setdefault(anchor, []):
                    anchor_payoffs[anchor].append(item.chapter)
        delivered_anchors = [anchor for anchor, chapters in anchor_payoffs.items() if chapters]
        if len(delivered_anchors) < 2:
            findings.append(_finding("belonging_payoff_incomplete", "At least two approved belonging anchors need observable payoff"))
        metrics = _quality_metrics(evidence)
        metrics["belonging_anchor_payoffs"] = {
            anchor: chapters for anchor, chapters in anchor_payoffs.items() if chapters
        }
        metrics["delivered_belonging_anchor_count"] = len(delivered_anchors)

        delivered_dimensions = {
            item.contract.resource_dimension for item in evidence
        }
        ladder_dimensions = [
            step.resource_dimension for step in story.conflict_ladder
        ]
        if (
            ladder_dimensions[0] not in delivered_dimensions
            or ladder_dimensions[-1] not in delivered_dimensions
        ):
            findings.append(
                _finding(
                    "core_conflict_progression_incomplete",
                    "The book does not deliver both the opening and terminal conflict dimensions",
                )
            )
        required_dimension_breadth = min(3, len(set(ladder_dimensions)))
        if len(delivered_dimensions.intersection(ladder_dimensions)) < required_dimension_breadth:
            findings.append(
                _finding(
                    "resource_escalation_incomplete",
                    "The conflict does not escalate across enough approved resource dimensions",
                )
            )

        reader_jobs = {
            job for item in evidence for job in item.contract.reader_jobs
        }
        required_job_groups = (
            {"recognition"},
            {"anger", "pity", "regret"},
            {"agency", "hope"},
            {"belonging"},
        )
        if any(not reader_jobs.intersection(group) for group in required_job_groups):
            findings.append(
                _finding(
                    "reader_value_progression_incomplete",
                    "The book lacks one or more recognition, pressure, agency, or belonging stages",
                )
            )

        hook_types = {item.contract.hook_type for item in evidence}
        if chapter_count >= 3 and len(hook_types) < 2:
            findings.append(
                _finding(
                    "hook_distribution_flat",
                    "The whole book repeats one hook mechanism without enough variation",
                )
            )
        if (
            story.quality_budgets.protagonist_causes_major_turn
            and metrics["protagonist_caused_turn_count"] != len(evidence)
        ):
            findings.append(
                _finding(
                    "protagonist_turn_incomplete",
                    "At least one promoted chapter leaves the major turn outside protagonist agency",
                )
            )
        if (
            metrics["maximum_humiliation_streak"]
            > story.quality_budgets.consecutive_humiliation_scenes_max
        ):
            findings.append(
                _finding(
                    "humiliation_budget_exceeded",
                    "The whole book exceeds the consecutive humiliation budget",
                )
            )
        if (
            metrics["unseeded_rescue_count"]
            > story.quality_budgets.unseeded_rescue_max
        ):
            findings.append(
                _finding(
                    "unseeded_rescue_budget_exceeded",
                    "The whole book uses resources before they are seeded",
                )
            )
    except Exception as exc:  # noqa: BLE001 - callers receive an auditable blocked report
        findings.append(_finding("book_review_inputs_invalid", str(exc)))
        metrics = _quality_metrics(evidence)
    status = "blocked" if any(item.severity == "blocked" for item in findings) else "warning" if findings else "pass"
    return CommercialBookReport(
        status=status,
        chapter_count=chapter_count,
        story_contract_revision_id=story_revision_id,
        free_trial_report_id=free_trial_report.report_id if free_trial_report else "",
        chapter_contract_revision_ids=tuple(item.contract_revision_id for item in evidence),
        final_artifact_revision_ids=tuple(item.final_revision_id for item in evidence),
        commercial_report_ids=tuple(item.report.report_id for item in evidence),
        promotion_receipt_ids=tuple(item.promotion_receipt_id for item in evidence),
        findings=tuple(findings),
        metrics=metrics,
        artifact_bindings=tuple(item.binding for item in evidence),
    )


def _review_input_hashes(
    project: Path, chapter_count: int, *, include_free_trial: bool = False
) -> dict[str, str]:
    """Hash immutable review inputs, including heads and promotion bindings."""
    try:
        _story, story_revision_id = _load_story_contract_for_review(project)
    except Exception:
        story_revision_id = ""
    artifacts = ArtifactStore(project)
    values: dict[str, str] = {"story_contract_revision_id": story_revision_id}
    for number in range(1, chapter_count + 1):
        contract_head = artifacts.get_head(number, "chapter_contract")
        final_head = artifacts.get_head(number, "final")
        values[f"chapter_{number:03d}_contract_revision_id"] = contract_head.revision_id if contract_head else ""
        values[f"chapter_{number:03d}_final_revision_id"] = final_head.revision_id if final_head else ""
        if final_head is not None:
            try:
                revision = artifacts.get_revision(final_head.revision_id)
                values[f"chapter_{number:03d}_final_sha256"] = revision.sha256
                receipt = _promotion_receipt_for_revision(project, revision.revision_id, revision.sha256)
                values[f"chapter_{number:03d}_promotion_receipt_id"] = receipt.receipt_id if receipt else ""
            except Exception:
                values[f"chapter_{number:03d}_final_sha256"] = ""
                values[f"chapter_{number:03d}_promotion_receipt_id"] = ""
        report_path = project / "outputs/quality/commercial" / f"chapter_{number:03d}_report.json"
        values[f"chapter_{number:03d}_commercial_report_sha256"] = (
            hashlib.sha256(report_path.read_bytes()).hexdigest() if report_path.is_file() else ""
        )
    if include_free_trial:
        free_trial_path = project / "outputs/quality/commercial-free-trial-report.json"
        values["commercial_free_trial_report_sha256"] = (
            hashlib.sha256(free_trial_path.read_bytes()).hexdigest()
            if free_trial_path.is_file()
            else ""
        )
    return values


def commercial_free_trial_input_hashes(project: Path) -> dict[str, str]:
    try:
        story, _revision = _load_story_contract_for_review(Path(project))
        chapter_count = story.free_trial_arc.chapter_count
    except Exception:
        chapter_count = 3
    return _review_input_hashes(Path(project), chapter_count)


def commercial_book_input_hashes(project: Path, chapter_count: int) -> dict[str, str]:
    return _review_input_hashes(Path(project), chapter_count, include_free_trial=True)


def _trailing_count(prior: Sequence[ChapterContract], value: str, field: str) -> int:
    count = 0
    for contract in reversed(prior):
        if getattr(contract, field) != value:
            break
        count += 1
    return count


def _free_trial_findings(
    contract: ChapterContract, story: CommercialStoryContract
) -> list[CommercialFinding]:
    chapter = contract.chapter
    arc = story.free_trial_arc
    if chapter > arc.chapter_count:
        return []
    findings: list[CommercialFinding] = []
    jobs = set(contract.reader_jobs)
    if chapter == 1 and "recognition" not in jobs:
        findings.append(_finding("free_trial_recognition_missing", "Chapter 1 must declare recognition."))
    if 1 < chapter < arc.chapter_count and not jobs.intersection({"anger", "pity", "regret"}):
        findings.append(
            _finding(
                "free_trial_intermediate_emotion_missing",
                "An intermediate free-trial chapter must declare anger, pity, or regret.",
            )
        )
    if chapter == arc.chapter_count and not jobs.intersection({"agency", "hope"}):
        findings.append(
            _finding(
                "free_trial_closing_agency_missing",
                "The final free-trial chapter must declare agency or hope.",
            )
        )
    return findings


def validate_chapter_design(
    contract: ChapterContract,
    prior_contracts: Sequence[ChapterContract],
    story_contract: CommercialStoryContract,
) -> ChapterDesignReport:
    if not isinstance(contract, ChapterContract):
        raise TypeError("contract must be ChapterContract")
    if not isinstance(story_contract, CommercialStoryContract):
        raise TypeError("story_contract must be CommercialStoryContract")
    if contract.schema_version != 2:
        raise ValueError("commercial chapter design requires schema-v2 chapter contract")
    prior = tuple(prior_contracts)
    findings: list[CommercialFinding] = []
    premise = story_contract.premise_engine
    allowed_anchors = set(premise.belonging_anchors)
    if any(anchor not in allowed_anchors for anchor in contract.belonging_anchors):
        findings.append(_finding("unapproved_belonging_anchor", "Chapter anchor is outside the approved story anchors."))
    if contract.belonging_anchors and "belonging" not in contract.reader_jobs:
        findings.append(_finding("anchor_without_belonging_job", "A declared chapter anchor requires the belonging reader job."))
    if "belonging" in contract.reader_jobs and not contract.belonging_anchors:
        findings.append(_finding("belonging_job_without_anchor", "The belonging reader job requires at least one chapter anchor."))

    known_resources = {
        resource_id
        for previous in prior
        for resource_id in previous.seeded_resource_ids
    }
    known_resources.update(contract.seeded_resource_ids)
    missing_resources = sorted(
        resource_id
        for resource_id in contract.used_resource_ids
        if resource_id not in known_resources
    )
    if missing_resources:
        findings.append(
            _finding(
                "unseeded_resource",
                "Every used resource must be seeded in this or an earlier "
                f"chapter. Missing seeded resource IDs: {', '.join(missing_resources)}.",
            )
        )
    if not contract.protagonist_causes_turn:
        findings.append(_finding("protagonist_does_not_cause_turn", "The protagonist must cause the chapter's major turn."))

    if prior and _trailing_count(prior, contract.hook_type, "hook_type") + 1 > story_contract.quality_budgets.identical_hook_type_max + 1:
        findings.append(_finding("identical_hook_budget", "The same hook type repeats beyond the configured consecutive budget."))
    if contract.humiliation_scene and _trailing_count(prior, True, "humiliation_scene") + 1 > story_contract.quality_budgets.consecutive_humiliation_scenes_max:
        findings.append(_finding("humiliation_budget", "Consecutive humiliation scenes exceed the configured budget."))

    findings.extend(_free_trial_findings(contract, story_contract))
    status = "blocked" if any(item.severity == "blocked" for item in findings) else "warning" if findings else "pass"
    return ChapterDesignReport(
        status=status,
        chapter=contract.chapter,
        story_contract_id=story_contract.contract_id,
        chapter_contract_id=contract.contract_id,
        prior_contract_ids=tuple(item.contract_id for item in prior),
        findings=tuple(findings),
    )


def write_design_report(project: Path, report: ChapterDesignReport) -> Path:
    path = project / "outputs/quality/commercial" / f"chapter_{report.chapter:03d}_design.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f"{path.name}.tmp-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def chapter_design_input_hashes(project: Path, chapter: int) -> dict[str, str]:
    """Return immutable contract-head bindings used by the design gate."""
    artifacts = ArtifactStore(project)
    story_head = artifacts.get_head(0, "story_contract")
    current_head = artifacts.get_head(chapter, "chapter_contract")
    if story_head is None or current_head is None:
        raise ValueError("chapter design requires story and chapter contract heads")
    prior_ids: list[str] = []
    for number in range(1, chapter):
        head = artifacts.get_head(number, "chapter_contract")
        if head is None:
            raise ValueError(f"chapter design is missing prior contract head {number}")
        prior_ids.append(head.revision_id)
    encoded = json.dumps(prior_ids, separators=(",", ":")).encode("utf-8")
    return {
        "story_contract_revision_id": story_head.revision_id,
        "chapter_contract_revision_id": current_head.revision_id,
        "prior_contract_revisions_sha256": hashlib.sha256(encoded).hexdigest(),
    }


__all__ = [
    "ChapterDesignReport",
    "CommercialFinding",
    "CommercialChapterReport",
    "CommercialFreeTrialReport",
    "CommercialBookReport",
    "chapter_design_input_hashes",
    "commercial_free_trial_input_hashes",
    "commercial_book_input_hashes",
    "evaluate_commercial_free_trial",
    "evaluate_commercial_book",
    "free_trial_beats_for_chapter",
    "review_commercial_chapter",
    "write_commercial_report",
    "write_commercial_free_trial_report",
    "write_commercial_book_report",
    "validate_chapter_design",
    "write_design_report",
]
