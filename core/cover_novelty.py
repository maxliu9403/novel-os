"""Structural fingerprints for intra-portfolio and cross-book cover novelty."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_FIELDS: tuple[tuple[str, float], ...] = (
    ("focal_strategy", 0.16),
    ("cast_scale", 0.08),
    ("composition_family", 0.15),
    ("scene_family", 0.13),
    ("art_style", 0.16),
    ("emotion_register", 0.08),
    ("typography_style", 0.11),
    ("location_family", 0.05),
    ("primary_prop", 0.04),
    ("visual_signature", 0.04),
)


def _value(source: Any, name: str, default: Any = "") -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _tokens(value: Any) -> set[str]:
    return {
        token for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", _normalize(value))
        if len(token) > 1
    }


def _cast_scale(plan: Any) -> str:
    cast = _value(plan, "cast", ()) or ()
    count = len(cast) if isinstance(cast, (list, tuple, set)) else 0
    if count == 0:
        return "no-character"
    if count == 1:
        return "single-character"
    if count == 2:
        return "two-character"
    return "ensemble"


def plan_fingerprint(
    plan: Any,
    *,
    project_id: str = "",
    direction_id: str = "",
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "direction_id": direction_id,
        "concept_id": _normalize(_value(plan, "concept_id")),
        "focal_strategy": _normalize(
            _value(plan, "focal_strategy") or _value(plan, "visual_strategy")
        ),
        "cast_scale": _cast_scale(plan),
        "composition_family": _normalize(_value(plan, "composition_family")),
        "scene_family": _normalize(_value(plan, "scene_family")),
        "art_style": _normalize(_value(plan, "art_style")),
        "emotion_register": _normalize(_value(plan, "emotion_register")),
        "typography_style": _normalize(_value(plan, "typography_style")),
        "location_family": _normalize(_value(plan, "location_family")),
        "primary_prop": _normalize(_value(plan, "primary_prop")),
        "visual_signature": _normalize(
            _value(plan, "visual_signature") or _value(plan, "frozen_action")
        ),
    }


def fingerprint_similarity(first: Mapping[str, Any], second: Mapping[str, Any]) -> float:
    weighted = 0.0
    available = 0.0
    for field, weight in _FIELDS:
        left = _normalize(first.get(field))
        right = _normalize(second.get(field))
        if not left or not right:
            continue
        available += weight
        if left == right:
            score = 1.0
        else:
            left_tokens = _tokens(left)
            right_tokens = _tokens(right)
            union = left_tokens | right_tokens
            score = len(left_tokens & right_tokens) / len(union) if union else 0.0
        weighted += weight * score
    return weighted / available if available else 0.0


def portfolio_collisions(
    plans: Sequence[Any],
    *,
    threshold: float = 0.78,
) -> tuple[dict[str, Any], ...]:
    fingerprints = [plan_fingerprint(plan) for plan in plans]
    collisions: list[dict[str, Any]] = []
    for left_index, left in enumerate(fingerprints):
        for right_index in range(left_index + 1, len(fingerprints)):
            right = fingerprints[right_index]
            similarity = fingerprint_similarity(left, right)
            if similarity >= threshold:
                collisions.append({
                    "first_concept_id": left["concept_id"],
                    "second_concept_id": right["concept_id"],
                    "similarity": round(similarity, 3),
                })
    return tuple(collisions)


def historical_collisions(
    plans: Sequence[Any],
    recent_fingerprints: Iterable[Mapping[str, Any]],
    *,
    threshold: float = 0.90,
) -> tuple[dict[str, Any], ...]:
    history = tuple(recent_fingerprints)
    collisions: list[dict[str, Any]] = []
    for plan in plans:
        current = plan_fingerprint(plan)
        if not history:
            continue
        closest = max(history, key=lambda item: fingerprint_similarity(current, item))
        similarity = fingerprint_similarity(current, closest)
        if similarity >= threshold:
            collisions.append({
                "concept_id": current["concept_id"],
                "similarity": round(similarity, 3),
                "project_id": str(closest.get("project_id") or ""),
                "direction_id": str(closest.get("direction_id") or ""),
                "matched_concept_id": str(closest.get("concept_id") or ""),
            })
    return tuple(collisions)


def recent_direction_fingerprints(
    projects_root: str | Path,
    *,
    exclude_project_id: str = "",
    limit: int = 80,
) -> tuple[dict[str, Any], ...]:
    """Read recent persisted direction fingerprints without loading image bytes."""
    root = Path(projects_root)
    paths = sorted(
        root.glob("*/outputs/covers/directions/direction-*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    result: list[dict[str, Any]] = []
    for path in paths:
        if len(result) >= limit:
            break
        project_id = path.parents[3].name
        if project_id == exclude_project_id or path.name.endswith(".brief.json"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        direction_id = str(payload.get("direction_id") or path.stem)
        plans = payload.get("plans") or ()
        if not isinstance(plans, (list, tuple)):
            continue
        result.extend(
            plan_fingerprint(plan, project_id=project_id, direction_id=direction_id)
            for plan in plans if isinstance(plan, Mapping)
        )
    return tuple(result[:limit])


__all__ = [
    "fingerprint_similarity",
    "historical_collisions",
    "plan_fingerprint",
    "portfolio_collisions",
    "recent_direction_fingerprints",
]
