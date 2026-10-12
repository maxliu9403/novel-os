from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import replace

import pytest

from core.cover_analysis import (
    CoverAnalysisError, CoverStoryAnalyzer, derive_analysis_context, load_analysis,
    reserve_analysis, segment_snapshot, director_projection,
)
from core.cover_sources import CoverSourceContext, CoverSourceSelector, capture_source
from tests.test_cover_models_v2 import two_character_fixture


def publication_header():
    brief = two_character_fixture()
    labels = {'title': 'Title', 'author': 'Author', 'language': 'Language', 'genre': 'Genre',
              'target_audience': 'Audience', 'market_scope': 'Market'}
    return ''.join(f'{label}: {brief[field]}\n' for field, label in labels.items())


@pytest.fixture
def source(tmp_path):
    project = tmp_path / "project"
    (project / "outputs/input").mkdir(parents=True)
    prompt = ("# The Door Is Mine\r\n\r\n"
              "## 人物和冲突\r\nMara removes Oren's key. 她关上了门。\r\n\r\n"
              "## Frozen approved design\r\n```json\r\n"
              + json.dumps(two_character_fixture(), ensure_ascii=False, indent=2)
              + "\r\n```\r\n\r\n## Ending\r\nThe final reconciliation remains uncertain.\r\n")
    (project / "outputs/input/prompt.md").write_bytes(prompt.encode("utf-8"))
    context = CoverSourceContext(tmp_path / "covers", "default:project:book", lambda _: project)
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    return context, snapshot, project


class ModelFixture:
    def __init__(self):
        self.calls = []
        self.mutate = None

    def __call__(self, system, user):
        request = json.loads(user)
        self.calls.append(request)
        phase = request["phase"]
        if phase == "segment":
            result = {"classification": "narrative", "reason": "source story section",
                      "claims": [{"kind": "event", "statement": "Frozen source narrative", "quote": request["segment"]["text"]}],
                      "unresolved": []}
        elif phase == "aggregate":
            facts = []
            for item in request["inputs"]:
                if not any(x["statement"] == item["statement"] for x in facts):
                    facts.append({"statement": item["statement"], "kind": item["kind"],
                                  "evidence_refs": item["evidence_refs"][:1]})
            result = {"covered_input_ids": [x["input_id"] for x in request["inputs"]], "facts": facts}
        elif phase == "global":
            refs = [x["evidence_ref"] for x in request["evidence"]]
            brief = two_character_fixture()
            result = {
                "story": {"core_conflict": brief["core_conflict"], "protagonist_goal": brief["core_task"],
                          "stakes": "the family home", "reader_promise": brief["emotional_promise"], "evidence_refs": refs},
                "characters": [{"character_id": x["character_id"], "name": x["name"],
                                "narrative_role": x["narrative_role"], "evidence_refs": refs} for x in brief["principal_characters"]],
                "relationships": [{"from_character_id": "char_mara", "to_character_id": "char_oren", "epoch": "opening",
                                  "relationship": "estranged spouses", "evidence_refs": refs}],
                "events": [{"event_id": "door_choice", "participants": ["char_mara", "char_oren"], "epoch": "opening",
                            "location": "apartment", "action": "Mara removes the key", "reaction": "Oren stops reaching",
                            "consequence": "access is refused", "evidence_refs": refs}],
                "suspense": [{"question": "Will she close the door?", "withheld_answer": "her final decision", "evidence_refs": refs}],
                "visual_assets": [{"description": "brass key", "evidence_refs": refs}],
                "critical_claims": [{"claim_id": "conflict", "statement": brief["core_conflict"], "evidence_refs": refs}],
                "unresolved": [], "brief": brief,
            }
        else:
            assert phase == "verify"
            checks = []
            for check in request["required_checks"]:
                claim_id = check["claim_id"]
                refs = [item["evidence_ref"] for item in request["evidence"]
                        if not claim_id.startswith("segment:")
                        or item["segment_id"] == claim_id.removeprefix("segment:")]
                checks.append({"claim_id": claim_id, "supported": True, "evidence_refs": refs})
            result = {"status": "pass", "checks": checks, "issues": []}
        if self.mutate:
            result = self.mutate(request, result)
        return json.dumps(result, ensure_ascii=False)


def analyzer(model, **kwargs):
    return CoverStoryAnalyzer(complete=model, model="fixture-analyzer", max_repair_attempts=0, **kwargs)


def test_segmentation_covers_every_raw_byte_without_splitting_utf8(source):
    _, snapshot, _ = source
    segments = segment_snapshot(snapshot, max_segment_bytes=90)
    assert b"".join(x.text.encode() for x in segments) == snapshot.primary_bytes
    assert all(len(x.text.encode()) <= 90 for x in segments)
    assert segments[0].byte_start == 0 and segments[-1].byte_end == len(snapshot.primary_bytes)
    assert all(a.byte_end == b.byte_start for a, b in zip(segments, segments[1:]))
    assert any(x.continuation for x in segments)
    large = segment_snapshot(snapshot, max_segment_bytes=100000)
    assert sum("```json" in x.text for x in large) == 1
    fenced = next(x for x in large if "```json" in x.text)
    assert fenced.text.count("```") == 2


def test_complete_analysis_verifies_spans_and_projects_only_frozen_source(source):
    context, snapshot, project = source
    model = ModelFixture()
    result = analyzer(model).analyze(context, snapshot)
    assert result.coverage_status == "complete"
    assert {x["phase"] for x in model.calls} == {"segment", "global", "verify"}
    assert result.coverage["expected_segment_ids"] == result.coverage["processed_segment_ids"]
    assert result.coverage["failed_segment_ids"] == []
    assert result.source_content_sha256 == hashlib.sha256(snapshot.primary_bytes).hexdigest()
    assert result.analysis_sha256 != result.source_content_sha256
    for evidence in result.evidence:
        assert snapshot.primary_bytes[evidence["byte_start"]:evidence["byte_end"]] == evidence["exact_quote"].encode()
        assert evidence["snapshot_id"] == snapshot.snapshot_id
    (project / "outputs/input/prompt.md").write_text("Completely different live story")
    brief, ledger = derive_analysis_context(context, result)
    assert brief.title == "The Door Is Mine" and brief.source_prompt_sha256 == snapshot.source_content_sha256
    assert len(ledger.items) == len(result.evidence)
    assert all(item.source_ref.startswith(f"snapshot:{snapshot.snapshot_id}") for item in ledger.items)
    assert load_analysis(context, result.analysis_id).analysis_sha256 == result.analysis_sha256


def test_reserved_progress_cache_reuse_and_same_content_new_provenance(source):
    context, snapshot, _ = source
    reserved = reserve_analysis(context, snapshot)
    assert load_analysis(context, reserved).coverage_status == "pending"
    with pytest.raises(CoverAnalysisError) as error:
        derive_analysis_context(context, reserved)
    assert error.value.code == "analysis_incomplete"
    model = ModelFixture()
    engine = analyzer(model)
    result = engine.analyze(context, snapshot, analysis_id=reserved)
    count = len(model.calls)
    assert engine.analyze(context, snapshot).analysis_id == result.analysis_id
    assert len(model.calls) == count
    new_snapshot = capture_source(context, snapshot.selector)
    reused = engine.analyze(context, new_snapshot)
    assert reused.analysis_id != result.analysis_id
    assert reused.snapshot_id == new_snapshot.snapshot_id
    assert reused.reused_from_analysis_id == result.analysis_id
    assert all(x["snapshot_id"] == new_snapshot.snapshot_id for x in reused.evidence)
    assert len(model.calls) == count
    forced = engine.analyze(context, snapshot, force=True)
    assert forced.analysis_id != result.analysis_id and len(model.calls) > count


def test_failed_extraction_is_durable_and_resume_skips_finished_segments(source):
    context, snapshot, _ = source
    model = ModelFixture()
    count = 0
    def fail_second(request, result):
        nonlocal count
        if request["phase"] == "segment":
            count += 1
            if count == 2:
                raise RuntimeError("fixture provider interrupted")
        return result
    model.mutate = fail_second
    with pytest.raises(CoverAnalysisError) as error:
        analyzer(model, max_segment_bytes=512).analyze(context, snapshot)
    analysis_id = error.value.analysis_id
    partial = load_analysis(context, analysis_id)
    assert partial.coverage_status == "partial"
    assert len(partial.coverage["processed_segment_ids"]) == 1
    assert partial.coverage["failed_segment_ids"]
    first_id = partial.coverage["processed_segment_ids"][0]
    assert partial.attempts[-1]["status"] == "failed"
    model.mutate = None
    complete = analyzer(model, max_segment_bytes=512).analyze(context, snapshot, analysis_id=analysis_id)
    assert complete.coverage_status == "complete"
    assert sum(x["phase"] == "segment" and x["segment"]["segment_id"] == first_id for x in model.calls) == 1


@pytest.mark.parametrize("failure", ["fake_quote", "unknown_evidence", "missing_verification", "unresolved_attachment"])
def test_incomplete_or_unsupported_analysis_never_cached_as_complete(source, failure):
    context, snapshot, _ = source
    model = ModelFixture()
    def mutate(request, result):
        if failure == "fake_quote" and request["phase"] == "segment":
            result["claims"][0]["quote"] = "This was not in the frozen source"
        if failure == "unknown_evidence" and request["phase"] == "global":
            result["events"][0]["evidence_refs"] = ["evidence:invented"]
        if failure == "missing_verification" and request["phase"] == "verify":
            result["checks"] = result["checks"][:1]
        if failure == "unresolved_attachment" and request["phase"] == "global":
            result["unresolved"] = [{"kind": "required_dependency", "path": "missing-characters.md"}]
        return result
    model.mutate = mutate
    with pytest.raises(CoverAnalysisError) as error:
        analyzer(model).analyze(context, snapshot)
    result = load_analysis(context, error.value.analysis_id)
    assert result.coverage_status != "complete"
    assert not list((context.cover_root / "analysis-cache").glob("*.json"))
    with pytest.raises(CoverAnalysisError):
        derive_analysis_context(context, result)


def test_repeated_quotes_require_exact_position_and_unicode_uses_byte_spans(source):
    context, _, project = source
    prefix = publication_header()
    raw = prefix + "她说不。她说不。\n"
    (project / "outputs/input/prompt.md").write_text(raw)
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    model = ModelFixture()
    def mutate(request, result):
        if request["phase"] == "segment":
            result["claims"][0]["quote"] = "她说不。"
        return result
    model.mutate = mutate
    with pytest.raises(CoverAnalysisError):
        analyzer(model).analyze(context, snapshot)
    def located(request, result):
        if request["phase"] == "segment":
            result["claims"][0].update(quote="她说不。", quote_start=len(prefix) + 4)
        return result
    model.mutate = located
    result = analyzer(model).analyze(context, snapshot)
    assert result.evidence[0]["byte_start"] == len((prefix + "她说不。").encode())


def test_context_budget_and_cancel_do_not_claim_success(source):
    context, snapshot, _ = source
    model = ModelFixture()
    with pytest.raises(CoverAnalysisError) as error:
        analyzer(model, token_counter=lambda text: len(text), max_input_tokens=10).analyze(context, snapshot)
    assert error.value.code == "analysis_context_limit" and model.calls == []
    event = threading.Event()
    event.set()
    with pytest.raises(CoverAnalysisError) as error:
        analyzer(model).analyze(context, snapshot, cancel_event=event)
    assert error.value.code == "analysis_cancelled"
    assert load_analysis(context, error.value.analysis_id).phase == "cancelled"


def test_request_byte_bound_never_sends_oversized_context(source):
    context, snapshot, _ = source
    model = ModelFixture()
    with pytest.raises(CoverAnalysisError) as error:
        analyzer(model, max_request_bytes=4096).analyze(context, snapshot)
    assert error.value.code == "analysis_context_limit"
    failed = load_analysis(context, error.value.analysis_id)
    assert failed.coverage_status != "complete"
    assert "No source was truncated" in failed.last_error["message"]
    assert all(len(json.dumps(request, ensure_ascii=False).encode()) < 4096 for request in model.calls)


def test_250kb_story_uses_bounded_hierarchy_and_verifies_the_last_reversal(source):
    context, _, project = source
    beginning = "Mara thinks Oren controls the home."
    ending = "FINAL_REVERSAL: Mara owns the house; Oren is guarding her forged deed."
    paragraph = "Mara watches Oren at their apartment threshold, considering who controls the shared key. "
    raw = ("# The Door Is Mine\n" + publication_header() + beginning + "\n" +
           "\n".join(f"## Chapter {index}\n" + paragraph * 47 for index in range(64)) +
           "\n## Final revelation\n" + ending + "\n")
    assert len(raw.encode()) > 250000
    (project / "outputs/input/prompt.md").write_text(raw)
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    model = ModelFixture()
    def long_story(request, result):
        if request["phase"] == "segment":
            text = request["segment"]["text"]
            result["claims"][0]["statement"] = ending if "FINAL_REVERSAL" in text else beginning
        if request["phase"] == "global":
            assert ending in [item["statement"] for item in request["evidence"]]
            result["story"]["final_reversal"] = ending
        return result
    model.mutate = long_story
    byte_sizes = []
    def complete(system, user):
        byte_sizes.append(len((system + "\n" + user).encode()))
        return model(system, user)
    result = CoverStoryAnalyzer(complete=complete, model="long-fixture", max_request_bytes=49152,
                               max_repair_attempts=0).analyze(context, snapshot)
    assert result.coverage_status == "complete"
    assert result.story["final_reversal"] == ending
    assert any(item["phase"] == "aggregate" for item in model.calls)
    checked = [item for item in model.calls if item.get("mode") == "source_segment"]
    assert len(checked) == len(result.coverage["expected_segment_ids"])
    assert "FINAL_REVERSAL" in checked[-1]["documents"][0]["text"]
    assert b"".join(item["documents"][0]["text"].encode() for item in checked) == snapshot.primary_bytes
    assert result.verification["verified_segment_ids"] == result.coverage["expected_segment_ids"]
    assert max(byte_sizes) <= 49152
    assert load_analysis(context, result.analysis_id).coverage_status == "complete"
    projected = director_projection(result.to_dict())
    assert projected["story"]["final_reversal"] == ending
    assert [x["evidence_id"] for x in projected["evidence"]] == [x["evidence_id"] for x in result.evidence]
    assert len(json.dumps(projected, ensure_ascii=False).encode()) < 60000
    serialized = json.dumps(projected, ensure_ascii=False)
    assert "exact_quote" not in serialized and "segment_results" not in serialized and "attempts" not in serialized
    assert paragraph not in serialized
    projected["story"]["final_reversal"] = "changed detached copy"
    assert result.story["final_reversal"] == ending


def test_complete_record_tampering_and_cross_scope_reads_rejected(source):
    context, snapshot, _ = source
    result = analyzer(ModelFixture()).analyze(context, snapshot)
    with pytest.raises(CoverAnalysisError):
        load_analysis(replace(context, owner_scope="another:owner"), result.analysis_id)
    path = context.cover_root / "analyses" / result.analysis_id / "analysis.json"
    raw = json.loads(path.read_text())
    raw["events"][0]["participants"] = ["invented"]
    path.write_text(json.dumps(raw))
    with pytest.raises(CoverAnalysisError) as error:
        load_analysis(context, result.analysis_id)
    assert error.value.code == "analysis_invalid"


def test_same_reserved_id_cannot_change_model_or_snapshot(source):
    context, snapshot, _ = source
    model = ModelFixture()
    event = threading.Event()
    event.set()
    with pytest.raises(CoverAnalysisError) as error:
        analyzer(model).analyze(context, snapshot, cancel_event=event)
    reserved = error.value.analysis_id
    with pytest.raises(CoverAnalysisError) as error:
        CoverStoryAnalyzer(complete=model, model="another-model").analyze(context, snapshot, analysis_id=reserved)
    assert error.value.code == "analysis_conflict"
    other = capture_source(context, snapshot.selector)
    with pytest.raises(CoverAnalysisError):
        analyzer(model).analyze(context, other, analysis_id=reserved)
