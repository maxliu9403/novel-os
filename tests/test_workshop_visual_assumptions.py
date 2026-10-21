"""A complete model reply must not be regenerated for legacy visual-note strings."""
import json
import re

import pytest

from api import workshop
from api.workshop_contract_repair import normalize_workshop_contracts
from test_workshop_api import client, complete_prompt, create, finish  # noqa: F401
from test_workshop_contract_repair import response, stub_model, submit, _seed_legacy_candidate


def with_assumptions(prompt, assumptions):
    match = re.search(r"COVER_HANDOFF_BEGIN\s*(\{.*?\})\s*COVER_HANDOFF_END", prompt, re.S)
    payload = json.loads(match.group(1))
    payload["visual_assumptions"] = assumptions
    return prompt[:match.start(1)] + json.dumps(payload, ensure_ascii=False) + prompt[match.end(1):]


def cover(prompt):
    return json.loads(re.search(r"COVER_HANDOFF_BEGIN\s*(\{.*?\})\s*COVER_HANDOFF_END", prompt, re.S).group(1))


def test_complete_reply_with_visual_notes_finishes_without_another_model_call(client, monkeypatch, tmp_path):
    notes = ["Cover colors have not been confirmed.", "保留人物的自主选择；服装尚未确定。"]
    raw = with_assumptions(complete_prompt(tmp_path), notes)
    calls = stub_model(monkeypatch, lambda *_: response(raw))
    data = create(client)
    _seed_legacy_candidate(tmp_path, data, raw)
    submit(client, data, "生成完整骨架")
    current = finish(client, data["id"])

    assert current["status"] == "ready", current["last_error"]
    assert len(calls) == 1
    rows = cover(current["prompt_md"])["visual_assumptions"]
    assert [row["proposed_value"] for row in rows] == notes
    assert all(row["status"] == "pending_confirmation" for row in rows)
    candidates = tmp_path / "workshop_v2" / data["id"] / "candidates"
    revision = current["revision"] - 1
    assert (candidates / f"{revision}-attempt-1.md").read_text() == raw
    assert (candidates / f"{revision}.md").read_text() == current["prompt_md"]
    records = [json.loads(line) for line in (candidates.parent / "diagnostics" / f"{revision}-brainstorm.jsonl").read_text().splitlines()]
    repaired = [r for r in records if r["event"] == "contract.normalized"]
    assert len(repaired) == 1 and repaired[0]["normalized_fields"] == 2
    assert not any(note in json.dumps(repaired, ensure_ascii=False) for note in notes)


def test_only_cover_json_changes_and_normalization_is_idempotent(tmp_path):
    structured = {"field": "hair", "proposed_value": "brown", "reason": "unconfirmed", "status": "pending_confirmation"}
    raw = with_assumptions(complete_prompt(tmp_path), [structured, "  Original note unchanged.\n"])
    normalized, changes = normalize_workshop_contracts(raw)
    assert changes == ["COVER_HANDOFF.visual_assumptions[1]"]
    old, new = cover(raw), cover(normalized)
    assert new["visual_assumptions"][0] == structured
    assert new["visual_assumptions"][1]["proposed_value"] == "  Original note unchanged.\n"
    assert {k: v for k, v in new.items() if k != "visual_assumptions"} == {k: v for k, v in old.items() if k != "visual_assumptions"}
    before, after = raw.split("COVER_HANDOFF_BEGIN")[0], raw.split("COVER_HANDOFF_END")[1]
    assert normalized.startswith(before + "COVER_HANDOFF_BEGIN")
    assert normalized.endswith("COVER_HANDOFF_END" + after)
    assert normalize_workshop_contracts(normalized) == (normalized, [])
    path = tmp_path / "normalized.md"
    path.write_text(normalized)
    workshop._validate_candidate(path)


@pytest.mark.parametrize("value", [[], {}, {"field": "hair"}, [None], [12], [""], ["   "]])
def test_does_not_guess_or_discard_other_shapes(tmp_path, value):
    raw = with_assumptions(complete_prompt(tmp_path), value)
    assert normalize_workshop_contracts(raw) == (raw, [])


def test_safe_normalization_does_not_bypass_other_contract_failures(client, monkeypatch, tmp_path):
    raw = with_assumptions(complete_prompt(tmp_path), ["Unconfirmed visual note.", 42])
    calls = stub_model(monkeypatch, lambda *_: response(raw))
    data = create(client)
    _seed_legacy_candidate(tmp_path, data, raw)
    submit(client, data, "生成完整骨架")
    current = finish(client, data["id"])
    assert len(calls) == 3
    assert current["status"] == "error"
    assert current["prompt_md"] == ""
    assert "visual_assumptions[1]" in current["last_error"]
