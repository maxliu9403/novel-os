"""Deterministic chapter design and commercial quality contracts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
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


@dataclass(frozen=True)
class CommercialFinding:
    code: str
    severity: str
    message: str

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
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.status not in _COMMERCIAL_REPORT_STATUSES:
            raise ValueError("commercial chapter report has invalid status")
        if not isinstance(self.chapter, int) or self.chapter < 1:
            raise ValueError("commercial chapter report chapter must be positive")
        if not isinstance(self.artifact_sha256, str) or len(self.artifact_sha256) != 64:
            raise ValueError("commercial chapter report artifact sha is invalid")
        if any(not isinstance(item, CommercialFinding) for item in self.findings):
            raise ValueError("commercial chapter report findings are invalid")
        if any(not isinstance(item, QualityFinding) for item in self.quality_findings):
            raise ValueError("commercial chapter report quality findings are invalid")
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
        if set(data) != expected:
            raise ValueError("commercial chapter report has invalid fields")
        findings = tuple(
            CommercialFinding(
                code=item["code"], severity=item["severity"], message=item["message"]
            )
            for item in data["findings"]
        )
        quality_findings = tuple(
            QualityFinding.from_dict(item) for item in data["quality_findings"]
        )
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
        )
        if data["report_id"] != report.report_id:
            raise ValueError("commercial chapter report id does not match artifact")
        return report


def _finding(code: str, message: str, severity: str = "blocked") -> CommercialFinding:
    return CommercialFinding(code=code, severity=severity, message=message)


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
    quote = value.get("quote")
    start, end = value.get("start"), value.get("end")
    if (
        not isinstance(quote, str)
        or not quote.strip()
        or type(start) is not int
        or type(end) is not int
        or start < 0
        or end <= start
        or end > len(candidate_text)
        or candidate_text[start:end] != quote
    ):
        findings.append(_finding(f"invalid_{label}_evidence", f"{label} evidence does not match the exact candidate"))
        return findings, None
    return findings, EvidenceSpan(
        artifact_sha256=artifact_sha256,
        quote=quote,
        start=start,
        end=end,
    )


def _strict_guardian_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _GUARDIAN_FIELDS:
        raise ValueError("commercial Guardian payload has invalid fields")
    if not isinstance(payload["findings"], list):
        raise ValueError("commercial Guardian findings must be a list")
    if not isinstance(payload["free_trial_beats"], list):
        raise ValueError("commercial Guardian free_trial_beats must be a list")
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
                start, end, quote = item["start"], item["end"], item["quote"]
                if (
                    type(start) is not int
                    or type(end) is not int
                    or not isinstance(quote, str)
                    or start < 0
                    or end <= start
                    or end > len(candidate_text)
                    or candidate_text[start:end] != quote
                ):
                    valid = False
                    break
                spans.append(EvidenceSpan(artifact_sha256=artifact_sha256, quote=quote, start=start, end=end))
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


def review_commercial_chapter(
    candidate_text: str,
    chapter_contract: ChapterContract,
    guardian_payload: Mapping[str, Any],
    recent_updates: Sequence[Mapping[str, Any]],
    *,
    story_contract_revision_id: str = "",
    chapter_contract_revision_id: str = "",
) -> CommercialChapterReport:
    """Validate Guardian's exact delivery claims against one candidate artifact."""
    if not isinstance(candidate_text, str) or not candidate_text.strip():
        raise ValueError("candidate_text must be nonblank")
    if not isinstance(chapter_contract, ChapterContract) or chapter_contract.schema_version != 2:
        raise ValueError("commercial review requires a schema-v2 chapter contract")
    payload = _strict_guardian_payload(guardian_payload)
    artifact_sha256 = hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()
    findings: list[CommercialFinding] = []

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
    if any(resource_id not in known_resources for resource_id in contract.used_resource_ids):
        findings.append(_finding("unseeded_resource", "Every used resource must be seeded in this or an earlier chapter."))
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
    "chapter_design_input_hashes",
    "validate_chapter_design",
    "write_design_report",
]
