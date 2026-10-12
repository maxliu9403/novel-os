"""Packed analysis preserves the full source while avoiding paragraph-per-call costs."""
from __future__ import annotations

import json

import pytest

from core.cover_analysis import (
    LEGACY_SEGMENTER_VERSION, SEGMENTER_VERSION, CoverAnalysisError, CoverStoryAnalyzer,
    derive_analysis_context, load_analysis, segment_snapshot,
)
from core.cover_review_units import build_review_units
from core.cover_sources import CoverSourceContext, CoverSourceSelector, capture_source, canonical_hash
from tests.test_cover_analysis_snapshot import ModelFixture, publication_header


def capture(tmp_path, text, *, dependencies=None):
    project = tmp_path / "project"
    (project / "outputs/input").mkdir(parents=True, exist_ok=True)
    (project / "outputs/input/prompt.md").write_bytes(text.encode("utf-8"))
    for name, body in (dependencies or {}).items():
        (project / f"{name}.md").write_bytes(body.encode("utf-8"))
    context = CoverSourceContext(
        tmp_path / "covers", "default:project:book", lambda _: project,
        dependency_resolver=(lambda *_: {name: f"{name}.md" for name in dependencies})
        if dependencies else None)
    return context, capture_source(context, CoverSourceSelector("project_input", project_id="book"))


def engine(model, **kwargs):
    return CoverStoryAnalyzer(complete=model, model="packing-fixture", max_repair_attempts=0, **kwargs)


def skeleton():
    return ("# The Door Is Mine\n\n" + publication_header() + "\n"
            + "\n\n".join(f"## Chapter {i}\n\n" + f"Mara removes Oren's key at doorway {i}. " * 10
                          for i in range(1, 22))
            + "\n\n## Ending\n\nThe final reconciliation remains uncertain.\n")


def assert_partition(snapshot, segments, budget):
    assert len({item.segment_id for item in segments}) == len(segments)
    for doc in snapshot.documents:
        members = [item for item in segments if item.document_id == doc["document_id"]]
        original = snapshot.document_bytes(doc["document_id"])
        assert b"".join(item.text.encode("utf-8") for item in members) == original
        assert members[0].byte_start == 0 and members[-1].byte_end == len(original)
        assert all(left.byte_end == right.byte_start for left, right in zip(members, members[1:]))
        for item in members:
            assert original[item.byte_start:item.byte_end] == item.text.encode("utf-8")
            assert 0 < item.byte_end - item.byte_start <= budget
            assert item.document_sha256 == doc["sha256"]


def test_small_complete_skeleton_uses_four_requests_and_then_zero_call_cache(tmp_path):
    context, snapshot = capture(tmp_path, skeleton())
    model = ModelFixture()
    legacy = segment_snapshot(snapshot, segmenter_version=LEGACY_SEGMENTER_VERSION)
    assert len(legacy) > 40
    result = engine(model).analyze(context, snapshot)
    assert result.configuration["segmenter_version"] == SEGMENTER_VERSION
    assert [request["phase"] for request in model.calls] == ["segment", "global", "verify", "verify"]
    assert model.calls[0]["segment"]["text"] == snapshot.primary_text
    review = next(request for request in model.calls if request.get("mode") == "source_segment")
    assert "".join(doc["text"] for doc in review["documents"]) == snapshot.primary_text
    assert len(result.coverage["expected_segment_ids"]) == len(result.verification_progress["units"]) == 1
    assert result.coverage_status == "complete"
    assert result.brief_payload["title"] == "The Door Is Mine"
    assert result.brief_payload["language"] == "English"
    assert result.events[0]["participants"] == ["char_mara", "char_oren"]
    assert all(character["evidence_refs"] for character in result.characters)
    assert engine(model).analyze(context, snapshot).analysis_id == result.analysis_id
    assert len(model.calls) == 4
    other_snapshot = capture_source(context, snapshot.selector)
    reused = engine(model).analyze(context, other_snapshot)
    assert reused.reused_from_analysis_id == result.analysis_id
    assert len(model.calls) == 4
    assert load_analysis(context, reused.analysis_id).coverage_status == "complete"


@pytest.mark.parametrize("budget", [32, 33, 63, 64, 127, 24000])
def test_packing_preserves_unicode_crlf_blank_lines_fences_and_dependencies(tmp_path, budget):
    text = ("# 小说\r\n\r\n" + publication_header().replace("\n", "\r\n") + "\r\n\r\n"
            "## Characters\r\n\r\n玛拉🙂关门。\r\n\r\n"
            + '```json\r\n{"fact":"' + "她说不🙂" * 25 + '"}\r\n```\r\n\r\n'
            "## End\r\n\r\nThe choice remains open.\r\n")
    context, snapshot = capture(tmp_path, text, dependencies={"cast": "# 人物\r\n\r\n奥伦停住。\r\n"})
    segments = segment_snapshot(snapshot, max_segment_bytes=budget)
    assert_partition(snapshot, segments, budget)
    documents = [{"document_id": item["document_id"], "sha256": item["sha256"],
                  "text": snapshot.document_text(item["document_id"])} for item in snapshot.documents]
    units = build_review_units(documents, [item.to_dict() for item in segments])
    for doc in documents:
        assert "".join(unit["text"] for unit in units if unit["document_id"] == doc["document_id"]) == doc["text"]
    if budget == 24000:
        assert len(segments) == len(snapshot.documents) == len(units) == 2
        assert next(item for item in segments if item.document_id == "primary").text.count("```") == 2
    else:
        assert any(item.continuation for item in segments)


def test_complete_dependencies_and_exact_quote_offsets_survive_packing(tmp_path):
    text = "# Book\r\n\r\n" + publication_header() + "\r\n## Choices\r\n\r\n她说不。她说不。\r\n"
    context, snapshot = capture(tmp_path, text, dependencies={"cast": "Mara and Oren share the doorway."})
    model = ModelFixture()

    def locate(request, result):
        if request["phase"] == "segment" and request["segment"]["document_id"] == "primary":
            body = request["segment"]["text"]
            result["claims"][0].update(quote="她说不。", quote_start=body.rindex("她说不。"))
        return result

    model.mutate = locate
    result = engine(model).analyze(context, snapshot)
    assert len([request for request in model.calls if request["phase"] == "segment"]) == 2
    assert len([request for request in model.calls if request.get("mode") == "source_segment"]) == 2
    primary = next(item for item in result.evidence if item["document_id"] == "primary")
    assert primary["byte_start"] == len(text[:text.rindex("她说不。")].encode("utf-8"))
    assert primary["heading_path"] == ["Book", "Choices"]
    for item in result.evidence:
        assert snapshot.document_bytes(item["document_id"])[item["byte_start"]:item["byte_end"]] == item["exact_quote"].encode("utf-8")
    assert load_analysis(context, result.analysis_id).coverage_status == "complete"


@pytest.mark.parametrize("budget", [90, 24000])
def test_packed_quotes_retain_actual_sibling_nested_and_fenced_heading_context(tmp_path, budget):
    text = ("# Book\r\n\r\n" + publication_header() + "\r\n"
            "## Opening\r\n\r\nMara leaves.\r\n\r\n"
            "### Doubt\r\n\r\nOren waits.\r\n\r\n"
            "## Ending\r\n\r\n### Return\r\n\r\nOren returns the key.\r\n\r\n"
            "```text\r\n## Not a source chapter\r\n```\r\n\r\n"
            "Mara finally looks up.\r\n\r\n#### Aftermath\r\n\r\n她接受钥匙。\r\n")
    context, snapshot = capture(tmp_path, text)
    expected = {
        "Mara leaves.": ["Book", "Opening"],
        "Oren waits.": ["Book", "Opening", "Doubt"],
        "Oren returns the key.": ["Book", "Ending", "Return"],
        "Mara finally looks up.": ["Book", "Ending", "Return"],
        "她接受钥匙。": ["Book", "Ending", "Return", "Aftermath"],
    }
    model = ModelFixture()

    def quote_local_facts(request, result):
        if request["phase"] == "segment":
            quotes = [quote for quote in expected if quote in request["segment"]["text"]]
            if quotes:
                result["claims"] = [{"kind": "event", "statement": quote, "quote": quote} for quote in quotes]
        return result

    model.mutate = quote_local_facts
    result = engine(model, max_segment_bytes=budget).analyze(context, snapshot)
    found = {item["exact_quote"]: item["heading_path"] for item in result.evidence
             if item["exact_quote"] in expected}
    assert found == expected
    assert load_analysis(context, result.analysis_id).evidence == result.evidence


def test_v1_partition_ids_remain_stable_and_completed_directions_remain_readable(tmp_path):
    text = "# Book\n\n" + publication_header() + "\nMara removes the key.\n\nOren stops.\n"
    context, snapshot = capture(tmp_path, text)
    pieces = ["# Book\n\n", publication_header() + "\n", "Mara removes the key.\n\n", "Oren stops.\n"]
    segments = segment_snapshot(snapshot, segmenter_version=LEGACY_SEGMENTER_VERSION)
    cursor = 0
    for piece, segment in zip(pieces, segments, strict=True):
        end = cursor + len(piece.encode())
        expected = canonical_hash(["primary", snapshot.source_content_sha256, cursor, end, LEGACY_SEGMENTER_VERSION])
        assert segment.segment_id == "segment-" + expected[:24]
        assert segment.text == piece
        cursor = end
    model = ModelFixture()
    old = engine(model, segmenter_version=LEGACY_SEGMENTER_VERSION).analyze(context, snapshot)
    old_payload = old.to_dict()
    old_call_count = len(model.calls)
    assert load_analysis(context, old.analysis_id).to_dict() == old_payload
    assert derive_analysis_context(context, old)[0].title == "The Door Is Mine"
    runner = engine(model)
    assert runner.analyze(context, snapshot, analysis_id=old.analysis_id).analysis_id == old.analysis_id
    assert len(model.calls) == old_call_count
    fresh = runner.analyze(context, snapshot)
    assert fresh.analysis_id != old.analysis_id and fresh.cache_key != old.cache_key
    assert len(model.calls) == old_call_count + 4
    assert load_analysis(context, old.analysis_id).to_dict() == old_payload
    assert runner.configuration["segmenter_version"] == SEGMENTER_VERSION


@pytest.mark.parametrize("version", [LEGACY_SEGMENTER_VERSION, SEGMENTER_VERSION])
def test_interrupted_extraction_preserves_partition_and_only_replays_missing_work(tmp_path, version):
    context, snapshot = capture(tmp_path, skeleton())
    model = ModelFixture()
    segment_calls = 0

    def interrupt(request, result):
        nonlocal segment_calls
        if request["phase"] == "segment":
            segment_calls += 1
            if segment_calls == 3:
                raise RuntimeError("temporary unavailable worker")
        return result

    model.mutate = interrupt
    with pytest.raises(CoverAnalysisError) as failure:
        engine(model, segmenter_version=version, max_segment_bytes=1024).analyze(context, snapshot)
    saved = load_analysis(context, failure.value.analysis_id)
    completed = set(saved.coverage["processed_segment_ids"])
    assert len(completed) == 2
    before = len(model.calls)
    model.mutate = None
    current = engine(model, max_segment_bytes=1024)
    result = current.analyze(context, snapshot, analysis_id=saved.analysis_id)
    assert result.configuration["segmenter_version"] == version
    assert current.configuration["segmenter_version"] == SEGMENTER_VERSION
    replayed = {request["segment"]["segment_id"] for request in model.calls[before:] if request["phase"] == "segment"}
    assert replayed.isdisjoint(completed)
    assert set(result.coverage["expected_segment_ids"]) == completed | replayed
    assert load_analysis(context, result.analysis_id).coverage_status == "complete"
    with pytest.raises(CoverAnalysisError, match="another configuration"):
        CoverStoryAnalyzer(complete=model, model="changed-model", max_segment_bytes=1024).analyze(
            context, snapshot, analysis_id=saved.analysis_id)


def test_unknown_segmenter_and_version_tampering_cannot_reuse_completed_evidence(tmp_path):
    context, snapshot = capture(tmp_path, skeleton())
    with pytest.raises(ValueError, match="Unknown source segmenter"):
        segment_snapshot(snapshot, segmenter_version="cover-segments.future")
    with pytest.raises(ValueError, match="Unknown source segmenter"):
        engine(ModelFixture(), segmenter_version="cover-segments.future")
    result = engine(ModelFixture()).analyze(context, snapshot)
    path = context.cover_root / "analyses" / result.analysis_id / "analysis.json"
    payload = json.loads(path.read_text())
    payload["configuration"]["segmenter_version"] = LEGACY_SEGMENTER_VERSION
    payload.pop("analysis_sha256")
    payload["analysis_sha256"] = canonical_hash(payload)
    path.write_text(json.dumps(payload))
    with pytest.raises(CoverAnalysisError, match="segmentation"):
        load_analysis(context, result.analysis_id)


def test_packed_source_is_independently_checked_for_omitted_final_story_fact(tmp_path):
    context, snapshot = capture(tmp_path, skeleton())
    model = ModelFixture()
    quote = "The final reconciliation remains uncertain."

    def reject_omission(request, result):
        if request.get("mode") == "source_segment":
            doc = next(item for item in request["documents"] if quote in item["text"])
            checks = [{**check, "supported": False} for check in result["checks"]]
            return {**result, "status": "fail", "checks": checks, "issues": [{
                "segment_id": doc["segment_id"], "problem": "The final story fact is omitted.",
                "exact_source_text": quote,
                "evidence_refs": [item["evidence_ref"] for item in request["evidence"]]}]}
        return result

    model.mutate = reject_omission
    with pytest.raises(CoverAnalysisError) as failure:
        engine(model).analyze(context, snapshot)
    assert failure.value.code == "analysis_semantic_repair_exhausted"
    saved = load_analysis(context, failure.value.analysis_id)
    assert saved.coverage_status != "complete"
    assert not list((context.cover_root / "analysis-cache").glob("*.json"))

