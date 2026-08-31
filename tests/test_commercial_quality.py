from __future__ import annotations

from commercial_fixtures import (
    chapter_contract_v2,
    commercial_project_with_reports,
    commercial_story_fixture,
    guardian_reader_value_payload,
    span,
)
from commercial_quality import (
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
