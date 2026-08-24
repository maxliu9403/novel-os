"""Receipt-based artifact and canon promotion tests."""

import json
import multiprocessing
import os
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from artifacts import ArtifactStore, StaleArtifactHead
from canon import CanonDeltaProposal
from canon_ledger import CanonCorruptionError, CanonLedger, canonical_canon_sha
from project_lock import ProjectLockError
from promotion import (
    IdempotencyConflict,
    PromotionCorruptionError,
    PromotionRequest,
    PromotionService,
)
from proposals import ProposalStore
from quality import EvaluationRequest, QualityLab
from state_manager import StoryState


def _promotion_fixture(project: Path, *, key: str = "promotion-1"):
    state = StoryState(str(project))
    state.metadata = {"title": "North Door"}
    state.story_bible = {"rule": "The door stays locked."}
    state.save_state()

    artifacts = ArtifactStore(project)
    old = artifacts.put_text(
        chapter=1, kind="final", text="Old chapter.", source="import"
    )
    candidate_text = "Mara opened the north door."
    candidate = artifacts.put_text(
        chapter=1,
        kind="final",
        text=candidate_text,
        source="editor",
        parent_revision_id=old.revision_id,
    )
    artifacts.set_head(1, "final", old.revision_id, expected_revision_id=None)

    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=candidate.sha256,
        delta={"key_events": ["Mara opened the north door."]},
        timestamp="2026-08-24T00:00:00+00:00",
    )
    ProposalStore(project).save(proposal)
    evaluation_request = EvaluationRequest.for_text(
        1, candidate_text, artifact_revision_id=candidate.revision_id
    )
    report = QualityLab.evaluate_deterministic(
        evaluation_request,
        candidate_text=candidate_text,
        continuity_findings=[],
        created_at="2026-08-24T00:00:00+00:00",
    )
    request = PromotionRequest(
        project_id=project.name,
        chapter=1,
        candidate_revision_id=candidate.revision_id,
        candidate_sha256=candidate.sha256,
        evaluation_report=report,
        canon_proposal_id=proposal.proposal_id,
        expected_final_revision_id=old.revision_id,
        expected_final_sha256=old.sha256,
        base_canon_sha=canonical_canon_sha(StoryState(str(project))),
        idempotency_key=key,
        actor="author",
        reason="approved",
    )
    return request, old, candidate


def _snapshot(project: Path):
    state_path = project / "outputs" / "state" / "story_state.json"
    artifacts = ArtifactStore(project)
    head = artifacts.get_head(1, "final")
    return (
        state_path.read_bytes(),
        head.revision_id if head else None,
        canonical_canon_sha(StoryState(str(project))),
        CanonLedger(project).history(),
        sorted(
            path.name
            for path in (project / "outputs" / "state" / "promotion_receipts").glob("*.json")
        )
        if (project / "outputs" / "state" / "promotion_receipts").exists()
        else [],
    )


def _process_promote(project: str, request_data: dict, queue) -> None:
    try:
        request = PromotionRequest.from_dict(request_data)
        receipt = PromotionService(Path(project)).promote(request)
    except Exception as exc:  # noqa: BLE001 - subprocess reports the exact outcome
        queue.put(("error", type(exc).__name__, str(exc)))
    else:
        queue.put(("ok", receipt.receipt_id, receipt.new_artifact_revision_id))


def test_stale_artifact_base_does_not_mutate_head_canon_ledger_or_receipts(tmp_path):
    project = tmp_path / "project"
    request, old, _candidate = _promotion_fixture(project)
    artifacts = ArtifactStore(project)
    newer = artifacts.put_text(
        chapter=1,
        kind="final",
        text="Already promoted elsewhere.",
        source="other",
        parent_revision_id=old.revision_id,
    )
    artifacts.set_head(
        1, "final", newer.revision_id, expected_revision_id=old.revision_id
    )
    before = _snapshot(project)

    with pytest.raises(StaleArtifactHead):
        PromotionService(project).promote(request)

    assert _snapshot(project) == before


def test_repeated_same_idempotency_key_returns_one_committed_receipt(tmp_path):
    project = tmp_path / "project"
    request, old, candidate = _promotion_fixture(project)
    service = PromotionService(project)

    first = service.promote(request)
    second = service.promote(PromotionRequest.from_dict(request.to_dict()))

    assert second == first
    assert first.old_artifact_revision_id == old.revision_id
    assert first.new_artifact_revision_id == candidate.revision_id
    assert first.old_canon_sha == request.base_canon_sha
    assert first.new_canon_sha != first.old_canon_sha
    assert PromotionService(project).load_receipt(request.idempotency_key) == first
    assert len(CanonLedger(project).history()) == 1
    receipt_files = list(
        (project / "outputs" / "state" / "promotion_receipts").glob("*.json")
    )
    assert len(receipt_files) == 1
    assert json.loads(receipt_files[0].read_text(encoding="utf-8"))["receipt"][
        "receipt_id"
    ] == first.receipt_id


def test_promotion_request_and_receipt_are_strict_frozen_contracts(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)

    restored = PromotionRequest.from_dict(request.to_dict())
    receipt = PromotionService(project).promote(request)

    assert restored == request
    assert type(receipt).from_dict(receipt.to_dict()) == receipt
    with pytest.raises(FrozenInstanceError):
        request.actor = "other"
    with pytest.raises(FrozenInstanceError):
        receipt.status = "changed"
    with pytest.raises(ValueError, match="fields"):
        PromotionRequest.from_dict({**request.to_dict(), "extra": True})


def test_same_idempotency_key_with_different_request_conflicts(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    PromotionService(project).promote(request)
    conflict = replace(request, reason="different", request_id="")

    with pytest.raises(IdempotencyConflict):
        PromotionService(project).promote(conflict)


def test_nonpassing_quality_report_changes_nothing(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    rejected_report = replace(
        request.evaluation_report,
        hard_gates={"no_blocking_findings": False},
        status="fail",
        report_id="",
    )
    rejected = replace(
        request,
        evaluation_report=rejected_report,
        request_id="",
    )
    before = _snapshot(project)

    with pytest.raises(ValueError, match="quality"):
        PromotionService(project).promote(rejected)

    assert _snapshot(project) == before


def test_proposal_source_mismatch_changes_nothing(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    wrong = replace(request, candidate_sha256="f" * 64, request_id="")
    before = _snapshot(project)

    with pytest.raises(ValueError, match="candidate"):
        PromotionService(project).promote(wrong)

    assert _snapshot(project) == before


@pytest.mark.parametrize(
    "failpoint",
    [
        "before_state_commit",
        "after_state_commit",
        "before_head_commit",
        "after_head_commit",
        "before_receipt_commit",
        "after_receipt_commit",
    ],
)
def test_retry_after_injected_failure_converges_exactly_once(tmp_path, failpoint):
    project = tmp_path / "project"
    request, _old, candidate = _promotion_fixture(project)
    failed = False

    def fail_once(point):
        nonlocal failed
        if point == failpoint and not failed:
            failed = True
            raise RuntimeError(f"injected failure at {point}")

    with pytest.raises(RuntimeError, match="injected failure"):
        PromotionService(project, fault_injector=fail_once).promote(request)

    receipt = PromotionService(project).promote(request)

    assert receipt.new_artifact_revision_id == candidate.revision_id
    assert ArtifactStore(project).get_head(1, "final").revision_id == candidate.revision_id
    assert canonical_canon_sha(StoryState(str(project))) == receipt.new_canon_sha
    assert len(CanonLedger(project).history()) == 1
    assert PromotionService(project).load_receipt(request.idempotency_key) == receipt


def test_tampered_receipt_is_rejected(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    PromotionService(project).promote(request)
    receipt_path = next(
        (project / "outputs/state/promotion_receipts").glob("*.json")
    )
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["receipt"]["actor"] = "attacker"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PromotionCorruptionError):
        PromotionService(project).load_receipt(request.idempotency_key)


def test_tampered_ledger_is_rejected(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    PromotionService(project).promote(request)
    ledger_path = project / "outputs/state/canon_ledger.jsonl"
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    payload["entry"]["actor"] = "attacker"
    ledger_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(CanonCorruptionError):
        CanonLedger(project).history()


def test_tampered_journal_is_rejected_during_recovery(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)

    def fail_after_preparation(point):
        if point == "before_state_commit":
            raise RuntimeError("stop after journal")

    with pytest.raises(RuntimeError):
        PromotionService(project, fault_injector=fail_after_preparation).promote(request)
    journal_path = next((project / "outputs/state/promotion_journal").glob("*.json"))
    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    payload["journal"]["actor"] = "attacker"
    journal_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PromotionCorruptionError):
        PromotionService(project).promote(request)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlinks/flock")
def test_symlink_lock_path_is_rejected_without_changes(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    lock_path = project / "outputs/state/.promotion.lock"
    external = tmp_path / "external-lock"
    external.write_text("outside", encoding="utf-8")
    lock_path.symlink_to(external)
    before = _snapshot(project)

    with pytest.raises(ProjectLockError, match="symlink"):
        PromotionService(project).promote(request)

    assert _snapshot(project) == before
    assert external.read_text(encoding="utf-8") == "outside"


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlinks")
@pytest.mark.parametrize("directory_name", ["promotion_journal", "promotion_receipts"])
def test_symlinked_transaction_directory_is_rejected_before_changes(
    tmp_path, directory_name
):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    external = tmp_path / f"external-{directory_name}"
    external.mkdir()
    (project / "outputs" / "state" / directory_name).symlink_to(
        external, target_is_directory=True
    )
    before = _snapshot(project)

    with pytest.raises(PromotionCorruptionError, match="real directory"):
        PromotionService(project).promote(request)

    assert _snapshot(project) == before
    assert list(external.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX flock/fork")
def test_two_processes_same_key_return_one_receipt(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_process_promote,
            args=(str(project), request.to_dict(), queue),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
    results = [queue.get(timeout=2) for _ in processes]

    assert all(not process.is_alive() and process.exitcode == 0 for process in processes)
    assert [result[0] for result in results] == ["ok", "ok"]
    assert len({result[1] for result in results}) == 1
    assert len(CanonLedger(project).history()) == 1
