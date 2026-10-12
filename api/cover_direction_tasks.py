"""Durable owner-scoped direction jobs, independent of browser request lifetime.

JobRunner owns execution and project-deletion fences. These small records own
reconnection and terminal outcomes; losing a worker never starts another paid
model call automatically. Model calls always run outside the storage lock.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
import threading
import uuid
import weakref

from fastapi import HTTPException


_PROCESS_ID = uuid.uuid4().hex
_TASK_ID = re.compile(r"^direction-task-[0-9a-f]{32}$")
_ACTIVE = {"queued", "running"}
_LOCKS = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()
_PROGRESS_SINK = ContextVar("cover_direction_progress_sink", default=None)
_PHASES = frozenset({"draft_started", "draft_response", "draft_validation", "semantic_started",
                     "semantic_response", "semantic_validation", "completed", "failure"})


def current_progress_sink():
    """Only the executing task can obtain its exact owner/task-bound callback."""
    return _PROGRESS_SINK.get()


@contextmanager
def _task_lock(workflow):
    """Serialize status projections with dispatch/checkpoints, without mkdir."""
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(workflow.store.root.resolve()), threading.RLock())
    with lock:
        yield


def _now():
    return datetime.now(timezone.utc).isoformat()


def _directory(workflow):
    root = workflow.store.root / "direction_tasks"
    if root.is_symlink():
        raise ValueError("Invalid cover direction task directory")
    return root


def _read(workflow, task_id):
    if not _TASK_ID.fullmatch(task_id):
        raise ValueError("Invalid cover direction task id")
    path = _directory(workflow) / f"{task_id}.json"
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("Cover direction task does not exist")
    payload = json.loads(path.read_text("utf-8"))
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or payload.get("status") not in _ACTIVE | {"completed", "failed", "interrupted"}
            or not isinstance(payload.get("created_at"), str)):
        raise ValueError("Invalid cover direction task record")
    if payload.get("task_id") != task_id or payload.get("owner_scope") != workflow.context.owner_scope:
        raise FileNotFoundError("Cover direction task does not belong to this owner")
    return payload


def _write(workflow, payload):
    path = _directory(workflow) / f"{payload['task_id']}.json"
    if path.is_symlink():
        raise ValueError("Invalid cover direction task path")
    workflow.store._write_json(path, payload)


def _records(workflow):
    result = []
    for path in _directory(workflow).glob("direction-task-*.json"):
        try:
            result.append(_read(workflow, path.stem))
        except (ValueError, OSError):
            continue
    return sorted(result, key=lambda value: (value["created_at"], value["task_id"]), reverse=True)


def _project(payload, runner):
    result = dict(payload)
    if result["status"] in _ACTIVE:
        job = runner.get(result.get("job_id", "")) if result.get("process_id") == _PROCESS_ID else None
        if not (job and job.get("status") == "running"
                and (job.get("direction_task_id") or (job.get("meta") or {}).get("direction_task_id")) == result["task_id"]):
            result.update(status="interrupted", error="服务已重启或任务执行已中断，请检查已保存方向后手动重试。")
    return {key: value for key, value in result.items()
            if key not in {"owner_scope", "process_id", "job_id", "diagnostic_attempt_id"}}


def latest(workflow, runner):
    # Records are replaced atomically. GET must not acquire the directory-
    # creating mutation lock: a read racing project deletion must not recreate
    # the deleted project's folders.
    with _task_lock(workflow):
        records = _records(workflow)
        return _project(records[0], runner) if records else None


def get(workflow, task_id, runner):
    with _task_lock(workflow):
        return _project(_read(workflow, task_id), runner)


def _error_message(exc):
    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("detail") or json.dumps(detail, ensure_ascii=False)
    return str(detail) or type(exc).__name__


def start(workflow, runner, body, operation, *, preflight, project_id="", session_id=""):
    with _task_lock(workflow), workflow.store.mutation_lock():
        for previous in _records(workflow):
            if previous["status"] in _ACTIVE:
                projected = _project(previous, runner)
                if projected["status"] in _ACTIVE:
                    # One owner cannot spend twice by double-clicking or opening
                    # another tab, even when that tab has different UI settings.
                    return projected
                previous.update(status="interrupted", error=projected["error"], updated_at=_now(), finished_at=_now())
                _write(workflow, previous)
        preflight()
        now = _now()
        payload = {"task_id": f"direction-task-{uuid.uuid4().hex}", "schema_version": 1,
                   "owner_scope": workflow.context.owner_scope, "process_id": _PROCESS_ID,
                   "job_id": "", "status": "queued", "analysis_id": body.analysis_id,
                   "count": body.count, "freshness_policy": body.freshness_policy,
                   "direction_id": None, "error": None, "created_at": now,
                   "updated_at": now, "finished_at": None}
        _write(workflow, payload)

        received_responses = set()

        def progress(event):
            # This closure is tied to the record allocated above, never a lookup
            # of the latest task or analysis shared with another request.
            phase, attempt = event.get("phase"), event.get("attempt")
            response_received = phase in {"draft_response", "semantic_response"}
            diagnostic_id = event.get("diagnostic_attempt_id")
            if (phase not in _PHASES or type(attempt) is not int or attempt < 1
                    or not isinstance(diagnostic_id, str) or not re.fullmatch(r"[0-9a-f]{32}", diagnostic_id)):
                raise ValueError("Invalid cover direction progress event")
            with _task_lock(workflow):
                try:
                    current = _read(workflow, payload["task_id"])
                except FileNotFoundError:
                    return  # Do not recreate a deleted owner for a late event.
                if current["status"] != "running" or current.get("process_id") != _PROCESS_ID:
                    return  # Late callbacks never reopen completed/interrupted work.
                if current.get("diagnostic_attempt_id", diagnostic_id) != diagnostic_id:
                    raise ValueError("Cover progress belongs to a different planning request")
                previous = current.get("progress", {})
                if attempt < previous.get("attempt", 0):
                    return
                if phase == "failure" and previous:
                    # The terminal task error describes failure. Retain the
                    # actual work phase where it happened for later recovery.
                    phase = previous["phase"]
                now = _now()
                if response_received:
                    received_responses.add((attempt, phase))
                same_phase = phase == previous.get("phase") and attempt == previous.get("attempt")
                current.update(diagnostic_attempt_id=diagnostic_id, updated_at=now, progress={
                    "phase": phase, "attempt": attempt,
                    "phase_started_at": previous["phase_started_at"] if same_phase else now,
                    "last_event_at": now, "completed_model_calls": len(received_responses),
                })
                with workflow.store.mutation_lock():
                    _write(workflow, current)

        def execute():
            with _task_lock(workflow), workflow.store.mutation_lock():
                running = _read(workflow, payload["task_id"])
                running.update(status="running", updated_at=_now())
                _write(workflow, running)
            token = _PROGRESS_SINK.set(progress)
            try:
                preflight()
                result = operation()
            except Exception as exc:
                with _task_lock(workflow), workflow.store.mutation_lock():
                    failed = _read(workflow, payload["task_id"])
                    failed.update(status="failed", error=_error_message(exc), updated_at=_now(), finished_at=_now())
                    _write(workflow, failed)
                raise
            finally:
                _PROGRESS_SINK.reset(token)
            with _task_lock(workflow), workflow.store.mutation_lock():
                done = _read(workflow, payload["task_id"])
                done.update(status="completed", direction_id=result["direction_id"], updated_at=_now(), finished_at=_now())
                _write(workflow, done)
            return result

        try:
            payload["job_id"] = runner.submit("cover.direction", execute,
                meta={"project_id": project_id, "project_path": str(workflow.store.project_path) if project_id else "",
                      "session_id": session_id, "direction_task_id": payload["task_id"]},
                unique_key=f"cover-direction:{payload['task_id']}",
                result_mapper=lambda result: {"direction_id": result["direction_id"]})
        except Exception as exc:
            payload.update(status="failed", error=_error_message(exc), updated_at=_now(), finished_at=_now())
            _write(workflow, payload)
            raise
        _write(workflow, payload)
        return _project(payload, runner)
