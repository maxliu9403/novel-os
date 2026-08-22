import json
from pathlib import Path

from orchestrator import NovelOrchestrator
from pipeline_models import RunSpec
from pipeline_runner import PipelineRunner
from state_manager import StoryState


class PipelineLLM:
    provider = "fake"
    model = "fake-fiction-model"

    def run_agent(self, agent_name, prompt):
        if agent_name == "architect" and "Full Novel Blueprint" in prompt:
            foundation = {
                "title": "One Prompt Book",
                "premise": "Mara chooses a new life.",
                "themes": ["renewal"],
                "setting": {"time_period": "present", "primary_location": "Boston", "world_rules": []},
                "characters": [{
                    "id": "char_001", "name": "Mara Vale", "role": "protagonist",
                    "internal_desire": "self-trust", "external_goal": "open a studio",
                }],
                "plot_threads": [{
                    "id": "plot_001", "name": "Second Chance", "description": "Mara starts over",
                    "type": "main", "priority": 5, "resolution_chapter": 2,
                }],
                "style": {"tone": "intimate", "pov": "third_limited", "tense": "past", "prose_style": "balanced"},
                "chapters": [
                    {"number": 1, "title": "The Key", "pov": "Mara Vale", "summary": "Mara accepts the lease.", "target_words": 30},
                    {"number": 2, "title": "Open Door", "pov": "Mara Vale", "summary": "Mara opens the studio.", "target_words": 30},
                ],
            }
            return (
                "[STORY_FOUNDATION_JSON]\n"
                + json.dumps(foundation)
                + "\n[/STORY_FOUNDATION_JSON]\n\n# ARCHITECT ANALYSIS\n\n## Chapter Plan\nComplete.\n"
            )
        if agent_name == "architect":
            number = 2 if "Chapter 2" in prompt else 1
            return f"# Chapter {number}\n\n## Chapter Goal\nAdvance the decision.\n\n## Beats\n1. Choice\n2. Consequence\n\n## Ending Hook\nA door opens.\n"
        if agent_name == "scribe":
            number = 2 if "Chapter 2" in prompt else 1
            return f"""<!--
CHAPTER: {number} - Chapter {number}
POV: Mara Vale
-->
# Chapter {number}

Mara turned the key. The room waited, bright and unfinished.

[SCRIBE_STATE_UPDATE]
Characters_Present: [Mara Vale]
Key_Events: [Mara commits to change]
Emotional_Shifts: [Mara Vale: hopeful]
New_Information_Revealed: [The studio is hers]
Foreshadowing_Planted: [The open door]
[/SCRIBE_STATE_UPDATE]
"""
        if agent_name == "editor":
            number = 2 if "Chapter 2" in prompt else 1
            return f"""[EDITOR_ANALYSIS]
Mode: line
[/EDITOR_ANALYSIS]
[REVISED_CHAPTER]
# Chapter {number}

Mara turned the key. The unfinished room filled with morning light.
[/REVISED_CHAPTER]
[EDITOR_STATE_UPDATE]
Quality_Score_Before: 6/10
Quality_Score_After: 8/10
Remaining_Concerns: [None]
[/EDITOR_STATE_UPDATE]
"""
        if agent_name == "continuity_guardian":
            return """[CONTINUITY_REPORT]
Status: PASS
Critical_Issues: [None]
Warnings: [None]
New_Facts_Established: [The studio is open]
[/CONTINUITY_REPORT]
"""
        if agent_name == "style_curator":
            number = 2 if "Chapter 2" in prompt else 1
            return f"""[STYLE_ANALYSIS]
Consistency_Score: 9/10
Genre_Adherence: 9/10
Voice_Strength: 9/10
[/STYLE_ANALYSIS]
[REVISED_CHAPTER]
# Chapter {number}

Mara turned the key. Morning light claimed the unfinished room.
[/REVISED_CHAPTER]
[STYLE_STATE_UPDATE]
Maintained_Characteristics: [intimate close POV]
[/STYLE_STATE_UPDATE]
"""
        raise AssertionError(agent_name)


def _real_orchestrator_with_fake_llm(project_path):
    orchestrator = NovelOrchestrator(project_path)
    orchestrator._llm = PipelineLLM()
    return orchestrator


def test_real_orchestrator_pipeline_completes_two_chapters(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    spec = RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=2,
        target_words=60,
        approval_policy="auto",
    )

    manifest = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm).run(spec)

    assert manifest.status == "completed", manifest.error
    assert manifest.get("book.check").status == "done"
    assert (project / "outputs/input/foundation.json").exists()
    assert (project / "outputs/manuscript/chapter_002_final.md").exists()
    final_state = StoryState(str(project))
    assert final_state.get_chapter(1).status == "complete"
    assert final_state.get_chapter(2).status == "complete"
    book = (project / "outputs/deliverables/book.md").read_text(encoding="utf-8")
    assert "# One Prompt Book" in book
    assert "Morning light claimed" in book
