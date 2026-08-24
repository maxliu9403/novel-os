import hashlib
import json
from pathlib import Path

import pytest

from artifacts import ArtifactStore
from pipeline_models import RunSpec, StageResult
from pipeline_runner import PipelineError, PipelineRunner
from llm_client import LLMError
from state_manager import StoryState


class FakeOrchestrator:
    calls = []
    fail_validation = False

    def __init__(self, project_path: str):
        self.project = Path(project_path)
        self.state = StoryState(project_path)
        self.outputs = self.project / "outputs"
        self.manuscript = self.outputs / "manuscript"
        self.feedback = self.outputs / "feedback"
        self.manuscript.mkdir(parents=True, exist_ok=True)
        self.feedback.mkdir(parents=True, exist_ok=True)

    def plan_outline(self, chapters, words, dry_run=False):
        type(self).calls.append(("outline", chapters))
        (self.outputs / "outline.json").write_text(
            json.dumps({"chapter_summaries": [{"number": n} for n in range(1, chapters + 1)]}),
            encoding="utf-8",
        )
        (self.outputs / "outline.md").write_text("# Full Outline\n", encoding="utf-8")
        (self.outputs / "input").mkdir(parents=True, exist_ok=True)
        (self.outputs / "input/foundation.json").write_text("{}\n", encoding="utf-8")

    def plan_chapter(self, number, summary="", pov="", dry_run=False):
        type(self).calls.append(("plan", number))
        chapter = self.state.get_chapter(number) or self.state.create_chapter(number)
        chapter.title = f"Chapter {number}"
        chapter.status = "planned"
        chapter.target_word_count = 20
        self.state.save_state()
        (self.outputs / f"chapter_{number:03d}_outline.md").write_text(
            f"# Chapter {number}\n", encoding="utf-8"
        )

    def write_chapter(self, number, dry_run=False):
        type(self).calls.append(("write", number))
        chapter = self.state.get_chapter(number)
        chapter.status = "drafted"
        self.state.save_state()
        (self.manuscript / f"chapter_{number:03d}_draft.md").write_text(
            f"Draft {number}\n", encoding="utf-8"
        )

    def run_checks(self, number=None):
        type(self).calls.append(("check", number))
        return 0

    def edit_chapter(self, number, mode="line", dry_run=False):
        type(self).calls.append(("edit", number))
        chapter = self.state.get_chapter(number)
        chapter.status = "edited"
        self.state.save_state()
        (self.manuscript / f"chapter_{number:03d}_revised.md").write_text(
            f"Revised {number}\n", encoding="utf-8"
        )

    def validate_chapter(self, number, dry_run=False):
        type(self).calls.append(("validate", number))
        chapter = self.state.get_chapter(number)
        chapter.status = "validated"
        chapter.continuity_checks["status"] = "FAIL" if self.fail_validation else "PASS"
        self.state.save_state()
        (self.feedback / f"chapter_{number:03d}_continuity_report.md").write_text(
            f"Status: {chapter.continuity_checks['status']}\n", encoding="utf-8"
        )

    def curate_chapter(self, number, dry_run=False):
        type(self).calls.append(("style", number))
        (self.feedback / f"chapter_{number:03d}_style_report.md").write_text(
            "Style PASS\n", encoding="utf-8"
        )
        (self.manuscript / f"chapter_{number:03d}_candidate_final.md").write_text(
            f"Final {number}\n", encoding="utf-8"
        )

    def runtime_provenance_for(self, agent_name):
        role = {
            "architect": "architect",
            "scribe": "writer",
            "editor": "editor",
            "continuity_guardian": "guardian",
            "style_curator": "style",
        }[agent_name]
        return f"provider-{role}", f"model-{role}"


def _factory(project_path):
    return FakeOrchestrator(project_path)


def test_runner_completes_two_chapter_book(tmp_path: Path):
    FakeOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Book\n\nA story.", encoding="utf-8")
    spec = RunSpec(
        project_path=str(tmp_path / "project"),
        prompt_path=str(prompt),
        num_chapters=2,
        target_words=40,
        approval_policy="auto",
    )

    manifest = PipelineRunner(orchestrator_factory=_factory).run(spec)

    assert manifest.status == "completed"
    assert manifest.get("chapter.promote", 2).status == "done"
    assert "approval_policy=auto" in manifest.get("chapter.promote", 2).decisions[0]
    assert (tmp_path / "project/outputs/manuscript/chapter_001_final.md").exists()
    assert (tmp_path / "project/outputs/deliverables/book.md").exists()
    assert (tmp_path / "project/outputs/runs" / manifest.run_id / "events.jsonl").exists()
    assert (tmp_path / "project/outputs/runs" / manifest.run_id / "stages/chapter-write-chapter-001.json").exists()
    assert manifest.get("chapter.write", 1).state_snapshot_path
    assert ("write", 1) in FakeOrchestrator.calls
    assert ("write", 2) in FakeOrchestrator.calls


def test_agent_stages_persist_actual_role_provenance(tmp_path: Path):
    FakeOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")

    manifest = PipelineRunner(orchestrator_factory=_factory).run(RunSpec(
        project_path=str(tmp_path / "project"),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))

    assert (manifest.get("chapter.write", 1).provider, manifest.get("chapter.write", 1).model) == (
        "provider-writer",
        "model-writer",
    )
    assert (
        manifest.get("chapter.validate", 1).provider,
        manifest.get("chapter.validate", 1).model,
    ) == ("provider-guardian", "model-guardian")
    assert (manifest.get("chapter.style", 1).provider, manifest.get("chapter.style", 1).model) == (
        "provider-style",
        "model-style",
    )
    assert manifest.get("chapter.check.pre", 1).provider == ""
    assert manifest.get("compile").model == ""


def test_writer_guardian_and_style_record_actual_role_model(tmp_path: Path):
    FakeOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")

    manifest = PipelineRunner(orchestrator_factory=_factory).run(RunSpec(
        project_path=str(tmp_path / "project"),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))

    assert manifest.get("chapter.write", 1).model == "model-writer"
    assert manifest.get("chapter.validate", 1).model == "model-guardian"
    assert manifest.get("chapter.style", 1).model == "model-style"


def test_intake_persists_story_contract_revision(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "# North Door\n\nGenre: Suspense\nAudience: Adult\n\nMara must choose whether to open it.",
        encoding="utf-8",
    )
    project = tmp_path / "project"

    manifest = PipelineRunner(orchestrator_factory=_factory).run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))

    intake = manifest.get("intake")
    assert intake.story_contract_revision_id
    revision = ArtifactStore(project).get_revision(intake.story_contract_revision_id)
    assert revision.kind == "story_contract"
    assert revision.chapter == 0
    assert ArtifactStore(project).get_head(0, "story_contract").revision_id == revision.revision_id
    state = StoryState(str(project))
    assert state.metadata["story_contract_revision_id"] == revision.revision_id
    assert state.metadata["story_contract_id"].startswith("contract:")


def test_resume_reuses_valid_revision_without_rewriting(tmp_path: Path):
    FakeOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    paused = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="review_required",
    ))
    style = paused.get("chapter.style", 1)
    revisions_path = project / "outputs/artifacts/revisions.jsonl"
    before_records = revisions_path.read_text(encoding="utf-8").splitlines()

    completed = runner.resume(paused.run_id, project, approval_policy="auto")

    assert completed.status == "completed", completed.error
    assert completed.get("chapter.style", 1).revision_id == style.revision_id
    assert revisions_path.read_text(encoding="utf-8").splitlines() == before_records
    assert FakeOrchestrator.calls.count(("style", 1)) == 1


def test_quality_gate_blocks_candidate_without_promotion_receipt(tmp_path: Path):
    result = StageResult(
        phase="chapter.promote",
        chapter=1,
        status="done",
        revision_id="a" * 64,
    )

    assert PipelineRunner._quality_gate_complete("evidence_v1", result) is False


def test_evidence_policy_requires_promotion_receipt(tmp_path: Path):
    result = StageResult(
        phase="chapter.promote",
        chapter=1,
        status="done",
        revision_id="a" * 64,
        evaluation_report_ids=["report-" + "b" * 64],
    )

    with pytest.raises(PipelineError, match="promotion receipt"):
        PipelineRunner._require_quality_gate("evidence_v1", result)


def test_guardian_fail_blocks_auto_promotion(tmp_path: Path):
    FakeOrchestrator.calls = []
    FakeOrchestrator.fail_validation = True
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    spec = RunSpec(
        project_path=str(tmp_path / "project"),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    )

    manifest = PipelineRunner(orchestrator_factory=_factory).run(spec)

    assert manifest.status == "paused"
    assert manifest.get("chapter.validate", 1).status == "blocked"
    assert not (tmp_path / "project/outputs/manuscript/chapter_001_final.md").exists()
    FakeOrchestrator.fail_validation = False


def test_review_pause_resumes_with_auto_approval_without_rewriting_chapter(tmp_path: Path):
    FakeOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    spec = RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="review_required",
    )
    runner = PipelineRunner(orchestrator_factory=_factory)
    paused = runner.run(spec)
    calls_before_resume = list(FakeOrchestrator.calls)
    assert not (project / "outputs/deliverables/book.md").exists()

    completed = runner.resume(paused.run_id, project, approval_policy="auto")

    assert completed.status == "completed"
    assert FakeOrchestrator.calls.count(("write", 1)) == calls_before_resume.count(("write", 1))
    assert (project / "outputs/manuscript/chapter_001_final.md").exists()


def test_review_pause_can_approve_one_chapter_and_keep_review_policy(tmp_path: Path):
    FakeOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    paused = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="review_required",
    ))
    candidate = project / "outputs/manuscript/chapter_001_candidate_final.md"
    candidate.write_text("Human edited final.\n", encoding="utf-8")

    completed = runner.resume(paused.run_id, project, approve_chapter=1)

    assert completed.status == "completed"
    assert completed.spec.approval_policy == "review_required"
    promote = completed.get("chapter.promote", 1)
    assert promote.status == "done"
    assert "human approval" in promote.decisions[0].lower()
    identity = json.dumps(
        {
            "run_id": completed.run_id,
            "chapter": 1,
            "revision_id": promote.revision_id,
            "artifact_hashes": promote.artifact_hashes,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert promote.promotion_receipt_id == (
        "legacy-receipt-" + hashlib.sha256(identity).hexdigest()
    )
    assert (project / "outputs/manuscript/chapter_001_final.md").read_text(
        encoding="utf-8"
    ) == "Human edited final.\n"
    assert FakeOrchestrator.calls.count(("style", 1)) == 1


def test_resume_reexecutes_stage_when_checkpoint_hash_changed(tmp_path: Path):
    FakeOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    paused = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="review_required",
    ))
    outline = project / "outputs/chapter_001_outline.md"
    outline.write_text("tampered\n", encoding="utf-8")
    before = FakeOrchestrator.calls.count(("plan", 1))

    runner.resume(paused.run_id, project, approval_policy="auto")

    assert FakeOrchestrator.calls.count(("plan", 1)) == before + 1
    assert FakeOrchestrator.calls.count(("write", 1)) == 2


def test_retryable_llm_error_retries_stage_and_records_attempt(tmp_path: Path):
    class FlakyOrchestrator(FakeOrchestrator):
        write_attempts = 0

        def write_chapter(self, number, dry_run=False):
            type(self).write_attempts += 1
            if type(self).write_attempts == 1:
                raise LLMError("temporary upstream timeout")
            return super().write_chapter(number, dry_run=dry_run)

    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    manifest = PipelineRunner(orchestrator_factory=FlakyOrchestrator).run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
        max_retries=1,
        retry_backoff_seconds=0,
    ))

    assert manifest.status == "completed", manifest.error
    assert manifest.get("chapter.write", 1).attempt == 2


def test_failed_agent_stage_persists_actual_role_provenance(tmp_path: Path):
    class FailingWriterOrchestrator(FakeOrchestrator):
        def write_chapter(self, number, dry_run=False):
            raise LLMError("permanent upstream failure")

    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(orchestrator_factory=FailingWriterOrchestrator).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            max_retries=0,
        )
    )

    failed = manifest.get("chapter.write", 1)
    assert failed.status == "failed"
    assert (failed.provider, failed.model) == (
        "provider-writer",
        "model-writer",
    )


def test_retryable_agent_attempt_persists_actual_role_provenance(tmp_path: Path):
    class FlakyWriterOrchestrator(FakeOrchestrator):
        write_attempts = 0

        def write_chapter(self, number, dry_run=False):
            type(self).write_attempts += 1
            if type(self).write_attempts == 1:
                raise LLMError("temporary upstream timeout")
            return super().write_chapter(number, dry_run=dry_run)

    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=FlakyWriterOrchestrator)
    saved_results = []
    original_save_stage = runner._save_stage

    def capture_save(*args, **kwargs):
        result = args[3]
        original_save_stage(*args, **kwargs)
        saved_results.append(StageResult.from_dict(result.to_dict()))

    runner._save_stage = capture_save

    manifest = runner.run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            max_retries=1,
            retry_backoff_seconds=0,
        )
    )

    retryable = next(
        result
        for result in saved_results
        if result.phase == "chapter.write" and result.status == "retryable"
    )
    assert manifest.status == "completed"
    assert (retryable.provider, retryable.model) == (
        "provider-writer",
        "model-writer",
    )


def test_provenance_resolution_failure_persists_the_agent_stage_error(tmp_path: Path):
    class BrokenProvenanceOrchestrator(FakeOrchestrator):
        def runtime_provenance_for(self, agent_name):
            raise LLMError(f"invalid model configuration for {agent_name}")

    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(orchestrator_factory=BrokenProvenanceOrchestrator).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            max_retries=0,
        )
    )

    failed = manifest.get("outline")
    assert manifest.status == "failed"
    assert failed is not None
    assert failed.status == "failed"
    assert failed.provider == ""
    assert failed.model == ""
    assert "invalid model configuration for architect" in failed.error


def test_retry_validation_preserves_manual_revised_edit(tmp_path: Path):
    FakeOrchestrator.calls = []
    FakeOrchestrator.fail_validation = True
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    paused = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))
    revised = project / "outputs/manuscript/chapter_001_revised.md"
    revised.write_text("Human continuity fix.\n", encoding="utf-8")
    edits_before = FakeOrchestrator.calls.count(("edit", 1))
    checks_before = FakeOrchestrator.calls.count(("check", 1))
    FakeOrchestrator.fail_validation = False

    completed = runner.retry(
        paused.run_id,
        project_path=project,
        phase="chapter.validate",
        chapter=1,
    )

    assert completed.status == "completed", completed.error
    assert FakeOrchestrator.calls.count(("edit", 1)) == edits_before
    assert FakeOrchestrator.calls.count(("check", 1)) == checks_before + 1
    FakeOrchestrator.fail_validation = False


def test_retry_precheck_adopts_current_story_state_at_write_checkpoint(tmp_path: Path):
    class StateGateOrchestrator(FakeOrchestrator):
        def run_checks(self, number=None):
            type(self).calls.append(("check", number))
            return 0 if self.state.metadata.get("continuity_repaired") else 1

    StateGateOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=StateGateOrchestrator)
    paused = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))
    assert paused.status == "paused"
    assert paused.get("chapter.check.pre", 1).status == "blocked"

    repaired = StoryState(str(project))
    repaired.metadata["continuity_repaired"] = True
    repaired.save_state()

    completed = runner.retry(
        paused.run_id,
        project_path=project,
        phase="chapter.check.pre",
        chapter=1,
    )

    assert completed.status == "completed", completed.error
    assert StoryState(str(project)).metadata["continuity_repaired"] is True
    assert StateGateOrchestrator.calls.count(("write", 1)) == 1


def test_retry_precheck_rebinds_manually_edited_draft_revision(tmp_path: Path):
    class DraftGateOrchestrator(FakeOrchestrator):
        def run_checks(self, number=None):
            type(self).calls.append(("check", number))
            if number is None:
                return 0
            draft = self.manuscript / f"chapter_{number:03d}_draft.md"
            return 0 if "Human continuity fix" in draft.read_text(encoding="utf-8") else 1

    DraftGateOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=DraftGateOrchestrator)
    paused = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))
    assert paused.status == "paused"
    assert paused.get("chapter.check.pre", 1).status == "blocked"

    draft = project / "outputs/manuscript/chapter_001_draft.md"
    draft.write_text("Human continuity fix.\n", encoding="utf-8")

    completed = runner.retry(
        paused.run_id,
        project_path=project,
        phase="chapter.check.pre",
        chapter=1,
    )

    assert completed.status == "completed", completed.error
    write = completed.get("chapter.write", 1)
    assert ArtifactStore(project).read_text(write.revision_id) == "Human continuity fix.\n"
    assert DraftGateOrchestrator.calls.count(("write", 1)) == 1


def test_retry_book_check_adopts_current_story_state_at_last_promote(tmp_path: Path):
    class BookCheckStateGateOrchestrator(FakeOrchestrator):
        def run_checks(self, number=None):
            type(self).calls.append(("check", number))
            if number is None:
                return 0 if self.state.metadata.get("continuity_repaired") else 1
            return 0

    BookCheckStateGateOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=BookCheckStateGateOrchestrator)
    paused = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))
    assert paused.status == "paused"
    assert paused.get("book.check").status == "blocked"

    repaired = StoryState(str(project))
    repaired.metadata["continuity_repaired"] = True
    repaired.save_state()

    completed = runner.retry(
        paused.run_id,
        project_path=project,
        phase="book.check",
    )

    assert completed.status == "completed", completed.error
    assert StoryState(str(project)).metadata["continuity_repaired"] is True
    assert BookCheckStateGateOrchestrator.calls.count(("write", 1)) == 1


def test_checkpoint_rejects_empty_artifact_even_when_hash_matches(tmp_path: Path):
    project = tmp_path / "project"
    artifact = project / "outputs/manuscript/chapter_001_final.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("", encoding="utf-8")
    result = StageResult(
        phase="chapter.promote",
        chapter=1,
        status="done",
        artifact_paths=["outputs/manuscript/chapter_001_final.md"],
        artifact_hashes={
            "outputs/manuscript/chapter_001_final.md": PipelineRunner._sha256(artifact),
        },
    )

    assert PipelineRunner._checkpoint_valid(project, result) is False


def test_promote_rejects_empty_candidate_final(tmp_path: Path):
    project = tmp_path / "project"
    candidate = project / "outputs/manuscript/chapter_001_candidate_final.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("", encoding="utf-8")

    with pytest.raises(PipelineError, match="empty"):
        PipelineRunner(project)._promote(project, 1, "auto")

    assert not (project / "outputs/manuscript/chapter_001_final.md").exists()


def test_compile_rejects_empty_final_instead_of_skipping_chapter(tmp_path: Path):
    FakeOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    manifest = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))
    (project / "outputs/manuscript/chapter_001_final.md").write_text("", encoding="utf-8")

    with pytest.raises(PipelineError, match="empty"):
        runner._compile_book(manifest, project)


def test_resume_completed_run_repairs_final_from_trusted_candidate_without_agents(tmp_path: Path):
    FakeOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    manifest = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))
    calls_before = list(FakeOrchestrator.calls)
    candidate = project / "outputs/manuscript/chapter_001_candidate_final.md"
    final = project / "outputs/manuscript/chapter_001_final.md"
    final.write_text("", encoding="utf-8")

    repaired = runner.resume(manifest.run_id, project)

    assert repaired.status == "completed"
    assert final.read_bytes() == candidate.read_bytes()
    assert FakeOrchestrator.calls == calls_before
    assert "Final 1" in (project / "outputs/deliverables/book.md").read_text(encoding="utf-8")
    assert any("repaired" in item.lower() for item in repaired.get("compile").decisions)


def test_run_cli_persists_quality_policy(tmp_path: Path):
    from orchestrator import main

    prompt = tmp_path / "prompt.md"
    prompt.write_text("# CLI Book\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"

    exit_code = main([
        "run",
        "--project",
        str(project),
        "--prompt",
        str(prompt),
        "--chapters",
        "1",
        "--words",
        "20",
        "--quality-policy",
        "evidence_v1",
        "--dry-run",
    ])

    run_files = list((project / "outputs/runs").glob("*/run.json"))
    assert exit_code == 0
    assert len(run_files) == 1
    assert json.loads(run_files[0].read_text(encoding="utf-8"))["spec"][
        "quality_policy"
    ] == "evidence_v1"
