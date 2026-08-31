from __future__ import annotations

from commercial_fixtures import (
    chapter_contract_v2,
    commercial_story_fixture,
    guardian_reader_value_payload,
    span,
)
from commercial_quality import review_commercial_chapter, validate_chapter_design


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
