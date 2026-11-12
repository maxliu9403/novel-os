"""Read-only route execution projections for both details and library listings.

The library needs task metadata, not story artifacts. Reading this view never
loads source text, route payloads or paid responses, and never migrates a run.
"""

from copy import deepcopy
from datetime import UTC, datetime
import json
import re
from typing import get_args

from core.creation_mode import SourceMode
from core.source_design_store import SourceDesignStore

_ACTIVE = {"prepared", "queued", "running"}
_READY = {"ready", "complete", "completed"}
_PAUSED = {"paused", "cancelled", "interrupted"}
_ATTENTION = {"failed", "error", "pending_unknown", "needs_author", "needs_capacity", "conflict"}
_OPERATIONS = {"initial_routes", "revise_routes", "expand_routes", "revisit_source"}


def job_path(library, identifier, run_id):
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("路线运行标识无效。")
    return SourceDesignStore(library.path(identifier)).path("api-jobs", f"{run_id}.json")


def route_job(library, identifier, run_id, runner):
    path = job_path(library, identifier, run_id)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text("utf-8"))
        if record["run_id"] != run_id or not isinstance(record["job_id"], str):
            raise ValueError()
        return runner.get(record["job_id"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("路线任务关联记录损坏；原设计结果保持不变。") from None


def public_route_state(library, identifier, value, runner):
    """Worker loss is a projection, never a GET-triggered state rewrite."""
    result = deepcopy(value)
    job = route_job(library, identifier, value["run_id"], runner)
    active = bool(job and job.get("status") == "running")
    result.update(
        job_id=job.get("job_id") if job else None,
        worker_status=job.get("status") if job else "absent",
        worker_active=active,
        cancel_requested=bool(job and job.get("cancel_requested")),
        interrupted=not active and value.get("status") in _ACTIVE,
        checked_at=datetime.now(UTC).isoformat(),
    )
    if job and job.get("status") == "error" and value.get("status") in _ACTIVE:
        result["error"] = result.get("error") or "路线工作线程已停止；已保存资料和进度，可检查连接后继续。"
    return result


def route_is_active(row):
    return bool(row.get("worker_active") and row.get("status") in _ACTIVE)


def ordered_route_states(rows):
    """The library and route workspace select the same actual unfinished work."""
    return sorted(rows, key=lambda row: (
        route_is_active(row),
        str(row.get("updated_at") or row.get("created_at") or ""),
        str(row.get("created_at") or ""), row["run_id"],
    ), reverse=True)


def _progress(value, key):
    number = value.get(key, 0)
    if type(number) is not int or number < 0:
        raise ValueError("Invalid saved progress")
    return number


def design_summary(library, identifier, runner):
    """One small summary per book, with live work taking precedence over history."""
    result = {"status": "not_started", "active_count": 0, "total_runs": 0,
              "completed": 0, "total": 0}
    rows = []
    unreadable = False
    try:
        store = SourceDesignStore(library.path(identifier))
        paths = list(store.path("runs").glob("*/state.json"))
    except (OSError, ValueError):
        return {**result, "status": "unknown"}
    result["total_runs"] = len(paths)
    for path in paths:
        try:
            value = store.state(path.parent.name)
            if (not isinstance(value, dict) or value.get("run_id") != path.parent.name
                    or value.get("source_id") != identifier
                    or not isinstance(value.get("status"), str)):
                raise ValueError("Run identity does not match its source")
            row = public_route_state(library, identifier, value, runner)
            _progress(row, "completed")
            _progress(row, "total")
            rows.append(row)
        except (OSError, ValueError, TypeError, KeyError):
            unreadable = True
    if not rows:
        return {**result, "status": "unknown" if unreadable else "not_started"}
    active = [row for row in rows if route_is_active(row)]
    result["active_count"] = len(active)
    selected = ordered_route_states(rows)[0]
    status = selected.get("status")
    if active:
        projected = "running"
    elif unreadable:
        projected = "unknown"
    elif status in _READY:
        projected = "ready"
    elif status in _PAUSED or status in _ACTIVE:
        projected = "paused"
    elif status in _ATTENTION:
        projected = "needs_attention"
    else:
        projected = "unknown"
    result.update(status=projected, latest_run_id=selected["run_id"],
                  completed=selected.get("completed", 0), total=selected.get("total", 0))
    request = selected.get("request")
    if isinstance(request, dict):
        if request.get("mode") in get_args(SourceMode):
            result["mode"] = request["mode"]
        if isinstance(request.get("operation"), str) and request["operation"] in _OPERATIONS:
            result["operation"] = request["operation"]
    updated = selected.get("updated_at") or selected.get("created_at")
    if isinstance(updated, str):
        result["updated_at"] = updated
    return result
