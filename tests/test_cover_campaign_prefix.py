"""Stable transport prefixes never alter complete source or payload semantics."""
from copy import deepcopy
import json

import pytest

from core.cover_analysis import director_projection
from core.cover_campaign_transport import (
    decode_evidence_registry, encode_evidence_registry, serialize_campaign_request,
)
from tests.test_cover_campaign_transport import _request


STABLE_KEYS = (
    "frozen_source_context", "cover_evidence_registry", "evidence_encoding_contract",
    "story_analysis", "brief", "visual_evidence_ledger",
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def wire(request, analysis):
    return serialize_campaign_request(json.dumps(request, ensure_ascii=False, sort_keys=True), analysis)


def shared_prefix(left, right):
    for index, (a, b) in enumerate(zip(left.encode(), right.encode())):
        if a != b:
            return index
    return min(len(left.encode()), len(right.encode()))


def old_packed_value(request, analysis):
    # This is the pre-prefix codec contract, independent of JSON key order.
    result = deepcopy(request)
    reference = {"registry": "/cover_evidence_registry", "rows": "all"}
    result["story_analysis"]["evidence"] = {**reference, "fields": sorted({
        key for item in request["story_analysis"]["evidence"] for key in item})}
    result["frozen_source_context"]["evidence_spans"] = {**reference, "fields": [
        "evidence_ref", "document_id", "byte_start", "byte_end", "quote_sha256"]}
    if "selected_verified_evidence" in result:
        result["selected_verified_evidence"] = {"registry": "/cover_evidence_registry",
            "refs": ["evidence:" + item["evidence_id"] for item in request["selected_verified_evidence"]],
            "original_records": True}
    result["cover_evidence_registry"] = encode_evidence_registry(analysis["evidence"])
    return result


@pytest.mark.parametrize("phase", ["initial", "repair", "schema", "review"])
def test_bound_phase_keeps_identical_old_json_semantics_with_stable_top_level_fields(phase):
    request, analysis = _request()
    request["visual_evidence_ledger"] = {"z": ["all facts"], "a": {"z": None, "a": "原文"}}
    request["complete_new_extension"] = {"leave": ["every", "field"]}
    request["previous_response"] = {"plans": ["all four full plans"]}
    if phase == "initial":
        request.pop("previous_response")
    elif phase == "repair":
        request["validation_findings"] = [{"code": "fix", "message": "完整反馈"}]
    elif phase == "schema":
        request["schema_error"] = "invalid shape"
        request["previous_response"] = "not JSON: 原始回复保留"
    else:
        request["task"] = "review_campaign_semantics"
        request["selected_verified_evidence"] = [deepcopy(analysis["evidence"][i]) for i in (199, 0, 199)]
    before = deepcopy((request, analysis))
    result = wire(request, analysis)
    packed = json.loads(result)
    assert list(packed)[:6] == list(STABLE_KEYS)
    assert list(packed)[6:] == sorted(set(packed) - set(STABLE_KEYS))
    expected = old_packed_value(request, analysis)
    expected["evidence_encoding_contract"] = packed["evidence_encoding_contract"]
    assert canonical(packed) == canonical(expected)
    assert len(result.encode()) == len(canonical(expected).encode())
    assert decode_evidence_registry(packed["cover_evidence_registry"], original=True) == analysis["evidence"]
    assert (request, analysis) == before


def test_initial_schema_repair_and_review_share_all_equal_stable_fields_before_variation():
    initial, analysis = _request()
    initial.pop("previous_response")
    initial["visual_evidence_ledger"] = {"source_files": {"primary": "identity"}, "facts": ["all facts"]}
    repair = {**deepcopy(initial), "task": "Repair all identified errors",
              "previous_response": {"plans": ["complete prior response"]},
              "validation_findings": [{"code": "repair"}]}
    schema = {**deepcopy(initial), "task": "Repair schema", "schema_error": "wrong field",
              "previous_response": "bad JSON"}
    review = {**deepcopy(initial), "task": "review_campaign_semantics",
              "plans": ["whole portfolio"], "selected_verified_evidence": deepcopy(analysis["evidence"])}
    baseline = wire(initial, analysis)
    payload = json.loads(baseline)
    stable = "{" + ",".join(canonical(key) + ":" + canonical(payload[key]) for key in STABLE_KEYS) + ","
    assert baseline.startswith(stable)
    for request in (repair, schema, review):
        result = wire(request, analysis)
        assert result.startswith(stable)
        assert shared_prefix(baseline, result) >= len(stable.encode())
        assert shared_prefix(baseline, result) > shared_prefix(canonical(json.loads(baseline)), canonical(json.loads(result)))


def test_review_without_brief_or_ledger_still_shares_frozen_source_registry_and_analysis():
    initial, analysis = _request()
    review = deepcopy(initial)
    review.pop("brief")
    review.pop("previous_response")
    review["task"] = "review_campaign_semantics"
    review["checks"] = ["independent system also differs, no promise of cross-phase provider cache"]
    first, second = wire(initial, analysis), wire(review, analysis)
    payload = json.loads(first)
    stable = "{" + ",".join(canonical(key) + ":" + canonical(payload[key]) for key in STABLE_KEYS[:4]) + ","
    assert first.startswith(stable) and second.startswith(stable)


def test_nested_maps_are_canonical_regardless_of_insertion_order():
    request, analysis = _request()
    request["new_metadata"] = {"z": 1, "a": {"z": False, "a": [None, "雪"]}}
    original = wire(request, analysis)
    def reverse_maps(value):
        if isinstance(value, dict):
            return {key: reverse_maps(value[key]) for key in reversed(list(value))}
        if isinstance(value, list):
            return [reverse_maps(item) for item in value]
        return value
    assert wire(reverse_maps(request), reverse_maps(analysis)) == original


def test_source_and_evidence_changes_break_prefix_at_the_changed_stable_field():
    request, analysis = _request()
    baseline = wire(request, analysis)
    changed_request, changed_analysis = _request(201)
    changed_source = wire(changed_request, changed_analysis)
    registry_start = baseline.encode().index(b',"cover_evidence_registry":')
    assert shared_prefix(baseline, changed_source) < registry_start
    changed_request, changed_analysis = deepcopy(request), deepcopy(analysis)
    changed_analysis["evidence"][0]["statement"] = "different supported annotation"
    changed_request["story_analysis"] = director_projection(changed_analysis)
    changed_evidence = wire(changed_request, changed_analysis)
    assert shared_prefix(baseline, changed_evidence) > registry_start
    contract_start = baseline.encode().index(b',"evidence_encoding_contract":')
    assert shared_prefix(baseline, changed_evidence) < contract_start


def test_unbound_and_nonbeneficial_requests_remain_byte_identical():
    request, analysis = _request(1)
    raw = json.dumps(request, ensure_ascii=False, indent=2)
    assert serialize_campaign_request(raw, analysis) == raw
    request, analysis = _request()
    request["frozen_source_context"] = None
    raw = json.dumps(request, ensure_ascii=False, indent=2)
    assert serialize_campaign_request(raw, analysis) == raw
    assert serialize_campaign_request(raw, None) == raw
