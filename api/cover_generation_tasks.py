"""Owner-local batch recovery; task reads never authorize another image call."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import re
import threading
import uuid
import weakref

from fastapi import HTTPException


_PROCESS_ID = uuid.uuid4().hex
_TASK_ID = re.compile(r"^generation-task-[0-9a-f]{32}$")
_ACTIVE = {"queued", "running"}
_PHASES = {"preparing", "generating", "composing", "reviewing", "comparing", "saving"}
_LOCKS = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()
_PROGRESS_SINK = ContextVar("cover_generation_progress_sink", default=None)
_GENERATION_BATCH_ID = ContextVar("cover_generation_batch_id", default="")
_REQUEST_ID = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$")


class GenerationProgressError(RuntimeError):
    """Durability failure must escape per-candidate provider error handling."""


def current_progress_sink():
    return _PROGRESS_SINK.get()


def current_generation_batch_id():
    return _GENERATION_BATCH_ID.get()


@contextmanager
def _batch_scope(task_id):
    token = _GENERATION_BATCH_ID.set(task_id)
    try:
        yield
    finally:
        _GENERATION_BATCH_ID.reset(token)


@contextmanager
def progress_scope(sink):
    token = _PROGRESS_SINK.set(sink)
    try:
        yield
    finally:
        _PROGRESS_SINK.reset(token)



def _now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _task_lock(workflow):
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(workflow.store.root.resolve()), threading.RLock())
    with lock:
        yield


def _directory(workflow):
    path = workflow.store.root / "generation_tasks"
    if path.is_symlink():
        raise ValueError("Invalid cover generation task directory")
    return path


def _read(workflow, task_id):
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise ValueError("Invalid cover generation task id")
    path = _directory(workflow) / f"{task_id}.json"
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("Cover generation task does not exist")
    value = json.loads(path.read_text("utf-8"))
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("status") not in _ACTIVE | {"completed", "failed", "interrupted"}
            or not isinstance(value.get("created_at"), str)):
        raise ValueError("Invalid cover generation task record")
    if value.get("owner_scope") != workflow.context.owner_scope or value.get("task_id") != task_id:
        raise FileNotFoundError("Cover generation task does not belong to this owner")
    return value


def _write(workflow, value):
    path = _directory(workflow) / f"{value['task_id']}.json"
    if path.is_symlink():
        raise ValueError("Invalid cover generation task path")
    workflow.store._write_json(path, value)


def _records(workflow):
    values = []
    for path in _directory(workflow).glob("generation-task-*.json"):
        try:
            values.append(_read(workflow, path.stem))
        except (OSError, ValueError):
            continue
    return sorted(values, key=lambda value: (value["created_at"], value["task_id"]), reverse=True)


def _project(value, runner):
    result = dict(value)
    if value["status"] in _ACTIVE:
        job = runner.get(value.get("job_id", "")) if value.get("process_id") == _PROCESS_ID else None
        if not (job and job.get("status") == "running"
                and (job.get("generation_task_id") or (job.get("meta") or {}).get("generation_task_id")) == value["task_id"]):
            result.update(status="interrupted", error="服务已重启或出图任务已中断；请检查本批已保存候选，不会自动重复出图。")
    return {key: val for key, val in result.items() if key not in {"owner_scope", "process_id"}}


def latest(workflow, runner):
    with _task_lock(workflow):
        records = _records(workflow)
        return _project(records[0], runner) if records else None


def get(workflow, task_id, runner):
    with _task_lock(workflow):
        return _project(_read(workflow, task_id), runner)


def as_job(task, runner):
    """Keep the existing POST Job envelope without exposing record internals."""
    return {"job_id": task["job_id"], "task_id": task["task_id"], "kind": "cover.generate",
        "status": "running" if task["status"] in _ACTIVE else "done" if task["status"] == "completed" else "error",
        "error": task["error"], "started_at": task["started_at"] or task["created_at"],
        "finished_at": task["finished_at"], "cancel_requested": False,
        "meta": {"generation_task_id": task["task_id"], "direction_id": task["direction_id"],
                 "cover_set_id": task["cover_set_id"], "cover_status": task.get("result_status")}}


def _message(exc):
    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
    return (detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)) or type(exc).__name__


def _progress_fields(event, count):
    if event.get("phase") not in _PHASES or event.get("total") != count:
        raise ValueError("Invalid cover generation phase or total")
    for key in ("total", "completed", "failed", "ready"):
        if type(event.get(key)) is not int or not 0 <= event[key] <= count:
            raise ValueError("Invalid cover generation progress count")
    if event["completed"] != event["ready"] + event["failed"]:
        raise ValueError("Cover generation completed count must include ready and failed attempts")
    candidate, index = event.get("current_candidate_id"), event.get("current_candidate_index")
    if candidate is not None and (not isinstance(candidate, str) or not candidate):
        raise ValueError("Invalid cover generation candidate")
    if index is not None and (type(index) is not int or not 1 <= index <= count):
        raise ValueError("Invalid cover generation candidate index")
    return {key: event.get(key) for key in ("phase", "total", "completed", "failed", "ready",
                                            "current_candidate_id", "current_candidate_index")}


def start(workflow, runner, *, operation, preflight, count, direction_id="", project_id="", session_id="",
          request_id="", approved_direction_sha256=""):
    if not isinstance(request_id, str) or (request_id and not _REQUEST_ID.fullmatch(request_id)):
        raise ValueError("Invalid cover generation request id")
    with _task_lock(workflow), workflow.store.mutation_lock():
        records = _records(workflow)
        def same_intent(previous):
            return (previous["direction_id"] == direction_id and previous["count"] == count
                    and previous.get("approved_direction_sha256", "") == approved_direction_sha256)
        if request_id:
            for previous in records:
                if request_id == previous.get("request_id") or request_id in previous.get("request_aliases", []):
                    if not same_intent(previous):
                        raise ValueError("Generation request id is already bound to different approved inputs")
                    return _project(previous, runner)
        for previous in records:
            if previous["status"] in _ACTIVE:
                projected = _project(previous, runner)
                if projected["status"] in _ACTIVE:
                    if request_id:
                        if not same_intent(previous):
                            from core.cover_store import CoverConflict
                            raise CoverConflict("已有不同的封面出图任务正在运行，请等待本批结束。")
                        # A double click in another tab is the same active work.
                        # Remember its key so a delayed replay after completion
                        # cannot silently create another paid batch.
                        previous["request_aliases"] = [*previous.get("request_aliases", []), request_id]
                        _write(workflow, previous)
                        projected = _project(previous, runner)
                    return projected
                previous.update(status="interrupted", error=projected["error"], updated_at=_now())
                _write(workflow, previous)
        if type(count) is not int or count < 1:
            raise ValueError("Cover generation requires a positive candidate count")
        from .cover_repair_tasks import assert_idle
        assert_idle(workflow, runner)
        task_id = f"generation-task-{uuid.uuid4().hex}"
        with _batch_scope(task_id):
            preflight()
        now = _now()
        payload = {"schema_version": 1, "task_id": task_id,
            "owner_scope": workflow.context.owner_scope, "process_id": _PROCESS_ID,
            "job_id": "", "status": "queued", "direction_id": direction_id,
            "request_id": request_id, "request_aliases": [],
            "approved_direction_sha256": approved_direction_sha256,
            "cover_set_id": None, "count": count, "progress": None, "result_status": None,
            "error": None, "created_at": now, "started_at": None, "updated_at": now, "finished_at": None}
        _write(workflow, payload)
        poisoned = None
        operation_finished = False

        def progress(event):
            nonlocal poisoned
            if operation_finished:
                return  # A completed callable cannot publish late work.
            if poisoned is not None:
                raise poisoned
            try:
                with _task_lock(workflow):
                    try:
                        current = _read(workflow, payload["task_id"])
                    except FileNotFoundError as exc:
                        raise GenerationProgressError("Live cover generation task record is missing") from exc
                    if current["status"] != "running" or current["process_id"] != _PROCESS_ID:
                        raise GenerationProgressError("Live cover generation task no longer owns execution")
                    fields = _progress_fields(event, count)
                    set_id = event.get("cover_set_id")
                    if not isinstance(set_id, str) or not set_id:
                        raise ValueError("Cover generation progress requires an exact batch")
                    if current["cover_set_id"] not in {None, set_id}:
                        raise ValueError("Cover generation progress belongs to another batch")
                    cover = workflow.store.load(set_id)
                    if len(cover.candidates) != count:
                        raise ValueError("Cover generation batch count differs from the task")
                    expected_owner = project_id or (f"workshop-{session_id}" if session_id else None)
                    if expected_owner and cover.project_id != expected_owner:
                        raise ValueError("Cover generation batch belongs to another owner")
                    counts = {"completed": sum(item.status != "pending" for item in cover.candidates),
                        "ready": sum(item.status in {"ready", "selected"} for item in cover.candidates),
                        "failed": sum(item.status == "failed" for item in cover.candidates)}
                    if any(fields[key] != number for key, number in counts.items()):
                        raise ValueError("Cover generation progress counts differ from the saved batch")
                    candidate_ids = [item.candidate_id for item in cover.candidates]
                    candidate, index = fields["current_candidate_id"], fields["current_candidate_index"]
                    if candidate and (candidate not in candidate_ids or (index and candidate_ids[index - 1] != candidate)):
                        raise ValueError("Cover generation progress belongs to another candidate")
                    previous = current.get("progress") or {}
                    now = _now()
                    same = all(fields[key] == previous.get(key) for key in ("phase", "current_candidate_id", "current_candidate_index"))
                    current.update(cover_set_id=set_id, updated_at=now, progress={**fields,
                        "phase_started_at": previous["phase_started_at"] if same else now, "last_event_at": now})
                    _write(workflow, current)
            except Exception as exc:
                poisoned = GenerationProgressError(f"Cover generation progress could not be saved: {exc}")
                raise poisoned from exc

        def terminal(**changes):
            with _task_lock(workflow):
                current = _read(workflow, payload["task_id"])
                now = _now()
                current.update(updated_at=now, finished_at=now, **changes)
                _write(workflow, current)

        def execute():
            nonlocal operation_finished
            with _task_lock(workflow):
                running = _read(workflow, payload["task_id"])
                now = _now()
                running.update(status="running", started_at=now, updated_at=now)
                _write(workflow, running)
            token = _PROGRESS_SINK.set(progress)
            batch_token = _GENERATION_BATCH_ID.set(payload["task_id"])
            try:
                preflight()
                result = operation()
                if poisoned is not None:
                    raise poisoned
                set_id = result.cover_set_id
                with _task_lock(workflow):
                    current = _read(workflow, payload["task_id"])
                    if current["cover_set_id"] not in {None, set_id} or len(result.candidates) != count:
                        raise ValueError("Returned cover batch differs from the generation task")
                    saved = workflow.store.load(set_id)
                    if saved.to_dict() != result.to_dict():
                        raise ValueError("Returned cover batch differs from its persisted result")
                    expected_owner = project_id or (f"workshop-{session_id}" if session_id else None)
                    if expected_owner and result.project_id != expected_owner:
                        raise ValueError("Returned cover batch belongs to another owner")
                    ready = sum(item.status in {"ready", "selected", "rejected"} for item in result.candidates)
                    failed = sum(item.status == "failed" for item in result.candidates)
                    if ready + failed != count:
                        raise ValueError("Cover generation returned unfinished candidates")
                    previous = current.get("progress") or {}
                    now = _now()
                    fields = {**previous, "phase": previous.get("phase", "saving"), "total": count,
                        "completed": count, "failed": failed, "ready": ready,
                        "current_candidate_id": None, "current_candidate_index": None,
                        "phase_started_at": previous.get("phase_started_at", now), "last_event_at": now}
                    terminal(status="failed" if failed == count else "completed", cover_set_id=set_id,
                        result_status="failed" if failed == count else "partial" if failed else str(result.status),
                        progress=fields, error="本批候选均生成失败，请查看已保存的失败详情。" if failed == count else None)
                return result
            except Exception as exc:
                try:
                    terminal(status="failed", error=_message(exc))
                except (OSError, ValueError):
                    pass  # Keep the last durable record; GET reports interruption.
                raise
            finally:
                operation_finished = True
                _PROGRESS_SINK.reset(token)
                _GENERATION_BATCH_ID.reset(batch_token)

        try:
            payload["job_id"] = runner.submit("cover.generate", execute,
                meta={"project_id": project_id, "project_path": str(workflow.store.project_path) if project_id else "",
                      "session_id": session_id, "direction_id": direction_id, "generation_task_id": payload["task_id"]},
                unique_key=f"cover-generation:{payload['task_id']}")
        except Exception as exc:
            payload.update(status="failed", error=_message(exc), updated_at=_now(), finished_at=_now())
            _write(workflow, payload)
            raise
        _write(workflow, payload)
        return _project(payload, runner)
