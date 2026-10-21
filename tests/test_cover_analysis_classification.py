"""Classification failures must be repairable without discarding source facts."""
from __future__ import annotations

import json
import re
from functools import partial

import pytest

from core.cover_analysis import (
    LEGACY_SEGMENTER_VERSION, CoverAnalysisError, CoverStoryAnalyzer, load_analysis,
    segment_snapshot as _segment_snapshot,
)
from core.cover_sources import CoverSourceContext, CoverSourceSelector, capture_source
from tests.test_cover_analysis_snapshot import ModelFixture, publication_header


TECHNICAL_TEXT = "输出格式：Markdown + EPUB；每章3000字；禁止调用外部命令。"
SETTING_QUOTE = "故事地点：虚构的松岚镇河畔公寓。"
SETTING_FACT = "The story takes place in the riverside apartment in fictional Songlan."
TECHNICAL_PSEUDOFACT = "The characters produce an EPUB every time they enter a scene."
segment_snapshot = partial(_segment_snapshot, segmenter_version=LEGACY_SEGMENTER_VERSION)


@pytest.fixture
def classification_source(tmp_path):
    project = tmp_path / "project"
    inputs = project / "outputs/input"
    inputs.mkdir(parents=True)
    text = (
        "# Canonical conflict\n" + publication_header() + "Mara removes Oren's key at the apartment doorway.\n\n"
        f"## Engine settings\n{TECHNICAL_TEXT}\n\n"
        f"## Readership and setting\n目标读者：成年家庭悬疑读者；{SETTING_QUOTE}\n\n"
        "## Ending\nThe final reconciliation remains uncertain.\n"
    )
    (inputs / "prompt.md").write_text(text, encoding="utf-8")
    context = CoverSourceContext(tmp_path / "covers", "default:project:book", lambda _: project)
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    return context, snapshot


def _engine(model):
    # Retain regression coverage for legacy isolated technical paragraphs.
    return CoverStoryAnalyzer(complete=model, model="classification-fixture", max_repair_attempts=1,
                              segmenter_version=LEGACY_SEGMENTER_VERSION)


def _technical_response(*, contradictory=False):
    return {
        "classification": "technical",
        "reason": "Only output format and execution instructions, not canonical story facts.",
        "claims": ([{"kind": "event", "statement": TECHNICAL_PSEUDOFACT, "quote": TECHNICAL_TEXT}]
                   if contradictory else []),
        "unresolved": [],
    }


def _is_technical_request(request):
    return request["phase"] == "segment" and TECHNICAL_TEXT in request["segment"]["text"]


def test_technical_claims_get_actionable_repair_without_polluting_story(classification_source):
    context, snapshot = classification_source
    model = ModelFixture()
    def respond(request, result):
        if not _is_technical_request(request):
            return result
        if "repair" not in request:
            return _technical_response(contradictory=True)
        # The correction must explain both choices, rather than merely repeat
        # the rejection or invite the model to discard a mixed segment's facts.
        error = request["repair"]["error"]
        assert "claims=[]" in re.sub(r"\s+", "", error)
        assert all(term in error.casefold() for term in ("mixed", "canonical", "setting", "narrative"))
        previous = request["repair"]["previous_response"]
        previous = json.loads(previous) if isinstance(previous, str) else previous
        assert previous == _technical_response(contradictory=True)
        return _technical_response()
    model.mutate = respond

    result = _engine(model).analyze(context, snapshot)

    technical_calls = [call for call in model.calls if _is_technical_request(call)]
    assert len(technical_calls) == 2
    segment_id = technical_calls[0]["segment"]["segment_id"]
    assert result.coverage_status == "complete"
    assert result.coverage["processed_segment_ids"] == result.coverage["expected_segment_ids"]
    assert result.segment_results[segment_id]["evidence"] == []
    assert any(item["segment_id"] == segment_id for item in result.coverage["technical_segments"])
    assert not any(item["segment_id"] == segment_id or item["statement"] == TECHNICAL_PSEUDOFACT
                   for item in result.evidence)
    global_call = next(call for call in model.calls if call["phase"] == "global")
    assert TECHNICAL_PSEUDOFACT not in json.dumps(global_call, ensure_ascii=False)
    attempts = [item for item in result.attempts if item["phase"] == "segment" and item["segment_id"] == segment_id]
    assert [item["status"] for item in attempts] == ["failed", "complete"]
    technical_review = next(call for call in model.calls if call.get("mode") == "source_segment"
                            and call["documents"][0]["segment_id"] == segment_id)
    assert technical_review["evidence"] == []


def test_mixed_readership_and_canonical_setting_remains_cited_narrative(classification_source):
    context, snapshot = classification_source
    model = ModelFixture()
    def respond(request, result):
        if _is_technical_request(request):
            return _technical_response()
        if request["phase"] == "segment" and SETTING_QUOTE in request["segment"]["text"]:
            return {"classification": "narrative",
                    "reason": "Mixed readership metadata and a canonical setting; retain the story setting.",
                    "claims": [{"kind": "setting", "statement": SETTING_FACT, "quote": SETTING_QUOTE}],
                    "unresolved": []}
        if request["phase"] == "global":
            setting = next(item for item in request["evidence"] if item["statement"] == SETTING_FACT)
            result["events"][0].update(location="虚构的松岚镇河畔公寓", evidence_refs=[setting["evidence_ref"]])
        return result
    model.mutate = respond

    result = _engine(model).analyze(context, snapshot)

    evidence = next(item for item in result.evidence if item["statement"] == SETTING_FACT)
    assert result.coverage_status == "complete"
    assert result.segment_results[evidence["segment_id"]]["classification"] == "narrative"
    assert not any(item["segment_id"] == evidence["segment_id"] for item in result.coverage["technical_segments"])
    assert evidence["exact_quote"] == SETTING_QUOTE
    assert snapshot.primary_bytes[evidence["byte_start"]:evidence["byte_end"]] == SETTING_QUOTE.encode("utf-8")
    assert result.events[0]["location"] == "虚构的松岚镇河畔公寓"
    assert "evidence:" + evidence["evidence_id"] in result.events[0]["evidence_refs"]


def test_repeated_classification_conflict_stays_partial_and_resumes_same_id(classification_source):
    context, snapshot = classification_source
    model = ModelFixture()
    model.mutate = lambda request, result: _technical_response(contradictory=True) if _is_technical_request(request) else result
    engine = _engine(model)

    with pytest.raises(CoverAnalysisError) as error:
        engine.analyze(context, snapshot)

    analysis_id = error.value.analysis_id
    partial = load_analysis(context, analysis_id)
    technical_id = next(segment.segment_id for segment in segment_snapshot(snapshot) if TECHNICAL_TEXT in segment.text)
    completed = set(partial.coverage["processed_segment_ids"])
    assert partial.coverage_status == "partial" and completed
    assert technical_id not in completed and technical_id not in partial.segment_results
    assert technical_id in partial.coverage["failed_segment_ids"]
    assert not any(item["segment_id"] == technical_id or item["statement"] == TECHNICAL_PSEUDOFACT
                   for item in partial.evidence)
    assert len([call for call in model.calls if _is_technical_request(call)]) == 2
    assert not list((context.cover_root / "analysis-cache").glob("*.json"))
    calls_before_resume = len(model.calls)
    model.mutate = lambda request, result: _technical_response() if _is_technical_request(request) else result

    result = engine.analyze(context, snapshot, analysis_id=analysis_id)

    assert result.analysis_id == analysis_id and result.coverage_status == "complete"
    resumed_segments = [call["segment"]["segment_id"] for call in model.calls[calls_before_resume:]
                        if call["phase"] == "segment"]
    assert technical_id in resumed_segments
    assert completed.isdisjoint(resumed_segments)
    assert result.coverage["failed_segment_ids"] == []
    assert TECHNICAL_PSEUDOFACT not in json.dumps(result.evidence, ensure_ascii=False)
