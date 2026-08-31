import copy
import hashlib
import json
from pathlib import Path

import pytest

import orchestrator as orchestrator_module
from commercial_fixtures import architect_foundation_text, commercial_story_fixture
from canon_ledger import canonical_canon_sha
from continuity_engine import Finding
from llm_client import LLMError
from model_router import ModelRouter
from orchestrator import NovelOrchestrator
from prompt_intake import ingest_prompt
from proposals import ProposalStore
from state_parser import normalize_agent_output


class FakeLLM:
    provider = "fake"
    model = "fake-model"

    def __init__(self):
        self.calls = []

    def run_agent(self, agent_name, prompt):
        self.calls.append((agent_name, prompt))
        if agent_name == "architect":
            if "Outline Chapter" in prompt:
                return """# Chapter 1

## Chapter Goal
Mara chooses change.

[CHAPTER_CONTRACT]
{"chapter": 1, "goal": "Mara chooses change.", "active_choice": "Mara opens the studio."}
[/CHAPTER_CONTRACT]
"""
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
        if agent_name == "scribe":
            return """<!--
CHAPTER: 1 - Opening
POV: Mara Vale
-->
# Chapter One

Mara turned the key.

[SCRIBE_STATE_UPDATE]
Characters_Present: [Mara Vale]
Key_Events: [Mara opens the studio]
[/SCRIBE_STATE_UPDATE]
"""
        if agent_name == "continuity_guardian":
            return """[CONTINUITY_REPORT]
Chapter: 1
Status: PASS
Warnings: []
[/CONTINUITY_REPORT]

[CONTINUITY_STATE_UPDATE]
New_Facts_Established: [The studio key still works]
[/CONTINUITY_STATE_UPDATE]
"""
        raise AssertionError(agent_name)


def _state_snapshot(state):
    return copy.deepcopy(
        {
            "metadata": state.metadata,
            "story_bible": state.story_bible,
            "characters": state.characters,
            "codex": state.codex,
            "relationships": state.relationships,
            "collections": state.collections,
            "continuity_exemptions": state.continuity_exemptions,
            "compile_styles": state.compile_styles,
            "plot_threads": state.plot_threads,
            "chapters": state.chapters,
            "timeline": state.timeline,
            "style_profile": state.style_profile,
            "binder": state.binder.to_list(),
            "session_log": state.session_log,
        }
    )


def _prepared_orchestrator(tmp_path: Path, *, mode: str = "legacy_apply"):
    project = tmp_path / "project"
    orch = NovelOrchestrator(str(project), state_update_mode=mode)
    orch.state.create_chapter(1)
    orch.state.save_state()
    orch._llm = FakeLLM()
    return project, orch


def test_proposal_only_scribe_run_persists_artifacts_without_mutating_state(
    tmp_path: Path, monkeypatch
):
    project, orch = _prepared_orchestrator(tmp_path, mode="proposal_only")
    state_path = project / "outputs/state/story_state.json"
    before_state = _state_snapshot(orch.state)
    before_bytes = state_path.read_bytes()
    save_calls = []
    monkeypatch.setattr(orch.state, "save_state", lambda: save_calls.append(True))
    output_path = project / "outputs/manuscript/chapter_001_draft.md"

    result = orch._run_agent_or_save_prompt(
        "scribe",
        "write chapter 1",
        project / "outputs/manuscript/chapter_001_prompt.md",
        output_path,
        dry_run=False,
        label="Scribe drafting",
        chapter_number=1,
    )

    expected = "# Chapter One\n\nMara turned the key.\n"
    assert result == expected
    assert output_path.read_bytes() == expected.encode("utf-8")
    assert output_path.with_suffix(".md.raw").read_text(encoding="utf-8").endswith(
        "[/SCRIBE_STATE_UPDATE]\n"
    )
    assert len(orch.last_canon_proposal_ids) == 1
    proposal = ProposalStore(project).load(orch.last_canon_proposal_ids[0])
    assert proposal.agent_name == "scribe"
    assert proposal.source_artifact_sha == hashlib.sha256(output_path.read_bytes()).hexdigest()
    assert list(proposal.delta["key_events"]) == ["Mara opens the studio"]
    assert save_calls == []
    assert _state_snapshot(orch.state) == before_state
    assert state_path.read_bytes() == before_bytes


def test_state_update_mode_rejects_invalid_values(tmp_path: Path):
    with pytest.raises(ValueError, match="state_update_mode"):
        NovelOrchestrator(str(tmp_path / "invalid"), state_update_mode="invalid")


def test_orchestrator_routes_and_caches_clients_by_agent_role(tmp_path: Path, monkeypatch):
    created = []

    class RoleClient:
        def __init__(self, role):
            self.provider = f"provider-{role}"
            self.model = f"model-{role}"

    def client_for(_self, role):
        normalized = ModelRouter.normalize_role(role)
        created.append(normalized)
        return RoleClient(normalized)

    monkeypatch.setattr(ModelRouter, "client_for", client_for)
    orch = NovelOrchestrator(str(tmp_path / "project"))

    writer = orch._get_llm("scribe")
    assert orch._get_llm("writer") is writer
    assert orch._get_llm("continuity_guardian") is not writer
    assert orch._get_llm("style_curator") is not writer
    assert created == ["writer", "guardian", "style"]
    assert orch.runtime_provenance_for("scribe") == (
        "provider-writer",
        "model-writer",
    )


def test_legacy_llm_injection_remains_default_for_every_role(tmp_path: Path):
    orch = NovelOrchestrator(str(tmp_path / "project"))
    legacy = FakeLLM()
    orch._llm = legacy

    assert orch.llm is legacy
    assert orch._get_llm("architect") is legacy
    assert orch._get_llm("style_curator") is legacy
    assert orch.runtime_provenance_for("continuity_guardian") == (
        "fake",
        "fake-model",
    )


def test_state_update_mode_override_is_isolated_and_legacy_still_saves(
    tmp_path: Path, monkeypatch
):
    project, orch = _prepared_orchestrator(tmp_path)
    save_calls = []
    original_save = orch.state.save_state

    def save_spy():
        save_calls.append(True)
        original_save()

    monkeypatch.setattr(orch.state, "save_state", save_spy)
    output_path = project / "outputs/manuscript/chapter_001_draft.md"

    orch._run_agent_or_save_prompt(
        "scribe",
        "proposal call",
        project / "outputs/feedback/proposal_prompt.md",
        output_path,
        dry_run=False,
        label="Scribe drafting",
        chapter_number=1,
        state_update_mode="proposal_only",
    )

    assert orch.state_update_mode == "legacy_apply"
    assert orch.state.get_chapter(1).plot_advances == []
    assert save_calls == []
    assert len(orch.last_canon_proposal_ids) == 1

    orch._run_agent_or_save_prompt(
        "scribe",
        "legacy call",
        project / "outputs/feedback/legacy_prompt.md",
        output_path,
        dry_run=False,
        label="Scribe drafting",
        chapter_number=1,
    )

    assert orch.state.get_chapter(1).plot_advances == ["Mara opens the studio"]
    assert save_calls == [True]
    assert orch.last_canon_proposal_ids == []

    with pytest.raises(ValueError, match="state_update_mode"):
        orch._run_agent_or_save_prompt(
            "scribe",
            "invalid call",
            project / "outputs/feedback/invalid_prompt.md",
            output_path,
            dry_run=False,
            label="Scribe drafting",
            chapter_number=1,
            state_update_mode="invalid",
        )
    assert orch.state_update_mode == "legacy_apply"


def test_proposal_only_style_curator_persists_candidate_proposal_without_state_changes(
    tmp_path: Path, monkeypatch
):
    project, orch = _prepared_orchestrator(tmp_path, mode="proposal_only")
    chapter = orch.state.get_chapter(1)
    chapter.status = "validated"
    chapter.continuity_checks["status"] = "PASS"
    revised_path = project / "outputs/manuscript/chapter_001_revised.md"
    revised_path.write_text("# Chapter One\n\nRough prose.\n", encoding="utf-8")
    orch.state.save_state()
    state_path = project / "outputs/state/story_state.json"
    before_state = _state_snapshot(orch.state)
    before_bytes = state_path.read_bytes()
    save_calls = []
    monkeypatch.setattr(orch.state, "save_state", lambda: save_calls.append(True))

    result = orch.curate_chapter(1)

    candidate_path = project / "outputs/manuscript/chapter_001_candidate_final.md"
    report_path = project / "outputs/feedback/chapter_001_style_report.md"
    raw_path = report_path.with_suffix(".md.raw")
    assert result == "# Chapter One\n\nPolished prose.\n"
    assert candidate_path.read_bytes() == result.encode("utf-8")
    assert "[STYLE_ANALYSIS]" in report_path.read_text(encoding="utf-8")
    assert raw_path.read_text(encoding="utf-8") == FakeLLM().run_agent(
        "style_curator", "unused"
    )
    assert len(orch.last_canon_proposal_ids) == 1
    proposal = ProposalStore(project).load(orch.last_canon_proposal_ids[0])
    assert proposal.agent_name == "style_curator"
    assert proposal.source_artifact_sha == hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    assert list(proposal.delta["maintained_characteristics"]) == ["close POV"]
    assert save_calls == []
    assert _state_snapshot(orch.state) == before_state
    assert state_path.read_bytes() == before_bytes


def test_proposal_only_submit_draft_sanitizes_and_proposes_without_state_changes(
    tmp_path: Path, monkeypatch
):
    project, orch = _prepared_orchestrator(tmp_path, mode="proposal_only")
    submitted = tmp_path / "submitted_draft.md"
    submitted.write_text(FakeLLM().run_agent("scribe", "manual"), encoding="utf-8")
    state_path = project / "outputs/state/story_state.json"
    before_state = _state_snapshot(orch.state)
    before_bytes = state_path.read_bytes()
    save_calls = []
    monkeypatch.setattr(orch.state, "save_state", lambda: save_calls.append(True))

    orch.submit_draft(1, str(submitted))

    draft_path = project / "outputs/manuscript/chapter_001_draft.md"
    assert draft_path.read_text(encoding="utf-8") == "# Chapter One\n\nMara turned the key.\n"
    assert len(orch.last_canon_proposal_ids) == 1
    proposal = ProposalStore(project).load(orch.last_canon_proposal_ids[0])
    assert proposal.source_artifact_sha == hashlib.sha256(draft_path.read_bytes()).hexdigest()
    assert list(proposal.delta["characters_present"]) == ["Mara Vale"]
    assert save_calls == []
    assert _state_snapshot(orch.state) == before_state
    assert state_path.read_bytes() == before_bytes


def test_proposal_only_submit_edit_saves_manuscript_projection_and_proposal(
    tmp_path: Path, monkeypatch
):
    project, orch = _prepared_orchestrator(tmp_path, mode="proposal_only")
    submitted = tmp_path / "submitted_edit.md"
    submitted.write_text(FakeLLM().run_agent("editor", "manual"), encoding="utf-8")
    state_path = project / "outputs/state/story_state.json"
    before_state = _state_snapshot(orch.state)
    before_bytes = state_path.read_bytes()
    save_calls = []
    monkeypatch.setattr(orch.state, "save_state", lambda: save_calls.append(True))

    orch.submit_edit(1, str(submitted))

    revised_path = project / "outputs/manuscript/chapter_001_revised.md"
    assert revised_path.read_text(encoding="utf-8") == "# Chapter One\n\nEdited prose.\n"
    assert "EDITOR_" not in revised_path.read_text(encoding="utf-8")
    assert len(orch.last_canon_proposal_ids) == 1
    proposal = ProposalStore(project).load(orch.last_canon_proposal_ids[0])
    assert proposal.source_artifact_sha == hashlib.sha256(revised_path.read_bytes()).hexdigest()
    assert proposal.delta["quality_score_after"] == "8/10"
    assert save_calls == []
    assert _state_snapshot(orch.state) == before_state
    assert state_path.read_bytes() == before_bytes


@pytest.mark.parametrize(
    ("operation", "initial_status", "agent_name", "artifact"),
    [
        ("write", "planned", "scribe", "outputs/manuscript/chapter_001_draft.md"),
        ("edit", "drafted", "editor", "outputs/manuscript/chapter_001_revised.md"),
        (
            "validate",
            "edited",
            "continuity_guardian",
            "outputs/feedback/chapter_001_continuity_report.md",
        ),
    ],
)
def test_proposal_only_outer_agent_workflows_do_not_mutate_or_save_state(
    tmp_path: Path,
    monkeypatch,
    operation: str,
    initial_status: str,
    agent_name: str,
    artifact: str,
):
    project, orch = _prepared_orchestrator(tmp_path, mode="proposal_only")
    chapter = orch.state.get_chapter(1)
    chapter.status = initial_status
    draft_path = project / "outputs/manuscript/chapter_001_draft.md"
    draft_path.write_text("# Chapter One\n\nDraft prose.\n", encoding="utf-8")
    if operation == "validate":
        revised_path = project / "outputs/manuscript/chapter_001_revised.md"
        revised_path.write_text("# Chapter One\n\nEdited prose.\n", encoding="utf-8")
        monkeypatch.setattr(
            orchestrator_module,
            "run_continuity_checks",
            lambda *_args, **_kwargs: [
                Finding(
                    severity="warning",
                    category="test_finding",
                    message="A deterministic warning.",
                    chapter=1,
                )
            ],
        )
    orch.state.save_state()
    state_path = project / "outputs/state/story_state.json"
    before_state = _state_snapshot(orch.state)
    before_bytes = state_path.read_bytes()
    save_calls = []
    monkeypatch.setattr(orch.state, "save_state", lambda: save_calls.append(True))

    if operation == "write":
        orch.write_chapter(1)
    elif operation == "edit":
        orch.edit_chapter(1)
    else:
        orch.validate_chapter(1)

    artifact_path = project / artifact
    assert artifact_path.exists()
    assert len(orch.last_canon_proposal_ids) == 1
    proposal = ProposalStore(project).load(orch.last_canon_proposal_ids[0])
    assert proposal.agent_name == agent_name
    assert proposal.source_artifact_sha == hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert save_calls == []
    assert _state_snapshot(orch.state) == before_state
    assert state_path.read_bytes() == before_bytes


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


def test_architect_foundation_must_echo_approved_commercial_contract() -> None:
    approved = commercial_story_fixture()
    text = architect_foundation_text(approved.to_dict())

    parsed = NovelOrchestrator._parse_story_foundation(text, 3, approved)

    assert parsed["commercial_story_contract"] == approved.to_dict()
    assert parsed["commercial_story_contract_id"] == approved.contract_id

    mutated = json.loads(json.dumps(approved.to_dict()))
    mutated["premise_engine"]["sacred_asset"] = "home"
    with pytest.raises(ValueError, match="approved commercial story contract"):
        NovelOrchestrator._parse_story_foundation(
            architect_foundation_text(mutated), 3, approved
        )


def test_architect_foundation_cannot_omit_approved_commercial_contract() -> None:
    approved = commercial_story_fixture()
    text = architect_foundation_text(approved.to_dict())
    payload = json.loads(
        text.split("[STORY_FOUNDATION_JSON]\n", 1)[1].split(
            "\n[/STORY_FOUNDATION_JSON]", 1
        )[0]
    )
    payload.pop("commercial_story_contract")
    payload.pop("commercial_story_contract_id")
    omitted = (
        "[STORY_FOUNDATION_JSON]\n"
        + json.dumps(payload)
        + "\n[/STORY_FOUNDATION_JSON]"
    )

    with pytest.raises(ValueError, match="approved commercial story contract"):
        NovelOrchestrator._parse_story_foundation(omitted, 3, approved)


def test_proposal_only_plan_outline_preserves_canonical_state_and_file(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    ingest_prompt(project, prompt, {"title": "Author Title", "chapters": 2, "words": 5000})
    orch = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    orch._llm = FakeLLM()
    state_path = project / "outputs/state/story_state.json"
    before_snapshot = _state_snapshot(orch.state)
    before_hash = canonical_canon_sha(orch.state)
    before_bytes = state_path.read_bytes()

    orch.plan_outline(2, 5000)

    assert _state_snapshot(orch.state) == before_snapshot
    assert canonical_canon_sha(orch.state) == before_hash
    assert state_path.read_bytes() == before_bytes
    assert orch.state.get_chapter(1) is None
    assert (project / "outputs/input/foundation.json").exists()


def test_proposal_only_plan_chapter_rehydrates_runtime_without_changing_canon(
    tmp_path: Path,
):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    ingest_prompt(project, prompt, {"title": "Author Title", "chapters": 2, "words": 5000})
    planner = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    planner._llm = FakeLLM()
    planner.plan_outline(2, 5000)

    resumed = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    resumed._llm = FakeLLM()
    state_path = project / "outputs/state/story_state.json"
    before_snapshot = _state_snapshot(resumed.state)
    before_hash = canonical_canon_sha(resumed.state)
    before_bytes = state_path.read_bytes()

    resumed.plan_chapter(1)

    chapter_prompt = (project / "outputs/chapter_001_prompt.md").read_text(
        encoding="utf-8"
    )
    assert "Mara Vale" in chapter_prompt
    assert "Mara chooses change." in chapter_prompt
    outline_path = project / "outputs/chapter_001_outline.md"
    assert outline_path.exists()
    assert len(resumed.last_canon_proposal_ids) == 1
    proposal = ProposalStore(project).load(resumed.last_canon_proposal_ids[0])
    assert proposal.agent_name == "architect"
    assert proposal.chapter == 1
    assert proposal.source_artifact_sha == hashlib.sha256(outline_path.read_bytes()).hexdigest()
    assert proposal.delta["chapter_contract"]["active_choice"] == "Mara opens the studio."
    assert _state_snapshot(resumed.state) == before_snapshot
    assert canonical_canon_sha(resumed.state) == before_hash
    assert state_path.read_bytes() == before_bytes
    assert resumed.state.get_chapter(1) is None


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


def test_empty_editor_manuscript_block_does_not_mutate_story_state(tmp_path: Path):
    class EmptyEditor:
        provider = "fake"
        model = "fake-model"

        def run_agent(self, _agent, _prompt):
            return """[EDITOR_ANALYSIS]
Mode: developmental
[/EDITOR_ANALYSIS]
[REVISED_CHAPTER]
[EDITOR_STATE_UPDATE]
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
    draft_path = project / "outputs/manuscript/chapter_001_draft.md"
    draft_path.write_text("Draft prose.\n", encoding="utf-8")
    orch._llm = EmptyEditor()
    orch.raise_llm_errors = True

    with pytest.raises(LLMError, match=r"empty revised chapter"):
        orch.edit_chapter(1)

    assert not (project / "outputs/manuscript/chapter_001_revised.md").exists()
    assert chapter.quality_scores == {}


def test_missing_editor_manuscript_block_does_not_write_sanitized_analysis(
    tmp_path: Path,
):
    class AnalysisOnlyEditor:
        provider = "fake"
        model = "fake-model"

        def run_agent(self, _agent, _prompt):
            return """[EDITOR_ANALYSIS]
Mode: developmental
Issues_Found: analysis only
[/EDITOR_ANALYSIS]
"""

    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    ingest_prompt(project, prompt, {"chapters": 1, "words": 1000})
    orch = NovelOrchestrator(str(project))
    chapter = orch.state.create_chapter(1)
    chapter.status = "drafted"
    orch.state.save_state()
    draft_path = project / "outputs/manuscript/chapter_001_draft.md"
    draft_path.write_text("Draft prose.\n", encoding="utf-8")
    orch._llm = AnalysisOnlyEditor()

    orch.edit_chapter(1)

    assert not (project / "outputs/manuscript/chapter_001_revised.md").exists()
    assert (project / "outputs/manuscript/chapter_001_revised.md.raw").exists()
    assert chapter.quality_scores == {}


def test_editor_accepts_revised_chapter_without_closing_marker(tmp_path: Path):
    class TruncatedEditor:
        provider = "fake"
        model = "fake-model"

        def run_agent(self, _agent, _prompt):
            return """[EDITOR_ANALYSIS]
Mode: developmental
[/EDITOR_ANALYSIS]
[REVISED_CHAPTER]
# Chapter One

Recovered edited prose.
[EDITOR_STATE_UPDATE]
Quality_Score_Before: 5/10
Quality_Score_After: 8/10
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
    orch._llm = TruncatedEditor()
    orch.raise_llm_errors = True

    orch.edit_chapter(1)

    revised = (project / "outputs/manuscript/chapter_001_revised.md").read_text(
        encoding="utf-8"
    )
    assert revised == "# Chapter One\n\nRecovered edited prose.\n"
    assert chapter.quality_scores["quality_score_before"] == 5.0
    assert chapter.quality_scores["quality_score_after"] == 8.0


def test_style_curator_accepts_revised_chapter_without_closing_marker(tmp_path: Path):
    class TruncatedStyleCurator:
        provider = "fake"
        model = "fake-model"

        def run_agent(self, _agent, _prompt):
            return """[STYLE_ANALYSIS]
Consistency_Score: 9/10
[/STYLE_ANALYSIS]
[REVISED_CHAPTER]
# Chapter One

Recovered polished prose.
[STYLE_STATE_UPDATE]
Maintained_Characteristics: [close POV]
[/STYLE_STATE_UPDATE]
"""

    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    ingest_prompt(project, prompt, {"chapters": 1, "words": 1000})
    orch = NovelOrchestrator(str(project))
    chapter = orch.state.create_chapter(1)
    chapter.status = "validated"
    chapter.continuity_checks["status"] = "PASS"
    orch.state.save_state()
    (project / "outputs/manuscript/chapter_001_revised.md").write_text(
        "Rough prose.\n", encoding="utf-8"
    )
    orch._llm = TruncatedStyleCurator()
    orch.raise_llm_errors = True

    orch.curate_chapter(1)

    candidate = (project / "outputs/manuscript/chapter_001_candidate_final.md").read_text(
        encoding="utf-8"
    )
    assert candidate == "# Chapter One\n\nRecovered polished prose.\n"
    report = (project / "outputs/feedback/chapter_001_style_report.md").read_text(
        encoding="utf-8"
    )
    assert "STYLE_STATE_UPDATE" in report


def test_proposal_only_submit_edit_accepts_revised_chapter_without_closing_marker(
    tmp_path: Path, monkeypatch
):
    project, orch = _prepared_orchestrator(tmp_path, mode="proposal_only")
    submitted = tmp_path / "submitted_edit.md"
    submitted.write_text(
        """[EDITOR_ANALYSIS]
Mode: line
[/EDITOR_ANALYSIS]
[REVISED_CHAPTER]
# Chapter One

Recovered submitted prose.
[EDITOR_STATE_UPDATE]
Quality_Score_After: 9/10
[/EDITOR_STATE_UPDATE]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(orch.state, "save_state", lambda: None)

    orch.submit_edit(1, str(submitted))

    revised = (project / "outputs/manuscript/chapter_001_revised.md").read_text(
        encoding="utf-8"
    )
    assert revised == "# Chapter One\n\nRecovered submitted prose.\n"
    assert len(orch.last_canon_proposal_ids) == 1
    proposal = ProposalStore(project).load(orch.last_canon_proposal_ids[0])
    assert proposal.delta["quality_score_after"] == "9/10"


def test_legacy_submit_edit_accepts_revised_chapter_without_closing_marker(
    tmp_path: Path,
):
    project, orch = _prepared_orchestrator(tmp_path)
    submitted = tmp_path / "submitted_edit.md"
    submitted.write_text(
        """[EDITOR_ANALYSIS]
Mode: line
[/EDITOR_ANALYSIS]
[REVISED_CHAPTER]
# Chapter One

Recovered legacy prose.
[EDITOR_STATE_UPDATE]
Quality_Score_After: 9/10
[/EDITOR_STATE_UPDATE]
""",
        encoding="utf-8",
    )
    orch.raise_llm_errors = True

    orch.submit_edit(1, str(submitted))

    revised = (project / "outputs/manuscript/chapter_001_revised.md").read_text(
        encoding="utf-8"
    )
    assert revised == "# Chapter One\n\nRecovered legacy prose.\n"
    assert orch.state.get_chapter(1).quality_scores["quality_score_after"] == 9.0


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
