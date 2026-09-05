from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from commercial_fixtures import (
    commercial_story_fixture,
    commercial_story_fixture_variant,
    high_overlap_candidate,
    high_overlap_reference,
)
from story_originality import (
    CATALOG_PATH,
    OriginalityReferenceSet,
    StoryFingerprint,
    compare_fingerprints,
    evaluate_story_originality,
)


def test_exact_structural_signature_blocks_without_storing_source_prose():
    candidate = StoryFingerprint.from_contract(commercial_story_fixture())

    report = compare_fingerprints(candidate, [candidate])

    assert report.status == "blocked"
    assert report.findings[0].code == "duplicate_story_signature"
    serialized = json.dumps(report.to_dict()).casefold()
    assert "dialogue" not in serialized
    assert "protagonist_desire_beyond_escape" not in serialized


def test_shared_genre_with_different_mechanism_passes():
    first = StoryFingerprint.from_contract(commercial_story_fixture())
    second = StoryFingerprint.from_contract(commercial_story_fixture_variant())

    report = compare_fingerprints(first, [second])

    assert report.status == "pass"
    assert report.findings == ()


def test_high_dimension_and_action_sequence_overlap_blocks():
    candidate = StoryFingerprint.from_contract(high_overlap_candidate())
    reference = StoryFingerprint.from_contract(high_overlap_reference())

    report = compare_fingerprints(candidate, [reference])

    assert report.dimension_similarity >= 0.84
    assert report.action_sequence_similarity == 0.75
    assert report.status == "blocked"
    assert report.findings[0].code == "high_structure_and_action_overlap"


def test_high_dimensions_without_ordered_action_overlap_warns():
    candidate = StoryFingerprint.from_contract(high_overlap_candidate())
    reference = replace(
        StoryFingerprint.from_contract(high_overlap_reference()),
        action_sequence=("recognize", "document", "protect", "leave"),
    )

    report = compare_fingerprints(candidate, [reference])

    assert report.dimension_similarity >= 0.84
    assert report.action_sequence_similarity < 0.75
    assert report.status == "warning"
    assert report.findings[0].code == "high_dimension_overlap"


def test_reference_set_is_order_independent_and_hash_bound():
    first = StoryFingerprint.from_contract(commercial_story_fixture())
    second = StoryFingerprint.from_contract(commercial_story_fixture_variant())

    left = OriginalityReferenceSet.capture([second, first])
    right = OriginalityReferenceSet.capture([first, second])

    assert left.to_dict() == right.to_dict()
    assert left.reference_set_sha256 == right.reference_set_sha256
    assert left.reference_ids == tuple(sorted(left.reference_ids))


def test_evaluation_writes_abstract_bound_artifacts(tmp_path: Path):
    project = tmp_path / "projects" / "candidate"
    foundation_path = project / "outputs/input/foundation.json"
    foundation_path.parent.mkdir(parents=True)
    contract = commercial_story_fixture_variant()
    foundation_path.write_text(
        json.dumps(
            {
                "title": "A New Story",
                "premise": "Story-specific prose stays outside the fingerprint.",
                "commercial_story_contract": contract.to_dict(),
                "commercial_story_contract_id": contract.contract_id,
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_story_originality(project, contract)

    fingerprint_path = project / "outputs/input/story-fingerprint.json"
    report_path = project / "outputs/quality/story-originality-report.json"
    assert report.status == "pass"
    assert fingerprint_path.is_file()
    assert report_path.is_file()
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["foundation_sha256"] == hashlib.sha256(
        foundation_path.read_bytes()
    ).hexdigest()
    assert persisted["catalog_sha256"] == hashlib.sha256(
        CATALOG_PATH.read_bytes()
    ).hexdigest()
    combined = (fingerprint_path.read_text() + report_path.read_text()).casefold()
    for forbidden in ("a new story", "story-specific prose", "dialogue", "/projects/"):
        assert forbidden not in combined


def test_catalog_contains_only_abstract_pattern_fields():
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    allowed = {
        "pattern_id",
        "protagonist_life_stage",
        "invisible_labor",
        "sacred_asset",
        "boundary_transfer",
        "beneficiary_role",
        "proof_type",
        "deadline_type",
        "agency_source",
        "action_cost",
        "belonging_anchors",
        "relationship_shape",
        "action_sequence",
        "source_count",
        "risk_note",
    }
    assert payload["schema_version"] == 1
    assert len(payload["patterns"]) == 3
    assert all(set(pattern) == allowed for pattern in payload["patterns"])
    serialized = json.dumps(payload).casefold()
    for forbidden in ("title", "name", "path", "quote", "summary", "dialogue"):
        assert forbidden not in serialized
