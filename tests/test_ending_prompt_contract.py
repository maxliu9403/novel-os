"""Ending obligations use one output protocol and only committed history."""

import json
from pathlib import Path

import pytest

from artifacts import ArtifactStore
from canon import CanonDeltaProposal
from canon_ledger import canonical_canon_sha
from context_pack import build_context_pack, format_context_pack
from orchestrator import NovelOrchestrator
from project_identity import ensure_project_instance_id
from promotion import PromotionRequest, PromotionService
from proposals import ProposalStore
from quality import EvaluationRequest, QualityLab
from state_manager import Character, StoryState, initialize_project


def _project(tmp_path):
    project = tmp_path / "book"
    initialize_project(str(project), "Bounded Counsel", "drama")
    state = StoryState(str(project))
    state.characters["L01"] = Character(id="L01", full_name="Marian Reed", role="lawyer")
    for number in (1, 3, 15):
        chapter = state.create_chapter(number)
        chapter.pov_character = "Marian Reed"
    state.save_state()
    contract = {
        "schema_version": 1,
        "enforce": True,
        "finale_window": {"start_chapter": 11, "end_chapter": 15},
        "character_arcs": [{
            "character_id": "L01", "required_arc_stage": "resolution",
            "required_outcome": "bounded_counsel_delivered",
            "required_choice": "Deliver bounded legal advice without choosing for the client.",
        }],
        "plot_payoffs": [{"id": "payoff_004", "required_payoff": "The protection takes effect.", "deadline": 15}],
        "main_conflict": {"thread_id": "family_choice"},
        "emotional_contract": {"reader_emotion": "relief"},
    }
    path = project / "outputs/input/ending_contract.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract), encoding="utf-8")
    return project


def _commit(project, *, delta=None, chain=False):
    state = StoryState(str(project))
    artifacts = ArtifactStore(project)
    text = "Marian delivered the signed protection notice. The client retained her choice."
    final = artifacts.put_text(chapter=1, kind="final", text=text, source="editor")
    proposal = CanonDeltaProposal(
        chapter=1, agent_name="continuity_guardian", source_artifact_sha=final.sha256,
        delta=delta or {
            "status": "PASS",
            "payoff_events": ["payoff_004 | status=paid | evidence=The protection notice took effect. | chapter=1"],
            "arc_state_updates": ["L01 | stage=resolution | progress=100 | outcome=bounded_counsel_delivered | evidence=Marian delivered the notice without choosing for the client."],
        },
    )
    if chain:
        item = {key: value for key, value in proposal.to_dict().items()
                if key in {"proposal_id", "agent_name", "source_artifact_sha", "delta"}}
        proposal = CanonDeltaProposal(
            chapter=1, agent_name="continuity_guardian", source_artifact_sha=final.sha256,
            delta={"proposal_chain": [item]},
        )
    ProposalStore(project).save(proposal)
    evaluation = EvaluationRequest.for_text(1, text, artifact_revision_id=final.revision_id)
    report = QualityLab.evaluate_deterministic(evaluation, candidate_text=text, continuity_findings=[])
    request = PromotionRequest(
        project_id=project.name, project_instance_id=ensure_project_instance_id(project),
        chapter=1, candidate_revision_id=final.revision_id, candidate_sha256=final.sha256,
        evaluation_report=report, canon_proposal_id=proposal.proposal_id,
        expected_final_revision_id=None, expected_final_sha256=None,
        base_canon_sha=canonical_canon_sha(state), idempotency_key="ending-prompt-1",
        actor="author", reason="approved",
    )
    PromotionService(project).promote(request)
    return final, proposal


def test_roles_and_guardian_task_share_ending_fields_and_separate_blocks(tmp_path):
    root = Path(__file__).resolve().parents[1]
    for role in ("scribe", "continuity_guardian"):
        template = (root / "agents" / role / "prompt.md").read_text()
        contract = template.split("# OUTPUT CONTRACT", 1)[1]
        for field in ("Payoff_Events", "Arc_State_Updates", "Ending_Evidence"):
            assert f"`{field}`" in contract
            assert f"\n{field}:\n" in contract
    project = _project(tmp_path)
    prompt = NovelOrchestrator(str(project))._generate_validation_prompt(15, "The client decides.")
    assert prompt.count("[CONTINUITY_REPORT]") == 1
    assert prompt.count("[/CONTINUITY_REPORT]") == 1
    assert prompt.count("[CONTINUITY_STATE_UPDATE]") == 1
    assert prompt.count("[/CONTINUITY_STATE_UPDATE]") == 1
    assert "  - Arc_State_Updates:" not in prompt
    state_block = prompt.split("[CONTINUITY_STATE_UPDATE]", 1)[1].split("[/CONTINUITY_STATE_UPDATE]", 1)[0]
    for field in ("Payoff_Events", "Arc_State_Updates", "Ending_Evidence"):
        assert f"\n{field}:\n" in state_block


def test_pre_finale_can_record_already_delivered_professional_outcome(tmp_path):
    project = _project(tmp_path)
    orchestrator = NovelOrchestrator(str(project))
    for prompt in (
        orchestrator._generate_chapter_prompt(orchestrator.state.get_chapter(3)),
        orchestrator._generate_validation_prompt(3, "Marian delivers the advice."),
    ):
        assert '"required_outcome": "bounded_counsel_delivered"' in prompt
        assert '"character_id": "L01"' in prompt
        assert '"full_name": "Marian Reed"' in prompt
        assert "Do not perform a future choice early" in prompt
        assert "already occurred in this chapter" in prompt
        assert '"main_conflict"' not in prompt
        assert '"emotional_contract"' not in prompt
        assert "outcome=<canonical outcome value from ending_contract>" in prompt


def test_context_cast_includes_stable_character_id(tmp_path):
    project = _project(tmp_path)
    rendered = format_context_pack(build_context_pack(StoryState(str(project)), 3, purpose="guardian"))
    assert any("**Marian Reed**" in line and "id=L01" in line for line in rendered.splitlines())


def test_ending_identity_survives_remote_character_dropping_from_context_pack(tmp_path):
    project = _project(tmp_path)
    state = StoryState(str(project))
    state.characters["L01"].last_appearance_chapter = 3
    state.characters["C01"] = Character(id="C01", full_name="Eleanor", role="protagonist", last_appearance_chapter=15)
    state.chapters[15].pov_character = "Eleanor"
    state.save_state()
    pack = build_context_pack(state, 15, purpose="guardian", chapter_text="Eleanor chose for herself.")
    assert "L01" not in {item["id"] for item in pack.cast}
    context = NovelOrchestrator(str(project))._ending_contract_context(15)
    directory = context.split("## Ending Character Identity Directory", 1)[1].split("## Committed Ending Evidence Index", 1)[0]
    assert '"character_id": "L01"' in directory
    assert '"full_name": "Marian Reed"' in directory


def test_finale_history_uses_committed_records_not_planned_or_current_events(tmp_path):
    project = _project(tmp_path)
    final, proposal = _commit(project)
    state = StoryState(str(project))
    state.chapters[3].payoff_events = [{"id": "payoff_004", "status": "paid", "evidence": "UNAPPROVED DRAFT"}]
    state.chapters[15].arc_state_updates = [{"character_id": "L01", "evidence": "CURRENT CANDIDATE"}]
    state.save_state()
    prompt = NovelOrchestrator(str(project))._generate_validation_prompt(15, "The client decides.")
    history = prompt.split("## Committed Ending Evidence Index", 1)[1].split("### Character Positions", 1)[0]
    assert final.sha256 in history
    assert final.revision_id in history
    assert proposal.proposal_id in history
    assert "protection notice took effect" in history
    assert "Marian delivered the notice" in history
    assert "UNAPPROVED DRAFT" not in history
    assert "CURRENT CANDIDATE" not in history
    assert "not new events in this chapter" in history


def test_uncommitted_final_artifact_is_not_ending_evidence(tmp_path):
    project = _project(tmp_path)
    artifacts = ArtifactStore(project)
    final = artifacts.put_text(chapter=1, kind="final", text="UNCOMMITTED", source="editor")
    artifacts.set_head(1, "final", final.revision_id, expected_revision_id=None)
    state = StoryState(str(project))
    state.chapters[1].status = "complete"
    state.chapters[1].canonical_revision_id = final.revision_id
    state.chapters[1].payoff_events = [{"id": "payoff_004", "status": "paid", "evidence": "UNCOMMITTED"}]
    state.save_state()
    prompt = NovelOrchestrator(str(project))._ending_contract_context(15)
    assert "## Committed Ending Evidence Index" in prompt
    assert "UNCOMMITTED" not in prompt
    assert "No committed records" in prompt


def test_corrupt_committed_artifact_fails_closed(tmp_path):
    project = _project(tmp_path)
    final, _ = _commit(project)
    artifacts = ArtifactStore(project)
    (artifacts.blob_root / final.sha256).write_text("TAMPERED", encoding="utf-8")
    with pytest.raises(ValueError, match="Ending evidence.*integrity"):
        NovelOrchestrator(str(project))._ending_contract_context(15)


def test_history_preserves_entire_related_record_and_excludes_other_obligations(tmp_path):
    project = _project(tmp_path)
    evidence = "Complete bounded professional explanation. " * 250 + "END OF EVIDENCE"
    _commit(project, delta={
        "status": "PASS",
        "payoff_events": [
            f"payoff_004 | status=paid | evidence={evidence} | chapter=1",
            "unrelated_payoff | status=paid | evidence=UNRELATED OBLIGATION | chapter=1",
        ],
        "new_facts_established": ["UNRELATED GENERAL FACT"],
    })
    context = NovelOrchestrator(str(project))._ending_contract_context(15)
    history = context.split("## Committed Ending Evidence Index", 1)[1]
    assert evidence in history
    assert "UNRELATED OBLIGATION" not in history
    assert "UNRELATED GENERAL FACT" not in history


def test_history_follows_current_artifact_not_superseded_promotion(tmp_path):
    project = _project(tmp_path)
    final, _ = _commit(project)
    artifacts = ArtifactStore(project)
    replacement = artifacts.put_text(chapter=1, kind="final", text="Replacement awaiting approval", source="editor")
    artifacts.set_head(1, "final", replacement.revision_id, expected_revision_id=final.revision_id)
    context = NovelOrchestrator(str(project))._ending_contract_context(15)
    assert "protection notice took effect" not in context
    assert "No committed records" in context


def test_history_reads_committed_proposal_chain(tmp_path):
    project = _project(tmp_path)
    _, proposal = _commit(project, chain=True)
    context = NovelOrchestrator(str(project))._ending_contract_context(15)
    history = context.split("## Committed Ending Evidence Index", 1)[1]
    assert proposal.proposal_id in history
    assert "protection notice took effect" in history
    assert "Marian delivered the notice" in history


def _record_supplement(project):
    from ending_evidence import EndingEvidenceStore, contract_sha256

    final, _ = _commit(project, delta={"status": "PASS", "key_events": ["Historical on-page action"]})
    text = ArtifactStore(project).read_text(final.revision_id)
    path = project / "outputs/manuscript/chapter_001_final.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    receipt = PromotionService(project).load_receipt("ending-prompt-1")
    contract = json.loads((project / "outputs/input/ending_contract.json").read_text())
    payload = {
        "schema_version": 1, "project_instance_id": receipt.project_instance_id,
        "contract_sha256": contract_sha256(contract), "reviewed_at": "2026-10-14T08:00:00+08:00",
        "reviewer": {"kind": "agent", "name": "Codex", "reason": "Reviewed the approved chapter's legal duty."},
        "supersedes": [], "claims": [{
            "kind": "arc", "character_id": "L01", "arc_stage": "resolution",
            "outcome_state": "bounded_counsel_delivered", "evidence": "SUPPLEMENTAL COUNSEL EVIDENCE",
            "sources": [{
                "chapter": 1, "revision_id": final.revision_id, "sha256": final.sha256,
                "promotion_key": receipt.idempotency_key, "promotion_receipt_id": receipt.receipt_id,
                "quote": text, "quote_start": 0, "quote_end": len(text),
            }],
        }],
    }
    return EndingEvidenceStore(project).record(payload), contract


def test_reviewed_supplement_keeps_its_own_source_and_reviewer_provenance(tmp_path):
    project = _project(tmp_path)
    record, _ = _record_supplement(project)
    context = NovelOrchestrator(str(project))._ending_contract_context(15)
    supplemental = context.split("## Reviewed Supplemental Ending Evidence", 1)[1]
    assert record["review_id"] in supplemental
    assert record["claims"][0]["sources"][0]["quote"] in supplemental
    assert '"kind": "agent"' in supplemental
    assert "SUPPLEMENTAL COUNSEL EVIDENCE" in supplemental
    assert "not original canon proposals" in supplemental


def test_supplement_integrity_failure_is_explicitly_unavailable_in_prompt(tmp_path):
    project = _project(tmp_path)
    _record_supplement(project)
    (project / "outputs/manuscript/chapter_001_final.md").write_text("CHANGED", encoding="utf-8")
    context = NovelOrchestrator(str(project))._ending_contract_context(15)
    supplemental = context.split("## Reviewed Supplemental Ending Evidence", 1)[1]
    assert "Supplemental evidence unavailable" in supplemental
    assert "SUPPLEMENTAL COUNSEL EVIDENCE" not in supplemental
    assert "Final ending validation remains required" in supplemental


def test_first_chapter_never_includes_its_own_review_as_history(tmp_path):
    project = _project(tmp_path)
    _, contract = _record_supplement(project)
    context = NovelOrchestrator(str(project))._committed_ending_evidence_context(1, contract)
    assert "SUPPLEMENTAL COUNSEL EVIDENCE" not in context
    assert "No reviewed supplemental evidence" in context
