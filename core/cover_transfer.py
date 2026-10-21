"""Explicit, immutable Workshop cover import into a project in the same workspace.

Imported directions and approvals are historical evidence, never target-side
spending authority. Only candidate/media projections enter the target store.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .cover_models import CoverCandidate
from .cover_sources import (CoverSourceContext, CoverSourceError, CoverSourceSelector,
                            canonical_hash, load_snapshot, preview_source)
from .cover_store import CoverConflict, CoverStore
from .cover_typography_amendments import validate_candidate_amendment
from .image_binary import content_type, dimensions

_SHA = re.compile(r"^[0-9a-f]{64}$")
_KEY = re.compile(r"^transfer-[0-9a-f]{64}$")
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _scope(value: str) -> tuple[str, str, str]:
    parts = value.rsplit(":", 2)
    if len(parts) != 3 or not all(parts) or parts[1] not in {"project", "workshop"}:
        raise CoverConflict("Cover transfer requires an explicit workspace:owner:id scope")
    return tuple(parts)


def _safe(root: Path, relative: str) -> Path:
    path = Path(relative)
    if not relative or path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise CoverConflict("Unsafe cover import path")
    if root.is_symlink():
        raise CoverConflict("Cover import root cannot be a symlink")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise CoverConflict("Cover import paths cannot contain symlinks")
    if not current.resolve().is_relative_to(root.resolve()):
        raise CoverConflict("Cover import path escaped its owner")
    return current


def _inventory(root: Path) -> dict[str, str]:
    if root.is_symlink():
        raise CoverConflict("Cover source root cannot be a symlink")
    result = {}
    if not root.exists():
        return result
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(directory) / name
            if path.is_symlink():
                raise CoverConflict("Cover transfer refuses symlinked source assets")
        for name in sorted(files):
            if name.endswith(".lock") or (name.startswith(".") and name.endswith(".tmp")):
                continue
            path = Path(directory) / name
            if not path.is_file():
                raise CoverConflict("Cover transfer only accepts regular files")
            relative = path.relative_to(root).as_posix()
            result[relative] = hashlib.sha256(_safe(root, relative).read_bytes()).hexdigest()
    return dict(sorted(result.items()))


def _write_new(path: Path, data: bytes) -> None:
    """Atomic create-or-verify: never overwrite a conflicting import artifact."""
    if path.exists():
        if path.is_symlink() or path.read_bytes() != data:
            raise CoverConflict(f"Imported artifact conflicts with existing data: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise CoverConflict("Concurrent cover import conflicts with existing data")
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


@contextmanager
def _transfer_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = _safe(root, ".cover-transfer.lock")
    with lock_path.open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _target_bundle(context, project_id):
    try:
        preview = preview_source(context, CoverSourceSelector("project_input", project_id=project_id))
        return preview.source_bundle_sha256, ""
    except CoverSourceError as exc:
        if exc.code in {"invalid_scope", "invalid_selector"}:
            raise
        return "", exc.code


def _approval_verified(store, binding: Mapping[str, Any]) -> bool:
    """Check immutable direction/approval integrity without demanding live freshness."""
    direction_id = binding.get("direction_id")
    if not direction_id:
        return False
    direction = store.load_direction(direction_id)
    if direction.direction_sha256 != binding.get("direction_sha256"):
        raise CoverConflict("Imported candidate direction hash does not match its provenance")
    direction_binding = direction.source_binding or {}
    if any(binding.get(key) != value for key, value in direction_binding.items()):
        raise CoverConflict("Imported candidate source binding differs from its direction")
    path = _safe(store.root, f"approvals/{direction_id}.json")
    if not path.is_file():
        return False
    approval = json.loads(path.read_text("utf-8"))
    return bool(approval.get("source_confirmed") and not approval.get("revoked")
                and direction.status not in {"rejected"}
                and approval.get("direction_sha256") == direction.direction_sha256
                and approval.get("binding_sha256") == canonical_hash(direction_binding)
                and approval.get("recipes_sha256") == canonical_hash(direction.compiled_recipes))


def _verify_manifest(folder: Path) -> dict[str, Any]:
    path = _safe(folder, "manifest.json")
    manifest = json.loads(path.read_text("utf-8"))
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest.get("manifest_sha256") != canonical_hash(unsigned):
        raise CoverConflict("Cover import manifest integrity failed")
    if not _KEY.fullmatch(str(manifest.get("transfer_key", ""))) or folder.name != manifest["transfer_key"]:
        raise CoverConflict("Cover import identity mismatch")
    provenance = _safe(folder, "provenance")
    if _inventory(provenance) != manifest["source_files"]:
        raise CoverConflict("Imported historical provenance changed")
    key_input = {"schema_version": 1, "source_owner": manifest["source_owner"],
                 "target_owner": manifest["target_owner"], "source_files": manifest["source_files"]}
    if manifest["transfer_key"] != "transfer-" + canonical_hash(key_input):
        raise CoverConflict("Imported historical provenance identity failed")
    for asset in manifest["assets"].values():
        data = _safe(folder, asset["import_relative_path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise CoverConflict("Imported image bytes changed")
    return manifest


def transfer_workshop_covers(source_context, target_context, source_store, target_store,
                            media_store, media_add, target_project_id) -> dict[str, Any]:
    """Copy one stable cover history and register reusable, non-active candidates.

    Repeating the same source history returns the same committed manifest and
    never creates a second media row, candidate or budget allowance.
    """
    source_workspace, source_kind, session_id = _scope(source_context.owner_scope)
    target_workspace, target_kind, project_id = _scope(target_context.owner_scope)
    if (source_workspace != target_workspace or source_kind != "workshop" or target_kind != "project"
            or project_id != target_project_id):
        raise CoverConflict("Cover transfer is limited to a Workshop and project in the same workspace")
    for context, store in ((source_context, source_store), (target_context, target_store)):
        if context.cover_root.is_symlink() or context.cover_root.resolve() != store.root.resolve():
            raise CoverConflict("Cover transfer store does not match the scoped owner")
        if not store.root.resolve().is_relative_to(store.project_path.resolve()):
            raise CoverConflict("Cover transfer root is outside the scoped owner")
        _safe(store.project_path, store.root.relative_to(store.project_path).as_posix())
    if source_context.workshop_resolver and source_context.workshop_resolver(session_id).resolve() != source_store.project_path:
        raise CoverConflict("Workshop resolver does not match source cover owner")
    if not target_context.project_resolver or target_context.project_resolver(project_id).resolve() != target_store.project_path:
        raise CoverConflict("Project resolver does not match target cover owner")
    with _transfer_lock(target_store.root):
        source_files = _inventory(source_store.root)
        key_input = {"schema_version": 1, "source_owner": source_context.owner_scope,
                     "target_owner": target_context.owner_scope, "source_files": source_files}
        key = "transfer-" + canonical_hash(key_input)
        folder = _safe(target_store.root, f"imports/{key}")
        if _safe(folder, "manifest.json").is_file():
            return _verify_manifest(folder)
        target_bundle, target_error = _target_bundle(target_context, project_id)
        sets = source_store.list()
        namespace = f"workshop-{session_id}"
        if any(item.project_id != namespace for item in sets):
            raise CoverConflict("Workshop cover sets contain a foreign media owner")
        # Amendment approval is bound to the original owner, candidate and
        # recipe. Import does not rebind that authority; reject before creating
        # provenance copies or media rows rather than publish unusable candidates.
        for cover_set in sets:
            concepts = {item.concept_id: item for item in cover_set.concepts}
            for candidate in cover_set.candidates:
                if validate_candidate_amendment(source_store.root, cover_set, candidate,
                                                concepts[candidate.concept_id]) is not None:
                    raise CoverConflict("Typography-amended Workshop cover history cannot be imported; "
                                        "cross-owner amendment approval is not supported")
        # Verify all captured sources, including histories without generated images.
        for relative in source_files:
            if re.fullmatch(r"sources/snapshot-[0-9a-f]{32}/manifest.json", relative):
                load_snapshot(source_context, Path(relative).parts[1])
        for relative, expected in source_files.items():
            data = _safe(source_store.root, relative).read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise CoverConflict("Workshop covers changed during transfer")
            _write_new(_safe(folder, f"provenance/{relative}"), data)

        assets: dict[str, dict[str, Any]] = {}
        media_map: dict[str, str] = {}
        def copy_asset(sha: str, old_media_id: str = "", relative: str = "") -> dict[str, Any]:
            if not _SHA.fullmatch(sha):
                raise CoverConflict("Imported media requires a valid content digest")
            if sha in assets:
                if old_media_id:
                    media_map[old_media_id] = assets[sha]["media_id"]
                return assets[sha]
            # A candidate's projected filename may now contain a later attempt;
            # content-addressed media is authoritative for the older attempt.
            data = None
            for extension in _EXTENSIONS.values():
                data = media_store.read(namespace, sha, extension)
                if data is not None:
                    break
            if data is None and relative:
                data = _safe(source_store.project_path, relative).read_bytes()
            if data is None or hashlib.sha256(data).hexdigest() != sha:
                raise CoverConflict("Imported image bytes are missing or fail their digest")
            mime, (width, height) = content_type(data), dimensions(data)
            extension = _EXTENSIONS.get(mime)
            if not extension or not width or not height:
                raise CoverConflict("Imported image format or dimensions are invalid")
            local = f"media/{sha}{extension}"
            destination = _safe(folder, local)
            _write_new(destination, data)
            media_store.put(project_id, sha, extension, data)
            record = media_add(project_id=project_id, sha=sha, ext=extension,
                filename=f"imported-cover-{sha[:12]}{extension}", content_type=mime,
                size=len(data), width=width, height=height, kind="cover", alt="Imported Workshop cover asset")
            media_id = str(record.get("id", "") if isinstance(record, Mapping) else getattr(record, "id", ""))
            if not media_id:
                raise CoverConflict("Imported media registration did not return an id")
            asset = {"sha256": sha, "media_id": media_id, "content_type": mime, "width": width,
                     "height": height, "import_relative_path": local,
                     "relative_path": destination.relative_to(target_store.project_path).as_posix()}
            assets[sha] = asset
            if old_media_id:
                media_map[old_media_id] = media_id
            return asset

        def rewrite_assets(value: Any) -> Any:
            if isinstance(value, (list, tuple)):
                return [rewrite_assets(item) for item in value]
            if not isinstance(value, Mapping):
                return value
            result = {name: rewrite_assets(item) for name, item in value.items()}
            # Supplemental artwork lives in evaluation/attempt dictionaries.
            if result.get("artwork_sha256"):
                asset = copy_asset(result["artwork_sha256"], result.get("artwork_media_id", ""), result.get("artwork_relative_path", ""))
                result.update(artwork_media_id=asset["media_id"], artwork_relative_path=asset["relative_path"])
            if result.get("media_id") and (result.get("image_sha256") or result.get("sha256")):
                asset = copy_asset(result.get("image_sha256") or result["sha256"], result["media_id"], result.get("relative_path", ""))
                result.update(media_id=asset["media_id"], relative_path=asset["relative_path"])
            # Read-only JSON report locations continue to point to immutable
            # original content inside the imported provenance directory.
            relative = result.get("relative_path")
            if isinstance(relative, str) and relative.endswith(".json"):
                original = _safe(source_store.project_path, relative)
                if original.is_relative_to(source_store.root):
                    copied = _safe(folder, "provenance/" + original.relative_to(source_store.root).as_posix())
                    if not copied.is_file():
                        raise CoverConflict("Imported evaluation report is missing")
                    result["relative_path"] = copied.relative_to(target_store.project_path).as_posix()
            return result

        def new_id(kind: str, original: str) -> str:
            return kind + "-" + hashlib.sha256(f"{key}:{kind}:{original}".encode()).hexdigest()[:32]

        # Historical evaluations can reference artwork superseded by a later
        # attempt. Register those assets too, even when no current candidate
        # points to them; the original reports themselves remain immutable.
        for relative, file_digest in source_files.items():
            if relative.startswith("evaluations/") and relative.endswith(".json"):
                rewrite_assets(json.loads(_safe(source_store.root, relative).read_text("utf-8")))
            elif Path(relative).suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}:
                original_path = _safe(source_store.root, relative)
                copy_asset(file_digest, relative=original_path.relative_to(source_store.project_path).as_posix())

        projected = []
        imported_sets = []
        id_map = {"cover_sets": {}, "candidates": {}, "media": media_map}
        for original in sets:
            new_set_id = new_id("cover", original.cover_set_id)
            id_map["cover_sets"][original.cover_set_id] = new_set_id
            concepts, candidates, matches = [], [], []
            for concept in original.concepts:
                scene = deepcopy(concept.scene_plan)
                binding = scene.get("_source_binding") or {}
                snapshot = None
                approval_verified = False
                if binding:
                    if binding.get("owner_scope") != source_context.owner_scope:
                        raise CoverConflict("Imported candidate references another source owner")
                    snapshot = load_snapshot(source_context, binding["snapshot_id"])
                    if (binding.get("source_bundle_sha256") != snapshot.source_bundle_sha256
                            or binding.get("snapshot_sha256") != snapshot.snapshot_sha256):
                        raise CoverConflict("Imported candidate source snapshot binding is invalid")
                    approval_verified = _approval_verified(source_store, binding)
                matched = bool(snapshot and target_bundle and snapshot.source_bundle_sha256 == target_bundle)
                matches.append(matched)
                scene["_cover_import"] = {"transfer_key": key, "source_owner": source_context.owner_scope,
                    "source_set_id": original.cover_set_id, "source_snapshot_id": snapshot.snapshot_id if snapshot else "",
                    "source_bundle_sha256": snapshot.source_bundle_sha256 if snapshot else "",
                    "target_bundle_sha256": target_bundle, "source_match": "matched" if matched else "mismatched" if snapshot and target_bundle else "unknown",
                    "source_approval_verified": approval_verified, "new_image_calls_authorized": False,
                    "manifest_relative_path": (folder / "manifest.json").relative_to(target_store.project_path).as_posix()}
                scene["_original_generation_authorization"] = scene.get("_generation_authorization")
                scene["_generation_authorization"] = {"extra_image_attempts": 0, "image_attempts_reserved": 1}
                concepts.append(replace(concept, scene_plan=scene))
            candidate_records = []
            for candidate in original.candidates:
                identifier = new_id("candidate", candidate.candidate_id)
                id_map["candidates"][candidate.candidate_id] = identifier
                payload = rewrite_assets(candidate.to_dict())
                payload["candidate_id"] = identifier
                if payload["status"] == "selected":
                    payload["status"] = "ready"
                if payload["status"] == "pending":
                    payload.update(status="failed", error="Imported pending attempt has no target generation authorization")
                candidates.append(CoverCandidate.from_dict(payload))
                candidate_records.append({"source_candidate_id": candidate.candidate_id, "candidate_id": identifier,
                    "concept_id": candidate.concept_id, "sha256": candidate.sha256, "media_id": payload["media_id"],
                    "relative_path": payload["relative_path"]})
            status = "ready" if all(item.status == "ready" for item in candidates) else "partial"
            if not all(matches):
                status = "stale"
            projected.append(replace(original, cover_set_id=new_set_id, project_id=project_id,
                concepts=tuple(concepts), candidates=tuple(candidates), selected_candidate_id="", status=status, revision=1))
            imported_sets.append({"source_set_id": original.cover_set_id, "cover_set_id": new_set_id,
                "source_selected_candidate_id": original.selected_candidate_id,
                "source_match": "matched" if all(matches) else "historical", "candidates": candidate_records})
        if _inventory(source_store.root) != source_files:
            raise CoverConflict("Workshop covers changed during transfer; retry with a stable history")
        if _target_bundle(target_context, project_id) != (target_bundle, target_error):
            raise CoverConflict("Project skeleton changed during cover transfer")
        # Publish queryable projections only after all source bytes and images
        # have been validated. A missing final manifest keeps them unselectable.
        _safe(target_store.root, "sets")
        for cover_set in projected:
            path = _safe(target_store.root, f"sets/{cover_set.cover_set_id}.json")
            if path.exists():
                if target_store.load(cover_set.cover_set_id).to_dict() != cover_set.to_dict():
                    raise CoverConflict("Existing imported cover set conflicts with its provenance")
            else:
                target_store.create(cover_set)
        manifest = {**key_input, "transfer_key": key, "imported_sets": imported_sets,
                    "id_map": id_map, "assets": assets, "target_bundle_sha256": target_bundle,
                    "target_source_error": target_error, "new_image_budget": 0, "active_selection_inherited": False,
                    "created_at": datetime.now(timezone.utc).isoformat()}
        manifest["manifest_sha256"] = canonical_hash(manifest)
        _write_new(_safe(folder, "manifest.json"), _json_bytes(manifest))
        return manifest


def validate_import_selection(target_context, target_store, cover_set, candidate) -> None:
    """Allow explicit selection only for intact, approved, currently matching imports."""
    workspace, kind, project_id = _scope(target_context.owner_scope)
    if kind != "project" or cover_set.project_id != project_id or target_context.cover_root.resolve() != target_store.root.resolve():
        raise CoverConflict("Imported cover belongs to another project")
    concept = next((item for item in cover_set.concepts if item.concept_id == candidate.concept_id), None)
    metadata = concept.scene_plan.get("_cover_import") if concept else None
    if not isinstance(metadata, Mapping) or not _KEY.fullmatch(str(metadata.get("transfer_key", ""))):
        raise CoverConflict("Candidate has no valid cover import record")
    folder = _safe(target_store.root, "imports/" + metadata["transfer_key"])
    manifest = _verify_manifest(folder)
    if manifest["target_owner"] != target_context.owner_scope or _scope(manifest["source_owner"])[0] != workspace:
        raise CoverConflict("Imported cover crosses its workspace scope")
    imported = next((item for item in manifest["imported_sets"] if item["cover_set_id"] == cover_set.cover_set_id), None)
    record = next((item for item in imported["candidates"] if item["candidate_id"] == candidate.candidate_id), None) if imported else None
    if not record or any(record[key] != getattr(candidate, key) for key in ("concept_id", "sha256", "media_id", "relative_path")):
        raise CoverConflict("Imported candidate differs from its immutable transfer record")
    provenance = _safe(folder, "provenance")
    original_store = CoverStore(target_store.project_path, cover_root=provenance)
    original_set = original_store.load(imported["source_set_id"])
    original_concept = next(item for item in original_set.concepts if item.concept_id == record["concept_id"])
    binding = original_concept.scene_plan.get("_source_binding") or {}
    if not binding or binding.get("owner_scope") != manifest["source_owner"] or not _approval_verified(original_store, binding):
        raise CoverConflict("Imported historical cover lacks valid source approval")
    context = CoverSourceContext(provenance, manifest["source_owner"])
    snapshot = load_snapshot(context, binding["snapshot_id"])
    current_bundle, _ = _target_bundle(target_context, project_id)
    if not current_bundle or current_bundle != snapshot.source_bundle_sha256:
        raise CoverConflict("Imported historical cover does not match the project's current skeleton and dependencies")
    if candidate.status not in {"ready", "selected", "rejected"}:
        raise CoverConflict("Only an existing completed imported image can be selected")
