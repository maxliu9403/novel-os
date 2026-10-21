"""Versioned poster plans, independent of legacy analysis and direction approvals."""
from __future__ import annotations

from copy import deepcopy
import json
import re
import uuid

from .cover_sources import canonical_hash, check_source_head, load_snapshot
from .cover_store import CoverConflict


def _path(flow, plan_id):
    if not re.fullmatch(r"poster-plan-[0-9a-f]{32}", plan_id):
        raise ValueError("Invalid poster plan id")
    path = flow.store.root / "poster" / "plans" / f"{plan_id}.json"
    if any(p.is_symlink() for p in (flow.store.root, path.parent.parent, path.parent, path)):
        raise ValueError("Invalid poster plan path")
    return path


def _hash(payload):
    return canonical_hash({key: value for key, value in payload.items()
                           if key not in {"plan_hash", "status", "approved_at", "stale"}})


def save_plan(flow, payload):
    from datetime import datetime, timezone
    plan = deepcopy(payload)
    plan.update(plan_id=f"poster-plan-{uuid.uuid4().hex}", owner_scope=flow.context.owner_scope,
                created_at=datetime.now(timezone.utc).isoformat(), status="draft")
    plan["plan_hash"] = _hash(plan)
    path = _path(flow, plan["plan_id"])
    flow.store._write_json(path, plan)
    return plan


def load_plan(flow, plan_id):
    plan = json.loads(_path(flow, plan_id).read_text("utf-8"))
    if (plan.get("plan_id") != plan_id or plan.get("owner_scope") != flow.context.owner_scope
            or plan.get("plan_hash") != _hash(plan) or plan.get("pipeline_kind") != "skeleton_poster"):
        raise CoverConflict("Poster plan provenance failed verification")
    return plan


def list_plans(flow):
    root = _path(flow, "poster-plan-" + "0" * 32).parent
    return sorted([load_plan(flow, p.stem) for p in root.glob("poster-plan-*.json")],
                  key=lambda row: (row["created_at"], row["plan_id"]), reverse=True)


def validate_fresh(flow, plan):
    snapshot = load_snapshot(flow.context, plan["snapshot_id"])
    if (snapshot.source_bundle_sha256 != plan["source_bundle_sha256"]
            or snapshot.source_content_sha256 != plan["source_content_sha256"]):
        raise CoverConflict("Poster plan source hash mismatch")
    check_source_head(flow.context, snapshot)


def approve(flow, plan_id, plan_hash):
    from datetime import datetime, timezone
    plan = load_plan(flow, plan_id)
    if plan["plan_hash"] != plan_hash:
        raise CoverConflict("设计方案已变化，请重新查看后生成。")
    validate_fresh(flow, plan)
    if plan["status"] != "approved":
        plan.update(status="approved", approved_at=datetime.now(timezone.utc).isoformat())
        flow.store._write_json(_path(flow, plan_id), plan)
    return plan
