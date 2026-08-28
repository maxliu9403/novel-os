"""Deterministic adaptation of continuity findings into quality reports."""

import hashlib
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from continuity_engine import Finding
from quality import (
    DEFAULT_RUBRIC_VERSION,
    EvidenceSpan,
    EvaluationRequest,
    QualityLab,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _request(text: str, revision: str = "revision-1") -> EvaluationRequest:
    return EvaluationRequest.for_text(
        1,
        text,
        artifact_revision_id=revision,
        rubric_version="quality.v1",
    )


def _finding(text: str, **overrides):
    quote = "three days earlier"
    start = text.find(quote)
    values = {
        "severity": "warning",
        "category": "timeline",
        "message": "The date conflicts with the earlier scene.",
        "suggestion": "Align the dates.",
        "evidence": [
            {
                "artifact_sha256": _sha(text),
                "quote": quote,
                "start": start,
                "end": start + len(quote),
            }
        ],
    }
    values.update(overrides)
    return values


def test_empty_input_returns_exact_request_bound_immutable_pass_report():
    text = "Mara opened the door."
    request = _request(text)

    report = QualityLab.evaluate_deterministic(
        request,
        candidate_text=text,
        continuity_findings=[],
        created_at="2026-08-24T00:00:00+00:00",
    )

    assert DEFAULT_RUBRIC_VERSION == "quality.v1"
    assert report.request is request
    assert report.artifact_sha256 == request.artifact_sha256
    assert report.evaluation_id == request.evaluation_id
    assert report.rubric_version == "quality.v1"
    assert report.evaluator_provider == "deterministic"
    assert report.evaluator_model == "continuity-engine.v1"
    assert report.hard_gates == {"no_blocking_findings": True}
    assert report.semantic_dimensions == {}
    assert report.findings == ()
    assert report.status == "pass"
    assert "score" not in report.to_dict()
    with pytest.raises(TypeError):
        report.hard_gates["no_blocking_findings"] = False
    with pytest.raises(FrozenInstanceError):
        report.status = "fail"


def test_valid_exact_quote_and_span_make_warning_a_verified_major_blocker():
    text = "Mara arrived three days earlier than the ledger allowed."

    report = QualityLab.evaluate_deterministic(
        _request(text),
        candidate_text=text,
        continuity_findings=[_finding(text)],
        created_at="2026-08-24T00:00:00+00:00",
    )

    finding = report.findings[0]
    assert finding.category == "timeline"
    assert finding.severity == "major"
    assert finding.suggested_action == "Align the dates."
    assert finding.repair_class == "continuity"
    assert finding.evidence_verified is True
    assert finding.blocking is True
    assert report.hard_gates == {"no_blocking_findings": False}
    assert report.status == "needs_repair"


def test_verified_critical_finding_fails_report():
    text = "Mara arrived three days earlier than the ledger allowed."

    report = QualityLab.evaluate_deterministic(
        _request(text),
        candidate_text=text,
        continuity_findings=[_finding(text, severity="critical")],
        created_at="2026-08-24T00:00:00+00:00",
    )

    assert report.findings[0].blocking is True
    assert report.status == "fail"


def test_invalid_and_cross_revision_evidence_stays_auditable_but_nonblocking():
    text = "Mara arrived today."
    invalid_quote = _finding(
        text,
        severity="critical",
        evidence=[{"artifact_sha256": _sha(text), "quote": "not in candidate"}],
    )
    cross_revision = _finding(
        text,
        severity="critical",
        message="Evidence came from another revision.",
        evidence=[{"artifact_sha256": "a" * 64, "quote": "Mara arrived today."}],
    )

    report = QualityLab.evaluate_deterministic(
        _request(text),
        candidate_text=text,
        continuity_findings=[invalid_quote, cross_revision],
        created_at="2026-08-24T00:00:00+00:00",
    )

    assert len(report.findings) == 2
    assert all(not finding.blocking for finding in report.findings)
    assert report.findings[0].evidence_verification_result is False
    assert report.findings[1].evidence == ()
    assert report.hard_gates["no_blocking_findings"] is True
    assert report.status == "pass"


def test_finding_level_revision_mismatch_cannot_rebind_nested_evidence():
    text = "Mara arrived three days earlier than the ledger allowed."
    from_old_revision = _finding(
        text,
        severity="critical",
        artifact_revision_id="revision-0",
        evidence=[{"quote": "three days earlier"}],
    )

    report = QualityLab.evaluate_deterministic(
        _request(text, "revision-1"),
        candidate_text=text,
        continuity_findings=[from_old_revision],
        created_at="2026-08-24T00:00:00+00:00",
    )

    assert report.findings[0].evidence == ()
    assert report.findings[0].blocking is False
    assert report.status == "pass"


def test_object_finding_revision_mismatch_cannot_rebind_quote_evidence():
    text = "Mara arrived three days earlier than the ledger allowed."
    from_old_revision = SimpleNamespace(
        severity="critical",
        category="timeline",
        message="Object evidence came from another revision.",
        suggestion="Check the source revision.",
        artifact_revision_id="revision-0",
        evidence=[{"quote": "three days earlier"}],
    )

    report = QualityLab.evaluate_deterministic(
        _request(text, "revision-1"),
        candidate_text=text,
        continuity_findings=[from_old_revision],
        created_at="2026-08-24T00:00:00+00:00",
    )

    assert report.findings[0].evidence == ()
    assert report.findings[0].blocking is False
    assert report.status == "pass"


def test_nested_evidence_revision_mismatch_cannot_be_rebound():
    text = "Mara arrived three days earlier than the ledger allowed."
    stale_nested = _finding(
        text,
        severity="critical",
        artifact_revision_id="revision-1",
        evidence=[
            {
                "artifact_revision_id": "revision-0",
                "quote": "three days earlier",
            }
        ],
    )

    report = QualityLab.evaluate_deterministic(
        _request(text, "revision-1"),
        candidate_text=text,
        continuity_findings=[stale_nested],
        created_at="2026-08-24T00:00:00+00:00",
    )

    assert report.findings[0].evidence == ()
    assert report.findings[0].blocking is False
    assert report.status == "pass"


def test_current_finding_without_exact_evidence_is_nonblocking():
    text = "Mara arrived today."
    current = Finding(
        severity="critical",
        category="timeline",
        message="The date may conflict.",
        suggestion="Check the calendar.",
        chapter=1,
    )

    report = QualityLab.evaluate_deterministic(
        _request(text),
        candidate_text=text,
        continuity_findings=[current],
        created_at="2026-08-24T00:00:00+00:00",
    )

    assert len(report.findings) == 1
    assert report.findings[0].evidence == ()
    assert report.findings[0].blocking is False
    assert report.status == "pass"


def test_duplicate_stable_claims_ignore_untrusted_blocking_flags():
    text = "Mara arrived three days earlier than the ledger allowed."
    first = _finding(text, blocking=False, evidence_verified=False)
    duplicate = _finding(text, blocking=True, evidence_verified=True)

    report = QualityLab.evaluate_deterministic(
        _request(text),
        candidate_text=text,
        continuity_findings=[first, duplicate],
        created_at="2026-08-24T00:00:00+00:00",
    )

    assert len(report.findings) == 1
    assert report.findings[0].blocking is True


def test_malformed_current_findings_are_omitted_without_inventing_claims():
    text = "Mara arrived today."

    report = QualityLab.evaluate_deterministic(
        _request(text),
        candidate_text=text,
        continuity_findings=[
            None,
            object(),
            {"severity": "urgent", "category": "timeline", "message": "Bad"},
            {"severity": "info", "category": "", "message": "Bad"},
        ],
        created_at="2026-08-24T00:00:00+00:00",
    )

    assert report.findings == ()
    assert report.status == "pass"


def test_top_level_quote_form_and_default_action_are_supported():
    text = "The gate was already open."

    report = QualityLab.evaluate_deterministic(
        _request(text),
        candidate_text=text,
        continuity_findings=[
            {
                "severity": "info",
                "category": "world_rule",
                "message": "The gate state changed.",
                "quote": "already open",
            }
        ],
        created_at="2026-08-24T00:00:00+00:00",
    )

    finding = report.findings[0]
    assert finding.severity == "info"
    assert finding.suggested_action == "Review this continuity finding."
    assert finding.evidence == (EvidenceSpan(_sha(text), quote="already open"),)
    assert finding.evidence_verification_result is True
    assert finding.blocking is False


def test_request_text_hash_mismatch_is_rejected():
    request = _request("Original candidate.")

    with pytest.raises(ValueError, match="candidate_text"):
        QualityLab.evaluate_deterministic(
            request,
            candidate_text="Different revision.",
            continuity_findings=[],
        )


def test_request_with_other_rubric_is_rejected():
    text = "Candidate."
    request = EvaluationRequest.for_text(1, text, rubric_version="quality.v2")

    with pytest.raises(ValueError, match="rubric"):
        QualityLab.evaluate_deterministic(
            request,
            candidate_text=text,
            continuity_findings=[],
        )


def test_report_for_another_revision_is_rejected():
    text = "Candidate."
    first_request = _request(text, "revision-1")
    second_request = _request(text, "revision-2")
    report = QualityLab.evaluate_deterministic(
        first_request,
        candidate_text=text,
        continuity_findings=[],
        created_at="2026-08-24T00:00:00+00:00",
    )

    with pytest.raises(ValueError, match="request"):
        QualityLab.validate_report_for_request(second_request, report)
    with pytest.raises(ValueError, match="evaluation_id"):
        replace(
            report,
            request=second_request,
            artifact_sha256=second_request.artifact_sha256,
        )


def test_explicit_deterministic_provenance_is_preserved():
    text = "Candidate."

    report = QualityLab.evaluate_deterministic(
        _request(text),
        candidate_text=text,
        continuity_findings=[],
        prompt_sha256="f" * 64,
        provider="local-checks",
        model="continuity-rules-2026-08",
        created_at="2026-08-24T00:00:00Z",
    )

    assert report.prompt_sha256 == "f" * 64
    assert report.evaluator_provider == "local-checks"
    assert report.evaluator_model == "continuity-rules-2026-08"
