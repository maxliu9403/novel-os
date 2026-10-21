"""Bounded field-replacement transport for an existing cover portfolio.

This module grants no creative or source approval. Its result must pass the
normal full schema, canon, compiler and independent whole-portfolio review.
The input is a raw model payload or a normalized repair copy of a prior
direction. The caller owns that exact base; persisted approvals are never edited
or carried forward by applying a patch.
"""
from __future__ import annotations

from dataclasses import fields
import hashlib
import json
from typing import Any, Mapping

try:
    from .cover_models_v2 import CoverScenePlan
except ImportError:  # pragma: no cover - legacy CLI imports
    from cover_models_v2 import CoverScenePlan


PATCH_SCHEMA = "cover-direction-patch.v1"
_PLAN_FIELDS = frozenset(field.name for field in fields(CoverScenePlan) if field.name != "concept_id")
_SHARED_FIELDS = frozenset({"visual_identity", "core_conflict_visual_contract", "visual_assumptions", "director_notes"})


class DirectionPatchError(ValueError):
    """A patch cannot be applied safely to its declared complete base."""


def _serialized(value: Any) -> str:
    def check_keys(item: Any) -> None:
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise DirectionPatchError("Direction patch JSON object keys must be strings")
            for child in item.values():
                check_keys(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check_keys(child)
    try:
        check_keys(value)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, RecursionError) as exc:
        raise DirectionPatchError("Direction patch requires finite UTF-8 JSON data with string object keys") from exc


def _base(base: Mapping[str, Any], count: int) -> tuple[dict[str, Any], str]:
    if type(count) is not int or not 3 <= count <= 5 or not isinstance(base, Mapping):
        raise DirectionPatchError("Direction patch requires a complete 3–5-plan base")
    encoded = _serialized(base)
    result = json.loads(encoded)
    plans = result.get("plans")
    if not isinstance(plans, list) or len(plans) != count:
        raise DirectionPatchError("Direction patch cannot change the original plan count")
    ids = [plan.get("concept_id") if isinstance(plan, dict) else None for plan in plans]
    if (any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in ids)
            or len(set(ids)) != count):
        raise DirectionPatchError("Direction patch base requires unique nonempty concept IDs")
    return result, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def make_patch_contract(base: Mapping[str, Any], count: int) -> dict[str, Any]:
    """Describe a patch bound to the exact previous raw response's contents."""
    payload, checksum = _base(base, count)
    return {
        "schema": PATCH_SCHEMA, "base_sha256": checksum,
        "plan_updates": [{"concept_id": "one exact allowed_concept_ids value",
                          "changes": {"allowed_plan_field": "complete replacement value"}}],
        "shared_updates": {"allowed_shared_field": "complete replacement value, only when needed"},
        "allowed_concept_ids": [plan["concept_id"] for plan in payload["plans"]],
        "allowed_plan_fields": sorted(_PLAN_FIELDS), "allowed_shared_fields": sorted(_SHARED_FIELDS),
        "rules": [
            "Return only schema, base_sha256, plan_updates and shared_updates; copy the exact schema and base digest.",
            "Return changed top-level plan fields only. Each concept_id occurs at most once; never add, remove, rename or reorder plans.",
            "Array and object values replace the whole field. Supply the complete campaign or visual_hook when changing it; never send a recursive merge, path operation or delete instruction.",
            "Retain every untouched fact and creative decision. Update dependent fields explicitly when needed; no source facts, evidence IDs, approvals or review receipts may be invented.",
            "Only the listed shared fields may change. Do not return source_binding, compiled_recipes, evidence_ledger, profile_version, model, status, hashes or approval metadata.",
            "At least one value must actually change. The application reconstructs and validates the complete portfolio, including a fresh independent evidence review; a patch is not acceptance.",
        ],
    }


def apply_direction_patch(base: Mapping[str, Any], response: Mapping[str, Any], count: int) -> dict[str, Any]:
    """Apply explicit field replacements atomically, preserving IDs and order."""
    result, checksum = _base(base, count)
    if not isinstance(response, Mapping) or set(response) != {"schema", "base_sha256", "plan_updates", "shared_updates"}:
        raise DirectionPatchError("Direction patch must contain exactly schema, base_sha256, plan_updates and shared_updates")
    if response["schema"] != PATCH_SCHEMA or response["base_sha256"] != checksum:
        raise DirectionPatchError("Direction patch schema or base_sha256 does not match its original payload")
    updates, shared = response["plan_updates"], response["shared_updates"]
    if not isinstance(updates, list) or not isinstance(shared, Mapping) or set(shared) - _SHARED_FIELDS:
        raise DirectionPatchError("Direction patch has invalid plan_updates or forbidden shared fields")
    plans = {plan["concept_id"]: plan for plan in result["plans"]}
    seen = set()
    for update in updates:
        if not isinstance(update, Mapping) or set(update) != {"concept_id", "changes"}:
            raise DirectionPatchError("Each plan update requires exactly concept_id and changes")
        identifier, changes = update["concept_id"], update["changes"]
        if not isinstance(identifier, str) or identifier not in plans or identifier in seen:
            raise DirectionPatchError("Direction patch concept_id is unknown or duplicated")
        if not isinstance(changes, Mapping) or not changes or set(changes) - _PLAN_FIELDS:
            raise DirectionPatchError("Direction patch contains empty changes or forbidden plan fields")
        seen.add(identifier)
        plans[identifier].update(json.loads(_serialized(changes)))
    result.update(json.loads(_serialized(shared)))
    if _serialized(result) == _serialized(base):
        raise DirectionPatchError("Direction patch is a no-op; at least one value must change")
    return result
