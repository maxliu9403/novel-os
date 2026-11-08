"""Recover author art requests from the poster studio's existing durable tasks.

No second mutable preference store: submitted intents, including interrupted
ones, remain the source of truth. New requests are frozen under the task lock.
"""
from __future__ import annotations

import json

from core.author_requirements import build_requirements, validate_requirements


def record_direction_edit(plan, directions):
    """A direct author edit supersedes visual defaults without a model call."""
    previous = {item["id"]: item for item in plan["directions"]}
    changes = []
    for direction in directions:
        old = previous.get(direction["id"], {})
        changed = {key: value for key, value in direction.items()
                   if key != "id" and old.get(key) != value}
        if changed:
            changes.append({"direction_id": direction["id"], "changes": changed})
    if not changes:
        return {"author_direction_edit": None}
    text = json.dumps({"author_direction_edits": changes,
        "scope": "Apply to the named direction. When replanning, retain this author intent "
                 "where applicable; do not impose a candidate-specific variation on every direction."},
        ensure_ascii=False, separators=(",", ":"))
    snapshot = plan.get("author_requirements")
    entries = (validate_requirements(snapshot, scope="cover_design",
               source_sha256=plan["source_content_sha256"])["entries"] if snapshot else [])
    entry = {"id": f"direction-edit-{plan['plan_id']}", "text": text, "source": "author_direction_edit"}
    return {"author_direction_edit": text,
            "author_requirements": build_requirements("cover_design", [*entries, entry],
                source_sha256=plan["source_content_sha256"])}


def preference_context(records, *, saved_plans=(), current=None, request_id="", source_sha256=""):
    chronological = sorted((row for row in records if row.get("kind") == "plan"),
                           key=lambda row: (row["created_at"], row["task_id"]))
    preferences, entries = {}, []
    if not chronological:
        # Plans created before durable intents existed are still useful input.
        chronological = [{"request_id": plan["plan_id"], "created_at": plan["created_at"],
                          "input": {"preferences": plan.get("preferences", {})}}
                         for plan in sorted(saved_plans, key=lambda plan: (plan["created_at"], plan["plan_id"]))]
    chronological.extend({"request_id": plan["plan_id"], "created_at": plan["created_at"],
                          "author_direction_edit": plan["author_direction_edit"]}
                         for plan in saved_plans if plan.get("author_direction_edit"))
    chronological.sort(key=lambda row: (row["created_at"], row.get("request_id") or row["task_id"]))
    if current is not None:
        chronological.append({"request_id": request_id, "input": {"preferences": current}})
    for index, row in enumerate(chronological):
        if row.get("author_direction_edit"):
            entries.append({"id": f"cover-request-{index + 1}", "source": row["request_id"],
                            "text": row["author_direction_edit"]})
            continue
        supplied = (row.get("input") or {}).get("preferences") or {}
        if not isinstance(supplied, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in supplied.items()):
            raise ValueError("已保存的封面创作要求格式无效，请检查任务记录。")
        # Absent fields preserve earlier values; an explicitly empty field
        # clears that form field. Free-text history is never heuristically cut.
        changes = {key: value for key, value in supplied.items() if preferences.get(key) != value}
        preferences.update(supplied)
        if changes:
            entries.append({"id": f"cover-request-{index + 1}",
                            "source": str(row.get("request_id") or row.get("task_id") or index),
                            "text": json.dumps(changes, ensure_ascii=False, separators=(",", ":"))})
    return {"preferences": preferences,
            "author_requirements": build_requirements("cover_design", entries, source_sha256=source_sha256)}
