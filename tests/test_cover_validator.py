from __future__ import annotations

from dataclasses import replace

from core.cover_director import CoverArtDirector
from core.cover_validator import validate_direction
from core.cover_novelty import plan_fingerprint
from tests.test_cover_director import (
    _brief, adaptive_director_fixture, director_fixture, v3_director_fixture,
)


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


def test_v3_validator_blocks_four_relabels_of_the_same_scene_and_missing_treatments() -> None:
    payload = director_fixture()
    payload["profile_version"] = "cover-profiles.v3"
    direction = CoverArtDirector.from_fixture(payload).plan(_brief(), count=4)

    findings = validate_direction(_brief(), direction)
    codes = {item.code for item in findings}

    assert "portfolio_treatment_mismatch" in codes
    assert "duplicate_scene_family" in codes
    assert "duplicate_story_beat" in codes


def test_v3_validator_accepts_four_assigned_expression_systems_and_distinct_beats() -> None:
    direction = CoverArtDirector.from_fixture(v3_director_fixture()).plan(_brief(), count=4)

    findings = validate_direction(_brief(), direction)

    assert findings == ()


def test_v3_validator_requires_each_approved_location_before_reusing_the_clinic() -> None:
    from core.cover_models_v2 import CoverBriefV2

    brief_payload = _brief().to_dict()
    brief_payload["lived_environment"]["primary_spaces"] = [
        "family kitchen", "public ceremony hall", "clinic corridor", "apartment threshold",
    ]
    brief = CoverBriefV2.from_dict(brief_payload, source_prompt_sha256="a" * 64)
    payload = v3_director_fixture()
    for plan in payload["plans"]:
        plan["location_family"] = "clinic corridor"
    repeated = CoverArtDirector.from_fixture(payload).plan(brief, count=4)

    findings = validate_direction(brief, repeated)

    assert "insufficient_location_diversity" in {item.code for item in findings}

    for plan, location in zip(
        payload["plans"], brief.lived_environment.primary_spaces, strict=True,
    ):
        plan["location_family"] = location
    varied = CoverArtDirector.from_fixture(payload).plan(brief, count=4)
    assert validate_direction(brief, varied) == ()


def test_v4_validator_accepts_book_specific_character_free_design_hypotheses() -> None:
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(
        _brief(), count=4,
    )

    assert validate_direction(_brief(), direction) == ()


def test_v4_validator_blocks_structurally_repeated_plans_without_prescribing_a_style() -> None:
    payload = adaptive_director_fixture()
    repeated = dict(payload["plans"][0])
    repeated.update({
        "concept_id": "concept-repeated",
        "visual_strategy": "renamed_strategy",
        "frozen_action": "same composition with a nominally different instant",
    })
    payload["plans"][1] = repeated
    direction = CoverArtDirector.from_fixture(payload).plan(_brief(), count=4)

    codes = {item.code for item in validate_direction(_brief(), direction)}

    assert "portfolio_similarity" in codes


def test_v4_validator_requires_source_bound_visual_identity_and_evidence() -> None:
    payload = adaptive_director_fixture()
    payload.pop("visual_identity")
    payload.pop("evidence_ledger")
    direction = CoverArtDirector.from_fixture(payload).plan(_brief(), count=4)

    codes = {item.code for item in validate_direction(_brief(), direction)}

    assert {"missing_visual_identity", "missing_evidence_ledger"} <= codes


def test_v4_validator_rejects_incomplete_visual_identity_language() -> None:
    payload = adaptive_director_fixture()
    payload["visual_identity"]["material_language"] = []
    direction = CoverArtDirector.from_fixture(payload).plan(_brief(), count=4)

    codes = {item.code for item in validate_direction(_brief(), direction)}

    assert "missing_visual_identity" in codes


def test_v4_accepts_designer_prose_for_single_visual_identity_dimensions() -> None:
    payload = adaptive_director_fixture()
    identity = payload["visual_identity"]
    identity["visual_grammar"] = "; ".join(identity["visual_grammar"])
    identity["material_language"] = "; ".join(identity["material_language"])
    identity["spoiler_boundary"] = identity["spoiler_boundary"][0]
    direction = CoverArtDirector.from_fixture(payload).plan(_brief(), count=4)

    codes = {item.code for item in validate_direction(_brief(), direction)}

    assert "missing_visual_identity" not in codes


def test_v4_validator_detects_a_near_duplicate_from_another_book() -> None:
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(
        _brief(), count=4,
    )
    recent = [
        plan_fingerprint(
            direction.plans[0], project_id="another-book", direction_id="direction-old",
        )
    ]

    findings = validate_direction(_brief(), direction, recent_fingerprints=recent)

    assert any(item.code == "historical_similarity" for item in findings)


def test_v4_validator_requires_visible_protagonist_anchor_in_every_plan() -> None:
    payload = adaptive_director_fixture()
    payload["plans"][1].update({
        "cast": [],
        "focal_character_id": "",
        "gaze_graph": [],
    })
    payload["plans"][2].update({
        "cast": ["char_oren"],
        "focal_character_id": "char_oren",
        "gaze_graph": ["char_oren -> doorway"],
    })
    direction = CoverArtDirector.from_fixture(payload).plan(_brief(), count=4)

    codes = {item.code for item in validate_direction(_brief(), direction)}

    assert "missing_human_anchor" in codes
    assert "missing_reader_anchor" in codes


def test_v4_validator_requires_scene_evidence_beyond_character_identity() -> None:
    payload = adaptive_director_fixture()
    payload["plans"][1]["story_evidence_refs"] = ["character:char_mara"]
    direction = CoverArtDirector.from_fixture(payload).plan(_brief(), count=4)

    codes = {item.code for item in validate_direction(_brief(), direction)}

    assert "missing_story_scene_evidence" in codes
