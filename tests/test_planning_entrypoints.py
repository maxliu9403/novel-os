"""Independent CLI/API outline jobs share the same durable planning work."""
import json

import pytest

from api.services import PHASES
from orchestrator import NovelOrchestrator
from pipeline_runner import PipelineError, project_execution_lock
from test_outline_planning import PlanningModel, SOURCE


def test_standalone_and_api_resume_same_project_without_a_pipeline_manifest(tmp_path, monkeypatch):
    import orchestrator as cli
    book = NovelOrchestrator(str(tmp_path), state_update_mode="proposal_only")
    inputs = book.outputs_dir / "input"
    inputs.mkdir(exist_ok=True)
    (inputs / "prompt.md").write_text(SOURCE)
    model = PlanningModel(fail_batch=5)
    prior = book.outputs_dir / "outline.json"
    prior.write_text('{"author":"previous edition"}')
    with monkeypatch.context() as environment:
        environment.chdir(tmp_path)
        environment.setattr(cli, "_provider_configured", lambda: True)
        environment.setattr(cli.NovelOrchestrator, "_get_llm", lambda self, role: model)
        with pytest.raises(TimeoutError):
            cli.main(["plan", "outline", "--chapters", "8", "--words", "8000"])
    assert prior.read_text() == '{"author":"previous edition"}'
    directory = book.outputs_dir / "standalone" / "planning"
    checkpoint, = directory.rglob("batch-001.json")
    original = checkpoint.read_bytes()

    resumed = NovelOrchestrator(str(tmp_path), state_update_mode="proposal_only")
    resumed._llm = PlanningModel()
    text = PHASES["plan_outline"](resumed, {"chapters": 8, "words": 8000})
    assert [stage for stage, _ in resumed._llm.calls] == ["batch", "review"]
    assert checkpoint.read_bytes() == original
    assert (book.outputs_dir / "outline.md").read_text() == text
    assert json.loads(prior.read_text())["metadata"]["planning_mode"] == "staged"
    assert len(json.loads((inputs / "foundation.json").read_text())["chapters"]) == 8
    assert not list((book.outputs_dir / "runs").glob("*/run.json"))
    assert NovelOrchestrator(str(tmp_path))._uses_staged_outline()


def test_pipeline_ownership_prevents_standalone_from_writing_or_dispatching(tmp_path):
    book = NovelOrchestrator(str(tmp_path))
    inputs = book.outputs_dir / "input"
    inputs.mkdir(exist_ok=True)
    (inputs / "prompt.md").write_text(SOURCE)
    book._llm = PlanningModel()
    with project_execution_lock(tmp_path, "pipeline"):
        with pytest.raises(PipelineError, match="already active"):
            book.plan_outline(4, 4000)
    assert book._llm.calls == []
    assert not (book.outputs_dir / "standalone").exists()


def test_manually_created_book_plans_from_saved_character_and_story_bible(tmp_path):
    from state_manager import initialize_project
    state = initialize_project(str(tmp_path), "The Lost Bell", "Mystery")
    state.update_story_bible("promise", "The bell was hidden in the frozen cistern.")
    state.save_state()
    book = NovelOrchestrator(str(tmp_path))
    book._llm = PlanningModel()
    book.plan_outline(4, 4000)
    assert [stage for stage, _ in book._llm.calls] == ["global", "batch", "review"]
    source = book._llm.calls[0][1]["complete_adopted_source"]
    assert "The Lost Bell" in source and "hidden in the frozen cistern" in source
    assert not (book.outputs_dir / "input" / "prompt.md").exists()
    assert (book.outputs_dir / "standalone" / "planning" / "coverage.json").is_file()
    again = NovelOrchestrator(str(tmp_path))
    again._llm = PlanningModel()
    again.plan_outline(4, 4000)
    assert again._llm.calls == []
    again.state.update_story_bible("additional_fact", "A copper key fits the tower door.")
    again.state.save_state()
    again.plan_outline(4, 4000)
    assert "copper key" in again._llm.calls[0][1]["complete_adopted_source"]
