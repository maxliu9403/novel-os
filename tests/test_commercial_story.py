from __future__ import annotations

import json

import pytest

from commercial_fixtures import commercial_story_fixture, commercial_story_payload
from commercial_story import (
    CommercialStoryContract,
    commercial_story_block,
    parse_commercial_story_block,
)


def test_commercial_story_contract_round_trips_with_stable_identity() -> None:
    contract = commercial_story_fixture()
    restored = CommercialStoryContract.from_dict(
        json.loads(json.dumps(contract.to_dict()))
    )

    assert restored == contract
    assert restored.contract_id == contract.contract_id
    assert restored.contract_id.startswith("commercial-story:")


def test_contract_requires_five_conflict_levels_and_two_belonging_anchors() -> None:
    payload = commercial_story_payload()
    payload["conflict_ladder"] = payload["conflict_ladder"][:4]
    with pytest.raises(ValueError, match="five conflict levels"):
        CommercialStoryContract.from_dict(payload)

    payload = commercial_story_payload()
    payload["premise_engine"]["belonging_anchors"] = ["self"]
    with pytest.raises(ValueError, match="belonging_anchors"):
        CommercialStoryContract.from_dict(payload)


def test_contract_rejects_unknown_nested_fields_and_wrong_quality_budget() -> None:
    payload = commercial_story_payload()
    payload["premise_engine"]["sample_title"] = "Surface Copy"
    with pytest.raises(ValueError, match="premise_engine has invalid fields"):
        CommercialStoryContract.from_dict(payload)

    payload = commercial_story_payload()
    payload["quality_budgets"]["identical_hook_type_max"] = 2
    with pytest.raises(ValueError, match="quality_budgets"):
        CommercialStoryContract.from_dict(payload)


def test_prompt_block_round_trips_and_rejects_duplicate_json_keys() -> None:
    contract = commercial_story_fixture()
    assert parse_commercial_story_block(commercial_story_block(contract)) == contract
    assert parse_commercial_story_block("ordinary prompt") is None

    duplicate = """[COMMERCIAL_STORY_JSON]
{"schema_version": 1, "schema_version": 1}
[/COMMERCIAL_STORY_JSON]"""
    with pytest.raises(ValueError, match="duplicate JSON field"):
        parse_commercial_story_block(duplicate)


def test_prompt_rejects_duplicate_blocks_and_authored_contract_id() -> None:
    block = commercial_story_block(commercial_story_fixture())
    with pytest.raises(ValueError, match="exactly one"):
        parse_commercial_story_block(block + "\n" + block)

    payload = commercial_story_payload()
    payload["contract_id"] = "commercial-story:" + "0" * 64
    authored = (
        "[COMMERCIAL_STORY_JSON]\n"
        + json.dumps(payload)
        + "\n[/COMMERCIAL_STORY_JSON]"
    )
    with pytest.raises(ValueError, match="contract_id"):
        parse_commercial_story_block(authored)
