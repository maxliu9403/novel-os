"""Exact parent provenance for mixed-origin single-candidate repair portfolios."""
from __future__ import annotations

import json
import re
import hashlib

try:
    from .cover_store import CoverConflict
except ImportError:  # Legacy top-level CLI import.
    from cover_store import CoverConflict


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()

_FIELDS = ("concept_id", "generation_prompt", "sha256", "relative_path", "media_id", "model", "provider",
           "request_id", "content_type", "width", "height", "safe_request_parameters")


def record_parent(store, parent, *, candidate_id, operation):
    snapshot = parent.to_dict()
    checksum = digest(snapshot)
    directory = store.root / "repair_sources"
    path = directory / f"{checksum}.json"
    if directory.is_symlink() or path.is_symlink():
        raise CoverConflict("Invalid cover repair source path")
    if path.exists():
        if digest(json.loads(path.read_text("utf-8"))) != checksum:
            raise CoverConflict("Cover repair parent snapshot changed")
    else:
        store._write_json(path, snapshot)
    target = next(x for x in parent.candidates if x.candidate_id == candidate_id)
    return {"schema_version": 1, "parent_cover_set_id": parent.cover_set_id,
            "parent_snapshot_sha256": checksum, "parent_candidate_id": candidate_id,
            "target_concept_id": target.concept_id, "operation": operation}


def validate_lineage(store, cover_set, *, candidate_id=None, for_generation=False):
    lineage = cover_set.repair_lineage
    if not lineage:
        return
    checksum = lineage.get("parent_snapshot_sha256", "")
    if lineage.get("schema_version") != 1 or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise CoverConflict("Invalid cover repair lineage")
    directory = store.root / "repair_sources"
    path = directory / f"{checksum}.json"
    if directory.is_symlink() or path.is_symlink() or not path.is_file():
        raise CoverConflict("Cover repair parent snapshot is unavailable")
    parent = json.loads(path.read_text("utf-8"))
    if (digest(parent) != checksum or parent["cover_set_id"] != lineage["parent_cover_set_id"]
            or parent["project_id"] != cover_set.project_id):
        raise CoverConflict("Cover repair parent provenance failed verification")
    candidates = {x["candidate_id"]: x for x in parent["candidates"]}
    concepts = {x["concept_id"]: x for x in parent["concepts"]}
    target = lineage["target_concept_id"]
    if candidates.get(lineage["parent_candidate_id"], {}).get("concept_id") != target:
        raise CoverConflict("Cover repair target does not match its parent")
    if [item.concept_id for item in cover_set.candidates] != [item["concept_id"] for item in parent["candidates"]]:
        raise CoverConflict("Cover repair changed portfolio slot identities or order")
    for item in cover_set.candidates:
        if item.concept_id == target:
            if lineage.get("operation") == "image_repair":
                concept = next(x for x in cover_set.concepts if x.concept_id == target)
                repair = (concept.scene_plan.get("_source_binding") or {}).get("single_candidate_repair") or {}
                if (repair.get("parent_sha256") != checksum
                        or repair.get("cover_set_id") != parent["cover_set_id"]
                        or repair.get("candidate_id") != lineage["parent_candidate_id"]
                        or repair.get("concept_id") != target):
                    raise CoverConflict("Repaired candidate does not match its approved parent binding")
            elif lineage.get("operation") not in {"evaluation", "typography"}:
                raise CoverConflict("Unknown cover repair lineage operation")
            continue
        original = candidates.get(item.candidate_id)
        concept = next(x for x in cover_set.concepts if x.concept_id == item.concept_id)
        if (original is None or any(original.get(key) != item.to_dict().get(key) for key in _FIELDS)
                or concepts.get(item.concept_id) != concept.to_dict()):
            raise CoverConflict("Retained repair candidate changed its original image or recipe provenance")
        if for_generation and item.candidate_id == candidate_id:
            raise CoverConflict("Retained repair candidates do not receive new image allowance")
