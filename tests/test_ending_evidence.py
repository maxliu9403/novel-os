"""Source-bound ending reconciliation; no model or canonical-state writes."""

import copy
import json
from pathlib import Path

import pytest

from artifacts import ArtifactStore
from canon import CanonDeltaProposal
from canon_ledger import canonical_canon_sha
from ending_evidence import EndingEvidenceError, EndingEvidenceStore, contract_sha256
from project_identity import ensure_project_instance_id
from promotion import PromotionRequest, PromotionService
from proposals import ProposalStore
from quality import EvaluationRequest, QualityLab
from state_manager import Character, StoryState


def _promote(project, text, key, parent=None, *, chapter=1, payoff_status=None):
    store = ArtifactStore(project)
    revision = store.put_text(
        chapter=chapter, kind="final", text=text, source="editor",
        parent_revision_id=parent.revision_id if parent else None,
    )
    delta = {"key_events": [text]}
    if payoff_status:
        delta["payoff_events"] = [f"payoff_001 | status={payoff_status} | evidence=Approved chapter observation"]
    proposal = CanonDeltaProposal(
        chapter=chapter, agent_name="scribe", source_artifact_sha=revision.sha256,
        delta=delta, timestamp="2026-10-14T00:00:00+00:00",
    )
    ProposalStore(project).save(proposal)
    evaluation = QualityLab.evaluate_deterministic(
        EvaluationRequest.for_text(chapter, text, artifact_revision_id=revision.revision_id),
        candidate_text=text, continuity_findings=[],
        created_at="2026-10-14T00:00:00+00:00",
    )
    receipt = PromotionService(project).promote(PromotionRequest(
        project_id=project.name, project_instance_id=ensure_project_instance_id(project),
        chapter=chapter, candidate_revision_id=revision.revision_id,
        candidate_sha256=revision.sha256, evaluation_report=evaluation,
        canon_proposal_id=proposal.proposal_id,
        expected_final_revision_id=parent.revision_id if parent else None,
        expected_final_sha256=parent.sha256 if parent else None,
        base_canon_sha=canonical_canon_sha(StoryState(str(project))),
        idempotency_key=key, actor="author", reason="approved chapter",
    ))
    final = project / f"outputs/manuscript/chapter_{chapter:03d}_final.md"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_text(text, encoding="utf-8")
    return revision, receipt


@pytest.fixture
def evidence_book(tmp_path):
    project = tmp_path / "book"
    state = StoryState(str(project))
    state.metadata = {"title": "North Door", "target_chapters": 1}
    state.characters["L01"] = Character("L01", "Marian Reed", "supporting")
    state.save_state()
    text = "晨光。 Mara opened the north door. Marian gave bounded counsel."
    revision, receipt = _promote(project, text, "chapter-1")
    contract = {
        "schema_version": 1, "enforce": True,
        "plot_payoffs": [{"id": "payoff_001", "required_payoff": "The door opens."}],
        "character_arcs": [{"character_id": "L01", "required_arc_stage": "resolution",
                            "required_outcome": "bounded_counsel_delivered"}],
    }
    path = project / "outputs/input/ending_contract.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract), encoding="utf-8")
    source = {
        "chapter": 1, "revision_id": revision.revision_id, "sha256": revision.sha256,
        "promotion_key": receipt.idempotency_key, "promotion_receipt_id": receipt.receipt_id,
        "quote": text, "quote_start": 0, "quote_end": len(text),
    }
    payload = {
        "schema_version": 1, "project_instance_id": receipt.project_instance_id,
        "contract_sha256": contract_sha256(contract),
        "reviewer": {"kind": "agent", "name": "Codex", "reason": "Read approved chapter in full."},
        "reviewed_at": "2026-10-14T08:00:00+08:00", "supersedes": [],
        "claims": [
            {"kind": "payoff", "payoff_id": "payoff_001", "status": "paid",
             "evidence": "Mara opens the door.", "sources": [copy.deepcopy(source)]},
            {"kind": "arc", "character_id": "L01", "arc_stage": "resolution",
             "outcome_state": "bounded_counsel_delivered",
             "evidence": "Marian delivers bounded counsel.", "sources": [copy.deepcopy(source)]},
        ],
    }
    return project, contract, payload, revision


def _load(project, contract, as_of=1):
    return EndingEvidenceStore(project).load_validated(contract, StoryState(str(project)), as_of)


def test_record_is_idempotent_and_cold_load_leaves_canon_and_manuscript_unchanged(evidence_book):
    project, contract, payload, _ = evidence_book
    original = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    record = EndingEvidenceStore(project).record(payload)
    assert record["review_id"].startswith("ending-review-")
    assert EndingEvidenceStore(project).record(payload) == record
    assert _load(project, contract) == [record]
    assert all((project / p).read_bytes() == data for p, data in original.items())
    assert StoryState(str(project)).characters["L01"].outcome_state == ""


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(extra="unknown"),
    lambda p: p.update(schema_version=True),
    lambda p: p.update(project_instance_id="0" * 32),
    lambda p: p.update(contract_sha256="0" * 64),
    lambda p: p.update(reviewed_at="2026-10-14T08:00:00"),
    lambda p: p["reviewer"].update(kind="model-said-so"),
    lambda p: p["reviewer"].update(reason=""),
    lambda p: p["claims"][0].update(payoff_id="missing"),
    lambda p: p["claims"][0].update(status="recalled"),
    lambda p: p["claims"][1].update(character_id="missing"),
    lambda p: p["claims"][1].update(outcome_state="unrelated_outcome"),
    lambda p: p["claims"][0].update(sources=[]),
    lambda p: p["claims"].append(copy.deepcopy(p["claims"][0])),
    lambda p: p["claims"][0]["sources"][0].update(quote_start=True),
    lambda p: p["claims"][0]["sources"][0].update(quote_start=-1),
    lambda p: p["claims"][0]["sources"][0].update(quote="invented"),
    lambda p: p["claims"][0]["sources"][0].update(quote_end=999999),
    lambda p: p["claims"][0]["sources"][0].update(chapter=True),
    lambda p: p["claims"][0]["sources"][0].update(sha256="0" * 64),
    lambda p: p["claims"][0]["sources"][0].update(promotion_receipt_id="promotion-receipt-" + "0" * 64),
    lambda p: p["claims"][0]["sources"][0].update(promotion_key="../escape"),
])
def test_invalid_reviews_never_publish(evidence_book, mutation):
    project, _, payload, _ = evidence_book
    mutation(payload)
    with pytest.raises(EndingEvidenceError):
        EndingEvidenceStore(project).record(payload)
    assert not list((project / "outputs/state/ending_evidence_reviews").glob("*.json"))


def test_existing_conflicting_outcome_is_not_overridden(evidence_book):
    project, contract, payload, _ = evidence_book
    state = StoryState(str(project))
    state.characters["L01"].outcome_state = "advice_withdrawn"
    EndingEvidenceStore(project).record(payload)
    with pytest.raises(EndingEvidenceError, match="conflict"):
        EndingEvidenceStore(project).load_validated(contract, state, 1)


def test_future_source_not_eligible_in_earlier_review(evidence_book):
    project, contract, payload, _ = evidence_book
    EndingEvidenceStore(project).record(payload)
    with pytest.raises(EndingEvidenceError):
        _load(project, contract, 0)


@pytest.mark.parametrize("target", ["manuscript", "blob", "receipt", "ledger", "identity"])
def test_changed_bound_source_fails_closed_on_cold_load(evidence_book, target):
    project, contract, payload, revision = evidence_book
    EndingEvidenceStore(project).record(payload)
    paths = {
        "manuscript": "outputs/manuscript/chapter_001_final.md",
        "blob": f"outputs/artifacts/sha256/{revision.sha256}",
        "receipt": "outputs/state/promotion_receipts/chapter-1.json",
        "ledger": "outputs/state/canon_ledger.jsonl",
        "identity": "outputs/state/project_identity.json",
    }
    (project / paths[target]).write_text("corrupted", encoding="utf-8")
    with pytest.raises(EndingEvidenceError):
        _load(project, contract)


def test_immutable_review_cannot_be_tampered_or_silently_ignored(evidence_book):
    project, contract, payload, _ = evidence_book
    record = EndingEvidenceStore(project).record(payload)
    path = project / f"outputs/state/ending_evidence_reviews/{record['review_id']}.json"
    data = json.loads(path.read_text())
    data["reviewer"]["reason"] = "edited old review"
    path.write_text(json.dumps(data))
    with pytest.raises(EndingEvidenceError):
        _load(project, contract)


def test_duplicate_active_claim_requires_explicit_supersession(evidence_book):
    project, contract, payload, _ = evidence_book
    store = EndingEvidenceStore(project)
    first = store.record(payload)
    payload["reviewed_at"] = "2026-10-14T09:00:00+08:00"
    with pytest.raises(EndingEvidenceError, match="duplicate"):
        store.record(payload)
    payload["supersedes"] = [first["review_id"]]
    second = store.record(payload)
    assert _load(project, contract) == [second]
    assert len(list((project / "outputs/state/ending_evidence_reviews").glob("*.json"))) == 2


def test_new_approved_head_requires_new_review_without_destroying_old_record(evidence_book):
    project, contract, payload, old = evidence_book
    store = EndingEvidenceStore(project)
    first = store.record(payload)
    text = "晨光。 Mara pushed the door fully open. Marian delivered counsel."
    revision, receipt = _promote(project, text, "chapter-1-revised", parent=old)
    with pytest.raises(EndingEvidenceError):
        _load(project, contract)
    payload["supersedes"] = [first["review_id"]]
    payload["reviewed_at"] = "2026-10-14T10:00:00+08:00"
    for claim in payload["claims"]:
        claim["sources"][0].update(
            revision_id=revision.revision_id, sha256=revision.sha256,
            promotion_key=receipt.idempotency_key, promotion_receipt_id=receipt.receipt_id,
            quote=text, quote_start=0, quote_end=len(text),
        )
    second = store.record(payload)
    assert _load(project, contract) == [second]


@pytest.mark.parametrize("relative", ["outputs/state/ending_evidence_reviews", "outputs/manuscript", "outputs/artifacts/sha256"])
def test_symlinked_storage_or_sources_are_rejected(evidence_book, relative, tmp_path):
    project, _, payload, _ = evidence_book
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    replacement = tmp_path / (target.name + "-real")
    if target.exists():
        target.rename(replacement)
    else:
        replacement.mkdir()
    target.symlink_to(replacement, target_is_directory=True)
    with pytest.raises(EndingEvidenceError):
        EndingEvidenceStore(project).record(payload)


def test_fingerprint_detects_added_deleted_and_changed_evidence_inputs(evidence_book):
    project, _, payload, revision = evidence_book
    store = EndingEvidenceStore(project)
    before = store.input_hashes()
    record = store.record(payload)
    added = store.input_hashes()
    assert added != before
    final = project / "outputs/manuscript/chapter_001_final.md"
    final.unlink()
    assert store.input_hashes() != added
    final.write_text(payload["claims"][0]["sources"][0]["quote"])
    assert store.input_hashes() == added
    assert any(revision.sha256 in path for path in added)
    (project / f"outputs/state/ending_evidence_reviews/{record['review_id']}.json").unlink()
    assert store.input_hashes() == before


def test_contract_hash_ignores_formatting_but_not_semantics(evidence_book):
    project, contract, payload, _ = evidence_book
    EndingEvidenceStore(project).record(payload)
    reformatted = json.loads(json.dumps(contract, indent=8, sort_keys=True))
    assert _load(project, reformatted)
    changed = copy.deepcopy(contract)
    changed["plot_payoffs"][0]["required_payoff"] = "The door remains closed."
    with pytest.raises(EndingEvidenceError, match="contract"):
        _load(project, changed)


def test_empty_history_does_not_initialize_legacy_identity_or_state(tmp_path):
    project = tmp_path / "legacy"
    store = EndingEvidenceStore(project)
    assert store.input_hashes()
    assert store.load_validated({}, object(), 0) == []
    assert not project.exists()


@pytest.mark.parametrize("corruption", ["json", "unknown-fields", "symlink"])
def test_corrupt_input_fingerprint_invalidates_before_gate_reports_error(evidence_book, corruption, tmp_path):
    project, contract, payload, _ = evidence_book
    store = EndingEvidenceStore(project)
    record = store.record(payload)
    original = store.input_hashes()
    path = store.directory / f"{record['review_id']}.json"
    if corruption == "json":
        path.write_text("{broken")
    elif corruption == "unknown-fields":
        path.write_text(json.dumps({**record, "extra": True}))
    else:
        target = tmp_path / "review.json"
        path.rename(target)
        path.symlink_to(target)
    assert store.input_hashes() != original
    with pytest.raises(EndingEvidenceError):
        _load(project, contract)


def test_missing_or_cross_project_supersession_is_rejected(evidence_book):
    project, _, payload, _ = evidence_book
    payload["supersedes"] = ["ending-review-" + "0" * 64]
    with pytest.raises(EndingEvidenceError, match="missing"):
        EndingEvidenceStore(project).record(payload)


def test_earlier_canon_snapshot_does_not_invalidate_approved_receipt(evidence_book):
    project, contract, payload, _ = evidence_book
    EndingEvidenceStore(project).record(payload)
    state = StoryState(str(project))
    state.story_bible["restored_snapshot_marker"] = "different canon hash"
    assert EndingEvidenceStore(project).load_validated(contract, state, 1)


def test_intentionally_open_payoff_requires_canon_reconciliation(evidence_book):
    project, contract, payload, _ = evidence_book
    EndingEvidenceStore(project).record(payload)
    state = StoryState(str(project))
    state.get_chapter(1).payoff_events.append({"payoff_id": "payoff_001", "status": "intentional_open"})
    with pytest.raises(EndingEvidenceError, match="intentionally open"):
        EndingEvidenceStore(project).load_validated(contract, state, 1)


def test_quote_offsets_are_unicode_characters_and_not_utf8_bytes(evidence_book):
    project, contract, payload, _ = evidence_book
    source = payload["claims"][0]["sources"][0]
    quote = "Mara opened the north door."
    start = source["quote"].index(quote)
    source.update(quote=quote, quote_start=start, quote_end=start + len(quote))
    assert EndingEvidenceStore(project).record(payload)
    assert _load(project, contract)


def test_uncommitted_head_cannot_supply_evidence(evidence_book):
    project, _, payload, original = evidence_book
    artifacts = ArtifactStore(project)
    text = payload["claims"][0]["sources"][0]["quote"]
    uncommitted = artifacts.put_text(chapter=1, kind="final", text=text, source="unapproved-revision")
    artifacts.set_head(1, "final", uncommitted.revision_id, expected_revision_id=original.revision_id)
    for claim in payload["claims"]:
        claim["sources"][0]["revision_id"] = uncommitted.revision_id
    with pytest.raises(EndingEvidenceError, match="receipt"):
        EndingEvidenceStore(project).record(payload)


def test_unsafe_temp_is_not_hidden_from_cache_invalidation(evidence_book, tmp_path):
    project, contract, payload, _ = evidence_book
    store = EndingEvidenceStore(project)
    store.record(payload)
    original = store.input_hashes()
    (store.directory / ".ending-review-attacker.tmp").symlink_to(tmp_path / "absent")
    assert store.input_hashes() != original
    with pytest.raises(EndingEvidenceError):
        _load(project, contract)


def _payoff_source(project, payload, chapter, *, payoff_status=None):
    text = "Mara opened the north door, completing the promised choice."
    revision, receipt = _promote(project, text, f"chapter-{chapter}", chapter=chapter,
                                payoff_status=payoff_status)
    payload["claims"][0]["sources"] = [{
        "chapter": chapter, "revision_id": revision.revision_id, "sha256": revision.sha256,
        "promotion_key": receipt.idempotency_key, "promotion_receipt_id": receipt.receipt_id,
        "quote": text, "quote_start": 0, "quote_end": len(text),
    }]


@pytest.mark.parametrize("open_chapter,completion_chapter,recall_chapters", [
    (7, 8, (9, 15)),  # Current book payoff_004: completion omitted between open and recall.
    (10, 12, (15,)),  # Current book payoff_007: deferred until the later approved confrontation.
])
def test_earlier_open_does_not_negate_later_source_bound_completion(
    evidence_book, open_chapter, completion_chapter, recall_chapters,
):
    project, contract, payload, _ = evidence_book
    _promote(project, "This obligation remains open in this chapter.", f"chapter-{open_chapter}",
             chapter=open_chapter, payoff_status="intentional_open")
    _payoff_source(project, payload, completion_chapter)
    for chapter in recall_chapters:
        _promote(project, "The established result is remembered.", f"chapter-{chapter}",
                 chapter=chapter, payoff_status="recalled")
    state = StoryState(str(project))
    assert state.get_chapter(open_chapter).payoff_events[0]["status"] == "intentional_open"
    assert not state.get_chapter(completion_chapter).payoff_events
    assert state.get_chapter(recall_chapters[-1]).payoff_events[0]["status"] == "recalled"
    record = EndingEvidenceStore(project).record(payload)
    assert EndingEvidenceStore(project).load_validated(contract, state, recall_chapters[-1]) == [record]


@pytest.mark.parametrize("open_chapter", [8, 9])
def test_same_or_later_open_remains_a_conflict_even_with_an_older_paid_event(evidence_book, open_chapter):
    project, _, payload, _ = evidence_book
    _promote(project, "An earlier apparent completion.", "chapter-6", chapter=6, payoff_status="paid")
    _payoff_source(project, payload, 8, payoff_status="intentional_open" if open_chapter == 8 else None)
    if open_chapter == 9:
        _promote(project, "The obligation is explicitly left open.", "chapter-9",
                 chapter=9, payoff_status="intentional_open")
    with pytest.raises(EndingEvidenceError, match="intentionally open"):
        EndingEvidenceStore(project).record(payload)


def test_open_after_as_of_does_not_invalidate_earlier_review(evidence_book):
    project, contract, payload, _ = evidence_book
    _payoff_source(project, payload, 8)
    record = EndingEvidenceStore(project).record(payload)
    _promote(project, "A later reopening.", "chapter-9", chapter=9, payoff_status="intentional_open")
    store = EndingEvidenceStore(project)
    state = StoryState(str(project))
    assert store.load_validated(contract, state, 8) == [record]
    with pytest.raises(EndingEvidenceError, match="intentionally open"):
        store.load_validated(contract, state, 9)
