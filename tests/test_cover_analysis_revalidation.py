from __future__ import annotations

import copy
import json
import threading
from dataclasses import replace

import pytest

import core.cover_analysis as analysis_module
from core.cover_analysis import CoverAnalysisError, CoverStoryAnalyzer, load_analysis
from core.cover_analysis_execution import AnalysisCall
from core.cover_sources import CoverSourceError, capture_source
from tests.test_cover_analysis_snapshot import ModelFixture, source


def _engine(model, **kwargs):
    return CoverStoryAnalyzer(complete=model, model="fixture-analyzer", max_repair_attempts=1, **kwargs)


def _failed_global(source, monkeypatch, *, mapping_response=False, legacy=False):
    context, snapshot, _ = source
    fixture = ModelFixture()
    global_count = 0

    def response(request, result):
        nonlocal global_count
        if request["phase"] == "global":
            global_count += 1
            recipient = copy.deepcopy(result["brief"]["principal_characters"][1])
            recipient.update(character_id="char_leo", name="Leo", narrative_role="secondary recipient",
                             must_appear=False, agency_signal="", relationships=[])
            result["brief"]["principal_characters"].append(recipient)
            result["characters"].append({"character_id": "char_leo", "name": "Leo",
                "narrative_role": "secondary recipient", "evidence_refs": result["story"]["evidence_refs"]})
            if global_count == 1:
                result["events"][0]["location"] = None
        return result

    fixture.mutate = response

    def model(system, user):
        raw = fixture(system, user)
        return json.loads(raw) if mapping_response else raw

    current_validator = analysis_module._validate_global

    def old_validator(raw, *args, **kwargs):
        result = current_validator(raw, *args, **kwargs)
        if any(not char["must_appear"] and not char["agency_signal"]
               for char in result["brief_payload"]["principal_characters"]):
            raise ValueError("principal_characters[2].agency_signal is required")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(analysis_module, "_validate_global", old_validator)
        with pytest.raises(CoverAnalysisError) as failure:
            _engine(model).analyze(context, snapshot)
    analysis_id = failure.value.analysis_id
    partial = load_analysis(context, analysis_id).to_dict()
    assert partial["coverage_status"] != "complete"
    globals_ = [item for item in partial["attempts"] if item["phase"] == "global"]
    assert len(globals_) == 2 and all(item["status"] == "failed" for item in globals_)
    assert "location" in globals_[0]["error"]
    assert "agency_signal is required" in globals_[1]["error"]
    directory = context.cover_root / "analyses" / analysis_id
    if legacy:
        for record in globals_:
            record.pop("analysis_binding_sha256", None)
            path = directory / "attempts" / f"{record['attempt_id']}.json"
            artifact = json.loads(path.read_text())
            artifact.pop("analysis_binding_sha256", None)
            path.write_text(json.dumps(artifact), encoding="utf-8")
        (directory / "analysis.json").write_text(json.dumps(partial), encoding="utf-8")
    fixture.calls.clear()
    return fixture, model, partial, directory


@pytest.mark.parametrize("mapping_response", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_fixed_parser_revalidates_saved_global_repair_and_still_reviews_source(
    source, monkeypatch, mapping_response, legacy,
):
    context, snapshot, _ = source
    fixture, model, partial, directory = _failed_global(
        source, monkeypatch, mapping_response=mapping_response, legacy=legacy,
    )
    originals = {path.name: path.read_bytes() for path in (directory / "attempts").glob("*.json")}
    complete = _engine(model).analyze(context, snapshot, analysis_id=partial["analysis_id"])

    assert complete.coverage_status == "complete"
    assert fixture.calls and all(item["phase"] == "verify" for item in fixture.calls)
    assert any(item.get("mode") == "source_segment" for item in fixture.calls)
    assert any(item.get("mode") == "global_cross_segment" for item in fixture.calls)
    assert complete.attempts[:len(partial["attempts"])] == partial["attempts"]
    assert all((directory / "attempts" / name).read_bytes() == raw for name, raw in originals.items())
    assert len(complete.response_revalidations) == 1
    provenance = complete.response_revalidations[0]
    assert [item["attempt_id"] for item in provenance["attempt_chain"]] == [
        item["attempt_id"] for item in partial["attempts"] if item["phase"] == "global"]
    assert provenance["result_sha256"] == complete.global_checkpoint["synthesis_sha256"]
    assert load_analysis(context, complete.analysis_id).analysis_sha256 == complete.analysis_sha256


@pytest.mark.parametrize("damage", ["response", "request", "previous_response", "metadata", "missing", "binding", "null_binding"])
def test_unprovable_failed_global_response_falls_back_to_model(source, monkeypatch, damage):
    context, snapshot, _ = source
    fixture, model, partial, directory = _failed_global(source, monkeypatch)
    globals_ = [item for item in partial["attempts"] if item["phase"] == "global"]
    target = globals_[0] if damage == "previous_response" else globals_[-1]
    path = directory / "attempts" / f"{target['attempt_id']}.json"
    artifact = json.loads(path.read_text())
    if damage == "missing":
        path.unlink()
    else:
        if damage in {"response", "previous_response"}:
            artifact["response"] += " "
        elif damage == "request":
            target["request_sha256"] = artifact["request_sha256"] = "0" * 64
        elif damage in {"binding", "null_binding"}:
            target["analysis_binding_sha256"] = artifact["analysis_binding_sha256"] = (
                None if damage == "null_binding" else "0" * 64)
        else:
            artifact["error"] = "different repair error"
        path.write_text(json.dumps(artifact), encoding="utf-8")
    (directory / "analysis.json").write_text(json.dumps(partial), encoding="utf-8")

    complete = _engine(model).analyze(context, snapshot, analysis_id=partial["analysis_id"])
    assert complete.coverage_status == "complete"
    assert any(item["phase"] == "global" for item in fixture.calls)
    assert not complete.to_dict().get("response_revalidations")


def test_changed_global_request_does_not_revalidate_prior_response(source, monkeypatch):
    context, snapshot, _ = source
    fixture, model, partial, _ = _failed_global(source, monkeypatch)
    system = CoverStoryAnalyzer._system
    monkeypatch.setattr(CoverStoryAnalyzer, "_system", staticmethod(
        lambda phase: system(phase) + (" Changed global requirements." if phase == "global" else "")))
    complete = _engine(model).analyze(context, snapshot, analysis_id=partial["analysis_id"])
    assert any(item["phase"] == "global" for item in fixture.calls)
    assert not complete.to_dict().get("response_revalidations")


@pytest.mark.parametrize("changed", ["scope", "snapshot", "configuration"])
def test_failed_response_cannot_cross_analysis_identity(source, monkeypatch, changed):
    context, snapshot, _ = source
    fixture, model, partial, _ = _failed_global(source, monkeypatch)
    engine = _engine(model)
    if changed == "scope":
        context = replace(context, owner_scope="other:project:book")
    elif changed == "snapshot":
        snapshot = capture_source(context, snapshot.selector)
    else:
        engine = _engine(model, inference_parameters={"temperature": 0.5})
    with pytest.raises((CoverAnalysisError, CoverSourceError)):
        engine.analyze(context, snapshot, analysis_id=partial["analysis_id"])
    assert fixture.calls == []


def test_revalidation_cannot_bypass_source_rejection(source, monkeypatch):
    context, snapshot, _ = source
    fixture, model, partial, _ = _failed_global(source, monkeypatch)

    def reject_source(request, result):
        if request["phase"] == "verify":
            raise RuntimeError("source review unavailable")
        return result

    fixture.mutate = reject_source
    with pytest.raises(CoverAnalysisError):
        _engine(model).analyze(context, snapshot, analysis_id=partial["analysis_id"])
    saved = load_analysis(context, partial["analysis_id"])
    assert saved.coverage_status != "complete"
    assert len(saved.response_revalidations) == 1
    assert all(item["phase"] == "verify" for item in fixture.calls)


def test_response_still_rejected_by_current_parser_calls_model_normally(source, monkeypatch):
    context, snapshot, _ = source
    fixture, model, partial, _ = _failed_global(source, monkeypatch)

    def reject(*args, **kwargs):
        raise ValueError("still invalid under current rules")

    monkeypatch.setattr(analysis_module, "_validate_global", reject)
    with pytest.raises(CoverAnalysisError):
        _engine(model).analyze(context, snapshot, analysis_id=partial["analysis_id"])
    assert [item["phase"] for item in fixture.calls] == ["global", "global"]
    assert not load_analysis(context, partial["analysis_id"]).to_dict().get("response_revalidations")


def test_other_phases_never_revalidate_failed_responses(source, monkeypatch):
    context, _, _ = source
    _, _, partial, _ = _failed_global(source, monkeypatch)
    calls = []

    def model(system, user):
        calls.append(user)
        return {"valid": True}

    def obsolete_validator(raw):
        raise ValueError("obsolete parser")

    engine = _engine(model)
    with pytest.raises(ValueError, match="obsolete parser"):
        list(engine._calls(context, partial, [AnalysisCall("verify", "verify", {}, obsolete_validator)]))
    result = list(engine._calls(context, partial, [AnalysisCall("verify", "verify", {}, dict)]))
    assert result == [("verify", {"valid": True})]
    assert len(calls) == 3
    assert not partial.get("response_revalidations")


def test_cancelled_resume_never_revalidates_or_dispatches(source, monkeypatch):
    context, snapshot, _ = source
    fixture, model, partial, _ = _failed_global(source, monkeypatch)
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(CoverAnalysisError, match="cancelled"):
        _engine(model).analyze(context, snapshot, analysis_id=partial["analysis_id"], cancel_event=cancellation)
    assert fixture.calls == []
    assert not load_analysis(context, partial["analysis_id"]).to_dict().get("response_revalidations")
