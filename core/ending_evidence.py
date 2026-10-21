"""Append-only adjudications of missing ending evidence in approved chapters.

These reviews never amend canon, proposals, receipts or manuscripts. Quote and
receipt checks establish provenance, not the truth of the reviewer's semantic
judgment. Consumers may apply validated claims only to a temporary projection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from artifacts import ArtifactStore
from project_identity import load_project_instance_id_unlocked
from project_lock import ProjectLock
from promotion import PromotionService
from state_manager import StoryState


REVIEWS_RELATIVE = "outputs/state/ending_evidence_reviews"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^ending-review-[0-9a-f]{64}$")
_PROMOTION_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_STAGES = {"beginning", "middle", "climax", "resolution"}
_FIELDS = {"schema_version", "project_instance_id", "contract_sha256", "reviewer",
           "reviewed_at", "supersedes", "claims"}
_SOURCE_FIELDS = {"chapter", "revision_id", "sha256", "promotion_key",
                  "promotion_receipt_id", "quote", "quote_start", "quote_end"}
_MAX_RECORD_BYTES = 4 * 1024 * 1024


class EndingEvidenceError(ValueError):
    """A historical ending review cannot safely contribute completion evidence."""


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def contract_sha256(contract: dict[str, Any]) -> str:
    return hashlib.sha256(_json(contract)).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EndingEvidenceError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != expected:
        raise EndingEvidenceError(f"{label} has invalid fields")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise EndingEvidenceError(f"{label} must be nonblank text")
    return value


def _digest(value, label, pattern=_SHA):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise EndingEvidenceError(f"{label} has invalid identity")


def _claim_key(claim):
    return (claim["kind"], claim.get("payoff_id", claim.get("character_id")))


def _unpublished_temp(path):
    return (path.name.startswith(".ending-review-") and path.name.endswith(".tmp")
            and stat.S_ISREG(path.lstat().st_mode))


def _validate_schema(payload):
    _fields(payload, _FIELDS, "ending review")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise EndingEvidenceError("unsupported ending review schema")
    _digest(payload["project_instance_id"], "project instance", re.compile(r"^[0-9a-f]{32}$"))
    _digest(payload["contract_sha256"], "contract hash")
    reviewer = payload["reviewer"]
    _fields(reviewer, {"kind", "name", "reason"}, "reviewer")
    if reviewer["kind"] not in ("human", "agent"):
        raise EndingEvidenceError("reviewer kind must be human or agent")
    _text(reviewer["name"], "reviewer name")
    _text(reviewer["reason"], "reviewer reason")
    try:
        timestamp = datetime.fromisoformat(_text(payload["reviewed_at"], "reviewed_at"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timezone missing")
    except ValueError as exc:
        raise EndingEvidenceError("reviewed_at must be a timezone-aware timestamp") from exc
    supersedes = payload["supersedes"]
    if not isinstance(supersedes, list):
        raise EndingEvidenceError("supersedes must be a list")
    for identifier in supersedes:
        _digest(identifier, "superseded review", _ID)
    if len(set(supersedes)) != len(supersedes):
        raise EndingEvidenceError("duplicate superseded review")
    claims = payload["claims"]
    if not isinstance(claims, list) or not claims:
        raise EndingEvidenceError("review requires explicit claims")
    seen = set()
    for claim in claims:
        if not isinstance(claim, dict):
            raise EndingEvidenceError("claim must be an object")
        if claim.get("kind") == "payoff":
            _fields(claim, {"kind", "payoff_id", "status", "evidence", "sources"}, "payoff claim")
            _text(claim["payoff_id"], "payoff ID")
            if claim["status"] != "paid":
                raise EndingEvidenceError("payoff claim may only record paid evidence")
        elif claim.get("kind") == "arc":
            _fields(claim, {"kind", "character_id", "arc_stage", "outcome_state", "evidence", "sources"}, "arc claim")
            _text(claim["character_id"], "character ID")
            if claim["arc_stage"] not in (*_STAGES, ""):
                raise EndingEvidenceError("invalid arc stage")
            if not isinstance(claim["outcome_state"], str):
                raise EndingEvidenceError("outcome state must be text")
        else:
            raise EndingEvidenceError("unknown ending claim kind")
        key = _claim_key(claim)
        if key in seen:
            raise EndingEvidenceError("duplicate ending claim")
        seen.add(key)
        _text(claim["evidence"], "claim evidence")
        if not isinstance(claim["sources"], list) or not claim["sources"]:
            raise EndingEvidenceError("claim requires approved chapter sources")
        for source in claim["sources"]:
            _fields(source, _SOURCE_FIELDS, "source")
            if type(source["chapter"]) is not int or source["chapter"] < 1:
                raise EndingEvidenceError("source chapter must be a positive integer")
            for key in ("revision_id", "sha256"):
                _digest(source[key], key)
            _digest(source["promotion_key"], "promotion key", _PROMOTION_KEY)
            _digest(source["promotion_receipt_id"], "receipt ID", re.compile(r"^promotion-receipt-[0-9a-f]{64}$"))
            _text(source["quote"], "source quote")
            start, end = source["quote_start"], source["quote_end"]
            if type(start) is not int or type(end) is not int or not 0 <= start < end:
                raise EndingEvidenceError("quote offsets must be an increasing character range")


class EndingEvidenceStore:
    def __init__(self, project: Path | str):
        self.project = Path(os.path.abspath(os.fspath(project)))
        self.directory = self.project / REVIEWS_RELATIVE

    def _safe(self, path: Path, *, missing=False):
        """Reject pre-existing symlinks throughout every managed source path."""
        relative = path.relative_to(self.project)
        current = self.project
        for index, component in enumerate(relative.parts):
            current /= component
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                if missing:
                    return
                raise EndingEvidenceError(f"ending evidence source is missing: {relative}") from None
            if stat.S_ISLNK(mode):
                raise EndingEvidenceError(f"ending evidence path must not be a symlink: {relative}")
            last = index == len(relative.parts) - 1
            if not last and not stat.S_ISDIR(mode):
                raise EndingEvidenceError(f"ending evidence parent is not a directory: {relative}")
            if last and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise EndingEvidenceError(f"ending evidence path is not a regular file/directory: {relative}")

    def _read_json(self, path):
        self._safe(path)
        if not path.is_file() or path.stat().st_size > _MAX_RECORD_BYTES:
            raise EndingEvidenceError(f"invalid ending evidence file size/type: {path.name}")
        return json.loads(path.read_bytes(), object_pairs_hook=_object)

    def _contract(self):
        path = self.project / "outputs/input/ending_contract.json"
        self._safe(path, missing=True)
        if path.exists():
            result = self._read_json(path)
        else:
            result = self._read_json(self.project / "outputs/input/foundation.json").get("ending_contract")
        if not isinstance(result, dict):
            raise EndingEvidenceError("ending contract is missing or malformed")
        return result

    def _records(self):
        self._safe(self.directory, missing=True)
        if not self.directory.exists():
            return {}
        if not self.directory.is_dir():
            raise EndingEvidenceError("ending evidence storage must be a directory")
        records = {}
        for path in sorted(self.directory.iterdir()):
            # Interrupted publication can leave an unpublished fsynced temp.
            if _unpublished_temp(path):
                continue
            if path.suffix != ".json" or not _ID.fullmatch(path.stem):
                raise EndingEvidenceError(f"unexpected ending review storage entry: {path.name}")
            record = self._read_json(path)
            _fields(record, _FIELDS | {"review_id"}, "stored ending review")
            payload = {key: value for key, value in record.items() if key != "review_id"}
            _validate_schema(payload)
            identifier = "ending-review-" + hashlib.sha256(_json(payload)).hexdigest()
            if record["review_id"] != identifier or path.stem != identifier:
                raise EndingEvidenceError("stored ending review identity/hash mismatch")
            records[identifier] = record
        return records

    @staticmethod
    def _active(records):
        superseded = set()
        visiting, visited = set(), set()

        def visit(identifier):
            if identifier in visiting:
                raise EndingEvidenceError("ending review supersession cycle")
            if identifier in visited:
                return
            visiting.add(identifier)
            for old in records[identifier]["supersedes"]:
                if old not in records:
                    raise EndingEvidenceError("superseded ending review is missing")
                superseded.add(old)
                visit(old)
            visiting.remove(identifier)
            visited.add(identifier)

        for identifier in records:
            visit(identifier)
        return [record for identifier, record in records.items() if identifier not in superseded]

    def _source(self, source, state, as_of):
        chapter = source["chapter"]
        chapter_state = state.get_chapter(chapter)
        if chapter > as_of or chapter_state is None or chapter_state.status != "complete":
            raise EndingEvidenceError("source chapter is not approved within the evaluated book")
        relatives = (
            "outputs/state/project_identity.json", "outputs/state/canon_ledger.jsonl",
            f"outputs/state/promotion_receipts/{source['promotion_key']}.json",
            "outputs/artifacts/heads.json", "outputs/artifacts/revisions.jsonl",
            f"outputs/artifacts/sha256/{source['sha256']}",
            f"outputs/manuscript/chapter_{chapter:03d}_final.md",
        )
        for relative in relatives:
            path = self.project / relative
            self._safe(path)
            if not path.is_file():
                raise EndingEvidenceError(f"approved source is not a regular file: {relative}")
        artifacts = ArtifactStore(self.project)
        head = artifacts.get_head(chapter, "final")
        if head is None or head.revision_id != source["revision_id"]:
            raise EndingEvidenceError("ending evidence references a stale Final head")
        revision = artifacts.get_revision(head.revision_id)
        if revision.sha256 != source["sha256"] or revision.chapter != chapter or revision.kind != "final":
            raise EndingEvidenceError("ending evidence artifact binding mismatch")
        text = artifacts.read_text(head.revision_id)
        projection = self.project / f"outputs/manuscript/chapter_{chapter:03d}_final.md"
        if hashlib.sha256(projection.read_bytes()).hexdigest() != revision.sha256:
            raise EndingEvidenceError("approved manuscript projection no longer matches Final")
        if text[source["quote_start"]:source["quote_end"]] != source["quote"] or source["quote_end"] > len(text):
            raise EndingEvidenceError("quote does not match its exact character offsets")
        # This store holds ProjectLock. Reuse the unlocked receipt reader and
        # validator rather than reacquiring the non-reentrant promotion lock.
        # No promotion recovery or canonical-tail mutation is performed here.
        promotions = PromotionService(self.project)
        receipt = promotions._load_receipt_unlocked(source["promotion_key"])
        if receipt is None:
            raise EndingEvidenceError("committed promotion receipt is missing")
        promotions._receipt_consistent(receipt, check_current_tail=False)
        if (receipt.receipt_id != source["promotion_receipt_id"]
                or receipt.chapter != chapter
                or receipt.new_artifact_revision_id != revision.revision_id
                or receipt.new_artifact_sha256 != revision.sha256
                or receipt.project_instance_id != load_project_instance_id_unlocked(self.project)):
            raise EndingEvidenceError("source is not bound to the approved promotion receipt")

    def _validated(self, records, contract, state, as_of):
        if type(as_of) is not int or as_of < 1:
            raise EndingEvidenceError("as_of_chapter must be a positive integer")
        active = self._active(records)
        if not records:
            return []
        instance = load_project_instance_id_unlocked(self.project)
        # Historical records may have an old contract, never another identity.
        if any(r["project_instance_id"] != instance for r in records.values()):
            raise EndingEvidenceError("ending review belongs to another project instance")
        payoffs = {item["id"]: item for item in contract.get("plot_payoffs", []) if isinstance(item, dict) and "id" in item}
        arcs = {item["character_id"]: item for item in contract.get("character_arcs", []) if isinstance(item, dict) and "character_id" in item}
        seen = set()
        for record in active:
            if record["contract_sha256"] != contract_sha256(contract):
                raise EndingEvidenceError("ending review contract has changed")
            for claim in record["claims"]:
                key = _claim_key(claim)
                if key in seen:
                    raise EndingEvidenceError("duplicate active ending claim; explicit supersession is required")
                seen.add(key)
                if claim["kind"] == "payoff":
                    if claim["payoff_id"] not in payoffs:
                        raise EndingEvidenceError("ending review claims an unknown payoff")
                    # "Open" is a chapter-local observation, not a permanent
                    # prohibition on fulfilling the promise later. Only an
                    # explicit open observation at/after this reviewed
                    # completion contradicts the supplemental paid claim.
                    completed_at = max(source["chapter"] for source in claim["sources"])
                    conflicting_open = any(
                        isinstance(event, dict)
                        and event.get("payoff_id") == claim["payoff_id"]
                        and event.get("status") == "intentional_open"
                        for chapter in state.chapters.values()
                        if completed_at <= chapter.number <= as_of
                        for event in chapter.payoff_events
                    )
                    if conflicting_open:
                        raise EndingEvidenceError("review cannot override intentionally open canonical payoff")
                else:
                    identifier = claim["character_id"]
                    if identifier not in arcs or identifier not in state.characters:
                        raise EndingEvidenceError("ending review claims an unknown character obligation")
                    obligation = arcs[identifier]
                    stage = str(obligation.get("required_arc_stage") or "").strip().lower()
                    outcome = str(obligation.get("required_outcome") or "").strip().lower()
                    if not stage and not outcome:
                        legacy = str(obligation.get("required_end_state") or "resolution").strip().lower()
                        if legacy in _STAGES:
                            stage = legacy
                        else:
                            outcome = legacy
                    if claim["arc_stage"] != stage or claim["outcome_state"].strip().lower() != outcome:
                        raise EndingEvidenceError("reviewed arc completion does not match contract obligation")
                    current = state.characters[identifier].outcome_state.strip().lower()
                    if current and current != claim["outcome_state"].strip().lower():
                        raise EndingEvidenceError("review cannot override conflicting canonical outcome")
                for source in claim["sources"]:
                    self._source(source, state, as_of)
        return active

    def load_validated(self, contract, state, as_of_chapter):
        """Return active source-checked records; never persist the given state."""
        try:
            self._safe(self.directory, missing=True)
            if not self.directory.exists():
                return []
            with ProjectLock(self.project):
                return self._validated(self._records(), contract, state, as_of_chapter)
        except EndingEvidenceError:
            raise
        except Exception as exc:
            raise EndingEvidenceError(f"cannot validate ending evidence: {exc}") from exc

    def record(self, review_payload):
        """Atomically append one reviewed payload, or return its exact replay."""
        try:
            payload = json.loads(_json(review_payload), object_pairs_hook=_object)
            _validate_schema(payload)
            identifier = "ending-review-" + hashlib.sha256(_json(payload)).hexdigest()
            record = {"review_id": identifier, **payload}
            serialized = _json(record) + b"\n"
            if len(serialized) > _MAX_RECORD_BYTES:
                raise EndingEvidenceError("ending review exceeds its storage limit")
            with ProjectLock(self.project):
                records = self._records()
                if identifier in records and records[identifier] != record:
                    raise EndingEvidenceError("ending review identity collision")
                prospective = {**records, identifier: record}
                self._safe(self.project / "outputs/state/story_state.json")
                state = StoryState(str(self.project))
                self._validated(prospective, self._contract(), state, max(state.chapters, default=0))
                if identifier in records:
                    return records[identifier]
                self.directory.mkdir(exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=".ending-review-", suffix=".tmp", dir=self.directory)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(serialized)
                        handle.flush()
                        os.fsync(handle.fileno())
                    # Hard-link publication is atomic and cannot replace an
                    # existing record, including after uncertain durability.
                    os.link(temporary, self.directory / f"{identifier}.json")
                    directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                finally:
                    os.unlink(temporary)
                return record
        except EndingEvidenceError:
            raise
        except Exception as exc:
            raise EndingEvidenceError(f"cannot record ending evidence: {exc}") from exc

    def input_hashes(self):
        """Checkpoint inputs, including membership/deletion and current sources.

        This is deliberately tolerant of corrupt review records: checkpoint
        comparison must invalidate them, then evaluation writes a useful gate
        report. It never treats a fingerprint as source validation.
        """
        def digest(relative):
            path = self.project / relative
            try:
                self._safe(path, missing=True)
                if not path.exists():
                    return "missing"
                if not path.is_file():
                    return "invalid:not-a-file"
                return hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, ValueError) as exc:
                return "invalid:" + hashlib.sha256(str(exc).encode()).hexdigest()

        empty = {"ending_evidence_review_members": hashlib.sha256(_json([])).hexdigest()}
        try:
            self._safe(self.directory, missing=True)
            if not self.directory.exists():
                return empty
            with ProjectLock(self.project):
                if not self.directory.is_dir():
                    return {"ending_evidence_review_storage": digest(REVIEWS_RELATIVE)}
                entries = [path for path in sorted(self.directory.iterdir())
                           if not _unpublished_temp(path)]
                paths = {str(path.relative_to(self.project)) for path in entries}
                if entries:
                    paths.update(("outputs/state/project_identity.json", "outputs/state/canon_ledger.jsonl",
                                  "outputs/artifacts/heads.json", "outputs/artifacts/revisions.jsonl",
                                  "outputs/input/ending_contract.json", "outputs/input/foundation.json"))
                # Collect safe source paths from any structurally readable
                # record; invalid JSON/schema remains represented by raw hash.
                for path in entries:
                    try:
                        record = self._read_json(path)
                        _fields(record, _FIELDS | {"review_id"}, "stored ending review")
                        _validate_schema({key: value for key, value in record.items() if key != "review_id"})
                    except (OSError, ValueError, TypeError):
                        continue
                    for claim in record["claims"]:
                        for source in claim["sources"]:
                            paths.update((f"outputs/state/promotion_receipts/{source['promotion_key']}.json",
                                          f"outputs/artifacts/sha256/{source['sha256']}",
                                          f"outputs/manuscript/chapter_{source['chapter']:03d}_final.md"))
                result = {"ending_evidence_review_members": hashlib.sha256(_json([path.name for path in entries])).hexdigest()}
                for relative in sorted(paths):
                    result[relative] = digest(relative)
                return result
        except Exception as exc:
            return {"ending_evidence_invalid_inputs": hashlib.sha256(str(exc).encode()).hexdigest()}


__all__ = ["EndingEvidenceError", "EndingEvidenceStore", "REVIEWS_RELATIVE", "contract_sha256"]
