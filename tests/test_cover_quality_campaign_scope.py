"""Actual review requests separate approved image obligations from book context."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from core.cover_director import CoverArtDirector
from core.cover_models_v2 import CoverConstraints
from core.cover_quality import build_llm_visual_evaluator
from core.cover_validator import validate_direction
from tests.test_cover_campaign import context
from tests.test_cover_director import _brief, director_fixture
from tests.test_cover_quality import _blind_observation, _real_image, _scores


class CaptureClient:
    provider = "fixture"
    model = "fixture-vision"

    def __init__(self, response=None):
        self.calls = []
        self.response = response or _scores()

    def complete_with_images(self, system, user, images):
        request = json.loads(user)
        self.calls.append((system, request, images))
        return json.dumps(self.response if "scene_contract" in request else _blind_observation())


def split_leads():
    brief, ledger, payload, analysis = context()
    brief = replace(brief, principal_characters=(*brief.principal_characters, _brief(3).principal_characters[-1]))
    # The existing V10 fixture has valid scene and composite directions. A
    # co-lead owns alternate moments; no direction must contain both leads.
    for index in (1, 3):
        payload["plans"][index] = json.loads(json.dumps(payload["plans"][index]).replace("char_mara", "char_3"))
        analysis["events"][index]["participants"] = ["char_3", "char_oren"]
    analysis["characters"] = [{"character_id": "char_mara", "evidence_refs": list(ledger.allowed_refs)}]
    direction = CoverArtDirector.from_fixture(payload).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert validate_direction(brief, direction, story_analysis=analysis) == ()
    return brief, direction, analysis


def review(evaluator, brief, scene):
    return evaluator.review(image=_real_image(), thumbnail=b"final thumbnail", artwork=_real_image(),
                            brief=brief, scene=scene)


def test_campaign_review_uses_each_approved_cast_without_losing_book_identity_or_leaking_between_directions():
    brief, direction, _ = split_leads()
    original = deepcopy(brief.to_dict())
    client = CaptureClient()
    evaluator = build_llm_visual_evaluator(client)
    for scene in direction.plans[:2]:
        reviewed = review(evaluator, brief, scene)
        request = client.calls[-1][1]
        scope = request["cast_scope"]
        assert scope["required_character_ids"] == list(scene.cast)
        assert scope["layers"] == [{key: layer[key] for key in ("layer_id", "type", "cast", "epoch", "location")}
                                   for layer in scene.campaign["layers"]]
        registry = {item["character_id"]: item for item in request["approved_characters"]}
        assert set(registry) == {item.character_id for item in brief.principal_characters}
        for item in brief.principal_characters:
            sent = registry[item.character_id]
            assert "must_appear" not in sent  # No ambiguous per-image flag from the book registry.
            assert sent["book_level_must_appear"] == item.must_appear
            assert sent["required_in_this_image"] == (item.character_id in scene.cast)
            assert {k: v for k, v in sent.items() if k not in {"book_level_must_appear", "required_in_this_image"}} == {
                k: v for k, v in item.to_dict().items() if k != "must_appear"}
        assert reviewed.evaluation["evaluation_status"] == "complete"
    assert client.calls[1][1]["cast_scope"]["required_character_ids"] != client.calls[3][1]["cast_scope"]["required_character_ids"]
    for _, blind, _ in client.calls[::2]:
        assert "cast_scope" not in blind and "approved_characters" not in blind
    assert brief.to_dict() == original


def test_campaign_scope_preserves_explicit_user_obligations_and_their_original_scope():
    brief, direction, analysis = split_leads()
    constraints = CoverConstraints.from_dict({"constraint_version": "cover-constraints.v1",
        "must_show_if_supported": ["char_mara"], "must_not_show": ["final reconciliation"],
        "must_preserve_story_facts": ["the spouses are estranged"]})
    brief = replace(brief, cover_constraints=constraints)
    assert validate_direction(brief, direction, story_analysis=analysis) == ()
    client = CaptureClient()
    review(build_llm_visual_evaluator(client), brief, direction.plans[1])
    request = client.calls[-1][1]
    assert request["author_constraints"] == constraints.to_dict()
    assert "char_mara" not in request["cast_scope"]["required_character_ids"]
    assert "original scope" in request["cast_scope"]["author_constraint_rule"]
    assert "must_show_if_supported" in request["cast_scope"]["author_constraint_rule"]
    assert "take precedence" in request["cast_scope"]["author_constraint_rule"]


def test_explicit_current_image_user_requirement_is_not_waived_by_a_narrower_planned_cast():
    brief, direction, _ = split_leads()
    explicit = f"In {direction.plans[0].concept_id}, char_3 must be visible if supported by the source."
    constraints = CoverConstraints.from_dict({"constraint_version": "cover-constraints.v1",
                                            "must_show_if_supported": [explicit]})
    brief = replace(brief, cover_constraints=constraints)
    response = {**_scores(), "status": "blocked", "blockers": ["missing_character"],
        "repair_codes": ["missing_character"], "findings": [{"code": "missing_character",
        "severity": "blocker", "message": "The explicitly requested source-supported character is absent",
        "evidence": "Only the two planned participants are visible; no third participant appears"}]}
    client = CaptureClient(response)
    result = review(build_llm_visual_evaluator(client), brief, direction.plans[0])
    request = client.calls[-1][1]
    assert "char_3" not in request["cast_scope"]["required_character_ids"]
    assert request["author_constraints"]["must_show_if_supported"] == [explicit]
    assert "mandatory even if omitted from the planned cast" in request["cast_scope"]["author_constraint_rule"]
    assert result.report.blockers == ("missing_character",)
    assert result.report.findings[0].evidence == response["findings"][0]["evidence"]


@pytest.mark.parametrize("code", ["missing_character", "core_conflict_missing", "protagonist_action_missing"])
def test_campaign_scope_does_not_discard_genuine_rendered_failure_findings(code):
    brief, direction, _ = split_leads()
    response = {**_scores(), "status": "blocked", "blockers": [code], "repair_codes": [code],
        "findings": [{"code": code, "severity": "blocker", "message": "The approved reaching participant/action is absent",
                      "evidence": "The planned char_oren and reaching hand cannot be seen at the right side of the doorway"}]}
    client = CaptureClient(response)
    result = review(build_llm_visual_evaluator(client), brief, direction.plans[0])
    assert "char_oren" in client.calls[-1][1]["cast_scope"]["required_character_ids"]
    assert result.evaluation["evaluation_status"] == "complete"
    assert result.report.status == "blocked" and result.report.blockers == (code,)
    assert result.report.repair_codes == (code,)
    assert result.report.findings[0].evidence == response["findings"][0]["evidence"]


def test_campaign_story_review_is_scoped_to_the_approved_visible_promise_not_withheld_book_answers():
    brief, direction, _ = split_leads()
    client = CaptureClient()
    review(build_llm_visual_evaluator(client), brief, direction.plans[0])
    system, request, _ = client.calls[-1]
    rules = " ".join(request["semantic_rules"])
    assert "approved direction" in rules and "scene_contract.conflict_read" in rules
    assert "campaign.hook.visible_fact" in rules and "interaction_edges" in rules
    assert "withheld_answer" in rules and "not a requirement to summarize the entire book" in rules
    assert "visible cause and response" in rules
    assert request["story"]["core_conflict"] == brief.core_conflict
    assert request["scene_contract"] == direction.plans[0].to_dict()
    assert "cast_scope" in system


def test_legacy_without_campaign_retains_original_required_character_wire_contract():
    brief = _brief()
    scene = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4).plans[0]
    client = CaptureClient()
    review(build_llm_visual_evaluator(client), brief, scene)
    request = client.calls[-1][1]
    assert "cast_scope" not in request
    assert request["approved_characters"] == [item.to_dict() for item in brief.principal_characters]
    assert any(item["must_appear"] for item in request["approved_characters"])
