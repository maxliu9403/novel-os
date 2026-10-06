"""Auditable recovery for legacy runs that omitted foundation canon."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from canon import apply_canon_proposal
from canon_ledger import (
    CanonCommitUncertain,
    CanonLedger,
    CanonLedgerEntry,
    CanonReconciliationEntry,
    canonical_canon_sha,
)
from foundation_canon import (
    FoundationCanonError,
    FoundationCanonReceipt,
    FoundationCanonService,
    FoundationIdempotencyConflict,
)
from project_identity import ensure_project_instance_id_unlocked
from project_lock import ProjectLock
from proposals import ProposalStore
from state_codec import blank_story_state, state_payload
from state_manager import StoryState
from story_foundation import apply_story_foundation


_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ARC_STAGES = {"beginning", "middle", "climax", "resolution"}
_CHAPTER_OPERATIONAL_FIELDS = {
    "canonical_revision_id",
    "continuity_checks",
    "contract_id",
    "last_evaluation_id",
    "last_modified",
    "quality_scores",
    "status",
    "target_word_count",
    "word_count",
}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return f"{prefix}-{hashlib.sha256(_json_bytes(value)).hexdigest()}"


def _record(kind: str, payload: Mapping[str, Any]) -> bytes:
    value = dict(payload)
    return _json_bytes(
        {
            kind: value,
            "record_sha256": hashlib.sha256(_json_bytes(value)).hexdigest(),
        }
    ) + b"\n"


class CanonReconciliationService(FoundationCanonService):
    """Rebuild foundation-backed canon and append a distinct ledger event."""

    def __init__(
        self,
        project_root: Path | str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(project_root, clock=clock)
        self.reconciliation_journal_dir = (
            self.state_dir / "foundation_reconciliation_journal"
        )
        self.ledger = CanonLedger(self.project_root)
        self.proposals = ProposalStore(self.project_root)
        self.artifact_heads_path = self.project_root / "outputs/artifacts/heads.json"

    def _artifact_heads_sha(self) -> str:
        try:
            mode = self.artifact_heads_path.lstat().st_mode
        except FileNotFoundError:
            return hashlib.sha256(b"").hexdigest()
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise FoundationCanonError(
                "artifact heads must be a regular non-symlink file"
            )
        return hashlib.sha256(self.artifact_heads_path.read_bytes()).hexdigest()

    @staticmethod
    def _normalize_outcomes(
        outcomes: Mapping[str, Mapping[str, str]],
    ) -> dict[str, dict[str, str]]:
        normalized = json.loads(_json_bytes(outcomes))
        if not isinstance(normalized, dict):
            raise ValueError("outcomes must be an object")
        for character_id, value in normalized.items():
            if not character_id.strip() or not isinstance(value, dict):
                raise ValueError("outcomes must map character ids to objects")
            if set(value) != {"outcome_state", "outcome_evidence"}:
                raise ValueError("each outcome requires state and evidence")
            value["outcome_state"] = str(value["outcome_state"]).strip().lower()
            value["outcome_evidence"] = str(value["outcome_evidence"]).strip()
            if not value["outcome_state"] or not value["outcome_evidence"]:
                raise ValueError("each outcome requires nonblank state and evidence")
        return normalized

    @staticmethod
    def _required_outcomes(foundation: Mapping[str, Any]) -> dict[str, str]:
        contract = foundation.get("ending_contract") or {}
        required: dict[str, str] = {}
        for raw in contract.get("character_arcs") or []:
            if not isinstance(raw, dict):
                continue
            character_id = str(raw.get("character_id") or "").strip()
            outcome = str(raw.get("required_outcome") or "").strip().lower()
            if not outcome:
                legacy = str(raw.get("required_end_state") or "").strip().lower()
                if legacy and legacy not in _ARC_STAGES:
                    outcome = legacy
            if character_id and outcome:
                required[character_id] = outcome
        return required

    @staticmethod
    def _preserve_operational_state(
        current: StoryState, candidate: StoryState
    ) -> None:
        for chapter_number, source in current.chapters.items():
            target = candidate.chapters.get(chapter_number)
            if target is None:
                continue
            for field in _CHAPTER_OPERATIONAL_FIELDS:
                setattr(target, field, copy.deepcopy(getattr(source, field)))
        candidate.compile_styles = copy.deepcopy(current.compile_styles)
        candidate.session_log = copy.deepcopy(current.session_log)
        candidate.binder = copy.deepcopy(current.binder)
        for character_id, source in current.characters.items():
            target = candidate.characters.get(character_id)
            if target is not None:
                target.portrait_media_id = source.portrait_media_id

    def _build_candidate(
        self,
        current: StoryState,
        foundation: Mapping[str, Any],
        target_words: int,
        history: tuple[CanonLedgerEntry, ...],
        outcomes: Mapping[str, Mapping[str, str]],
    ) -> StoryState:
        candidate = blank_story_state(self.project_root)
        candidate.metadata = copy.deepcopy(current.metadata)
        candidate.story_bible = copy.deepcopy(current.story_bible)
        candidate.continuity_exemptions = copy.deepcopy(
            current.continuity_exemptions
        )
        apply_story_foundation(candidate, foundation, target_words)

        for entry in history:
            milestone_lengths = {
                thread_id: len(thread.milestones)
                for thread_id, thread in candidate.plot_threads.items()
            }
            proposal = self.proposals.load(
                entry.proposal_id,
                expected_source_artifact_sha=entry.source_artifact_sha,
            )
            apply_canon_proposal(candidate, proposal, entry.source_artifact_sha)
            chapter = candidate.get_chapter(entry.chapter)
            if chapter is not None:
                chapter.status = "complete"
                chapter.last_modified = entry.committed_at
            for thread_id, thread in candidate.plot_threads.items():
                prior_length = milestone_lengths.get(thread_id, 0)
                for milestone in thread.milestones[prior_length:]:
                    milestone["timestamp"] = entry.committed_at

        # Recovery can happen mid-book. An ending contract describes future
        # obligations; requiring its outcomes here would fabricate unwritten
        # events just to restore a planning baseline. Keep completed-book
        # evidence requirements, and replay only committed facts otherwise.
        planned_chapters = {int(item["number"]) for item in foundation.get("chapters") or []}
        committed_chapters = {entry.chapter for entry in history}
        required = (
            self._required_outcomes(foundation)
            if planned_chapters.issubset(committed_chapters)
            else {}
        )
        for character_id, expected in required.items():
            if character_id not in outcomes:
                raise ValueError(
                    f"semantic outcome evidence is required for {character_id}"
                )
            if outcomes[character_id]["outcome_state"] != expected:
                raise ValueError(
                    f"semantic outcome for {character_id} must be {expected}"
                )
        for character_id, outcome in outcomes.items():
            character = candidate.characters.get(character_id)
            if character is None:
                raise ValueError(f"outcome references unknown character {character_id}")
            character.outcome_state = outcome["outcome_state"]
            character.outcome_evidence = outcome["outcome_evidence"]

        self._preserve_operational_state(current, candidate)
        return candidate

    def _journal_path(self, key: str) -> Path:
        return self.reconciliation_journal_dir / f"{self._safe_key(key)}.json"

    def _write_reconciliation_journal(
        self, journal: Mapping[str, Any], state: str
    ) -> dict[str, Any]:
        payload = {**dict(journal), "state": state}
        key = payload["request"]["idempotency_key"]
        self._atomic_write(
            self._journal_path(key),
            _record("reconciliation_journal", payload),
        )
        return payload

    def _load_reconciliation_journal(self, key: str) -> dict[str, Any] | None:
        return self._read_record(
            self._journal_path(key), "reconciliation_journal"
        )

    def _resume(self, journal: dict[str, Any]) -> FoundationCanonReceipt:
        receipt = FoundationCanonReceipt.from_dict(journal["receipt"])
        entry = CanonReconciliationEntry.from_dict(journal["ledger_entry"])
        if canonical_canon_sha(journal["base_state_payload"]) != entry.base_canon_sha:
            raise FoundationCanonError(
                "reconciliation base snapshot does not match the ledger base"
            )
        if canonical_canon_sha(journal["state_payload"]) != entry.new_canon_sha:
            raise FoundationCanonError(
                "reconciliation state snapshot does not match the ledger result"
            )
        current_sha = canonical_canon_sha(StoryState(str(self.project_root)))
        if self._artifact_heads_sha() != entry.artifact_heads_sha256:
            raise FoundationCanonError(
                "artifact heads changed during foundation reconciliation"
            )

        if journal["state"] == "prepared":
            if current_sha == entry.base_canon_sha:
                self._atomic_write(
                    self.state_path,
                    _json_bytes(journal["state_payload"]) + b"\n",
                )
            elif current_sha != entry.new_canon_sha:
                raise FoundationCanonError(
                    "reconciliation journal matches neither old nor new canon"
                )
            journal = self._write_reconciliation_journal(
                journal, "state_committed"
            )

        if journal["state"] == "state_committed":
            if canonical_canon_sha(StoryState(str(self.project_root))) != entry.new_canon_sha:
                raise FoundationCanonError("reconciled canonical state is missing")
            try:
                self.ledger.append(entry)
            except CanonCommitUncertain:
                matches = [
                    item
                    for item in self.ledger.history()
                    if item.entry_id == entry.entry_id
                ]
                if matches != [entry]:
                    raise
            journal = self._write_reconciliation_journal(
                journal, "ledger_committed"
            )

        if journal["state"] == "ledger_committed":
            if self.ledger.current() != entry:
                raise FoundationCanonError(
                    "reconciliation ledger entry is not the canon head"
                )
            self._atomic_write(
                self.receipt_dir / f"{receipt.idempotency_key}.json",
                _record("receipt", receipt.to_dict()),
            )
            journal = self._write_reconciliation_journal(journal, "committed")

        if journal["state"] != "committed":
            raise FoundationCanonError("foundation reconciliation did not commit")
        stored = self._load_receipt(receipt.idempotency_key)
        if stored != receipt:
            raise FoundationCanonError("reconciliation foundation receipt is missing")
        if self.ledger.current() != entry:
            raise FoundationCanonError("reconciliation ledger tail is missing")
        if canonical_canon_sha(StoryState(str(self.project_root))) != entry.new_canon_sha:
            raise FoundationCanonError("reconciliation canonical state is missing")
        if self._artifact_heads_sha() != entry.artifact_heads_sha256:
            raise FoundationCanonError(
                "artifact heads changed during foundation reconciliation"
            )
        return receipt

    def reconcile(
        self,
        foundation: Mapping[str, Any],
        *,
        target_words: int,
        idempotency_key: str,
        outcomes: Mapping[str, Mapping[str, str]],
        reason: str,
    ) -> FoundationCanonReceipt:
        key = self._safe_key(idempotency_key)
        if target_words < 1:
            raise ValueError("target_words must be positive")
        reason = str(reason).strip()
        if not reason:
            raise ValueError("reconciliation reason must be nonblank")
        normalized_foundation = json.loads(_json_bytes(foundation))
        normalized_outcomes = self._normalize_outcomes(outcomes)
        foundation_sha = hashlib.sha256(
            _json_bytes(normalized_foundation)
        ).hexdigest()

        with ProjectLock(self.project_root):
            self._preflight_storage()
            self._ensure_storage_directory(self.reconciliation_journal_dir)
            project_instance_id = ensure_project_instance_id_unlocked(
                self.project_root
            )
            existing_receipt = self._load_receipt(key)
            existing_journal = self._load_reconciliation_journal(key)
            if existing_receipt is not None:
                if existing_receipt.schema_version != 2:
                    raise FoundationIdempotencyConflict(
                        "idempotency key is bound to a non-reconciliation foundation"
                    )
                if existing_journal is None:
                    raise FoundationCanonError(
                        "reconciliation receipt has no durable journal"
                    )
                request = existing_journal.get("request") or {}
                if (
                    request.get("foundation_sha256") != foundation_sha
                    or request.get("target_words") != target_words
                    or request.get("outcomes") != normalized_outcomes
                    or request.get("reason") != reason
                ):
                    raise FoundationIdempotencyConflict(
                        "idempotency key is already bound to another reconciliation"
                    )
                return self._resume(existing_journal)
            if existing_journal is not None:
                request = existing_journal.get("request") or {}
                if (
                    request.get("foundation_sha256") != foundation_sha
                    or request.get("target_words") != target_words
                    or request.get("outcomes") != normalized_outcomes
                    or request.get("reason") != reason
                ):
                    raise FoundationIdempotencyConflict(
                        "idempotency key is already bound to another reconciliation"
                    )
                return self._resume(existing_journal)

            raw_history = self.ledger.history()
            if not raw_history:
                raise FoundationCanonError(
                    "foundation reconciliation requires committed chapter history"
                )
            history = tuple(
                item for item in raw_history if isinstance(item, CanonLedgerEntry)
            )
            if not history:
                raise FoundationCanonError(
                    "foundation reconciliation requires chapter promotion history"
                )
            current = StoryState(str(self.project_root))
            base_canon_sha = canonical_canon_sha(current)
            tail = raw_history[-1]
            if base_canon_sha != tail.new_canon_sha:
                raise FoundationCanonError(
                    "canonical state does not match committed chapter history"
                )

            candidate = self._build_candidate(
                current,
                normalized_foundation,
                target_words,
                history,
                normalized_outcomes,
            )
            new_canon_sha = canonical_canon_sha(candidate)
            if new_canon_sha == base_canon_sha:
                raise FoundationCanonError(
                    "foundation reconciliation produced no canon change"
                )
            artifact_heads_sha = self._artifact_heads_sha()
            committed_at = self._clock().astimezone(timezone.utc).isoformat()
            request_identity = {
                "project_instance_id": project_instance_id,
                "idempotency_key": key,
                "foundation_sha256": foundation_sha,
                "target_words": target_words,
                "base_canon_sha": base_canon_sha,
                "new_canon_sha": new_canon_sha,
                "replayed_entry_ids": [item.entry_id for item in history],
                "outcomes": normalized_outcomes,
                "reason": reason,
                "artifact_heads_sha256": artifact_heads_sha,
                "schema_version": 1,
            }
            request_id = _content_id(
                "foundation-reconciliation-request", request_identity
            )
            request = {**request_identity, "request_id": request_id}
            entry = CanonReconciliationEntry(
                project_instance_id=project_instance_id,
                previous_entry_id=tail.entry_id,
                base_canon_sha=base_canon_sha,
                new_canon_sha=new_canon_sha,
                foundation_sha256=foundation_sha,
                artifact_heads_sha256=artifact_heads_sha,
                replayed_entry_ids=tuple(item.entry_id for item in history),
                outcomes=normalized_outcomes,
                reason=reason,
                request_id=request_id,
                idempotency_key=key,
                committed_at=committed_at,
            )
            receipt = FoundationCanonReceipt(
                project_instance_id=project_instance_id,
                idempotency_key=key,
                foundation_sha256=foundation_sha,
                target_words=target_words,
                old_canon_sha=base_canon_sha,
                new_canon_sha=new_canon_sha,
                request_id=request_id,
                committed_at=committed_at,
                schema_version=2,
                reconciliation_entry_id=entry.entry_id,
            )
            journal = {
                "schema_version": 1,
                "state": "prepared",
                "request": request,
                "receipt": receipt.to_dict(),
                "ledger_entry": entry.to_dict(),
                "base_state_payload": json.loads(
                    _json_bytes(state_payload(current, committed_at))
                ),
                "state_payload": json.loads(
                    _json_bytes(state_payload(candidate, committed_at))
                ),
            }
            journal = self._write_reconciliation_journal(journal, "prepared")
            return self._resume(journal)


__all__ = ["CanonReconciliationService"]
