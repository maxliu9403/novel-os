"""Deterministic quality facade for exact-revision continuity evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .models import (
    DEFAULT_RUBRIC_VERSION,
    EvidenceSpan,
    EvaluationReport,
    EvaluationRequest,
    QualityFinding,
)


DETERMINISTIC_PROVIDER = "deterministic"
DETERMINISTIC_MODEL = "continuity-engine.v1"
DETERMINISTIC_PROMPT_SHA256 = hashlib.sha256(
    b"novel-os:quality-lab:deterministic-continuity:v1"
).hexdigest()

_SEVERITY_MAP = {
    "critical": "critical",
    "warning": "major",
    "major": "major",
    "minor": "minor",
    "info": "info",
}


class QualityLab:
    """Adapt deterministic findings without granting unverified authority."""

    @classmethod
    def evaluate_deterministic(
        cls,
        request: EvaluationRequest,
        continuity_findings: Iterable[Any] = (),
        *,
        candidate_text: str,
        prompt_sha256: str = DETERMINISTIC_PROMPT_SHA256,
        provider: str = DETERMINISTIC_PROVIDER,
        model: str = DETERMINISTIC_MODEL,
        created_at: str | None = None,
    ) -> EvaluationReport:
        if not isinstance(request, EvaluationRequest):
            raise TypeError("request must be an EvaluationRequest")
        if not isinstance(candidate_text, str):
            raise TypeError("candidate_text must be a string")
        if not request.verify_text(candidate_text):
            raise ValueError("candidate_text does not match the request artifact_sha256")
        if request.rubric_version != DEFAULT_RUBRIC_VERSION:
            raise ValueError(
                f"request rubric must be {DEFAULT_RUBRIC_VERSION!r}"
            )

        findings = cls._adapt_findings(
            continuity_findings, request, candidate_text
        )
        has_critical = any(
            finding.blocking and finding.severity == "critical"
            for finding in findings
        )
        has_blocking = any(finding.blocking for finding in findings)
        status = "fail" if has_critical else "needs_repair" if has_blocking else "pass"

        return EvaluationReport(
            request=request,
            artifact_sha256=request.artifact_sha256,
            evaluation_id=request.evaluation_id,
            rubric_version=request.rubric_version,
            prompt_sha256=prompt_sha256,
            evaluator_provider=provider,
            evaluator_model=model,
            hard_gates={"no_blocking_findings": not has_blocking},
            semantic_dimensions={},
            findings=findings,
            status=status,
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def validate_report_for_request(
        request: EvaluationRequest, report: EvaluationReport
    ) -> EvaluationReport:
        if not isinstance(request, EvaluationRequest):
            raise TypeError("request must be an EvaluationRequest")
        if not isinstance(report, EvaluationReport):
            raise TypeError("report must be an EvaluationReport")
        if report.request.evaluation_id != request.evaluation_id:
            raise ValueError("report does not match the supplied request revision")
        return report

    @classmethod
    def _adapt_findings(
        cls,
        raw_findings: Iterable[Any],
        request: EvaluationRequest,
        candidate_text: str,
    ) -> tuple[QualityFinding, ...]:
        if raw_findings is None:
            raw_findings = ()
        if isinstance(raw_findings, (str, bytes, Mapping)):
            raw_findings = (raw_findings,)

        deduplicated: dict[str, QualityFinding] = {}
        try:
            iterator = iter(raw_findings)
        except TypeError:
            iterator = iter(())
        for raw in iterator:
            finding = cls._adapt_finding(raw, request, candidate_text)
            if finding is not None:
                deduplicated.setdefault(finding.claim_signature, finding)
        return tuple(deduplicated.values())

    @classmethod
    def _adapt_finding(
        cls,
        raw: Any,
        request: EvaluationRequest,
        candidate_text: str,
    ) -> QualityFinding | None:
        values = cls._finding_values(raw)
        severity_value = values.get("severity")
        if not isinstance(severity_value, str):
            return None
        severity = _SEVERITY_MAP.get(severity_value.strip().lower())
        category = values.get("category")
        message = values.get("message")
        if (
            severity is None
            or not isinstance(category, str)
            or not category.strip()
            or not isinstance(message, str)
            or not message.strip()
        ):
            return None

        suggestion = values.get("suggested_action", values.get("suggestion", ""))
        if not isinstance(suggestion, str) or not suggestion.strip():
            suggestion = "Review this continuity finding."
        evidence = cls._evidence_for(values, request)

        finding = QualityFinding.create(
            artifact_sha256=request.artifact_sha256,
            category=category,
            severity=severity,
            message=message,
            evidence=evidence,
            suggested_action=suggestion,
            repair_class="continuity",
        )
        return finding.with_verified_evidence(
            candidate_text, request.artifact_sha256
        )

    @staticmethod
    def _finding_values(raw: Any) -> dict[str, Any]:
        if isinstance(raw, QualityFinding):
            return {
                "severity": raw.severity,
                "category": raw.category,
                "message": raw.message,
                "suggested_action": raw.suggested_action,
                "evidence": raw.evidence,
            }
        if isinstance(raw, Mapping):
            return dict(raw)
        return {
            field: getattr(raw, field)
            for field in (
                "severity",
                "category",
                "message",
                "suggestion",
                "suggested_action",
                "evidence",
                "quote",
                "start",
                "end",
                "artifact_sha256",
                "artifact_revision_id",
            )
            if hasattr(raw, field)
        }

    @classmethod
    def _evidence_for(
        cls, values: Mapping[str, Any], request: EvaluationRequest
    ) -> tuple[EvidenceSpan, ...]:
        artifact_sha256 = request.artifact_sha256
        finding_sha = values.get("artifact_sha256")
        finding_revision = values.get("artifact_revision_id")
        if finding_sha not in (None, artifact_sha256) or finding_revision not in (
            None,
            "",
            request.artifact_revision_id,
        ):
            return ()
        if "evidence" in values:
            raw_evidence = values["evidence"]
            if isinstance(raw_evidence, (EvidenceSpan, Mapping)):
                candidates = (raw_evidence,)
            elif isinstance(raw_evidence, Sequence) and not isinstance(
                raw_evidence, (str, bytes)
            ):
                candidates = raw_evidence
            else:
                candidates = ()
        elif any(field in values for field in ("quote", "start", "end")):
            candidates = (values,)
        else:
            candidates = ()

        evidence: list[EvidenceSpan] = []
        for raw in candidates:
            if isinstance(raw, EvidenceSpan):
                item = raw
            elif isinstance(raw, Mapping):
                evidence_sha = raw.get("artifact_sha256", artifact_sha256)
                evidence_revision = raw.get("artifact_revision_id")
                if evidence_sha != artifact_sha256 or evidence_revision not in (
                    None,
                    "",
                    request.artifact_revision_id,
                ):
                    continue
                try:
                    item = EvidenceSpan(
                        artifact_sha256=artifact_sha256,
                        quote=raw.get("quote"),
                        start=raw.get("start"),
                        end=raw.get("end"),
                    )
                except (TypeError, ValueError):
                    continue
            else:
                continue
            if item.artifact_sha256 == artifact_sha256:
                evidence.append(item)
        return tuple(evidence)


__all__ = [
    "DETERMINISTIC_MODEL",
    "DETERMINISTIC_PROMPT_SHA256",
    "DETERMINISTIC_PROVIDER",
    "QualityLab",
]
