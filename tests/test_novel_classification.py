from __future__ import annotations

import json

import pytest

from core.novel_classification import (
    CATALOG_VERSION,
    SCHEMA_VERSION,
    NovelClassification,
    catalog_payload,
    infer_classification,
    markdown_front_matter,
    parse_classification_block,
)


def test_engine_separates_shelf_story_emotion_setting_and_audience() -> None:
    value = infer_classification(
        genre="Adult Contemporary Women's Fiction / Family / Revenge",
        audience="women aged 45-60",
        tone="Angst and emotional realism",
        premise="A wife discovers her husband's affair and prepares to leave.",
        chapters=22,
    ).to_dict()

    assert value["schema_version"] == SCHEMA_VERSION
    assert value["catalog_version"] == CATALOG_VERSION
    assert value["primary_genre_id"] == "womens_fiction"
    assert value["secondary_genre_ids"] == ["family_drama"]
    assert value["story_type_ids"] == ["revenge", "domestic_betrayal"]
    assert value["tone_ids"] == ["angst", "emotional_realism"]
    assert value["setting_ids"] == ["family_domestic"]
    assert value["audience"] == {"channel": "female", "age_band": "midlife"}
    assert value["length"] == {
        "form": "short",
        "chapter_band": "chapters_0_50",
    }
    assert value["filter_type_ids"] == [
        "womens_fiction",
        "family_drama",
        "revenge",
        "domestic_betrayal",
        "angst",
        "emotional_realism",
        "family_domestic",
    ]


def test_catalog_covers_and_normalizes_the_screenshot_labels() -> None:
    catalog = catalog_payload()
    entries = [item for axis in catalog["axes"].values() for item in axis]
    ids = {item["id"] for item in entries}

    assert {
        "young_adult", "contemporary_urban", "social_life", "mystery",
        "romance", "sweet_romance", "ceo_romance", "revenge",
        "supernatural", "angst", "mafia", "werewolf", "family_drama",
        "emotional_realism", "thriller", "fantasy", "womens_fiction",
        "dark_romance", "second_chance", "royal_intrigue", "western",
        "post_apocalyptic",
    } <= ids
    thriller = next(item for item in entries if item["id"] == "thriller")
    angst = next(item for item in entries if item["id"] == "angst")
    assert "惊悚片" in thriller["legacy_labels"]
    assert "焦虑" in angst["legacy_labels"]


def test_explicit_contract_rejects_unknown_or_conflicting_ids() -> None:
    with pytest.raises(ValueError, match="unknown ids"):
        NovelClassification.from_dict({
            "primary_genre_id": "romance",
            "story_type_ids": ["invented_by_model"],
        })
    with pytest.raises(ValueError, match="mutually exclusive"):
        NovelClassification.from_dict({
            "primary_genre_id": "romance",
            "story_type_ids": ["sweet_romance", "dark_romance"],
        })


def test_derived_fields_and_identity_are_tamper_evident() -> None:
    payload = infer_classification(genre="狼人浪漫", audience="女频").to_dict()
    payload["filter_type_ids"] = ["romance"]
    with pytest.raises(ValueError, match="filter_type_ids"):
        NovelClassification.from_dict(payload)


def test_markdown_front_matter_exposes_the_h5_filter_fields() -> None:
    value = infer_classification(
        genre="Family Women's Fiction",
        premise="Second chance after betrayal",
        chapters=20,
    )
    front_matter = markdown_front_matter(value)

    assert front_matter.startswith("---\n")
    assert f'novel_os_schema: {json.dumps(SCHEMA_VERSION)}' in front_matter
    assert 'primary_genre_id: "womens_fiction"' in front_matter
    assert 'story_type_ids: ["second_chance", "domestic_betrayal"]' in front_matter
    assert "filter_type_ids:" in front_matter


def test_prompt_block_locks_only_catalog_ids() -> None:
    value = parse_classification_block("""
[NOVEL_CLASSIFICATION_JSON]
```json
{
  "primary_genre_id": "womens_fiction",
  "secondary_genre_ids": ["family_drama"],
  "story_type_ids": ["revenge", "second_chance"],
  "tone_ids": ["angst"],
  "setting_ids": ["contemporary_urban"],
  "audience": {"channel": "female", "age_band": "midlife"},
  "length": {"form": "short", "chapter_band": "chapters_0_50"}
}
```
[/NOVEL_CLASSIFICATION_JSON]
""")

    assert value is not None
    assert value.source == "author_confirmed"
    assert value.confidence == 1.0
    assert value.story_type_ids == ("revenge", "second_chance")


def test_rejected_prompt_options_do_not_become_published_types() -> None:
    value = infer_classification(
        genre="Women's Fiction / Family",
        audience="women 45-60",
        premise="A betrayed wife rebuilds her home.",
        raw_prompt="Forbidden: mafia, werewolf, CEO romance, dark romance.",
    )

    assert value.story_type_ids == ("domestic_betrayal",)
