"""Durable identity for one logical Novel OS project instance.

A raw filesystem copy preserves this identity and is therefore a replica of the
same logical project. A product-level clone/fork must establish a new identity
before accepting new promotion transactions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any

from project_lock import ProjectLock


SCHEMA_VERSION = 1
_INSTANCE_RE = re.compile(r"^[0-9a-f]{32}$")


class ProjectIdentityError(Exception):
    """The durable project identity is missing, malformed, or unsafe."""


class ProjectIdentityNotInitialized(ProjectIdentityError):
    """The project has not established its durable identity yet."""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _identity_path(project_root: os.PathLike[str] | str) -> Path:
    root = Path(os.path.abspath(os.fspath(project_root)))
    return root / "outputs" / "state" / "project_identity.json"


def _record(project_instance_id: str) -> bytes:
    identity = {
        "schema_version": SCHEMA_VERSION,
        "project_instance_id": project_instance_id,
    }
    return _json_bytes(
        {
            "identity": identity,
            "record_sha256": hashlib.sha256(_json_bytes(identity)).hexdigest(),
        }
    ) + b"\n"


def load_project_instance_id_unlocked(
    project_root: os.PathLike[str] | str,
) -> str:
    path = _identity_path(project_root)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise ProjectIdentityNotInitialized(
            "project identity is not initialized"
        ) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ProjectIdentityError(
            "project identity must be a regular non-symlink file"
        )
    try:
        payload = json.loads(path.read_bytes())
        if not isinstance(payload, dict) or set(payload) != {
            "identity",
            "record_sha256",
        }:
            raise ValueError("record has invalid fields")
        identity = payload["identity"]
        if not isinstance(identity, dict) or set(identity) != {
            "schema_version",
            "project_instance_id",
        }:
            raise ValueError("identity has invalid fields")
        if identity["schema_version"] != SCHEMA_VERSION:
            raise ValueError("identity schema version is invalid")
        project_instance_id = identity["project_instance_id"]
        if not isinstance(project_instance_id, str) or not _INSTANCE_RE.fullmatch(
            project_instance_id
        ):
            raise ValueError("project_instance_id is invalid")
        expected = hashlib.sha256(_json_bytes(identity)).hexdigest()
        if payload["record_sha256"] != expected:
            raise ValueError("identity record hash mismatch")
        return project_instance_id
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ProjectIdentityError(f"invalid project identity: {exc}") from exc


def _persist_identity_unlocked(root: Path, project_instance_id: str) -> None:
    path = _identity_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    published = False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_record(project_instance_id))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        published = True
        temporary = ""
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        qualifier = "may already be persisted" if published else "was not persisted"
        raise ProjectIdentityError(f"project identity {qualifier}: {exc}") from exc
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def ensure_project_instance_id_unlocked(
    project_root: os.PathLike[str] | str,
) -> str:
    """Load or establish identity while the caller holds the project lock."""
    root = Path(os.path.abspath(os.fspath(project_root)))
    try:
        return load_project_instance_id_unlocked(root)
    except ProjectIdentityNotInitialized:
        project_instance_id = uuid.uuid4().hex
        _persist_identity_unlocked(root, project_instance_id)
        return project_instance_id


def ensure_project_instance_id(project_root: os.PathLike[str] | str) -> str:
    root = Path(os.path.abspath(os.fspath(project_root)))
    with ProjectLock(root):
        return ensure_project_instance_id_unlocked(root)


def _has_promotion_history(root: Path) -> bool:
    ledger = root / "outputs" / "state" / "canon_ledger.jsonl"
    try:
        mode = ledger.lstat().st_mode
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ProjectIdentityError(
                "canon ledger must be a regular non-symlink file before fork"
            )
        if ledger.stat().st_size:
            return True

    state_dir = root / "outputs" / "state"
    for name in ("promotion_journal", "promotion_receipts"):
        directory = state_dir / name
        try:
            mode = directory.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ProjectIdentityError(
                f"{name} must be a real directory before project fork"
            )
        if any(directory.iterdir()):
            return True
    return False


def fork_project_instance_id(project_root: os.PathLike[str] | str) -> str:
    """Assign a new identity while retaining state/artifact projections.

    Forking is allowed only before receipt, journal, or canon-ledger history
    exists. Projects with promotion history require an explicit migration
    receipt rather than an in-place rekey.
    """
    root = Path(os.path.abspath(os.fspath(project_root)))
    with ProjectLock(root):
        load_project_instance_id_unlocked(root)
        if _has_promotion_history(root):
            raise ProjectIdentityError(
                "cannot fork project identity after promotion history exists"
            )
        project_instance_id = uuid.uuid4().hex
        _persist_identity_unlocked(root, project_instance_id)
        return project_instance_id


def load_project_instance_id(project_root: os.PathLike[str] | str) -> str:
    root = Path(os.path.abspath(os.fspath(project_root)))
    with ProjectLock(root):
        return load_project_instance_id_unlocked(root)


__all__ = [
    "ProjectIdentityError",
    "ProjectIdentityNotInitialized",
    "ensure_project_instance_id",
    "ensure_project_instance_id_unlocked",
    "fork_project_instance_id",
    "load_project_instance_id",
    "load_project_instance_id_unlocked",
]
