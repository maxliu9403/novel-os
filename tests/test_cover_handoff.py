from __future__ import annotations

import hashlib
import json

import pytest

from core.cover_handoff import build_cover_concepts, parse_cover_handoff, parse_cover_handoff_v2


def _handoff(
    title: str = "The Door Is Mine",
    language: str = "English",
    market_scope: str = "English serialized fiction",
) -> dict:
    return {
        "schema_version": 1,
        "title": title,
        "author": "A. Writer",
        "language": language,
        "genre": "domestic revenge",
        "target_audience": "women 30-50 rebuilding after betrayal",
        "market_scope": market_scope,
        "core_task": "A caregiver claims an independent home.",
        "core_conflict": "Two families demand her labor and income.",
        "emotional_promise": "anger followed by earned independence",
        "protagonist": {
            "role": "caregiver",
            "visual_identity": "composed woman in practical work clothes",
            "agency_signal": "holding the only key to her new home",
        },
        "relationship_or_power_contrast": "one woman facing two entitled households",
        "decisive_story_node": "her name appears alone on the deed",
        "secondary_task": {
            "story_function": "protect her child",
            "visual_signal": "a small backpack beside a packed moving box",
        },
        "world_signals": ["fictional commuter district", "modest new apartment"],
        "title_direction": {
            "hierarchy": "large exact title",
            "preferred_zone": "top",
            "readability": "mobile_thumbnail",
        },
        "forbidden_elements": ["real cities", "logos", "ending spoilers"],
    }


def _prompt(handoff: dict) -> str:
    return (
        "# Approved Novel Prompt\n\n"
        "COVER_HANDOFF_BEGIN\n"
        "```json\n"
        + json.dumps(handoff, ensure_ascii=False, indent=2)
        + "\n```\nCOVER_HANDOFF_END\n"
    )


def test_parse_cover_handoff_uses_json_boundary_and_prompt_hash() -> None:
    prompt = _prompt(_handoff())

    brief = parse_cover_handoff(prompt)

    assert brief.title == "The Door Is Mine"
    assert brief.target_audience.startswith("women 30-50")
    assert brief.source_prompt_sha256 == hashlib.sha256(prompt.encode()).hexdigest()


def test_parse_cover_handoff_supports_non_english_titles() -> None:
    brief = parse_cover_handoff(_prompt(_handoff("门钥匙只在我手里", "Chinese")))

    assert brief.title == "门钥匙只在我手里"
    assert brief.language == "Chinese"


def test_parse_cover_handoff_v2_normalizes_the_same_source_hash() -> None:
    prompt = _prompt(_handoff())

    brief = parse_cover_handoff_v2(prompt)

    assert brief.schema_version == 2
    assert brief.source_prompt_sha256 == hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def test_parse_cover_handoff_rejects_missing_or_duplicate_boundaries() -> None:
    with pytest.raises(ValueError, match="COVER_HANDOFF_BEGIN"):
        parse_cover_handoff("# prompt without handoff")
    prompt = _prompt(_handoff())
    with pytest.raises(ValueError, match="exactly one"):
        parse_cover_handoff(prompt + prompt)


def test_build_cover_concepts_extracts_precise_story_signals() -> None:
    brief = parse_cover_handoff(_prompt(_handoff()))

    concepts = build_cover_concepts(brief, count=4)

    assert [item.visual_strategy for item in concepts] == [
        "protagonist_confrontation",
        "decisive_story_node",
        "symbolic_evidence",
        "world_relationship_pressure",
    ]
    assert all(brief.title in item.generation_prompt for item in concepts)
    assert all(brief.core_conflict in item.generation_prompt for item in concepts)
    assert all(brief.target_audience in item.generation_prompt for item in concepts)
    assert any(brief.decisive_story_node in item.generation_prompt for item in concepts)
    assert any(brief.secondary_task["visual_signal"] in item.generation_prompt for item in concepts)
    assert len({item.focal_scene for item in concepts}) == 4


def test_english_market_prompts_use_premium_western_story_cover_direction() -> None:
    brief = parse_cover_handoff(_prompt(_handoff()))

    prompt = build_cover_concepts(brief, count=4)[0].generation_prompt

    assert "Do not infer or invent ethnicity" in prompt
    assert "American white casting" not in prompt
    assert "premium US commercial fiction magazine cover" in prompt
    assert "one frozen cinematic story moment" in prompt
    assert "visible action, reaction, and stakes" in prompt
    assert "Western editorial typography" in prompt


def test_non_western_market_prompts_keep_market_neutral_casting() -> None:
    brief = parse_cover_handoff(
        _prompt(_handoff(language="Chinese", market_scope="Chinese serialized fiction"))
    )

    prompt = build_cover_concepts(brief, count=4)[0].generation_prompt

    assert "American white casting" not in prompt
    assert "premium US commercial fiction magazine cover" not in prompt
    assert "Western editorial typography" not in prompt


def test_build_cover_concepts_enforces_three_to_five() -> None:
    brief = parse_cover_handoff(_prompt(_handoff()))
    with pytest.raises(ValueError, match="between 3 and 5"):
        build_cover_concepts(brief, count=2)
