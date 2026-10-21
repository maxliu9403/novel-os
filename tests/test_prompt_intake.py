from pathlib import Path

from commercial_fixtures import commercial_story_fixture
from commercial_story import commercial_story_block
from narrative_format import infer_narrative_format
from novel_classification import infer_classification
from prompt_intake import ingest_prompt
from state_manager import StoryState


def test_ingest_prompt_preserves_source_and_applies_overrides(tmp_path: Path):
    source = tmp_path / "source.md"
    prompt = "# A Second Spring\n\nA woman rebuilds her life after forty.\n\nGenre: Contemporary Fiction\n"
    source.write_text(prompt, encoding="utf-8")

    result = ingest_prompt(
        tmp_path / "project",
        source,
        {
            "audience": "US adult readers",
            "language": "English",
            "chapters": 2,
            "words": 5000,
        },
    )

    project = tmp_path / "project"
    assert (project / "outputs/input/prompt.md").read_text(encoding="utf-8") == prompt
    assert result.brief["title"] == "A Second Spring"
    assert result.brief["genre"] == "Contemporary Fiction"
    assert result.brief["classification"]["schema_version"] == "novel-classification.v1"
    assert result.brief["narrative_format"]["confirmation_status"] == "confirmed"
    assert result.brief["narrative_format"]["selection_source"] == "prompt_explicit"
    assert result.brief["audience"] == "US adult readers"
    assert result.brief["target_chapters"] == 2
    assert (project / "outputs/input/brief.json").exists()
    assert (project / "outputs/story_bible.md").exists()

    state = StoryState(str(project))
    assert state.metadata["language"] == "English"
    assert state.metadata["classification"]["primary_genre_id"] == "contemporary_realism"
    assert state.metadata["narrative_format"]["total_chapters"] == 2
    assert state.story_bible["prompt_source"] == "outputs/input/prompt.md"


def test_ingest_prompt_accepts_stdin_text(tmp_path: Path):
    result = ingest_prompt(
        tmp_path / "project",
        "-",
        {"title": "From stdin", "genre": "Thriller"},
        stdin_text="A locked-room mystery in Boston.",
    )

    assert result.raw_prompt == "A locked-room mystery in Boston."
    assert result.brief["title"] == "From stdin"
    assert result.brief["genre"] == "Thriller"


def test_ingest_prompt_prefers_the_locked_classification_block(tmp_path: Path):
    result = ingest_prompt(
        tmp_path / "project",
        "-",
        stdin_text="""# A Family Reckoning

Genre: Women's Fiction / Family
Audience: women 45-60
Chapters: 22

[NOVEL_CLASSIFICATION_JSON]
{"primary_genre_id":"womens_fiction","secondary_genre_ids":["family_drama"],"story_type_ids":["revenge","second_chance"],"tone_ids":["angst"],"setting_ids":["contemporary_urban"],"audience":{"channel":"female","age_band":"midlife"},"length":{"form":"short","chapter_band":"chapters_0_50"}}
[/NOVEL_CLASSIFICATION_JSON]
""",
    )

    classification = result.brief["classification"]
    assert classification["source"] == "author_confirmed"
    assert classification["story_type_ids"] == ["revenge", "second_chance"]
    assert StoryState(str(tmp_path / "project")).metadata["classification"] == classification


def test_prompt_intake_persists_the_approved_commercial_contract(tmp_path: Path):
    prompt = (
        "# Built From Her Records\n\n"
        + commercial_story_block(commercial_story_fixture())
    )

    result = ingest_prompt(tmp_path / "project", "-", stdin_text=prompt)

    assert result.brief["commercial_story_contract_id"].startswith(
        "commercial-story:"
    )
    assert result.brief["commercial_story_contract"]["schema_version"] == 1


def test_prompt_without_commercial_block_keeps_optional_fields_absent(tmp_path: Path):
    result = ingest_prompt(
        tmp_path / "project",
        "-",
        stdin_text="# Ordinary Story\n\nA quiet premise.",
    )

    assert "commercial_story_contract" not in result.brief
    assert "commercial_story_contract_id" not in result.brief
    assert result.brief["narrative_format"]["confirmation_status"] == "pending_confirmation"


def test_prompt_intake_preserves_an_author_confirmed_multi_volume_choice(tmp_path: Path):
    classification = infer_classification(
        genre="Women's Fiction", premise="A family secret", chapters=80
    )
    format_contract = infer_narrative_format(
        classification,
        chapters=80,
        target_words=72000,
        explicit_length=True,
    ).with_confirmation("author_selected")
    import json

    result = ingest_prompt(
        tmp_path / "project",
        "-",
        stdin_text=(
            "# Four Movements\n\nGenre: Women's Fiction\n\n"
            "[NARRATIVE_FORMAT_JSON]\n"
            + json.dumps(format_contract.to_dict())
            + "\n[/NARRATIVE_FORMAT_JSON]\n"
        ),
    )

    assert result.brief["target_chapters"] == 80
    assert result.brief["narrative_format"]["mode"] == "multi_volume"
    assert result.brief["narrative_format"]["volume_count"] == 4
    assert result.brief["narrative_format"]["selection_source"] == "author_selected"


def test_master_prompt_heading_is_not_used_as_book_title(tmp_path: Path):
    source = tmp_path / "prompt.md"
    source.write_text(
        """# Master Prompt: Contemporary U.S. Women's Fiction

- Primary category: **Adult Contemporary Women's Fiction**.
- Primary audience: women approximately 35-55.
- Language: natural contemporary American English.
- Default working title: **The Life She Kept**. Treat it as a starting point, not mandatory.
- Use **20-22 chapters total**.
- Target **1,200-1,800 English words per chapter**.
""",
        encoding="utf-8",
    )

    result = ingest_prompt(tmp_path / "project", source)

    assert result.brief["title"] == "Untitled"
    assert result.brief["working_title"] == "The Life She Kept"
    assert result.brief["genre"] == "Adult Contemporary Women's Fiction"
    assert result.brief["audience"] == "women approximately 35-55"
    assert result.brief["target_chapters"] == 22
    assert result.brief["target_words"] == 33000
