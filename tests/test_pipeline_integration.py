import json
from pathlib import Path

import pytest

from artifacts import ArtifactStore
from canon_ledger import canonical_canon_sha
from orchestrator import NovelOrchestrator
from pipeline_models import RunSpec
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
