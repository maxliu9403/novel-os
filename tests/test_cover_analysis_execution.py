"""Concurrency preserves full source requests and durable ordered checkpoints."""
from __future__ import annotations

import json
import threading
import time
from functools import partial

import pytest

from core.cover_analysis import (
    LEGACY_SEGMENTER_VERSION, CoverAnalysisError, CoverStoryAnalyzer, load_analysis,
    segment_snapshot as _segment_snapshot,
)
from core.cover_sources import CoverSourceContext, CoverSourceSelector, capture_source
from tests.test_cover_analysis_snapshot import ModelFixture
from tests.test_cover_models_v2 import two_character_fixture

# These regressions deliberately preserve old per-paragraph checkpoints and
# structural review units. The packed v2 workflow has its own end-to-end suite.
segment_snapshot = partial(_segment_snapshot, segmenter_version=LEGACY_SEGMENTER_VERSION)


@pytest.fixture
def source(tmp_path):
    project = tmp_path / "project"
    inputs = project / "outputs/input"
    inputs.mkdir(parents=True)
    brief = two_character_fixture()
    metadata = "\n".join(f"{key}: {brief[key]}" for key in
                         ("title", "author", "language", "genre", "target_audience", "market_scope"))
    inputs.joinpath("prompt.md").write_text("\n\n".join(
        (f"# Chapter 0\n{metadata}\n" if i == 0 else f"## Chapter {i}\n")
        + f"Mara removes Oren's key at doorway {i}." for i in range(6)))
    context = CoverSourceContext(tmp_path / "covers", "default:project:book", lambda _: project)
    return context, capture_source(context, CoverSourceSelector("project_input", project_id="book"))


def engine(model, **kwargs):
    kwargs.setdefault("segmenter_version", LEGACY_SEGMENTER_VERSION)
    return CoverStoryAnalyzer(complete=model, model="concurrent-fixture", max_repair_attempts=0, **kwargs)


def _structural_review_case(source, *, wrong_heading=False, hidden_body=False):
    context, snapshot = source
    project = context.project_resolver("book")
    original = snapshot.primary_bytes.decode("utf-8")
    name = "Oren" if wrong_heading else "Mara"
    heading = f"### char_mara · {name}"
    body = "Mara removes Oren's key and denies him entry."
    project.joinpath("outputs/input/prompt.md").write_text(
        original + f"\n\n## Character profiles\n\n{heading}\n\n{body}\n")
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    model = ModelFixture()
    def mutate(request, response):
        if request["phase"] == "segment":
            text = request["segment"]["text"].strip()
            if text in {"## Character profiles", heading} or (hidden_body and text == body):
                return {"classification": "technical", "reason": "Only structural text was extracted",
                        "claims": [], "unresolved": []}
        if request.get("mode") == "source_segment":
            documents = request["documents"]
            target = next((item for item in documents if item["text"].strip() == heading), None)
            if target:
                has_body = any(item["text"].strip() == body for item in documents)
                if not has_body or wrong_heading or hidden_body:
                    failed = next((item for item in documents if hidden_body and item["text"].strip() == body), target)
                    for check in response["checks"]:
                        if check["claim_id"] == "segment:" + failed["segment_id"]:
                            check["supported"] = False
                    return {**response, "status": "fail", "issues": [{"segment_id": failed["segment_id"],
                        "problem": "Body story was misclassified" if hidden_body else "Character heading identity is unsupported",
                        "exact_source_text": failed["text"].strip(), "evidence_refs": [], "character_id": "char_mara"}]}
        return response
    model.mutate = mutate
    return context, snapshot, model, heading, body


def test_structural_headings_and_body_receive_one_review_with_independent_checks(source):
    context, snapshot, model, heading, body = _structural_review_case(source)
    result = engine(model).analyze(context, snapshot)
    review = next(item for item in model.calls if item.get("mode") == "source_segment"
                  and any(doc["text"].strip() == heading for doc in item["documents"]))
    assert [doc["text"].strip() for doc in review["documents"]] == ["## Character profiles", heading, body]
    assert [doc["structural_role"] for doc in review["documents"]] == ["heading", "heading", "content"]
    assert [doc["extraction_classification"] for doc in review["documents"]] == ["technical", "technical", "narrative"]
    assert len(review["required_checks"]) == 3
    assert result.verification["verified_segment_ids"] == [segment.segment_id for segment in segment_snapshot(snapshot)]
    assert len(result.verification_progress["units"]) < len(result.verification_progress["segments"])
    assert result.global_checkpoint["synthesis_sha256"]


@pytest.mark.parametrize("defect", ["wrong_heading", "hidden_body"])
def test_structural_unit_still_rejects_identity_mismatch_and_misclassified_story(source, defect):
    context, snapshot, model, _, _ = _structural_review_case(source, **{defect: True})
    with pytest.raises(CoverAnalysisError) as error:
        engine(model).analyze(context, snapshot)
    assert error.value.code == "analysis_semantic_repair_exhausted"
    saved = load_analysis(context, error.value.analysis_id)
    assert saved.semantic_revision["round"] == 1
    assert saved.semantic_revision["status"] == "exhausted"


def test_parallel_requests_keep_source_order_and_phase_barriers(source, monkeypatch):
    context, snapshot = source
    segments = segment_snapshot(snapshot)
    indices = {s.segment_id: i for i, s in enumerate(segments)}
    model = ModelFixture()
    lock = threading.Lock()
    active = {"segment": 0, "verify": 0}
    peaks = dict(active)
    completed = {"segment": [], "verify": []}
    owner = threading.get_ident()
    from core import cover_analysis as module
    original_save = module._save
    def save(*args):
        assert threading.get_ident() == owner, "workers must never mutate or persist shared analysis"
        original_save(*args)
    monkeypatch.setattr(module, "_save", save)
    def complete(system, user):
        request = json.loads(user)
        phase = request["phase"]
        local = phase == "segment" or request.get("mode") == "source_segment"
        if phase == "global":
            assert len(completed["segment"]) == len(segments)
            assert [x["segment_id"] for x in request["evidence"]] == [s.segment_id for s in segments]
        if request.get("mode") == "global_cross_segment":
            assert len(completed["verify"]) == len(segments)
        if local:
            sid = request.get("segment", request.get("documents", [{}])[0])["segment_id"]
            with lock:
                active[phase] += 1
                peaks[phase] = max(peaks[phase], active[phase])
            time.sleep(.04 if indices[sid] % 3 == 0 else .004)
            value = model(system, user)
            with lock:
                active[phase] -= 1
                completed[phase].append(sid)
            return value
        return model(system, user)
    result = engine(complete, max_workers=3).analyze(context, snapshot)
    assert peaks == {"segment": 3, "verify": 3}
    expected = [s.segment_id for s in segments]
    assert completed["segment"] != expected
    assert result.coverage["processed_segment_ids"] == expected
    assert [e["segment_id"] for e in result.evidence] == expected
    assert result.verification["verified_segment_ids"] == expected
    assert all(a["segment_id"] for a in result.attempts if a.get("mode") == "source_segment")
    assert len([r for r in model.calls if r.get("mode") == "source_segment"]) == len(segments)
    assert load_analysis(context, result.analysis_id).coverage_status == "complete"


def test_failed_batch_drains_completed_work_and_resume_reuses_global_checkpoint(source):
    context, snapshot = source
    segments = segment_snapshot(snapshot)
    failed_id = segments[1].segment_id
    model = ModelFixture()
    failed = False
    def complete(system, user):
        nonlocal failed
        request = json.loads(user)
        if request.get("mode") == "source_segment":
            sid = request["documents"][0]["segment_id"]
            if sid == failed_id and not failed:
                failed = True
                raise RuntimeError("temporary verifier unavailable")
            time.sleep(.03)
        return model(system, user)
    with pytest.raises(CoverAnalysisError) as error:
        engine(complete, max_workers=3).analyze(context, snapshot)
    analysis_id = error.value.analysis_id
    partial = load_analysis(context, analysis_id)
    assert partial.phase == "failed"
    assert partial.global_checkpoint["synthesis_sha256"]
    saved = set(partial.verification_progress["segments"])
    assert saved and failed_id not in saved
    assert len(saved) < len(segments)
    first_count = len(model.calls)
    # Execution concurrency is deliberately excluded from semantic cache identity.
    result = engine(complete, max_workers=2).analyze(context, snapshot, analysis_id=analysis_id)
    new_calls = model.calls[first_count:]
    assert result.coverage_status == "complete"
    assert not any(r["phase"] in {"segment", "global"} for r in new_calls)
    assert {r["documents"][0]["segment_id"] for r in new_calls if r.get("mode") == "source_segment"} == set(s.segment_id for s in segments) - saved


def test_cancel_stops_dispatch_and_waits_for_inflight_results(source):
    context, snapshot = source
    cancelled = threading.Event()
    barrier = threading.Barrier(3)
    model = ModelFixture()
    lock = threading.Lock()
    calls = []
    active = 0
    def complete(system, user):
        nonlocal active
        request = json.loads(user)
        if request["phase"] == "segment":
            with lock:
                calls.append(request["segment"]["segment_id"])
                active += 1
            barrier.wait(timeout=2)
            cancelled.set()
            time.sleep(.02)
            value = model(system, user)
            with lock:
                active -= 1
            return value
        return model(system, user)
    with pytest.raises(CoverAnalysisError) as error:
        engine(complete, max_workers=3).analyze(context, snapshot, cancel_event=cancelled)
    assert error.value.code == "analysis_cancelled"
    assert active == 0 and len(calls) == 3
    partial = load_analysis(context, error.value.analysis_id)
    assert set(partial.coverage["processed_segment_ids"]) == set(calls)
    assert all(a["status"] == "complete" for a in partial.attempts)
    resumed = engine(model, max_workers=2).analyze(context, snapshot, analysis_id=partial.analysis_id)
    assert resumed.coverage_status == "complete"


@pytest.mark.parametrize("tamper", ["result", "request"])
def test_invalid_global_checkpoint_recomputes_instead_of_reusing(source, tamper):
    context, snapshot = source
    model = ModelFixture()
    def fail_verify(request, result):
        if request.get("mode") == "source_segment":
            raise RuntimeError("pause after global")
        return result
    model.mutate = fail_verify
    with pytest.raises(CoverAnalysisError) as error:
        engine(model).analyze(context, snapshot)
    analysis_id = error.value.analysis_id
    path = context.cover_root / "analyses" / analysis_id / "analysis.json"
    payload = json.loads(path.read_text())
    if tamper == "result":
        payload["global_checkpoint"]["synthesis"]["story"]["stakes"] = "corrupt checkpoint"
    else:
        payload["global_checkpoint"]["input_sha256"] = "wrong request"
    path.write_text(json.dumps(payload))
    count = len(model.calls)
    model.mutate = None
    result = engine(model).analyze(context, snapshot, analysis_id=analysis_id)
    assert result.coverage_status == "complete"
    assert len([r for r in model.calls[count:] if r["phase"] == "global"]) == 1
    assert not any(r["phase"] == "segment" for r in model.calls[count:])


def test_parallel_complete_factory_is_thread_local(source):
    context, snapshot = source
    created = []
    model = ModelFixture()
    def factory():
        owner = threading.get_ident()
        created.append(owner)
        def complete(system, user):
            assert threading.get_ident() == owner
            time.sleep(.002)
            return model(system, user)
        return complete
    result = engine(model, max_workers=3, complete_factory=factory).analyze(context, snapshot)
    assert result.coverage_status == "complete"
    assert len(created) >= 3


def _make_legacy_partial(context, snapshot):
    from core.cover_sources import canonical_hash
    model = ModelFixture()
    def fail_global(request, result):
        if request["phase"] == "global":
            raise RuntimeError("legacy global failure")
        return result
    model.mutate = fail_global
    with pytest.raises(CoverAnalysisError) as error:
        engine(model).analyze(context, snapshot)
    path = context.cover_root / "analyses" / error.value.analysis_id / "analysis.json"
    payload = json.loads(path.read_text())
    payload["configuration"].pop("publication_metadata_version", None)
    payload["cache_key"] = canonical_hash({"scope": context.owner_scope, "bundle": snapshot.source_bundle_sha256,
                                           "configuration": payload["configuration"]})
    payload["global_checkpoint"] = {"stale": True}
    payload["verification_progress"] = {"synthesis_sha256": "old", "segments": {"stale": True}}
    path.write_text(json.dumps(payload))
    return path, payload


def test_legacy_partial_upgrade_reuses_all_verified_extraction(source):
    context, snapshot = source
    _, before = _make_legacy_partial(context, snapshot)
    model = ModelFixture()
    result = engine(model, max_workers=3).analyze(context, snapshot, analysis_id=before["analysis_id"])
    assert result.coverage_status == "complete"
    assert result.configuration["publication_metadata_version"]
    assert result.configuration_upgrades[-1]["previous_cache_key"] == before["cache_key"]
    assert result.evidence == before["evidence"]
    assert not any(r["phase"] == "segment" for r in model.calls)
    assert len([r for r in model.calls if r.get("mode") == "source_segment"]) == len(segment_snapshot(snapshot))


@pytest.mark.parametrize("tamper", ["quote", "span", "registry", "coverage", "config"])
def test_legacy_partial_upgrade_rejects_tampered_extraction(source, tamper):
    context, snapshot = source
    path, payload = _make_legacy_partial(context, snapshot)
    sid = payload["coverage"]["processed_segment_ids"][0]
    if tamper == "quote":
        payload["segment_results"][sid]["evidence"][0]["exact_quote"] = "not source text"
    elif tamper == "span":
        payload["segment_results"][sid]["evidence"][0]["byte_start"] += 1
    elif tamper == "registry":
        payload["evidence"] = []
    elif tamper == "coverage":
        payload["coverage"]["expected_segment_ids"] = []
    else:
        payload["configuration"]["model"] = "another-model"
    path.write_text(json.dumps(payload))
    model = ModelFixture()
    with pytest.raises(CoverAnalysisError):
        engine(model).analyze(context, snapshot, analysis_id=payload["analysis_id"])
    assert model.calls == []


def test_cancellation_during_global_preserves_validated_checkpoint(source):
    context, snapshot = source
    cancelled = threading.Event()
    model = ModelFixture()
    def complete(system, user):
        result = model(system, user)
        if json.loads(user)["phase"] == "global":
            cancelled.set()
        return result
    with pytest.raises(CoverAnalysisError) as error:
        engine(complete).analyze(context, snapshot, cancel_event=cancelled)
    assert error.value.code == "analysis_cancelled"
    partial = load_analysis(context, error.value.analysis_id)
    assert partial.global_checkpoint["synthesis_sha256"]
    count = len(model.calls)
    result = engine(model).analyze(context, snapshot, analysis_id=partial.analysis_id)
    assert result.coverage_status == "complete"
    assert not any(r["phase"] in {"segment", "global"} for r in model.calls[count:])


def test_concurrency_changes_neither_model_requests_nor_semantic_output(source):
    from core.cover_sources import canonical_hash
    context, snapshot = source
    serial_model, parallel_model = ModelFixture(), ModelFixture()
    serial = engine(serial_model, max_workers=1).analyze(context, snapshot)
    parallel = engine(parallel_model, max_workers=3).analyze(context, snapshot, force=True)
    assert sorted(canonical_hash(request) for request in serial_model.calls) == sorted(
        canonical_hash(request) for request in parallel_model.calls)
    for key in ("evidence", "story", "characters", "relationships", "events", "suspense", "visual_assets",
                "critical_claims", "brief_payload", "verification", "configuration", "cache_key"):
        assert getattr(serial, key) == getattr(parallel, key)
    calls_before = len(parallel_model.calls)
    assert engine(parallel_model, max_workers=2).analyze(context, snapshot).analysis_id == parallel.analysis_id
    assert len(parallel_model.calls) == calls_before


def test_failure_finishing_during_result_persistence_blocks_new_dispatch(monkeypatch):
    from core import cover_analysis_execution as execution
    first_batch = threading.Barrier(2)
    release_failure = threading.Event()
    failure_finished = threading.Event()
    started = []
    original_pool = execution.ThreadPoolExecutor
    class ObservedPool(original_pool):
        def submit(self, fn, call, *args):
            future = super().submit(fn, call, *args)
            if call.key == "1":
                future.add_done_callback(lambda _: failure_finished.set())
            return future
    monkeypatch.setattr(execution, "ThreadPoolExecutor", ObservedPool)
    def complete(_system, user):
        started.append(user)
        if user in {"0", "1"}:
            first_batch.wait(timeout=2)
        if user == "1":
            assert release_failure.wait(timeout=2)
            raise RuntimeError("terminal failure while successful result is being persisted")
        return {}
    calls = [execution.AnalysisCall(str(i), "segment", {}, lambda raw: raw) for i in range(3)]
    iterator = execution.execute_calls(calls, max_workers=2, max_repairs=0, complete=complete,
        complete_factory=None, begin=lambda call, request: ({}, "", call.key),
        finish=lambda *args: None, cancel_event=None, cancellation_error=lambda: RuntimeError("cancelled"),
        is_terminal_error=lambda error: False)
    assert next(iterator) == ("0", {})
    release_failure.set()
    assert failure_finished.wait(timeout=2)
    with pytest.raises(RuntimeError, match="terminal failure"):
        list(iterator)
    assert set(started) == {"0", "1"}


def test_final_verification_completed_during_cancel_is_reused(source):
    context, snapshot = source
    cancelled = threading.Event()
    model = ModelFixture()
    def complete(system, user):
        result = model(system, user)
        if json.loads(user).get("mode") == "global_cross_segment":
            cancelled.set()
        return result
    with pytest.raises(CoverAnalysisError) as error:
        engine(complete).analyze(context, snapshot, cancel_event=cancelled)
    assert error.value.code == "analysis_cancelled"
    partial = load_analysis(context, error.value.analysis_id)
    assert partial.verification_checkpoint["result_sha256"]
    assert partial.verification["verified_segment_ids"] == partial.coverage["expected_segment_ids"]
    count = len(model.calls)
    result = engine(model).analyze(context, snapshot, analysis_id=partial.analysis_id)
    assert result.coverage_status == "complete"
    assert len(model.calls) == count


@pytest.mark.parametrize("tamper", [None, "input", "result"])
def test_cancelled_aggregation_retains_and_validates_completed_group_checkpoint(source, tamper):
    from core.cover_analysis import reserve_analysis
    context, snapshot = source
    reserved = reserve_analysis(context, snapshot)
    payload = load_analysis(context, reserved).to_dict()
    payload["evidence"] = [{"evidence_id": "known"}]
    facts = [{"evidence_ref": "evidence:known", "evidence_refs": ["evidence:known"],
              "statement": "A long redundant statement. " * 8, "kind": "event"} for _ in range(14)]
    cancelled = threading.Event()
    model = ModelFixture()
    def complete(system, user):
        result = model(system, user)
        cancelled.set()
        return result
    analyzer = engine(complete, max_request_bytes=10000)
    with pytest.raises(CoverAnalysisError) as error:
        analyzer._bounded_facts(context, payload, facts, cancelled)
    assert error.value.code == "analysis_cancelled"
    assert len(payload["aggregation_progress"]) == 1
    saved = load_analysis(context, reserved)
    assert saved.aggregation_progress == payload["aggregation_progress"]
    count = len(model.calls)
    key = next(iter(payload["aggregation_progress"]))
    if tamper == "input":
        payload["aggregation_checkpoints"][key]["input_sha256"] = "wrong-input"
    elif tamper == "result":
        payload["aggregation_progress"][key]["facts"][0]["statement"] = "corrupted checkpoint"
    merged = engine(model, max_request_bytes=10000)._bounded_facts(context, payload, facts, None)
    assert merged
    # A valid checkpoint avoids repeating group one; invalid input/output
    # digests force that group to be checked again alongside unfinished work.
    assert len(model.calls) == count + (2 if tamper else 1)


@pytest.mark.parametrize("tamper", ["input", "result"])
def test_final_verification_checkpoint_pollution_is_not_reused(source, tamper):
    context, snapshot = source
    cancelled = threading.Event()
    model = ModelFixture()
    def complete(system, user):
        result = model(system, user)
        if json.loads(user).get("mode") == "global_cross_segment":
            cancelled.set()
        return result
    with pytest.raises(CoverAnalysisError) as error:
        engine(complete).analyze(context, snapshot, cancel_event=cancelled)
    path = context.cover_root / "analyses" / error.value.analysis_id / "analysis.json"
    payload = json.loads(path.read_text())
    if tamper == "input":
        payload["verification_checkpoint"]["input_sha256"] = "wrong-input"
    else:
        payload["verification_checkpoint"]["result"]["checks"][0]["supported"] = False
    path.write_text(json.dumps(payload))
    count = len(model.calls)
    result = engine(model).analyze(context, snapshot, analysis_id=payload["analysis_id"])
    assert result.coverage_status == "complete"
    assert [request.get("mode") for request in model.calls[count:]] == ["global_cross_segment"]


def test_optional_unknowns_and_pending_visual_identity_do_not_block_story_analysis(source):
    context, snapshot = source
    model = ModelFixture()
    def missing_visual_details(request, response):
        if request["phase"] == "global":
            character = response["brief"]["principal_characters"][0]
            character.update(age=None, age_band="", occupation_and_status="", daily_wardrobe="",
                             lived_environment="", physical_identity="", gender_presentation="")
        return response
    model.mutate = missing_visual_details
    def complete(system, user):
        request = json.loads(user)
        assert "Only blocking" in system
        assert "pending_confirmation" in system
        if request["phase"] == "global":
            assert "Deliberately unspecified optional details" in request["rule"]
            assert "not blockers" in request["rule"]
        return model(system, user)
    result = engine(complete).analyze(context, snapshot)
    assert result.coverage_status == "complete"
    assert result.unresolved == []
    character = result.brief_payload["principal_characters"][0]
    assert character["age"] is None and character["age_band"] == ""
    assert character["physical_identity"] == "" and character["occupation_and_status"] == ""
    pending = result.brief_payload["visual_assumptions"]
    assert any(item["field"] == "principal_characters[0].age" and item["critical"] is True
               and item["status"] == "pending_confirmation" for item in pending)
    assert len([request for request in model.calls if request.get("mode") == "source_segment"]) == len(segment_snapshot(snapshot))


def test_real_core_story_contradiction_still_blocks_and_never_enters_cache(source):
    context, snapshot = source
    model = ModelFixture()
    def contradiction(request, response):
        if request["phase"] == "global":
            response["unresolved"] = [{"kind": "core_source_contradiction",
                "description": "The frozen source identifies two incompatible perpetrators for the central incident."}]
        return response
    model.mutate = contradiction
    with pytest.raises(CoverAnalysisError) as error:
        engine(model).analyze(context, snapshot)
    assert error.value.code == "analysis_unresolved"
    assert load_analysis(context, error.value.analysis_id).unresolved
    assert not any(request["phase"] == "verify" for request in model.calls)
    assert not list((context.cover_root / "analysis-cache").glob("*.json"))


def _age_feedback_source(source):
    context, _, = source
    project = context.project_resolver("book")
    path = project / "outputs/input/prompt.md"
    text = path.read_text().replace("Mara removes Oren's key at doorway 0.",
        "Mara is an eight-year-old girl. Her age is a provisional design value pending confirmation.")
    path.write_text(text)
    return context, capture_source(context, CoverSourceSelector("project_input", project_id="book"))


def _cited_age_failure(request):
    evidence = request["evidence"][0]["evidence_ref"]
    return {"status": "fail", "checks": [{"claim_id": request["required_checks"][0]["claim_id"],
        "supported": False, "evidence_refs": [evidence]}], "issues": [{"type": "omitted_age",
        "character_id": "char_mara", "exact_source_text": "Mara is an eight-year-old girl.",
        "problem": "The projection omits the source-provided provisional age and calls it unknown.",
        "evidence_refs": [evidence]}]}


def _age_model(*, never_fixed=False, cancel_after_revision=False):
    model = ModelFixture()
    cancelled = threading.Event()
    def respond(request, response):
        if request["phase"] == "global":
            character = response["brief"]["principal_characters"][0]
            character.update(age=None, age_band="", physical_identity="Exact age unknown")
            if request.get("semantic_revision"):
                character.update(age=8, physical_identity="eight-year-old girl; provisional source design")
                response["brief"]["visual_assumptions"].append({"field": "principal_characters[0].age",
                    "proposed_value": "8", "reason": "Source labels this age provisional, pending confirmation",
                    "status": "pending_confirmation", "critical": True})
                if cancel_after_revision:
                    cancelled.set()
        if request.get("mode") == "source_segment" and "eight-year-old" in request["documents"][0]["text"]:
            if never_fixed or request["analysis"]["brief_payload"]["principal_characters"][0]["age"] is None:
                return _cited_age_failure(request)
        return response
    model.mutate = respond
    return model, cancelled


def test_cited_semantic_failure_repairs_global_once_and_rechecks_all_source(source):
    context, snapshot = _age_feedback_source(source)
    model, _ = _age_model()
    result = engine(model, max_workers=3).analyze(context, snapshot)
    globals_ = [request for request in model.calls if request["phase"] == "global"]
    assert len(globals_) == 2
    correction = globals_[1]["semantic_revision"]
    assert correction["verification_feedback"][0]["report"]["status"] == "fail"
    assert b"".join(document["text"].encode() for document in correction["source_documents"]) == snapshot.primary_bytes
    assert "provisional design" in str(correction)
    assert result.brief_payload["principal_characters"][0]["age"] == 8
    assert any(item["field"] == "principal_characters[0].age" and item["status"] == "pending_confirmation"
               for item in result.brief_payload["visual_assumptions"])
    corrected_reviews = [request for request in model.calls if request.get("mode") == "source_segment"
                         and request["analysis"]["brief_payload"]["principal_characters"][0]["age"] == 8]
    assert len(corrected_reviews) == len(segment_snapshot(snapshot))
    assert all("repair" not in request for request in model.calls if request["phase"] == "verify")
    assert result.coverage_status == "complete"


def test_semantic_repair_budget_survives_resume_without_repeating_failed_calls(source):
    context, snapshot = _age_feedback_source(source)
    model, _ = _age_model(never_fixed=True)
    with pytest.raises(CoverAnalysisError) as error:
        engine(model).analyze(context, snapshot)
    assert error.value.code == "analysis_semantic_repair_exhausted"
    analysis_id = error.value.analysis_id
    partial = load_analysis(context, analysis_id)
    assert partial.semantic_revision["round"] == 1
    assert partial.semantic_revision["status"] == "exhausted"
    assert "source-provided provisional age" in partial.last_error["message"]
    assert "segment:segment-" in partial.last_error["message"]
    assert partial.coverage["processed_segment_ids"] == partial.coverage["expected_segment_ids"]
    count = len(model.calls)
    with pytest.raises(CoverAnalysisError) as resumed:
        engine(model).analyze(context, snapshot, analysis_id=analysis_id)
    assert resumed.value.code == "analysis_semantic_repair_exhausted"
    assert "source-provided provisional age" in str(resumed.value)
    assert len(model.calls) == count


def test_semantic_repair_checkpoint_reused_after_cancel_without_regenerating_global(source):
    context, snapshot = _age_feedback_source(source)
    model, cancelled = _age_model(cancel_after_revision=True)
    with pytest.raises(CoverAnalysisError) as error:
        engine(model).analyze(context, snapshot, cancel_event=cancelled)
    assert error.value.code == "analysis_cancelled"
    count = len(model.calls)
    result = engine(model).analyze(context, snapshot, analysis_id=error.value.analysis_id)
    assert result.coverage_status == "complete"
    assert not any(request["phase"] in {"global", "segment", "aggregate"} for request in model.calls[count:])


def test_character_registry_mismatch_names_missing_and_extra_and_explains_cast_selection(source):
    context, snapshot = source
    model = ModelFixture()
    def missing_character(request, response):
        if request["phase"] == "global":
            response["brief"]["principal_characters"] = response["brief"]["principal_characters"][:1]
        return response
    model.mutate = missing_character
    with pytest.raises(CoverAnalysisError) as error:
        engine(model).analyze(context, snapshot)
    assert "missing=['char_oren']" in str(error.value)
    assert "extra=[]" in str(error.value)
    assert "must_appear" in str(error.value)


def test_cancel_before_semantic_dispatch_does_not_consume_correction_budget(source, monkeypatch):
    from core import cover_analysis as module
    context, snapshot = _age_feedback_source(source)
    model, cancelled = _age_model()
    save = module._save
    def cancel_pending(context, payload):
        save(context, payload)
        if payload.get("semantic_revision", {}).get("status") == "pending":
            cancelled.set()
    monkeypatch.setattr(module, "_save", cancel_pending)
    with pytest.raises(CoverAnalysisError) as error:
        engine(model).analyze(context, snapshot, cancel_event=cancelled)
    assert error.value.code == "analysis_cancelled"
    partial = load_analysis(context, error.value.analysis_id)
    assert partial.semantic_revision["status"] == "pending"
    assert "first_attempt_id" not in partial.semantic_revision
    assert len([request for request in model.calls if request["phase"] == "global"]) == 1
    monkeypatch.setattr(module, "_save", save)
    result = engine(model).analyze(context, snapshot, analysis_id=partial.analysis_id)
    assert result.coverage_status == "complete"
    assert len([request for request in model.calls if request["phase"] == "global"]) == 2


@pytest.mark.parametrize("tamper", ["claim", "evidence", "quote", "character"])
def test_malformed_or_unbound_failures_never_trigger_semantic_revision(source, tamper):
    context, snapshot = _age_feedback_source(source)
    model, _ = _age_model()
    mutate = model.mutate
    def invalid_report(request, response):
        result = mutate(request, response)
        if result.get("status") == "fail":
            if tamper == "claim":
                result["checks"][0]["claim_id"] = "segment:other-book"
            elif tamper == "evidence":
                result["issues"][0]["evidence_refs"] = ["evidence:invented"]
            elif tamper == "quote":
                result["issues"][0]["exact_source_text"] = "This quotation is absent from the frozen source."
            else:
                result["issues"][0]["character_id"] = "invented-child"
        return result
    model.mutate = invalid_report
    with pytest.raises(CoverAnalysisError) as error:
        engine(model).analyze(context, snapshot)
    assert error.value.code == "analysis_failed"
    assert not load_analysis(context, error.value.analysis_id).to_dict().get("semantic_revision")
    assert len([request for request in model.calls if request["phase"] == "global"]) == 1


def test_legacy_cited_failure_is_imported_without_repeating_old_verification(source, monkeypatch):
    context, snapshot = _age_feedback_source(source)
    model, _ = _age_model()
    original_schedule = CoverStoryAnalyzer._schedule_semantic_revision
    original_source = CoverStoryAnalyzer._source_verification_call
    def legacy_source(self, *args, **kwargs):
        return original_source(self, *args, **{**kwargs, "legacy": True})
    monkeypatch.setattr(CoverStoryAnalyzer, "_schedule_semantic_revision", lambda *args: False)
    monkeypatch.setattr(CoverStoryAnalyzer, "_source_verification_call", legacy_source)
    with pytest.raises(CoverAnalysisError) as error:
        engine(model).analyze(context, snapshot)
    assert error.value.code == "analysis_verification_failed"
    partial = load_analysis(context, error.value.analysis_id)
    assert partial.verification_progress["input_sha256"] == partial.global_checkpoint["input_sha256"]
    count = len(model.calls)
    monkeypatch.setattr(CoverStoryAnalyzer, "_schedule_semantic_revision", original_schedule)
    monkeypatch.setattr(CoverStoryAnalyzer, "_source_verification_call", original_source)
    result = engine(model).analyze(context, snapshot, analysis_id=partial.analysis_id)
    assert result.coverage_status == "complete"
    assert model.calls[count]["phase"] == "global"
    assert model.calls[count]["semantic_revision"]
    assert not any(request["phase"] == "segment" for request in model.calls[count:])


def test_global_semantic_feedback_resolves_only_existing_exact_source_refs(source):
    context, snapshot = _age_feedback_source(source)
    model, _ = _age_model()
    mutate = model.mutate
    def global_only(request, response):
        if request.get("mode") == "source_segment":
            return response
        if request.get("mode") == "global_cross_segment" and request["analysis"]["brief_payload"]["principal_characters"][0]["age"] is None:
            response["status"] = "fail"
            response["checks"][0]["supported"] = False
            response["issues"] = [{"problem": "The projection omits the source's provisional age value.",
                "character_id": "char_mara", "evidence_refs": [request["evidence"][0]["evidence_ref"]]}]
            return response
        return mutate(request, response)
    model.mutate = global_only
    result = engine(model).analyze(context, snapshot)
    assert result.coverage_status == "complete"
    report = result.semantic_revision["verification_feedback"][0]["report"]
    quoted = report["issues"][0]["source_quotes"]
    assert quoted and "eight-year-old" in quoted[0]["exact_source_text"]
    assert quoted[0]["evidence_ref"] in {"evidence:" + item["evidence_id"] for item in result.evidence}


def test_long_source_semantic_repair_keeps_full_feedback_segments_and_rechecks_every_byte(source):
    context, _ = _age_feedback_source(source)
    path = context.project_resolver("book") / "outputs/input/prompt.md"
    path.write_text(path.read_text() + "\n\n## Extended background\n" + "The hallway remains quiet. " * 2600)
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    assert len(snapshot.primary_bytes) > 49152
    model, _ = _age_model()
    sizes = []
    def complete(system, user):
        sizes.append(len((system + "\n" + user).encode()))
        return model(system, user)
    result = engine(complete, max_workers=3, max_request_bytes=49152).analyze(context, snapshot)
    globals_ = [request for request in model.calls if request["phase"] == "global"]
    assert len(globals_) == 2
    correction = globals_[1]["semantic_revision"]
    assert correction["source_scope"] == "complete_feedback_segments_plus_all_global_facts"
    assert globals_[1]["evidence"] == globals_[0]["evidence"] == result.global_facts
    documents = correction["source_documents"]
    assert documents and len(documents) < len(result.coverage["expected_segment_ids"])
    for document in documents:
        raw = document["text"].encode()
        assert snapshot.document_bytes(document["document_id"])[document["byte_start"]:document["byte_start"] + len(raw)] == raw
    corrected_reviews = [request for request in model.calls if request.get("mode") == "source_segment"
                         and request["analysis"]["brief_payload"]["principal_characters"][0]["age"] == 8]
    segments = segment_snapshot(snapshot, max_segment_bytes=result.configuration["max_segment_bytes"])
    by_id = {request["documents"][0]["segment_id"]: request["documents"][0]["text"].encode() for request in corrected_reviews}
    assert b"".join(by_id[segment.segment_id] for segment in segments) == snapshot.primary_bytes
    assert result.coverage_status == "complete"
    assert max(sizes) <= 49152


def _technical_scope_case(source):
    context, _ = _age_feedback_source(source)
    path = context.project_resolver("book") / "outputs/input/prompt.md"
    path.write_text(path.read_text() + "\n\n## Production settings\nTone: restrained suspense\nPOV: third person\nChapters: 15\nWords: 7500\n")
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    model, _ = _age_model()
    age_mutate = model.mutate
    def respond(request, response):
        if request["phase"] == "segment" and "Chapters: 15" in request["segment"]["text"]:
            return {"classification": "technical", "reason": "Writing workflow parameters, not story events",
                    "claims": [], "unresolved": []}
        if request.get("mode") == "source_segment" and "Chapters: 15" in request["documents"][0]["text"]:
            scope = request.get("verification_scope")
            if scope:
                assert "production parameters" in str(scope)
                assert "misclassified" in str(scope)
                return response
            return {"status": "fail", "checks": [{"claim_id": request["required_checks"][0]["claim_id"],
                "supported": False, "evidence_refs": []}], "issues": [{"problem": "The projection omits Tone, POV, Chapters and Words.",
                    "exact_source_text": "Chapters: 15", "evidence_refs": []}]}
        return age_mutate(request, response)
    model.mutate = respond
    return context, snapshot, model


def test_legacy_exhausted_verifier_policy_upgrades_once_without_new_global_or_semantic_budget(source):
    context, snapshot, model = _technical_scope_case(source)
    legacy = engine(model)
    legacy.configuration.pop("verification_policy_version", None)
    with pytest.raises(CoverAnalysisError) as error:
        legacy.analyze(context, snapshot)
    assert error.value.code == "analysis_semantic_repair_exhausted"
    previous = load_analysis(context, error.value.analysis_id)
    assert previous.semantic_revision["round"] == 1
    assert previous.semantic_revision["status"] == "exhausted"
    count = len(model.calls)
    result = engine(model).analyze(context, snapshot, analysis_id=previous.analysis_id)
    assert result.coverage_status == "complete"
    assert result.global_checkpoint == previous.global_checkpoint
    assert result.segment_results == previous.segment_results
    assert result.semantic_revision["round"] == 1
    assert result.semantic_revision["first_attempt_id"] == previous.semantic_revision["first_attempt_id"]
    assert result.semantic_revision["result_sha256"] == previous.semantic_revision["result_sha256"]
    assert result.verification_policy_upgrade["from_version"] is None
    assert result.verification_progress["policy_version"] == result.configuration["verification_policy_version"]
    assert all(request["phase"] == "verify" for request in model.calls[count:])
    assert len([request for request in model.calls[count:] if request.get("mode") == "source_segment"]) == len(segment_snapshot(snapshot))


def test_current_policy_failure_after_upgrade_does_not_reopen_exhausted_budget(source):
    context, snapshot, model = _technical_scope_case(source)
    legacy = engine(model)
    legacy.configuration.pop("verification_policy_version", None)
    with pytest.raises(CoverAnalysisError) as error:
        legacy.analyze(context, snapshot)
    original_mutate = model.mutate
    def real_contradiction(request, response):
        if request.get("mode") == "source_segment" and "eight-year-old" in request["documents"][0]["text"]:
            return _cited_age_failure(request)
        return original_mutate(request, response)
    model.mutate = real_contradiction
    with pytest.raises(CoverAnalysisError) as upgraded:
        engine(model).analyze(context, snapshot, analysis_id=error.value.analysis_id)
    assert upgraded.value.code == "analysis_semantic_repair_exhausted"
    count = len(model.calls)
    with pytest.raises(CoverAnalysisError) as again:
        engine(model).analyze(context, snapshot, analysis_id=error.value.analysis_id)
    assert again.value.code == "analysis_semantic_repair_exhausted"
    assert len(model.calls) == count


def test_new_verification_scope_still_reviews_technical_segments_instead_of_mechanical_pass(source):
    context, snapshot, model = _technical_scope_case(source)
    result = engine(model).analyze(context, snapshot)
    reviews = [request for request in model.calls if request.get("mode") == "source_segment"
               and "Chapters: 15" in request["documents"][0]["text"]]
    assert reviews and all(request["classification"] == "technical" for request in reviews)
    assert all(request["verification_scope"]["version"] == result.configuration["verification_policy_version"] for request in reviews)
    final = next(request for request in model.calls if request.get("mode") == "global_cross_segment")
    assert final["verification_scope"] == reviews[-1]["verification_scope"]
    assert result.coverage_status == "complete"


@pytest.mark.parametrize("previous_policy", [None, "cover-story-verification.v2"])
def test_verifier_policy_upgrade_preserves_old_cancelled_unsent_semantic_round(source, monkeypatch, previous_policy):
    from core import cover_analysis as module
    context, snapshot = _age_feedback_source(source)
    model, cancelled = _age_model()
    legacy = engine(model)
    if previous_policy is None:
        legacy.configuration.pop("verification_policy_version", None)
    else:
        legacy.configuration["verification_policy_version"] = previous_policy
    original_save = module._save
    def cancel_pending(context, payload):
        original_save(context, payload)
        if payload.get("semantic_revision", {}).get("status") == "pending":
            cancelled.set()
    monkeypatch.setattr(module, "_save", cancel_pending)
    with pytest.raises(CoverAnalysisError) as error:
        legacy.analyze(context, snapshot, cancel_event=cancelled)
    previous = load_analysis(context, error.value.analysis_id)
    assert previous.semantic_revision["status"] == "pending"
    assert "first_attempt_id" not in previous.semantic_revision
    assert len([request for request in model.calls if request["phase"] == "global"]) == 1
    monkeypatch.setattr(module, "_save", original_save)
    result = engine(model).analyze(context, snapshot, analysis_id=previous.analysis_id)
    assert result.coverage_status == "complete"
    assert result.semantic_revision["round"] == 1
    assert result.verification_policy_upgrade["pending_semantic_dispatch"] is True
    assert len([request for request in model.calls if request["phase"] == "global"]) == 2


def test_new_scope_does_not_pass_real_narrative_hidden_in_technical_segment(source):
    context, snapshot, model = _technical_scope_case(source)
    path = context.project_resolver("book") / "outputs/input/prompt.md"
    path.write_text(path.read_text() + "Mara betrays Oren and hides the deed.\n")
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    previous_mutate = model.mutate
    def hidden_story(request, response):
        if request.get("mode") == "source_segment" and "Mara betrays" in request["documents"][0]["text"]:
            assert request["classification"] == "technical"
            assert request["verification_scope"]
            return {"status": "fail", "checks": [{"claim_id": request["required_checks"][0]["claim_id"],
                "supported": False, "evidence_refs": []}], "issues": [{"problem": "A canonical betrayal was misclassified as production metadata.",
                    "character_id": "char_mara", "exact_source_text": "Mara betrays Oren and hides the deed.", "evidence_refs": []}]}
        return previous_mutate(request, response)
    model.mutate = hidden_story
    with pytest.raises(CoverAnalysisError) as error:
        engine(model).analyze(context, snapshot)
    assert error.value.code == "analysis_semantic_repair_exhausted"
    assert "canonical betrayal" in str(error.value)
    assert load_analysis(context, error.value.analysis_id).coverage_status != "complete"


@pytest.mark.parametrize("tamper", ["global_input", "global_result", "revision_result"])
def test_verification_policy_upgrade_refuses_changed_successful_correction_checkpoint(source, tamper):
    context, snapshot, model = _technical_scope_case(source)
    legacy = engine(model)
    legacy.configuration.pop("verification_policy_version", None)
    with pytest.raises(CoverAnalysisError) as error:
        legacy.analyze(context, snapshot)
    path = context.cover_root / "analyses" / error.value.analysis_id / "analysis.json"
    payload = json.loads(path.read_text())
    if tamper == "global_input":
        payload["global_checkpoint"]["input_sha256"] = "unrelated-request"
    elif tamper == "global_result":
        payload["global_checkpoint"]["synthesis"]["story"]["stakes"] = "corrupted-result"
    else:
        payload["semantic_revision"]["result_sha256"] = "unrelated-result"
    path.write_text(json.dumps(payload))
    count = len(model.calls)
    with pytest.raises(CoverAnalysisError) as invalid:
        engine(model).analyze(context, snapshot, analysis_id=payload["analysis_id"])
    assert invalid.value.code == "analysis_invalid"
    assert len(model.calls) == count


def test_cancelled_policy_reverification_resumes_current_progress_without_second_upgrade(source):
    context, snapshot, model = _technical_scope_case(source)
    legacy = engine(model)
    legacy.configuration.pop("verification_policy_version", None)
    with pytest.raises(CoverAnalysisError) as error:
        legacy.analyze(context, snapshot)
    cancelled = threading.Event()
    count = len(model.calls)
    def stop_after_one(system, user):
        result = model(system, user)
        if json.loads(user).get("mode") == "source_segment":
            cancelled.set()
        return result
    with pytest.raises(CoverAnalysisError) as stopped:
        engine(stop_after_one).analyze(context, snapshot, analysis_id=error.value.analysis_id, cancel_event=cancelled)
    assert stopped.value.code == "analysis_cancelled"
    partial = load_analysis(context, stopped.value.analysis_id)
    assert len(partial.verification_progress["segments"]) == 1
    result = engine(model).analyze(context, snapshot, analysis_id=partial.analysis_id)
    assert result.coverage_status == "complete"
    assert result.verification_policy_upgrade == partial.verification_policy_upgrade
    assert len([request for request in model.calls[count:] if request.get("mode") == "source_segment"]) == len(segment_snapshot(snapshot))
    assert all(request["phase"] == "verify" for request in model.calls[count:])


def _approval_scope_case(source, *, explicit_false_approval=False):
    context, snapshot = _age_feedback_source(source)
    path = context.project_resolver("book") / "outputs/input/prompt.md"
    path.write_text(path.read_text() + "\n\n## Workflow review\nThe full skeleton awaits author confirmation; the title remains provisional.\n")
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    model, _ = _age_model()
    original_mutate = model.mutate
    def respond(request, response):
        result = original_mutate(request, response)
        if request["phase"] == "segment" and "awaits author confirmation" in request["segment"]["text"]:
            return {"classification": "technical", "reason": "Approval workflow for the design proposal, not a fictional fact",
                    "claims": [], "unresolved": []}
        if request["phase"] == "global":
            result["brief"]["title_direction"] = {"hierarchy": (
                "The author has formally approved this finalized publication title." if explicit_false_approval else
                "Use the exact English title as the dominant high-contrast text; the author field is empty.")}
        if request.get("mode") == "source_segment" and "awaits author confirmation" in request["documents"][0]["text"]:
            scope = request.get("verification_scope", {})
            if scope.get("approval_and_proposals") and not explicit_false_approval:
                assert "proposed layout" in scope["approval_and_proposals"]
                assert "provisional" in scope["story_qualifications"]
                assert "explicit assertion" in scope["decision_standard"]
                return response
            return {"status": "fail", "checks": [{"claim_id": request["required_checks"][0]["claim_id"],
                "supported": False, "evidence_refs": []}], "issues": [{
                    "problem": "The projection claims final author approval despite the source awaiting confirmation." if explicit_false_approval
                               else "The projection omits that the full skeleton and exact title still await author approval.",
                    "exact_source_text": "The full skeleton awaits author confirmation; the title remains provisional.",
                    "evidence_refs": []}]}
        return result
    model.mutate = respond
    return context, snapshot, model


def test_v2_exhausted_approval_false_positive_upgrades_to_v3_without_changing_global(source):
    context, snapshot, model = _approval_scope_case(source)
    v2 = engine(model)
    v2.configuration["verification_policy_version"] = "cover-story-verification.v2"
    with pytest.raises(CoverAnalysisError) as error:
        v2.analyze(context, snapshot)
    assert error.value.code == "analysis_semantic_repair_exhausted"
    previous = load_analysis(context, error.value.analysis_id)
    count = len(model.calls)
    result = engine(model).analyze(context, snapshot, analysis_id=previous.analysis_id)
    assert result.coverage_status == "complete"
    assert result.configuration["verification_policy_version"] == "cover-story-verification.v3"
    assert result.verification_policy_upgrade["from_version"] == "cover-story-verification.v2"
    assert result.verification_policy_upgrade["to_version"] == "cover-story-verification.v3"
    assert result.global_checkpoint == previous.global_checkpoint
    assert result.semantic_revision["round"] == 1
    assert result.semantic_revision["first_attempt_id"] == previous.semantic_revision["first_attempt_id"]
    assert result.semantic_revision["binding_sha256"] == previous.semantic_revision["binding_sha256"]
    assert all(request["phase"] == "verify" for request in model.calls[count:])
    assert len([request for request in model.calls[count:] if request.get("mode") == "source_segment"]) == len(segment_snapshot(snapshot))


def test_v3_scope_still_rejects_explicit_false_approval_and_preserves_exhaustion(source):
    context, snapshot, model = _approval_scope_case(source, explicit_false_approval=True)
    with pytest.raises(CoverAnalysisError) as error:
        engine(model).analyze(context, snapshot)
    assert error.value.code == "analysis_semantic_repair_exhausted"
    assert "claims final author approval" in str(error.value)
    partial = load_analysis(context, error.value.analysis_id)
    assert partial.semantic_revision["verification_policy_version"] == "cover-story-verification.v3"
    count = len(model.calls)
    with pytest.raises(CoverAnalysisError) as repeated:
        engine(model).analyze(context, snapshot, analysis_id=partial.analysis_id)
    assert repeated.value.code == "analysis_semantic_repair_exhausted"
    assert len(model.calls) == count


@pytest.mark.parametrize("unknown_policy", [None, "cover-story-verification.v1", "cover-story-verification.v999"])
def test_unknown_verifier_policy_cannot_claim_known_policy_upgrade(source, unknown_policy):
    context, snapshot, model = _approval_scope_case(source)
    unknown = engine(model)
    unknown.configuration["verification_policy_version"] = unknown_policy
    with pytest.raises(CoverAnalysisError) as error:
        unknown.analyze(context, snapshot)
    count = len(model.calls)
    with pytest.raises(CoverAnalysisError) as blocked:
        engine(model).analyze(context, snapshot, analysis_id=error.value.analysis_id)
    assert blocked.value.code == "analysis_conflict"
    assert len(model.calls) == count
