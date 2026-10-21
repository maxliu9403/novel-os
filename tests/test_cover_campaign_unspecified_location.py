from copy import deepcopy
from dataclasses import replace
import json

import pytest

from core.cover_campaign import CoverCampaignPlan, campaign_response_contract, validate_campaign
from core.cover_campaign_evidence import build_evidence_claims
from core.cover_validator import validate_direction
from tests.test_cover_campaign import campaign_fixture, context
from tests.test_cover_commercial_campaign import commercial_context


@pytest.mark.parametrize("expression", [None, "literal_scene", "editorial"])
def test_unknown_event_location_cannot_become_a_literal_meeting(expression):
    brief, ledger, payload, analysis = context()
    campaign = payload["plans"][0]["campaign"]
    if expression is not None:
        campaign["expression_mode"] = expression
    # A valid event can cover remote participants without a source-owned room.
    analysis["events"][0].update(location="", reaction="The lawyer receives an email, not a visit")
    findings = validate_campaign(CoverCampaignPlan.from_dict(campaign),
        cast=payload["plans"][0]["cast"], title=brief.title,
        allowed_refs=set(ledger.allowed_refs), story_analysis=analysis,
        defer_evidence_review=True)
    missing = [item for item in findings if item.code == "campaign_event_location_unspecified"]
    assert len(missing) == 1
    detail = json.loads(missing[0].evidence)
    assert detail["event_id"] == "event-0" and detail["source_location"] == ""
    assert detail["proposed_location"] == "apartment entry"
    assert detail["path"] == "campaign.layers[0].location"
    assert "do not establish co-presence" in detail["required"]
    assert "editorial portrait/object" in detail["required"]
    # Report the missing source fact, rather than a misleading value mismatch.
    assert "campaign_event_time_place" not in {item.code for item in findings}
    assert analysis["events"][0]["location"] == ""


def test_an_unselected_remote_event_does_not_block_a_located_scene():
    brief, ledger, payload, analysis = context()
    analysis["events"].append({**deepcopy(analysis["events"][0]),
                              "event_id": "remote-email", "location": ""})
    campaign = CoverCampaignPlan.from_dict(payload["plans"][0]["campaign"])
    assert validate_campaign(campaign, cast=payload["plans"][0]["cast"], title=brief.title,
        allowed_refs=set(ledger.allowed_refs), story_analysis=analysis) == ()


def test_editorial_objects_remain_available_without_inventing_locations_or_encounters():
    brief, direction, analysis = commercial_context()
    for event in analysis["events"]:
        event["location"] = ""
    findings = validate_direction(brief, direction, story_analysis=analysis,
                                 defer_campaign_evidence_review=True)
    assert not [item for item in findings if item.severity == "blocker"]
    # Independent review still sees the factual editorial claim and its quotes.
    claims = build_evidence_claims(direction, analysis)
    assert len(claims) == 4
    assert all(item["kind"] == "editorial_expression" and item["evidence_refs"]
               and item["source_event"] is None for item in claims)


def test_known_source_location_still_requires_an_exact_match():
    brief, ledger, payload, analysis = context()
    analysis["events"][0]["location"] = "lawyer's office"
    findings = validate_campaign(CoverCampaignPlan.from_dict(payload["plans"][0]["campaign"]),
        cast=payload["plans"][0]["cast"], title=brief.title,
        allowed_refs=set(ledger.allowed_refs), story_analysis=analysis)
    assert "campaign_event_time_place" in {item.code for item in findings}
    assert "campaign_event_location_unspecified" not in {item.code for item in findings}


def test_two_empty_locations_cannot_establish_same_moment():
    brief, ledger, _, _ = context()
    original = campaign_fixture(composite=True)
    campaign = CoverCampaignPlan.from_dict(original)
    empty = deepcopy(original)
    for layer in empty["layers"]:
        layer.update(location="", epoch="opening")
    empty["layer_relationships"][0]["temporal_relationship"] = "same_moment"
    with pytest.raises(ValueError, match="campaign.location must be non-empty"):
        CoverCampaignPlan.from_dict(empty)
    # Validation also rejects equality of unknowns for in-memory plans that
    # reach this boundary without the serialization parser.
    changed = replace(campaign, layers=tuple(empty["layers"]),
                      layer_relationships=tuple(empty["layer_relationships"]))
    findings = validate_campaign(changed, cast=("char_mara", "char_oren"),
        title=brief.title, allowed_refs=set(ledger.allowed_refs))
    assert "campaign_false_simultaneity" in {item.code for item in findings}
    assert CoverCampaignPlan.from_dict(original).to_dict() == original


@pytest.mark.parametrize("profile", ["cover-profiles.v10", "cover-profiles.v11"])
def test_both_planning_contracts_explain_unknown_place_and_remote_participants(profile):
    rules = " ".join(campaign_response_contract(profile_version=profile)["rules"])
    assert "An empty source event location" in rules
    assert "story_moment requires a nonempty source location" in rules
    assert "participants can be remote senders" in rules
    assert "Two empty locations never prove same_moment" in rules
