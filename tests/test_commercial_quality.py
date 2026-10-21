from __future__ import annotations

from commercial_fixtures import (
    chapter_contract_v2,
    commercial_project_with_reports,
    commercial_story_fixture,
    guardian_reader_value_payload,
    span,
)
from commercial_quality import (
    commercial_book_input_hashes,
    evaluate_commercial_book,
    evaluate_commercial_free_trial,
    review_commercial_chapter,
    validate_chapter_design,
    write_commercial_free_trial_report,
)


def test_commercial_report_requires_exact_candidate_quotes():
    text = "Megan signed the withdrawal. The lender froze the draw."
    payload = guardian_reader_value_payload(
        agency_span=span(text, "Megan signed the withdrawal."),
        payoff_span=span(text, "The lender froze the draw."),
        hook_span={
            "status": "present",
            "quote": "A quote not present in the candidate.",
            "start": 0,
            "end": 37,
        },
    )

    report = review_commercial_chapter(text, chapter_contract_v2(), payload, ())

    assert report.status == "blocked"
    assert "invalid_hook_evidence" in {item.code for item in report.blockers}


def test_commercial_report_reanchors_unique_exact_quote_with_wrong_offsets():
    text = "Megan signed the withdrawal. The lender froze the draw."
    payload = guardian_reader_value_payload(
        agency_span={
            "status": "present",
            "quote": "Megan signed the withdrawal.",
            "start": 7,
            "end": 35,
        }
    )

    report = review_commercial_chapter(text, chapter_contract_v2(), payload, ())

    assert report.status == "pass"
    assert report.blockers == ()


def test_commercial_report_rejects_ambiguous_quote_with_wrong_offsets():
    text = (
        "Megan signed the withdrawal. The lender froze the draw. "
        "The lender froze the draw."
    )
    payload = guardian_reader_value_payload(
        hook_span={
            "status": "present",
            "quote": "The lender froze the draw.",
            "start": 0,
            "end": 27,
        }
    )

    report = review_commercial_chapter(text, chapter_contract_v2(), payload, ())

    assert report.status == "blocked"
    assert "invalid_hook_evidence" in {item.code for item in report.blockers}


def test_commercial_finding_reanchors_unique_exact_quote_with_wrong_offsets():
    text = "Megan signed the withdrawal. The lender froze the draw."
    quote = "The lender froze the draw."
    payload = guardian_reader_value_payload(
        findings=[
            {
                "category": "micro_tension",
                "severity": "minor",
                "message": "The consequence could land more sharply.",
                "suggested_action": "Tighten the cited consequence.",
                "evidence": [
                    {
                        "quote": quote,
                        "start": 0,
                        "end": len(quote),
                    }
                ],
            }
        ]
    )

    report = review_commercial_chapter(text, chapter_contract_v2(), payload, ())

    assert report.status == "warning"
    assert len(report.quality_findings) == 1
    evidence = report.quality_findings[0].evidence[0]
    assert evidence.start == text.index(quote)
    assert evidence.end == evidence.start + len(quote)


def test_free_trial_beat_requires_exact_candidate_evidence():
    text = "Megan signed the withdrawal. The lender froze the draw."
    payload = guardian_reader_value_payload(
        free_trial_beats={
            "recognition_event": {
                "status": "present",
                "quote": "A missing sentence.",
                "start": 0,
                "end": 19,
            }
        }
    )

    report = review_commercial_chapter(
        text,
        chapter_contract_v2(),
        payload,
        (),
        expected_free_trial_beats=("recognition_event",),
    )

    assert report.status == "blocked"
    assert "invalid_free_trial_recognition_event_evidence" in {
        item.code for item in report.blockers
    }


def test_free_trial_review_requires_complete_micro_arc(tmp_path):
    project = commercial_project_with_reports(
        tmp_path, missing_chapter_3_payoff=True
    )

    report = evaluate_commercial_free_trial(project)

    assert report.status == "blocked"
    assert "free_trial_local_payoff_missing" in {
        item.code for item in report.blockers
    }


def test_book_review_requires_two_belonging_anchor_payoffs(tmp_path):
    project = commercial_project_with_reports(
        tmp_path, delivered_belonging=("self",)
    )
    free_trial = evaluate_commercial_free_trial(project)
    write_commercial_free_trial_report(project, free_trial)

    report = evaluate_commercial_book(project, chapter_count=12)

    assert "belonging_payoff_incomplete" in {
        item.code for item in report.blockers
    }


def test_book_input_hashes_validate_each_matching_receipt_once(
    tmp_path, monkeypatch
):
    from promotion import PromotionService

    project = commercial_project_with_reports(tmp_path)
    receipts = PromotionService(project).list_receipts()
    expected_ids = {
        receipt.chapter: receipt.receipt_id for receipt in receipts
    }
    load_calls = []
    real_load_receipt = PromotionService.load_receipt

    def counted_load_receipt(self, key, *, check_current_tail=True):
        load_calls.append((key, check_current_tail))
        return real_load_receipt(
            self, key, check_current_tail=check_current_tail
        )

    monkeypatch.setattr(PromotionService, "load_receipt", counted_load_receipt)

    hashes = commercial_book_input_hashes(project, chapter_count=12)

    assert load_calls == [
        (f"fixture-commercial-chapter-{number}", False)
        for number in range(1, 13)
    ]
    assert {
        number: hashes[f"chapter_{number:03d}_promotion_receipt_id"]
        for number in range(1, 13)
    } == expected_ids


def test_book_input_hashes_revalidate_tampered_durable_evidence(tmp_path):
    from artifacts import ArtifactStore

    project = commercial_project_with_reports(tmp_path)
    baseline = commercial_book_input_hashes(project, chapter_count=12)
    receipt_paths = sorted(
        (project / "outputs/state/promotion_receipts").glob("*.json")
    )

    receipt_path = receipt_paths[0]
    receipt_bytes = receipt_path.read_bytes()
    receipt_path.write_text("{}\n", encoding="utf-8")
    receipt_tampered = commercial_book_input_hashes(project, chapter_count=12)
    receipt_path.write_bytes(receipt_bytes)

    assert all(
        receipt_tampered[f"chapter_{number:03d}_promotion_receipt_id"] == ""
        for number in range(1, 13)
    )

    artifacts = ArtifactStore(project)
    chapter = 6
    final_head = artifacts.get_head(chapter, "final")
    assert final_head is not None
    final_revision = artifacts.get_revision(final_head.revision_id)
    blob_path = artifacts.blob_root / final_revision.sha256
    blob_bytes = blob_path.read_bytes()
    blob_path.write_text("tampered", encoding="utf-8")
    artifact_tampered = commercial_book_input_hashes(project, chapter_count=12)
    blob_path.write_bytes(blob_bytes)

    assert artifact_tampered[
        f"chapter_{chapter:03d}_promotion_receipt_id"
    ] == ""
    assert artifact_tampered[f"chapter_{chapter:03d}_final_sha256"] == (
        final_revision.sha256
    )
    assert artifact_tampered["chapter_005_promotion_receipt_id"] == (
        baseline["chapter_005_promotion_receipt_id"]
    )

    ledger_path = project / "outputs/state/canon_ledger.jsonl"
    ledger_bytes = ledger_path.read_bytes()
    ledger_path.write_bytes(ledger_bytes + b"not-json\n")
    canon_tampered = commercial_book_input_hashes(project, chapter_count=12)
    ledger_path.write_bytes(ledger_bytes)

    assert all(
        canon_tampered[f"chapter_{number:03d}_promotion_receipt_id"] == ""
        for number in range(1, 13)
    )
    assert commercial_book_input_hashes(project, chapter_count=12) == baseline


def test_design_gate_blocks_unseeded_resource_and_passive_turn():
    contract = chapter_contract_v2(
        used_resource_ids=("license_record",),
        seeded_resource_ids=(),
        protagonist_causes_turn=False,
    )

    report = validate_chapter_design(contract, (), commercial_story_fixture())

    assert {item.code for item in report.blockers} == {
        "unseeded_resource",
        "protagonist_does_not_cause_turn",
    }
    unseeded = next(
        item for item in report.blockers if item.code == "unseeded_resource"
    )
    assert "license_record" in unseeded.message


def test_design_gate_blocks_repeated_hook_and_third_humiliation_scene():
    prior = (
        chapter_contract_v2(chapter=1, hook_type="arrival", humiliation_scene=True),
        chapter_contract_v2(chapter=2, hook_type="arrival", humiliation_scene=True),
    )
    current = chapter_contract_v2(
        chapter=3, hook_type="arrival", humiliation_scene=True
    )

    report = validate_chapter_design(current, prior, commercial_story_fixture())

    assert "identical_hook_budget" in {item.code for item in report.blockers}
    assert "humiliation_budget" in {item.code for item in report.blockers}


def test_design_gate_requires_belonging_job_for_declared_anchor():
    contract = chapter_contract_v2(belonging_anchors=("child",), reader_jobs=("anger",))

    report = validate_chapter_design(contract, (), commercial_story_fixture())

    assert "anchor_without_belonging_job" in {item.code for item in report.blockers}


def test_design_gate_requires_anchor_when_belonging_is_declared():
    contract = chapter_contract_v2(reader_jobs=("recognition", "belonging"))

    report = validate_chapter_design(contract, (), commercial_story_fixture())

    assert "belonging_job_without_anchor" in {item.code for item in report.blockers}


def test_design_gate_passes_seeded_protagonist_turn():
    contract = chapter_contract_v2()

    report = validate_chapter_design(contract, (), commercial_story_fixture())

    assert report.status == "pass"
    assert report.blockers == ()
