from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
from io import BytesIO
import json

import pytest
from PIL import Image

from core.cover_models import CoverBrief, CoverConcept, CoverSet
from core.cover_poster_planner import (
    PosterPlanningError, normalize_poster_plan, plan_poster, poster_storage_objects,
)
from core.cover_sources import CoverSourceContext, CoverSourceSelector, capture_source


@pytest.fixture
def poster_source(tmp_path):
    project = tmp_path / "book"
    (project / "outputs/input").mkdir(parents=True)
    body = ("# 完整骨架\r\nMara, 58, chooses independence from her adult son Leo.\r\n"
            "New York, contemporary family drama. Her late-life choice remains loving.\r\n"
            "## Ending\r\nShe moves into her own flat; no reconciliation guarantee.\r\n"
            "```COVER_SOURCE_DEPENDENCIES\r\n{\"cast\":\"cast.md\"}\r\n```\r\n").encode()
    (project / "outputs/input/prompt.md").write_bytes(body)
    (project / "cast.md").write_bytes("Mara is 58; Leo is her adult son, not her lover.\n".encode())
    context = CoverSourceContext(tmp_path / "covers", "default:project:book", lambda _: project)
    return capture_source(context, CoverSourceSelector("project_input", project_id="book"))


@pytest.fixture
def metadata():
    return {"title": "She Still Loved Them, but the Choice Was Hers", "author": "M. Example",
            "language": "en", "genre": "Domestic drama", "market_scope": "North America",
            "target_audience": "Women 40–60"}


def response():
    return {
        "story": {"premise": "Mara chooses independence while preserving family affection.",
                  "core_conflict": "Love and duty pull against her own agency.",
                  "emotional_promise": "A mature woman can take ownership of her future.",
                  "world": "Contemporary New York; ordinary domestic life.",
                  "cast": [{"name": "Mara", "role": "protagonist", "identity": "Woman, 58",
                            "relationship": "Leo's mother"},
                           {"name": "Leo", "role": "supporting", "identity": "Her adult son",
                            "relationship": "Mara's son"}],
                  "hard_constraints": ["Preserve mature age and mother-son relationship."]},
        "directions": [{"id": "model-must-not-control-ids", "title": f"Design {index}",
                        "story_promise": f"Family pressure, visual interpretation {index}",
                        "composition": "Mother in foreground; adult son behind, one cohesive environment.",
                        "medium": medium, "palette": "Indigo, amber and cream",
                        "characters": ["Mara", "Leo"] if index < 3 else ["Mara"],
                        "image_prompt": "A poised mature woman in layered domestic space, coherent dramatic lighting. No words.",
                        "title_position": position}
                       for index, (medium, position) in enumerate([
                           ("commercial retouched photography", "top"), ("cinematic montage", "bottom"),
                           ("semi-realistic illustration", "left"), ("expressive painting", "top")], 1)]}


class Model:
    def __init__(self, *outputs):
        self.outputs = list(outputs) or [json.dumps(response())]
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, json.loads(user)))
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_complete_primary_and_explicit_attachment_are_read_once_in_one_call(poster_source, metadata):
    model, progress = Model(), []
    preferences = {"composition_bias": "relationship_poster", "ui_language": "zh"}
    plan = plan_poster(poster_source, metadata, preferences, model, model="configured-model", progress=progress.append)
    assert len(model.calls) == 1
    system, request = model.calls[0]
    assert request["phase"] == "plan_poster"
    documents = request["source"]["documents"]
    assert len(documents) == 2
    for document in documents:
        body = poster_source.document_bytes(document["document_id"])
        assert document["text"].encode() == body
        assert document["sha256"] == hashlib.sha256(body).hexdigest()
        assert document["byte_length"] == len(body)
    assert "No evidence ledger" in system and "untrusted task data" in system
    assert "source" in request and "story_analysis" not in request
    assert plan["source_bundle_sha256"] == poster_source.source_bundle_sha256
    assert plan["metadata"] == metadata
    assert plan["pipeline_kind"] == "skeleton_poster"
    assert plan["completed_model_calls"] == 1
    assert [direction["id"] for direction in plan["directions"]] == [f"direction_{index:02d}" for index in range(1, 5)]
    assert [item["completed_model_calls"] for item in progress] == [0, 1]
    assert progress[-1]["response_checkpoint"]["raw"] == json.dumps(response())
    assert plan["request_budgets"][0]["context_check"] == "not_configured"


def test_artistic_staging_supports_multiple_media_and_solo_without_semantic_gate(poster_source, metadata):
    plan = plan_poster(poster_source, metadata, {}, Model())
    assert len({item["medium"] for item in plan["directions"]}) == 4
    assert plan["directions"][2]["characters"] == ["Mara"]
    assert all(item["expression_mode"] == "poster_staging" for item in plan["directions"])
    assert all("evidence" not in item for item in plan["directions"])


def test_one_schema_correction_preserves_complete_source_and_formal_metadata(poster_source, metadata):
    invalid = response()
    invalid["directions"][2]["title_position"] = "somewhere"
    model = Model(json.dumps(invalid), json.dumps(response()))
    plan = plan_poster(poster_source, metadata, {}, model)
    assert plan["completed_model_calls"] == 2
    first, correction = model.calls[0][1], model.calls[1][1]
    assert correction["phase"] == "correct_poster_format"
    assert correction["source"] == first["source"]
    assert correction["metadata"] == first["metadata"] == metadata
    assert correction["previous_response"] == json.dumps(invalid)
    assert "title_position" in correction["format_error"]


def test_second_format_failure_stops_after_two_calls(poster_source, metadata):
    model = Model("not JSON", "still not JSON")
    with pytest.raises(PosterPlanningError, match="single format correction") as error:
        plan_poster(poster_source, metadata, {}, model)
    assert error.value.code == "invalid_response"
    assert error.value.details["completed_model_calls"] == 2
    assert len(model.calls) == 2


def test_transport_failure_is_never_retried(poster_source, metadata):
    model = Model(TimeoutError("remote outcome unknown"))
    with pytest.raises(TimeoutError):
        plan_poster(poster_source, metadata, {}, model)
    assert len(model.calls) == 1


def test_budget_accounts_for_utf8_and_correction_before_call(poster_source, metadata):
    model = Model()
    with pytest.raises(PosterPlanningError) as error:
        plan_poster(poster_source, metadata, {}, model, max_request_bytes=9000, max_response_bytes=6000)
    assert error.value.code == "request_budget"
    assert error.value.details["actual_bytes"] > len(poster_source.primary_bytes)
    assert error.value.details["reserved_bytes"] == 16096
    assert not model.calls


def test_context_preflight_reserves_both_reply_and_possible_correction(poster_source, metadata):
    model = Model()
    with pytest.raises(PosterPlanningError) as error:
        plan_poster(poster_source, metadata, {}, model, context_window_tokens=10000,
                    token_counter=lambda _: 8000, max_output_tokens=2000, output_token_limit_supported=True)
    assert error.value.code == "context_budget"
    assert error.value.details["reserved_tokens"] == 6048
    assert not model.calls
    with pytest.raises(PosterPlanningError) as estimated_error:
        plan_poster(poster_source, metadata, {}, model, context_window_tokens=10000)
    assert estimated_error.value.details["input_tokens_estimated"] is True
    assert estimated_error.value.details["reserved_tokens"] == 130048
    assert not model.calls


def test_oversized_received_response_stops_without_schema_retry(poster_source, metadata):
    model = Model(" " * 2001)
    with pytest.raises(PosterPlanningError) as error:
        plan_poster(poster_source, metadata, {}, model, max_response_bytes=2000)
    assert error.value.code == "response_budget"
    assert len(model.calls) == 1


def test_snapshot_mutation_is_rejected_before_model(poster_source, metadata):
    changed = replace(poster_source, _bodies={**poster_source._bodies, "primary": b"changed ending"})
    model = Model()
    with pytest.raises(PosterPlanningError, match="hash or length mismatch"):
        plan_poster(changed, metadata, {}, model)
    assert not model.calls


def test_duplicate_designs_are_advisory_and_do_not_spend_a_repair_call(poster_source, metadata):
    data = response()
    data["directions"] = [deepcopy(data["directions"][0]) for _ in range(4)]
    model = Model(json.dumps(data))
    plan = plan_poster(poster_source, metadata, {}, model)
    assert len(model.calls) == 1
    assert plan["advisories"]


def test_storage_bridge_preserves_exact_title_and_full_constraints_without_old_gate(poster_source, metadata):
    metadata["title"] = "她仍然爱着他们 — 但选择属于她！"
    plan = plan_poster(poster_source, metadata, {}, Model())
    brief, concepts = poster_storage_objects(plan)
    assert brief.title == metadata["title"]
    assert brief.source_prompt_sha256 == poster_source.source_content_sha256
    assert CoverBrief.from_dict(brief.to_dict()) == brief
    assert len(CoverSet.new("book", brief, concepts).candidates) == 4
    for concept in concepts:
        assert CoverConcept.from_dict(concept.to_dict()) == concept
        assert metadata["title"] in concept.generation_prompt
        assert "TEXT-FREE" in concept.generation_prompt
        assert "New York" in concept.generation_prompt
        assert "Preserve mature age" in concept.generation_prompt
        assert concept.scene_plan["pipeline_kind"] == "skeleton_poster"
        assert concept.scene_plan["snapshot_sha256"] == poster_source.snapshot_sha256
        assert "evidence_ledger" not in concept.scene_plan
        assert concept.scene_plan["_poster_recipe"]["title_layout"]["title"] == metadata["title"]
        assert concept.scene_plan["_poster_recipe"]["title_layout"]["font_sha256"]


def test_default_recipe_actually_composes_full_long_title_at_all_positions(poster_source, metadata):
    from core.cover_poster_typography import compose_poster_cover

    metadata["title"] = "He Chose His Hidden Family for Twenty Years and Abandoned Our Daughter During Her Most Difficult Days"
    plan = plan_poster(poster_source, metadata, {}, Model())
    _, concepts = poster_storage_objects(plan)
    image = BytesIO()
    Image.new("RGB", (512, 768), "#536478").save(image, format="PNG")
    for concept in concepts:
        recipe = concept.scene_plan["_poster_recipe"]
        composed = compose_poster_cover(image.getvalue(), recipe["title_layout"], expected_title=metadata["title"])
        assert composed.artwork_sha256 == hashlib.sha256(image.getvalue()).hexdigest()
        assert composed.final_cover_sha256 != composed.artwork_sha256
        assert composed.text_bounds


def test_local_edit_validation_preserves_source_and_never_calls_model(poster_source, metadata):
    model = Model()
    plan = plan_poster(poster_source, metadata, {}, model)
    edited = deepcopy(plan)
    edited["directions"][1]["composition"] = "A unified triangular arrangement in one domestic space."
    normalized = normalize_poster_plan(edited)
    assert normalized["directions"][1]["composition"] != plan["directions"][1]["composition"]
    assert normalized["metadata"] == plan["metadata"]
    assert normalized["source_bundle_sha256"] == plan["source_bundle_sha256"]
    assert len(model.calls) == 1
    edited["directions"][1]["characters"] = ["A model-invented third party"]
    with pytest.raises(PosterPlanningError, match="reference names"):
        normalize_poster_plan(edited)


@pytest.mark.parametrize("key,value", [("title", ""), ("author", None), ("market_scope", 123)])
def test_invalid_metadata_never_spends_a_call(poster_source, metadata, key, value):
    metadata[key] = value
    model = Model()
    with pytest.raises(PosterPlanningError):
        plan_poster(poster_source, metadata, {}, model)
    assert not model.calls


@pytest.mark.parametrize("genre,medium,cast_count", [
    ("Mature domestic fiction", "commercial retouched photography", 2),
    ("Romantic comedy", "editorial illustration", 2),
    ("Epic fantasy", "semi-realistic fantasy painting", 2),
    ("Solo wilderness survival", "cinematic landscape photography", 1),
    ("Urban mystery", "symbolic noir montage", 1),
    ("Science fiction", "retro-futurist illustration", 1),
    ("Historical family saga", "painterly ensemble poster", 2),
    ("Children's adventure", "colourful storybook illustration", 2),
])
def test_protocol_has_no_genre_or_ensemble_quota(poster_source, metadata, genre, medium, cast_count):
    # This verifies protocol freedom with fixtures, not a model's design quality.
    data = response()
    data["story"]["cast"] = data["story"]["cast"][:cast_count]
    metadata["genre"] = genre
    for direction in data["directions"]:
        direction["characters"] = [item["name"] for item in data["story"]["cast"]]
        direction["medium"] = medium
    model = Model(json.dumps(data))
    plan = plan_poster(poster_source, metadata, {}, model)
    assert len(model.calls) == 1
    assert all(direction["medium"] == medium for direction in plan["directions"])
    assert plan["metadata"]["genre"] == genre
    assert all(len(direction["characters"]) == cast_count for direction in plan["directions"])


def title_recipe():
    return {"lines": ["She Still Loved Them,", "but the Choice", "Was Hers"],
            "font_id": "serif-bold", "color": "#FFFFFF", "align": "left",
            "font_size_min": .025, "font_size_max": .11,
            "region": [.06, .05, .88, .33], "line_spacing": .2, "letter_spacing": .001,
            "line_styles": [{"font_id": "sans-bold", "scale": .8, "color": "#FFFFFF"},
                            {"font_id": "serif-bold", "scale": 1.1, "color": "#FFDB91"},
                            {"font_id": "serif-bold", "scale": 1.5, "color": "#FFDB91"}]}


def test_planned_title_hierarchy_is_executable_and_preserved_through_storage(poster_source, metadata):
    from core.cover_poster_typography import compose_poster_cover
    data = response()
    data["directions"][0]["title_recipe"] = title_recipe()
    model = Model(json.dumps(data))
    plan = plan_poster(poster_source, metadata, {}, model)
    request = model.calls[0][1]
    assert "serif-bold" in request["title_capabilities"]["font_ids"]
    assert request["title_capabilities"]["fixed_font_weights"]["serif-bold"] == "bold"
    assert plan["directions"][0]["title_recipe"]["lines"] == title_recipe()["lines"]
    brief, concepts = poster_storage_objects(plan)
    saved = CoverSet.from_dict(CoverSet.new("project", brief, concepts).to_dict())
    layout = saved.concepts[0].scene_plan["_poster_recipe"]["title_layout"]
    assert layout["title"] == metadata["title"]
    assert layout["lines"] == title_recipe()["lines"]
    assert layout["line_styles"] == title_recipe()["line_styles"]
    assert layout["region"] == title_recipe()["region"]
    assert set(layout["fonts"]) == {"sans-bold", "serif-bold"}
    stream = BytesIO()
    Image.new("RGB", (1024, 1536), "navy").save(stream, format="PNG")
    rendered = compose_poster_cover(stream.getvalue(), layout, expected_title=metadata["title"])
    first, last = rendered.text_bounds[0], rendered.text_bounds[-1]
    assert last[3] - last[1] > first[3] - first[1]  # The hierarchy actually affects letter size.
    assert "[0.06,0.05,0.88,0.33]" in concepts[0].generation_prompt
    assert "render no typography" in concepts[0].generation_prompt
    assert len(model.calls) == 1


@pytest.mark.parametrize("bad", [
    {"lines": ["Was Hers", "She Still Loved Them, but the Choice"]},
    {"font_family": "Futura"},
    {"font_id": "imaginary-display-font"},
    {"font_size_min": .02, "font_size_max": .02, "region": [.05, .05, .02, .02]},
    {"safe_zones": [[0, 0, 1, 1]]},
    {"line_styles": [{"scale": 2}]},
    {"line_spacing": 2},
    {"font_id": "serif-bold", "font_weight": "light"},
    {"stroke": {"width": .03}},
    {"shadow": {"opacity": 2}},
])
def test_unexecutable_title_recipe_uses_only_existing_single_format_correction(poster_source, metadata, bad):
    broken, fixed = response(), response()
    broken["directions"][0]["title_recipe"] = bad
    fixed["directions"][0]["title_recipe"] = title_recipe()
    model = Model(json.dumps(broken), json.dumps(fixed))
    plan = plan_poster(poster_source, metadata, {}, model)
    assert len(model.calls) == 2 and plan["completed_model_calls"] == 2
    assert "title_recipe" in model.calls[1][1]["format_error"]
    assert model.calls[1][1]["phase"] == "correct_poster_format"
    assert plan["directions"][0]["title_recipe"]["lines"] == title_recipe()["lines"]


def test_title_recipe_cannot_retry_past_the_single_format_correction(poster_source, metadata):
    broken = response()
    broken["directions"][0]["title_recipe"] = {"lines": ["Rewritten title"]}
    model = Model(json.dumps(broken), json.dumps(broken))
    with pytest.raises(PosterPlanningError, match="single format correction"):
        plan_poster(poster_source, metadata, {}, model)
    assert len(model.calls) == 2


def test_older_plans_without_title_recipe_keep_the_previous_fallback(poster_source, metadata):
    plan = plan_poster(poster_source, metadata, {}, Model())
    _, concepts = poster_storage_objects(plan)
    assert all("title_recipe" not in direction for direction in plan["directions"])
    assert concepts[0].scene_plan["_poster_recipe"]["title_layout"]["position"] == "top"
    assert concepts[2].scene_plan["_poster_recipe"]["title_layout"]["align"] == "left"
    assert concepts[2].scene_plan["_poster_recipe"]["title_layout"]["region"] == [.06, .32, .45, .56]


def test_planned_fixed_weight_and_contrast_effects_use_actual_supported_controls(poster_source, metadata):
    data = response()
    data["directions"][0]["title_recipe"] = {**title_recipe(), "font_weight": 700,
        "stroke": {"width": .001, "color": "#202020"},
        "shadow": {"color": "#000000", "opacity": .4, "blur": .002, "offset_x": .001, "offset_y": .002},
        "backdrop": {"color": "#102030", "opacity": .35, "mode": "gradient", "direction": "top"}}
    model = Model(json.dumps(data))
    plan = plan_poster(poster_source, metadata, {}, model)
    _, concepts = poster_storage_objects(plan)
    layout = concepts[0].scene_plan["_poster_recipe"]["title_layout"]
    assert layout["font_weight"] == "bold"
    assert layout["stroke"]["width"] == .001
    assert layout["shadow"]["opacity"] == .4
    assert layout["backdrop"]["mode"] == "gradient"
    assert len(model.calls) == 1
