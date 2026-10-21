"""Commercial expression is source-grounded without requiring a literal scene."""
from copy import deepcopy
from dataclasses import replace

import pytest

from core.cover_campaign import CoverCampaignPlan, campaign_response_contract, uses_campaign
from core.cover_campaign_evidence import (
    approved_evidence_claim_paths, approved_reusable_claim_reviews, build_evidence_claims,
    build_evidence_review_receipt, validate_evidence_reviews,
)
from core.cover_models_v2 import ArtDirectionSet, CoverConstraints
from core.cover_prompt_compiler import compile_cover_prompt
from core.cover_validator import validate_direction
from tests.test_cover_campaign import direction_context


def commercial_context():
    brief, old, analysis = direction_context()
    plans = []
    for index, plan in enumerate(old.plans):
        campaign = deepcopy(plan.campaign)
        campaign.update(expression_mode="editorial", composition_mode="scene", cover_epoch="promotional space",
                        event_ids=[], interaction_edges=[], layer_relationships=[], read_order=["symbol"])
        campaign["layers"] = [{"layer_id": "symbol", "type": "story_object", "cast": [], "epoch": "timeless",
            "location": "promotional space", "function": f"A worn key and a divided field express threatened belonging, variation {index}",
            "region": [0, 0.3, 1, 0.7], "visual_weight": 1, "evidence_refs": list(plan.story_evidence_refs)}]
        campaign["hook"].update(visible_fact="A solitary key on the dividing edge of light and shadow",
            unexpected_contradiction="A familiar home no longer promises belonging", visual_trigger="The familiar key separated from domestic warmth",
            open_question="Who still belongs in this home?", withheld_answer="The final choice about the home",
            reader_promise="Domestic trust, belonging and a woman's recovery of control")
        plans.append(replace(plan, campaign=campaign, cast=(), focal_character_id="", protagonist_action_visible=False,
            causal_visibility="", conflict_delivery="", conflict_read="", cause_signal="", consequence_signal="",
            conflict_character_ids=(), frozen_action="A source-grounded photographic symbol of divided belonging"))
    return brief, replace(old, profile_version="cover-profiles.v11", plans=tuple(plans)), analysis


def blockers(findings):
    return [item for item in findings if item.severity == "blocker"]


def test_editorial_object_cover_can_plan_and_compile_without_fabricating_an_event_or_human_action():
    brief, direction, analysis = commercial_context()
    assert uses_campaign(direction.profile_version)
    assert not blockers(validate_direction(brief, direction, story_analysis=analysis, defer_campaign_evidence_review=True))
    compiled = compile_cover_prompt(brief, direction.plans[0], evidence_ledger=direction.evidence_ledger)
    assert compiled.compiler_version == "cover-compiler.v18"
    assert "editorial" in compiled.text.lower() and "not a literal" in compiled.text.lower()
    assert "CAST LOCK" in compiled.modules
    assert "Exact story instant" not in compiled.text
    assert brief.title not in compiled.text  # composed artwork, title is independently frozen


def test_editorial_requires_complete_real_evidence_review_not_empty_claims_or_empty_receipt():
    _, direction, analysis = commercial_context()
    claims = build_evidence_claims(direction, analysis)
    assert len(claims) == 4
    assert all(claim["kind"] == "editorial_expression" and claim["path"] == "campaign" for claim in claims)
    assert all(claim["source_event"] is None and claim["event_id"] is None for claim in claims)
    assert all(claim["evidence_refs"] and claim["claimed_content"]["campaign"]["hook"] for claim in claims)
    receipt, findings = validate_evidence_reviews(direction, analysis, [])
    assert receipt is None and findings
    reviews = [{"claim_id": claim["claim_id"], "verdict": "supported", "reasoning": "The frozen story supports this theme and relationship, not a claimed literal event.",
                "evidence_refs": claim["evidence_refs"]} for claim in claims]
    receipt, findings = validate_evidence_reviews(direction, analysis, reviews)
    assert receipt and not findings
    reviews[0]["verdict"] = "unsupported"
    assert validate_evidence_reviews(direction, analysis, reviews)[0] is None


def test_v10_serialization_and_compiler_are_unchanged_and_cannot_use_editorial_exemption():
    brief, old, analysis = direction_context()
    original = deepcopy(old.plans[0].campaign)
    assert CoverCampaignPlan.from_dict(original).to_dict() == original
    assert "expression_mode" not in CoverCampaignPlan.from_dict(original).to_dict()
    assert compile_cover_prompt(brief, old.plans[0], evidence_ledger=old.evidence_ledger).compiler_version == "cover-compiler.v17"
    _, commercial, _ = commercial_context()
    legacy = replace(commercial, profile_version="cover-profiles.v10")
    assert "campaign_expression_profile" in {item.code for item in blockers(validate_direction(brief, legacy, story_analysis=analysis))}


def test_new_literal_mode_preserves_event_truth_checks():
    brief, direction, analysis = direction_context()
    plans = tuple(replace(plan, campaign={**plan.campaign, "expression_mode": "literal_scene"}) for plan in direction.plans)
    direction = replace(direction, profile_version="cover-profiles.v11", plans=plans)
    analysis["events"][0]["participants"] = ["char_mara"]
    assert "campaign_invented_encounter" in {item.code for item in blockers(validate_direction(brief, direction, story_analysis=analysis))}


def test_repeated_editorial_composition_is_visible_advice_not_a_story_blocker():
    brief, direction, analysis = commercial_context()
    findings = validate_direction(brief, direction, story_analysis=analysis, defer_campaign_evidence_review=True)
    assert not blockers(findings)
    assert any(item.code == "campaign_portfolio_repeated" and item.severity == "warning" for item in findings)


@pytest.mark.parametrize("mode", ["", "symbolic-anything", None, 1])
def test_unknown_explicit_expression_mode_is_rejected(mode):
    _, direction, _ = commercial_context()
    campaign = deepcopy(direction.plans[0].campaign); campaign["expression_mode"] = mode
    with pytest.raises(ValueError, match="expression_mode"):
        CoverCampaignPlan.from_dict(campaign)


def supported(direction, analysis):
    return [{"claim_id": claim["claim_id"], "verdict": "supported", "reasoning": "Source-supported core facts.",
             "evidence_refs": claim["evidence_refs"]} for claim in build_evidence_claims(direction, analysis)]


def reviewed_context():
    brief, direction, analysis = commercial_context()
    receipt = build_evidence_review_receipt(direction, analysis, supported(direction, analysis),
        policy_version="cover-commercial-review.v1", advisories=[{"category": "composition", "message": "Consider varied hierarchy."}])
    return brief, replace(direction, source_binding={"campaign_evidence_review": receipt}), analysis


def test_editorial_roundtrip_and_complete_receipt_preserve_full_fact_gate_without_event_registry():
    brief, direction, analysis = commercial_context()
    analysis["events"] = []  # A story's themes and world do not require a selected literal moment.
    direction = ArtDirectionSet.from_dict(direction.to_dict())
    receipt = build_evidence_review_receipt(direction, analysis, supported(direction, analysis), policy_version="cover-commercial-review.v1")
    direction = replace(direction, source_binding={"campaign_evidence_review": receipt})
    assert approved_evidence_claim_paths(direction, analysis)[1] == ()
    assert not blockers(validate_direction(brief, direction, story_analysis=analysis))


def test_editorial_keeps_author_constraint_and_unknown_identity_as_blockers():
    brief, direction, analysis = commercial_context()
    brief = replace(brief, cover_constraints=CoverConstraints(must_not_show=("a divided field",)))
    assert "cover_constraint_violation" in {finding.code for finding in blockers(validate_direction(
        brief, direction, story_analysis=analysis, defer_campaign_evidence_review=True))}
    campaign = deepcopy(direction.plans[0].campaign)
    campaign["layers"][0]["cast"] = ["unapproved_stranger"]
    direction = replace(direction, plans=(replace(direction.plans[0], cast=("unapproved_stranger",),
        focal_character_id="unapproved_stranger", campaign=campaign), *direction.plans[1:]))
    assert "identity_invention" in {finding.code for finding in blockers(validate_direction(
        brief, direction, story_analysis=analysis, defer_campaign_evidence_review=True))}


def test_editorial_portrait_layers_can_echo_without_asserting_a_shared_event_but_not_invent_causality():
    brief, direction, analysis = commercial_context()
    campaign = deepcopy(direction.plans[0].campaign)
    left = campaign["layers"][0]
    left.update(type="character_portrait", cast=["char_mara"], epoch="timeless", location="portrait field")
    right = {**deepcopy(left), "layer_id": "other", "cast": ["char_oren"], "epoch": "another time", "location": "different field"}
    campaign.update(composition_mode="campaign_composite", layers=[left, right], read_order=["symbol", "other"],
        layer_relationships=[{"from_layer": "symbol", "to_layer": "other", "relationship": "visual_echo",
            "temporal_relationship": "timeless_portrait", "truth_boundary": "Separate portraits do not share an event", "evidence_refs": []}])
    direction = replace(direction, plans=(replace(direction.plans[0], cast=("char_mara", "char_oren"),
        focal_character_id="char_mara", campaign=campaign), *direction.plans[1:]))
    assert not blockers(validate_direction(brief, direction, story_analysis=analysis, defer_campaign_evidence_review=True))
    campaign["layer_relationships"][0].update(relationship="cause", evidence_refs=campaign["hook"]["evidence_refs"])
    assert "campaign_false_causality" in {finding.code for finding in blockers(validate_direction(
        brief, direction, story_analysis=analysis, defer_campaign_evidence_review=True))}


def test_editorial_physical_interaction_still_requires_literal_event_support():
    brief, direction, analysis = commercial_context()
    _, literal, _ = direction_context()
    campaign = deepcopy(literal.plans[0].campaign)
    campaign["expression_mode"] = "editorial"
    direction = replace(direction, plans=(replace(literal.plans[0], campaign=campaign), *direction.plans[1:]))
    analysis["events"][0]["participants"] = ["char_mara"]
    assert "campaign_invented_encounter" in {finding.code for finding in blockers(validate_direction(
        brief, direction, story_analysis=analysis, defer_campaign_evidence_review=True))}


def test_prior_full_receipt_can_be_reused_only_for_the_requested_unchanged_siblings():
    _, prior, analysis = reviewed_context()
    current = replace(prior, plans=(replace(prior.plans[0], frozen_action="New approved promotional pose"), *prior.plans[1:]))
    ids = {plan.concept_id for plan in current.plans[1:]}
    reviews = approved_reusable_claim_reviews(prior, current, analysis, ids)
    assert reviews and {item["claim_id"].split(":")[0] for item in reviews} == ids
    assert approved_reusable_claim_reviews(prior, current, analysis, {current.plans[0].concept_id}) is None
    # Returned data must not mutate the persisted source receipt.
    reviews[0]["reasoning"] = "modified"
    assert prior.source_binding["campaign_evidence_review"]["evidence_reviews"][1]["reasoning"] == "Source-supported core facts."


@pytest.mark.parametrize("mutation", ["receipt", "analysis", "shared", "brief", "missing", "unknown_id"])
def test_reused_claims_fail_closed_on_changed_source_or_shared_contract(mutation):
    _, prior, analysis = reviewed_context()
    current = replace(prior)
    ids = {plan.concept_id for plan in current.plans[1:]}
    if mutation == "receipt":
        prior.source_binding["campaign_evidence_review"]["evidence_reviews"].pop()
    elif mutation == "analysis":
        analysis = deepcopy(analysis); analysis["story"]["core_conflict"] += " Another story."
    elif mutation == "shared":
        current = replace(current, core_conflict_visual_contract=None)
        if prior.core_conflict_visual_contract is None:
            current = replace(current, evidence_ledger=None)
    elif mutation == "brief": current = replace(current, brief_sha256="b" * 64)
    elif mutation == "missing": prior = replace(prior, source_binding=None)
    else: ids.add("unknown")
    assert approved_reusable_claim_reviews(prior, current, analysis, ids) is None


def test_v10_receipts_and_literal_siblings_can_be_reused_without_rewriting_old_plan_bytes():
    _, old, analysis = direction_context()
    receipt = build_evidence_review_receipt(old, analysis, supported(old, analysis))
    assert "policy_version" not in receipt and "advisories" not in receipt
    old = replace(old, source_binding={"campaign_evidence_review": receipt})
    current = replace(old, profile_version="cover-profiles.v11", plans=(
        replace(old.plans[0], campaign={**old.plans[0].campaign, "expression_mode": "literal_scene"}), *old.plans[1:]))
    assert approved_reusable_claim_reviews(old, current, analysis, {plan.concept_id for plan in old.plans[1:]})
    assert current.plans[1].to_dict() == old.plans[1].to_dict()


@pytest.mark.parametrize("policy", [{}, [], "unknown-policy"])
def test_receipt_policy_is_validated_without_unhashable_exception(policy):
    _, direction, analysis = commercial_context()
    receipt, findings = validate_evidence_reviews(direction, analysis, supported(direction, analysis), policy_version=policy)
    assert receipt is None and findings


def test_commercial_contract_and_compiler_keep_title_author_constraints_and_spoiler_boundaries():
    brief, direction, _ = commercial_context()
    brief = replace(brief, cover_constraints=CoverConstraints(must_not_show=("final reconciliation",),
        must_preserve_story_facts=("estranged spouses",), spoiler_ceiling="Keep the final relationship outcome hidden."))
    scene = direction.plans[0]
    compiled = compile_cover_prompt(brief, scene, evidence_ledger=direction.evidence_ledger)
    assert "Do not depict: final reconciliation." in compiled.modules["AUTHORITATIVE STORY BOUNDARIES"]
    assert "Keep the final relationship outcome hidden." in compiled.text
    assert scene.campaign["hook"]["spoiler_ceiling"] in compiled.modules["VISIBLE HOOK CONTRACT"]
    assert "without any title" in compiled.modules["TITLE AND SAFE ZONE"]
    native = replace(scene, campaign={**deepcopy(scene.campaign), "title_render_mode": "native"})
    assert brief.title in compile_cover_prompt(brief, native, evidence_ledger=direction.evidence_ledger).modules["TITLE AND SAFE ZONE"]
    rules = campaign_response_contract(profile_version="cover-profiles.v11")["rules"]
    assert any("Keep the exact approved title" in rule for rule in rules)
    assert any("safe_zones are protected" in rule for rule in rules)
    assert "expression_mode" not in campaign_response_contract()


def test_commercial_prompt_cannot_silently_drop_overbudget_story_boundaries():
    brief, direction, _ = commercial_context()
    brief = replace(brief, cover_constraints=CoverConstraints(must_preserve_story_facts=("A source-bound fact. " * 1000,)))
    with pytest.raises(ValueError, match="protected story content"):
        compile_cover_prompt(brief, direction.plans[0], evidence_ledger=direction.evidence_ledger)
