from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from core.cover_campaign import CoverCampaignPlan, validate_campaign
from core.cover_design import evidence_ledger_from_brief
from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_prompt_compiler import compile_cover_prompt, compile_direction_recipes, compile_repair_prompt
from core.cover_validator import validate_direction
from tests.cover_review_fixtures import supported_review
from tests.test_cover_director import _brief, adaptive_director_fixture


def campaign_fixture(index=0, *, composite=False):
    ref = evidence_ledger_from_brief(_brief()).allowed_refs[0]
    layer = {"layer_id": "moment", "type": "story_moment", "event_id": f"event-{index}",
             "cast": ["char_mara", "char_oren"], "epoch": f"chapter-{index + 1}", "location": "apartment entry",
             "function": "Mara withdraws the shared key as Oren reaches and stops", "region": [0, 0.3, 1, 0.7],
             "visual_weight": 1, "evidence_refs": [ref]}
    layers = [layer]
    links = []
    if composite:
        layers.append({"layer_id": "portrait", "type": "character_portrait", "cast": ["char_mara"],
                       "epoch": "timeless", "location": "campaign space", "function": "Mara's vigilant portrait",
                       "region": [0.05, 0.05, 0.45, 0.4], "visual_weight": 0.4, "evidence_refs": [ref]})
        links.append({"from_layer": "portrait", "to_layer": "moment", "relationship": "visual_echo",
                      "temporal_relationship": "timeless_portrait", "truth_boundary": "The portrait is not a witness physically present above the doorway", "evidence_refs": []})
    return {"schema_version": 1, "composition_mode": "campaign_composite" if composite else "scene",
            "cover_epoch": layer["epoch"], "event_ids": [f"event-{index}"],
            "interaction_edges": [{"actor_id": "char_mara", "action": "withdraws the key", "target_id": "char_oren",
                "affected_character_ids": ["char_oren"], "visible_reaction": "his reaching hand stops", "evidence_refs": [ref], "layer_id": "moment"}],
            "hook": {"visible_fact": "One hand takes the key away from another", "unexpected_contradiction": "The person expecting access is stopped at his own doorway",
                     "visual_trigger": "The withdrawing key leaves an open reaching hand", "open_question": "Will he still have access to the home?",
                     "withheld_answer": "Whether Mara closes the door permanently", "reader_promise": "An excluded partner chooses her own boundary",
                     "spoiler_ceiling": "Do not show the final separation", "evidence_refs": [ref]},
            "layers": layers, "layer_relationships": links, "read_order": [item["layer_id"] for item in layers],
            "title_render_mode": "composed", "title_layout": {"title": _brief().title, "language": "en",
                "lines": [_brief().title], "font_id": "sans-bold", "font_weight": "bold", "font_size_min": 0.04,
                "font_size_max": 0.1, "align": "center", "letter_spacing": 0, "color": "#ffffff",
                "region": [0.05, 0.05, 0.9, 0.2], "safe_zones": [[0, 0.3, 1, 0.7]]}}


def context():
    brief = _brief()
    ledger = evidence_ledger_from_brief(brief)
    payload = adaptive_director_fixture()
    payload.update(profile_version="cover-profiles.v10", schema_version=2)
    events = []
    for index, plan in enumerate(payload["plans"]):
        plan["cast"] = ["char_mara", "char_oren"]
        plan["campaign"] = campaign_fixture(index, composite=index % 2 == 1)
        plan["story_evidence_refs"] = list(ledger.allowed_refs)
        events.append({"event_id": f"event-{index}", "participants": ["char_mara", "char_oren"],
                       "epoch": f"chapter-{index + 1}", "location": "apartment entry", "evidence_refs": list(ledger.allowed_refs)})
    analysis = {"coverage_status": "complete", "events": events, "story": {"core_conflict": brief.core_conflict},
                "evidence": [{"evidence_id": item.evidence_id, "statement": item.summary,
                              "exact_quote": "Verified source quotation: " + item.summary,
                              "document_id": "primary", "byte_start": index * 100, "byte_end": index * 100 + 90}
                             for index, item in enumerate(ledger.items)]}
    return brief, ledger, payload, analysis


def direction_context():
    brief, ledger, payload, analysis = context()
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    return brief, direction, analysis


def test_campaign_scene_and_composite_bind_to_source_events():
    brief, direction, analysis = direction_context()
    assert validate_direction(brief, direction, story_analysis=analysis) == ()
    assert CoverCampaignPlan.from_dict(direction.plans[1].campaign).composition_mode == "campaign_composite"
    bad = deepcopy(analysis)
    bad["events"][0]["participants"] = ["char_mara"]
    assert "campaign_invented_encounter" in {x.code for x in validate_direction(brief, direction, story_analysis=bad)}
    bad["events"][0]["epoch"] = "ending"
    assert "campaign_event_time_place" in {x.code for x in validate_direction(brief, direction, story_analysis=bad)}


def test_campaign_repair_feedback_identifies_actual_fields_layers_and_missing_cast():
    brief, ledger, payload, analysis = context()
    scene = payload["plans"][0]["campaign"]
    scene["cover_epoch"] = "generic pre-resolution"
    composite = payload["plans"][1]["campaign"]
    composite["layer_relationships"][0].update(relationship="cause", temporal_relationship="same_moment",
                                              evidence_refs=[ledger.allowed_refs[0]])
    composite["interaction_edges"][0]["layer_id"] = "portrait"
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4, evidence_ledger=ledger,
                                                           story_analysis=analysis)
    findings = validate_direction(brief, direction, story_analysis=analysis)
    prompt = json.loads(CoverArtDirector._repair_prompt(brief=brief, count=4, previous_payload=payload,
        findings=findings, profile_version="cover-profiles.v10", evidence_ledger=ledger, story_analysis=analysis))
    selected = {row["code"]: row for row in prompt["validation_findings"]}
    def details(code, concept):
        prefix = concept + ": "
        assert selected[code]["concept_or_evidence"].startswith(prefix)
        return json.loads(selected[code]["concept_or_evidence"][len(prefix):])
    timing = details("campaign_scene_time", "concept-1")
    assert timing["cover_epoch"] == "generic pre-resolution"
    assert timing["event_ids"] == ["event-0"]
    assert timing["layers"][0] == {"path": "campaign.layers[0].epoch", "layer_id": "moment", "epoch": "chapter-1"}
    simultaneity = details("campaign_false_simultaneity", "concept-2")
    assert simultaneity["path"] == "campaign.layer_relationships[0]"
    assert simultaneity["from_layer"] == {"layer_id": "portrait", "epoch": "timeless", "location": "campaign space"}
    assert simultaneity["to_layer"] == {"layer_id": "moment", "epoch": "chapter-2", "location": "apartment entry"}
    causality = details("campaign_false_causality", "concept-2")
    assert causality["path"] == "campaign.layer_relationships[0].relationship"
    assert causality["relationship"] == "cause"
    assert causality["from_layer"] == {"layer_id": "portrait", "type": "character_portrait"}
    interaction = details("campaign_interaction_cast", "concept-2")
    assert interaction["path"] == "campaign.interaction_edges[0]"
    assert interaction["layer_id"] == "portrait"
    assert interaction["layer_cast"] == ["char_mara"]
    assert interaction["missing_cast"] == ["char_oren"]
    # The diagnosis only helps repair; all four blockers still apply.
    assert set(selected) >= {"campaign_scene_time", "campaign_false_simultaneity",
                             "campaign_false_causality", "campaign_interaction_cast"}


def test_campaign_contract_explains_exact_time_layer_causality_and_visible_participants():
    from core.cover_campaign import campaign_response_contract
    rules = " ".join(campaign_response_contract()["rules"])
    assert "cover_epoch must exactly equal" in rules
    assert "same_moment" in rules and "exactly matching epoch and location" in rules
    assert "cause" in rules and "two story_moment layers" in rules
    assert "affected_character_ids" in rules and "layer.cast" in rules
    assert "different times or locations" in rules


def test_campaign_exhaustion_reports_code_concept_and_fields_without_extra_attempts():
    brief, ledger, payload, analysis = context()
    payload["plans"][0]["campaign"]["cover_epoch"] = "generic pre-resolution"
    requests = []
    def complete(system, user):
        requests.append(json.loads(user))
        return json.dumps(payload)
    director = CoverArtDirector(complete=complete, model="fixture", profile_version="cover-profiles.v10")
    with pytest.raises(CoverDirectionError) as error:
        director.plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert len(requests) == 3  # Initial plan plus the existing two repairs.
    assert "campaign_scene_time" in str(error.value)
    assert "concept-1" in str(error.value)
    assert "campaign.layers[0].epoch" in str(error.value)
    assert "generic pre-resolution" in str(error.value)
    for request in requests[1:]:
        finding = next(item for item in request["validation_findings"] if item["code"] == "campaign_scene_time")
        assert "concept-1" in finding["concept_or_evidence"]
        assert "chapter-1" in finding["concept_or_evidence"]


def test_campaign_blocks_unknown_refs_portrait_actions_and_false_causation():
    brief, direction, analysis = direction_context()
    data = deepcopy(direction.plans[1].campaign)
    data["interaction_edges"][0]["layer_id"] = "portrait"
    data["interaction_edges"][0]["affected_character_ids"] = ["char_mara"]
    data["interaction_edges"][0]["target_id"] = "key"
    data["hook"]["evidence_refs"] = ["evidence:invented"]
    data["layer_relationships"][0].update(relationship="cause", evidence_refs=[direction.evidence_ledger.allowed_refs[0]])
    findings = validate_campaign(CoverCampaignPlan.from_dict(data), cast=direction.plans[1].cast,
        title=brief.title, allowed_refs=set(direction.evidence_ledger.allowed_refs), story_analysis=analysis)
    assert {item.code for item in findings} >= {"campaign_unknown_evidence", "campaign_portrait_as_event", "campaign_false_causality"}


def test_generic_question_and_repeated_portfolio_do_not_pass_field_presence():
    brief, direction, analysis = direction_context()
    plans = list(direction.plans)
    campaign = deepcopy(plans[0].campaign)
    campaign["hook"]["open_question"] = "接下来会怎样？"
    campaign["layers"][0]["region"] = [0.1, 0.35, 0.8, 0.6]  # a crop is not a new hypothesis
    plans[1] = replace(plans[1], campaign=campaign)
    altered = replace(direction, plans=tuple(plans))
    assert {item.code for item in validate_direction(brief, altered, story_analysis=analysis)} >= {
        "campaign_generic_hook", "campaign_portfolio_repeated"}


def test_composite_compiles_layers_without_global_single_moment_or_title_leak():
    brief, direction, _ = direction_context()
    recipes = compile_direction_recipes(brief, direction)
    assert set(recipes) == {plan.concept_id for plan in direction.plans}
    recipe = recipes[direction.plans[1].concept_id]
    assert recipe["title_render_mode"] == "composed"
    assert recipe["compiler_version"] == "cover-compiler.v17"
    assert brief.title not in recipe["image_prompt"]
    assert "Render the exact title" not in recipe["image_prompt"]
    assert "intentional campaign composite" in recipe["image_prompt"]
    assert "never flatten separate layers" in recipe["image_prompt"]
    assert "one plausible moment" not in recipe["image_prompt"]
    assert "campaign space" in recipe["image_prompt"]
    assert recipe["title_layout"]["title"] == brief.title
    digest = recipe.pop("recipe_sha256")
    assert digest == hashlib.sha256(json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_campaign_compiler_protects_hook_and_fails_before_truncation():
    brief, direction, _ = direction_context()
    scene = direction.plans[0]
    context_args = dict(evidence_ledger=direction.evidence_ledger)
    compiled = compile_cover_prompt(brief, scene, **context_args)
    assert scene.campaign["hook"]["withheld_answer"] in compiled.text
    assert scene.campaign["hook"]["open_question"] in compiled.text
    data = deepcopy(scene.campaign)
    data["hook"]["visual_trigger"] = "不可删除的视觉事实" * 2000
    with pytest.raises(ValueError, match="Protected modules.*VISIBLE HOOK CONTRACT"):
        compile_cover_prompt(brief, replace(scene, campaign=data), **context_args)
    with pytest.raises(ValueError, match="new direction and approval"):
        compile_repair_prompt(brief, scene, compiled, ["core_conflict_missing"], **context_args)


@pytest.mark.parametrize("title", ["Mara", "门"])
def test_composed_book_title_matching_story_fact_does_not_erase_that_fact(title):
    brief, direction, _ = direction_context()
    brief = replace(brief, title=title, principal_characters=tuple(
        replace(character, name="Mara") if character.character_id == "char_mara" else character
        for character in brief.principal_characters))
    scene = direction.plans[0]
    campaign = deepcopy(scene.campaign)
    campaign["title_layout"].update(title=title, lines=[title])
    campaign["hook"]["visual_trigger"] = "Mara closes the 门 while his reaching hand stops"
    scene = replace(scene, campaign=campaign)
    compiled = compile_cover_prompt(brief, scene, evidence_ledger=direction.evidence_ledger)
    assert "char_mara (Mara)" in compiled.text
    assert campaign["hook"]["visual_trigger"] in compiled.text
    assert "Render the exact title" not in compiled.text
    assert "Generate artwork without any title" in compiled.text
    assert '"title":' not in compiled.text


def test_v10_requires_analysis_and_independent_semantic_review():
    brief, ledger, payload, analysis = context()
    calls = []
    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        if request["task"] == "review_campaign_semantics":
            return json.dumps(supported_review(request))
        return json.dumps(payload)
    director = CoverArtDirector(complete=complete)
    direction = director.plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert direction.profile_version == "cover-profiles.v10"
    assert director.profile_version == "cover-profiles.v9"
    assert len(calls) == 2
    assert calls[0]["story_analysis"]["events"] == analysis["events"]
    assert "Within each story_moment layer" in calls[0]["render_policy"]["human_performance"]
    assert calls[1]["task"] == "review_campaign_semantics"
    with pytest.raises(CoverDirectionError, match="complete frozen"):
        director.plan(brief, count=4, story_analysis={"coverage_status": "partial"})


def test_semantic_review_receives_author_constraints_without_a_second_analysis_call():
    brief, ledger, payload, analysis = context()
    constrained_payload = brief.to_dict()
    constrained_payload["cover_constraints"] = {
        "constraint_version": "cover-constraints.v1",
        "must_preserve_story_facts": ["the spouses remain estranged at the cover epoch"],
        "must_show_if_supported": ["door_choice"],
        "must_not_show": ["final reconciliation"],
        "spoiler_ceiling": "Do not reveal the final relationship outcome.",
        "user_visual_preferences": ["restrained photography"],
        "preferred_story_node_ids": ["door_choice"],
        "preferred_character_ids": ["char_mara"],
        "source_refs": ["node:door_choice"],
    }
    from core.cover_models_v2 import CoverBriefV2
    constrained = CoverBriefV2.from_dict(
        constrained_payload, source_prompt_sha256=brief.source_prompt_sha256,
    )
    calls = []

    def complete(_system, user):
        request = json.loads(user)
        calls.append(request)
        return json.dumps(supported_review(request))

    director = CoverArtDirector(complete=complete, profile_version="cover-profiles.v10")
    direction = CoverArtDirector.from_fixture(payload).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis,
    )
    assert director._review_campaign_semantics(direction, analysis, brief=constrained) == ()
    assert len(calls) == 1
    assert calls[0]["author_constraints"]["must_not_show"] == ["final reconciliation"]
    assert any("must_show_if_supported" in check for check in calls[0]["checks"])


def test_semantic_rejection_is_repaired_with_evidence_and_not_silently_returned():
    brief, ledger, payload, analysis = context()
    calls = []
    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        if request["task"] == "review_campaign_semantics":
            return json.dumps(supported_review(request, accepted=False, findings=[{"concept_id": "concept-1",
                "message": "The visual trigger does not support the implied exclusion", "evidence_refs": [ledger.allowed_refs[0]]}]))
        return json.dumps(payload)
    with pytest.raises(CoverDirectionError, match="failed review"):
        CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert len(calls) == 6
    assert calls[2]["validation_findings"][0]["code"] == "campaign_semantic_review"


def test_semantic_review_separates_story_truth_from_cover_only_performance():
    brief, ledger, payload, analysis = context()
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4, evidence_ledger=ledger,
                                                           story_analysis=analysis)
    captured = []
    def complete(system, user):
        request = json.loads(user)
        captured.append((system, request))
        return json.dumps(supported_review(request))
    director = CoverArtDirector(complete=complete, profile_version="cover-profiles.v10")
    assert director._review_campaign_semantics(direction, analysis, brief=brief) == ()
    system, request = captured[0]
    checks = " ".join(request["checks"])
    assert "relationships, events, character knowledge" in checks
    assert "cover-only" in checks and "prop holding" in checks
    assert "not require verbatim source wording for every finger" in checks
    assert "new narrative action" in checks and "causal trigger" in checks
    assert "Every direction" in checks and "readable protagonist response" in checks
    assert "At least one direction" in checks and "concrete protagonist choice" in checks
    assert "same kind of active choice in every direction" in checks
    assert "not authority" in system and "never let it change story facts" in system
    assert request["selected_verified_evidence"]


def test_director_projects_large_analysis_without_losing_late_story_or_evidence_ids():
    brief, ledger, payload, analysis = context()
    huge = "UNREFERENCED ORIGINAL SOURCE " * 10000
    extra = replace(ledger.items[0], evidence_id="unused-last", summary="The ending reveals a second key.")
    ledger = replace(ledger, items=(*ledger.items, extra))
    analysis["evidence"].append({"evidence_id": extra.evidence_id, "statement": extra.summary,
                                 "exact_quote": huge, "document_id": "primary", "byte_start": 900000, "byte_end": 1200000})
    analysis.update(segment_results=[{"raw": huge}], attempts=[{"response": huge}],
                    verification_progress={"raw": huge}, aggregation_progress=[huge])
    analysis["story"]["last_chapter_reversal"] = "The second key reveals who controlled access."
    original = deepcopy(analysis)
    calls = []
    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        return json.dumps(supported_review(request) if request["task"] == "review_campaign_semantics" else payload)
    director = CoverArtDirector(complete=complete)
    direction = director.plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    prompts = [calls[0], json.loads(director._schema_repair_prompt(brief=brief, count=4,
        previous_response="invalid", error="invalid", profile_version="cover-profiles.v10",
        evidence_ledger=ledger, recent_fingerprints=(), story_analysis=analysis)),
        json.loads(director._repair_prompt(brief=brief, count=4, previous_payload=payload,
            findings=(), profile_version="cover-profiles.v10", evidence_ledger=ledger, story_analysis=analysis))]
    for request in prompts:
        projection = request["story_analysis"]
        assert projection["story"]["last_chapter_reversal"] == analysis["story"]["last_chapter_reversal"]
        assert [item["evidence_id"] for item in projection["evidence"]] == [item["evidence_id"] for item in analysis["evidence"]]
        serialized = json.dumps(request)
        assert "exact_quote" not in serialized and "UNREFERENCED ORIGINAL SOURCE" not in serialized
        assert "segment_results" not in serialized and "attempts" not in serialized
        assert len(serialized.encode()) < 100000
    review = calls[1]
    assert review["plans"] == [plan.to_dict() for plan in direction.plans]
    assert all(item["evidence_id"] != "unused-last" for item in review["selected_verified_evidence"])
    originals = {item["evidence_id"]: item for item in analysis["evidence"]}
    assert all(item == originals[item["evidence_id"]] for item in review["selected_verified_evidence"])
    assert analysis == original


def test_semantic_review_fails_before_provider_on_oversized_selected_original_quote():
    brief, ledger, payload, analysis = context()
    analysis["configuration"] = {"max_request_bytes": 100000}
    analysis["evidence"][0]["exact_quote"] = "x" * 120000
    calls = []
    def complete(system, user):
        calls.append(json.loads(user))
        return json.dumps(payload)
    with pytest.raises(CoverDirectionError, match="exceeds its byte budget"):
        CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert len(calls) == 1 and calls[0]["task"] != "review_campaign_semantics"


def test_director_fails_before_provider_instead_of_truncating_oversized_story_model():
    brief, ledger, _, analysis = context()
    analysis["configuration"] = {"max_request_bytes": 100000}
    analysis["story"]["all_story_facts"] = "x" * 120000
    calls = []
    with pytest.raises(CoverDirectionError, match="exceeds its byte budget"):
        CoverArtDirector(complete=lambda *args: calls.append(args)).plan(
            brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert calls == []


def test_semantic_review_does_not_replace_missing_source_quote_with_summary():
    brief, ledger, payload, analysis = context()
    analysis["evidence"][0].pop("exact_quote")
    calls = []
    def complete(system, user):
        calls.append(json.loads(user))
        return json.dumps(payload)
    with pytest.raises(CoverDirectionError, match="complete cited source quotes"):
        CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert len(calls) == 1


def test_legacy_direction_has_no_campaign_or_new_fields_added():
    brief = _brief()
    payload = adaptive_director_fixture()
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4)
    assert all("campaign" not in plan.to_dict() for plan in direction.plans)
    assert "source_binding" not in direction.to_dict()
    assert "compiled_recipes" not in direction.to_dict()
