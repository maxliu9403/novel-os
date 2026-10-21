"""V10 casts source-supported interactions, not everyone named in an event."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_prompt_compiler import compile_direction_recipes
from core.cover_validator import validate_direction
from tests.cover_review_fixtures import supported_review
from tests.test_cover_campaign import context


def solo_context(*, remote=False, pressure="a storm"):
    brief, ledger, payload, analysis = context()
    if not remote:
        brief = replace(brief, principal_characters=(brief.principal_characters[0],),
                        relationship_map=(), core_conflict=f"Mara must protect her shelter from {pressure}.")
        payload["core_conflict_visual_contract"]["pressure_character_ids"] = []
    for index, plan in enumerate(payload["plans"]):
        plan.update(cast=["char_mara"], focal_character_id="char_mara", conflict_character_ids=[],
                    causal_visibility="indirect" if remote else "direct",
                    cause_signal="The remote access cancellation on the established terminal" if remote else pressure,
                    consequence_signal="Mara keeps the shelter secured despite the pressure",
                    protagonist_action_visible=True)
        campaign = plan["campaign"]
        for layer in campaign["layers"]:
            layer["cast"] = ["char_mara"]
        for edge in campaign["interaction_edges"]:
            edge.update(actor_id="char_mara", target_id="shelter-control",
                        affected_character_ids=["char_mara"], action="Mara secures the shelter control",
                        visible_reaction="Mara steadies her hand against the pressure")
        event = analysis["events"][index]
        event.update(action="Mara secures the shelter control", reaction="Mara steadies her hand",
                     consequence="The shelter remains secured")
        if remote:
            # Causal participants include a sender who is explicitly elsewhere.
            event["action"] = "Oren cancels access remotely from another city; Mara receives the notice alone."
        else:
            event["participants"] = ["char_mara"]
    return brief, ledger, payload, analysis


@pytest.mark.parametrize("pressure", ["a storm", "an automated institutional shutdown", "her own panic"])
def test_single_protagonist_pressure_needs_no_invented_relationship(pressure):
    brief, ledger, payload, analysis = solo_context(pressure=pressure)
    contract = CoverArtDirector._response_contract(brief, 4, profile_version="cover-profiles.v10")
    policy = contract["story_cast_policy"]
    assert "minimum_relationship_plans" not in policy
    assert policy["relationship_coverage"]["mode"] == "source_supported_interaction"
    direction = CoverArtDirector.from_fixture(payload).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert validate_direction(brief, direction, story_analysis=analysis) == ()
    recipes = compile_direction_recipes(brief, direction)
    assert len(recipes) == 4
    assert all(plan.cast == ("char_mara",) for plan in direction.plans)


def test_remote_event_participant_never_forces_a_physical_encounter():
    brief, ledger, payload, analysis = solo_context(remote=True)
    before = deepcopy(analysis)
    contract = CoverArtDirector._response_contract(brief, 4, profile_version="cover-profiles.v10")
    policy = contract["story_cast_policy"]
    assert "require_colead_scene" not in policy
    assert "require_direct_pressure_scene" not in policy
    direction = CoverArtDirector.from_fixture(payload).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert validate_direction(brief, direction, story_analysis=analysis) == ()
    assert len(compile_direction_recipes(brief, direction)) == 4
    assert analysis == before
    assert all("char_oren" in event["participants"] for event in analysis["events"])


@pytest.mark.parametrize("remote", [False, True])
def test_non_interpersonal_or_remote_plan_reaches_review_and_recipe_compilation(remote):
    brief, ledger, payload, analysis = solo_context(remote=remote)
    requests = []

    def complete(_system, user):
        request = json.loads(user)
        requests.append(request)
        return json.dumps(supported_review(request)
                          if request["task"] == "review_campaign_semantics" else payload)

    direction = CoverArtDirector(complete=complete).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert len(requests) == 2
    assert requests[-1]["task"] == "review_campaign_semantics"
    assert len(compile_direction_recipes(brief, direction)) == 4


@pytest.mark.parametrize("field", ["cause_signal", "consequence_signal", "conflict_delivery", "conflict_read"])
def test_v10_missing_causal_description_is_not_a_relationship_waiver(field):
    brief, ledger, payload, analysis = solo_context()
    payload["plans"][0][field] = ""
    direction = CoverArtDirector.from_fixture(payload).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    findings = validate_direction(brief, direction, story_analysis=analysis)
    assert "missing_core_conflict" in {finding.code for finding in findings}


def test_v10_portfolio_still_requires_a_declared_visible_protagonist_choice():
    brief, ledger, payload, analysis = solo_context()
    for plan in payload["plans"]:
        plan["protagonist_action_visible"] = False
    direction = CoverArtDirector.from_fixture(payload).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    findings = validate_direction(brief, direction, story_analysis=analysis)
    assert "protagonist_action_undercoverage" in {finding.code for finding in findings}


def test_action_visible_boolean_with_only_nonprotagonist_action_does_not_satisfy_choice_gate():
    brief, ledger, payload, analysis = context()
    brief = replace(brief, principal_characters=tuple(
        replace(character, narrative_role="antagonist") if character.character_id == "char_oren" else character
        for character in brief.principal_characters))
    for plan in payload["plans"]:
        plan["protagonist_action_visible"] = True
        for edge in plan["campaign"]["interaction_edges"]:
            edge.update(actor_id="char_oren", target_id="char_mara",
                        affected_character_ids=["char_mara"])
    direction = CoverArtDirector.from_fixture(payload).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert "protagonist_action_undercoverage" in {
        item.code for item in validate_direction(brief, direction, story_analysis=analysis)}


def test_visible_colead_may_make_the_choice_while_focal_lead_reacts():
    brief, ledger, payload, analysis = context()
    for plan in payload["plans"]:
        plan.update(focal_character_id="char_oren", protagonist_action_visible=True)
        assert plan["campaign"]["interaction_edges"][0]["actor_id"] == "char_mara"
    direction = CoverArtDirector.from_fixture(payload).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert validate_direction(brief, direction, story_analysis=analysis) == ()


def test_source_supported_encounter_avoidance_is_an_explicit_semantic_blocker():
    brief, ledger, payload, analysis = solo_context(remote=True)
    for event in analysis["events"]:
        event.update(action="Mara withdraws the shared key as Oren reaches across the threshold.",
                     reaction="Oren's reaching hand stops.")
    requests = []

    def complete(_system, user):
        request = json.loads(user)
        requests.append(request)
        if request["task"] != "review_campaign_semantics":
            return json.dumps(payload)
        checks = " ".join(request["checks"])
        assert "Reject the portfolio" in checks
        assert "source-supported central encounter" in checks
        assert "participants alone" in checks
        assert "single camera viewpoint" in checks
        assert "campaign_composite" in checks
        return json.dumps(supported_review(request, accepted=False, findings=[{
            "concept_id": "portfolio", "message": "Every direction omits the source-supported central encounter.",
            "evidence_refs": [ledger.allowed_refs[0]],
        }]))

    with pytest.raises(CoverDirectionError, match="campaign_semantic_review.*central encounter"):
        CoverArtDirector(complete=complete).plan(
            brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    # The independent rejection remains mandatory through the existing repair budget.
    assert [request["task"] for request in requests].count("review_campaign_semantics") == 3
