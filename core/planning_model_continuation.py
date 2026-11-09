"""Continue checked story-design work across model routes within one run."""
from __future__ import annotations

from collections import deque
import hashlib
import json
import re


# Decisions with published creative work can cross the model boundary. Raw
# replies, reviews and repairs remain bound to their original instructions.
_MODE = "global-seed-source-v1"
_DESIGN_UNIT = re.compile(
    r"global|global-seed-(?:source-)?v1|roadmap-v1-[0-9]+-[0-9]+(?:-split)?"
    r"|batch-[0-9]+(?:-part-[0-9]+-[0-9]+)?(?:-split)?"
)
_VERSION = "planning.continuation.v2"


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise ValueError("Invalid planning predecessor identifier")
    return value


def _json_file(path):
    if path.is_symlink():
        raise ValueError("Planning continuation record must be stored inside this run")
    return json.loads(path.read_bytes())


def _namespace(planner, identifier):
    from core.outline_planning import content_hash
    directory = planner.cache.parent / _identifier(identifier)
    path = directory / "identity.json"
    if directory.is_symlink() or not path.is_file():
        raise ValueError("Planning predecessor identity missing or outside this run")
    identity = _json_file(path)
    if not isinstance(identity, dict) or content_hash(identity) != identifier:
        raise ValueError("Planning predecessor identity checksum mismatch")

    def contract(value):
        return {k: v for k, v in value.items() if k != "model"}
    return directory if contract(identity) == contract(planner.identity) else None


def _ancestry(directory):
    from core.outline_planning import content_hash
    path = directory / "model-continuation.json"
    if not path.exists() and not path.is_symlink():
        return []
    record = _json_file(path)
    if not isinstance(record, dict):
        raise ValueError("Invalid planning continuation record")
    if set(record) == {"predecessor"}:  # earlier single-link version
        return [_identifier(record["predecessor"])]
    origins = record.get("predecessors")
    if (set(record) != {"version", "identity_hash", "predecessors", "sha256"}
            or record["version"] != _VERSION or record["identity_hash"] != directory.name
            or not isinstance(origins, list)
            or record["sha256"] != content_hash({k: v for k, v in record.items() if k != "sha256"})):
        raise ValueError("Planning continuation record checksum or identity mismatch")
    return [_identifier(origin) for origin in origins]


def open_predecessors(planner):
    """Persist this route's ancestry before changing the active run pointer.

    Walk only named records in this run (never similar-looking cache folders).
    A→B→A is normal: each address is read at most once per traversal.
    """
    from core.outline_planning import content_hash
    progress = planner.directory / "progress.json"
    previous = None
    if progress.exists() or progress.is_symlink():
        record = _json_file(progress)
        if not isinstance(record, dict):
            raise ValueError("Invalid planning progress record")
        previous = _identifier(record.get("identity_hash"))
    if planner.cache.is_symlink() or planner.cache.parent.is_symlink():
        raise ValueError("Planning namespace must be stored inside this run")
    identity = planner.cache / "identity.json"
    if identity.exists() or identity.is_symlink() or previous == planner.identity_hash:
        _namespace(planner, planner.identity_hash)
    original = _ancestry(planner.cache)
    pending = deque(original)
    if previous and previous != planner.identity_hash and _namespace(planner, previous) is not None:
        pending.appendleft(previous)
    visited = {planner.identity_hash}
    directories = []
    while pending:
        origin = pending.popleft()
        if origin in visited:
            continue
        visited.add(origin)
        directory = _namespace(planner, origin)
        if directory is None:
            raise ValueError("Planning continuation refers to incompatible story inputs")
        directories.append(directory)
        pending.extend(_ancestry(directory))
    origins = [path.name for path in directories]
    if origins != original:
        body = {"version": _VERSION, "identity_hash": planner.identity_hash, "predecessors": origins}
        planner._write_json(planner.cache / "model-continuation.json", {**body, "sha256": content_hash(body)})
    return directories


def is_design_unit(name):
    return bool(_DESIGN_UNIT.fullmatch(name))


def _check_source_address(record, name, namespaces):
    address = record.get("inherited_from")
    if "inherited_from" not in record:
        return
    if (not isinstance(address, dict) or set(address) != {"identity_hash", "checkpoint", "record_sha256"}
            or address["checkpoint"] != f"{name}.json"
            or address["identity_hash"] == record["identity_hash"]
            or _identifier(address["identity_hash"]) not in namespaces):
        raise ValueError(f"Invalid inherited checkpoint provenance for {name}")
    original = namespaces[address["identity_hash"]] / address["checkpoint"]
    if (not original.is_file() or original.is_symlink()
            or hashlib.sha256(original.read_bytes()).hexdigest() != address["record_sha256"]):
        raise ValueError(f"Inherited planning checkpoint {name} source checksum mismatch")


def continue_design_unit(planner, name, dependencies, validate):
    """Reuse only if all matching validated records agree; never clobber a fork."""
    from core.outline_planning import SCHEMA_VERSION, content_hash
    expected = content_hash(dependencies)
    chosen = None
    namespaces = {p.name: p for p in (planner.cache, *planner.predecessors)}
    for directory in namespaces.values():
        local = directory == planner.cache
        path = directory / f"{name}.json"
        if path.is_symlink():
            raise ValueError("Planning predecessor checkpoint must be stored inside this run")
        if not path.is_file():
            continue
        if not local and name == _MODE and not (directory / "global-seed-v1.json").is_file():
            # A scope decision alone, with no accepted creative work, must not
            # lock a smaller replacement model into an unrepresentable request.
            continue
        raw = path.read_bytes()
        record = json.loads(raw)
        if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION or record.get("identity_hash") != directory.name:
            raise ValueError(f"Incompatible planning predecessor checkpoint {name}")
        _check_source_address(record, name, namespaces)
        payload = record["payload"]
        if record["payload_hash"] != content_hash(payload):
            raise ValueError(f"Planning predecessor checkpoint {name} checksum mismatch")
        if record["dependencies_hash"] != expected:
            if local:
                raise ValueError(f"Existing checkpoint {name} belongs to different inputs; preserving both design branches")
            continue
        validated = validate(payload)
        digest = content_hash(validated)
        if chosen is None:
            chosen = validated, digest, directory, hashlib.sha256(raw).hexdigest()
        elif chosen[1] != digest:
            raise ValueError(f"Divergent validated design for {name}; preserving both branches without overwriting")
    if chosen is None:
        return None
    validated, _, directory, record_digest = chosen
    if directory != planner.cache:
        planner._save(name, dependencies, validated, inherited={
            "identity_hash": directory.name, "checkpoint": f"{name}.json", "record_sha256": record_digest,
        })
    return validated
