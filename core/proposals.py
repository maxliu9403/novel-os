"""Durable, immutable storage for canon delta proposals."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from canon import CanonDeltaProposal


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROPOSAL_ID_RE = re.compile(r"^proposal-[0-9a-f]{64}$")
_MANAGED_COMPONENTS = ("outputs", "state", "proposals")
_SECURE_DIR_FD = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.link in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)


class ProposalCommitUncertain(Exception):
    """Raised when a published proposal may not be durably committed."""

    def __init__(self, *, operation: str, proposal_id: str) -> None:
        self.operation = operation
        self.proposal_id = proposal_id
        super().__init__(
            f"{operation} for {proposal_id} may already be committed; "
            "reconcile the stored proposal by proposal_id before retrying"
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _record_sha256(proposal_data: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(proposal_data).encode("utf-8")).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _symlink_error(path: object) -> ValueError:
    return ValueError(f"proposal storage path must not be a symlink: {path}")


class ProposalStore:
    """Append-only proposal records rooted below a Novel OS project.

    POSIX operations access managed paths relative to open directory
    descriptors with ``O_NOFOLLOW``. Other platforms use a checked fallback.
    """

    def __init__(self, project_root: os.PathLike[str] | str):
        self.project_root = Path(project_root).resolve(strict=False)
        self.directory = self.project_root / "outputs" / "state" / "proposals"

    @staticmethod
    def _validate_proposal_id(proposal_id: str) -> None:
        if not isinstance(proposal_id, str) or not _PROPOSAL_ID_RE.fullmatch(proposal_id):
            raise ValueError("proposal_id must be proposal- followed by lowercase 64 hex")

    @staticmethod
    def _validate_source_sha(source_sha: str) -> None:
        if not isinstance(source_sha, str) or not _SHA256_RE.fullmatch(source_sha):
            raise ValueError("expected source artifact sha must be lowercase 64 hex")

    def _assert_safe_layout(self) -> None:
        current = self.project_root
        for component in _MANAGED_COMPONENTS:
            current = current / component
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError(
                    f"proposal storage parent must not be a symlink: {current}"
                )
            if not stat.S_ISDIR(metadata.st_mode):
                raise ValueError(
                    f"proposal storage parent must be a directory: {current}"
                )

    @contextmanager
    def _directory_fd(self, *, create: bool) -> Iterator[int]:
        if not _SECURE_DIR_FD:
            raise RuntimeError("secure directory descriptors are unavailable")
        if create:
            self.project_root.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current_fd = os.open(self.project_root, flags)
        try:
            for component in _MANAGED_COMPONENTS:
                try:
                    child_fd = os.open(component, flags, dir_fd=current_fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(component, mode=0o755, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                    child_fd = os.open(component, flags, dir_fd=current_fd)
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise _symlink_error(component) from exc
                    raise
                os.close(current_fd)
                current_fd = child_fd
            yield current_fd
        finally:
            os.close(current_fd)

    def _parse_record(
        self,
        raw: bytes,
        proposal_id: str,
        expected_source_artifact_sha: Optional[str],
    ) -> CanonDeltaProposal:
        try:
            text = raw.decode("utf-8")
            record = json.loads(text, object_pairs_hook=_strict_object)
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

    def _load_from_fd(
        self,
        directory_fd: int,
        proposal_id: str,
        expected_source_artifact_sha: Optional[str],
    ) -> CanonDeltaProposal:
        name = f"{proposal_id}.json"
        try:
            record_fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise _symlink_error(name) from exc
            raise
        try:
            if not stat.S_ISREG(os.fstat(record_fd).st_mode):
                raise ValueError("proposal record must be a regular file")
            with os.fdopen(record_fd, "rb", closefd=False) as handle:
                raw = handle.read()
        finally:
            os.close(record_fd)
        return self._parse_record(raw, proposal_id, expected_source_artifact_sha)

    def _save_secure(self, proposal: CanonDeltaProposal, payload: bytes) -> CanonDeltaProposal:
        target_name = f"{proposal.proposal_id}.json"
        with self._directory_fd(create=True) as directory_fd:
            try:
                return self._load_from_fd(
                    directory_fd,
                    proposal.proposal_id,
                    proposal.source_artifact_sha,
                )
            except FileNotFoundError:
                pass

            temporary_name = f".{proposal.proposal_id}.{os.urandom(8).hex()}.tmp"
            temporary_exists = False
            linked = False
            try:
                temp_fd = os.open(
                    temporary_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory_fd,
                )
                temporary_exists = True
                with os.fdopen(temp_fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(
                        temporary_name,
                        target_name,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    return self._load_from_fd(
                        directory_fd,
                        proposal.proposal_id,
                        proposal.source_artifact_sha,
                    )
                linked = True
                os.unlink(temporary_name, dir_fd=directory_fd)
                temporary_exists = False
                os.fsync(directory_fd)
                return proposal
            except OSError as exc:
                if linked:
                    raise ProposalCommitUncertain(
                        operation="proposal_save",
                        proposal_id=proposal.proposal_id,
                    ) from exc
                raise
            finally:
                if temporary_exists:
                    try:
                        os.unlink(temporary_name, dir_fd=directory_fd)
                    except FileNotFoundError:
                        pass

    def _save_compat(self, proposal: CanonDeltaProposal, payload: bytes) -> CanonDeltaProposal:
        self._assert_safe_layout()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._assert_safe_layout()
        target = self.directory / f"{proposal.proposal_id}.json"
        if target.exists() or target.is_symlink():
            return self.load(
                proposal.proposal_id,
                expected_source_artifact_sha=proposal.source_artifact_sha,
            )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{proposal.proposal_id}.", suffix=".tmp", dir=self.directory
        )
        temporary = Path(temporary_name)
        linked = False
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
            linked = True
            temporary.unlink()
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return proposal
        except OSError as exc:
            if linked:
                raise ProposalCommitUncertain(
                    operation="proposal_save",
                    proposal_id=proposal.proposal_id,
                ) from exc
            raise
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def save(self, proposal: CanonDeltaProposal) -> CanonDeltaProposal:
        """Persist once, returning the first valid record for this identity."""
        if not isinstance(proposal, CanonDeltaProposal):
            raise TypeError("proposal must be a CanonDeltaProposal")
        proposal = CanonDeltaProposal.from_dict(proposal.to_dict())
        proposal_data = proposal.to_dict()
        record = {
            "proposal": proposal_data,
            "record_sha256": _record_sha256(proposal_data),
        }
        payload = (_canonical_json(record) + "\n").encode("utf-8")
        if _SECURE_DIR_FD:
            return self._save_secure(proposal, payload)
        return self._save_compat(proposal, payload)

    def _load_compat(
        self,
        proposal_id: str,
        expected_source_artifact_sha: Optional[str],
    ) -> CanonDeltaProposal:
        self._assert_safe_layout()
        path = self.directory / f"{proposal_id}.json"
        if path.is_symlink():
            raise ValueError("proposal record must not be a symlink")
        return self._parse_record(
            path.read_bytes(), proposal_id, expected_source_artifact_sha
        )

    def load(
        self,
        proposal_id: str,
        expected_source_artifact_sha: Optional[str] = None,
    ) -> CanonDeltaProposal:
        """Load a proposal after validating record, identity, path, and source."""
        self._validate_proposal_id(proposal_id)
        if expected_source_artifact_sha is not None:
            self._validate_source_sha(expected_source_artifact_sha)
        if _SECURE_DIR_FD:
            with self._directory_fd(create=False) as directory_fd:
                return self._load_from_fd(
                    directory_fd, proposal_id, expected_source_artifact_sha
                )
        return self._load_compat(proposal_id, expected_source_artifact_sha)


__all__ = ["ProposalCommitUncertain", "ProposalStore"]
