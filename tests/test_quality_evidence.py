"""Tests for artifact-bound quality evidence and evaluation reports."""

import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from quality import (
    EvidenceSpan,
    EvaluationReport,
    EvaluationRequest,
    QualityFinding,
    verify_evidence,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _finding(text: str, **overrides) -> QualityFinding:
    artifact_sha = _sha(text)
    values = {
        "artifact_sha256": artifact_sha,
        "category": "causality",
        "severity": "major",
        "message": "The consequence is unclear",
        "evidence": (EvidenceSpan(artifact_sha, text, None, None),),
        "suggested_action": "Show the immediate consequence",
        "repair_class": "chapter_structure",
    }
    values.update(overrides)
    return QualityFinding.create(**values)


def _request(text: str = "Maren signed the agreement.") -> EvaluationRequest:
    return EvaluationRequest.for_text(
        3,
        text,
        artifact_revision_id="revision-3",
        story_contract_id="contract:story",
        chapter_contract_id="contract:chapter-3",
        rubric_version="quality-rubric-v1",
    )


def _report(**overrides) -> EvaluationReport:
    text = "Maren signed the agreement."
    request = _request(text)
    finding = _finding(text).with_verified_evidence(text, request.artifact_sha256)
    values = {
        "artifact_sha256": request.artifact_sha256,
        "evaluation_id": request.evaluation_id,
        "rubric_version": request.rubric_version,
        "prompt_sha256": "c" * 64,
        "evaluator_provider": "fixture",
        "evaluator_model": "deterministic-v1",
        "hard_gates": {"continuity": True, "causal_chain": False},
        "semantic_dimensions": {"causality": 8.5, "voice": 7},
        "findings": (finding,),
        "status": "needs_repair",
        "created_at": "2026-08-24T10:15:00+00:00",
    }
    values.update(overrides)
    return EvaluationReport(**values)


def test_quote_from_another_revision_cannot_block_candidate():
    text = "Maren signed the agreement."
    evidence = EvidenceSpan(artifact_sha256="b" * 64, quote=text, start=None, end=None)
    actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert verify_evidence(text, actual_sha, evidence) is False


def test_invalid_evidence_downgrades_blocking_finding():
    text = "Actual text"
    actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    finding = QualityFinding.create(
        artifact_sha256=actual_sha,
        category="causality",
        severity="critical",
        message="Choice is missing",
        evidence=(EvidenceSpan(actual_sha, "not present", None, None),),
        suggested_action="Show the decision",
        repair_class="chapter_structure",
    )
    assert finding.with_verified_evidence(text, actual_sha).blocking is False


def test_quote_only_evidence_verifies_exact_occurrence():
    text = "First choice. Second choice."
    evidence = EvidenceSpan(_sha(text), "Second choice.")

    assert verify_evidence(text, _sha(text), evidence) is True
    assert (
        verify_evidence(text, _sha(text), EvidenceSpan(_sha(text), "second choice."))
        is False
    )


def test_span_only_evidence_uses_python_unicode_character_offsets():
    text = "Mara chose 雪 before dawn."
    start = text.index("雪")
    evidence = EvidenceSpan(_sha(text), start=start, end=start + 1)

    assert text[evidence.start : evidence.end] == "雪"
    assert verify_evidence(text, _sha(text), evidence) is True


def test_quote_and_span_must_identify_the_same_exact_text():
    text = "Mara chose the north door."
    start = text.index("north")

    assert verify_evidence(
        text,
        _sha(text),
        EvidenceSpan(_sha(text), "north", start, start + len("north")),
    ) is True
    assert verify_evidence(
        text,
        _sha(text),
        EvidenceSpan(_sha(text), "south", start, start + len("north")),
    ) is False


def test_evidence_mismatch_and_out_of_range_return_false():
    text = "short"

    assert verify_evidence(text, "f" * 64, EvidenceSpan(_sha(text), text)) is False
    assert (
        verify_evidence(
            text, _sha(text), EvidenceSpan(_sha(text), start=0, end=99)
        )
        is False
    )


@pytest.mark.parametrize("bad_sha", ["a" * 63, "A" * 64, "z" * 64, True, None])
def test_evidence_rejects_malformed_sha(bad_sha):
    with pytest.raises(ValueError, match="artifact_sha256"):
        EvidenceSpan(bad_sha, "quote")


@pytest.mark.parametrize(
    ("quote", "start", "end"),
    [
        (None, None, None),
        ("   ", None, None),
        (None, 0, None),
        (None, None, 1),
        (None, -1, 1),
        (None, 1, 1),
        (None, 2, 1),
        (None, True, 2),
        (None, 0, False),
    ],
)
def test_evidence_rejects_missing_or_invalid_locations(quote, start, end):
    with pytest.raises(ValueError):
        EvidenceSpan("a" * 64, quote, start, end)


def test_evidence_round_trips_and_rejects_malformed_payloads():
    evidence = EvidenceSpan("a" * 64, "雪", 4, 5)
    payload = json.loads(json.dumps(evidence.to_dict()))

    assert EvidenceSpan.from_dict(payload) == evidence
    with pytest.raises(ValueError, match="fields"):
        EvidenceSpan.from_dict({**payload, "unexpected": True})
    with pytest.raises(ValueError, match="schema_version"):
        EvidenceSpan.from_dict({**payload, "schema_version": True})


def test_blocking_requires_major_or_critical_verified_evidence():
    text = "The door locked."
    major = _finding(text)
    minor = _finding(text, severity="minor")

    assert major.blocking is False
    assert major.with_verified_evidence(text, _sha(text)).blocking is True
    assert minor.with_verified_evidence(text, _sha(text)).blocking is False


def test_direct_construction_cannot_claim_evidence_was_verified():
    text = "The door locked."
    artifact_sha = _sha(text)

    with pytest.raises(ValueError, match="with_verified_evidence"):
        QualityFinding(
            artifact_sha256=artifact_sha,
            category="causality",
            severity="critical",
            message="The consequence is unclear",
            evidence=(EvidenceSpan(artifact_sha, text),),
            suggested_action="Show the consequence",
            repair_class="chapter_structure",
            evidence_verified=True,
        )


def test_empty_finding_evidence_is_auditable_but_never_blocking():
    text = "No cited passage."
    finding = _finding(text, severity="critical", evidence=())
    checked = finding.with_verified_evidence(text, _sha(text))

    assert checked.evidence == ()
    assert checked.evidence_verified is False
    assert checked.blocking is False


def test_finding_rejects_evidence_from_a_different_artifact():
    with pytest.raises(ValueError, match="artifact"):
        _finding(
            "Current text",
            evidence=(EvidenceSpan("b" * 64, "Old text"),),
        )


@pytest.mark.parametrize("field", ["category", "message", "suggested_action"])
def test_finding_rejects_blank_text_fields(field):
    with pytest.raises(ValueError, match=field):
        _finding("Text", **{field: "  "})


@pytest.mark.parametrize("severity", ["blocker", "CRITICAL", "", None])
def test_finding_rejects_invalid_severity(severity):
    with pytest.raises(ValueError, match="severity"):
        _finding("Text", severity=severity)


@pytest.mark.parametrize("repair_class", ["rewrite_everything", "", None])
def test_finding_rejects_unsupported_repair_class(repair_class):
    with pytest.raises(ValueError, match="repair_class"):
        _finding("Text", repair_class=repair_class)


def test_finding_round_trip_preserves_stable_id_and_verification_state():
    text = "The door locked."
    finding = _finding(text).with_verified_evidence(text, _sha(text))
    payload = json.loads(json.dumps(finding.to_dict()))
    restored = QualityFinding.from_dict(payload)

    assert restored == finding
    assert restored.finding_id == finding.finding_id
    assert restored.finding_id.startswith("finding-")
    with pytest.raises(FrozenInstanceError):
        finding.severity = "minor"
    with pytest.raises(FrozenInstanceError):
        finding.evidence += (EvidenceSpan(_sha(text), text),)


def test_finding_from_dict_detects_id_and_nested_evidence_tampering():
    finding = _finding("The door locked.")
    payload = finding.to_dict()

    with pytest.raises(ValueError, match="finding_id"):
        QualityFinding.from_dict({**payload, "evidence_verified": True})

    nested_tamper = json.loads(json.dumps(payload))
    nested_tamper["evidence"][0]["quote"] = "changed"
    with pytest.raises(ValueError, match="finding_id"):
        QualityFinding.from_dict(nested_tamper)


def test_request_for_text_binds_hash_without_serializing_manuscript():
    text = "雪 fell across the threshold."
    request = _request(text)
    payload = request.to_dict()

    assert request.artifact_sha256 == _sha(text)
    assert request.verify_text(text) is True
    assert request.verify_text(text + " Changed.") is False
    assert "text" not in payload
    assert text not in json.dumps(payload, ensure_ascii=False)


def test_request_for_text_rejects_a_supplied_hash_for_other_bytes():
    with pytest.raises(ValueError, match="artifact_sha256"):
        EvaluationRequest.for_text(1, "candidate", artifact_sha256="b" * 64)


def test_request_round_trip_has_stable_identity_and_backward_safe_context_defaults():
    request = EvaluationRequest.for_text(1, "candidate")
    restored = EvaluationRequest.from_dict(json.loads(json.dumps(request.to_dict())))

    assert restored == request
    assert restored.evaluation_id == request.evaluation_id
    assert restored.request_id == request.evaluation_id
    assert restored.artifact_revision_id == ""
    assert restored.story_contract_id == ""
    assert restored.chapter_contract_id == ""


@pytest.mark.parametrize("chapter", [0, -1, True, 1.0])
def test_request_rejects_invalid_chapter(chapter):
    with pytest.raises(ValueError, match="chapter"):
        EvaluationRequest.for_text(chapter, "candidate")


def test_request_from_dict_detects_identity_tamper_and_bad_schema():
    payload = _request().to_dict()

    with pytest.raises(ValueError, match="evaluation_id"):
        EvaluationRequest.from_dict({**payload, "chapter": 4})
    with pytest.raises(ValueError, match="schema_version"):
        EvaluationRequest.from_dict({**payload, "schema_version": True})


def test_report_round_trips_nested_models_without_loss_and_is_deeply_immutable():
    report = _report()
    restored = EvaluationReport.from_dict(json.loads(json.dumps(report.to_dict())))

    assert restored == report
    assert restored.report_id == report.report_id
    assert restored.report_id.startswith("report-")
    assert restored.findings[0].evidence[0].quote == "Maren signed the agreement."
    with pytest.raises(TypeError):
        report.hard_gates["continuity"] = False
    with pytest.raises(TypeError):
        report.semantic_dimensions["voice"] = 2
    with pytest.raises(FrozenInstanceError):
        report.status = "pass"


def test_equivalent_reports_have_stable_ids_and_no_aggregate_score():
    first = _report()
    second = EvaluationReport.from_dict(first.to_dict())

    assert first.report_id == second.report_id
    assert "score" not in first.to_dict()
    assert "aggregate_score" not in first.to_dict()


@pytest.mark.parametrize("status", ["approved", "unknown", "", None])
def test_report_rejects_invalid_status(status):
    with pytest.raises(ValueError, match="status"):
        _report(status=status)


@pytest.mark.parametrize("value", [-0.1, 10.1, True, float("nan"), float("inf"), "8"])
def test_report_rejects_invalid_semantic_dimension_values(value):
    with pytest.raises(ValueError, match="semantic_dimensions"):
        _report(semantic_dimensions={"causality": value})


@pytest.mark.parametrize("value", [0, 1, "true", None, [], {}])
def test_report_rejects_non_boolean_hard_gate_values(value):
    with pytest.raises(ValueError, match="hard_gates"):
        _report(hard_gates={"continuity": value})


def test_report_rejects_findings_bound_to_another_artifact():
    other = _finding("Different artifact")

    with pytest.raises(ValueError, match="artifact"):
        _report(findings=(other,))


@pytest.mark.parametrize("field", ["artifact_sha256", "prompt_sha256"])
def test_report_rejects_malformed_sha_fields(field):
    with pytest.raises(ValueError, match=field):
        _report(**{field: "A" * 64})


def test_report_from_dict_detects_root_and_nested_tampering():
    report = _report()
    payload = report.to_dict()

    with pytest.raises(ValueError, match="report_id"):
        EvaluationReport.from_dict({**payload, "status": "pass"})

    malformed_nested = json.loads(json.dumps(payload))
    malformed_nested["findings"][0]["evidence"][0]["quote"] = "not the same"
    with pytest.raises(ValueError):
        EvaluationReport.from_dict(malformed_nested)


@pytest.mark.parametrize("schema_version", [True, "1", 0, 2])
def test_all_models_reject_invalid_schema_versions(schema_version):
    text = "Text"
    finding = _finding(text)
    request = _request(text)
    report = _report()

    constructors = (
        lambda: EvidenceSpan(_sha(text), text, schema_version=schema_version),
        lambda: QualityFinding.create(
            artifact_sha256=_sha(text),
            category="causality",
            severity="major",
            message="Problem",
            evidence=(EvidenceSpan(_sha(text), text),),
            suggested_action="Fix it",
            repair_class="continuity",
            schema_version=schema_version,
        ),
        lambda: EvaluationRequest(
            chapter=request.chapter,
            artifact_sha256=request.artifact_sha256,
            schema_version=schema_version,
        ),
        lambda: EvaluationReport(
            artifact_sha256=report.artifact_sha256,
            evaluation_id=report.evaluation_id,
            rubric_version=report.rubric_version,
            prompt_sha256=report.prompt_sha256,
            evaluator_provider=report.evaluator_provider,
            evaluator_model=report.evaluator_model,
            hard_gates=report.hard_gates,
            semantic_dimensions=report.semantic_dimensions,
            findings=(finding,),
            status=report.status,
            created_at=report.created_at,
            schema_version=schema_version,
        ),
    )

    for construct in constructors:
        with pytest.raises(ValueError, match="schema_version"):
            construct()
