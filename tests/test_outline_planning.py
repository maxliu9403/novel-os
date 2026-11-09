"""Fake-model integration checks for bounded, quality-preserving planning."""

import copy
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from commercial_fixtures import commercial_story_fixture
from narrative_format import infer_narrative_format, volume_contract_template
from novel_classification import infer_classification
from orchestrator import NovelOrchestrator
from outline_planning import (
    CHAPTER_TEXT, REVIEW_CHECKS, OutlinePlanningError, StagedOutlinePlanner,
    public_model_identity, render_foundation,
)
from pipeline_runtime import PipelineRuntime, PipelineStopped


SOURCE = "Mara refuses the sale, rebuilds the studio, and chooses independent work. Her grief and attachment remain real."


class PlanningModel:
    provider = "fake"
    model = "fixture-planner"
    reasoning_effort = "high"

    def __init__(self, *, fail_batch=None, reviews=None, mutate_global=None):
        self.calls = []
        self.fail_batch = fail_batch
        self.reviews = list(reviews or [])
        self.mutate_global = mutate_global

    def complete(self, system, user):
        context = json.loads(user.rsplit("\n\n", 1)[1])
        if user.startswith("STAGE: format_correction"):
            self.calls.append(("format", context))
            return context["completed_response"]
        if user.startswith('STAGE: global_reference_repair'):
            self.calls.append(('global_repair', context))
            return json.dumps({'status': 'requires_author', 'reason': 'This fixture needs a story decision, not a chapter reference correction.', 'edits': []})
        if "STAGE: targeted_repair" in user:
            stage = "repair"
        elif user.startswith("STAGE: global_design_seed"):
            stage = "seed"
        elif user.startswith("STAGE: chapter_roadmap"):
            stage = "roadmap"
        elif user.startswith("STAGE: global_design"):
            stage = "global"
        elif user.startswith("STAGE: semantic_review"):
            stage = "review"
        else:
            stage = "batch"
        self.calls.append((stage, context))
        assert context.get("complete_adopted_source") or context["adopted_source_projection"]["text"]
        if stage in {"global", "seed"}:
            result = global_design(context)
            if stage == "seed":
                result.pop("chapter_map")
            if self.mutate_global:
                self.mutate_global(result)
            return json.dumps(result)
        if stage == "roadmap":
            return json.dumps({"chapter_map": [roadmap_entry(n) for n in context["requested_chapters"]],
                               "source_anchors": []})
        if stage == "review":
            return json.dumps(self.reviews.pop(0) if self.reviews else review())
        numbers = context["requested_chapters"]
        if stage == "batch" and self.fail_batch == numbers[0]:
            raise TimeoutError("fixture timeout")
        return json.dumps({"chapters": [chapter(n, repaired=stage == "repair") for n in numbers]})


def chapter(number, *, repaired=False):
    result = {key: f"{key}: Mara weighs grief against independence in chapter {number}." for key in CHAPTER_TEXT}
    result.update(number=number, title=f"Mara's choice {number}", pov="Mara", target_words=1000,
                  continuity_obligations=["The sale remains refused."], setups=[], payoffs=[], reserved_later_payoffs=[],
                  source_anchor_ids=["source_1"], scenes=[{
                      "pov": "Mara", "goal": "Preserve the studio", "conflict": "Financial pressure",
                      "change": "Mara accepts a specific cost", "emotional_progression": "Grief mixed with hope",
                      "subtext": "She keeps the old cup but declines the sale.",
                  }], bespoke_detail={"gesture": "She rotates the cracked cup toward the light."})
    if repaired:
        result["incoming_consequence"] = "Repaired exact carryover from the previous chapter."
    return result


def roadmap_entry(number):
    return {"number": number, "purpose": f"Specific movement {number}", "incoming_consequence": "Prior cost",
            "outgoing_commitment": "Next choice", "source_anchor_ids": ["source_1"],
            "reserved_reveals": [], "ending_obligations": []}


def global_design(context):
    count = context["chapter_count"]
    volumes = copy.deepcopy(context["volume_contract_template"])
    for i, item in enumerate(volumes, 1):
        item.update(title=f"Movement {i}", central_conflict=f"Pressure {i}", volume_promise=f"Reward {i}",
                    protagonist_shift=f"Shift {i}", payoff=f"Payoff {i}", carryover_hook=f"Consequence {i}" if i < len(volumes) else "")
    return {
        "title": "The Studio", "premise": "Mara rebuilds after refusing a sale.", "logline": "A widow preserves her work.",
        "thematic_argument": "Independence can coexist with grief.", "themes": ["agency"],
        "setting": {"time_period": "contemporary", "primary_location": "studio", "world_rules": ["ordinary world"]},
        "characters": [{"id": "mara", "name": "Mara", "role": "protagonist", "arc": "agency", "gender": "woman", "pronouns": "she/her"}],
        "relationships": [{"characters": ["mara"], "history": "Her lost partner's memory remains important."}],
        "plot_threads": [{"id": "studio", "name": "The studio", "description": "Keep independent work", "type": "main", "priority": 5}],
        "causal_chain": ["Refusal requires rebuilding"], "act_beat_map": [{"act": 1, "chapters": list(range(1, count + 1))}],
        "style": {"tone": "intimate", "pov": "third_limited", "tense": "past", "prose_style": "balanced"},
        "ending_contract": {
            "schema_version": 1, "enforce": True,
            "finale_window": {"start_chapter": max(1, count - 3), "end_chapter": count},
            "main_conflict": {"thread_id": "studio", "required_status": "resolved", "protagonist_choice": "Keep the studio", "consequence": "Independent work"},
            "character_arcs": [{"character_id": "mara", "required_arc_stage": "resolution", "required_outcome": "independent_work", "required_choice": "refuse sale"}],
            "plot_payoffs": [], "antagonist_outcome": {"required": False},
            "emotional_contract": {"reader_emotion": "hope", "afterglow_state": "earned independence"},
        },
        "volume_contracts": volumes,
        "chapter_map": [roadmap_entry(n) for n in range(1, count + 1)],
        "source_anchors": [{"id": "source_1", "quote": context["complete_adopted_source"]}],
        "assumptions": [], "structural_risks": [],
    }


def review(*issues):
    return {"status": "blocked" if issues else "passed", "coverage": dict.fromkeys(REVIEW_CHECKS, True), "issues": list(issues)}


def issue(scope="chapter", number=3):
    return {"scope": scope, "chapter": number, "message": "Preserve the consequence of refusing the sale.", "source_evidence": "Mara refuses the sale"}


def make_planner(tmp_path, model, *, count=20, source=SOURCE, final_validator=None, **options):
    classification = infer_classification(genre="Women's Fiction", premise=source, audience="women 45-60")
    fmt = infer_narrative_format(classification, chapters=count, target_words=count * 1000, explicit_length=True, source="author_selected")
    brief = {"classification": classification.to_dict(), "narrative_format": fmt.to_dict()}
    commercial = options.pop("commercial", None)
    if commercial:
        brief.update(commercial_story_contract=commercial.to_dict(), commercial_story_contract_id=commercial.contract_id)

    def validate(candidate):
        return NovelOrchestrator._parse_story_foundation(render_foundation(candidate), count, commercial, fmt)

    return StagedOutlinePlanner(directory=tmp_path / "planning", source=source, brief=brief, chapter_count=count,
        target_words=count * 1000, client=model, narrative_format=fmt.to_dict(),
        commercial_contract=commercial.to_dict() if commercial else None,
        commercial_contract_id=commercial.contract_id if commercial else None,
        volume_template=volume_contract_template(fmt), final_validator=final_validator or validate, **options)


def test_twenty_chapters_merge_with_rich_scene_design_and_actual_saved_progress(tmp_path):
    model = PlanningModel()
    planner = make_planner(tmp_path, model)
    result = planner.run()
    assert [c["number"] for c in result["chapters"]] == list(range(1, 21))
    assert [stage for stage, _ in model.calls] == ["global"] + ["batch"] * 5 + ["review"]
    assert all(c["complete_adopted_source"] == SOURCE for _, c in model.calls)
    assert model.calls[2][1]["previous_completed_chapters"][-1]["number"] == 4
    assert model.calls[2][1]["frozen_global_design"] == model.calls[1][1]["frozen_global_design"]
    md = render_foundation(result)
    for preserved in ("cracked cup", "Grief mixed with hope", "sale remains refused", "reserved later payoffs", "earned independence"):
        assert preserved in md
    progress = json.loads((tmp_path / "planning/progress.json").read_text())
    assert progress["completed_batches"] == progress["total_batches"] == 5
    assert progress["status"] == "running"  # Publication is a separate, real save.
    assert "foundation_published" not in [m["id"] for m in progress["milestones"]]
    planner.published()
    assert json.loads((tmp_path / "planning/progress.json").read_text())["status"] == "complete"


def test_resume_keeps_saved_global_and_batches_and_does_not_recall_model(tmp_path):
    initial = PlanningModel(fail_batch=9)
    with pytest.raises(TimeoutError):
        make_planner(tmp_path, initial).run()
    progress = json.loads((tmp_path / "planning/progress.json").read_text())
    assert progress["completed_batches"] == 2
    resumed = PlanningModel()
    result = make_planner(tmp_path, resumed).run()
    assert len(result["chapters"]) == 20
    assert [stage for stage, _ in resumed.calls] == ["batch"] * 3 + ["review"]
    assert resumed.calls[0][1]["requested_chapters"] == [9, 10, 11, 12]
    again = PlanningModel()
    assert make_planner(tmp_path, again).run() == result
    assert again.calls == []


@pytest.mark.parametrize("change", ["source", "contract"])
def test_checkpoint_identity_invalidates_changed_source_configuration_or_contract(tmp_path, change):
    original = make_planner(tmp_path, PlanningModel(), count=4)
    original.run()
    model = PlanningModel()
    source = SOURCE + " The ending retains grief." if change == "source" else SOURCE
    changed = make_planner(tmp_path, model, count=5 if change == "contract" else 4, source=source)
    changed.run()
    assert model.calls[0][0] == "global"
    assert original.cache.exists() and changed.cache.exists() and original.cache != changed.cache


def test_switching_models_continues_verified_design_and_reviews_with_active_model(tmp_path):
    original = make_planner(tmp_path, PlanningModel(fail_batch=5), count=8)
    with pytest.raises(TimeoutError):
        original.run()
    saved = {name: (original.cache / name).read_bytes() for name in ["global.json", "batch-001.json"]}
    new_client = PlanningModel()
    new_client.model = "replacement-model"
    resumed = make_planner(tmp_path, new_client, count=8)
    assert len(resumed.run()["chapters"]) == 8
    assert [stage for stage, _ in new_client.calls] == ["batch", "review"]
    assert new_client.calls[0][1]["requested_chapters"] == [5, 6, 7, 8]
    assert resumed.cache != original.cache
    for name, raw in saved.items():
        assert (original.cache / name).read_bytes() == raw
        record = json.loads((resumed.cache / name).read_bytes())
        assert record["inherited_from"]["identity_hash"] == original.identity_hash
        assert record["inherited_from"]["record_sha256"] == hashlib.sha256(raw).hexdigest()
    calls = len(new_client.calls)
    assert make_planner(tmp_path, new_client, count=8).run()["chapters"]
    assert len(new_client.calls) == calls


def test_model_switch_refuses_corrupt_predecessor_before_dispatch(tmp_path):
    original = make_planner(tmp_path, PlanningModel(), count=4)
    original.run()
    path = original.cache / "global.json"
    record = json.loads(path.read_bytes())
    record["payload"]["title"] = "unverified replacement"
    path.write_text(json.dumps(record))
    for _ in range(2):
        client = PlanningModel()
        client.model = "second-model"
        with pytest.raises(OutlinePlanningError, match="checksum mismatch"):
            make_planner(tmp_path, client, count=4).run()
        assert not client.calls


def test_incomplete_suitability_echo_cannot_corrupt_confirmed_metadata(tmp_path):
    def corrupt_echo(result):
        # Live 51431ffa5230 failure: model dropped suitability dimensions after
        # completing the whole outline. The field belongs to approved code data.
        result["narrative_format"] = {"long_form_suitability": {"dimensions": []}}
        result["commercial_story_contract"] = {"invalid_model_echo": True}
    commercial = commercial_story_fixture()
    planner = make_planner(tmp_path, PlanningModel(mutate_global=corrupt_echo), count=4, commercial=commercial)
    result = planner.run()
    assert result["narrative_format"] == planner.locked["narrative_format"]
    assert result["commercial_story_contract"] == commercial.to_dict()
    assert result["commercial_story_contract_id"] == commercial.contract_id
    assert result["classification"] == planner.brief["classification"]


@pytest.mark.parametrize("field", ["narrative_format", "commercial_story_contract", "classification", "ending_contract"])
def test_metadata_loss_in_final_validation_cannot_skip_applicable_gates(tmp_path, field):
    def dropping(candidate):
        value = copy.deepcopy(candidate)
        value.pop(field)
        return value
    with pytest.raises(OutlinePlanningError):
        make_planner(tmp_path, PlanningModel(), count=4, final_validator=dropping, commercial=commercial_story_fixture()).run()
    assert not (tmp_path / "planning/coverage.json").exists()
    assert not list((tmp_path / "planning/checkpoints").glob("*/validated.json"))


def test_bad_json_preserves_raw_and_does_not_save_or_approve_global_candidate(tmp_path):
    model = PlanningModel()
    model.complete = lambda *_, **__: '{"title":"partial'
    with pytest.raises(OutlinePlanningError, match="complete JSON"):
        make_planner(tmp_path, model, count=4).run()
    # One bounded correction for the old single-object request, followed by
    # one for the smaller, independently recoverable seed. Neither is valid.
    replies = list((tmp_path / "planning").rglob("*.response.txt"))
    assert sorted(path.name.split(".")[0] for path in replies) == [
        "global", "global-format-correction", "global-seed-v1", "global-seed-v1-format-correction"]
    assert all(path.read_text() == '{"title":"partial' for path in replies)
    assert not list((tmp_path / "planning").rglob("global.json"))
    assert not list((tmp_path / "planning").rglob("validated.json"))
    resumed = PlanningModel()
    with pytest.raises(OutlinePlanningError, match="one bounded format correction"):
        make_planner(tmp_path, resumed, count=4).run()
    assert resumed.calls == []


def test_one_targeted_repair_preserves_other_chapters_and_requires_exact_candidate_review(tmp_path):
    model = PlanningModel(reviews=[review(issue()), review()])
    result = make_planner(tmp_path, model, count=8).run()
    assert [stage for stage, _ in model.calls][-3:] == ["review", "repair", "review"]
    assert model.calls[-2][1]["requested_chapters"] == [3]
    assert result["chapters"][2]["incoming_consequence"].startswith("Repaired")
    assert result["chapters"][1]["incoming_consequence"] == chapter(2)["incoming_consequence"]
    assert model.calls[-1][1]["candidate"]["chapters"][2]["incoming_consequence"].startswith("Repaired")


def test_failed_final_review_cannot_become_approved_on_resume_or_start_another_repair(tmp_path):
    model = PlanningModel(reviews=[review(issue()), review(issue())])
    with pytest.raises(OutlinePlanningError, match="still needs attention"):
        make_planner(tmp_path, model, count=4).run()
    resumed = PlanningModel()
    with pytest.raises(OutlinePlanningError, match="still needs attention"):
        make_planner(tmp_path, resumed, count=4).run()
    assert resumed.calls == []
    assert not list((tmp_path / "planning").rglob("validated.json"))


def test_global_source_conflict_is_blocked_not_disguised_as_chapter_only_repair(tmp_path):
    model = PlanningModel(reviews=[review(issue("global"))])
    with pytest.raises(OutlinePlanningError, match="Global design conflicts"):
        make_planner(tmp_path, model, count=4).run()
    assert "repair" not in [stage for stage, _ in model.calls]


def test_corrupted_cached_payload_is_not_trusted_or_silently_recomputed(tmp_path):
    planner = make_planner(tmp_path, PlanningModel(), count=4)
    planner.run()
    path = planner.cache / "batch-001.json"
    payload = json.loads(path.read_text())
    payload["payload"]["chapters"][0]["summary"] = "tampered"
    path.write_text(json.dumps(payload))
    model = PlanningModel()
    with pytest.raises(OutlinePlanningError, match="payload hash mismatch"):
        make_planner(tmp_path, model, count=4).run()
    assert model.calls == []


def test_cancelled_completed_response_is_not_promoted_and_prior_batch_survives(tmp_path):
    cancel = threading.Event()
    model = PlanningModel()
    complete = model.complete

    def cancel_after_reply(system, user):
        response = complete(system, user)
        if model.calls[-1][0] == "batch" and model.calls[-1][1]["requested_chapters"][0] == 5:
            cancel.set()
        return response

    model.complete = cancel_after_reply
    with PipelineRuntime(tmp_path, "run", cancellation_event=cancel):
        with pytest.raises(PipelineStopped):
            make_planner(tmp_path, model, count=8).run()
    assert list((tmp_path / "planning").rglob("batch-001.json"))
    assert not list((tmp_path / "planning").rglob("batch-002.json"))
    assert not list((tmp_path / "planning").rglob("validated.json"))


def test_missing_ending_cannot_produce_an_unenforced_final_foundation(tmp_path):
    model = PlanningModel(mutate_global=lambda value: value.pop("ending_contract"))
    with pytest.raises(OutlinePlanningError, match="ending_contract"):
        make_planner(tmp_path, model, count=4).run()
    assert [stage for stage, _ in model.calls] == ["global", "format"]


def test_one_local_format_correction_recovers_full_original_response_without_redesign(tmp_path):
    model = PlanningModel()
    complete = model.complete

    def missing_field_then_correct(system, user):
        if user.startswith("STAGE: format_correction"):
            context = json.loads(user.rsplit("\n\n", 1)[1])
            model.calls.append(("format", context))
            value = json.loads(context["completed_response"])
            value["chapters"][0]["continuity_obligations"] = ["Preserve the refused sale."]
            return json.dumps(value)
        raw = complete(system, user)
        if model.calls[-1][0] == "batch":
            value = json.loads(raw)
            value["chapters"][0].pop("continuity_obligations")
            return json.dumps(value)
        return raw

    model.complete = missing_field_then_correct
    result = make_planner(tmp_path, model, count=4).run()
    assert [stage for stage, _ in model.calls] == ["global", "batch", "format", "review"]
    assert result["chapters"][0]["bespoke_detail"] == chapter(1)["bespoke_detail"]
    resumed = PlanningModel()
    assert make_planner(tmp_path, resumed, count=4).run() == result
    assert resumed.calls == []


def test_orchestrator_publishes_only_reviewed_foundation_and_api_reads_saved_previews(tmp_path):
    from api.project_task_summary import task_sidecar

    project = tmp_path / "project"
    orch = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    orch.outline_planning_mode = "staged"
    orch.pipeline_run_id = "run1"
    orch._llm = PlanningModel()
    inputs = orch.outputs_dir / "input"
    inputs.mkdir(exist_ok=True)
    (inputs / "prompt.md").write_text(SOURCE)
    text = orch.plan_outline(num_chapters=4, target_words=4000)
    foundation = json.loads((inputs / "foundation.json").read_text())
    assert foundation["ending_contract"]["enforce"] is True
    assert len(foundation["chapters"]) == 4
    assert "cracked cup" in text
    progress = task_sidecar(project.resolve(), "run1", "planning/progress.json")
    assert progress is not None
    assert progress["schema_version"] == 1 and progress["run_id"] == "run1"
    assert progress["status"] == "complete"
    assert progress["milestones"][0]["summary"] == foundation["logline"]
    assert "Mara's choice 1" in progress["milestones"][1]["summary"]
    assert "thoughts" not in progress


def test_orchestrator_never_overwrites_published_outline_on_review_failure(tmp_path):
    project = tmp_path / "project"
    orch = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    orch.outline_planning_mode = "staged"
    orch.pipeline_run_id = "run1"
    orch._llm = PlanningModel(reviews=[review(issue("global"))])
    inputs = orch.outputs_dir / "input"
    inputs.mkdir(exist_ok=True)
    (inputs / "prompt.md").write_text(SOURCE)
    originals = {orch.outputs_dir / "outline.md": "Prior approved outline",
                 orch.outputs_dir / "outline.json": '{"previous":true}',
                 inputs / "foundation.json": '{"previous":true}'}
    for path, text in originals.items():
        path.write_text(text)
    with pytest.raises(OutlinePlanningError):
        orch.plan_outline(num_chapters=4, target_words=4000)
    assert all(path.read_text() == text for path, text in originals.items())


def test_staged_chapter_context_retains_late_scene_detail_past_legacy_50k_cutoff(tmp_path):
    from state_manager import ChapterState

    model = PlanningModel()
    foundation = make_planner(tmp_path, model, count=20).run()
    foundation["chapters"][0]["summary"] = "early detail " * 5000
    foundation["chapters"][19]["scenes"][0]["subtext"] = "UNIQUE_FINAL_CHAPTER_EMOTIONAL_CHOICE"
    project = tmp_path / "project"
    orch = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    orch.outline_planning_mode = "staged"
    inputs = orch.outputs_dir / "input"
    inputs.mkdir(exist_ok=True)
    (inputs / "foundation.json").write_text(json.dumps(foundation))
    (orch.outputs_dir / "outline.md").write_text(render_foundation(foundation))
    prompt = orch._generate_chapter_outline_prompt(ChapterState(number=20, title="Final", target_word_count=1000))
    assert "UNIQUE_FINAL_CHAPTER_EMOTIONAL_CHOICE" in prompt
    assert "early detail early detail" not in prompt
    assert "Next choice" in prompt  # Full global chapter commitments remain.


def test_public_model_identity_detects_resolved_endpoint_changes_without_disclosing_url():
    client = SimpleNamespace(provider_name="openai_compatible", model="same-model",
                             _backend=SimpleNamespace(base_url="https://private-a.example/v1?key=do-not-expose"))
    first = public_model_identity(client)
    client._backend.base_url = "https://private-b.example/v1?key=do-not-expose"
    second = public_model_identity(client)
    assert first["provider"] == "openai_compatible"
    assert first["endpoint_hash"] != second["endpoint_hash"]
    assert "do-not-expose" not in json.dumps([first, second])


def test_source_change_during_reply_prevents_checkpoint_or_publication(tmp_path):
    project = tmp_path / "project"
    orch = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    orch.outline_planning_mode = "staged"
    orch.pipeline_run_id = "source-change"
    inputs = orch.outputs_dir / "input"
    inputs.mkdir(exist_ok=True)
    source_path = inputs / "prompt.md"
    source_path.write_text(SOURCE)
    model = PlanningModel()
    complete = model.complete

    def change_source(system, user):
        raw = complete(system, user)
        source_path.write_text(SOURCE + " The author changed this while the model ran.")
        return raw

    model.complete = change_source
    orch._llm = model
    with pytest.raises(OutlinePlanningError, match="changed during planning"):
        orch.plan_outline(num_chapters=4, target_words=4000)
    assert not (inputs / "foundation.json").exists()
    assert not list((orch.outputs_dir / "runs/source-change").rglob("global.json"))
    assert list((orch.outputs_dir / "runs/source-change").rglob("*.response.txt"))
