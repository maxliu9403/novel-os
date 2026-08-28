"""Receipt-based artifact and canon promotion tests."""

import copy
import hashlib
import json
import multiprocessing
import os
import shutil
import stat
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import canon_ledger as canon_ledger_module
from artifacts import ArtifactStore, StaleArtifactHead
from canon import CanonDeltaProposal
from canon_ledger import (
    CanonCorruptionError,
    CanonLedger,
    CanonLedgerEntry,
    canonical_canon_sha,
)
from project_lock import ProjectLock, ProjectLockError
from project_identity import (
    ProjectIdentityError,
    ensure_project_instance_id,
    fork_project_instance_id,
    load_project_instance_id,
)
from promotion import (
    IdempotencyConflict,
    PromotionCorruptionError,
    PromotionReceipt,
    PromotionRequest,
    PromotionService,
    StaleCanonError,
)
from proposals import ProposalStore
from quality import EvaluationRequest, QualityLab
from state_manager import PlotThread, StoryState


def _promotion_fixture(
    project: Path,
    *,
    key: str = "promotion-1",
    with_story_contract: bool = False,
    with_plot_thread_milestone: bool = False,
    with_existing_session_log: bool = False,
    with_continuity_status: bool = False,
):
    state = StoryState(str(project))
    state.metadata = {"title": "North Door"}
    state.story_bible = {"rule": "The door stays locked."}
    if with_plot_thread_milestone:
        state.plot_threads["north-door"] = PlotThread(
            id="north-door",
            name="The North Door",
            description="Mara must decide whether to open it.",
            thread_type="main",
        )
    if with_existing_session_log:
        state.session_log.append(
            {
                "timestamp": "2026-08-23T23:00:00",
                "action": "existing",
                "details": {"stable": True},
            }
        )
    state.save_state()

    artifacts = ArtifactStore(project)
    story_contract = None
    if with_story_contract:
        story_contract = artifacts.put_json(
            chapter=0,
            kind="story_contract",
            value={"premise": "The north door stays locked."},
            source="architect",
        )
        artifacts.set_head(
            0,
            "story_contract",
            story_contract.revision_id,
            expected_revision_id=None,
        )
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
        story_contract_revision_id=(
            story_contract.revision_id if story_contract is not None else None
        ),
    )
    artifacts.set_head(1, "final", old.revision_id, expected_revision_id=None)

    delta = {"key_events": ["Mara opened the north door."]}
    if with_plot_thread_milestone:
        delta["plot_thread_updates"] = [
            "north-door | status=resolved | milestone=The door opened."
        ]
    if with_continuity_status:
        delta["status"] = "PASS"
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=candidate.sha256,
        delta=delta,
        timestamp="2026-08-24T00:00:00+00:00",
    )
    ProposalStore(project).save(proposal)
    evaluation_request = EvaluationRequest.for_text(
        1,
        candidate_text,
        artifact_revision_id=candidate.revision_id,
        story_contract_id=(
            story_contract.revision_id if story_contract is not None else ""
        ),
    )
    report = QualityLab.evaluate_deterministic(
        evaluation_request,
        candidate_text=candidate_text,
        continuity_findings=[],
        created_at="2026-08-24T00:00:00+00:00",
    )
    request = PromotionRequest(
        project_id=project.name,
        project_instance_id=ensure_project_instance_id(project),
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
        story_contract_revision_id=(
            story_contract.revision_id if story_contract is not None else None
        ),
    )
    return request, old, candidate


def _promotion_request(
    project: Path,
    *,
    expected_revision_id: str,
    expected_sha256: str,
    base_canon_sha: str,
    key: str,
    text: str,
):
    artifacts = ArtifactStore(project)
    candidate = artifacts.put_text(
        chapter=1,
        kind="final",
        text=text,
        source="editor",
        parent_revision_id=expected_revision_id,
    )
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=candidate.sha256,
        delta={"key_events": [text]},
        timestamp="2026-08-24T00:00:00+00:00",
    )
    ProposalStore(project).save(proposal)
    evaluation_request = EvaluationRequest.for_text(
        1, text, artifact_revision_id=candidate.revision_id
    )
    report = QualityLab.evaluate_deterministic(
        evaluation_request,
        candidate_text=text,
        continuity_findings=[],
        created_at="2026-08-24T00:00:00+00:00",
    )
    request = PromotionRequest(
        project_id=project.name,
        project_instance_id=ensure_project_instance_id(project),
        chapter=1,
        candidate_revision_id=candidate.revision_id,
        candidate_sha256=candidate.sha256,
        evaluation_report=report,
        canon_proposal_id=proposal.proposal_id,
        expected_final_revision_id=expected_revision_id,
        expected_final_sha256=expected_sha256,
        base_canon_sha=base_canon_sha,
        idempotency_key=key,
        actor="author",
        reason="approved",
    )
    return request, candidate


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


def _process_promote(project: str, request_data: dict, queue, barrier=None) -> None:
    try:
        request = PromotionRequest.from_dict(request_data)
        if barrier is not None:
            barrier.wait(timeout=5)
        receipt = PromotionService(Path(project)).promote(request)
    except Exception as exc:  # noqa: BLE001 - subprocess reports the exact outcome
        queue.put(("error", type(exc).__name__, str(exc)))
    else:
        queue.put(("ok", receipt.receipt_id, receipt.new_artifact_revision_id))


def _legacy_json_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _legacy_content_id(prefix: str, value: dict) -> str:
    return f"{prefix}-{hashlib.sha256(_legacy_json_bytes(value)).hexdigest()}"


def _legacy_record(kind: str, payload: dict) -> bytes:
    return _legacy_json_bytes(
        {
            kind: payload,
            "record_sha256": hashlib.sha256(_legacy_json_bytes(payload)).hexdigest(),
        }
    ) + b"\n"


def _request_as_c546849(request: dict) -> dict:
    identity = {
        field: value
        for field, value in request.items()
        if field not in {"request_id", "project_instance_id"}
    }
    identity["schema_version"] = 1
    return {
        "request_id": _legacy_content_id("promotion-request", identity),
        **identity,
    }


def _rewrite_transaction_as_c546849(project: Path, key: str) -> tuple[dict, dict]:
    """Rewrite one generated transaction to the exact pre-identity v1 format."""
    state_dir = project / "outputs/state"
    journal_path = state_dir / "promotion_journal" / f"{key}.json"
    journal = json.loads(journal_path.read_bytes())["journal"]

    request = _request_as_c546849(journal["request"])
    request_id = request["request_id"]

    legacy_state_payload = copy.deepcopy(journal["state_payload"])
    for item in legacy_state_payload["session_log"]:
        if item.get("timestamp") == journal["receipt"]["committed_at"]:
            item["timestamp"] = "2026-08-24T00:00:00"
    chapter_payload = legacy_state_payload["chapters"].get("1")
    if chapter_payload is not None:
        chapter_payload["last_modified"] = "2026-08-24T00:00:00"
        if "validated_at" in chapter_payload["continuity_checks"]:
            chapter_payload["continuity_checks"]["validated_at"] = (
                "2026-08-24T00:00:00"
            )
    for thread in legacy_state_payload["plot_threads"].values():
        for milestone in thread["milestones"]:
            if milestone.get("timestamp") == journal["receipt"]["committed_at"]:
                milestone["timestamp"] = "2026-08-24T00:00:00"
    legacy_new_canon_sha = canonical_canon_sha(legacy_state_payload)

    receipt = {
        field: value
        for field, value in journal["receipt"].items()
        if field not in {"receipt_id", "project_instance_id"}
    }
    receipt["request_id"] = request_id
    receipt["new_canon_sha"] = legacy_new_canon_sha
    receipt["schema_version"] = 1
    receipt_id = _legacy_content_id("promotion-receipt", receipt)
    receipt = {"receipt_id": receipt_id, **receipt}

    entry = {
        field: value
        for field, value in journal["ledger_entry"].items()
        if field not in {"entry_id", "project_instance_id"}
    }
    entry["request_id"] = request_id
    entry["receipt_id"] = receipt_id
    entry["new_canon_sha"] = legacy_new_canon_sha
    entry["schema_version"] = 1
    entry_id = _legacy_content_id("canon-entry", entry)
    entry = {"entry_id": entry_id, **entry}

    legacy_journal = {
        "schema_version": 1,
        "state": journal["state"],
        "request": request,
        "receipt": receipt,
        "ledger_entry": entry,
        "state_payload": legacy_state_payload,
    }
    journal_path.write_bytes(_legacy_record("journal", legacy_journal))

    if journal["state"] != "prepared":
        (state_dir / "story_state.json").write_bytes(
            _legacy_json_bytes(legacy_state_payload) + b"\n"
        )

    receipt_path = state_dir / "promotion_receipts" / f"{key}.json"
    if receipt_path.exists():
        receipt_path.write_bytes(_legacy_record("receipt", receipt))

    ledger_path = state_dir / "canon_ledger.jsonl"
    if ledger_path.exists():
        ledger_payload = {
            "entry": entry,
            "record_sha256": hashlib.sha256(_legacy_json_bytes(entry)).hexdigest(),
        }
        ledger_path.write_bytes(_legacy_json_bytes(ledger_payload) + b"\n")

    (state_dir / "project_identity.json").unlink()
    return request, receipt


def _legacy_transaction_at(
    project: Path,
    state: str,
    *,
    with_plot_thread_milestone: bool = False,
    with_existing_session_log: bool = False,
    with_continuity_status: bool = False,
) -> tuple[dict, dict]:
    request, _old, _candidate = _promotion_fixture(
        project,
        with_plot_thread_milestone=with_plot_thread_milestone,
        with_existing_session_log=with_existing_session_log,
        with_continuity_status=with_continuity_status,
    )
    failpoint = {
        "prepared": "before_state_commit",
        "state_committed": "after_state_commit",
        "head_committed": "after_head_commit",
    }.get(state)

    if failpoint is None:
        PromotionService(project).promote(request)
    else:

        def stop(point):
            if point == failpoint:
                raise RuntimeError(f"stop at {point}")

        with pytest.raises(RuntimeError, match=failpoint):
            PromotionService(project, fault_injector=stop).promote(request)

    journal_path = project / "outputs/state/promotion_journal/promotion-1.json"
    assert json.loads(journal_path.read_bytes())["journal"]["state"] == state
    return _rewrite_transaction_as_c546849(project, request.idempotency_key)


def _transaction_files(project: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(project)): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file() and path.name != "project_identity.json"
    }


def test_c546849_committed_receipt_load_and_retry_preserve_v1_content_ids(tmp_path):
    project = tmp_path / "project"
    request_data, receipt_data = _legacy_transaction_at(project, "committed")
    state_dir = project / "outputs/state"
    immutable_before = {
        name: (state_dir / name).read_bytes()
        for name in (
            "promotion_journal/promotion-1.json",
            "promotion_receipts/promotion-1.json",
            "canon_ledger.jsonl",
        )
    }

    loaded = PromotionService(project).load_receipt("promotion-1")
    retried = PromotionService(project).promote(
        PromotionRequest.from_dict(request_data)
    )

    assert loaded == retried == PromotionReceipt.from_dict(receipt_data)
    assert loaded.schema_version == 1
    assert loaded.project_instance_id == ""
    assert loaded.request_id == request_data["request_id"]
    assert loaded.receipt_id == receipt_data["receipt_id"]
    assert load_project_instance_id(project)
    persisted = json.loads(
        (project / "outputs/state/promotion_journal/promotion-1.json").read_bytes()
    )["journal"]
    assert persisted["schema_version"] == 1
    assert "base_state_payload" not in persisted
    assert persisted["request"] == request_data
    assert persisted["receipt"] == receipt_data
    assert {
        name: (state_dir / name).read_bytes() for name in immutable_before
    } == immutable_before


@pytest.mark.parametrize("state", ["prepared", "state_committed", "head_committed"])
def test_c546849_unfinished_journal_recovers_after_identity_initialization(
    tmp_path, state
):
    project = tmp_path / "project"
    request_data, receipt_data = _legacy_transaction_at(project, state)

    receipt = PromotionService(project).promote(
        PromotionRequest.from_dict(request_data)
    )

    assert receipt == PromotionReceipt.from_dict(receipt_data)
    assert receipt.schema_version == 1
    assert receipt.project_instance_id == ""
    assert CanonLedger(project).history()[0].schema_version == 1
    assert CanonLedger(project).history()[0].project_instance_id == ""
    persisted = json.loads(
        (project / "outputs/state/promotion_journal/promotion-1.json").read_bytes()
    )["journal"]
    assert persisted["state"] == "committed"
    assert persisted["schema_version"] == 1
    assert "base_state_payload" not in persisted
    assert persisted["request"] == request_data
    assert persisted["receipt"] == receipt_data


def test_c546849_prepared_replay_uses_persisted_milestone_timestamp(tmp_path):
    project = tmp_path / "project"
    request_data, receipt_data = _legacy_transaction_at(
        project,
        "prepared",
        with_plot_thread_milestone=True,
    )

    receipt = PromotionService(project).promote(
        PromotionRequest.from_dict(request_data)
    )

    assert receipt == PromotionReceipt.from_dict(receipt_data)
    milestone = StoryState(str(project)).plot_threads["north-door"].milestones[0]
    assert milestone["timestamp"] == "2026-08-24T00:00:00"


@pytest.mark.parametrize(
    "field_class",
    [
        "compile_styles",
        "binder",
        "existing_session_log",
        "new_session_log",
        "last_saved",
        "canonical_revision_id",
        "last_evaluation_id",
        "quality_scores",
        "metadata_operational",
    ],
)
def test_c546849_prepared_rejects_rehashed_noncanonical_projection_tamper(
    tmp_path, field_class
):
    project = tmp_path / "project"
    request_data, _receipt_data = _legacy_transaction_at(
        project,
        "prepared",
        with_existing_session_log=True,
    )
    journal_path = project / "outputs/state/promotion_journal/promotion-1.json"
    journal = json.loads(journal_path.read_bytes())["journal"]
    state_payload = journal["state_payload"]
    chapter = state_payload["chapters"]["1"]
    if field_class == "compile_styles":
        state_payload["compile_styles"]["attacker"] = {"font": "Forged"}
    elif field_class == "binder":
        state_payload["binder"][0]["synopsis"] = "Forged"
    elif field_class == "existing_session_log":
        state_payload["session_log"][0]["details"]["stable"] = False
    elif field_class == "new_session_log":
        state_payload["session_log"][-1]["action"] = "forged"
    elif field_class == "last_saved":
        state_payload["last_saved"] = "2026-08-24T01:00:00+00:00"
    elif field_class == "canonical_revision_id":
        chapter["canonical_revision_id"] = "f" * 64
    elif field_class == "last_evaluation_id":
        chapter["last_evaluation_id"] = "evaluation-forged"
    elif field_class == "quality_scores":
        chapter["quality_scores"]["voice"] = 10.0
    else:
        state_payload["metadata"]["updated_at"] = "2026-08-24T01:00:00"
    journal_path.write_bytes(_legacy_record("journal", journal))
    before = _transaction_files(project)

    with pytest.raises(PromotionCorruptionError, match="legacy|projection|replay"):
        PromotionService(project).promote(PromotionRequest.from_dict(request_data))

    assert _transaction_files(project) == before


@pytest.mark.parametrize(
    "timestamp_class",
    [
        "chapter_last_modified",
        "continuity_validated_at",
        "new_session_log",
        "new_plot_milestone",
    ],
)
def test_c546849_prepared_rejects_invalid_allowed_timestamp(
    tmp_path, timestamp_class
):
    project = tmp_path / "project"
    request_data, _receipt_data = _legacy_transaction_at(
        project,
        "prepared",
        with_plot_thread_milestone=True,
        with_existing_session_log=True,
        with_continuity_status=True,
    )
    journal_path = project / "outputs/state/promotion_journal/promotion-1.json"
    journal = json.loads(journal_path.read_bytes())["journal"]
    state_payload = journal["state_payload"]
    chapter = state_payload["chapters"]["1"]
    if timestamp_class == "chapter_last_modified":
        chapter["last_modified"] = "not-a-timestamp"
    elif timestamp_class == "continuity_validated_at":
        chapter["continuity_checks"]["validated_at"] = "not-a-timestamp"
    elif timestamp_class == "new_session_log":
        state_payload["session_log"][-1]["timestamp"] = "not-a-timestamp"
    else:
        state_payload["plot_threads"]["north-door"]["milestones"][-1][
            "timestamp"
        ] = "not-a-timestamp"
        new_canon_sha = canonical_canon_sha(state_payload)
        receipt_identity = {
            **{
                key: value
                for key, value in journal["receipt"].items()
                if key != "receipt_id"
            },
            "new_canon_sha": new_canon_sha,
        }
        receipt_id = _legacy_content_id("promotion-receipt", receipt_identity)
        journal["receipt"] = {"receipt_id": receipt_id, **receipt_identity}
        entry_identity = {
            **{
                key: value
                for key, value in journal["ledger_entry"].items()
                if key != "entry_id"
            },
            "receipt_id": receipt_id,
            "new_canon_sha": new_canon_sha,
        }
        journal["ledger_entry"] = {
            "entry_id": _legacy_content_id("canon-entry", entry_identity),
            **entry_identity,
        }
    journal_path.write_bytes(_legacy_record("journal", journal))
    before = _transaction_files(project)

    with pytest.raises(PromotionCorruptionError, match="timestamp|ISO datetime"):
        PromotionService(project).promote(PromotionRequest.from_dict(request_data))

    assert _transaction_files(project) == before


def test_new_v2_promotion_extends_c546849_v1_ledger_chain(tmp_path):
    project = tmp_path / "project"
    _request_data, first_receipt_data = _legacy_transaction_at(project, "committed")
    first_receipt = PromotionService(project).load_receipt("promotion-1")
    second_request, _candidate = _promotion_request(
        project,
        expected_revision_id=first_receipt.new_artifact_revision_id,
        expected_sha256=first_receipt.new_artifact_sha256,
        base_canon_sha=first_receipt.new_canon_sha,
        key="promotion-2",
        text="Mara locked the north door behind her.",
    )

    second_receipt = PromotionService(project).promote(second_request)
    history = CanonLedger(project).history()

    assert first_receipt == PromotionReceipt.from_dict(first_receipt_data)
    assert [entry.schema_version for entry in history] == [1, 2]
    assert history[1].previous_entry_id == history[0].entry_id
    assert history[1].project_instance_id == load_project_instance_id(project)
    assert second_receipt.schema_version == 2
    assert second_receipt.project_instance_id == load_project_instance_id(project)


def test_unpersisted_c546849_request_is_identity_bound_as_v2(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    legacy_request_data = _request_as_c546849(request.to_dict())
    (project / "outputs/state/project_identity.json").unlink()

    receipt = PromotionService(project).promote(
        PromotionRequest.from_dict(legacy_request_data)
    )

    instance_id = load_project_instance_id(project)
    persisted = json.loads(
        (project / "outputs/state/promotion_journal/promotion-1.json").read_bytes()
    )["journal"]
    assert receipt.schema_version == 2
    assert receipt.project_instance_id == instance_id
    assert receipt.request_id != legacy_request_data["request_id"]
    assert persisted["schema_version"] == 2
    assert persisted["request"]["schema_version"] == 2
    assert persisted["request"]["project_instance_id"] == instance_id
    assert "base_state_payload" in persisted


def test_c546849_ledger_entry_is_read_compatible_but_not_newly_appendable(tmp_path):
    project = tmp_path / "project"
    _legacy_transaction_at(project, "committed")
    state_dir = project / "outputs/state"
    journal = json.loads(
        (state_dir / "promotion_journal/promotion-1.json").read_bytes()
    )["journal"]
    legacy_entry = CanonLedgerEntry.from_dict(journal["ledger_entry"])
    (state_dir / "canon_ledger.jsonl").unlink()
    ensure_project_instance_id(project)

    with pytest.raises(CanonCorruptionError, match="project identity"):
        CanonLedger(project).append(legacy_entry)


def test_c546849_parsers_keep_strict_v1_field_sets(tmp_path):
    project = tmp_path / "project"
    request_data, receipt_data = _legacy_transaction_at(project, "committed")
    journal = json.loads(
        (project / "outputs/state/promotion_journal/promotion-1.json").read_bytes()
    )["journal"]

    for parser, record in (
        (PromotionRequest.from_dict, request_data),
        (PromotionReceipt.from_dict, receipt_data),
        (CanonLedgerEntry.from_dict, journal["ledger_entry"]),
    ):
        with pytest.raises(ValueError, match="invalid fields"):
            parser({**record, "unexpected": True})


def test_pure_c546849_ledger_history_does_not_require_or_create_identity(tmp_path):
    project = tmp_path / "project"
    identity = {
        "previous_entry_id": None,
        "proposal_id": f"proposal-{'a' * 64}",
        "source_artifact_sha": "b" * 64,
        "base_canon_sha": "c" * 64,
        "new_canon_sha": "d" * 64,
        "chapter": 1,
        "receipt_id": f"promotion-receipt-{'e' * 64}",
        "request_id": f"promotion-request-{'f' * 64}",
        "idempotency_key": "legacy-direct-read",
        "committed_at": "2026-08-24T00:00:00+00:00",
        "schema_version": 1,
    }
    entry = {"entry_id": _legacy_content_id("canon-entry", identity), **identity}
    ledger_path = project / "outputs/state/canon_ledger.jsonl"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_bytes(
        _legacy_json_bytes(
            {
                "entry": entry,
                "record_sha256": hashlib.sha256(
                    _legacy_json_bytes(entry)
                ).hexdigest(),
            }
        )
        + b"\n"
    )

    assert CanonLedger(project).history()[0].to_dict() == entry
    assert not (project / "outputs/state/project_identity.json").exists()


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


def test_historical_idempotency_retry_survives_later_promotion(tmp_path):
    project = tmp_path / "project"
    first_request, _old, first_candidate = _promotion_fixture(project, key="first")
    service = PromotionService(project)
    first_receipt = service.promote(first_request)
    second_request, _second_candidate = _promotion_request(
        project,
        expected_revision_id=first_candidate.revision_id,
        expected_sha256=first_candidate.sha256,
        base_canon_sha=canonical_canon_sha(StoryState(str(project))),
        key="second",
        text="Mara locked the north door behind her.",
    )
    service.promote(second_request)

    retried = PromotionService(project).promote(first_request)

    assert retried == first_receipt
    assert len(CanonLedger(project).history()) == 2


def test_historical_idempotency_retry_survives_contract_head_advance(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(
        project, with_story_contract=True
    )
    receipt = PromotionService(project).promote(request)
    artifacts = ArtifactStore(project)
    previous_contract = artifacts.get_head(0, "story_contract")
    next_contract = artifacts.put_json(
        chapter=0,
        kind="story_contract",
        value={"premise": "The north door may open."},
        source="architect",
        parent_revision_id=previous_contract.revision_id,
    )
    artifacts.set_head(
        0,
        "story_contract",
        next_contract.revision_id,
        expected_revision_id=previous_contract.revision_id,
    )

    assert PromotionService(project).promote(request) == receipt


def test_public_load_rejects_rehashed_historical_receipt_not_bound_to_ledger(tmp_path):
    project = tmp_path / "project"
    first_request, _old, first_candidate = _promotion_fixture(project, key="first")
    first_receipt = PromotionService(project).promote(first_request)
    second_request, _second_candidate = _promotion_request(
        project,
        expected_revision_id=first_candidate.revision_id,
        expected_sha256=first_candidate.sha256,
        base_canon_sha=canonical_canon_sha(StoryState(str(project))),
        key="second",
        text="Mara locked the north door behind her.",
    )
    PromotionService(project).promote(second_request)
    forged_data = first_receipt.to_dict()
    forged_data["actor"] = "attacker"
    forged_data["receipt_id"] = ""
    forged = type(first_receipt).from_dict(forged_data)
    receipt_path = project / "outputs/state/promotion_receipts/first.json"
    receipt_path.write_bytes(
        PromotionService._record("receipt", forged.to_dict())
    )

    with pytest.raises(PromotionCorruptionError, match="canon ledger"):
        PromotionService(project).load_receipt("first")


def test_public_load_rejects_receipt_when_promoted_blob_is_corrupt(tmp_path):
    project = tmp_path / "project"
    request, _old, candidate = _promotion_fixture(project)
    PromotionService(project).promote(request)
    blob = project / "outputs/artifacts/sha256" / candidate.sha256
    blob.write_bytes(b"X" * len(blob.read_bytes()))

    with pytest.raises(PromotionCorruptionError, match="artifact"):
        PromotionService(project).load_receipt(request.idempotency_key)


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


def test_request_cannot_omit_story_contract_bound_to_candidate_and_evaluation(
    tmp_path,
):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(
        project, with_story_contract=True
    )
    omitted = replace(request, story_contract_revision_id=None, request_id="")
    before = _snapshot(project)

    with pytest.raises(StaleArtifactHead, match="story contract|story_contract"):
        PromotionService(project).promote(omitted)

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


@pytest.mark.parametrize("failpoint", ["after_state_commit", "after_head_commit"])
def test_irreversible_recovery_survives_story_contract_head_advance(
    tmp_path, failpoint
):
    project = tmp_path / "project"
    request, _old, candidate = _promotion_fixture(
        project, with_story_contract=True
    )

    def interrupt(point):
        if point == failpoint:
            raise RuntimeError(f"stop at {point}")

    with pytest.raises(RuntimeError, match=failpoint):
        PromotionService(project, fault_injector=interrupt).promote(request)
    journal_path = project / "outputs/state/promotion_journal/promotion-1.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))["journal"]
    expected_receipt = PromotionReceipt.from_dict(journal["receipt"])

    artifacts = ArtifactStore(project)
    previous_contract = artifacts.get_head(0, "story_contract")
    next_contract = artifacts.put_json(
        chapter=0,
        kind="story_contract",
        value={"premise": "The north door may open."},
        source="architect",
        parent_revision_id=previous_contract.revision_id,
    )
    artifacts.set_head(
        0,
        "story_contract",
        next_contract.revision_id,
        expected_revision_id=previous_contract.revision_id,
    )

    receipt = PromotionService(project).promote(request)

    assert receipt == expected_receipt
    history = CanonLedger(project).history()
    assert len(history) == 1
    assert history[0].receipt_id == receipt.receipt_id
    assert canonical_canon_sha(StoryState(str(project))) == receipt.new_canon_sha
    assert artifacts.get_head(1, "final").revision_id == candidate.revision_id
    assert artifacts.get_head(0, "story_contract").revision_id == next_contract.revision_id


def test_prepared_recovery_rejects_story_contract_head_advance_before_writes(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(
        project, with_story_contract=True
    )

    def stop_before_state(point):
        if point == "before_state_commit":
            raise RuntimeError("stop before state")

    with pytest.raises(RuntimeError, match="before state"):
        PromotionService(project, fault_injector=stop_before_state).promote(request)

    artifacts = ArtifactStore(project)
    previous_contract = artifacts.get_head(0, "story_contract")
    next_contract = artifacts.put_json(
        chapter=0,
        kind="story_contract",
        value={"premise": "The north door may open."},
        source="architect",
        parent_revision_id=previous_contract.revision_id,
    )
    artifacts.set_head(
        0,
        "story_contract",
        next_contract.revision_id,
        expected_revision_id=previous_contract.revision_id,
    )
    before = _snapshot(project)

    with pytest.raises(StaleArtifactHead, match="story_contract"):
        PromotionService(project).promote(request)

    assert _snapshot(project) == before
    assert artifacts.get_head(0, "story_contract").revision_id == next_contract.revision_id


def test_story_contract_head_advance_at_state_write_boundary_is_rejected(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(
        project, with_story_contract=True
    )
    artifacts = ArtifactStore(project)
    previous_contract = artifacts.get_head(0, "story_contract")
    next_contract = artifacts.put_json(
        chapter=0,
        kind="story_contract",
        value={"premise": "The north door may open."},
        source="architect",
        parent_revision_id=previous_contract.revision_id,
    )
    hook_ran = False

    def advance_contract_at_write_boundary(point):
        nonlocal hook_ran
        if point != "before_state_commit" or hook_ran:
            return
        artifacts.set_head(
            0,
            "story_contract",
            next_contract.revision_id,
            expected_revision_id=previous_contract.revision_id,
        )
        hook_ran = True

    before = _snapshot(project)

    with pytest.raises(StaleArtifactHead, match="story_contract"):
        PromotionService(
            project, fault_injector=advance_contract_at_write_boundary
        ).promote(request)

    assert hook_ran is True
    assert _snapshot(project) == before
    assert artifacts.get_head(0, "story_contract").revision_id == next_contract.revision_id


def test_final_head_advance_at_state_write_boundary_is_rejected(tmp_path):
    project = tmp_path / "project"
    request, old, _candidate = _promotion_fixture(project)
    artifacts = ArtifactStore(project)
    intervening = artifacts.put_text(
        chapter=1,
        kind="final",
        text="An intervening final.",
        source="author",
        parent_revision_id=old.revision_id,
    )
    hook_ran = False

    def advance_final_at_write_boundary(point):
        nonlocal hook_ran
        if point != "before_state_commit" or hook_ran:
            return
        artifacts.set_head(
            1,
            "final",
            intervening.revision_id,
            expected_revision_id=old.revision_id,
        )
        hook_ran = True

    with pytest.raises(StaleArtifactHead, match="final"):
        PromotionService(
            project, fault_injector=advance_final_at_write_boundary
        ).promote(request)

    assert hook_ran is True
    assert canonical_canon_sha(StoryState(str(project))) == request.base_canon_sha
    assert artifacts.get_head(1, "final").revision_id == intervening.revision_id
    assert CanonLedger(project).history() == ()


def test_canon_advance_at_state_write_boundary_is_not_overwritten(tmp_path):
    project = tmp_path / "project"
    request, old, _candidate = _promotion_fixture(project)
    hook_ran = False

    def advance_canon_at_write_boundary(point):
        nonlocal hook_ran
        if point != "before_state_commit" or hook_ran:
            return
        state = StoryState(str(project))
        state.story_bible["rule"] = "The north door may open."
        state.save_state()
        hook_ran = True

    with pytest.raises(StaleCanonError, match="canon"):
        PromotionService(
            project, fault_injector=advance_canon_at_write_boundary
        ).promote(request)

    assert hook_ran is True
    assert StoryState(str(project)).story_bible["rule"] == "The north door may open."
    assert ArtifactStore(project).get_head(1, "final").revision_id == old.revision_id
    assert CanonLedger(project).history() == ()


@pytest.mark.parametrize("resource_kind", ["candidate", "story_contract"])
def test_referenced_blob_corruption_at_state_write_boundary_is_rejected(
    tmp_path, resource_kind
):
    project = tmp_path / "project"
    request, old, candidate = _promotion_fixture(
        project, with_story_contract=True
    )
    artifacts = ArtifactStore(project)
    revision_id = (
        candidate.revision_id
        if resource_kind == "candidate"
        else request.story_contract_revision_id
    )
    revision = artifacts.get_revision(revision_id)
    blob = project / "outputs/artifacts/sha256" / revision.sha256
    hook_ran = False

    def corrupt_blob_at_write_boundary(point):
        nonlocal hook_ran
        if point != "before_state_commit" or hook_ran:
            return
        blob.write_bytes(b"X" * len(blob.read_bytes()))
        hook_ran = True

    with pytest.raises(PromotionCorruptionError, match="artifact"):
        PromotionService(
            project, fault_injector=corrupt_blob_at_write_boundary
        ).promote(request)

    assert hook_ran is True
    assert canonical_canon_sha(StoryState(str(project))) == request.base_canon_sha
    assert artifacts.get_head(1, "final").revision_id == old.revision_id
    assert CanonLedger(project).history() == ()


@pytest.mark.parametrize("failpoint", ["after_state_commit", "before_receipt_commit"])
def test_new_key_recovers_incomplete_journal_before_checking_its_base(
    tmp_path, failpoint
):
    project = tmp_path / "project"
    first_request, old, first_candidate = _promotion_fixture(project, key="first")
    second_request, _second_candidate = _promotion_request(
        project,
        expected_revision_id=old.revision_id,
        expected_sha256=old.sha256,
        base_canon_sha=first_request.base_canon_sha,
        key="second",
        text="Mara left the north door open.",
    )

    def interrupt(point):
        if point == failpoint:
            raise RuntimeError(f"stop at {point}")

    with pytest.raises(RuntimeError, match=failpoint):
        PromotionService(project, fault_injector=interrupt).promote(first_request)

    with pytest.raises((StaleArtifactHead, StaleCanonError)):
        PromotionService(project).promote(second_request)

    first_receipt = PromotionService(project).load_receipt("first")
    assert first_receipt is not None
    assert first_receipt.new_artifact_revision_id == first_candidate.revision_id
    assert PromotionService(project).promote(first_request) == first_receipt
    assert len(CanonLedger(project).history()) == 1
    receipt_files = list(
        (project / "outputs" / "state" / "promotion_receipts").glob("*.json")
    )
    assert [path.name for path in receipt_files] == ["first.json"]


def test_prepared_journal_with_changed_ledger_base_fails_before_projection_writes(
    tmp_path,
):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)

    def stop_before_state(point):
        if point == "before_state_commit":
            raise RuntimeError("stop before state")

    with pytest.raises(RuntimeError, match="before state"):
        PromotionService(project, fault_injector=stop_before_state).promote(request)
    journal_path = project / "outputs/state/promotion_journal/promotion-1.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))["journal"]
    planned = CanonLedgerEntry.from_dict(journal["ledger_entry"])
    rogue = CanonLedgerEntry(
        project_instance_id=planned.project_instance_id,
        previous_entry_id=None,
        proposal_id=planned.proposal_id,
        source_artifact_sha=planned.source_artifact_sha,
        base_canon_sha=planned.base_canon_sha,
        new_canon_sha="f" * 64,
        chapter=planned.chapter,
        receipt_id=planned.receipt_id,
        request_id=planned.request_id,
        idempotency_key="rogue",
        committed_at=planned.committed_at,
    )
    CanonLedger(project).append(rogue)
    before = _snapshot(project)

    with pytest.raises(CanonCorruptionError):
        PromotionService(project).promote(request)

    assert _snapshot(project) == before


def test_rehashed_prepared_journal_cannot_forge_derived_canon(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)

    def stop_before_state(point):
        if point == "before_state_commit":
            raise RuntimeError("stop before state")

    with pytest.raises(RuntimeError, match="before state"):
        PromotionService(project, fault_injector=stop_before_state).promote(request)

    journal_path = project / "outputs/state/promotion_journal/promotion-1.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))["journal"]
    journal["state_payload"]["metadata"]["title"] = "FORGED CANON"
    forged_canon_sha = canonical_canon_sha(journal["state_payload"])

    receipt_data = copy.deepcopy(journal["receipt"])
    receipt_data.update(new_canon_sha=forged_canon_sha, receipt_id="")
    forged_receipt = PromotionReceipt.from_dict(receipt_data)
    journal["receipt"] = forged_receipt.to_dict()

    entry_data = copy.deepcopy(journal["ledger_entry"])
    entry_data.update(
        new_canon_sha=forged_canon_sha,
        receipt_id=forged_receipt.receipt_id,
        entry_id="",
    )
    journal["ledger_entry"] = CanonLedgerEntry.from_dict(entry_data).to_dict()
    journal_path.write_bytes(PromotionService._record("journal", journal))
    before = _snapshot(project)

    with pytest.raises(
        PromotionCorruptionError, match="proposal|journal|derived canon"
    ):
        PromotionService(project).promote(request)

    assert _snapshot(project) == before
    assert StoryState(str(project)).metadata["title"] != "FORGED CANON"


def test_committed_v2_journal_replays_after_process_restart(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)

    first = PromotionService(project).promote(request)

    # A new service instance must validate the JSON-persisted journal, not just
    # the in-memory payload returned by the initial commit.
    restarted = PromotionService(project)
    assert restarted.promote(request) == first


def test_prepared_v2_journal_normalizes_chapter_keys_before_replay(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    state = StoryState(str(project))
    state.create_chapter(1)
    state.save_state()
    request = replace(
        request,
        base_canon_sha=canonical_canon_sha(state),
        request_id="",
    )

    journal = PromotionService(project)._prepare(request)

    assert all(isinstance(key, str) for key in journal["state_payload"]["chapters"])


def test_rehashed_prepared_journal_cannot_persist_underived_state_projection(
    tmp_path,
):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)

    def stop_before_state(point):
        if point == "before_state_commit":
            raise RuntimeError("stop before state")

    with pytest.raises(RuntimeError, match="before state"):
        PromotionService(project, fault_injector=stop_before_state).promote(request)

    journal_path = project / "outputs/state/promotion_journal/promotion-1.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))["journal"]
    original_compile_styles = dict(journal["base_state_payload"]["compile_styles"])
    journal["state_payload"]["compile_styles"] = {"attacker": "forged"}
    journal_path.write_bytes(PromotionService._record("journal", journal))
    before = _snapshot(project)

    try:
        PromotionService(project).promote(request)
    except PromotionCorruptionError:
        assert _snapshot(project) == before
        persisted_journal = json.loads(journal_path.read_text(encoding="utf-8"))["journal"]
        assert persisted_journal["state"] == "prepared"
    else:
        assert StoryState(str(project)).compile_styles == original_compile_styles

    assert StoryState(str(project)).compile_styles != {"attacker": "forged"}


@pytest.mark.parametrize("failpoint", ["after_state_commit", "after_head_commit"])
def test_incomplete_journal_with_changed_ledger_base_makes_no_further_writes(
    tmp_path, failpoint
):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)

    def stop(point):
        if point == failpoint:
            raise RuntimeError(f"stop at {point}")

    with pytest.raises(RuntimeError, match=failpoint):
        PromotionService(project, fault_injector=stop).promote(request)
    journal_path = project / "outputs/state/promotion_journal/promotion-1.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))["journal"]
    planned = CanonLedgerEntry.from_dict(journal["ledger_entry"])
    CanonLedger(project).append(
        CanonLedgerEntry(
            project_instance_id=planned.project_instance_id,
            previous_entry_id=None,
            proposal_id=planned.proposal_id,
            source_artifact_sha=planned.source_artifact_sha,
            base_canon_sha=planned.base_canon_sha,
            new_canon_sha="e" * 64,
            chapter=planned.chapter,
            receipt_id=planned.receipt_id,
            request_id=planned.request_id,
            idempotency_key="rogue",
            committed_at=planned.committed_at,
        )
    )
    before = _snapshot(project)

    with pytest.raises(CanonCorruptionError):
        PromotionService(project).promote(request)

    assert _snapshot(project) == before


def test_head_committed_journal_with_changed_canon_fails_before_ledger_write(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)

    def stop_after_head(point):
        if point == "after_head_commit":
            raise RuntimeError("stop after head")

    with pytest.raises(RuntimeError, match="after head"):
        PromotionService(project, fault_injector=stop_after_head).promote(request)
    state = StoryState(str(project))
    state.metadata["title"] = "Tampered canon"
    state.save_state()
    before = _snapshot(project)

    with pytest.raises(PromotionCorruptionError, match="canon"):
        PromotionService(project).promote(request)

    assert _snapshot(project) == before


def test_head_committed_journal_with_corrupt_blob_fails_before_ledger_write(tmp_path):
    project = tmp_path / "project"
    request, _old, candidate = _promotion_fixture(project)

    def stop_after_head(point):
        if point == "after_head_commit":
            raise RuntimeError("stop after head")

    with pytest.raises(RuntimeError, match="after head"):
        PromotionService(project, fault_injector=stop_after_head).promote(request)
    blob = project / "outputs/artifacts/sha256" / candidate.sha256
    blob.write_bytes(b"X" * len(blob.read_bytes()))
    before = _snapshot(project)

    with pytest.raises(PromotionCorruptionError, match="artifact"):
        PromotionService(project).promote(request)

    assert _snapshot(project) == before


def test_state_committed_recovery_rejects_corrupt_referenced_story_contract(
    tmp_path,
):
    project = tmp_path / "project"
    request, old, _candidate = _promotion_fixture(
        project, with_story_contract=True
    )

    def stop_after_state(point):
        if point == "after_state_commit":
            raise RuntimeError("stop after state")

    with pytest.raises(RuntimeError, match="after state"):
        PromotionService(project, fault_injector=stop_after_state).promote(request)

    journal_path = project / "outputs/state/promotion_journal/promotion-1.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))["journal"]
    assert journal["state"] == "state_committed"
    artifacts = ArtifactStore(project)
    contract = artifacts.get_revision(request.story_contract_revision_id)
    contract_blob = project / "outputs/artifacts/sha256" / contract.sha256
    contract_blob.write_bytes(b"X" * len(contract_blob.read_bytes()))
    before = _snapshot(project)

    with pytest.raises(PromotionCorruptionError, match="contract|artifact"):
        PromotionService(project).promote(request)

    assert _snapshot(project) == before
    assert ArtifactStore(project).get_head(1, "final").revision_id == old.revision_id
    assert CanonLedger(project).history() == ()
    assert not (project / "outputs/state/promotion_receipts/promotion-1.json").exists()
    persisted_journal = json.loads(journal_path.read_text(encoding="utf-8"))["journal"]
    assert persisted_journal["state"] == "state_committed"


def test_renamed_raw_replica_accepts_original_receipt(tmp_path):
    source = tmp_path / "source-project"
    request, _old, _candidate = _promotion_fixture(source)
    receipt = PromotionService(source).promote(request)
    target = tmp_path / "target-project"
    shutil.copytree(source, target)

    assert PromotionService(target).load_receipt(request.idempotency_key) == receipt


def test_renamed_raw_replica_resumes_original_prepared_journal(tmp_path):
    source = tmp_path / "source-project"
    request, _old, _candidate = _promotion_fixture(source)

    def stop_before_state(point):
        if point == "before_state_commit":
            raise RuntimeError("stop before state")

    with pytest.raises(RuntimeError, match="before state"):
        PromotionService(source, fault_injector=stop_before_state).promote(request)
    target = tmp_path / "target-project"
    shutil.copytree(source, target)

    receipt = PromotionService(target).promote(request)

    assert receipt.project_instance_id == request.project_instance_id
    assert len(CanonLedger(target).history()) == 1


def _copy_as_distinct_same_named_project(source: Path, target: Path) -> str:
    shutil.copytree(source, target)
    return fork_project_instance_id(target)


def test_same_named_distinct_project_rejects_copied_receipt(tmp_path):
    source = tmp_path / "left/project"
    request, _old, _candidate = _promotion_fixture(source)
    target = tmp_path / "right/project"
    _copy_as_distinct_same_named_project(source, target)
    PromotionService(source).promote(request)
    source_receipt = source / "outputs/state/promotion_receipts/promotion-1.json"
    target_receipt = target / "outputs/state/promotion_receipts/promotion-1.json"
    target_receipt.parent.mkdir()
    shutil.copy2(source_receipt, target_receipt)

    with pytest.raises(PromotionCorruptionError, match="project"):
        PromotionService(target).load_receipt(request.idempotency_key)


def test_same_named_distinct_project_rejects_copied_prepared_journal(tmp_path):
    source = tmp_path / "left/project"
    request, _old, _candidate = _promotion_fixture(source)
    target = tmp_path / "right/project"
    target_instance_id = _copy_as_distinct_same_named_project(source, target)

    def stop_before_state(point):
        if point == "before_state_commit":
            raise RuntimeError("stop before state")

    with pytest.raises(RuntimeError, match="before state"):
        PromotionService(source, fault_injector=stop_before_state).promote(request)
    source_journal = source / "outputs/state/promotion_journal/promotion-1.json"
    target_journal = target / "outputs/state/promotion_journal/promotion-1.json"
    target_journal.parent.mkdir()
    shutil.copy2(source_journal, target_journal)
    target_request = replace(
        request,
        project_instance_id=target_instance_id,
        request_id="",
    )
    before = _snapshot(target)

    with pytest.raises(PromotionCorruptionError, match="project"):
        PromotionService(target).promote(target_request)

    assert _snapshot(target) == before


def test_fork_identity_refuses_project_with_promotion_history(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    original = load_project_instance_id(project)
    PromotionService(project).promote(request)

    with pytest.raises(ProjectIdentityError, match="history"):
        fork_project_instance_id(project)

    assert load_project_instance_id(project) == original


def test_foreign_sparse_journal_rejected_before_preflight_creates_storage(tmp_path):
    source = tmp_path / "left/project"
    request, _old, _candidate = _promotion_fixture(source)

    def stop_before_state(point):
        if point == "before_state_commit":
            raise RuntimeError("stop before state")

    with pytest.raises(RuntimeError, match="before state"):
        PromotionService(source, fault_injector=stop_before_state).promote(request)
    target = tmp_path / "right/project"
    target_instance_id = ensure_project_instance_id(target)
    target_request = replace(
        request,
        project_instance_id=target_instance_id,
        request_id="",
    )
    target_journal = target / "outputs/state/promotion_journal/promotion-1.json"
    target_journal.parent.mkdir()
    shutil.copy2(
        source / "outputs/state/promotion_journal/promotion-1.json",
        target_journal,
    )

    with pytest.raises(PromotionCorruptionError, match="project"):
        PromotionService(target).promote(target_request)

    assert not (target / "outputs/artifacts").exists()
    assert not (target / "outputs/state/promotion_receipts").exists()


def test_missing_receipt_for_committed_journal_blocks_new_key(tmp_path):
    project = tmp_path / "project"
    first_request, _old, first_candidate = _promotion_fixture(project, key="first")
    PromotionService(project).promote(first_request)
    second_request, _second_candidate = _promotion_request(
        project,
        expected_revision_id=first_candidate.revision_id,
        expected_sha256=first_candidate.sha256,
        base_canon_sha=canonical_canon_sha(StoryState(str(project))),
        key="second",
        text="Mara locked the north door behind her.",
    )
    (project / "outputs/state/promotion_receipts/first.json").unlink()
    before = _snapshot(project)

    with pytest.raises(PromotionCorruptionError, match="receipt"):
        PromotionService(project).promote(second_request)

    assert _snapshot(project) == before


def test_orphan_receipt_without_journal_or_ledger_blocks_new_key(tmp_path):
    project = tmp_path / "project"
    first_request, _old, first_candidate = _promotion_fixture(project, key="first")
    PromotionService(project).promote(first_request)
    second_request, _second_candidate = _promotion_request(
        project,
        expected_revision_id=first_candidate.revision_id,
        expected_sha256=first_candidate.sha256,
        base_canon_sha=canonical_canon_sha(StoryState(str(project))),
        key="second",
        text="Mara locked the north door behind her.",
    )
    (project / "outputs/state/canon_ledger.jsonl").unlink()
    (project / "outputs/state/promotion_journal/first.json").unlink()
    before = _snapshot(project)

    with pytest.raises(PromotionCorruptionError, match="receipt.*ledger"):
        PromotionService(project).promote(second_request)

    assert _snapshot(project) == before


@pytest.mark.parametrize("field", ["request", "ledger_entry", "state_payload"])
def test_rehashed_committed_journal_field_tamper_is_rejected(tmp_path, field):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    PromotionService(project).promote(request)
    journal_path = project / "outputs/state/promotion_journal/promotion-1.json"
    payload = json.loads(journal_path.read_text(encoding="utf-8"))["journal"]
    if field == "request":
        forged_data = dict(payload["request"])
        forged_data["reason"] = "forged"
        forged_data["request_id"] = ""
        payload["request"] = PromotionRequest.from_dict(forged_data).to_dict()
    elif field == "ledger_entry":
        forged_data = dict(payload["ledger_entry"])
        forged_data["idempotency_key"] = "forged"
        forged_data["entry_id"] = ""
        payload["ledger_entry"] = CanonLedgerEntry.from_dict(forged_data).to_dict()
    else:
        payload["state_payload"]["metadata"]["title"] = "Forged title"
    journal_path.write_bytes(PromotionService._record("journal", payload))
    before = _snapshot(project)

    with pytest.raises(PromotionCorruptionError, match="journal"):
        PromotionService(project).promote(request)

    assert _snapshot(project) == before


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlinks")
def test_project_lock_rejects_symlink_in_missing_project_prefix(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    linked_parent = trusted / "linked-parent"
    linked_parent.symlink_to(external, target_is_directory=True)
    missing_project = linked_parent / "new-project"

    with pytest.raises(ProjectLockError, match="symlink"):
        with ProjectLock(missing_project):
            pass

    assert list(external.iterdir()) == []


@pytest.mark.skipif(
    not Path("/var").is_symlink(), reason="requires the standard macOS /var alias"
)
def test_project_lock_accepts_standard_macos_var_alias(tmp_path):
    private_var = Path("/private/var")
    alias_root = Path("/var") / tmp_path.relative_to(private_var) / "alias-project"

    with ProjectLock(alias_root) as lock:
        assert lock.path == alias_root / "outputs/state/.promotion.lock"


def test_ledger_partial_write_failure_leaves_no_partial_record_and_retry_recovers(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    request, _old, candidate = _promotion_fixture(project)
    original_write = canon_ledger_module.os.write
    calls = 0

    def short_write_then_fail(descriptor, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, data[: max(1, len(data) // 3)])
        raise OSError("simulated ledger write failure")

    monkeypatch.setattr(canon_ledger_module.os, "write", short_write_then_fail)
    with pytest.raises(OSError, match="ledger write failure"):
        PromotionService(project).promote(request)
    monkeypatch.setattr(canon_ledger_module.os, "write", original_write)

    ledger_path = project / "outputs" / "state" / "canon_ledger.jsonl"
    assert not ledger_path.exists()
    receipt = PromotionService(project).promote(request)
    assert receipt.new_artifact_revision_id == candidate.revision_id
    assert CanonLedger(project).history()[0].receipt_id == receipt.receipt_id


def test_ledger_partial_write_preserves_existing_history_and_retry_recovers(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    first_request, _old, first_candidate = _promotion_fixture(project, key="first")
    PromotionService(project).promote(first_request)
    ledger_path = project / "outputs" / "state" / "canon_ledger.jsonl"
    ledger_before = ledger_path.read_bytes()
    second_request, second_candidate = _promotion_request(
        project,
        expected_revision_id=first_candidate.revision_id,
        expected_sha256=first_candidate.sha256,
        base_canon_sha=canonical_canon_sha(StoryState(str(project))),
        key="second",
        text="Mara locked the north door behind her.",
    )
    original_write = canon_ledger_module.os.write
    calls = 0

    def short_write_then_fail(descriptor, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, data[: max(1, len(data) // 3)])
        raise OSError("simulated ledger write failure")

    monkeypatch.setattr(canon_ledger_module.os, "write", short_write_then_fail)
    with pytest.raises(OSError, match="ledger write failure"):
        PromotionService(project).promote(second_request)
    monkeypatch.setattr(canon_ledger_module.os, "write", original_write)

    assert ledger_path.read_bytes() == ledger_before
    receipt = PromotionService(project).promote(second_request)
    assert receipt.new_artifact_revision_id == second_candidate.revision_id
    assert [entry.receipt_id for entry in CanonLedger(project).history()] == [
        PromotionService(project).load_receipt("first").receipt_id,
        receipt.receipt_id,
    ]


def test_ledger_directory_fsync_uncertainty_reconciles_committed_entry(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    request, _old, candidate = _promotion_fixture(project)
    ledger_path = project / "outputs/state/canon_ledger.jsonl"
    original_replace = canon_ledger_module.os.replace
    original_fsync = canon_ledger_module.os.fsync
    ledger_replaced = False
    failed_directory_fsync = False

    def track_replace(source, destination):
        nonlocal ledger_replaced
        result = original_replace(source, destination)
        if Path(destination) == ledger_path:
            ledger_replaced = True
        return result

    def fail_ledger_directory_fsync(descriptor):
        nonlocal failed_directory_fsync
        if (
            ledger_replaced
            and not failed_directory_fsync
            and stat.S_ISDIR(os.fstat(descriptor).st_mode)
        ):
            failed_directory_fsync = True
            raise OSError("simulated ledger directory fsync failure")
        return original_fsync(descriptor)

    monkeypatch.setattr(canon_ledger_module.os, "replace", track_replace)
    monkeypatch.setattr(canon_ledger_module.os, "fsync", fail_ledger_directory_fsync)

    receipt = PromotionService(project).promote(request)

    assert failed_directory_fsync is True
    assert receipt.new_artifact_revision_id == candidate.revision_id
    assert CanonLedger(project).history()[0].receipt_id == receipt.receipt_id
    assert PromotionService(project).promote(request) == receipt


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
    lock_path.unlink()
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


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlinks")
@pytest.mark.parametrize("directory_name", ["promotion_journal", "promotion_receipts"])
def test_non_json_symlink_in_transaction_directory_is_rejected_before_changes(
    tmp_path, directory_name
):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    managed_dir = project / "outputs" / "state" / directory_name
    managed_dir.mkdir(parents=True, exist_ok=True)
    external = tmp_path / f"external-entry-{directory_name}"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("outside", encoding="utf-8")
    external_before = [(path.name, path.read_bytes()) for path in external.iterdir()]
    ignored = managed_dir / "ignored.tmp"
    ignored.symlink_to(external, target_is_directory=True)
    before = _snapshot(project)

    with pytest.raises(PromotionCorruptionError, match="symlink|storage"):
        PromotionService(project).promote(request)

    assert _snapshot(project) == before
    assert [(path.name, path.read_bytes()) for path in external.iterdir()] == external_before


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlinks")
@pytest.mark.parametrize("directory_name", ["artifacts", "sha256"])
def test_symlinked_artifact_directory_is_rejected_before_promotion(
    tmp_path, directory_name
):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    artifact_root = project / "outputs" / "artifacts"
    target = artifact_root if directory_name == "artifacts" else artifact_root / "sha256"
    external = tmp_path / f"external-{directory_name}"
    target.rename(external)
    target.symlink_to(external, target_is_directory=True)
    state_before = (project / "outputs" / "state" / "story_state.json").read_bytes()

    with pytest.raises(PromotionCorruptionError, match="artifact"):
        PromotionService(project).promote(request)

    assert (project / "outputs" / "state" / "story_state.json").read_bytes() == state_before
    assert CanonLedger(project).history() == ()
    assert not (project / "outputs" / "state" / "promotion_receipts").exists()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlinks")
@pytest.mark.parametrize("managed_file", ["heads", "revisions", "candidate_blob"])
def test_symlinked_artifact_file_is_rejected_before_promotion(tmp_path, managed_file):
    project = tmp_path / "project"
    request, _old, candidate = _promotion_fixture(project)
    artifact_root = project / "outputs" / "artifacts"
    paths = {
        "heads": artifact_root / "heads.json",
        "revisions": artifact_root / "revisions.jsonl",
        "candidate_blob": artifact_root / "sha256" / candidate.sha256,
    }
    target = paths[managed_file]
    external = tmp_path / f"external-{managed_file}"
    target.rename(external)
    target.symlink_to(external)
    external_before = external.read_bytes()
    state_before = (project / "outputs" / "state" / "story_state.json").read_bytes()

    with pytest.raises(PromotionCorruptionError, match="artifact"):
        PromotionService(project).promote(request)

    assert external.read_bytes() == external_before
    assert (project / "outputs" / "state" / "story_state.json").read_bytes() == state_before
    assert CanonLedger(project).history() == ()
    assert not (project / "outputs" / "state" / "promotion_receipts").exists()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlinks")
def test_symlinked_outputs_artifact_parent_is_rejected_before_promotion(tmp_path):
    project = tmp_path / "project"
    request, _old, _candidate = _promotion_fixture(project)
    outputs = project / "outputs"
    external = tmp_path / "external-outputs"
    outputs.rename(external)
    outputs.symlink_to(external, target_is_directory=True)
    state_path = external / "state/story_state.json"
    heads_path = external / "artifacts/heads.json"
    state_before = state_path.read_bytes()
    heads_before = heads_path.read_bytes()

    with pytest.raises(
        (ProjectLockError, PromotionCorruptionError), match="symlink|real directory"
    ):
        PromotionService(project).promote(request)

    assert state_path.read_bytes() == state_before
    assert heads_path.read_bytes() == heads_before
    assert not (external / "state/canon_ledger.jsonl").exists()
    assert not (external / "state/promotion_receipts").exists()


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


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX flock/fork")
def test_two_processes_different_keys_with_shared_base_commit_exactly_once(tmp_path):
    project = tmp_path / "project"
    first_request, old, _first_candidate = _promotion_fixture(project, key="first")
    second_request, _second_candidate = _promotion_request(
        project,
        expected_revision_id=old.revision_id,
        expected_sha256=old.sha256,
        base_canon_sha=first_request.base_canon_sha,
        key="second",
        text="Mara left the north door open.",
    )
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    barrier = context.Barrier(2)
    processes = [
        context.Process(
            target=_process_promote,
            args=(str(project), request.to_dict(), queue, barrier),
        )
        for request in (first_request, second_request)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
    results = [queue.get(timeout=2) for _ in processes]

    assert all(not process.is_alive() and process.exitcode == 0 for process in processes)
    assert sorted(result[0] for result in results) == ["error", "ok"]
    error = next(result for result in results if result[0] == "error")
    assert error[1] in {"StaleArtifactHead", "StaleCanonError"}
    assert len(CanonLedger(project).history()) == 1
    receipt_files = list(
        (project / "outputs" / "state" / "promotion_receipts").glob("*.json")
    )
    assert len(receipt_files) == 1
