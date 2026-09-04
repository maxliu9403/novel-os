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


def test_validator_requires_layered_blocking_for_four_approved_cast_even_if_only_two_are_required() -> None:
    payload = director_fixture()
    brief_payload = _brief().to_dict()
    for character_id in ("char_pressure_a", "char_pressure_b"):
        character = dict(brief_payload["principal_characters"][1])
        character.update({
            "character_id": character_id,
            "name": character_id,
            "must_appear": False,
            "relationships": ["char_mara"],
        })
        brief_payload["principal_characters"].append(character)
    from core.cover_models_v2 import CoverBriefV2
    brief = CoverBriefV2.from_dict(brief_payload, source_prompt_sha256="a" * 64)
    payload["plans"] = [
        {
            **plan,
            "cast": ["char_mara", "char_oren", "char_pressure_a", "char_pressure_b"],
            "blocking": "four approved characters in a flat row",
        }
        for plan in payload["plans"]
    ]
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4)

    findings = validate_direction(brief, direction)

    assert any(item.code == "group_blocking" for item in findings)


def test_validator_accepts_four_character_depth_declared_in_depth_plan() -> None:
    brief = _brief(4)
    payload = director_fixture()
    payload["plans"] = [
        {
            **plan,
            "cast": ["char_mara", "char_oren", "char_3", "char_4"],
            "blocking": "Mara is nearest camera while the other three remain behind her",
            "depth_plan": (
                "Mara stays sharp in the foreground; the three-person causal group "
                "remains readable in the background"
            ),
        }
        for plan in payload["plans"]
    ]
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4)

    findings = validate_direction(brief, direction)

    assert "group_blocking" not in {item.code for item in findings}


def test_validator_requires_one_causal_tableau_when_optional_conflict_cast_is_approved() -> None:
    brief_payload = _brief().to_dict()
    optional = dict(brief_payload["principal_characters"][1])
    optional.update({
        "character_id": "char_pressure",
        "name": "Pressure Character",
        "must_appear": False,
        "relationships": ["char_mara"],
    })
    brief_payload["principal_characters"].append(optional)
    from core.cover_models_v2 import CoverBriefV2
    brief = CoverBriefV2.from_dict(brief_payload, source_prompt_sha256="a" * 64)
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4)

    findings = validate_direction(brief, direction)

    assert any(item.code == "missing_causal_conflict_tableau" for item in findings)


def test_validator_accepts_optional_conflict_cast_in_one_layered_plan_only() -> None:
    brief_payload = _brief().to_dict()
    optional = dict(brief_payload["principal_characters"][1])
    optional.update({
        "character_id": "char_pressure",
        "name": "Pressure Character",
        "must_appear": False,
        "relationships": ["char_mara"],
    })
    brief_payload["principal_characters"].append(optional)
    from core.cover_models_v2 import CoverBriefV2
    brief = CoverBriefV2.from_dict(brief_payload, source_prompt_sha256="a" * 64)
    payload = director_fixture()
    payload["plans"][0] = {
        **payload["plans"][0],
        "cast": ["char_mara", "char_oren", "char_pressure"],
        "story_evidence_refs": [
            "character:char_mara", "character:char_oren",
            "character:char_pressure", "node:door_choice",
        ],
        "blocking": (
            "char_mara carries the emotional consequence in the foreground while "
            "char_oren and char_pressure reveal the causal alignment in the background"
        ),
    }
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4)

    findings = validate_direction(brief, direction)

    assert "missing_causal_conflict_tableau" not in {item.code for item in findings}


def test_validator_detects_brief_hash_drift() -> None:
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(_brief(), count=4)
    changed = replace(direction, brief_sha256="b" * 64)

    findings = validate_direction(_brief(), changed)

    assert any(item.code == "brief_hash_mismatch" for item in findings)
