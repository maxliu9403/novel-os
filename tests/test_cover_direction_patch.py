"""Patch transport preserves the full plan; semantic acceptance stays downstream."""
from copy import deepcopy
import hashlib
import json

import pytest

from core.cover_direction_patch import DirectionPatchError, apply_direction_patch, make_patch_contract


def base():
    return {"schema_version": 2, "director_model": "fixture", "profile_version": "cover-profiles.v10",
            "visual_identity": {"design_thesis": "Architecture of a choice"},
            "plans": [{"concept_id": f"concept-{i}", "frozen_action": f"action {i}",
                       "campaign": {"layers": [{"layer_id": "main", "cast": ["character"]}],
                                    "interaction_edges": [{"action": "acts"}]},
                       "story_evidence_refs": [f"evidence:{i}"], "gaze_graph": ["looks at the doorway"]}
                      for i in range(1, 5)], "visual_assumptions": []}


def patch(payload, **overrides):
    return {"schema": "cover-direction-patch.v1", "base_sha256": make_patch_contract(payload, 4)["base_sha256"],
            "plan_updates": [{"concept_id": "concept-2", "changes": {"frozen_action": "stops at the doorway"}}],
            "shared_updates": {}, **overrides}


def test_targeted_field_replacement_preserves_every_untouched_value_and_input():
    original = base()
    frozen = deepcopy(original)
    response = patch(original)
    result = apply_direction_patch(original, response, 4)
    expected = deepcopy(original)
    expected["plans"][1]["frozen_action"] = "stops at the doorway"
    assert result == expected
    assert original == frozen
    result["plans"][0]["campaign"]["layers"][0]["cast"].append("later mutation")
    result["plans"][1]["gaze_graph"].append("later mutation")
    assert original == frozen
    assert response == patch(original)


def test_arrays_and_objects_replace_as_wholes_without_recursive_merge():
    original = base()
    campaign = {"layers": [{"layer_id": "replacement"}]}
    response = patch(original, plan_updates=[{"concept_id": "concept-2", "changes": {
        "campaign": campaign, "gaze_graph": ["turns toward the light"]}}],
        shared_updates={"visual_identity": {"design_thesis": "A different shared language"},
                        "visual_assumptions": [], "director_notes": "Review the complete merged portfolio."})
    result = apply_direction_patch(original, response, 4)
    assert result["plans"][1]["campaign"] == campaign
    assert "interaction_edges" not in result["plans"][1]["campaign"]
    assert result["plans"][1]["gaze_graph"] == ["turns toward the light"]
    assert result["visual_identity"] == response["shared_updates"]["visual_identity"]
    assert result["director_notes"] == "Review the complete merged portfolio."
    response["shared_updates"]["visual_identity"]["design_thesis"] = "response changed later"
    assert result["visual_identity"]["design_thesis"] == "A different shared language"


@pytest.mark.parametrize("mutation", ["wrong_schema", "wrong_hash", "missing_key", "unknown_key",
    "full_payload", "unknown_concept", "duplicate_concept", "concept_id_change", "new_plan",
    "empty_changes", "no_op", "empty_patch", "nested_path", "delete", "metadata",
    "shared_metadata", "malformed_updates", "malformed_change", "malformed_shared", "malformed_update"])
def test_forbidden_or_malformed_patch_is_rejected_without_mutating_base(mutation):
    original = base()
    frozen = deepcopy(original)
    response = patch(original)
    if mutation == "wrong_schema": response["schema"] = "other"
    elif mutation == "wrong_hash": response["base_sha256"] = "f" * 64
    elif mutation == "missing_key": response.pop("shared_updates")
    elif mutation == "unknown_key": response["extra"] = "no"
    elif mutation == "full_payload": response = original
    elif mutation == "unknown_concept": response["plan_updates"][0]["concept_id"] = "concept-99"
    elif mutation == "duplicate_concept": response["plan_updates"] *= 2
    elif mutation == "concept_id_change": response["plan_updates"][0]["changes"] = {"concept_id": "replacement"}
    elif mutation == "new_plan": response["shared_updates"] = {"plans": original["plans"] + [original["plans"][0]]}
    elif mutation == "empty_changes": response["plan_updates"][0]["changes"] = {}
    elif mutation == "no_op": response["plan_updates"][0]["changes"] = {"frozen_action": "action 2"}
    elif mutation == "empty_patch": response["plan_updates"] = []
    elif mutation == "nested_path": response["plan_updates"][0]["changes"] = {"campaign.layers[0].cast": []}
    elif mutation == "delete": response["plan_updates"][0] = {"concept_id": "concept-2", "remove": ["cast"]}
    elif mutation == "metadata": response["plan_updates"][0]["changes"] = {"source_binding": {"approved": True}}
    elif mutation == "shared_metadata": response["shared_updates"] = {"compiled_recipes": {"image_prompt": "unreviewed"}}
    elif mutation == "malformed_updates": response["plan_updates"] = {"concept-2": {}}
    elif mutation == "malformed_change": response["plan_updates"][0]["changes"] = []
    elif mutation == "malformed_shared": response["shared_updates"] = []
    else: response["plan_updates"] = [False]
    with pytest.raises(DirectionPatchError):
        apply_direction_patch(original, response, 4)
    assert original == frozen


@pytest.mark.parametrize("mutation", ["count", "count_bool", "count_out_of_range", "missing_plans",
    "wrong_plan_count", "duplicate_id", "blank_id", "non_text_id", "non_plan", "non_json", "non_string_key",
    "ambiguous_json_keys", "non_finite", "invalid_unicode", "whitespace_id"])
def test_invalid_base_never_gets_a_patch_contract(mutation):
    original = base()
    count = 4
    if mutation == "count": count = 3
    elif mutation == "count_bool": count = True
    elif mutation == "count_out_of_range": count = 6
    elif mutation == "missing_plans": original.pop("plans")
    elif mutation == "wrong_plan_count": original["plans"].pop()
    elif mutation == "duplicate_id": original["plans"][1]["concept_id"] = "concept-1"
    elif mutation == "blank_id": original["plans"][0]["concept_id"] = " "
    elif mutation == "non_text_id": original["plans"][0]["concept_id"] = {"id": "x"}
    elif mutation == "non_plan": original["plans"][0] = None
    elif mutation == "non_json": original["plans"][0]["campaign"] = object()
    elif mutation == "non_string_key": original["plans"][0]["campaign"] = {1: "numeric key"}
    elif mutation == "ambiguous_json_keys": original["plans"][0]["campaign"] = {1: "a", "1": "b"}
    elif mutation == "non_finite": original["plans"][0]["campaign"]["value"] = float("nan")
    elif mutation == "invalid_unicode": original["plans"][0]["frozen_action"] = "\ud800"
    else: original["plans"][0]["concept_id"] = " concept-1 "
    with pytest.raises(DirectionPatchError):
        make_patch_contract(original, count)


def test_patch_base_hash_is_canonical_and_covers_every_raw_field():
    original = base()
    reversed_keys = dict(reversed(list(original.items())))
    contract = make_patch_contract(original, 4)
    assert contract["base_sha256"] == make_patch_contract(reversed_keys, 4)["base_sha256"]
    expected = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert contract["base_sha256"] == expected
    original["director_notes"] = "new base content"
    assert contract["base_sha256"] != make_patch_contract(original, 4)["base_sha256"]


def test_stale_base_and_partial_invalid_updates_cannot_leak_partial_mutations():
    original = base()
    response = patch(original)
    modified = deepcopy(original)
    modified["plans"][3]["frozen_action"] = "a newer draft"
    with pytest.raises(DirectionPatchError, match="base_sha256"):
        apply_direction_patch(modified, response, 4)
    response["plan_updates"].append({"concept_id": "foreign", "changes": {"frozen_action": "invalid"}})
    with pytest.raises(DirectionPatchError):
        apply_direction_patch(original, response, 4)
    assert original == base()


def test_all_existing_plans_may_be_explicitly_updated_without_reordering_them():
    original = base()
    response = patch(original, plan_updates=[
        {"concept_id": p["concept_id"], "changes": {"frozen_action": "explicitly corrected"}}
        for p in reversed(original["plans"])])
    result = apply_direction_patch(original, response, 4)
    assert [p["concept_id"] for p in result["plans"]] == [f"concept-{i}" for i in range(1, 5)]
    assert all(p["frozen_action"] == "explicitly corrected" for p in result["plans"])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "numeric key"}, "\ud800"])
def test_replacement_values_must_be_lossless_finite_json(value):
    original = base()
    response = patch(original, shared_updates={"visual_identity": value})
    with pytest.raises(DirectionPatchError):
        apply_direction_patch(original, response, 4)
    assert original == base()


def test_explicit_null_is_a_value_not_deletion_and_semantic_validation_is_downstream():
    original = base()
    response = patch(original, plan_updates=[{"concept_id": "concept-2", "changes": {"campaign": None}}])
    result = apply_direction_patch(original, response, 4)
    assert "campaign" in result["plans"][1] and result["plans"][1]["campaign"] is None
    # This transport does not pretend to approve such a merged v10 plan.
