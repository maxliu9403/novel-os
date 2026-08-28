import copy
import json
from datetime import datetime, timezone

from canon import apply_canon_proposal, build_canon_proposal
from canon_ledger import CanonLedger, CanonLedgerEntry, canonical_canon_sha
from canon_reconciliation import CanonReconciliationService
from foundation_canon import FoundationCanonService
from orchestrator import NovelOrchestrator
from pipeline_models import RunManifest, RunSpec, StageResult
from pipeline_runner import PipelineRunner
from project_identity import ensure_project_instance_id
from proposals import ProposalStore
from state_manager import StoryState, initialize_project


SOURCE_SHA = "a" * 64


def _foundation() -> dict:
    return {
        "title": "North Door",
        "premise": "Mara chooses whether to open the north door.",
        "themes": ["agency"],
        "setting": {"time_period": "present", "primary_location": "Boston"},
        "characters": [
            {"id": "char_001", "name": "Mara Vale", "role": "protagonist"}
        ],
        "plot_threads": [
            {
                "id": "plot_001",
                "name": "The North Door",
                "description": "Mara must choose.",
                "type": "main",
                "priority": 5,
                "resolution_chapter": 1,
            }
        ],
        "ending_contract": {
            "schema_version": 1,
            "enforce": True,
            "character_arcs": [
                {
                    "character_id": "char_001",
                    "required_end_state": "accountability",
                    "required_choice": "Mara opens the door and accepts the cost",
                }
            ],
        },
        "style": {"tone": "intimate", "pov": "third_limited"},
        "chapters": [
            {"number": 1, "title": "The Key", "pov": "Mara Vale"}
        ],
    }


def _legacy_project(tmp_path):
    project = tmp_path / "project"
    initialize_project(str(project), "North Door", "suspense")
    state = StoryState(str(project))
    chapter = state.create_chapter(1)
    chapter.status = "complete"
    chapter.word_count = 123
    chapter.canonical_revision_id = "b" * 64
    state.save_state()
    base_sha = canonical_canon_sha(state)

    proposal = build_canon_proposal(
        state,
        chapter=1,
        agent_name="continuity_guardian",
        source_artifact_sha=SOURCE_SHA,
        text="""[CONTINUITY_REPORT]
Status: PASS
Plot_Thread_Updates:
  - plot_001 | status=resolved | milestone=Mara opens the door | chapter=1
Arc_State_Updates:
  - char_001 | stage=resolution | progress=100 | evidence=Mara accepts the cost
Ending_Evidence:
  - irreversible_change=Mara opens the north door
[/CONTINUITY_REPORT]""",
    )
    ProposalStore(project).save(proposal)
    apply_canon_proposal(state, proposal, SOURCE_SHA)
    state.save_state()
    legacy_tail_sha = canonical_canon_sha(state)
    entry = CanonLedgerEntry(
        project_instance_id=ensure_project_instance_id(project),
        previous_entry_id=None,
        proposal_id=proposal.proposal_id,
        source_artifact_sha=SOURCE_SHA,
        base_canon_sha=base_sha,
        new_canon_sha=legacy_tail_sha,
        chapter=1,
        receipt_id=f"promotion-receipt-{'c' * 64}",
        request_id=f"promotion-request-{'d' * 64}",
        idempotency_key="pipeline-legacy-chapter-1",
        committed_at=datetime.now(timezone.utc).isoformat(),
    )
    CanonLedger(project).append(entry)
    return project, entry


def test_foundation_reconciliation_replays_history_without_rewriting_it(tmp_path):
    project, original_entry = _legacy_project(tmp_path)
    ledger_path = project / "outputs/state/canon_ledger.jsonl"
    original_ledger = ledger_path.read_bytes()
    original_state = StoryState(str(project))
    original_chapter = copy.deepcopy(original_state.chapters[1])

    receipt = CanonReconciliationService(project).reconcile(
        _foundation(),
        target_words=123,
        idempotency_key="foundation-reconcile-legacy",
        outcomes={
            "char_001": {
                "outcome_state": "accountability",
                "outcome_evidence": "Mara opens the door and accepts the cost.",
            }
        },
        reason="Restore foundation omitted by the legacy proposal-only pipeline.",
    )

    state = StoryState(str(project))
    history = CanonLedger(project).history()
    assert history[0] == original_entry
    assert ledger_path.read_bytes().startswith(original_ledger)
    assert len(history) == 2
    assert history[-1].entry_kind == "foundation_reconciliation"
    assert history[-1].new_canon_sha == canonical_canon_sha(state)
    assert receipt.reconciliation_entry_id == history[-1].entry_id
    journal = json.loads(
        (
            project
            / "outputs/state/foundation_reconciliation_journal/foundation-reconcile-legacy.json"
        ).read_text(encoding="utf-8")
    )["reconciliation_journal"]
    assert journal["base_state_payload"]["characters"] == {}
    assert journal["state_payload"]["characters"]["char_001"]["full_name"] == "Mara Vale"
    assert state.characters["char_001"].arc_stage == "resolution"
    assert state.characters["char_001"].outcome_state == "accountability"
    assert state.plot_threads["plot_001"].status == "resolved"
    assert (
        state.plot_threads["plot_001"].milestones[-1]["timestamp"]
        == original_entry.committed_at
    )
    assert state.chapters[1].status == original_chapter.status
    assert state.chapters[1].word_count == original_chapter.word_count
    assert (
        state.chapters[1].canonical_revision_id
        == original_chapter.canonical_revision_id
    )

    repeated = CanonReconciliationService(project).reconcile(
        _foundation(),
        target_words=123,
        idempotency_key="foundation-reconcile-legacy",
        outcomes={
            "char_001": {
                "outcome_state": "accountability",
                "outcome_evidence": "Mara opens the door and accepts the cost.",
            }
        },
        reason="Restore foundation omitted by the legacy proposal-only pipeline.",
    )
    assert repeated == receipt
    assert len(CanonLedger(project).history()) == 2

    assert FoundationCanonService(project).initialize(
        _foundation(),
        target_words=123,
        idempotency_key="foundation-reconcile-legacy",
    ) == receipt
    alias = FoundationCanonService(project).initialize(
        _foundation(),
        target_words=123,
        idempotency_key="pipeline-file-hash-foundation",
    )
    assert alias.idempotency_key == "pipeline-file-hash-foundation"
    assert alias.reconciliation_entry_id == receipt.reconciliation_entry_id
    assert alias.new_canon_sha == receipt.new_canon_sha
    assert len(CanonLedger(project).history()) == 2
    assert (
        project
        / "outputs/state/foundation_receipts/pipeline-file-hash-foundation.json"
    ).is_file()

    state_path = project / "outputs/state/story_state.json"
    state_path.write_text(
        json.dumps(journal["base_state_payload"], ensure_ascii=False),
        encoding="utf-8",
    )
    restored = FoundationCanonService(project).initialize(
        _foundation(),
        target_words=123,
        idempotency_key="pipeline-file-hash-foundation",
    )
    assert restored == alias
    assert canonical_canon_sha(StoryState(str(project))) == receipt.new_canon_sha

    foundation_path = project / "outputs/input/foundation.json"
    foundation_path.parent.mkdir(parents=True, exist_ok=True)
    foundation_path.write_text(json.dumps(_foundation()), encoding="utf-8")
    runtime = NovelOrchestrator(str(project)).build_proposal_runtime_state()
    assert runtime.characters["char_001"].outcome_state == "accountability"


def test_reused_foundation_checkpoint_restores_reconciled_ledger_head(tmp_path):
    project, _original_entry = _legacy_project(tmp_path)
    foundation = _foundation()
    reconciliation = CanonReconciliationService(project).reconcile(
        foundation,
        target_words=123,
        idempotency_key="foundation-reconcile-legacy",
        outcomes={
            "char_001": {
                "outcome_state": "accountability",
                "outcome_evidence": "Mara opens the door and accepts the cost.",
            }
        },
        reason="Restore foundation omitted by the legacy proposal-only pipeline.",
    )
    ledger_head = CanonLedger(project).current()
    assert ledger_head is not None
    assert ledger_head.new_canon_sha == reconciliation.new_canon_sha

    foundation_path = project / "outputs/input/foundation.json"
    foundation_path.parent.mkdir(parents=True, exist_ok=True)
    foundation_path.write_text(json.dumps(foundation), encoding="utf-8")

    manifest = RunManifest.new(
        RunSpec(
            project_path=str(project),
            prompt_path="-",
            num_chapters=1,
            target_words=123,
            approval_policy="auto",
            quality_policy="evidence_v1",
        ),
        run_id="legacy-run",
    )
    runner = PipelineRunner(project)
    foundation_key = runner._foundation_idempotency_key(manifest, project)
    alias = FoundationCanonService(project).initialize(
        foundation,
        target_words=123,
        idempotency_key=foundation_key,
    )
    assert alias.reconciliation_entry_id == reconciliation.reconciliation_entry_id

    journal = json.loads(
        (
            project
            / "outputs/state/foundation_reconciliation_journal/foundation-reconcile-legacy.json"
        ).read_text(encoding="utf-8")
    )["reconciliation_journal"]
    stale_payload = journal["base_state_payload"]
    snapshot = project / "outputs/runs/legacy-run/stages/foundation-commit.state.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps(stale_payload), encoding="utf-8")
    state_path = project / "outputs/state/story_state.json"
    state_path.write_text(json.dumps(stale_payload), encoding="utf-8")
    stale_snapshot_hash = runner._sha256(snapshot)

    receipt_relative = f"outputs/state/foundation_receipts/{foundation_key}.json"
    stage = StageResult(
        phase="foundation.commit",
        status="done",
        artifact_paths=[receipt_relative],
        artifact_hashes={
            receipt_relative: runner._sha256(project / receipt_relative),
        },
        input_hashes=runner._stage_input_hashes(
            project, "foundation.commit", None
        ),
        state_snapshot_path=str(snapshot.relative_to(project)),
        state_snapshot_hash=stale_snapshot_hash,
    )
    manifest.record(stage)
    store = runner._store(project, manifest.run_id)
    store.save(manifest)
    calls = 0

    def initialize_foundation():
        nonlocal calls
        calls += 1
        return runner._commit_story_foundation(manifest, project)

    runner._rerun_started = False
    runner._last_valid_state_snapshot = ""
    runner._active_orchestrator = None
    reused = runner._stage(
        manifest,
        project,
        store,
        "foundation.commit",
        None,
        initialize_foundation,
        runner._validate_foundation_commit,
        [receipt_relative],
    )

    assert calls == 1
    assert canonical_canon_sha(StoryState(str(project))) == ledger_head.new_canon_sha
    assert reused.state_snapshot_hash != stale_snapshot_hash
    assert runner._sha256(project / reused.state_snapshot_path) == reused.state_snapshot_hash
    assert json.loads((project / reused.state_snapshot_path).read_text(encoding="utf-8")) == json.loads(
        state_path.read_text(encoding="utf-8")
    )
