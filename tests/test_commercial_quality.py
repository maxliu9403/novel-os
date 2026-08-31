from __future__ import annotations

from commercial_fixtures import chapter_contract_v2, commercial_story_fixture
from commercial_quality import validate_chapter_design


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
