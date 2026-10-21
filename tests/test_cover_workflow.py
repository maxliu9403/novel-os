from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from api.cover_service import CoverService
from core.cover_analysis import CoverStoryAnalyzer, derive_analysis_context
from core.cover_director import CoverArtDirector
from core.cover_models import CoverSet
from core.cover_sources import CoverSourceContext, CoverSourceSelector, capture_source
from core.cover_store import CoverConflict, CoverStore
from core.cover_workflow import CoverWorkflow
from tests.test_cover_analysis_snapshot import ModelFixture, source
from tests.test_cover_campaign import context as campaign_context


class FullStoryFixture:
    """Local model double for both exhaustive analysis and campaign direction."""
    model = "fixture-analyzer"

    def __init__(self):
        self.extractor = ModelFixture()
        self.extractor.mutate = self._with_events
        self._complete = self.extractor

    @staticmethod
    def _with_events(request, result):
        if request["phase"] == "global":
            template = result["events"][0]
            result["events"] = [{**template, "event_id": f"event-{i}",
                "epoch": f"chapter-{i + 1}", "location": "apartment entry"} for i in range(4)]
        return result

    def plan(self, brief, *, count=4, evidence_ledger, story_analysis):
        payload = deepcopy(campaign_context()[2])
        ref = evidence_ledger.allowed_refs[0]
        def replace_refs(value):
            if isinstance(value, dict):
                return {key: replace_refs(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace_refs(item) for item in value]
            return ref if isinstance(value, str) and value.startswith("evidence:") else value
        payload = replace_refs(payload)
        for plan in payload["plans"]:
            plan["story_evidence_refs"] = list(evidence_ledger.allowed_refs)
            # Native fixture avoids a host-font dependency in source integrity tests.
            plan["campaign"]["title_render_mode"] = "native"
        return CoverArtDirector.from_fixture(payload).plan(
            brief, count=count, evidence_ledger=evidence_ledger, story_analysis=story_analysis)


@pytest.fixture
def workflow(source):
    context, _, project = source
    store = CoverStore(project)
    context = replace(context, cover_root=store.root)
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    director = FullStoryFixture()
    result = CoverStoryAnalyzer(complete=director._complete, model=director.model,
                               max_repair_attempts=0).analyze(context, snapshot)
    return CoverWorkflow(context, store), result, director, project


def approve(flow, direction, **kwargs):
    return flow.approve(direction.direction_id, expected_brief_sha256=direction.brief_sha256,
        approved_direction_sha256=direction.direction_sha256, confirm_source=True, **kwargs)


def bound_set(flow, direction):
    brief, concepts, kwargs = flow.generation_arguments(direction.direction_id, direction.direction_sha256,
                                                      reserve=False)
    concepts = CoverService._bind_generation(concepts, brief, kwargs["compiled_recipes"],
                                            kwargs["extra_image_attempts"], kwargs["source_binding"])
    return flow.store.create(CoverSet.new("book", brief, concepts))


def test_approval_freezes_exact_recipe_and_one_batch_budget(workflow, monkeypatch):
    flow, analysis, director, _ = workflow
    direction = flow.plan(analysis.analysis_id, director)
    assert direction.schema_version == 2 and len(direction.compiled_recipes) == 4
    with pytest.raises(CoverConflict):
        flow.generation_arguments(direction.direction_id, direction.direction_sha256)
    with pytest.raises(CoverConflict, match="审核"):
        flow.approve(direction.direction_id, expected_brief_sha256=direction.brief_sha256,
            approved_direction_sha256=direction.direction_sha256, confirm_source=False)
    approved = approve(flow, direction, extra_image_attempts=1)
    assert approved.direction_sha256 == direction.direction_sha256
    import core.cover_prompt_compiler as compiler
    def changed_compiler(*args, **kwargs):
        raise AssertionError("An approved recipe must never be recompiled")
    monkeypatch.setattr(compiler, "compile_cover_prompt", changed_compiler)
    _, concepts, kwargs = flow.generation_arguments(direction.direction_id, direction.direction_sha256)
    assert kwargs["extra_image_attempts"] == 1
    assert [x.generation_prompt for x in concepts] == [direction.compiled_recipes[x.concept_id]["image_prompt"] for x in concepts]
    with pytest.raises(CoverConflict, match="额度"):
        flow.generation_arguments(direction.direction_id, direction.direction_sha256)
    approve(flow, direction, extra_image_attempts=1)
    assert flow.approval(direction.direction_id)["batch_reserved"] is True
    with pytest.raises(CoverConflict, match="replenished"):
        approve(flow, direction, extra_image_attempts=0)


def test_current_head_change_revokes_old_approval_but_new_pinned_direction_is_allowed(workflow):
    flow, analysis, director, project = workflow
    old = approve(flow, flow.plan(analysis.analysis_id, director))
    (project / "outputs/input/prompt.md").write_text("Different novel")
    with pytest.raises(CoverConflict, match="骨架已变化"):
        flow.validate(old.direction_id, require_approval=True)
    assert flow.store.load_direction(old.direction_id).status == "stale"
    new = approve(flow, flow.plan(analysis.analysis_id, director, freshness_policy="pinned_snapshot"))
    flow.validate(new.direction_id, require_approval=True)
    with pytest.raises(CoverConflict):
        flow.validate(old.direction_id, require_approval=True, require_latest=False)


def test_candidate_binding_recipe_and_latest_generation_are_verified(workflow):
    flow, analysis, director, _ = workflow
    direction = approve(flow, flow.plan(analysis.analysis_id, director, freshness_policy="pinned_snapshot"))
    cover_set = bound_set(flow, direction)
    candidate = cover_set.candidates[0]
    flow.validate_candidate(cover_set.cover_set_id, candidate.candidate_id)
    flow.plan(analysis.analysis_id, director, freshness_policy="pinned_snapshot")
    flow.validate_candidate(cover_set.cover_set_id, candidate.candidate_id)
    with pytest.raises(CoverConflict):
        flow.validate_candidate(cover_set.cover_set_id, candidate.candidate_id, for_generation=True)
    scene = deepcopy(cover_set.concepts[0].scene_plan)
    scene["_compiled_recipe"]["image_prompt"] += " changed"
    concepts = (replace(cover_set.concepts[0], scene_plan=scene), *cover_set.concepts[1:])
    flow.store.save(replace(cover_set, concepts=concepts), expected_revision=cover_set.revision)
    with pytest.raises(CoverConflict, match="recipe"):
        flow.validate_candidate(cover_set.cover_set_id, candidate.candidate_id)


def test_immutable_analysis_or_snapshot_tamper_blocks_approval(workflow):
    flow, analysis, director, _ = workflow
    direction = flow.plan(analysis.analysis_id, director)
    path = flow.store.root / "analyses" / analysis.analysis_id / "analysis.json"
    payload = json.loads(path.read_text())
    payload["story"]["core_conflict"] = "Counterfeit conflict"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        approve(flow, direction)
    assert not flow.approval(direction.direction_id)


def test_import_candidate_delegates_selection_but_cannot_generate(workflow, monkeypatch):
    flow, analysis, director, _ = workflow
    direction = approve(flow, flow.plan(analysis.analysis_id, director))
    cover_set = bound_set(flow, direction)
    scene = {**cover_set.concepts[0].scene_plan, "_cover_import": {"transfer_id": "fixture"}}
    cover_set = flow.store.save(replace(cover_set, concepts=(replace(cover_set.concepts[0], scene_plan=scene),
                                                            *cover_set.concepts[1:])), expected_revision=cover_set.revision)
    calls = []
    monkeypatch.setattr("core.cover_transfer.validate_import_selection", lambda *args: calls.append(args))
    flow.validate_candidate(cover_set.cover_set_id, cover_set.candidates[0].candidate_id)
    assert len(calls) == 1
    with pytest.raises(CoverConflict, match="Imported"):
        flow.validate_candidate(cover_set.cover_set_id, cover_set.candidates[0].candidate_id, for_generation=True)


def test_candidate_cannot_increase_approved_repair_budget(workflow):
    flow, analysis, director, _ = workflow
    direction = approve(flow, flow.plan(analysis.analysis_id, director), extra_image_attempts=0)
    cover_set = bound_set(flow, direction)
    candidate = cover_set.candidates[0]
    flow.validate_candidate(cover_set.cover_set_id, candidate.candidate_id, for_generation=True)
    scene = deepcopy(cover_set.concepts[0].scene_plan)
    scene["_generation_authorization"]["extra_image_attempts"] = 1
    flow.store.save(replace(cover_set, concepts=(replace(cover_set.concepts[0], scene_plan=scene),
                                                *cover_set.concepts[1:])), expected_revision=cover_set.revision)
    with pytest.raises(CoverConflict, match="repair allowance"):
        flow.validate_candidate(cover_set.cover_set_id, candidate.candidate_id, for_generation=True)


def test_frozen_section_constraints_reach_approved_generation(tmp_path):
    from tests.test_cover_models_v2 import two_character_fixture

    project = tmp_path / "project"
    (project / "outputs/input").mkdir(parents=True)
    handoff = two_character_fixture()
    handoff["cover_constraints"] = {
        "constraint_version": "cover-constraints.v1",
        "must_preserve_story_facts": ["Mara and Oren are estranged spouses"],
        "must_show_if_supported": ["char_mara"],
        "must_not_show": ["final reconciliation"],
        "spoiler_ceiling": "Do not reveal the final relationship outcome.",
        "source_refs": ["section:6.relationship_map", "section:9.node:door_choice"],
    }
    (project / "outputs/input/prompt.md").write_text(
        "# The Door Is Mine\n\n"
        "## 6. 人物关系及变化\nMara and Oren are estranged spouses.\n\n"
        "## 9. 全书／分卷／系列结构\nnode_id: door_choice\nMara withdraws the key.\n\n"
        "COVER_HANDOFF_BEGIN\n```json\n" + json.dumps(handoff, ensure_ascii=False)
        + "\n```\nCOVER_HANDOFF_END\n",
        encoding="utf-8",
    )
    store = CoverStore(project)
    context = CoverSourceContext(store.root, "default:project:book", lambda _: project)
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    director = FullStoryFixture()
    analysis = CoverStoryAnalyzer(
        complete=director._complete, model=director.model, max_repair_attempts=0,
    ).analyze(context, snapshot)
    flow = CoverWorkflow(context, store)

    direction = approve(flow, flow.plan(analysis.analysis_id, director))
    brief, concepts, arguments = flow.generation_arguments(
        direction.direction_id, direction.direction_sha256,
    )

    assert brief.cover_constraints.source_refs == (
        "section:6.relationship_map", "section:9.node:door_choice",
    )
    assert len(concepts) == 4
    assert arguments["source_binding"]["snapshot_id"] == snapshot.snapshot_id
    assert arguments["extra_image_attempts"] == 0
    for concept in concepts:
        assert "Do not depict: final reconciliation" in concept.generation_prompt
        assert "Do not reveal the final relationship outcome." in concept.generation_prompt
        assert concept.generation_prompt == direction.compiled_recipes[concept.concept_id]["image_prompt"]
    assert flow.approval(direction.direction_id)["batch_reserved"] is True


def test_legacy_unconstrained_approval_keeps_original_evidence_digest(workflow, monkeypatch):
    from core.cover_analysis import canonical_hash, load_analysis

    flow, analysis, director, _ = workflow
    # Historical analysis records predate both optional source-constraint keys.
    path = flow.store.root / "analyses" / analysis.analysis_id / "analysis.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("source_constraints_payload", None)
    payload.pop("source_constraint_sources", None)
    payload.pop("analysis_sha256")
    payload["analysis_sha256"] = canonical_hash(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    historical = load_analysis(flow.context, analysis.analysis_id)
    brief, ledger = derive_analysis_context(flow.context, historical)
    # The previous ledger contained per-document hashes, never a bare snapshot
    # key. Freeze that historical representation into a real approval record.
    old_ledger = replace(ledger, source_files={
        key: value for key, value in ledger.source_files.items()
        if key != f"snapshot:{historical.snapshot_id}"
    })
    with monkeypatch.context() as setup:
        setup.setattr("core.cover_analysis.derive_analysis_context", lambda *_: (brief, old_ledger))
        approved = approve(flow, flow.plan(historical.analysis_id, director))
    original_hash = approved.direction_sha256
    original_approval = flow.approval(approved.direction_id)

    restored, _ = flow.validate(approved.direction_id, require_approval=True)
    _, concepts, arguments = flow.generation_arguments(
        approved.direction_id, original_hash, reserve=False,
    )

    assert restored.direction_sha256 == original_hash
    assert flow.approval(approved.direction_id) == original_approval
    assert arguments["source_binding"]["evidence_sha256"] == canonical_hash(old_ledger.to_dict())
    assert [concept.generation_prompt for concept in concepts] == [
        approved.compiled_recipes[plan.concept_id]["image_prompt"] for plan in approved.plans
    ]
