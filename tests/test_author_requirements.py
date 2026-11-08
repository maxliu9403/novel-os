from copy import deepcopy
import hashlib
import json

import pytest

from core.author_requirements import (
    AuthorRequirementConflict, build_requirements, prompt_requirements,
    request_conflicts, validate_requirements, workshop_requirements,
)
from core.cover_refinement import bound_edit_prompt, edit_prompt, refinement_requirements
from api.poster_preferences import preference_context


def test_only_explicit_supersession_changes_status_and_originals_remain_exact():
    original = "  Keep the ending warm.\nDo not reunite the former couple.\n"
    source = "a" * 64
    ledger = build_requirements("story", [
        {"id": "one", "text": original},
        {"id": "two", "text": "Change the old direction; show regret."},
        {"id": "three", "text": "Use an open ending instead.", "supersedes": ["one"]},
    ], source_sha256=source)
    assert ledger["entries"][0]["text"] == original
    assert ledger["entries"][0]["text_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert ledger["entries"][0]["status"] == "superseded"
    assert ledger["entries"][0]["superseded_by"] == "three"
    assert ledger["entries"][1]["status"] == "active"  # No semantic keyword heuristic.
    projected = prompt_requirements(ledger)
    assert [entry["id"] for entry in projected["entries"]] == ["two", "three"]
    assert "Every formal candidate" in projected["priority"]
    assert validate_requirements(ledger, scope="story", source_sha256=source) == ledger
    copied = validate_requirements(ledger)
    copied["entries"][1]["text"] = "changed"
    assert ledger["entries"][1]["text"] != "changed"


@pytest.mark.parametrize("change", ["text", "status", "scope", "source"])
def test_ledger_integrity_rejects_rewriting_history(change):
    ledger = build_requirements("story", [{"id": "one", "text": "Choose a new ending."}], source_sha256="b" * 64)
    if change == "text": ledger["entries"][0]["text"] = "Choose the old ending."
    elif change == "status": ledger["entries"][0]["status"] = "superseded"
    elif change == "scope": ledger["scope"] = "cover"
    else: ledger["source_sha256"] = "c" * 64
    with pytest.raises(ValueError):
        validate_requirements(ledger)


@pytest.mark.parametrize("entries", [
    [{"id": "one", "text": "a", "supersedes": ["two"]}],
    [{"id": "one", "text": "a"}, {"id": "one", "text": "b"}],
    [{"id": "one", "text": "a", "supersedes": ["one"]}],
])
def test_invalid_or_forward_author_references_are_not_silently_discarded(entries):
    with pytest.raises(ValueError):
        build_requirements("story", entries)


def test_workshop_snapshot_keeps_all_user_text_not_model_suggestions():
    messages = [{"role": "user", "content": "  Keep her job.\n"},
                {"role": "assistant", "content": "Replace her job with marriage."},
                {"role": "user", "content": "No reunion; let her forgive herself instead."}]
    ledger = workshop_requirements(messages)
    assert [entry["text"] for entry in ledger["entries"]] == [messages[0]["content"], messages[2]["content"]]
    assert [entry["id"] for entry in ledger["entries"]] == ["author-message-1", "author-message-3"]


def test_repeated_original_text_shares_bytes_without_losing_chronological_precedence():
    original = "Retain her established professional identity. " * 100
    ledger = workshop_requirements([{"role": "user", "content": original},
        {"role": "user", "content": "Explore another profession instead."},
        {"role": "user", "content": original}])
    projected = prompt_requirements(ledger)
    entries = projected["entries"]
    assert entries[-1] == {"id": "author-message-3", "text_ref": "author-message-1"}
    assert entries[0]["text"] == original
    assert "chronological position" in projected["text_reference_policy"]
    restored = {row["id"]: row.get("text") for row in entries}
    assert [row.get("text", restored.get(row.get("text_ref"))) for row in entries] == [
        original, "Explore another profession instead.", original]
    assert ledger["entries"][-1]["text"] == original
    assert len(json.dumps(projected)) < len(json.dumps(ledger))


def test_preferences_recover_failed_intents_and_preserve_compatible_earlier_requests():
    records = [
        {"kind": "plan", "task_id": "t2", "request_id": "request2", "created_at": "2", "status": "failed",
         "input": {"preferences": {"notes": "Move the heroine to the center."}}},
        {"kind": "plan", "task_id": "t1", "request_id": "request1", "created_at": "1", "status": "complete",
         "input": {"preferences": {"market": "North America", "notes": "Preserve mature ages."}}},
    ]
    saved = deepcopy(records)
    context = preference_context(records, current={"notes": "Use warm lighting."}, request_id="request3")
    assert records == saved
    assert context["preferences"] == {"market": "North America", "notes": "Use warm lighting."}
    requirements = context["author_requirements"]
    assert len(requirements["entries"]) == 3
    assert [json.loads(entry["text"])["notes"] for entry in requirements["entries"]] == [
        "Preserve mature ages.", "Move the heroine to the center.", "Use warm lighting."]
    assert preference_context(records)["preferences"]["notes"] == "Move the heroine to the center."


def test_unchanged_preferences_do_not_expand_history_and_clear_is_explicit():
    records = [{"kind": "plan", "task_id": "t", "created_at": "1",
                "input": {"preferences": {"market": "US", "notes": "Warm light"}}}]
    context = preference_context(records, current={"market": "", "notes": "Warm light"})
    assert context["preferences"]["market"] == ""
    assert json.loads(context["author_requirements"]["entries"][-1]["text"]) == {"market": ""}
    same = preference_context(records, current={"market": "US", "notes": "Warm light"})
    assert len(same["author_requirements"]["entries"]) == 1


def test_refinement_chain_keeps_design_and_earlier_edits_but_freezes_new_scope():
    source = "a" * 64
    design = build_requirements("cover_design", [{"id": "design", "text": "Keep their mature ages."}], source_sha256=source)
    first = refinement_requirements({"_author_requirements": design}, "Move her to the center.", source_sha256=source)
    scene = {"_poster_refinement": {"prompt": "Move her to the center.", "author_requirements": first}}
    second = refinement_requirements(scene, "Replace the blue light with warm amber.", source_sha256=source)
    assert len(first["entries"]) == 2 and len(second["entries"]) == 3
    assert second["scope"] == "cover_refinement"
    text = bound_edit_prompt("Preserve both identities.", {
        "schema_version": 2, "prompt": second["entries"][-1]["text"], "author_requirements": second})
    assert "Move her to the center." in text and "warm amber" in text and "mature ages" in text
    assert "explicit changes override old pose" in text
    assert bound_edit_prompt("OLD PLAN", {"schema_version": 1}) == edit_prompt("OLD PLAN")
    with pytest.raises(ValueError, match="another story revision"):
        refinement_requirements(scene, "A changed world.", source_sha256="b" * 64)


def test_reported_scope_conflict_is_distinct_from_format_failure():
    with pytest.raises(AuthorRequirementConflict, match="已有结果保留"):
        request_conflicts({"request_conflicts": ["需要在故事设计中先确认新人物身份"]})
    request_conflicts({"request_conflicts": []})
    with pytest.raises(ValueError):
        request_conflicts({"request_conflicts": "silently ignore the author"})
