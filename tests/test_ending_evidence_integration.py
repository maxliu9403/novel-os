import hashlib
import json

import pytest

from ending_evidence import EndingEvidenceStore, contract_sha256
from ending_quality import ensure_quality_ledgers, evaluate_ending
from pipeline_models import RunManifest, RunSpec
from pipeline_runner import PipelineError, PipelineRunner
from state_manager import PlotThread, StoryState
from test_ending_evidence import evidence_book  # noqa: F401 - shared real promotion fixture


@pytest.fixture
def reviewed_book(evidence_book):
    project, contract, payload, _ = evidence_book
    state = StoryState(str(project))
    state.plot_threads["plot_001"] = PlotThread(
        "plot_001", "Decision", "The door opens", "main", status="resolved",
    )
    state.chapters[1].ending_evidence = ["The client keeps her choice."]
    state.save_state()
    contract["main_conflict"] = {"thread_id": "plot_001", "required_status": "resolved"}
    contract["finale_window"] = {"start_chapter": 1, "end_chapter": 1}
    (project / "outputs/input/ending_contract.json").write_text(json.dumps(contract))
    payload["contract_sha256"] = contract_sha256(contract)
    return project, payload


def test_reviewed_evidence_closes_only_record_gaps_without_rewriting_canon(reviewed_book):
    project, payload = reviewed_book
    assert len(evaluate_ending(project).critical) == 3
    before = (project / "outputs/state/story_state.json").read_bytes()
    record = EndingEvidenceStore(project).record(payload)

    report = evaluate_ending(project)

    assert report.status == "pass"
    assert (project / "outputs/state/story_state.json").read_bytes() == before
    assert any(item["category"] == "ending_evidence_review" and item["review_id"] == record["review_id"]
               for item in report.info)
    ledger = json.loads((project / "outputs/state/payoff_ledger.json").read_text())
    evidence = ledger["items"][0]["payoff_evidence"]
    assert evidence[0]["review_id"] == record["review_id"]
    assert evidence[0]["sources"] == payload["claims"][0]["sources"]


def test_stale_evidence_remains_blocked_with_a_clear_report(reviewed_book):
    project, payload = reviewed_book
    EndingEvidenceStore(project).record(payload)
    (project / "outputs/manuscript/chapter_001_final.md").write_text("A different ending.")
    report = evaluate_ending(project)
    assert report.status == "fail"
    assert any(item["category"] == "ending_evidence_invalid" for item in report.critical)


def test_ending_checkpoint_tracks_reviews_but_not_its_own_derived_ledger(reviewed_book):
    project, payload = reviewed_book
    evaluate_ending(project)
    baseline = PipelineRunner._stage_input_hashes(project, "ending.review", None)
    record = EndingEvidenceStore(project).record(payload)
    recorded = PipelineRunner._stage_input_hashes(project, "ending.review", None)
    assert recorded != baseline
    evaluate_ending(project)
    assert PipelineRunner._stage_input_hashes(project, "ending.review", None) == recorded
    (project / "outputs/state/ending_evidence_reviews" / f"{record['review_id']}.json").unlink()
    assert PipelineRunner._stage_input_hashes(project, "ending.review", None) != recorded
    assert evaluate_ending(project).status == "fail"


def test_blocked_ending_stage_restores_old_snapshot_and_uses_independent_review(reviewed_book):
    project, payload = reviewed_book
    manifest = RunManifest.new(
        RunSpec(project_path=str(project), num_chapters=1, target_words=100, max_retries=0),
        run_id="ending-evidence-recovery",
    )
    runner = PipelineRunner()
    store = runner._store(project, manifest.run_id)
    runner._stage(
        manifest, project, store, "ending.preflight", None,
        lambda: ensure_quality_ledgers(project),
        lambda _value: runner._require_files(project, ["outputs/input/ending_contract.json"]),
        ["outputs/input/ending_contract.json", "outputs/state/payoff_ledger.json"],
    )
    snapshot_relative = manifest.get("ending.preflight").state_snapshot_path
    snapshot = project / snapshot_relative
    snapshot_before = snapshot.read_bytes()
    state_path = project / "outputs/state/story_state.json"
    manuscript = project / "outputs/manuscript/chapter_001_final.md"
    manuscript_hash = hashlib.sha256(manuscript.read_bytes()).hexdigest()
    review_artifacts = ["outputs/state/payoff_ledger.json", "outputs/feedback/book_completion_report.json"]
    with pytest.raises(PipelineError, match="Book ending gate blocked compile"):
        runner._stage(
            manifest, project, store, "ending.review", None,
            lambda: evaluate_ending(project, as_of_chapter=1),
            runner._validate_ending_result, review_artifacts,
        )
    assert store.load().get("ending.review").status == "blocked"

    record = EndingEvidenceStore(project).record(payload)
    review_relative = f"outputs/state/ending_evidence_reviews/{record['review_id']}.json"
    review_before = (project / review_relative).read_bytes()
    # Make the live state visibly different from the saved checkpoint. The
    # retry must actually restore the snapshot, not merely re-read this file.
    interrupted_state = StoryState(str(project))
    interrupted_state.metadata["interrupted_recovery_marker"] = "must be rolled back"
    interrupted_state.save_state()
    assert state_path.read_bytes() != snapshot_before

    resumed_manifest = store.load()
    resumed = PipelineRunner()
    resumed._rerun_started = False
    resumed._last_valid_state_snapshot = snapshot_relative
    observed_restored_state = []

    def evaluate_after_restore():
        observed_restored_state.append(state_path.read_bytes())
        return evaluate_ending(project, as_of_chapter=1)

    result = resumed._stage(
        resumed_manifest, project, store, "ending.review", None,
        evaluate_after_restore, resumed._validate_ending_result, review_artifacts,
    )

    assert observed_restored_state == [snapshot_before]
    assert resumed._rerun_started is True
    assert result.status == "done"
    assert store.load().get("ending.review").status == "done"
    assert review_relative in result.input_hashes
    assert (project / review_relative).read_bytes() == review_before
    assert snapshot.read_bytes() == snapshot_before
    assert state_path.read_bytes() == snapshot_before
    assert hashlib.sha256(manuscript.read_bytes()).hexdigest() == manuscript_hash
    # The stage passed through the supplemental record, without patching the
    # old canon snapshot or treating its still-missing metadata as resolved.
    state = StoryState(str(project))
    assert state.characters["L01"].arc_stage == "beginning"
    assert state.characters["L01"].outcome_state == ""
    assert state.chapters[1].payoff_events == []
    report = json.loads((project / "outputs/feedback/book_completion_report.json").read_text())
    assert report["status"] == "pass"
    assert any(item.get("review_id") == record["review_id"] for item in report["info"])
