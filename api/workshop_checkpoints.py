"""Durable, append-only delivery results for an authorized workshop session.

The service owns the session lock, model validation and revision/job fencing.
This module owns bounded storage and artifact integrity; reads never create a
directory or invoke a model. ``directory`` is the session's delivery directory.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .workshop_assembly import ASSEMBLY_PARTS, ASSEMBLY_PART_LABELS, ASSEMBLY_PROTOCOL_VERSION

SCHEMA_VERSION = "workshop.delivery.v1"
STAGES = ("story_design", "chapter_plan", "assembly")
MAX_CHECKPOINT_BYTES = 4 * 1024 * 1024
_IDENTITY = ("schema_version", "run_id", "session_id", "source_revision",
             "input_sha256", "skill_version", "narrative_mode")
_FIELDS = frozenset((*_IDENTITY, "active_stage", "completed"))
_ARTIFACT_FIELDS = frozenset(("content", "content_md", "sha256", "saved_at"))
_PART_FIELDS = frozenset(("protocol_version", "active_part", "completed", "revisions"))
_ID = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class CheckpointError(ValueError):
    """Authored errors only: never include story text, paths or provider data."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CheckpointError("Delivery checkpoint contains invalid JSON data.") from exc


def make_fingerprint(context: dict, skill_version: str) -> str:
    """Hash the entire caller-selected story context and skill version.

    The caller supplies story context only, never credentials or provider
    configuration. No context fields are removed, shortened or logged here.
    """
    if not isinstance(context, dict) or not isinstance(skill_version, str) or not skill_version.strip():
        raise CheckpointError("Delivery checkpoint input identity is invalid.")
    return hashlib.sha256(_canonical({"context": context, "skill_version": skill_version})).hexdigest()


def _check_id(value: object) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise CheckpointError("Delivery checkpoint run or session identity is invalid.")


def _timestamp(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _artifact_digest(artifact: dict, previous: str, identity: dict, *, fill_hashes: bool,
                     extra_fields: frozenset = frozenset()) -> str:
    if not isinstance(artifact, dict):
        raise CheckpointError("Delivery checkpoint artifact is invalid.")
    expected = (_ARTIFACT_FIELDS | extra_fields) - ({"sha256"} if fill_hashes and "sha256" not in artifact else set())
    if set(artifact) != expected:
        raise CheckpointError("Delivery checkpoint artifact fields are invalid.")
    if not isinstance(artifact["content"], dict) or not isinstance(artifact["content_md"], str):
        raise CheckpointError("Delivery checkpoint artifact content is invalid.")
    if not artifact["content_md"].strip() or not _timestamp(artifact["saved_at"]):
        raise CheckpointError("Delivery checkpoint artifact presentation is invalid.")
    digest = hashlib.sha256(_canonical({
        "previous_sha256": previous, **identity, "content": artifact["content"],
        "content_md": artifact["content_md"], "saved_at": artifact["saved_at"],
    })).hexdigest()
    if "sha256" in artifact and artifact["sha256"] != digest:
        raise CheckpointError("Delivery checkpoint artifact integrity check failed.")
    if "sha256" not in artifact and not fill_hashes:
        raise CheckpointError("Delivery checkpoint artifact hash is missing.")
    artifact["sha256"] = digest
    return digest


def _validated_parts(record: dict, previous: str, *, fill_hashes: bool) -> str:
    """Extend the stage chain after chapter_plan; old stage digests stay intact."""
    parts = record["assembly_parts"]
    if not isinstance(parts, dict) or set(parts) != _PART_FIELDS:
        raise CheckpointError("Delivery assembly checkpoint fields are invalid.")
    if parts["protocol_version"] != ASSEMBLY_PROTOCOL_VERSION:
        raise CheckpointError("Delivery assembly checkpoint protocol is unsupported.")
    completed = parts["completed"]
    if not isinstance(completed, dict) or set(completed) != set(ASSEMBLY_PARTS[:len(completed)]):
        raise CheckpointError("Delivery assembly checkpoint parts are incomplete or out of order.")
    active = parts["active_part"]
    next_part = ASSEMBLY_PARTS[len(completed)] if len(completed) < len(ASSEMBLY_PARTS) else None
    if active is not None:
        if (next_part is not None and active != next_part) or active not in ASSEMBLY_PARTS:
            raise CheckpointError("Delivery assembly checkpoint active part is invalid.")
        if record["active_stage"] != "assembly":
            raise CheckpointError("Delivery assembly checkpoint active part has no active assembly stage.")
    revisions = parts["revisions"]
    if not isinstance(revisions, list) or (revisions and next_part is not None):
        raise CheckpointError("Delivery assembly checkpoint revisions are invalid.")
    if "assembly" in record["completed"] and (next_part is not None or active is not None):
        raise CheckpointError("Delivery assembly cannot be finalized before all parts are saved.")
    # The protocol label prevents a part from being transplanted into a different
    # protocol. The chapter digest already binds the complete author identity and
    # both previously saved stage artifacts.
    previous = hashlib.sha256(_canonical({"previous_sha256": previous,
                                          "protocol_version": parts["protocol_version"]})).hexdigest()
    for part in ASSEMBLY_PARTS[:len(completed)]:
        previous = _artifact_digest(completed[part], previous, {"part": part}, fill_hashes=fill_hashes)
    for index, revision in enumerate(revisions, 1):
        if not isinstance(revision, dict) or revision.get("part") not in ASSEMBLY_PARTS:
            raise CheckpointError("Delivery assembly checkpoint revision part is invalid.")
        previous = _artifact_digest(revision, previous, {"part": revision["part"], "revision": index},
                                    fill_hashes=fill_hashes, extra_fields=frozenset({"part"}))
    return previous


def _validated(record: dict, run_id: str, *, fill_hashes: bool = False) -> dict:
    _check_id(run_id)
    # Round-trip creates an independent copy and rejects non-JSON values.
    record = _decode(_canonical(record))
    if not isinstance(record, dict) or set(record) not in (_FIELDS, _FIELDS | {"assembly_parts"}):
        raise CheckpointError("Delivery checkpoint fields are invalid.")
    if record["schema_version"] != SCHEMA_VERSION:
        raise CheckpointError("Delivery checkpoint version is unsupported.")
    _check_id(record["run_id"])
    _check_id(record["session_id"])
    if record["run_id"] != run_id:
        raise CheckpointError("Delivery checkpoint belongs to another run.")
    if type(record["source_revision"]) is not int or record["source_revision"] < 0:
        raise CheckpointError("Delivery checkpoint source revision is invalid.")
    if not isinstance(record["input_sha256"], str) or not _SHA.fullmatch(record["input_sha256"]):
        raise CheckpointError("Delivery checkpoint input fingerprint is invalid.")
    for key in ("skill_version", "narrative_mode"):
        if not isinstance(record[key], str) or not record[key].strip() or len(record[key]) > 200:
            raise CheckpointError("Delivery checkpoint input identity is invalid.")
    completed = record["completed"]
    if not isinstance(completed, dict) or set(completed) != set(STAGES[:len(completed)]):
        raise CheckpointError("Delivery checkpoint stages are incomplete or out of order.")
    next_stage = STAGES[len(completed)] if len(completed) < len(STAGES) else None
    if record["active_stage"] is not None and record["active_stage"] != next_stage:
        raise CheckpointError("Delivery checkpoint active stage is invalid.")
    if "assembly_parts" in record and len(completed) < 2:
        raise CheckpointError("Delivery assembly parts require the complete saved story and chapter plan.")
    previous = hashlib.sha256(_canonical({key: record[key] for key in _IDENTITY})).hexdigest()
    for stage in STAGES[:len(completed)]:
        if stage == "assembly" and "assembly_parts" in record:
            previous = _validated_parts(record, previous, fill_hashes=fill_hashes)
        previous = _artifact_digest(completed[stage], previous, {"stage": stage}, fill_hashes=fill_hashes)
    if "assembly_parts" in record and "assembly" not in completed:
        _validated_parts(record, previous, fill_hashes=fill_hashes)
    return record


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, entry in pairs:
        if key in value:
            raise CheckpointError("Delivery checkpoint contains duplicate JSON fields.")
        value[key] = entry
    return value


def _decode(payload: bytes) -> dict:
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CheckpointError("Delivery checkpoint data cannot be decoded.") from exc


def _open_directory(directory: Path, *, create: bool) -> int | None:
    """Open each component without following links, including parent folders."""
    directory = Path(directory)
    if ".." in directory.parts:
        raise CheckpointError("Delivery checkpoint directory is invalid.")
    parts = directory.absolute().parts
    fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(parts[1:], start=1):
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    return None
                if index != len(parts) - 1:
                    raise CheckpointError("Delivery checkpoint session directory is missing.")
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.fsync(fd)
            os.close(fd)
            fd = child
        result, fd = fd, None
        return result
    except OSError as exc:
        raise CheckpointError("Delivery checkpoint directory is unavailable or unsafe.") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _read(directory_fd: int, run_id: str) -> dict | None:
    try:
        fd = os.open(f"{run_id}.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CheckpointError("Delivery checkpoint file is unavailable or unsafe.") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CheckpointError("Delivery checkpoint must be a regular private file.")
        if info.st_size > MAX_CHECKPOINT_BYTES:
            raise CheckpointError("Delivery checkpoint exceeds the storage byte limit; no content was truncated.")
        chunks, size = [], 0
        while size <= MAX_CHECKPOINT_BYTES:
            chunk = os.read(fd, min(65536, MAX_CHECKPOINT_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > MAX_CHECKPOINT_BYTES:
            raise CheckpointError("Delivery checkpoint exceeds the storage byte limit; no content was truncated.")
        return _validated(_decode(b"".join(chunks)), run_id)
    except OSError as exc:
        raise CheckpointError("Delivery checkpoint could not be read.") from exc
    finally:
        os.close(fd)


def load_checkpoint(directory: Path, run_id: str) -> dict | None:
    """Return a fully verified saved record; absent records have no side effects."""
    _check_id(run_id)
    fd = _open_directory(directory, create=False)
    if fd is None:
        return None
    try:
        return _read(fd, run_id)
    finally:
        os.close(fd)


def save_checkpoint(directory: Path, run_id: str, record: dict) -> None:
    """Atomically append stages. Caller must hold the matching session lock.

    Missing artifact hashes are filled in the saved copy; the caller's record is
    not mutated. Corrupt existing records must never be silently overwritten.
    """
    current = _validated(record, run_id, fill_hashes=True)
    payload = _canonical(current)
    if len(payload) > MAX_CHECKPOINT_BYTES:
        raise CheckpointError("Delivery checkpoint exceeds the storage byte limit; no content was truncated.")
    fd = _open_directory(directory, create=True)
    temporary = f".checkpoint-{uuid.uuid4().hex}"
    try:
        previous = _read(fd, run_id)
        if previous is not None:
            if any(previous[key] != current[key] for key in _IDENTITY):
                raise CheckpointError("Delivery checkpoint input identity cannot be changed.")
            for stage, artifact in previous["completed"].items():
                if current["completed"].get(stage) != artifact:
                    raise CheckpointError("Saved delivery checkpoint stages cannot be replaced or removed.")
            prior_parts = previous.get("assembly_parts")
            if prior_parts is not None:
                current_parts = current.get("assembly_parts")
                if current_parts is None or current_parts["protocol_version"] != prior_parts["protocol_version"]:
                    raise CheckpointError("Saved delivery assembly protocol cannot be replaced or removed.")
                for part, artifact in prior_parts["completed"].items():
                    if current_parts["completed"].get(part) != artifact:
                        raise CheckpointError("Saved delivery assembly parts cannot be replaced or removed.")
                count = len(prior_parts["revisions"])
                if current_parts["revisions"][:count] != prior_parts["revisions"]:
                    raise CheckpointError("Saved delivery assembly revisions cannot be replaced or removed.")
        output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=fd)
        with os.fdopen(output, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, f"{run_id}.json", src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    except OSError as exc:
        raise CheckpointError("Delivery checkpoint could not be safely saved.") from exc
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(fd)


def project_checkpoint(record: dict, session_status: str, source_revision: int | None = None) -> dict:
    """Public stage results only; this projection never infers model activity."""
    current = _validated(record, record.get("run_id") if isinstance(record, dict) else None)
    if source_revision is not None and current["source_revision"] != source_revision:
        raise CheckpointError("Delivery checkpoint source revision does not match.")
    completed = [stage for stage in STAGES if stage in current["completed"]]
    complete = len(completed) == len(STAGES)
    status = "complete" if complete else session_status if session_status in {"running", "paused", "error"} else "paused"
    active = current["active_stage"] if not complete else None
    result = {
        "run_id": current["run_id"], "source_revision": current["source_revision"],
        "status": status, "completed_stages": completed, "active_stage": active,
        # An assembly can be durable before the session's final publish. After
        # interruption, resume that local commit without asking the model again.
        "resumable": bool(completed) and session_status in {"paused", "error"},
        "stages": [{"stage": stage,
                    "status": "saved" if stage in completed else "running" if status == "running" and stage == active else "pending",
                    "content_md": current["completed"][stage]["content_md"] if stage in completed else ""}
                   for stage in STAGES],
    }
    if "assembly_parts" in current:
        parts = current["assembly_parts"]
        artifacts = {**parts["completed"]}
        artifacts.update({revision["part"]: revision for revision in parts["revisions"]})
        active_part = parts["active_part"] if not complete else None
        result["assembly"] = {
            "completed_parts": len(parts["completed"]), "total_parts": len(ASSEMBLY_PARTS),
            "active_part": active_part,
            "parts": [{"part": part, "label": ASSEMBLY_PART_LABELS[part],
                       "status": "running" if status == "running" and part == active_part else "saved" if part in artifacts else "pending",
                       "content_md": artifacts[part]["content_md"] if part in artifacts else ""}
                      for part in ASSEMBLY_PARTS],
        }
    return result
