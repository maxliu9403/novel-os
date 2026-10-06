import hashlib
import io
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

if os.name == "posix":
    import fcntl

from artifacts import ArtifactStore
from commercial_fixtures import (
    commercial_story_fixture_variant,
    chapter_contract_v2_payload,
    chapter_contract_v2,
    span,
    high_overlap_candidate,
    high_overlap_reference,
    legacy_chapter_contract_payload,
)
from commercial_quality import (
    free_trial_beats_for_chapter,
    review_commercial_chapter,
    write_commercial_report,
)
from canon import CanonDeltaProposal
from commercial_story import commercial_story_block
from pipeline_models import RunManifest, RunSpec, StageResult
from pipeline_runner import PipelineError, PipelineRunner
from llm_client import LLMError
from project_identity import ensure_project_instance_id
from proposals import ProposalStore
from state_manager import StoryState
from story_originality import StoryFingerprint


class FakeOrchestrator:
    calls = []
    fail_validation = False
    publication_model_calls = []
    publication_writer_response = None

    def __init__(self, project_path: str):
        self.project = Path(project_path)
        self.state = StoryState(project_path)
        self.outputs = self.project / "outputs"
        self.manuscript = self.outputs / "manuscript"
        self.feedback = self.outputs / "feedback"
        self.manuscript.mkdir(parents=True, exist_ok=True)
        self.feedback.mkdir(parents=True, exist_ok=True)
        self._publication_llms = {}

    def plan_outline(self, chapters, words, dry_run=False):
        type(self).calls.append(("outline", chapters))
        (self.outputs / "outline.json").write_text(
            json.dumps({"chapter_summaries": [{"number": n} for n in range(1, chapters + 1)]}),
            encoding="utf-8",
        )
        (self.outputs / "outline.md").write_text("# Full Outline\n", encoding="utf-8")
        (self.outputs / "input").mkdir(parents=True, exist_ok=True)
        foundation = {
            "title": "Test Book",
            "premise": "A test protagonist chooses change.",
            "themes": [],
            "setting": {},
            "characters": [
                {"id": "char_001", "name": "Test Protagonist", "role": "protagonist"}
            ],
            "plot_threads": [
                {
                    "id": "plot_001",
                    "name": "Test Conflict",
                    "description": "The protagonist must choose.",
                    "type": "main",
                }
            ],
            "style": {},
            "chapters": [
                {"number": number, "title": f"Chapter {number}"}
                for number in range(1, chapters + 1)
            ],
        }
        (self.outputs / "input/foundation.json").write_text(
            json.dumps(foundation), encoding="utf-8"
        )

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
            (
                f"# Chapter {number}\n\n"
                f"Final {number}: Mara keeps the neighborhood studio open while rent pressure "
                f"rises in chapter {number}.\n"
            ),
            encoding="utf-8",
        )

    def llm_for(self, agent_name):
        if agent_name not in self._publication_llms:
            self._publication_llms[agent_name] = _PublicationCompletionClient(
                type(self), agent_name
            )
        return self._publication_llms[agent_name]

    def runtime_provenance_for(self, agent_name):
        role = {
            "architect": "architect",
            "scribe": "writer",
            "editor": "editor",
            "continuity_guardian": "guardian",
            "style_curator": "style",
        }[agent_name]
        return f"provider-{role}", f"model-{role}"


class _PublicationCompletionClient:
    def __init__(self, owner, role):
        self.owner = owner
        self.role = role
        self.provider = f"provider-{role}"
        self.model = f"model-{role}"

    def complete(self, *, system, user):
        self.owner.publication_model_calls.append((self.role, system.splitlines()[0]))
        if system.startswith("whole-book-conflict.v3"):
            source = json.loads(user.rsplit("as canonical JSON:\n", 1)[1])
            chapters = source["chapters"]
            by_number = {item["number"]: item for item in chapters}
            last = max(by_number)
            middle = 1 if last <= 2 else (last + 1) // 2

            def quote(number):
                return next(
                    line.strip()
                    for line in reversed(by_number[number]["text"].splitlines())
                    if line.strip()
                )

            return json.dumps({
                "protagonist": "Mara Vale",
                "goal": "Keep her neighborhood studio open",
                "opposition": "Escalating rent pressure",
                "stakes": "Her savings, self-trust, and community space",
                "escalation": "Each chapter makes the studio harder to preserve",
                "unresolved_choice": "How much Mara will risk to keep the door open",
                "evidence": {
                    "opening": [{"chapter": 1, "source_quote": quote(1)}],
                    "middle": [{"chapter": middle, "source_quote": quote(middle)}],
                    "late": [{"chapter": last, "source_quote": quote(last)}],
                },
            })
        if system.startswith("publication-copy-writer.v2"):
            if self.owner.publication_writer_response is not None:
                return self.owner.publication_writer_response
            return json.dumps({
                "reader_heading": "Before the Story",
                "hook_lead": (
                    "Mara must defend her neighborhood studio before rising rent "
                    "destroys its future."
                ),
                "spoiler_free_blurb": (
                    "Mara Vale has staked her savings and fragile confidence on opening "
                    "a neighborhood studio, but the lease that promised independence now "
                    "gives a powerful landlord leverage over every decision. Each new "
                    "demand threatens the space, the people beginning to rely on it, and "
                    "the self-trust she has only started to rebuild. Walking away would "
                    "protect what little money remains, yet surrendering the keys would "
                    "confirm every fear that kept her waiting. Staying means gathering "
                    "allies, challenging rules written to favor someone richer, and "
                    "risking public failure before opening day. As pressure tightens, "
                    "Mara must decide whether a secure retreat matters more than the "
                    "uncertain community taking shape around her. The studio can become "
                    "proof that her new life is real, but only if she chooses what she is "
                    "prepared to sacrifice to keep its door open."
                ),
            })
        if "before any\nreader-facing copy" in system:
            return json.dumps({
                "status": "pass",
                "checks": {
                    "whole_book_core_conflict": True,
                    "source_supported": True,
                    "opening_middle_late_coherent": True,
                    "spoiler_free": True,
                },
                "findings": [],
            })
        return json.dumps({
            "status": "pass",
            "checks": {
                "whole_book_core_conflict": True,
                "hook_core_conflict": True,
                "blurb_core_conflict": True,
                "protagonist_stakes": True,
                "spoiler_free": True,
                "source_supported": True,
            },
            "reader_pull": {
                "status": "pass",
                "checks": {
                    "first_glance_clarity": True,
                    "concrete_emotional_stakes": True,
                    "escalating_pressure": True,
                    "protagonist_agency": True,
                    "open_loop": True,
                    "truthful_genre_promise": True,
                },
            },
            "claim_evidence": [],
            "findings": [],
        })


def _factory(project_path):
    return FakeOrchestrator(project_path)


def _commercial_factory(contract):
    class CommercialFakeOrchestrator(FakeOrchestrator):
        def plan_outline(self, chapters, words, dry_run=False):
            super().plan_outline(chapters, words, dry_run=dry_run)
            foundation_path = self.outputs / "input/foundation.json"
            foundation = json.loads(foundation_path.read_text(encoding="utf-8"))
            foundation["commercial_story_contract"] = contract.to_dict()
            foundation["commercial_story_contract_id"] = contract.contract_id
            foundation_path.write_text(
                json.dumps(foundation, sort_keys=True) + "\n", encoding="utf-8"
            )

    return CommercialFakeOrchestrator


def run_with_commercial_guardian_failures(failure_count: int):
    """Run a one-chapter evidence pipeline with deterministic Guardian failures."""
    contract = commercial_story_fixture_variant()
    base = _commercial_factory(contract)

    class CommercialRepairOrchestrator(base):
        commercial_attempts = 0

        def _proposal(self, number, agent_name, relative):
            source = self.project / relative
            proposal = ProposalStore(self.project).save(CanonDeltaProposal(
                chapter=number,
                agent_name=agent_name,
                source_artifact_sha=hashlib.sha256(source.read_bytes()).hexdigest(),
                delta={"key_events": [f"{agent_name} completed chapter {number}" ]},
            ))
            self.last_canon_proposal_ids = [proposal.proposal_id]

        def plan_chapter(self, number, summary="", pov="", dry_run=False):
            super().plan_chapter(number, summary, pov, dry_run=dry_run)
            outline = self.outputs / f"chapter_{number:03d}_outline.md"
            payload = chapter_contract_v2_payload(chapter=number)
            with outline.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n[CHAPTER_CONTRACT]\n" + json.dumps(payload)
                    + "\n[/CHAPTER_CONTRACT]\n"
                )
            self._proposal(number, "architect", f"outputs/chapter_{number:03d}_outline.md")

        def write_chapter(self, number, dry_run=False):
            super().write_chapter(number, dry_run=dry_run)
            self._proposal(number, "scribe", f"outputs/manuscript/chapter_{number:03d}_draft.md")

        def edit_chapter(self, number, mode="line", dry_run=False):
            super().edit_chapter(number, mode, dry_run=dry_run)
            self._proposal(number, "editor", f"outputs/manuscript/chapter_{number:03d}_revised.md")

        def validate_chapter(self, number, dry_run=False):
            super().validate_chapter(number, dry_run=dry_run)
            self._proposal(number, "continuity_guardian", f"outputs/feedback/chapter_{number:03d}_continuity_report.md")

        def curate_chapter(self, number, dry_run=False):
            super().curate_chapter(number, dry_run=dry_run)
            self._proposal(number, "style_curator", f"outputs/manuscript/chapter_{number:03d}_candidate_final.md")

        def review_commercial_chapter(self, number, dry_run=False):
            type(self).commercial_attempts += 1
            candidate = self.manuscript / f"chapter_{number:03d}_candidate_final.md"
            text = candidate.read_text(encoding="utf-8")
            quote = next(line for line in text.splitlines() if line.strip())
            evidence = span(text, quote)
            payload = {
                "agency": evidence,
                "resource_change": evidence,
                "local_payoff": evidence,
                "ending_hook": evidence,
                "reader_jobs": {"recognition": evidence, "anger": evidence},
                "belonging_anchors": {},
                "free_trial_beats": [],
                "child_voice": {"status": "not_applicable", "quote": None, "start": None, "end": None},
                "institutional_plausibility": evidence,
                "findings": [] if type(self).commercial_attempts > failure_count else [{
                    "category": "commercial_delivery",
                    "severity": "critical",
                    "message": "The candidate does not deliver the required turn.",
                    "suggested_action": "Make the protagonist cause the local turn.",
                    "evidence": [evidence],
                }],
            }
            artifacts = ArtifactStore(self.project)
            chapter_head = artifacts.get_head(number, "chapter_contract")
            story_head = artifacts.get_head(0, "story_contract")
            chapter_contract = chapter_contract_v2(**json.loads(artifacts.read_text(chapter_head.revision_id)))
            report = review_commercial_chapter(
                text,
                chapter_contract,
                payload,
                (),
                story_contract_revision_id=story_head.revision_id,
                chapter_contract_revision_id=chapter_head.revision_id,
            )
            write_commercial_report(self.project, report)
            if report.reader_value_update is not None:
                proposal = ProposalStore(self.project).save(CanonDeltaProposal(
                    chapter=number,
                    agent_name="continuity_guardian",
                    source_artifact_sha=report.artifact_sha256,
                    delta={"reader_value_updates": [dict(report.reader_value_update)]},
                ))
                self.last_canon_proposal_ids = [proposal.proposal_id]
            return report

        def repair_commercial_chapter(self, number, report, attempt, dry_run=False):
            candidate = self.manuscript / f"chapter_{number:03d}_candidate_final.md"
            candidate.write_text(
                candidate.read_text(encoding="utf-8") + f"\nCommercial repair {attempt} changes the turn.\n",
                encoding="utf-8",
            )
            self._proposal(number, "style_curator", f"outputs/manuscript/chapter_{number:03d}_candidate_final.md")

    CommercialRepairOrchestrator.commercial_attempts = 0
    with tempfile.TemporaryDirectory() as root:
        root_path = Path(root)
        prompt = root_path / "prompt.md"
        prompt.write_text(
            "# Commercial test\n\nA story.\n\n" + commercial_story_block(contract),
            encoding="utf-8",
        )
        return PipelineRunner(orchestrator_factory=CommercialRepairOrchestrator).run(
            RunSpec(
                project_path=str(root_path / "project"),
                prompt_path=str(prompt),
                num_chapters=1,
                target_words=20,
                approval_policy="auto",
                quality_policy="evidence_v1",
                max_quality_repairs=2,
                output_formats=("markdown",),
            )
        )


def test_pipeline_repairs_commercial_candidate_at_most_twice():
    manifest = run_with_commercial_guardian_failures(2)

    assert manifest.get("chapter.commercial.repair.1", 1).status == "done"
    assert manifest.get("chapter.commercial.repair.2", 1).status == "done"
    assert manifest.get("chapter.promote", 1).status == "done"


def test_latest_candidate_stage_prefers_new_resume_cycle_over_higher_old_repair_number():
    manifest = RunManifest.new(
        RunSpec(project_path="/tmp/project", prompt_path="/tmp/prompt.md")
    )
    manifest.record(
        StageResult(
            phase="chapter.style",
            chapter=1,
            status="done",
            revision_id="style-revision",
            finished_at="2026-09-01T16:24:37+00:00",
        )
    )
    manifest.record(
        StageResult(
            phase="chapter.commercial.repair.2",
            chapter=1,
            status="done",
            revision_id="old-repair-2",
            finished_at="2026-09-01T16:02:04+00:00",
        )
    )
    manifest.record(
        StageResult(
            phase="chapter.commercial.repair.1",
            chapter=1,
            status="done",
            revision_id="new-repair-1",
            finished_at="2026-09-01T16:30:07+00:00",
        )
    )

    latest = PipelineRunner._latest_candidate_stage(manifest, 1)

    assert latest is not None
    assert latest.revision_id == "new-repair-1"


def test_latest_editor_stage_prefers_new_resume_cycle_over_higher_old_repair_number():
    manifest = RunManifest.new(
        RunSpec(project_path="/tmp/project", prompt_path="/tmp/prompt.md")
    )
    manifest.record(
        StageResult(
            phase="chapter.edit",
            chapter=1,
            status="done",
            revision_id="edit-revision",
            finished_at="2026-09-01T16:20:00+00:00",
        )
    )
    manifest.record(
        StageResult(
            phase="chapter.repair.2",
            chapter=1,
            status="done",
            revision_id="old-repair-2",
            finished_at="2026-09-01T16:10:00+00:00",
        )
    )
    manifest.record(
        StageResult(
            phase="chapter.repair.1",
            chapter=1,
            status="done",
            revision_id="new-repair-1",
            finished_at="2026-09-01T16:25:00+00:00",
        )
    )

    latest = PipelineRunner._latest_editor_stage(manifest, 1)

    assert latest is not None
    assert latest.revision_id == "new-repair-1"


def test_style_checkpoint_reuses_newer_repaired_candidate_on_resume(tmp_path: Path):
    project = tmp_path / "project"
    candidate_relative = "outputs/manuscript/chapter_001_candidate_final.md"
    candidate = project / candidate_relative
    candidate.parent.mkdir(parents=True)
    candidate.write_text("Repaired candidate.\n", encoding="utf-8")

    artifacts = ArtifactStore(project)
    style_revision = artifacts.put_text(
        chapter=1,
        kind="final",
        text="Style candidate.\n",
        source="style_curator",
    )
    repair_revision = artifacts.put_text(
        chapter=1,
        kind="final",
        text="Repaired candidate.\n",
        source="style_curator",
        parent_revision_id=style_revision.revision_id,
    )
    manifest = RunManifest.new(
        RunSpec(project_path=str(project), prompt_path=str(tmp_path / "prompt.md"))
    )
    style = StageResult(
        phase="chapter.style",
        chapter=1,
        status="done",
        revision_id=style_revision.revision_id,
        artifact_paths=[candidate_relative],
        artifact_hashes={candidate_relative: style_revision.sha256},
        finished_at="2026-09-01T16:24:37+00:00",
    )
    repair = StageResult(
        phase="chapter.commercial.repair.1",
        chapter=1,
        status="done",
        revision_id=repair_revision.revision_id,
        artifact_paths=[candidate_relative],
        artifact_hashes={candidate_relative: repair_revision.sha256},
        finished_at="2026-09-01T16:30:07+00:00",
    )
    manifest.record(style)
    manifest.record(repair)
    store = PipelineRunner._store(project, manifest.run_id)
    store.save(manifest)
    runner = PipelineRunner()
    runner._rerun_started = False
    runner._last_valid_state_snapshot = ""

    result = runner._stage(
        manifest,
        project,
        store,
        "chapter.style",
        1,
        lambda: pytest.fail("Style Curator must not rerun after a valid repair"),
        lambda _value: None,
        [candidate_relative],
    )

    assert result is style
    assert candidate.read_text(encoding="utf-8") == "Repaired candidate.\n"


def run_with_free_trial_failure(tmp_path: Path, *, missing_payoff: bool = True):
    """Run through chapter three with one intentionally omitted window payoff."""
    contract = commercial_story_fixture_variant()
    base = _commercial_factory(contract)

    class FreeTrialFailureOrchestrator(base):
        calls = []

        def _proposal(self, number, agent_name, relative):
            source = self.project / relative
            proposal = ProposalStore(self.project).save(
                CanonDeltaProposal(
                    chapter=number,
                    agent_name=agent_name,
                    source_artifact_sha=hashlib.sha256(source.read_bytes()).hexdigest(),
                    delta={"key_events": [f"{agent_name} completed chapter {number}"]},
                )
            )
            self.last_canon_proposal_ids = [proposal.proposal_id]

        def plan_chapter(self, number, summary="", pov="", dry_run=False):
            type(self).calls.append(("chapter.plan", number))
            super().plan_chapter(number, summary, pov, dry_run=dry_run)
            jobs = (
                ("recognition", "anger")
                if number == 1
                else ("anger", "agency")
                if number == 2
                else ("agency", "hope")
            )
            outline = self.outputs / f"chapter_{number:03d}_outline.md"
            payload = chapter_contract_v2_payload(
                chapter=number,
                reader_jobs=jobs,
                hook_type=("decision", "evidence", "deadline")[(number - 1) % 3],
                seeded_resource_ids=(f"resource_{number:02d}",),
                used_resource_ids=(f"resource_{number:02d}",),
            )
            with outline.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n[CHAPTER_CONTRACT]\n"
                    + json.dumps(payload)
                    + "\n[/CHAPTER_CONTRACT]\n"
                )
            self._proposal(number, "architect", f"outputs/chapter_{number:03d}_outline.md")

        def write_chapter(self, number, dry_run=False):
            super().write_chapter(number, dry_run=dry_run)
            self._proposal(
                number, "scribe", f"outputs/manuscript/chapter_{number:03d}_draft.md"
            )

        def edit_chapter(self, number, mode="line", dry_run=False):
            super().edit_chapter(number, mode, dry_run=dry_run)
            self._proposal(
                number, "editor", f"outputs/manuscript/chapter_{number:03d}_revised.md"
            )

        def validate_chapter(self, number, dry_run=False):
            super().validate_chapter(number, dry_run=dry_run)
            self._proposal(
                number,
                "continuity_guardian",
                f"outputs/feedback/chapter_{number:03d}_continuity_report.md",
            )

        def curate_chapter(self, number, dry_run=False):
            super().curate_chapter(number, dry_run=dry_run)
            self._proposal(
                number,
                "style_curator",
                f"outputs/manuscript/chapter_{number:03d}_candidate_final.md",
            )

        def review_commercial_chapter(self, number, dry_run=False):
            candidate = self.manuscript / f"chapter_{number:03d}_candidate_final.md"
            text = candidate.read_text(encoding="utf-8")
            quote = next(line for line in text.splitlines() if line.strip())
            evidence = span(text, quote)
            artifacts = ArtifactStore(self.project)
            chapter_head = artifacts.get_head(number, "chapter_contract")
            story_head = artifacts.get_head(0, "story_contract")
            chapter_contract = chapter_contract_v2(
                **json.loads(artifacts.read_text(chapter_head.revision_id))
            )
            expected_beats = free_trial_beats_for_chapter(contract, number)
            payload = {
                "agency": evidence,
                "resource_change": evidence,
                "local_payoff": evidence,
                "ending_hook": evidence,
                "reader_jobs": {
                    job: evidence for job in chapter_contract.reader_jobs
                },
                "belonging_anchors": {},
                "free_trial_beats": {
                    beat: evidence for beat in expected_beats
                } if expected_beats else [],
                "child_voice": {
                    "status": "not_applicable", "quote": None, "start": None, "end": None
                },
                "institutional_plausibility": evidence,
                "findings": [],
            }
            report = review_commercial_chapter(
                text,
                chapter_contract,
                payload,
                (),
                story_contract_revision_id=story_head.revision_id,
                chapter_contract_revision_id=chapter_head.revision_id,
                expected_free_trial_beats=expected_beats,
            )
            if missing_payoff and number == contract.free_trial_arc.chapter_count:
                report = replace(
                    report,
                    free_trial_beats=tuple(
                        beat for beat in report.free_trial_beats if beat != "local_payoff"
                    ),
                )
            write_commercial_report(self.project, report)
            if report.reader_value_update is not None:
                proposal = ProposalStore(self.project).save(
                    CanonDeltaProposal(
                        chapter=number,
                        agent_name="continuity_guardian",
                        source_artifact_sha=report.artifact_sha256,
                        delta={
                            "reader_value_updates": [dict(report.reader_value_update)]
                        },
                    )
                )
                self.last_canon_proposal_ids = [proposal.proposal_id]
            return report

    FreeTrialFailureOrchestrator.calls = []
    prompt = tmp_path / "free-trial-prompt.md"
    prompt.write_text(
        "# Free Trial Test\n\n" + commercial_story_block(contract),
        encoding="utf-8",
    )
    manifest = PipelineRunner(
        orchestrator_factory=FreeTrialFailureOrchestrator
    ).run(
        RunSpec(
            project_path=str(tmp_path / "free-trial-project"),
            prompt_path=str(prompt),
            num_chapters=12,
            target_words=240,
            approval_policy="auto",
            quality_policy="evidence_v1",
            max_quality_repairs=0,
            output_formats=("markdown",),
        )
    )
    return manifest, list(FreeTrialFailureOrchestrator.calls)


def test_pipeline_stops_before_chapter_four_when_free_window_blocks(tmp_path):
    manifest, calls = run_with_free_trial_failure(tmp_path)

    assert manifest.get("commercial.free_trial_review").status == "blocked"
    assert ("chapter.plan", 4) not in calls
    assert manifest.get("commercial.book_review") is None


def test_pipeline_stops_before_compile_when_whole_book_review_blocks(tmp_path):
    manifest, calls = run_with_free_trial_failure(tmp_path, missing_payoff=False)

    assert manifest.get("commercial.free_trial_review").status == "done"
    assert ("chapter.plan", 12) in calls
    assert manifest.get("commercial.book_review").status == "blocked"
    assert manifest.get("ending.preflight") is None
    assert manifest.get("book.check") is None
    assert manifest.get("compile") is None


def _write_originality_sibling(project: Path, contract) -> None:
    foundation = project / "outputs/input/foundation.json"
    fingerprint = project / "outputs/input/story-fingerprint.json"
    foundation.parent.mkdir(parents=True, exist_ok=True)
    foundation.write_text(
        json.dumps(
            {
                "commercial_story_contract": contract.to_dict(),
                "commercial_story_contract_id": contract.contract_id,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    fingerprint.write_text(
        json.dumps(
            StoryFingerprint.from_contract(contract).to_dict(), sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )
    ensure_project_instance_id(project)


def test_runner_completes_two_chapter_book(tmp_path: Path):
    FakeOrchestrator.calls = []
    FakeOrchestrator.publication_model_calls = []
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


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX flock")
def test_run_rejects_concurrent_project_execution_without_creating_manifest(
    tmp_path: Path,
):
    project = tmp_path / "project"
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Book\n\nA story.", encoding="utf-8")
    lock_path = project / "outputs/.pipeline-execution.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.touch()

    with lock_path.open("r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(PipelineError, match="already active"):
            PipelineRunner(orchestrator_factory=_factory).run(
                RunSpec(
                    project_path=str(project),
                    prompt_path=str(prompt),
                    num_chapters=1,
                    target_words=20,
                    dry_run=True,
                )
            )

    assert not list((project / "outputs/runs").glob("*/run.json"))


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX flock")
def test_resume_rejects_concurrent_project_execution_without_mutating_manifest(
    tmp_path: Path,
):
    project = tmp_path / "project"
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Book\n\nA story.", encoding="utf-8")
    manifest = RunManifest.new(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            dry_run=True,
        ),
        run_id="concurrent-run",
    )
    runner = PipelineRunner(orchestrator_factory=_factory)
    store = runner._store(project, manifest.run_id)
    store.save(manifest)
    lock_path = project / "outputs/.pipeline-execution.lock"
    lock_path.touch()
    before = store.path.read_bytes()

    with lock_path.open("r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(PipelineError, match="already active"):
            runner.resume(manifest.run_id, project)

    assert store.path.read_bytes() == before


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX flock")
def test_retry_rejects_concurrent_execution_without_resetting_stage(
    tmp_path: Path,
):
    project = tmp_path / "project"
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Book\n\nA story.", encoding="utf-8")
    manifest = RunManifest.new(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            dry_run=True,
        ),
        run_id="concurrent-retry",
    )
    manifest.record(
        StageResult(
            phase="chapter.write",
            chapter=1,
            status="failed",
            error="provider interrupted",
        )
    )
    manifest.status = "failed"
    runner = PipelineRunner(orchestrator_factory=_factory)
    store = runner._store(project, manifest.run_id)
    store.save(manifest)
    lock_path = project / "outputs/.pipeline-execution.lock"
    lock_path.touch()
    before = store.path.read_bytes()

    with lock_path.open("r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(PipelineError, match="already active"):
            runner.retry(
                manifest.run_id,
                phase="chapter.write",
                chapter=1,
                project_path=project,
            )

    assert store.path.read_bytes() == before


def test_stage_order_and_publication_inputs_are_source_bound(tmp_path: Path):
    FakeOrchestrator.calls = []
    FakeOrchestrator.publication_model_calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Book\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(orchestrator_factory=_factory).run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=2,
        target_words=40,
        approval_policy="auto",
    ))

    phases = [
        result.phase
        for result in manifest.stages.values()
        if result.chapter is None
    ]
    assert phases.index("book.check") < phases.index("publication.copy")
    assert "foundation.originality" not in phases
    assert phases.index("publication.copy") < phases.index("compile")
    assert phases.index("compile") < phases.index("delivery.package")
    assert manifest.current_phase == "delivery.package"
    assert manifest.status == "completed"
    publication = manifest.get("publication.copy")
    assert set(publication.input_hashes) == {
        "source_set_sha256",
        "book_check_stage_sha256",
        "publication_metadata_sha256",
        "publication_copy_policy_sha256",
    }
    assert publication.input_hashes["publication_copy_policy_sha256"] == hashlib.sha256(
        b"publication-copy-policy.v3"
    ).hexdigest()
    assert manifest.get("compile").artifact_paths == [
        "outputs/deliverables/book.md",
        "outputs/deliverables/book.epub",
        "outputs/deliverables/book.pdf",
        "outputs/deliverables/book.docx",
        "outputs/publication/novel-classification.json",
        "outputs/publication/novel-serialization.json",
    ]
    assert manifest.get("delivery.package").artifact_paths == [
        "outputs/deliverables/meta/novel-classification.json",
        "outputs/deliverables/meta/novel-serialization.json",
        "outputs/deliverables/meta/h5-import.json",
        "outputs/deliverables/package-manifest.json",
        "outputs/deliverables/book-package.zip",
    ]
    assert manifest.get("delivery.package").decisions == [
        "h5_publication: inapplicable_less_than_four_chapters"
    ]
    package_manifest = json.loads(
        (project / "outputs/deliverables/package-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    roles = {entry["path"]: entry["role"] for entry in package_manifest["files"]}
    assert roles["meta/publication-copy.json"] == "publication_copy"
    assert roles["meta/novel-serialization.json"] == "novel_serialization"
    assert not any(path.startswith("h5-publication/") for path in roles)


def test_commercial_story_runs_originality_before_foundation_commit(tmp_path: Path):
    contract = commercial_story_fixture_variant()
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "# Commercial Test\n\n" + commercial_story_block(contract),
        encoding="utf-8",
    )
    project = tmp_path / "projects/candidate"

    manifest = PipelineRunner(
        orchestrator_factory=_commercial_factory(contract)
    ).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
        )
    )

    assert manifest.status == "completed", manifest.error
    phases = [
        result.phase for result in manifest.stages.values() if result.chapter is None
    ]
    assert phases.index("outline") < phases.index("foundation.originality")
    assert phases.index("foundation.originality") < phases.index("foundation.commit")
    originality = manifest.get("foundation.originality")
    assert originality.status == "done"
    assert set(originality.input_hashes) == {
        "foundation_sha256",
        "catalog_sha256",
        "sibling_reference_set_sha256",
    }
    assert originality.artifact_paths == [
        "outputs/input/story-fingerprint.json",
        "outputs/quality/story-originality-report.json",
    ]


def test_pipeline_blocks_when_foundation_omits_the_approved_commercial_contract(
    tmp_path: Path,
):
    contract = commercial_story_fixture_variant()
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "# Commercial Test\n\n" + commercial_story_block(contract),
        encoding="utf-8",
    )
    project = tmp_path / "projects/candidate"

    manifest = PipelineRunner(orchestrator_factory=_factory).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            max_retries=0,
        )
    )

    assert manifest.status == "paused"
    assert "approved commercial contract" in manifest.error.casefold()
    assert manifest.get("foundation.originality") is None
    assert manifest.get("foundation.commit") is None


def test_pipeline_blocks_foundation_from_privately_activating_commercial_story(
    tmp_path: Path,
):
    contract = commercial_story_fixture_variant()
    project = tmp_path / "projects/candidate"
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Legacy Story\n\nNo commercial contract.", encoding="utf-8")

    manifest = PipelineRunner(
        orchestrator_factory=_commercial_factory(contract)
    ).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            max_retries=0,
        )
    )

    assert manifest.status == "paused"
    assert "without prompt intake approval" in manifest.error.casefold()
    assert manifest.get("foundation.originality") is None
    assert manifest.get("foundation.commit") is None


def test_activated_commercial_story_blocks_schema_v1_chapter_contract_before_writing(
    tmp_path: Path,
):
    contract = commercial_story_fixture_variant()
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "# Commercial Test\n\n" + commercial_story_block(contract),
        encoding="utf-8",
    )
    project = tmp_path / "projects/candidate"
    base = _commercial_factory(contract)

    class LegacyChapterContractOrchestrator(base):
        def plan_chapter(self, number, summary="", pov="", dry_run=False):
            super().plan_chapter(number, summary, pov, dry_run=dry_run)
            outline = self.outputs / f"chapter_{number:03d}_outline.md"
            payload = legacy_chapter_contract_payload()
            payload["chapter"] = number
            with outline.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n[CHAPTER_CONTRACT]\n"
                    + json.dumps(payload)
                    + "\n[/CHAPTER_CONTRACT]\n"
                )

    orchestrator = LegacyChapterContractOrchestrator
    orchestrator.calls = []

    manifest = PipelineRunner(orchestrator_factory=orchestrator).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            quality_policy="evidence_v1",
            max_retries=0,
        )
    )

    assert manifest.status == "paused"
    assert "schema-v2" in manifest.error
    assert manifest.get("chapter.plan", 1).status == "blocked"
    assert not any(call == ("write", 1) for call in orchestrator.calls)


def test_commercial_design_gate_blocks_before_scribe_on_contract_budget_violation(
    tmp_path: Path,
):
    contract = commercial_story_fixture_variant()
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "# Commercial Test\n\n" + commercial_story_block(contract),
        encoding="utf-8",
    )
    project = tmp_path / "projects/candidate"
    base = _commercial_factory(contract)

    class InvalidDesignOrchestrator(base):
        def plan_chapter(self, number, summary="", pov="", dry_run=False):
            super().plan_chapter(number, summary, pov, dry_run=dry_run)
            outline = self.outputs / f"chapter_{number:03d}_outline.md"
            payload = chapter_contract_v2_payload(
                chapter=number,
                used_resource_ids=["license_record"],
                seeded_resource_ids=[],
                protagonist_causes_turn=False,
            )
            with outline.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n[CHAPTER_CONTRACT]\n"
                    + json.dumps(payload)
                    + "\n[/CHAPTER_CONTRACT]\n"
                )

    InvalidDesignOrchestrator.calls = []
    manifest = PipelineRunner(orchestrator_factory=InvalidDesignOrchestrator).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            quality_policy="evidence_v1",
            max_retries=0,
        )
    )

    design = manifest.get("chapter.design_check", 1)
    assert manifest.status == "paused"
    assert design.status == "blocked"
    assert {finding["code"] for finding in design.findings} == {
        "unseeded_resource",
        "protagonist_does_not_cause_turn",
    }
    assert (project / "outputs/quality/commercial/chapter_001_design.json").is_file()
    assert not any(call == ("write", 1) for call in InvalidDesignOrchestrator.calls)


def test_retrying_blocked_design_gate_replans_chapter_before_rechecking(tmp_path: Path):
    contract = commercial_story_fixture_variant()
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "# Commercial Test\n\n" + commercial_story_block(contract),
        encoding="utf-8",
    )
    project = tmp_path / "projects/candidate"
    base = _commercial_factory(contract)

    class RepairableDesignOrchestrator(base):
        plan_attempts = 0
        write_attempts = 0

        def plan_chapter(self, number, summary="", pov="", dry_run=False):
            super().plan_chapter(number, summary, pov, dry_run=dry_run)
            type(self).plan_attempts += 1
            outline = self.outputs / f"chapter_{number:03d}_outline.md"
            payload = chapter_contract_v2_payload(chapter=number)
            if type(self).plan_attempts == 1:
                payload["reader_jobs"] = ["recognition"]
                payload["belonging_anchors"] = ["child"]
            with outline.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n[CHAPTER_CONTRACT]\n"
                    + json.dumps(payload)
                    + "\n[/CHAPTER_CONTRACT]\n"
                )

        def write_chapter(self, number, dry_run=False):
            type(self).write_attempts += 1
            raise RuntimeError("stop after design recovery")

    RepairableDesignOrchestrator.plan_attempts = 0
    RepairableDesignOrchestrator.write_attempts = 0
    runner = PipelineRunner(orchestrator_factory=RepairableDesignOrchestrator)
    paused = runner.run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            quality_policy="evidence_v1",
            max_retries=0,
            max_quality_repairs=0,
        )
    )
    assert paused.get("chapter.design_check", 1).status == "blocked"

    recovered = runner.retry(
        paused.run_id,
        phase="chapter.design_check",
        chapter=1,
        project_path=project,
    )

    assert RepairableDesignOrchestrator.plan_attempts == 2
    assert RepairableDesignOrchestrator.write_attempts == 1
    assert recovered.get("chapter.design_check", 1).status == "done"
    assert recovered.get("chapter.write", 1).status == "failed"


def test_auto_mode_replans_blocked_chapter_design_before_writing(tmp_path: Path):
    contract = commercial_story_fixture_variant()
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "# Commercial Test\n\n" + commercial_story_block(contract),
        encoding="utf-8",
    )
    project = tmp_path / "projects/candidate"
    base = _commercial_factory(contract)

    class AutoRepairableDesignOrchestrator(base):
        plan_attempts = 0
        write_attempts = 0

        def plan_chapter(self, number, summary="", pov="", dry_run=False):
            super().plan_chapter(number, summary, pov, dry_run=dry_run)
            type(self).plan_attempts += 1
            outline = self.outputs / f"chapter_{number:03d}_outline.md"
            payload = chapter_contract_v2_payload(chapter=number)
            if type(self).plan_attempts == 1:
                payload["used_resource_ids"] = ["res_labor", "res_time"]
                payload["seeded_resource_ids"] = ["res_labor"]
            else:
                payload["used_resource_ids"] = ["res_labor", "res_time"]
                payload["seeded_resource_ids"] = ["res_labor", "res_time"]
            with outline.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n[CHAPTER_CONTRACT]\n"
                    + json.dumps(payload)
                    + "\n[/CHAPTER_CONTRACT]\n"
                )

        def write_chapter(self, number, dry_run=False):
            type(self).write_attempts += 1
            raise RuntimeError("stop after automatic design recovery")

    AutoRepairableDesignOrchestrator.plan_attempts = 0
    AutoRepairableDesignOrchestrator.write_attempts = 0
    manifest = PipelineRunner(
        orchestrator_factory=AutoRepairableDesignOrchestrator
    ).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            quality_policy="evidence_v1",
            max_retries=0,
            max_quality_repairs=1,
        )
    )

    assert AutoRepairableDesignOrchestrator.plan_attempts == 2
    assert AutoRepairableDesignOrchestrator.write_attempts == 1
    assert manifest.get("chapter.design_check", 1).status == "done"
    events = (
        project / f"outputs/runs/{manifest.run_id}/events.jsonl"
    ).read_text(encoding="utf-8")
    assert '"event": "chapter.design_repair_scheduled"' in events
    assert "res_time" in events


def test_blocked_originality_report_prevents_foundation_commit(tmp_path: Path):
    projects = tmp_path / "projects"
    _write_originality_sibling(projects / "reference", high_overlap_reference())
    contract = high_overlap_candidate()
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "# Candidate\n\n" + commercial_story_block(contract), encoding="utf-8"
    )

    manifest = PipelineRunner(
        orchestrator_factory=_commercial_factory(contract)
    ).run(
        RunSpec(
            project_path=str(projects / "candidate"),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            max_retries=0,
        )
    )

    originality = manifest.get("foundation.originality")
    assert manifest.status == "paused"
    assert originality.status == "blocked"
    assert originality.artifact_paths == [
        "outputs/input/story-fingerprint.json",
        "outputs/quality/story-originality-report.json",
    ]
    assert manifest.get("foundation.commit") is None


def test_new_sibling_fingerprint_invalidates_and_reblocks_paused_run(
    tmp_path: Path,
):
    contract = high_overlap_candidate()
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "# Candidate\n\n" + commercial_story_block(contract), encoding="utf-8"
    )
    project = tmp_path / "projects/candidate"
    orchestrator = _commercial_factory(contract)
    orchestrator.fail_validation = True
    runner = PipelineRunner(orchestrator_factory=orchestrator)
    paused = runner.run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            max_retries=0,
        )
    )
    assert paused.get("foundation.originality").status == "done"
    _write_originality_sibling(
        tmp_path / "projects/reference", high_overlap_reference()
    )
    orchestrator.fail_validation = False

    reblocked = runner.resume(paused.run_id, project)

    assert reblocked.status == "paused"
    assert reblocked.get("foundation.originality").status == "blocked"
    events = (
        project / f"outputs/runs/{paused.run_id}/events.jsonl"
    ).read_text(encoding="utf-8")
    assert events.count('"phase": "foundation.originality"') >= 4


def test_four_chapter_delivery_checkpoints_current_h5_publication(tmp_path: Path):
    FakeOrchestrator.calls = []
    FakeOrchestrator.publication_model_calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Book\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(orchestrator_factory=_factory).run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=4,
        target_words=80,
        approval_policy="auto",
    ))

    delivery = manifest.get("delivery.package")
    h5_metadata = [
        path for path in delivery.artifact_paths
        if path.endswith("/meta/publication_package.json")
    ]
    assert manifest.status == "completed", manifest.error
    assert manifest.current_phase == "delivery.package"
    assert len(h5_metadata) == 1
    assert (project / h5_metadata[0]).is_file()
    assert delivery.decisions == []
    package_manifest = json.loads(
        (project / "outputs/deliverables/package-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    roles = {entry["path"]: entry["role"] for entry in package_manifest["files"]}
    assert roles["meta/publication-copy.json"] == "publication_copy"
    assert any(role == "h5_publication_object" for role in roles.values())


def test_delivery_checkpoint_rejects_h5_stage_timestamp_drift(tmp_path: Path):
    FakeOrchestrator.publication_model_calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Book\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    manifest = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=4,
        target_words=80,
        approval_policy="auto",
    ))
    delivery = manifest.get("delivery.package")
    publication_package_path = next(
        project / path
        for path in delivery.artifact_paths
        if path.endswith("/meta/publication_package.json")
    )
    delivery_path = publication_package_path.parent / "delivery.json"
    delivery_payload = json.loads(delivery_path.read_bytes())
    delivery_payload["delivered_at"] = "2026-08-31T02:00:00Z"
    delivery_path.write_text(
        json.dumps(
            delivery_payload, ensure_ascii=False, sort_keys=True, indent=2
        ) + "\n",
        encoding="utf-8",
    )

    assert not runner._checkpoint_valid(project, delivery, manifest)


def test_publication_failure_stops_before_exports(tmp_path: Path):
    class InvalidPublicationOrchestrator(FakeOrchestrator):
        publication_model_calls = []
        publication_writer_response = "{}"

    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Book\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(
        orchestrator_factory=InvalidPublicationOrchestrator
    ).run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))

    assert manifest.status == "paused"
    assert manifest.current_phase == "publication.copy"
    assert manifest.get("publication.copy").status == "blocked"
    assert not (project / "outputs/publication/publication-copy.json").exists()
    assert not (project / "outputs/deliverables/book.md").exists()


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


def test_resume_failed_stdin_intake_uses_its_run_prompt_after_another_run_overwrites_project_prompt(
    tmp_path: Path, monkeypatch,
):
    import book_author

    first_prompt = (
        "# First North Door\n\nGenre: Suspense\nAudience: Adult\n\n"
        "Mara must choose whether to open it."
    )
    second_prompt = (
        "# Second South Door\n\nGenre: Romance\nAudience: Adult\n\n"
        "Nora must choose whether to close it."
    )
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)

    def fail_author_generation(*_args):
        raise LLMError("unexpected status 401 Unauthorized: missing bearer token")

    monkeypatch.setattr(book_author, "_default_complete", fail_author_generation)
    monkeypatch.setattr("prompt_intake.sys.stdin", io.StringIO(first_prompt))
    first_failed = runner.run(RunSpec(
        project_path=str(project),
        prompt_path="-",
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
        max_retries=0,
    ))

    assert first_failed.status == "failed"
    assert first_failed.get("intake").status == "failed"
    assert (project / "outputs/input/prompt.md").read_text(
        encoding="utf-8"
    ) == first_prompt
    first_run_prompt = (
        project / "outputs/runs" / first_failed.run_id / "input/prompt.md"
    )
    assert first_run_prompt.read_text(encoding="utf-8") == first_prompt

    monkeypatch.setattr("prompt_intake.sys.stdin", io.StringIO(second_prompt))
    second_failed = runner.run(RunSpec(
        project_path=str(project),
        prompt_path="-",
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
        max_retries=0,
    ))

    assert second_failed.status == "failed"
    assert second_failed.run_id != first_failed.run_id
    assert (project / "outputs/input/prompt.md").read_text(
        encoding="utf-8"
    ) == second_prompt
    assert (
        project
        / "outputs/runs"
        / second_failed.run_id
        / "input/prompt.md"
    ).read_text(encoding="utf-8") == second_prompt

    monkeypatch.setattr(
        book_author, "_default_complete", lambda *_args: '{"author":"Mara Vale"}'
    )
    monkeypatch.setattr("prompt_intake.sys.stdin", io.StringIO(""))
    resumed = runner.resume(first_failed.run_id, project)

    assert resumed.status == "completed", resumed.error
    assert (project / "outputs/input/prompt.md").read_text(
        encoding="utf-8"
    ) == first_prompt
    assert json.loads(
        (project / "outputs/input/brief.json").read_text(encoding="utf-8")
    )["title"] == "First North Door"
    assert first_run_prompt.read_text(encoding="utf-8") == first_prompt


def test_fresh_empty_stdin_does_not_reuse_unrelated_persisted_prompt(
    tmp_path: Path, monkeypatch, fake_author_model,
):
    project = tmp_path / "project"
    saved_prompt = project / "outputs/input/prompt.md"
    saved_prompt.parent.mkdir(parents=True)
    saved_prompt.write_text("# Unrelated Old Prompt\n\nAn old story.", encoding="utf-8")
    monkeypatch.setattr("prompt_intake.sys.stdin", io.StringIO(""))

    runner = PipelineRunner(orchestrator_factory=_factory)
    manifest = runner.run(RunSpec(
        project_path=str(project),
        prompt_path="-",
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
        max_retries=0,
    ))

    assert manifest.status == "failed"
    assert "Prompt is empty" in manifest.error
    assert saved_prompt.read_text(encoding="utf-8") == (
        "# Unrelated Old Prompt\n\nAn old story."
    )
    assert fake_author_model == []

    monkeypatch.setattr("prompt_intake.sys.stdin", io.StringIO(""))
    resumed_without_input = runner.resume(manifest.run_id, project)

    assert resumed_without_input.status == "failed"
    assert "Prompt is empty" in resumed_without_input.error
    assert saved_prompt.read_text(encoding="utf-8") == (
        "# Unrelated Old Prompt\n\nAn old story."
    )
    assert not (
        project / "outputs/runs" / manifest.run_id / "input/prompt.md"
    ).exists()
    assert fake_author_model == []

    replacement_prompt = "# Explicit Resume Prompt\n\nA new story."
    monkeypatch.setattr("prompt_intake.sys.stdin", io.StringIO(replacement_prompt))
    recovered = runner.resume(manifest.run_id, project)

    assert recovered.status == "completed", recovered.error
    assert (
        project / "outputs/runs" / manifest.run_id / "input/prompt.md"
    ).read_text(encoding="utf-8") == replacement_prompt
    assert fake_author_model and len(fake_author_model) == 1


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


def test_auto_approval_repairs_guardian_failure_within_same_run(tmp_path: Path):
    class RepairingOrchestrator(FakeOrchestrator):
        validation_attempts = 0

        def validate_chapter(self, number, dry_run=False):
            type(self).validation_attempts += 1
            type(self).calls.append(("validate", number))
            chapter = self.state.get_chapter(number)
            chapter.status = "validated"
            status = "PASS" if type(self).validation_attempts > 1 else "FAIL"
            chapter.continuity_checks["status"] = status
            self.state.save_state()
            (self.feedback / f"chapter_{number:03d}_continuity_report.md").write_text(
                f"Status: {status}\nCritical_Issues: [timeline contradiction]\n",
                encoding="utf-8",
            )

        def repair_chapter(self, number, feedback, attempt, dry_run=False):
            type(self).calls.append(("repair", number, attempt))
            assert "timeline contradiction" in feedback
            (self.manuscript / f"chapter_{number:03d}_revised.md").write_text(
                f"Repaired {number}\n", encoding="utf-8"
            )

    RepairingOrchestrator.calls = []
    RepairingOrchestrator.validation_attempts = 0
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(orchestrator_factory=RepairingOrchestrator).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            max_quality_repairs=2,
        )
    )

    assert manifest.status == "completed", manifest.error
    assert RepairingOrchestrator.validation_attempts == 2
    assert ("repair", 1, 1) in RepairingOrchestrator.calls
    assert manifest.get("chapter.repair.1", 1).status == "done"
    assert (project / "outputs/deliverables/book.md").exists()


def test_auto_approval_repairs_deterministic_precheck_within_same_run(
    tmp_path: Path,
):
    class RepairingPrecheckOrchestrator(FakeOrchestrator):
        repaired = False

        def run_checks(self, number=None):
            type(self).calls.append(("check", number))
            if number is None:
                return 0
            return 0 if type(self).repaired else 1

        def repair_chapter(self, number, feedback, attempt, dry_run=False):
            type(self).calls.append(("repair", number, attempt))
            type(self).repaired = True
            (self.manuscript / f"chapter_{number:03d}_revised.md").write_text(
                f"Repaired {number}\n", encoding="utf-8"
            )

    RepairingPrecheckOrchestrator.calls = []
    RepairingPrecheckOrchestrator.repaired = False
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(
        orchestrator_factory=RepairingPrecheckOrchestrator
    ).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            max_quality_repairs=2,
        )
    )

    assert manifest.status == "completed", manifest.error
    assert ("repair", 1, 1) in RepairingPrecheckOrchestrator.calls
    assert RepairingPrecheckOrchestrator.calls.count(("check", 1)) >= 3
    assert manifest.get("chapter.check.post", 1).status == "done"


def test_quality_repair_budget_exhaustion_still_blocks_promotion(tmp_path: Path):
    class UnrepairableOrchestrator(FakeOrchestrator):
        def validate_chapter(self, number, dry_run=False):
            type(self).calls.append(("validate", number))
            (self.feedback / f"chapter_{number:03d}_continuity_report.md").write_text(
                "Status: FAIL\nCritical_Issues: [persistent contradiction]\n",
                encoding="utf-8",
            )

        def repair_chapter(self, number, feedback, attempt, dry_run=False):
            type(self).calls.append(("repair", number, attempt))
            (self.manuscript / f"chapter_{number:03d}_revised.md").write_text(
                f"Still broken {number} attempt {attempt}\n", encoding="utf-8"
            )

    UnrepairableOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"

    manifest = PipelineRunner(orchestrator_factory=UnrepairableOrchestrator).run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            max_quality_repairs=2,
        )
    )

    assert manifest.status == "paused"
    assert "after 2 automatic repair" in manifest.error
    assert UnrepairableOrchestrator.calls.count(("repair", 1, 1)) == 1
    assert UnrepairableOrchestrator.calls.count(("repair", 1, 2)) == 1
    assert not (project / "outputs/manuscript/chapter_001_final.md").exists()


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


def test_architect_authentication_error_fails_once_and_can_resume(
    tmp_path: Path,
):
    class AuthenticationFailingOrchestrator(FakeOrchestrator):
        plan_attempts = 0
        authentication_valid = False

        def plan_chapter(self, number, summary="", pov="", dry_run=False):
            type(self).plan_attempts += 1
            if not type(self).authentication_valid:
                raise LLMError(
                    "Codex CLI failed (exit 1): unexpected status 401 Unauthorized: "
                    "Missing bearer or basic authentication in header, url: "
                    "https://api.openai.com/v1/responses"
                )
            return super().plan_chapter(
                number, summary=summary, pov=pov, dry_run=dry_run
            )

    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=AuthenticationFailingOrchestrator)
    manifest = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
        max_retries=5,
        retry_backoff_seconds=0,
    ))

    failed = manifest.get("chapter.plan", 1)
    assert AuthenticationFailingOrchestrator.plan_attempts == 1
    assert manifest.status == "failed"
    assert failed is not None
    assert failed.status == "failed"
    assert failed.attempt == 1
    assert failed.retryable is False
    assert PipelineRunner._store(project, manifest.run_id).load().status == "failed"

    AuthenticationFailingOrchestrator.authentication_valid = True
    resumed = runner.resume(manifest.run_id, project)

    assert resumed.status == "completed", resumed.error
    assert AuthenticationFailingOrchestrator.plan_attempts == 2


@pytest.mark.parametrize(
    "message",
    [
        "temporary upstream timeout",
        "unexpected status 429 Too Many Requests",
        "unexpected status 500 Internal Server Error",
        "unexpected status 503 Service Unavailable",
    ],
)
def test_transient_llm_errors_remain_retryable(message: str):
    assert PipelineRunner._is_retryable(LLMError(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "unexpected status 401 Unauthorized",
        "HTTP status 403",
        "status code: 401",
        "authentication failed for the configured account",
        "authentication_error: invalid credentials",
        "Codex is not authenticated; run codex login",
        "invalid API key",
        "missing bearer token",
    ],
)
def test_explicit_authentication_errors_are_not_retryable(message: str):
    assert PipelineRunner._is_retryable(LLMError(message)) is False


def test_forbidden_content_message_remains_retryable():
    error = LLMError("request rejected because the prompt contains a forbidden phrase")

    assert PipelineRunner._is_retryable(error) is True


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


def test_retry_outline_does_not_require_foundation_before_architect_runs(
    tmp_path: Path,
):
    class RetryOutlineOrchestrator(FakeOrchestrator):
        outline_attempts = 0

        def plan_outline(self, chapters, words, dry_run=False):
            type(self).outline_attempts += 1
            if type(self).outline_attempts == 1:
                raise LLMError("temporary outline failure")
            return super().plan_outline(chapters, words, dry_run=dry_run)

        def build_proposal_runtime_state(self):
            foundation = self.outputs / "input/foundation.json"
            if not foundation.exists():
                raise ValueError("proposal runtime state requires foundation.json")
            return StoryState(str(self.project))

    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=RetryOutlineOrchestrator)
    failed = runner.run(
        RunSpec(
            project_path=str(project),
            prompt_path=str(prompt),
            num_chapters=1,
            target_words=20,
            approval_policy="auto",
            max_retries=0,
        )
    )

    assert failed.status == "failed"
    assert not (project / "outputs/input/foundation.json").exists()

    completed = runner.retry(
        failed.run_id,
        project_path=project,
        phase="outline",
    )

    assert completed.status == "completed", completed.error
    assert (project / "outputs/input/foundation.json").exists()


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


def test_retry_precheck_adopts_repaired_foundation_without_rerunning_outline(tmp_path: Path):
    class FoundationGateOrchestrator(FakeOrchestrator):
        def run_checks(self, number=None):
            type(self).calls.append(("check", number))
            if number is None:
                return 0
            foundation = json.loads(
                (self.outputs / "input/foundation.json").read_text(encoding="utf-8")
            )
            return 0 if foundation.get("continuity_repaired") else 1

    FoundationGateOrchestrator.calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("A story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=FoundationGateOrchestrator)
    paused = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))
    assert paused.status == "paused"
    assert paused.get("chapter.check.pre", 1).status == "blocked"

    interrupted_state = StoryState(str(project))
    interrupted_state.metadata["interrupted_outline_state"] = True
    interrupted_state.save_state()

    foundation = project / "outputs/input/foundation.json"
    repaired_foundation = json.loads(foundation.read_text(encoding="utf-8"))
    repaired_foundation["continuity_repaired"] = True
    foundation.write_text(
        json.dumps(repaired_foundation) + "\n",
        encoding="utf-8",
    )

    completed = runner.retry(
        paused.run_id,
        project_path=project,
        phase="chapter.check.pre",
        chapter=1,
    )

    assert completed.status == "completed", completed.error
    assert FoundationGateOrchestrator.calls.count(("outline", 1)) == 1
    outline = completed.get("outline")
    relative = "outputs/input/foundation.json"
    assert outline.artifact_hashes[relative] == PipelineRunner._sha256(foundation)
    assert any("foundation" in decision.lower() for decision in outline.decisions)
    assert "interrupted_outline_state" not in StoryState(str(project)).metadata


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


def test_compile_ignores_empty_mutable_projection_when_final_head_is_valid(
    tmp_path: Path,
):
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

    runner._compile_book(manifest, project)

    assert "Final 1" in (
        project / "outputs/deliverables/book.md"
    ).read_text(encoding="utf-8")


def test_compile_blocks_when_final_head_outgrows_publication_binding(tmp_path: Path):
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
    artifacts = ArtifactStore(project)
    current = artifacts.get_head(1, "final")
    canonical = artifacts.put_text(
        chapter=1,
        kind="final",
        text="Verified canonical Final 1.\n",
        source="test_canonical",
        parent_revision_id=current.revision_id if current else None,
    )
    artifacts.set_head(
        1,
        "final",
        canonical.revision_id,
        expected_revision_id=current.revision_id if current else None,
    )
    projection = project / "outputs/manuscript/chapter_001_final.md"
    projection.write_text("Forged mutable projection.\n", encoding="utf-8")

    with pytest.raises(PipelineError, match="divergent"):
        runner._compile_book(manifest, project)


def test_resume_completed_run_repairs_final_from_trusted_candidate_without_agents(tmp_path: Path):
    FakeOrchestrator.calls = []
    FakeOrchestrator.publication_model_calls = []
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
    assert any(
        "repaired" in item.lower()
        for item in repaired.get("chapter.promote", 1).decisions
    )


def _downgrade_to_historical_completed_manifest(
    runner: PipelineRunner,
    manifest,
    project: Path,
) -> None:
    manifest.stages.pop("publication.copy")
    manifest.stages.pop("delivery.package")
    publication_path = project / "outputs/publication/publication-copy.json"
    publication_path.unlink()

    compile_result = manifest.get("compile")
    compile_result.input_hashes = runner._stage_input_hashes(
        project, "compile", None
    )
    compile_result.artifact_paths.extend([
        "outputs/deliverables/package-manifest.json",
        "outputs/deliverables/book-package.zip",
    ])
    compile_result.artifact_hashes = {
        relative: runner._sha256(project / relative)
        for relative in compile_result.artifact_paths
    }
    manifest.current_phase = "compile"
    manifest.current_chapter = None
    runner._store(project, manifest.run_id).save(manifest)


def test_resume_preserves_valid_historical_completed_manifest_without_backfill(
    tmp_path: Path,
):
    FakeOrchestrator.publication_model_calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Historical Book\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    completed = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))
    _downgrade_to_historical_completed_manifest(runner, completed, project)
    model_calls = list(FakeOrchestrator.publication_model_calls)
    export_sha = runner._sha256(project / "outputs/deliverables/book.md")

    resumed = runner.resume(completed.run_id, project)

    assert resumed.status == "completed"
    assert resumed.current_phase == "compile"
    assert resumed.get("publication.copy") is None
    assert resumed.get("delivery.package") is None
    assert not (project / "outputs/publication/publication-copy.json").exists()
    assert runner._sha256(project / "outputs/deliverables/book.md") == export_sha
    assert FakeOrchestrator.publication_model_calls == model_calls


def test_resume_repairs_historical_exports_without_publication_backfill(
    tmp_path: Path,
):
    FakeOrchestrator.publication_model_calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Historical Book\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    completed = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))
    _downgrade_to_historical_completed_manifest(runner, completed, project)
    model_calls = list(FakeOrchestrator.publication_model_calls)
    (project / "outputs/deliverables/book.md").unlink()

    resumed = runner.resume(completed.run_id, project)

    assert resumed.status == "completed"
    assert resumed.current_phase == "compile"
    assert resumed.get("publication.copy") is None
    assert resumed.get("delivery.package") is None
    assert not (project / "outputs/publication/publication-copy.json").exists()
    assert "Final 1" in (
        project / "outputs/deliverables/book.md"
    ).read_text(encoding="utf-8")
    assert (project / "outputs/deliverables/book-package.zip").is_file()
    assert FakeOrchestrator.publication_model_calls == model_calls


def test_compile_retry_reuses_publication_checkpoint_and_final_bytes(tmp_path: Path):
    FakeOrchestrator.calls = []
    FakeOrchestrator.publication_model_calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Book\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    completed = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))
    publication_path = project / "outputs/publication/publication-copy.json"
    final_path = project / "outputs/manuscript/chapter_001_final.md"
    publication_sha = PipelineRunner._sha256(publication_path)
    final_sha = PipelineRunner._sha256(final_path)
    model_calls = list(FakeOrchestrator.publication_model_calls)
    (project / "outputs/deliverables/book.md").unlink()

    resumed = runner.resume(completed.run_id, project)

    assert resumed.status == "completed", resumed.error
    assert resumed.current_phase == "delivery.package"
    assert PipelineRunner._sha256(publication_path) == publication_sha
    assert PipelineRunner._sha256(final_path) == final_sha
    assert resumed.get("publication.copy").attempt == 1
    assert resumed.get("compile").status == "done"
    assert resumed.get("delivery.package").status == "done"
    assert FakeOrchestrator.publication_model_calls == model_calls


def test_four_chapter_compile_retry_reuses_byte_identical_h5_root(tmp_path: Path):
    FakeOrchestrator.calls = []
    FakeOrchestrator.publication_model_calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Book\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    completed = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=4,
        target_words=80,
        approval_policy="auto",
    ))
    h5_base = project / "outputs/deliverables/h5-publication"
    original_roots = sorted(path.name for path in h5_base.iterdir())
    compile_finished_at = completed.get("compile").finished_at
    (project / "outputs/deliverables/book.md").unlink()

    resumed = runner.resume(completed.run_id, project)

    assert resumed.status == "completed", resumed.error
    assert resumed.current_phase == "delivery.package"
    assert resumed.get("compile").finished_at == compile_finished_at
    assert sorted(path.name for path in h5_base.iterdir()) == original_roots
    assert len(original_roots) == 1


def test_completed_run_does_not_regenerate_missing_publication_authority(
    tmp_path: Path,
):
    FakeOrchestrator.calls = []
    FakeOrchestrator.publication_model_calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Book\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    completed = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=1,
        target_words=20,
        approval_policy="auto",
    ))
    model_calls = list(FakeOrchestrator.publication_model_calls)
    (project / "outputs/publication/publication-copy.json").unlink()

    resumed = runner.resume(completed.run_id, project)

    assert resumed.status == "paused"
    assert "publication.copy authority" in resumed.error
    assert FakeOrchestrator.publication_model_calls == model_calls


def test_approved_final_head_change_invalidates_publication_and_downstream(tmp_path: Path):
    FakeOrchestrator.calls = []
    FakeOrchestrator.publication_model_calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Book\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    completed = runner.run(RunSpec(
        project_path=str(project),
        prompt_path=str(prompt),
        num_chapters=2,
        target_words=40,
        approval_policy="auto",
    ))
    old_copy_sha = PipelineRunner._sha256(
        project / "outputs/publication/publication-copy.json"
    )
    old_model_call_count = len(FakeOrchestrator.publication_model_calls)

    artifacts = ArtifactStore(project)
    old_head = artifacts.get_head(2, "final")
    approved_text = "# Chapter 2\n\nChanged approved text under rising rent pressure.\n"
    new_head = artifacts.put_text(
        chapter=2,
        kind="final",
        text=approved_text,
        source="test_approval",
        parent_revision_id=old_head.revision_id,
    )
    artifacts.set_head(
        2,
        "final",
        new_head.revision_id,
        expected_revision_id=old_head.revision_id,
    )
    final_relative = "outputs/manuscript/chapter_002_final.md"
    (project / final_relative).write_text(approved_text, encoding="utf-8")
    promote = completed.get("chapter.promote", 2)
    promote.revision_id = new_head.revision_id
    promote.artifact_hashes[final_relative] = new_head.sha256
    promote.promotion_receipt_id = runner._legacy_receipt_id(completed.run_id, promote)
    runner._write_stage_result(completed, project, promote)
    runner._store(project, completed.run_id).save(completed)

    resumed = runner.resume(completed.run_id, project)

    assert resumed.status == "completed", resumed.error
    assert resumed.get("book.check").status == "done"
    assert resumed.get("publication.copy").attempt == 1
    assert resumed.get("compile").status == "done"
    assert resumed.get("delivery.package").status == "done"
    assert len(FakeOrchestrator.publication_model_calls) > old_model_call_count
    assert PipelineRunner._sha256(
        project / "outputs/publication/publication-copy.json"
    ) != old_copy_sha
    assert "Changed approved text" in (
        project / "outputs/deliverables/book.md"
    ).read_text(encoding="utf-8")


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


def test_invalid_architect_chapter_contract_is_retryable(tmp_path: Path):
    path = tmp_path / "chapter_006_outline.md"
    payload = chapter_contract_v2_payload(chapter=6, hook_type="question")
    path.write_text(
        "[CHAPTER_CONTRACT]\n"
        + json.dumps(payload)
        + "\n[/CHAPTER_CONTRACT]\n",
        encoding="utf-8",
    )

    with pytest.raises(LLMError, match="hook_type has invalid value"):
        PipelineRunner._parse_chapter_contract(path, 6)
