"""Realistic persisted repair bases keep complete source without duplicate ledgers.

All analysis, review and direction completions below are local doubles. No test
uses project data, a network provider, or an image-generation call.
"""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from api import cover_repair_tasks as tasks
from core.cover_analysis import CoverStoryAnalyzer, derive_analysis_context
from core.cover_campaign_transport import decode_evidence_registry
from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_sources import CoverSourceContext, CoverSourceSelector, capture_source
from core.cover_store import CoverConflict, CoverStore
from core.cover_workflow import CoverWorkflow, _normalize_targeted_repair, digest
from tests.cover_review_fixtures import supported_review
from tests.test_cover_analysis_snapshot import publication_header
from tests.test_cover_workflow import FullStoryFixture, approve, bound_set, workflow  # noqa: F401
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_workbench import DeferredRunner


def _json_size(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


@pytest.fixture
def dense_repair(tmp_path):
    project = tmp_path / "project"
    source = project / "outputs/input/prompt.md"
    source.parent.mkdir(parents=True)
    lines = [(f"Moment {i:03}: Mara keeps the brass key while Oren stops reaching. "
             + "The apartment entry gives their estrangement a visible consequence. " * 4).strip()
             for i in range(200)]
    source.write_text(publication_header() + "\n" + "\n".join(lines), encoding="utf-8")
    store = CoverStore(project)
    context = CoverSourceContext(store.root, "default:project:book", lambda _: project)
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    fixture = FullStoryFixture()

    def extract(request, result):
        result = fixture._with_events(request, result)
        if request["phase"] == "segment":
            text = request["segment"]["text"]
            result["claims"] = [{"kind": "event", "statement": line,
                "quote": text[text.index(f"Moment {i:03}:"):].split("\n")[0]}
                for i, line in enumerate(lines) if f"Moment {i:03}:" in text]
            if not result["claims"]:
                result.update(classification="technical", reason="Publication metadata only")
        if request["phase"] == "aggregate":
            result["facts"] = [{"kind": "event", "statement": "Mara retains the key; Oren stops reaching.",
                "evidence_refs": list(dict.fromkeys(ref for item in request["inputs"]
                                                    for ref in item["evidence_refs"]))}]
        if request["phase"] == "global":
            # A realistic analysis cites the relevant evidence per fact, rather
            # than repeating all 200 refs on every character and event.
            def relevant(value):
                if isinstance(value, dict):
                    return {key: item[:3] if key == "evidence_refs" else relevant(item)
                            for key, item in value.items()}
                return [relevant(item) for item in value] if isinstance(value, list) else value
            result = relevant(result)
        return result

    fixture.extractor.mutate = extract
    analysis = CoverStoryAnalyzer(complete=fixture._complete, model=fixture.model,
        max_segment_bytes=16000, max_repair_attempts=0).analyze(context, snapshot)
    brief, ledger = derive_analysis_context(context, analysis)
    assert len(analysis.evidence) == len(ledger.items) == 200
    assert 65000 <= len(snapshot.primary_bytes) <= 70000

    class InitialDirector:
        def plan(self, brief, *, count, evidence_ledger, story_analysis):
            # Keep every evidence item available; each plan cites its own small
            # selection, like a real approved portfolio.
            small = replace(evidence_ledger, items=evidence_ledger.items[:3])
            payload = fixture.plan(brief, count=count, evidence_ledger=small,
                                   story_analysis=story_analysis).to_dict()
            return CoverArtDirector.from_fixture(payload).plan(brief, count=count,
                evidence_ledger=evidence_ledger, story_analysis=story_analysis)

    flow = CoverWorkflow(context, store)
    direction = approve(flow, flow.plan(analysis.analysis_id, InitialDirector()))
    parent = bound_set(flow, direction)
    quality = {"status": "blocked", "repair_decision": {"kind": "design"},
        "findings": [{"code": "core_conflict_missing", "severity": "blocker",
            "message": "Clarify the supported gesture without changing other candidates."}],
        "review_detail": "Full persisted quality evidence. " * 620}
    parent = store.save(replace(parent, candidates=tuple(
        replace(candidate, status="failed", quality_report=quality)
        for candidate in parent.candidates)), expected_revision=parent.revision)
    return flow, snapshot, analysis, ledger, direction, parent


def _repair_base(direction, parent):
    base = direction.to_dict()
    base["plans"] = [{key: deepcopy(value) for key, value in concept.scene_plan.items()
                      if not key.startswith("_")} for concept in parent.concepts]
    for key in ("source_binding", "compiled_recipes", "direction_id", "direction_sha256",
                "created_at", "status", "novelty_report"):
        base.pop(key, None)
    return base


def _assert_complete(request, snapshot, analysis):
    context = request["frozen_source_context"]
    assert context["documents"][0]["text"].encode() == snapshot.primary_bytes
    assert context["source_content_sha256"] == snapshot.source_content_sha256
    assert decode_evidence_registry(request["cover_evidence_registry"], original=True) == analysis.evidence
    projected = request["story_analysis"]
    for key in ("story", "characters", "relationships", "events", "critical_claims"):
        assert projected[key] == analysis.to_dict()[key]


@pytest.mark.parametrize("repair_round", ["none", "schema", "semantic"])
@pytest.mark.parametrize("refinement", [False, True])
def test_persisted_single_repair_fits_all_requests_without_losing_source_or_siblings(
        dense_repair, repair_round, refinement):
    flow, snapshot, analysis, ledger, direction, parent = dense_repair
    base = _repair_base(direction, parent)
    original = deepcopy(base)
    drafts, reviews, sizes = [], [], []
    target = base["plans"][1]["concept_id"]
    target_index = 1
    refinement_prompt = "保留原有人物和故事，调整选中封面的光线与前景层次。" * 100
    refinement_prompt = refinement_prompt[:2000]
    assert len(refinement_prompt) == 2000
    reference_image = {"sha256": "b" * 64, "relative_path": "artwork.png", "content_type": "image/png",
                       "kind": "artwork", "width": 1024, "height": 1536}
    saved = {path: path.read_bytes() for path in flow.store.root.rglob("*") if path.is_file()}

    def complete(system, user):
        request = json.loads(user)
        size = len((system + "\n" + user).encode())
        sizes.append(size)
        assert size <= 384000
        _assert_complete(request, snapshot, analysis)
        if request["task"] == "review_campaign_semantics":
            reviews.append(request)
            assert len(request["plans"]) == 4
            for i, plan in enumerate(request["plans"]):
                if i != target_index:
                    assert plan == base["plans"][i]
            if repair_round == "semantic" and len(reviews) == 1:
                return json.dumps(supported_review(request, accepted=False, findings=[{
                    "concept_id": target, "message": "Make the existing supported gesture more legible.",
                    "evidence_refs": [ledger.allowed_refs[0]]}]))
            return json.dumps(supported_review(request))
        drafts.append(request)
        current = request.get("repair_base", request["previous_response"])
        assert "evidence_ledger" not in current
        assert request["response_contract"]["base_sha256"] == digest(current)
        assert request["single_candidate_repair"]["concept_id"] == target
        if refinement:
            extra = request["single_candidate_repair"]["user_refinement"]
            assert extra["request"] == refinement_prompt
            assert extra["reference_image"] == reference_image
            assert "NOT supplied" in extra["rule"]
            assert "not authority" in extra["rule"]
        if len(drafts) == 1:
            # The unnormalized persisted ledger is sufficient to reproduce the
            # production failure, even after the existing lossless serializer.
            assert size + _json_size(base["evidence_ledger"]) > 384000
            assert current == {key: value for key, value in base.items() if key != "evidence_ledger"}
            if repair_round == "schema":
                return "not valid JSON"
        return json.dumps({"schema": "cover-direction-patch.v1", "base_sha256": digest(current),
            "plan_updates": [{"concept_id": target, "changes": {
                "frozen_action": base["plans"][target_index]["frozen_action"] + f" Revised visible pause {len(drafts)}."}}],
            "shared_updates": {}})

    if refinement:
        quality = {**deepcopy(parent.candidates[target_index].quality_report),
                   "user_refinement_request": refinement_prompt}
        repair = {"base": base, "concept_id": target, "quality_report": quality,
            "cover_set_id": parent.cover_set_id, "candidate_id": parent.candidates[target_index].candidate_id,
            "parent_sha256": digest(parent.to_dict()), "quality_sha256": digest(quality),
            "refinement_prompt": "  " + refinement_prompt + "  ", "reference_image": reference_image}
        repaired = flow.plan(analysis.analysis_id, CoverArtDirector(complete=complete), targeted_repair=repair)
        binding = repaired.source_binding["single_candidate_repair"]
        assert binding["refinement_prompt_sha256"] == digest(refinement_prompt)
        assert binding["reference_image_sha256"] == reference_image["sha256"]
        assert repair["base"] == original and repair["refinement_prompt"].startswith("  ")
    else:
        runner = DeferredRunner()
        started = tasks.start(flow, runner, CoverArtDirector(complete=complete),
            cover_set_id=parent.cover_set_id, candidate_id=parent.candidates[target_index].candidate_id,
            expected_revision=parent.revision, project_id="book")
        job = list(runner.jobs.values())[-1]
        job["fn"]()
        job["status"] = "done"
        task = tasks.get(flow, started["task_id"], runner)
        assert task["status"] == "awaiting_approval", task.get("error")
        repaired = flow.store.load_direction(task["direction_id"])
        assert task["image_calls"] == 1 and not task.get("approved_at")
    assert len(drafts) == (1 if repair_round == "none" else 2)
    assert len(reviews) == (2 if repair_round == "semantic" else 1)
    assert repaired.evidence_ledger.to_dict() == ledger.to_dict()
    assert repaired.source_binding["evidence_sha256"] == digest(ledger.to_dict())
    assert repaired.source_binding["campaign_evidence_review"]
    assert flow.store.load(parent.cover_set_id) == parent
    assert base == original
    assert all(path.read_bytes() == data for path, data in saved.items())


@pytest.mark.parametrize("tamper", ["statement", "bundle", "items", "null", "wrong_type"])
def test_repair_ledger_mismatch_is_not_hidden_by_deduplication(dense_repair, tamper):
    flow, _, analysis, _, direction, parent = dense_repair
    base = _repair_base(direction, parent)
    if tamper == "statement":
        base["evidence_ledger"]["items"][0]["summary"] += " Invented source fact."
    elif tamper == "bundle":
        base["evidence_ledger"]["source_bundle_sha256"] = "0" * 64
    elif tamper == "items":
        base["evidence_ledger"]["items"].pop()
    else:
        base["evidence_ledger"] = None if tamper == "null" else []
    calls = []
    with pytest.raises(CoverConflict, match="evidence ledger"):
        flow.plan(analysis.analysis_id, CoverArtDirector(complete=lambda *args: calls.append(args)),
                  targeted_repair={"base": base})
    assert calls == []


def test_oversized_repair_still_blocks_before_provider_and_preserves_original(dense_repair):
    flow, _, analysis, ledger, direction, parent = dense_repair
    repair = {"base": _repair_base(direction, parent), "concept_id": parent.concepts[0].concept_id,
              "quality_report": {"complete_review": "不可截断的审核证据" * 20000}}
    before = deepcopy(repair)
    normalized = _normalize_targeted_repair(repair, ledger)
    assert _normalize_targeted_repair(normalized, ledger) == normalized
    calls = []
    with pytest.raises(CoverDirectionError, match="exceeds its byte budget.*no story facts or evidence were truncated"):
        flow.plan(analysis.analysis_id, CoverArtDirector(complete=lambda *args: calls.append(args)),
                  targeted_repair=repair)
    assert calls == [] and repair == before


def test_patch_with_old_persisted_hash_cannot_authorize_the_normalized_base(dense_repair):
    flow, _, analysis, _, direction, parent = dense_repair
    repair = {"base": _repair_base(direction, parent), "concept_id": parent.concepts[0].concept_id,
              "quality_report": parent.candidates[0].quality_report}
    calls = []

    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        assert request["task"] != "review_campaign_semantics"
        current = request.get("repair_base", request["previous_response"])
        assert request["response_contract"]["base_sha256"] == digest(current)
        assert "evidence_ledger" not in current
        return json.dumps({"schema": "cover-direction-patch.v1", "base_sha256": digest(repair["base"]),
            "plan_updates": [{"concept_id": repair["concept_id"], "changes": {"frozen_action": "Changed"}}],
            "shared_updates": {}})

    with pytest.raises(CoverDirectionError, match="base_sha256"):
        flow.plan(analysis.analysis_id, CoverArtDirector(complete=complete), targeted_repair=repair)
    assert len(calls) == 3


@pytest.mark.parametrize("invalid", ["empty", "too_long", "wrong_type", "missing_image", "bad_hash"])
def test_invalid_refinement_binding_stops_before_model(workflow, invalid):
    flow, analysis, fixture, _ = workflow
    direction = flow.plan(analysis.analysis_id, fixture)
    repair = {"base": direction.to_dict(), "refinement_prompt": "Change the light.",
              "reference_image": {"sha256": "a" * 64}}
    if invalid == "empty":
        repair["refinement_prompt"] = " \n "
    elif invalid == "too_long":
        repair["refinement_prompt"] = "字" * 2001
    elif invalid == "wrong_type":
        repair["refinement_prompt"] = ["Change the light"]
    elif invalid == "missing_image":
        repair.pop("reference_image")
    else:
        repair["reference_image"]["sha256"] = "unbound"
    calls = []
    with pytest.raises(CoverConflict, match="refinement requires"):
        flow.plan(analysis.analysis_id, CoverArtDirector(complete=lambda *args: calls.append(args)),
                  targeted_repair=repair)
    assert calls == []


def test_refinement_must_change_only_the_selected_plan(workflow):
    flow, analysis, fixture, _ = workflow
    direction = flow.plan(analysis.analysis_id, fixture)
    base = direction.to_dict()
    repair = {"base": base, "concept_id": base["plans"][0]["concept_id"], "refinement_prompt": "Adjust light."}
    with pytest.raises(CoverDirectionError, match="must change the selected plan"):
        CoverArtDirector._validate_targeted_repair(repair, deepcopy(base))
    changed = deepcopy(base)
    changed["plans"][0]["frozen_action"] += " Revised light."
    CoverArtDirector._validate_targeted_repair(repair, changed)
    changed["plans"][1]["frozen_action"] += " Unrequested change."
    with pytest.raises(CoverDirectionError, match="unrelated plan"):
        CoverArtDirector._validate_targeted_repair(repair, changed)
