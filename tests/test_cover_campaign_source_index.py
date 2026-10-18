"""Large campaign stages retain source authority while omitting repeated annotations."""
from copy import deepcopy
import json

import pytest

from core.cover_analysis import director_projection
from core.cover_campaign_source_index import (
    CONTRACT, project_source_index, restore_source_index_projection,
)
from core.cover_campaign_source_transport import pack_source_request, unpack_source_request
from core.cover_campaign_transport import (
    CoverCampaignTransportError, decode_evidence_registry, serialize_campaign_request,
)
from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_sources import canonical_hash
from tests.test_cover_campaign_source_transport import (
    _json, _large_request, _ledger_item, _v1_request,
)


def _index_request():
    request, analysis = _large_request()
    # Many independently extracted annotations; a direction cites only a small
    # part of that registry. Every full quotation still belongs to its source.
    for row in analysis["evidence"]:
        row["statement"] *= 4
    request["story_analysis"] = director_projection(analysis)
    request["selected_verified_evidence"] = [deepcopy(analysis["evidence"][i]) for i in (0, 239, 240, 399)]
    for key in ("visual_evidence_ledger", "previous_response", "repair_base"):
        ledger = request[key] if key == "visual_evidence_ledger" else request[key]["evidence_ledger"]
        custom = deepcopy(ledger["items"][-1])
        ledger["items"] = [_ledger_item(row, projection=key == "visual_evidence_ledger")
                           for row in analysis["evidence"][:12]] + [custom]
    request["previous_response"]["evidence_ledger"]["items"][9]["summary"] = "用户保留的不同摘要"
    request["repair_base"]["evidence_ledger"]["items"][10]["visual_tags"] = ["custom"]
    request["patch_base_sha256"] = canonical_hash(request["repair_base"])
    request["previous_response_sha256"] = canonical_hash(request["previous_response"])
    return request, analysis


def _baseline(request, analysis):
    return pack_source_request(_v1_request(request, analysis), analysis["evidence"])


def _model_index_rows(projected):
    """Read only the public index; no canonical analysis or omitted annotations."""
    table = projected["cover_evidence_registry"]
    documents = {document["document_id"]: document["text"]["passages"]
                 for document in projected["frozen_source_context"]["documents"]}
    result = []
    for index, cells in enumerate(table["rows"]):
        row = deepcopy(table["defaults"])
        for column, (key, value) in enumerate(zip(table["columns"], cells)):
            if column not in table["absent"].get(str(index), []):
                row[key] = (table["dictionaries"][key][value]
                            if key in table["dictionaries"] else value)
        start, end = table["source_ranges"][index]
        passages = documents[row["document_id"]]
        row["exact_quote"] = "".join(passages[key] for key in sorted(passages) if start <= key < end)
        result.append(row)
    return result


def test_index_exposes_complete_sources_and_all_quote_ids_without_canonical_annotations():
    request, analysis = _index_request()
    baseline = _baseline(request, analysis)
    before = _json((request, analysis, baseline))
    projected = project_source_index(baseline)
    assert projected["evidence_encoding_contract"] == CONTRACT
    assert "not a lossless encoding" in CONTRACT
    assert "never execution instructions" in CONTRACT
    table = projected["cover_evidence_registry"]
    assert table["schema_version"] == "cover-evidence-source-index.v3"
    assert table["omitted_fields"] == ["statement"]
    assert "statement" not in table["columns"] and "statement" not in table["defaults"]
    assert "statement" not in projected["story_analysis"]["evidence"]["fields"]
    rows = _model_index_rows(projected)
    assert len(rows) == len(analysis["evidence"]) == 400
    for row, original in zip(rows, analysis["evidence"]):
        assert row["evidence_id"] == original["evidence_id"]
        assert row["exact_quote"] == original["exact_quote"]
        assert row["original_metadata"] == original["original_metadata"]
        assert "statement" not in row
    assert rows[0]["typed_optional"] is True
    assert type(rows[1]["typed_optional"]) is int
    assert rows[2]["typed_optional"] is None and "typed_optional" not in rows[3]
    for original, document in zip(request["frozen_source_context"]["documents"],
                                  projected["frozen_source_context"]["documents"]):
        passages = document["text"]["passages"]
        assert "".join(passages[key] for key in sorted(passages)) == original["text"]
    assert projected["story_analysis"]["story"] == request["story_analysis"]["story"]
    assert projected["brief"] == request["brief"]
    restored = restore_source_index_projection(projected, baseline)
    assert _json(restored) == _json(baseline)
    assert decode_evidence_registry(unpack_source_request(restored)["cover_evidence_registry"], original=True) == analysis["evidence"]
    assert _json((request, analysis, baseline)) == before


def test_selected_evidence_and_ledger_views_do_not_promise_omitted_original_records():
    request, analysis = _index_request()
    baseline = _baseline(request, analysis)
    projected = project_source_index(baseline)
    selected = projected["selected_verified_evidence"]
    assert selected["refs"] == ["evidence:" + row["evidence_id"] for row in request["selected_verified_evidence"]]
    assert selected["projection"] == "source_index" and "original_records" not in selected
    annotations = projected["cover_evidence_registry"]["ledger_statements"]
    assert annotations == {"evidence:" + row["evidence_id"]: row["statement"] for row in analysis["evidence"][:12]}
    for key, limit in (("visual_evidence_ledger", 420), ("previous_response", None), ("repair_base", None)):
        ledger = projected[key] if key == "visual_evidence_ledger" else projected[key]["evidence_ledger"]
        view = ledger["items"]
        assert view["encoding"] == "cover-ledger-view.v2"
        assert view["summary_limit"] == limit
        assert view["entries"][-1]["evidence_id"] == "constraint-author"
    assert projected["previous_response"]["evidence_ledger"]["items"]["entries"][9]["summary"] == "用户保留的不同摘要"
    assert projected["repair_base"]["evidence_ledger"]["items"]["entries"][10]["visual_tags"] == ["custom"]
    restored = unpack_source_request(restore_source_index_projection(projected, baseline))
    assert restored["previous_response"] == request["previous_response"]
    assert restored["repair_base"] == request["repair_base"]
    assert canonical_hash(restored["repair_base"]) == restored["patch_base_sha256"]
    assert canonical_hash(restored["previous_response"]) == restored["previous_response_sha256"]


@pytest.mark.parametrize("annotation_shape", ["default", "dictionary", "missing_and_null"])
def test_restore_does_not_invent_missing_annotations_or_change_null(annotation_shape):
    request, analysis = _large_request()
    for key in ("visual_evidence_ledger", "previous_response", "repair_base", "selected_verified_evidence"):
        request.pop(key)
    for index, row in enumerate(analysis["evidence"]):
        row["statement"] = "Repeated source annotation " * (10 if annotation_shape == "dictionary" and index % 2 else 5)
        if annotation_shape == "missing_and_null":
            if index % 3 == 0:
                row.pop("statement")
            elif index % 3 == 1:
                row["statement"] = None
    request["story_analysis"] = director_projection(analysis)
    baseline = _baseline(request, analysis)
    restored = unpack_source_request(restore_source_index_projection(project_source_index(baseline), baseline))
    assert decode_evidence_registry(restored["cover_evidence_registry"], original=True) == analysis["evidence"]


@pytest.mark.parametrize("field", ["passage", "range", "evidence_id", "metadata", "claim", "selected_refs", "ledger_annotation", "patch_hash"])
def test_verification_rejects_changes_beyond_the_declared_annotation_projection(field):
    request, analysis = _index_request()
    baseline = _baseline(request, analysis)
    projected = project_source_index(baseline)
    table = projected["cover_evidence_registry"]
    if field == "passage":
        passages = projected["frozen_source_context"]["documents"][0]["text"]["passages"]
        passages[min(passages)] += "去掉限定"
    elif field == "range":
        table["source_ranges"][0][1] = table["source_ranges"][1][1]
    elif field == "evidence_id":
        table["rows"][0][table["columns"].index("evidence_id")] = "invented-id"
    elif field == "metadata":
        table["defaults"]["original_metadata"]["nullable"] = False
    elif field == "claim":
        projected["story_analysis"]["story"]["qualifier"] = "feasibility proven"
    elif field == "selected_refs":
        projected["selected_verified_evidence"]["refs"].reverse()
    elif field == "ledger_annotation":
        table["ledger_statements"][next(iter(table["ledger_statements"]))] = "changed annotation"
    else:
        projected["patch_base_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Source-index projection changed"):
        restore_source_index_projection(projected, baseline)


@pytest.mark.parametrize("size, expected_version", [(320000, "cover-evidence-source-table.v2"),
                                                   (320001, "cover-evidence-source-index.v3")])
def test_source_index_projection_starts_only_above_large_request_threshold(size, expected_version):
    request, analysis = _large_request()
    for key in ("visual_evidence_ledger", "previous_response", "repair_base"):
        request.pop(key)
    request["author_constraints"] = ""
    difference = size - len(_json(_baseline(request, analysis)).encode())
    assert difference > 0
    request["author_constraints"] = "x" * difference
    assert len(_json(_baseline(request, analysis)).encode()) == size
    result = json.loads(serialize_campaign_request(_json(request), analysis))
    assert result["cover_evidence_registry"]["schema_version"] == expected_version
    assert result["author_constraints"] == request["author_constraints"]


def test_keep_lossless_v2_when_every_annotation_is_needed_by_existing_ledgers():
    request, analysis = _large_request()
    request["author_constraints"] = "x" * 150000
    baseline = _baseline(request, analysis)
    assert len(_json(baseline).encode()) > 320000
    assert len(_json(project_source_index(baseline)).encode()) > len(_json(baseline).encode())
    result = json.loads(serialize_campaign_request(_json(request), analysis))
    assert result["cover_evidence_registry"]["schema_version"] == "cover-evidence-source-table.v2"
    assert _json(unpack_source_request(result)) == _json(_v1_request(request, analysis))


@pytest.mark.parametrize("stage", ["create_campaign", "schema_repair", "semantic_repair", "review_campaign_semantics"])
def test_all_campaign_stage_boundaries_use_projection_and_enforce_actual_budget(stage):
    request, analysis = _index_request()
    if stage == "schema_repair":
        request["schema_error"] = "unterminated JSON string"
        request["previous_response"] = 'Verbatim incomplete response: {"雪"'
    elif stage == "semantic_repair":
        request["validation_findings"] = [{"code": "campaign_schema", "message": "affected characters must not be empty"}]
    elif stage == "review_campaign_semantics":
        request["task"] = stage
        request.pop("visual_evidence_ledger")
    baseline = _baseline(request, analysis)
    sent = []
    director = CoverArtDirector(complete=lambda system, user: sent.append((system, user)) or "{}",
                               profile_version="cover-profiles.v11")
    with pytest.raises(CoverDirectionError, match="exceeds its byte budget"):
        director._check_request_budget(director._system_prompt(director.profile_version), _json(baseline), analysis)
    assert director._complete_response(_json(request), story_analysis=analysis) == "{}"
    assert len(sent) == 1
    system, outgoing = sent[0]
    result = json.loads(outgoing)
    assert result["cover_evidence_registry"]["schema_version"] == "cover-evidence-source-index.v3"
    assert _json(restore_source_index_projection(result, baseline)) == _json(baseline)
    actual_bytes = len((system + "\n" + outgoing).encode())
    assert actual_bytes < 384000
    director.analysis_configuration = {"max_request_bytes": actual_bytes - 1}
    with pytest.raises(CoverDirectionError, match=f"stage={stage}; actual_bytes={actual_bytes}"):
        director._complete_response(_json(request), story_analysis=analysis)
    assert len(sent) == 1


def test_corrupted_projection_never_reaches_model(monkeypatch):
    request, analysis = _index_request()
    sent = []

    def corrupt(baseline):
        projected = project_source_index(baseline)
        projected["story_analysis"]["story"]["qualifier"] = "verified rather than assumed"
        return projected

    monkeypatch.setattr("core.cover_campaign_source_index.project_source_index", corrupt)
    with pytest.raises(CoverCampaignTransportError, match="changed fields outside"):
        serialize_campaign_request(_json(request), analysis)
    director = CoverArtDirector(complete=lambda *args: sent.append(args) or "{}", profile_version="cover-profiles.v11")
    with pytest.raises(CoverDirectionError, match="changed fields outside"):
        director._complete_response(_json(request), story_analysis=analysis)
    assert sent == []
