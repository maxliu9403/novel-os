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
    targets = {target}
    if lineage.get("operation") == "poster_retry":
        parent_ids = lineage.get("parent_candidate_ids")
        concept_ids = lineage.get("target_concept_ids")
        if (not isinstance(parent_ids, list) or not parent_ids or len(set(parent_ids)) != len(parent_ids)
                or parent_ids[0] != lineage["parent_candidate_id"]
                or concept_ids != [candidates.get(key, {}).get("concept_id") for key in parent_ids]
                or any(candidates.get(key, {}).get("status") not in {"failed", "pending"} for key in parent_ids)):
            raise CoverConflict("Poster retry targets do not match failed parent slots")
        targets = set(concept_ids)
    if [item.concept_id for item in cover_set.candidates] != [item["concept_id"] for item in parent["candidates"]]:
        raise CoverConflict("Cover repair changed portfolio slot identities or order")
    for item in cover_set.candidates:
        if item.concept_id in targets:
            if lineage.get("operation") == "image_repair":
                concept = next(x for x in cover_set.concepts if x.concept_id == target)
                repair = (concept.scene_plan.get("_source_binding") or {}).get("single_candidate_repair") or {}
                if (repair.get("parent_sha256") != checksum
                        or repair.get("cover_set_id") != parent["cover_set_id"]
                        or repair.get("candidate_id") != lineage["parent_candidate_id"]
                        or repair.get("concept_id") != target):
                    raise CoverConflict("Repaired candidate does not match its approved parent binding")
            elif lineage.get("operation") == "poster_retry":
                concept = next(x for x in cover_set.concepts if x.concept_id == item.concept_id)
                if concept.to_dict() != concepts[item.concept_id]:
                    raise CoverConflict("Poster retry changed the original direction")
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


def resolve_candidate_origin(store, cover_set, candidate_id):
    """Resolve retained siblings to the frozen owner of their typography receipt.

    Old amendments bind their original set id. Cloning a different slot never
    grants permission to rewrite that binding or discard its integrity check.
    """
    try:
        from .cover_models import CoverSet
    except ImportError:  # Legacy CLI import.
        from cover_models import CoverSet
    seen = set()
    current = cover_set
    while True:
        validate_lineage(store, current, candidate_id=candidate_id)
        candidate = next((item for item in current.candidates if item.candidate_id == candidate_id), None)
        if candidate is None:
            raise CoverConflict("Retained cover candidate is absent from its source")
        concept = next(item for item in current.concepts if item.concept_id == candidate.concept_id)
        lineage = current.repair_lineage
        targets = (set(lineage.get("target_concept_ids", ())) if lineage and lineage.get("operation") == "poster_retry"
                   else {lineage.get("target_concept_id")} if lineage else set())
        if not lineage or candidate.concept_id in targets:
            return current, candidate, concept
        checksum = lineage["parent_snapshot_sha256"]
        if checksum in seen or len(seen) >= 100:
            raise CoverConflict("Retained cover lineage contains a cycle")
        seen.add(checksum)
        path = store.root / "repair_sources" / f"{checksum}.json"
        current = CoverSet.from_dict(json.loads(path.read_text("utf-8")))
