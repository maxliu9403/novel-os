from pathlib import Path

import pytest

from llm_client import LLMError
from orchestrator import NovelOrchestrator
from prompt_intake import ingest_prompt
from state_parser import normalize_agent_output


class FakeLLM:
    provider = "fake"
    model = "fake-model"

    def __init__(self):
        self.calls = []

    def run_agent(self, agent_name, prompt):
        self.calls.append((agent_name, prompt))
        if agent_name == "architect":
            return """[STORY_FOUNDATION_JSON]
{
  "title": "Test",
  "premise": "A complete premise.",
  "themes": ["renewal"],
  "characters": [
    {"id": "char_001", "name": "Mara Vale", "role": "protagonist", "external_goal": "begin again"}
  ],
  "plot_threads": [
    {"id": "plot_001", "name": "Second Chance", "description": "Mara rebuilds", "type": "main", "priority": 5}
  ],
  "style": {"tone": "intimate", "pov": "third_limited", "tense": "past", "prose_style": "balanced"},
  "chapters": [
    {"number": 1, "title": "Opening", "pov": "Mara Vale", "summary": "Mara chooses change."},
    {"number": 2, "title": "Resolution", "pov": "Mara Vale", "summary": "Mara commits."}
  ]
}
[/STORY_FOUNDATION_JSON]

# ARCHITECT ANALYSIS: Test

## Chapter Plan
1. Opening
2. Resolution
"""
        if agent_name == "style_curator":
            return """[STYLE_ANALYSIS]
Consistency_Score: 9/10
Genre_Adherence: 9/10
Voice_Strength: 9/10
[/STYLE_ANALYSIS]

[REVISED_CHAPTER]
# Chapter One

Polished prose.
[/REVISED_CHAPTER]

[STYLE_STATE_UPDATE]
Maintained_Characteristics: [close POV]
[/STYLE_STATE_UPDATE]
"""
        if agent_name == "editor":
            return """[EDITOR_ANALYSIS]
Mode: line
[/EDITOR_ANALYSIS]
[REVISED_CHAPTER]
# Chapter One

Edited prose.
[/REVISED_CHAPTER]
[EDITOR_STATE_UPDATE]
Quality_Score_Before: 6/10
Quality_Score_After: 8/10
[/EDITOR_STATE_UPDATE]
"""
        raise AssertionError(agent_name)


def test_plan_outline_calls_architect_and_chapter_prompt_reads_it(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    ingest_prompt(project, prompt, {"title": "Author Title", "chapters": 2, "words": 5000})
    orch = NovelOrchestrator(str(project))
    fake = FakeLLM()
    orch._llm = fake

    orch.plan_outline(2, 5000)
    chapter = orch.state.get_chapter(1)
    chapter_prompt = orch._generate_chapter_outline_prompt(chapter)

    assert fake.calls[0][0] == "architect"
    assert (project / "outputs/outline.md").exists()
    assert (project / "outputs/outline.json").exists()
    assert (project / "outputs/input/foundation.json").exists()
    assert orch.state.get_character("char_001").full_name == "Mara Vale"
    assert orch.state.metadata["title"] == "Author Title"
    assert orch.state.get_plot_thread("plot_001").name == "Second Chance"
    assert orch.state.get_chapter(1).title == "Opening"
    assert "Mara Vale" in (project / "outputs/story_bible.md").read_text(encoding="utf-8")
    assert "ARCHITECT ANALYSIS: Test" in chapter_prompt


def test_style_curator_writes_clean_candidate_final(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    ingest_prompt(project, prompt, {"chapters": 1, "words": 1000})
    orch = NovelOrchestrator(str(project))
    chapter = orch.state.create_chapter(1)
    chapter.status = "validated"
    chapter.continuity_checks["status"] = "PASS"
    orch.state.save_state()
    revised = project / "outputs/manuscript/chapter_001_revised.md"
    revised.write_text("# Chapter One\n\nRough prose.\n", encoding="utf-8")
    orch._llm = FakeLLM()

    result = orch.curate_chapter(1)

    assert result == "# Chapter One\n\nPolished prose.\n"
    assert (project / "outputs/manuscript/chapter_001_candidate_final.md").read_text(
        encoding="utf-8"
    ) == result
    assert (project / "outputs/feedback/chapter_001_style_report.md").exists()


def test_editor_wrapper_is_not_saved_in_revised_manuscript(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    ingest_prompt(project, prompt, {"chapters": 1, "words": 1000})
    orch = NovelOrchestrator(str(project))
    chapter = orch.state.create_chapter(1)
    chapter.status = "drafted"
    orch.state.save_state()
    (project / "outputs/manuscript/chapter_001_draft.md").write_text(
        "# Chapter One\n\nDraft prose.\n", encoding="utf-8"
    )
    orch._llm = FakeLLM()

    orch.edit_chapter(1)

    revised = (project / "outputs/manuscript/chapter_001_revised.md").read_text(encoding="utf-8")
    assert revised == "# Chapter One\n\nEdited prose.\n"
    assert "REVISED_CHAPTER" not in revised


def test_invalid_editor_contract_does_not_mutate_story_state(tmp_path: Path):
    class InvalidEditor:
        provider = "fake"
        model = "fake-model"

        def run_agent(self, _agent, _prompt):
            return """[EDITOR_STATE_UPDATE]
Quality_Score_Before: 1/10
Quality_Score_After: 10/10
[/EDITOR_STATE_UPDATE]
"""

    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    ingest_prompt(project, prompt, {"chapters": 1, "words": 1000})
    orch = NovelOrchestrator(str(project))
    chapter = orch.state.create_chapter(1)
    chapter.status = "drafted"
    orch.state.save_state()
    (project / "outputs/manuscript/chapter_001_draft.md").write_text(
        "Draft prose.\n", encoding="utf-8"
    )
    orch._llm = InvalidEditor()
    orch.raise_llm_errors = True

    with pytest.raises(LLMError, match=r"missing \[REVISED_CHAPTER\]"):
        orch.edit_chapter(1)

    assert chapter.quality_scores == {}


def test_scribe_zero_width_contract_marker_is_normalized(tmp_path: Path):
    class ZeroWidthScribe:
        provider = "fake"
        model = "fake-model"

        def run_agent(self, _agent, _prompt):
            return (
                "# Chapter One\n\nMara turned the key.\n\n"
                "[S\u200bCRIBE_STATE_UPDATE]\n"
                "Characters_Present: [Mara Vale]\n"
                "Key_Events: [Mara opens the studio]\n"
                "[/SCRIBE_STATE_UPDATE]\n"
            )

    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    ingest_prompt(project, prompt, {"chapters": 1, "words": 1000})
    orch = NovelOrchestrator(str(project))
    chapter = orch.state.create_chapter(1)
    orch.state.save_state()
    orch._llm = ZeroWidthScribe()
    orch.raise_llm_errors = True

    output = project / "outputs/manuscript/chapter_001_draft.md"
    result = orch._run_agent_or_save_prompt(
        "scribe",
        "write chapter 1",
        project / "outputs/manuscript/chapter_001_prompt.md",
        output,
        dry_run=False,
        label="Scribe drafting",
        chapter_number=1,
    )

    assert "SCRIBE_STATE_UPDATE" not in result
    assert chapter.characters_present == ["Mara Vale"]
    assert chapter.plot_advances == ["Mara opens the studio"]


def test_protocol_tag_spelling_is_normalized():
    text = "[Scribe state update]\nKey_Events: [A change]\n[/Scribe state update]"

    normalized = normalize_agent_output(text)

    assert normalized.count("[SCRIBE_STATE_UPDATE]") == 1
    assert normalized.count("[/SCRIBE_STATE_UPDATE]") == 1
