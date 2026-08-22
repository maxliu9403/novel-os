from pathlib import Path

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
    assert result.brief["audience"] == "US adult readers"
    assert result.brief["target_chapters"] == 2
    assert (project / "outputs/input/brief.json").exists()
    assert (project / "outputs/story_bible.md").exists()

    state = StoryState(str(project))
    assert state.metadata["language"] == "English"
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
