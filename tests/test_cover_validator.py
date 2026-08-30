from __future__ import annotations

from dataclasses import replace

from core.cover_director import CoverArtDirector
from core.cover_validator import validate_direction
from tests.test_cover_director import _brief, director_fixture


def test_validator_accepts_fixture_direction_with_resolvable_evidence() -> None:
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(_brief(), count=4)

    findings = validate_direction(_brief(), direction)

    assert findings == ()


def test_validator_blocks_unknown_evidence_and_identity_invention() -> None:
    payload = director_fixture()
    payload["plans"][0]["story_evidence_refs"] = ["node:unknown"]
    payload["plans"][1]["frozen_action"] = "the American white woman becomes twenty years old"
    direction = CoverArtDirector.from_fixture(payload).plan(_brief(), count=4)

    findings = validate_direction(_brief(), direction)

    assert {item.code for item in findings} >= {"unknown_evidence", "identity_invention"}


def test_validator_blocks_pending_critical_visual_assumption() -> None:
    brief = _brief()
    payload = director_fixture()
    payload["visual_assumptions"] = [{
        "field": "char_mara.age",
        "proposed_value": "mid-thirties",
        "reason": "age is missing from approved story facts",
        "status": "pending_confirmation",
        "critical": True,
    }]
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4)

    findings = validate_direction(brief, direction)

    assert "unresolved_critical_assumption" in {item.code for item in findings}


def test_validator_requires_all_four_characters_in_same_scene_with_depth_blocking() -> None:
    brief = _brief(4)
    payload = director_fixture()
    payload["plans"] = [
        {**payload["plans"][0], "cast": ["char_mara", "char_oren", "char_3", "char_4"], "blocking": "four people in a row"}
        for _ in range(4)
    ]
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4)

    findings = validate_direction(brief, direction)

    assert any(item.code == "group_blocking" for item in findings)


def test_validator_detects_brief_hash_drift() -> None:
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(_brief(), count=4)
    changed = replace(direction, brief_sha256="b" * 64)

    findings = validate_direction(_brief(), changed)

    assert any(item.code == "brief_hash_mismatch" for item in findings)
