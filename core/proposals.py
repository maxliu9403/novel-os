"""Durable, immutable storage for canon delta proposals."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional

from canon import CanonDeltaProposal


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROPOSAL_ID_RE = re.compile(r"^proposal-[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_sha256(proposal_data: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(proposal_data).encode("utf-8")).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


class ProposalStore:
    """Append-only proposal records rooted below a Novel OS project."""

    def __init__(self, project_root: os.PathLike[str] | str):
        self.project_root = Path(project_root)
        self.directory = self.project_root / "outputs" / "state" / "proposals"

    @staticmethod
    def _validate_proposal_id(proposal_id: str) -> None:
        if not isinstance(proposal_id, str) or not _PROPOSAL_ID_RE.fullmatch(proposal_id):
            raise ValueError("proposal_id must be proposal- followed by lowercase 64 hex")

    @staticmethod
    def _validate_source_sha(source_sha: str) -> None:
        if not isinstance(source_sha, str) or not _SHA256_RE.fullmatch(source_sha):
            raise ValueError("expected source artifact sha must be lowercase 64 hex")

    def save(self, proposal: CanonDeltaProposal) -> CanonDeltaProposal:
        """Persist once, returning the first valid record for this identity."""
        if not isinstance(proposal, CanonDeltaProposal):
            raise TypeError("proposal must be a CanonDeltaProposal")
        # Reconstructing validates every identity field before touching disk.
        proposal = CanonDeltaProposal.from_dict(proposal.to_dict())
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{proposal.proposal_id}.json"
        if target.exists() or target.is_symlink():
            return self.load(
                proposal.proposal_id,
                expected_source_artifact_sha=proposal.source_artifact_sha,
            )

        proposal_data = proposal.to_dict()
        record = {
            "proposal": proposal_data,
            "record_sha256": _record_sha256(proposal_data),
        }
        payload = (_canonical_json(record) + "\n").encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{proposal.proposal_id}.",
            suffix=".tmp",
            dir=self.directory,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                return self.load(
                    proposal.proposal_id,
                    expected_source_artifact_sha=proposal.source_artifact_sha,
                )
            _fsync_directory(self.directory)
            return proposal
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            _fsync_directory(self.directory)

    def load(
        self,
        proposal_id: str,
        expected_source_artifact_sha: Optional[str] = None,
    ) -> CanonDeltaProposal:
        """Load a proposal after validating record, identity, path, and source."""
        self._validate_proposal_id(proposal_id)
        if expected_source_artifact_sha is not None:
            self._validate_source_sha(expected_source_artifact_sha)
        path = self.directory / f"{proposal_id}.json"
        if path.is_symlink():
            raise ValueError("proposal record must not be a symlink")
        try:
            raw = path.read_text(encoding="utf-8")
            record = json.loads(raw, object_pairs_hook=_strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("proposal record is not valid UTF-8 JSON") from exc
        if not isinstance(record, dict) or set(record) != {"proposal", "record_sha256"}:
            raise ValueError("proposal record has invalid fields")
        proposal_data = record["proposal"]
        record_hash = record["record_sha256"]
        if not isinstance(proposal_data, dict):
            raise ValueError("proposal record proposal must be an object")
        if not isinstance(record_hash, str) or not _SHA256_RE.fullmatch(record_hash):
            raise ValueError("proposal record hash has invalid format")
        if record_hash != _record_sha256(proposal_data):
            raise ValueError("proposal record hash mismatch")

        proposal = CanonDeltaProposal.from_dict(proposal_data)
        if proposal.proposal_id != proposal_id:
            raise ValueError("proposal record identity does not match path")
        if (
            expected_source_artifact_sha is not None
            and proposal.source_artifact_sha != expected_source_artifact_sha
        ):
            raise ValueError("proposal source artifact sha mismatch")
        return proposal
