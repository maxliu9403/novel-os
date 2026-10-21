from __future__ import annotations

import pytest

from core.cover_models_v2 import CoreConflictVisualContract, CoverBriefV2


def _character(character_id: str, *, age: int | None = 34, age_band: str = "") -> dict:
    return {
        "character_id": character_id,
        "name": character_id.replace("_", " ").title(),
        "narrative_role": "co_protagonist",
        "must_appear": True,
        "age": age,
        "age_band": age_band,
        "gender_presentation": "woman",
        "physical_identity": "tired eyes and natural skin texture",
        "occupation_and_status": "hospital administrator with stable income",
        "daily_wardrobe": "repeated-use office clothing",
        "lived_environment": "modest apartment kitchen",
        "current_emotional_state": "controlled hurt becoming resolve",
        "agency_signal": "holds the shared brass key",
        "relationships": ["char_oren"],
    }


def two_character_fixture() -> dict:
    return {
        "schema_version": 2,
        "title": "The Door Is Mine",
        "author": "",
        "language": "English",
        "genre": "contemporary romance",
        "target_audience": "women 30-45 seeking relationship drama",
        "market_scope": "United States mobile serialized fiction",
        "core_task": "A partner protects her child and claims a home.",
        "core_conflict": "A damaged relationship threatens the family's home.",
        "emotional_promise": "intimacy mixed with uncertainty",
        "principal_characters": [
            _character("char_mara"),
            {**_character("char_oren", age=None, age_band="late thirties"), "gender_presentation": "man"},
        ],
        "relationship_map": [{
            "from_character_id": "char_mara",
            "to_character_id": "char_oren",
            "relationship": "estranged spouses",
            "power_balance": "he controls public status; she controls the evidence",
            "visible_tension": "he avoids eye contact while she holds the key",
            "shared_risk": "their child witnesses the rupture",
        }],
        "lived_environment": {
            "era": "present day",
            "fictional_place": "invented North American city",
            "primary_spaces": ["lived-in apartment entry"],
            "economic_signals": ["repaired dining chair", "practical school backpack"],
            "cultural_signals": ["confirmed domestic routines"],
            "weather_and_season": "late autumn evening",
            "environment_truths": ["the home is actively lived in"],
        },
        "decisive_story_nodes": [{
            "node_id": "door_choice",
            "description": "She removes the shared key before closing the apartment door.",
            "evidence_refs": ["character:char_mara", "character:char_oren"],
        }],
        "secondary_signals": [{
            "signal_id": "child_backpack",
            "description": "a school backpack near the doorway",
            "story_function": "show the child witnesses the rupture",
        }],
        "genre_emotion_profile": {
            "primary_genre": "romance",
            "submode": "reconciliation",
            "emotional_temperature": "warm but restrained",
            "desired_viewer_feeling": "intimacy mixed with uncertainty",
            "relationship_motion": "about to move closer but still blocked",
            "prohibited_shortcuts": ["generic smiling couple", "wedding imagery"],
        },
        "commercial_visual_goal": {
            "market": "United States",
            "audience_segment": "women 30-45 interested in relationship drama",
            "display_context": "mobile_thumbnail",
            "thumbnail_reference_width": 120,
            "thumbnail_reference_height": 180,
            "first_glance_priority": "relationship rupture",
            "reader_identification": "a partner protecting herself and her child",
            "truthful_story_promise": "the protagonist recognizes the betrayal and acts",
        },
        "title_direction": {
            "hierarchy": "large",
            "preferred_zone": "upper third",
            "readability": "mobile_thumbnail",
        },
        "forbidden_elements": ["real landmarks", "logos", "watermarks"],
        "visual_assumptions": [],
    }


def test_v2_round_trip_preserves_required_characters_and_visual_goal() -> None:
    brief = CoverBriefV2.from_dict(two_character_fixture(), source_prompt_sha256="a" * 64)

    restored = CoverBriefV2.from_dict(
        brief.to_dict(), source_prompt_sha256="a" * 64,
    )

    assert restored == brief
    assert [item.character_id for item in brief.principal_characters] == [
        "char_mara", "char_oren",
    ]
    assert brief.commercial_visual_goal.display_context == "mobile_thumbnail"


@pytest.mark.parametrize("agency", ["", None, "  "])
def test_optional_registered_character_may_have_no_source_agency(agency) -> None:
    payload = two_character_fixture()
    recipient = {
        **_character("char_leo", age=20),
        "narrative_role": "secondary recipient",
        "must_appear": False,
        "agency_signal": agency,
    }
    payload["principal_characters"].append(recipient)

    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)
    restored = CoverBriefV2.from_dict(brief.to_dict(), source_prompt_sha256="a" * 64)

    assert restored == brief
    assert restored.principal_characters[-1].character_id == "char_leo"
    assert restored.principal_characters[-1].agency_signal == ""
    assert "char_leo" not in {item.character_id for item in restored.required_characters}


@pytest.mark.parametrize("explicit_required", [False, True])
@pytest.mark.parametrize("allow_pending", [False, True])
def test_required_character_still_needs_source_agency(explicit_required, allow_pending) -> None:
    payload = two_character_fixture()
    protagonist = payload["principal_characters"][0]
    protagonist.pop("agency_signal")
    if not explicit_required:
        protagonist.pop("must_appear")

    with pytest.raises(ValueError, match=r"principal_characters\[0\]\.agency_signal is required"):
        CoverBriefV2.from_dict(
            payload, source_prompt_sha256="a" * 64,
            allow_pending_required_facts=allow_pending,
        )


def test_optional_character_without_agency_survives_frozen_cover_workflow(tmp_path) -> None:
    from dataclasses import replace

    from core.cover_analysis import CoverStoryAnalyzer
    from core.cover_sources import CoverSourceSelector, capture_source
    from core.cover_store import CoverStore
    from core.cover_workflow import CoverWorkflow
    from tests.test_cover_analysis_snapshot import source
    from tests.test_cover_workflow import FullStoryFixture, approve

    context, _, project = source.__wrapped__(tmp_path)
    prompt = project / "outputs/input/prompt.md"
    prompt.write_text(
        prompt.read_text(encoding="utf-8")
        + "\n## Other registered people\nLeo, 20, is a secondary recipient. No action is specified.\n",
        encoding="utf-8",
    )
    store = CoverStore(project)
    context = replace(context, cover_root=store.root)
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    director = FullStoryFixture()

    def retain_recipient(request, result):
        result = director._with_events(request, result)
        if request["phase"] == "global":
            recipient = {
                **_character("char_leo", age=20),
                "name": "Leo", "narrative_role": "secondary recipient",
                "must_appear": False, "relationships": [],
            }
            recipient.pop("agency_signal")
            result["brief"]["principal_characters"].append(recipient)
            result["characters"].append({
                "character_id": "char_leo", "name": "Leo",
                "narrative_role": "secondary recipient",
                "evidence_refs": result["story"]["evidence_refs"],
            })
        return result

    director.extractor.mutate = retain_recipient
    analysis = CoverStoryAnalyzer(
        complete=director._complete, model=director.model, max_repair_attempts=0,
    ).analyze(context, snapshot)
    flow = CoverWorkflow(context, store)
    direction = approve(flow, flow.plan(analysis.analysis_id, director))
    brief, concepts, arguments = flow.generation_arguments(
        direction.direction_id, direction.direction_sha256,
    )

    assert len(analysis.characters) == len(brief.principal_characters) == 3
    assert brief.principal_characters[-1].agency_signal == ""
    assert all("char_leo" not in plan.cast for plan in direction.plans)
    assert all("char_leo" not in concept.generation_prompt for concept in concepts)
    assert arguments["source_binding"]["analysis_id"] == analysis.analysis_id


def test_v2_round_trip_preserves_story_facing_cover_constraints() -> None:
    payload = two_character_fixture()
    payload["cover_constraints"] = {
        "constraint_version": "cover-constraints.v1",
        "must_preserve_story_facts": ["estranged spouses", "the shared home is at stake"],
        "must_show_if_supported": ["door_choice"],
        "must_not_show": ["final reconciliation", "unsupported ethnicity"],
        "spoiler_ceiling": "Do not reveal the final relationship outcome.",
        "user_visual_preferences": ["restrained live-action photography"],
        "preferred_story_node_ids": ["door_choice"],
        "preferred_character_ids": ["char_mara"],
        "source_refs": ["section:6.relationship_map", "section:9.node:door_choice"],
    }

    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)
    restored = CoverBriefV2.from_dict(brief.to_dict(), source_prompt_sha256="a" * 64)

    assert restored.cover_constraints == brief.cover_constraints
    assert restored.to_dict()["cover_constraints"]["preferred_story_node_ids"] == ["door_choice"]


def test_v2_omits_optional_cover_constraints_for_legacy_handoffs() -> None:
    brief = CoverBriefV2.from_dict(two_character_fixture(), source_prompt_sha256="a" * 64)

    assert brief.cover_constraints is None
    assert "cover_constraints" not in brief.to_dict()


def test_v2_rejects_unknown_cover_constraint_version() -> None:
    payload = two_character_fixture()
    payload["cover_constraints"] = {"constraint_version": "cover-constraints.v2"}

    with pytest.raises(ValueError, match="constraint_version"):
        CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("candidate_moments", [], "unsupported fields"),
        ("preferred_story_node_ids", ["missing_node"], "decisive_story_nodes"),
        ("preferred_character_ids", ["missing_character"], "principal_characters"),
        ("must_not_show", ["valid", 7], "only strings"),
    ],
)
def test_v2_rejects_cover_constraint_duplication_or_unknown_references(
    field: str,
    value: list[str],
    message: str,
) -> None:
    payload = two_character_fixture()
    payload["cover_constraints"] = {
        "constraint_version": "cover-constraints.v1",
        "must_preserve_story_facts": [],
        "must_show_if_supported": [],
        "must_not_show": [],
        "spoiler_ceiling": "",
        "user_visual_preferences": [],
        "preferred_story_node_ids": [],
        "preferred_character_ids": [],
        "source_refs": [],
        field: value,
    }

    with pytest.raises(ValueError, match=message):
        CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)


def test_model_inferred_visual_assumption_is_visible_but_not_a_blocking_fact() -> None:
    payload = two_character_fixture()
    payload["visual_assumptions"] = [{
        "field": "principal_characters[0].physical_identity",
        "proposed_value": "natural, age-appropriate appearance with restrained styling",
        "reason": "The skeleton does not specify physical appearance.",
        "status": "pending_confirmation",
        "critical": True,
        "provenance": "model_inferred",
    }]

    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)

    assert brief.pending_critical_assumptions()[0].field.endswith("physical_identity")
    assert brief.blocking_pending_critical_assumptions() == ()
    assert brief.model_inferred_assumptions()[0].provenance == "model_inferred"
    assert brief.to_dict()["visual_assumptions"][0]["provenance"] == "model_inferred"


def test_v2_rejects_required_character_without_age_or_lived_identity() -> None:
    payload = two_character_fixture()
    payload["principal_characters"][0].pop("age")
    payload["principal_characters"][0].pop("age_band")

    with pytest.raises(ValueError, match="age"):
        CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)


def test_v2_requires_identity_anchor_for_required_character() -> None:
    payload = two_character_fixture()
    payload["principal_characters"][0]["occupation_and_status"] = ""
    payload["principal_characters"][0]["daily_wardrobe"] = ""
    payload["principal_characters"][0]["lived_environment"] = ""

    with pytest.raises(ValueError, match="lived identity"):
        CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)


def test_core_conflict_visual_contract_round_trip_preserves_causal_cast_and_signals() -> None:
    payload = {
        "protagonist_character_id": "char_mara",
        "conflict_kind": "interpersonal exclusion",
        "pressure_source": "Her spouse redirects loyalty away from the home",
        "pressure_character_ids": ["char_oren"],
        "relationship_stakes": "belonging and care",
        "visible_cause": "he turns away while still asking for the shared key",
        "decisive_consequence": "she removes his access",
        "required_visual_signals": ["his divided attention", "her removed key"],
        "evidence_refs": ["node:door_choice"],
        "spoiler_boundary": "do not reveal the final relationship outcome",
    }

    contract = CoreConflictVisualContract.from_dict(payload)

    assert contract.to_dict() == payload


def test_core_conflict_visual_contract_requires_drawable_cause_and_consequence_signals() -> None:
    with pytest.raises(ValueError, match="at least two signals"):
        CoreConflictVisualContract.from_dict({
            "protagonist_character_id": "char_mara",
            "conflict_kind": "interpersonal exclusion",
            "pressure_source": "withdrawal",
            "pressure_character_ids": ["char_oren"],
            "relationship_stakes": "belonging",
            "visible_cause": "he turns away",
            "decisive_consequence": "she removes access",
            "required_visual_signals": ["a key"],
            "evidence_refs": ["node:door_choice"],
            "spoiler_boundary": "final outcome",
        })


def test_v2_accepts_four_required_characters_for_group_composition() -> None:
    payload = two_character_fixture()
    payload["principal_characters"].extend([
        _character("char_ivy", age=42),
        _character("char_sam", age=None, age_band="early forties"),
    ])

    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)

    assert len([item for item in brief.principal_characters if item.must_appear]) == 4
