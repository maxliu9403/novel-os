import json
from pathlib import Path

import pytest

import pipeline_runner as pipeline_runner_module
from artifacts import ArtifactStore
from canon_ledger import canonical_canon_sha
from orchestrator import NovelOrchestrator
from pipeline_models import RunManifest, RunSpec, StageResult
from pipeline_runner import PipelineRunner
from promotion import PromotionService
from proposals import ProposalStore
from state_manager import StoryState


class PipelineLLM:
    provider = "fake"
    model = "fake-fiction-model"
    calls = []
    prompts = {}

    def run_agent(self, agent_name, prompt):
        type(self).calls.append(agent_name)
        type(self).prompts[agent_name] = prompt
        if agent_name == "architect" and "Full Novel Blueprint" in prompt:
            requested_chapters = 1 if "- Chapters: 1\n" in prompt else 2
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
                ][:requested_chapters],
            }
            return (
                "[STORY_FOUNDATION_JSON]\n"
                + json.dumps(foundation)
                + "\n[/STORY_FOUNDATION_JSON]\n\n# ARCHITECT ANALYSIS\n\n## Chapter Plan\nComplete.\n"
            )
        if agent_name == "architect":
            number = 2 if "Chapter 2" in prompt else 1
            contract = {
                "chapter": number,
                "goal": "Mara commits to opening the studio.",
                "obstacle": "She doubts the lease is affordable.",
                "active_choice": "She signs despite the risk.",
                "cost": "She spends her remaining savings.",
                "irreversible_change": "The lease becomes binding.",
                "local_payoff": "She receives the studio key.",
                "ending_pressure": "Opening day is tomorrow.",
                "preserve_facts": ["Mara has the key"],
                "allowed_knowledge": ["Mara knows the rent"],
                "world_event_ids": [],
            }
            contract_block = (
                "\n[CHAPTER_CONTRACT]\n"
                + json.dumps(contract)
                + "\n[/CHAPTER_CONTRACT]\n"
                if "[CHAPTER_CONTRACT]" in prompt
                else ""
            )
            return f"# Chapter {number}\n\n## Chapter Goal\nAdvance the decision.\n\n## Beats\n1. Choice\n2. Consequence\n\n## Ending Hook\nA door opens.\n{contract_block}"
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
Plot_Thread_Updates: [plot_001 | status=active | milestone=Mara advances the studio opening | chapter={number}]
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


def _one_chapter_spec(project: Path, prompt: Path, **overrides):
    values = {
        "project_path": str(project),
        "prompt_path": str(prompt),
        "num_chapters": 1,
        "target_words": 30,
        "approval_policy": "auto",
    }
    values.update(overrides)
    return RunSpec(**values)


def test_completed_plan_checkpoint_refreshes_proposal_runtime_state(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "outputs/input").mkdir(parents=True, exist_ok=True)
    (project / "outputs/input/foundation.json").write_text(
        json.dumps(
            {
                "title": "One Prompt Book",
                "premise": "Mara chooses a new life.",
                "themes": [],
                "setting": {},
                "characters": [],
                "plot_threads": [],
                "style": {},
                "chapters": [
                    {"number": 1, "title": "The Key", "target_words": 1},
                    {"number": 2, "title": "Open Door", "target_words": 1},
                ],
            }
        ),
        encoding="utf-8",
    )
    outline = project / "outputs/chapter_002_outline.md"
    outline.parent.mkdir(parents=True, exist_ok=True)
    outline.write_text("# Chapter 2\n", encoding="utf-8")

    spec = RunSpec(
        project_path=str(project),
        num_chapters=2,
        target_words=2,
        approval_policy="auto",
    )
    manifest = RunManifest.new(spec)
    previous = StageResult(
        phase="chapter.plan",
        chapter=2,
        status="done",
        artifact_paths=["outputs/chapter_002_outline.md"],
        artifact_hashes={
            "outputs/chapter_002_outline.md": PipelineRunner._sha256(outline)
        },
    )
    manifest.record(previous)

    orchestrator = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    runner = PipelineRunner()
    runner._active_orchestrator = orchestrator
    runner._rerun_started = False
    runner._last_valid_state_snapshot = ""

    runner._stage(
        manifest,
        project,
        runner._store(project, manifest.run_id),
        "chapter.plan",
        2,
        lambda: pytest.fail("completed plan checkpoint should be reused"),
        lambda _value: None,
        ["outputs/chapter_002_outline.md"],
    )

    assert orchestrator.state.get_chapter(2) is not None


def test_foundation_preserves_resolved_plot_thread_status(tmp_path: Path):
    project = tmp_path / "project"
    foundation_dir = project / "outputs/input"
    foundation_dir.mkdir(parents=True, exist_ok=True)
    (foundation_dir / "foundation.json").write_text(
        json.dumps(
            {
                "chapters": [],
                "characters": [],
                "plot_threads": [
                    {
                        "id": "plot_005",
                        "name": "Old Harbor Deadline",
                        "description": "The deadline has passed.",
                        "type": "subplot",
                        "status": "resolved",
                        "last_updated_chapter": 20,
                        "resolution_chapter": 20,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    orchestrator = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    runtime = orchestrator.build_proposal_runtime_state()

    thread = runtime.plot_threads["plot_005"]
    assert thread.status == "resolved"
    assert thread.last_updated_chapter == 20


def test_proposal_runtime_rebuilds_tracking_indexes_and_draft_status(tmp_path: Path):
    project = tmp_path / "project"
    foundation_dir = project / "outputs/input"
    manuscript_dir = project / "outputs/manuscript"
    foundation_dir.mkdir(parents=True, exist_ok=True)
    manuscript_dir.mkdir(parents=True, exist_ok=True)
    (foundation_dir / "foundation.json").write_text(
        json.dumps(
            {
                "title": "One Prompt Book",
                "premise": "Mara chooses a new life.",
                "themes": [],
                "setting": {},
                "characters": [
                    {
                        "id": "char_001",
                        "name": "Mara Vale",
                        "role": "protagonist",
                    },
                    {
                        "id": "char_002",
                        "name": "Owen Vale",
                        "role": "antagonist",
                    },
                ],
                "plot_threads": [
                    {
                        "id": "plot_001",
                        "name": "Second Chance",
                        "description": "Mara starts over.",
                        "type": "main",
                    }
                ],
                "style": {},
                "chapters": [
                    {"number": 1, "title": "The Key", "pov": "Mara Vale"},
                    {"number": 2, "title": "Open Door", "pov": "Mara Vale"},
                ],
            }
        ),
        encoding="utf-8",
    )

    state = StoryState(str(project))
    first = state.create_chapter(1)
    first.status = "complete"
    first.characters_present = ["Mara Vale", "Owen Vale"]
    first.plot_thread_updates = [
        {"thread_id": "plot_001", "status": "active", "chapter": 1}
    ]
    state.save_state()
    (manuscript_dir / "chapter_002_draft.md").write_text(
        "Mara opened the door.", encoding="utf-8"
    )

    orchestrator = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    runtime = orchestrator.build_proposal_runtime_state()

    assert runtime.characters["char_001"].last_appearance_chapter == 2
    assert runtime.characters["char_002"].last_appearance_chapter == 1
    assert runtime.plot_threads["plot_001"].last_updated_chapter == 1
    assert runtime.get_chapter(2).status == "drafted"


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
    assert final_state.characters["char_001"].full_name == "Mara Vale"
    assert final_state.plot_threads["plot_001"].name == "Second Chance"
    book = (project / "outputs/deliverables/book.md").read_text(encoding="utf-8")
    assert "# One Prompt Book" in book
    assert "Morning light claimed" in book


def test_evidence_pipeline_enforces_semantic_ending_contract(tmp_path: Path):
    class EnforcedEndingLLM(PipelineLLM):
        def run_agent(self, agent_name, prompt):
            response = super().run_agent(agent_name, prompt)
            if agent_name == "architect" and "Full Novel Blueprint" in prompt:
                prefix, rest = response.split("[STORY_FOUNDATION_JSON]\n", 1)
                encoded, suffix = rest.split("\n[/STORY_FOUNDATION_JSON]", 1)
                foundation = json.loads(encoded)
                foundation["ending_contract"] = {
                    "schema_version": 1,
                    "enforce": True,
                    "finale_window": {"start_chapter": 1, "end_chapter": 2},
                    "main_conflict": {
                        "thread_id": "plot_001",
                        "required_status": "resolved",
                        "protagonist_choice": "Mara opens the studio",
                        "consequence": "The lease becomes her responsibility",
                    },
                    "character_arcs": [
                        {
                            "character_id": "char_001",
                            "required_arc_stage": "resolution",
                            "required_outcome": "independence",
                            "required_choice": "Mara opens the studio",
                        }
                    ],
                    "plot_payoffs": [
                        {
                            "id": "payoff_001",
                            "setup_ids": ["ch1:fs1"],
                            "required_payoff": "The open door becomes her business",
                            "deadline": 2,
                            "allow_intentional_open": False,
                        }
                    ],
                    "antagonist_outcome": {"required": False},
                    "emotional_contract": {
                        "reader_emotion": "earned hope",
                        "afterglow_state": "self-directed life",
                    },
                }
                return (
                    prefix
                    + "[STORY_FOUNDATION_JSON]\n"
                    + json.dumps(foundation)
                    + "\n[/STORY_FOUNDATION_JSON]"
                    + suffix
                )
            if agent_name == "scribe" and "Chapter 2" in prompt:
                response = response.replace(
                    "status=active | milestone=Mara advances the studio opening",
                    "status=resolved | milestone=Mara opens the studio",
                )
                return response.replace(
                    "[/SCRIBE_STATE_UPDATE]",
                    """Payoff_Events: [payoff_001 | status=paid | evidence=The open door becomes her business | chapter=2]
Arc_State_Updates: [char_001 | stage=resolution | progress=100 | outcome=independence | evidence=Mara opens the studio in her own name]
Ending_Evidence: [irreversible_change=Mara assumes the lease; emotional_payoff=She chooses a self-directed life]
[/SCRIBE_STATE_UPDATE]""",
                )
            return response

    def factory(project_path):
        orchestrator = NovelOrchestrator(project_path)
        orchestrator._llm = EnforcedEndingLLM()
        return orchestrator

    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(orchestrator_factory=factory).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=2,
            target_words=60,
            approval_policy="auto",
            quality_policy="evidence_v1",
        )
    )

    assert manifest.status == "completed", manifest.error
    assert manifest.get("ending.review").status == "done"
    state = StoryState(str(project))
    assert state.plot_threads["plot_001"].status == "resolved"
    assert state.characters["char_001"].arc_stage == "resolution"
    assert state.characters["char_001"].outcome_state == "independence"
    report = json.loads(
        (project / "outputs/feedback/book_completion_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] == "pass"
    assert (project / "outputs/deliverables/book.md").is_file()


def test_evidence_run_restores_committed_tail_before_next_promotion(tmp_path: Path):
    class DriftBeforeSecondPromotionOrchestrator(NovelOrchestrator):
        def curate_chapter(self, chapter_number: int, dry_run: bool = False):
            result = super().curate_chapter(chapter_number, dry_run=dry_run)
            if chapter_number == 2:
                drifted = StoryState(str(self.project_path))
                drifted.metadata["transient_projection_drift"] = True
                drifted.save_state()
            return result

    def factory(project_path):
        orchestrator = DriftBeforeSecondPromotionOrchestrator(project_path)
        orchestrator._llm = PipelineLLM()
        return orchestrator

    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(orchestrator_factory=factory).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=2,
            target_words=60,
            approval_policy="auto",
            quality_policy="evidence_v1",
        )
    )

    assert manifest.status == "completed", manifest.error
    assert manifest.get("chapter.promote", 2).promotion_receipt_id
    assert "transient_projection_drift" not in StoryState(str(project)).metadata


def test_evidence_plan_requires_chapter_contract(tmp_path: Path):
    class MissingContractLLM(PipelineLLM):
        def run_agent(self, agent_name, prompt):
            response = super().run_agent(agent_name, prompt)
            if agent_name == "architect" and "Outline Chapter" in prompt:
                return response.split("[CHAPTER_CONTRACT]", 1)[0]
            return response

    def factory(project_path):
        orchestrator = NovelOrchestrator(project_path)
        orchestrator._llm = MissingContractLLM()
        return orchestrator

    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")

    manifest = PipelineRunner(orchestrator_factory=factory).run(
        _one_chapter_spec(
            tmp_path / "project",
            prompt,
            quality_policy="evidence_v1",
        )
    )

    assert manifest.status == "failed"
    assert "CHAPTER_CONTRACT" in manifest.error
    assert manifest.get("chapter.plan", 1).status == "failed"


def test_scribe_prompt_contains_current_chapter_contract(tmp_path: Path):
    PipelineLLM.prompts = {}
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm).run(
        _one_chapter_spec(project, prompt, quality_policy="evidence_v1")
    )

    assert manifest.status == "completed", manifest.error
    plan = manifest.get("chapter.plan", 1)
    assert plan.chapter_contract_revision_id
    persisted = json.loads(
        ArtifactStore(project).read_text(plan.chapter_contract_revision_id)
    )
    scribe_prompt = (project / "outputs/chapter_001_scribe_prompt.md").read_text(
        encoding="utf-8"
    )
    assert "## Current Chapter Contract" in scribe_prompt
    assert json.dumps(persisted, ensure_ascii=False, sort_keys=True, indent=2) in scribe_prompt


def test_evidence_policy_records_real_promotion_receipt(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm).run(
        _one_chapter_spec(project, prompt, quality_policy="evidence_v1")
    )

    assert manifest.status == "completed", manifest.error
    promotion = manifest.get("chapter.promote", 1)
    assert promotion.evaluation_report_ids
    assert promotion.promotion_receipt_id.startswith("promotion-receipt-")
    receipt = PromotionService(project).load_receipt(
        f"pipeline-{manifest.run_id}-chapter-1"
    )
    assert receipt.receipt_id == promotion.promotion_receipt_id
    assert ArtifactStore(project).get_head(1, "final").revision_id == promotion.revision_id
    assert (project / "outputs/manuscript/chapter_001_final.md").read_text(
        encoding="utf-8"
    ) == ArtifactStore(project).read_text(promotion.revision_id)


def test_resume_reconciles_committed_promotion_before_restoring_state(
    tmp_path: Path,
    monkeypatch,
):
    class SimulatedProcessCrash(BaseException):
        pass

    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    original_save_stage = runner._save_stage

    def crash_before_promotion_checkpoint(
        manifest,
        active_project,
        store,
        result,
        *,
        snapshot_state=False,
    ):
        if (
            result.phase == "chapter.promote"
            and result.status == "done"
            and result.promotion_receipt_id
        ):
            raise SimulatedProcessCrash
        return original_save_stage(
            manifest,
            active_project,
            store,
            result,
            snapshot_state=snapshot_state,
        )

    monkeypatch.setattr(runner, "_save_stage", crash_before_promotion_checkpoint)

    with pytest.raises(SimulatedProcessCrash):
        runner.run(_one_chapter_spec(
            project,
            prompt,
            quality_policy="evidence_v1",
        ))

    run_paths = list((project / "outputs/runs").glob("*/run.json"))
    assert len(run_paths) == 1
    run_id = run_paths[0].parent.name
    interrupted = runner._store(project, run_id).load()
    assert interrupted.get("chapter.promote", 1).status == "running"
    receipt = PromotionService(project).load_receipt(
        f"pipeline-{run_id}-chapter-1"
    )
    assert receipt is not None
    committed_canon_sha = canonical_canon_sha(StoryState(str(project)))
    assert committed_canon_sha == receipt.new_canon_sha

    resumed_runner = PipelineRunner(
        orchestrator_factory=_real_orchestrator_with_fake_llm
    )
    monkeypatch.setattr(
        resumed_runner,
        "_restore_state",
        lambda *_args, **_kwargs: pytest.fail(
            "resume restored a pre-promotion StoryState snapshot"
        ),
    )
    monkeypatch.setattr(
        PromotionService,
        "promote",
        lambda *_args, **_kwargs: pytest.fail(
            "resume constructed a second promotion request"
        ),
    )

    completed = resumed_runner.resume(run_id, project)

    assert completed.status == "completed", completed.error
    promotion = completed.get("chapter.promote", 1)
    assert promotion.status == "done"
    assert promotion.promotion_receipt_id == receipt.receipt_id
    assert promotion.revision_id == receipt.new_artifact_revision_id
    assert promotion.evaluation_report_ids == [receipt.evaluation_report_id]
    assert canonical_canon_sha(StoryState(str(project))) == committed_canon_sha


def test_resume_reuses_committed_chapters_after_outline_checkpoint_changes(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    manifest = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=2,
        target_words=60,
        approval_policy="auto",
        quality_policy="evidence_v1",
    ))
    assert manifest.status == "completed", manifest.error

    # Make the upstream outline checkpoint stale after both chapter
    # promotions have committed.  A resume must replay planning context only.
    outline = project / "outputs/outline.md"
    outline.write_text(outline.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    manifest.status = "paused"
    manifest.error = "simulated outline checkpoint drift"
    store = runner._store(project, manifest.run_id)
    store.save(manifest)

    PipelineLLM.calls.clear()
    resumed = PipelineRunner(
        orchestrator_factory=_real_orchestrator_with_fake_llm
    ).resume(manifest.run_id, project)

    assert resumed.status == "completed", resumed.error
    assert "scribe" not in PipelineLLM.calls
    assert "editor" not in PipelineLLM.calls
    assert all(
        resumed.get("chapter.promote", number).promotion_receipt_id
        for number in (1, 2)
    )


def test_resume_recovers_unfinished_promotion_before_restoring_state(
    tmp_path: Path,
    monkeypatch,
):
    class SimulatedProcessCrash(BaseException):
        pass

    def interrupt_before_receipt(point):
        if point == "before_receipt_commit":
            raise SimulatedProcessCrash

    real_promotion_service = PromotionService

    def interrupted_service(project):
        return real_promotion_service(
            project,
            fault_injector=interrupt_before_receipt,
        )

    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    monkeypatch.setattr(
        pipeline_runner_module,
        "PromotionService",
        interrupted_service,
    )

    with pytest.raises(SimulatedProcessCrash):
        runner.run(_one_chapter_spec(
            project,
            prompt,
            quality_policy="evidence_v1",
        ))

    run_paths = list((project / "outputs/runs").glob("*/run.json"))
    assert len(run_paths) == 1
    run_id = run_paths[0].parent.name
    key = f"pipeline-{run_id}-chapter-1"
    receipt_path = project / f"outputs/state/promotion_receipts/{key}.json"
    assert not receipt_path.exists()
    journal = json.loads(
        (project / f"outputs/state/promotion_journal/{key}.json").read_text(
            encoding="utf-8"
        )
    )["journal"]
    assert journal["state"] == "head_committed"
    committed_canon_sha = canonical_canon_sha(StoryState(str(project)))
    assert committed_canon_sha == journal["receipt"]["new_canon_sha"]

    monkeypatch.setattr(
        pipeline_runner_module,
        "PromotionService",
        real_promotion_service,
    )
    resumed_runner = PipelineRunner(
        orchestrator_factory=_real_orchestrator_with_fake_llm
    )
    monkeypatch.setattr(
        resumed_runner,
        "_restore_state",
        lambda *_args, **_kwargs: pytest.fail(
            "resume restored a pre-promotion StoryState snapshot"
        ),
    )
    monkeypatch.setattr(
        real_promotion_service,
        "promote",
        lambda *_args, **_kwargs: pytest.fail(
            "resume constructed a second promotion request"
        ),
    )

    completed = resumed_runner.resume(run_id, project)

    assert completed.status == "completed", completed.error
    receipt = real_promotion_service(project).load_receipt(key)
    assert receipt is not None
    assert completed.get("chapter.promote", 1).promotion_receipt_id == receipt.receipt_id
    assert canonical_canon_sha(StoryState(str(project))) == committed_canon_sha


def test_reconcile_historical_receipt_when_state_is_before_ledger_tail(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    manifest = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=2,
        target_words=60,
        approval_policy="auto",
        quality_policy="evidence_v1",
    ))
    assert manifest.status == "completed", manifest.error

    # Reproduce a recovery window where a committed chapter-1 receipt exists,
    # but the canonical projection has only been restored through chapter 1
    # while the ledger tail is chapter 2.
    first = manifest.get("chapter.promote", 1)
    second = manifest.get("chapter.promote", 2)
    assert first and second and first.state_snapshot_path
    runner._atomic_copy(project / first.state_snapshot_path, project / "outputs/state/story_state.json")
    first.status = "failed"
    first.error = "checkpoint write interrupted"
    first.finished_at = ""
    manifest.status = "paused"
    store = runner._store(project, manifest.run_id)
    store.save(manifest)

    reconciled = runner._reconcile_committed_promotions(manifest, project, store)

    assert reconciled == {1}
    assert manifest.get("chapter.promote", 1).status == "done"
    assert manifest.get("chapter.promote", 1).promotion_receipt_id == first.promotion_receipt_id

    # The same receipt also repairs a stage that says done but lost its final
    # projection after the checkpoint was written.
    final = project / "outputs/manuscript/chapter_001_final.md"
    final.unlink()
    assert runner._reconcile_committed_promotions(manifest, project, store) == {1}
    assert final.read_text(encoding="utf-8") == ArtifactStore(project).read_text(first.revision_id)


def test_multi_chapter_evidence_promotion_commits_all_agent_proposals(
    tmp_path: Path,
):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(
        orchestrator_factory=_real_orchestrator_with_fake_llm
    ).run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=2,
        target_words=60,
        approval_policy="auto",
        quality_policy="evidence_v1",
    ))

    assert manifest.status == "completed", manifest.error
    state = StoryState(str(project))
    for number in (1, 2):
        chapter = state.get_chapter(number)
        assert chapter.status == "complete"
        assert "Mara commits to change" in chapter.plot_advances
        assert "The studio is hers" in chapter.new_information
        assert "The studio is open" in chapter.new_information
        assert chapter.continuity_checks["status"] == "PASS"
    runtime = NovelOrchestrator(
        str(project), state_update_mode="proposal_only"
    ).build_proposal_runtime_state()
    assert runtime.characters["char_001"].last_appearance_chapter == 2
    assert runtime.plot_threads["plot_001"].last_updated_chapter == 2


def _pause_after_evidence_promotion(project: Path, prompt: Path):
    def factory(project_path):
        orchestrator = _real_orchestrator_with_fake_llm(project_path)
        original = orchestrator.run_checks

        def run_checks(number=None):
            return 1 if number is None else original(number)

        orchestrator.run_checks = run_checks
        return orchestrator

    return PipelineRunner(orchestrator_factory=factory).run(
        _one_chapter_spec(project, prompt, quality_policy="evidence_v1")
    )


@pytest.mark.parametrize("record_kind", ["receipt", "report"])
def test_paused_evidence_resume_rejects_missing_durable_record(
    tmp_path: Path, record_kind: str
):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    paused = _pause_after_evidence_promotion(project, prompt)
    promote = paused.get("chapter.promote", 1)
    assert promote.status == "done"
    if record_kind == "receipt":
        path = (
            project
            / "outputs/state/promotion_receipts"
            / f"pipeline-{paused.run_id}-chapter-1.json"
        )
    else:
        path = (
            project
            / "outputs/quality/evaluation_reports"
            / f"{promote.evaluation_report_ids[0]}.json"
        )
    path.unlink()

    resumed = runner.resume(paused.run_id, project)

    assert resumed.status == "paused"
    assert record_kind in resumed.error.lower()


@pytest.mark.parametrize("record_kind", ["receipt", "report"])
def test_paused_evidence_resume_rejects_tampered_durable_record(
    tmp_path: Path, record_kind: str
):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    paused = _pause_after_evidence_promotion(project, prompt)
    promote = paused.get("chapter.promote", 1)
    assert promote.status == "done"
    if record_kind == "receipt":
        path = (
            project
            / "outputs/state/promotion_receipts"
            / f"pipeline-{paused.run_id}-chapter-1.json"
        )
        record = json.loads(path.read_text(encoding="utf-8"))
        record["receipt"]["reason"] = "tampered"
    else:
        path = (
            project
            / "outputs/quality/evaluation_reports"
            / f"{promote.evaluation_report_ids[0]}.json"
        )
        record = json.loads(path.read_text(encoding="utf-8"))
        record["evaluator_model"] = "tampered-model"
    path.write_text(json.dumps(record), encoding="utf-8")

    resumed = runner.resume(paused.run_id, project)

    assert resumed.status == "paused"
    assert record_kind in resumed.error.lower()


@pytest.mark.parametrize("record_kind", ["receipt", "report"])
def test_completed_evidence_resume_rejects_missing_durable_record(
    tmp_path: Path, record_kind: str
):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    completed = runner.run(
        _one_chapter_spec(project, prompt, quality_policy="evidence_v1")
    )
    promote = completed.get("chapter.promote", 1)
    if record_kind == "receipt":
        path = (
            project
            / "outputs/state/promotion_receipts"
            / f"pipeline-{completed.run_id}-chapter-1.json"
        )
    else:
        path = (
            project
            / "outputs/quality/evaluation_reports"
            / f"{promote.evaluation_report_ids[0]}.json"
        )
    path.unlink()

    resumed = runner.resume(completed.run_id, project)

    assert resumed.status == "paused"
    assert record_kind in resumed.error.lower()


def test_resume_reloads_bound_canon_proposal_by_id(tmp_path: Path):
    PipelineLLM.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    paused = runner.run(_one_chapter_spec(
        project,
        prompt,
        quality_policy="evidence_v1",
        approval_policy="review_required",
    ))
    style = paused.get("chapter.style", 1)
    assert style.canon_proposal_ids
    bound_ids = list(style.canon_proposal_ids)
    before_calls = list(PipelineLLM.calls)
    raw = project / "outputs/feedback/chapter_001_style_report.md.raw"
    raw.write_text("[STYLE_STATE_UPDATE]\nMaintained_Characteristics: forged\n[/STYLE_STATE_UPDATE]", encoding="utf-8")

    resumed = runner.resume(paused.run_id, project, approval_policy="auto")

    assert resumed.status == "completed", resumed.error
    resumed_ids = resumed.get("chapter.style", 1).canon_proposal_ids
    assert resumed_ids[: len(bound_ids)] == bound_ids
    assert len(resumed_ids) == len(bound_ids) + 1
    assert PipelineLLM.calls == before_calls
    for proposal_id in resumed_ids:
        ProposalStore(project).load(proposal_id)


def test_resume_blocks_corrupt_bound_canon_proposal_without_reparsing(
    tmp_path: Path
):
    PipelineLLM.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    paused = runner.run(_one_chapter_spec(
        project,
        prompt,
        quality_policy="evidence_v1",
        approval_policy="review_required",
    ))
    style = paused.get("chapter.style", 1)
    calls_before = list(PipelineLLM.calls)
    proposal_path = (
        project
        / "outputs/state/proposals"
        / f"{style.canon_proposal_ids[0]}.json"
    )
    record = json.loads(proposal_path.read_text(encoding="utf-8"))
    record["proposal"]["delta"]["maintained_characteristics"] = ["forged"]
    proposal_path.write_text(json.dumps(record), encoding="utf-8")

    resumed = runner.resume(paused.run_id, project, approval_policy="auto")

    assert resumed.status == "paused"
    assert "Bound canon proposal" in resumed.error
    assert PipelineLLM.calls == calls_before


def test_resume_rechecks_evidence_receipt_on_completed_promotion(tmp_path: Path):
    def factory(project_path):
        orchestrator = _real_orchestrator_with_fake_llm(project_path)
        original = orchestrator.run_checks

        def run_checks(number=None):
            return 1 if number is None else original(number)

        orchestrator.run_checks = run_checks
        return orchestrator

    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=factory)
    paused = runner.run(_one_chapter_spec(
        project,
        prompt,
        quality_policy="evidence_v1",
    ))
    run_path = project / "outputs/runs" / paused.run_id / "run.json"
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    payload["stages"]["chapter.promote:1"]["promotion_receipt_id"] = ""
    run_path.write_text(json.dumps(payload), encoding="utf-8")

    resumed = runner.resume(paused.run_id, project)

    assert resumed.status == "paused"
    assert "promotion receipt" in resumed.error


def test_guardian_output_does_not_mutate_canon_before_promotion(tmp_path: Path):
    project = tmp_path / "project"
    orchestrator = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    orchestrator._llm = PipelineLLM()
    chapter = orchestrator.state.create_chapter(1)
    chapter.status = "edited"
    revised = project / "outputs/manuscript/chapter_001_revised.md"
    revised.write_text("# Chapter 1\n\nMara turned the key.\n", encoding="utf-8")
    orchestrator.state.save_state()
    before = canonical_canon_sha(StoryState(str(project)))

    orchestrator.validate_chapter(1)

    assert canonical_canon_sha(StoryState(str(project))) == before
    assert orchestrator.last_canon_proposal_ids


def test_style_output_does_not_mutate_canon_before_promotion(tmp_path: Path):
    project = tmp_path / "project"
    orchestrator = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    orchestrator._llm = PipelineLLM()
    chapter = orchestrator.state.create_chapter(1)
    chapter.status = "planned"
    revised = project / "outputs/manuscript/chapter_001_revised.md"
    revised.write_text("# Chapter 1\n\nMara turned the key.\n", encoding="utf-8")
    orchestrator.state.save_state()
    before = canonical_canon_sha(StoryState(str(project)))

    orchestrator.curate_chapter(1)

    assert canonical_canon_sha(StoryState(str(project))) == before
    assert orchestrator.last_canon_proposal_ids


def test_evidence_human_approval_records_receipt_without_legacy_copy(
    tmp_path: Path, monkeypatch
):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    paused = runner.run(_one_chapter_spec(
        project,
        prompt,
        quality_policy="evidence_v1",
        approval_policy="review_required",
    ))
    monkeypatch.setattr(
        runner,
        "_copy_candidate_to_final",
        lambda *_args, **_kwargs: pytest.fail("evidence approval used legacy copy"),
    )

    completed = runner.resume(paused.run_id, project, approve_chapter=1)

    assert completed.status == "completed", completed.error
    assert completed.get("chapter.promote", 1).promotion_receipt_id.startswith(
        "promotion-receipt-"
    )


def test_evidence_human_edit_rebinds_and_preserves_style_proposal(
    tmp_path: Path,
):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    paused = runner.run(_one_chapter_spec(
        project,
        prompt,
        quality_policy="evidence_v1",
        approval_policy="review_required",
    ))
    candidate = project / "outputs/manuscript/chapter_001_candidate_final.md"
    candidate.write_text(
        candidate.read_text(encoding="utf-8") + "\nHuman-approved closing line.\n",
        encoding="utf-8",
    )

    completed = runner.resume(paused.run_id, project, approve_chapter=1)

    assert completed.status == "completed", completed.error
    chapter = StoryState(str(project)).get_chapter(1)
    assert chapter.quality_scores["style_consistency_score"] == 9.0
    assert chapter.quality_scores["style_genre_adherence"] == 9.0
    assert chapter.quality_scores["style_voice_strength"] == 9.0
    style = completed.get("chapter.style", 1)
    rebound = ProposalStore(project).load(style.canon_proposal_ids[0])
    assert rebound.source_artifact_sha == ArtifactStore(project).get_revision(
        style.revision_id
    ).sha256
    assert rebound.to_dict()["delta"]["consistency_score"] == "9/10"


def test_evidence_human_approval_records_proposal_failure_in_manifest(
    tmp_path: Path,
):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    paused = runner.run(_one_chapter_spec(
        project,
        prompt,
        quality_policy="evidence_v1",
        approval_policy="review_required",
    ))
    style = paused.get("chapter.style", 1)
    proposal_path = (
        project
        / "outputs/state/proposals"
        / f"{style.canon_proposal_ids[0]}.json"
    )
    record = json.loads(proposal_path.read_text(encoding="utf-8"))
    record["proposal"]["delta"]["maintained_characteristics"] = ["forged"]
    proposal_path.write_text(json.dumps(record), encoding="utf-8")

    resumed = runner.resume(paused.run_id, project, approve_chapter=1)

    assert resumed.status in {"paused", "failed"}
    assert "proposal" in resumed.error.lower()
    events = [
        json.loads(line)
        for line in (
            project / "outputs/runs" / paused.run_id / "events.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["event"] == "chapter.human_approval_failed"
    assert events[-1]["error"] == resumed.error


def test_evidence_completed_run_repairs_projection_from_artifact_head(
    tmp_path: Path, monkeypatch
):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# One Prompt Book\n\nMara chooses a new life.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_real_orchestrator_with_fake_llm)
    manifest = runner.run(_one_chapter_spec(
        project,
        prompt,
        quality_policy="evidence_v1",
    ))
    final = project / "outputs/manuscript/chapter_001_final.md"
    final.unlink()
    monkeypatch.setattr(
        runner,
        "_copy_candidate_to_final",
        lambda *_args, **_kwargs: pytest.fail("evidence repair used legacy copy"),
    )

    repaired = runner.resume(manifest.run_id, project)

    assert repaired.status == "completed"
    head = ArtifactStore(project).get_head(1, "final")
    assert final.read_text(encoding="utf-8") == ArtifactStore(project).read_text(
        head.revision_id
    )
