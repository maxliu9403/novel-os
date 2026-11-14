"""In-memory background job runner for long agent phases.

Agent phases (write/edit/validate…) call the LLM and take 30–90s, so they must
never run inline in a request. We run them on a daemon thread and expose status
for the UI to poll. Single-process, single-user no external queue needed.
"""

from __future__ import annotations

from contextvars import copy_context
from contextlib import contextmanager, nullcontext
from core.usage_tracking import usage_scope

import threading
import uuid
from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from core.projection_events import notify as notify_projection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectJobBlocked(RuntimeError):
    """A project is being, or has been, permanently deleted."""


class ProjectJobsRunning(RuntimeError):
    """Permanent deletion cannot start while a project job is running."""

    def __init__(self, project_id: str, job_ids: list[str]) -> None:
        self.project_id = project_id
        self.job_ids = tuple(job_ids)
        super().__init__(
            f"Project {project_id!r} has running jobs: {', '.join(job_ids)}"
        )


class SessionJobBlocked(RuntimeError):
    """A deleted discussion must not start already-prepared background work."""


class SessionJobsRunning(RuntimeError):
    """Discussion deletion must not orphan its running inference or covers."""


class JobRunner:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        # Resolved paths, rather than display ids, keep independent workspace
        # roots isolated. Deleted paths stay fenced from already-prepared work;
        # project creation allocates a fresh path instead of reusing one.
        self._blocked_project_paths: set[str] = set()
        # Workshop ids are server-generated UUIDs, also used by cover jobs.
        self._blocked_session_ids: set[str] = set()
        self._boot_epoch = uuid.uuid4().hex
        self._scope_revisions: dict[str, int] = defaultdict(int)
        self._scope_changes: dict[str, deque] = defaultdict(lambda: deque(maxlen=512))
        self._visible_jobs: dict[str, dict[tuple[str, str], str]] = defaultdict(dict)
        self._active_jobs: dict[str, set[str]] = defaultdict(set)

    def submit(
        self,
        kind: str,
        fn: Callable[[], object],
        meta: Optional[dict] = None,
        *,
        result_mapper: Callable[[object], dict] | None = None,
        unique_key: str = "",
        cancel_event: threading.Event | None = None,
        defer_cancel_completion: bool = False,
    ) -> str:
        with self._lock:
            if (meta or {}).get("session_id") in self._blocked_session_ids:
                raise SessionJobBlocked("这条头脑风暴记录已删除，请刷新列表。")
            project_path = str((meta or {}).get("project_path") or "")
            if project_path and project_path in self._blocked_project_paths:
                raise ProjectJobBlocked(
                    f"Project at {project_path!r} is unavailable for new jobs"
                )
            if unique_key:
                for existing in self._jobs.values():
                    if (
                        existing.get("_unique_key") == unique_key
                        and existing.get("status") == "running"
                    ):
                        return str(existing["job_id"])
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = {
                "job_id": job_id,
                "kind": kind,
                "status": "running",
                "error": None,
                "started_at": _now(),
                "finished_at": None,
                "meta": dict(meta or {}),
                **(meta or {}),
                "_unique_key": unique_key,
                "_cancel_event": cancel_event,
                "_defer_cancel_completion": defer_cancel_completion,
                "cancel_requested": False,
            }
            self._record_change_locked(self._jobs[job_id])

        notify_projection(project_path, "task", "cover")

        def run() -> None:
            try:
                owner = (meta or {}).get("project_path")
                scope = usage_scope(owner, "cover" if kind.startswith("cover.") else "manuscript") if owner else nullcontext()
                with scope:
                    result = fn()
                result_meta = (
                    result_mapper(result)
                    if result_mapper is not None
                    else self._safe_result_meta(result)
                )
                self._update(job_id, status="done", meta=result_meta)
            except Exception as e:  # noqa: BLE001 - surface any agent failure to the UI
                self._update(job_id, status="error", error=f"{type(e).__name__}: {e}")

        threading.Thread(target=copy_context().run, args=(run,), daemon=True).start()
        return job_id

    def cancel(self, job_id: str) -> bool:
        """Request cancellation; opted-in workers remain active until they drain."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("_cancel_event") is None:
                return False
            if job.get("_cancelled") or job.get("cancel_requested"):
                return True
            if job.get("status") != "running":
                return False
            job["_cancel_event"].set()
            job["cancel_requested"] = True
            if not job.get("_defer_cancel_completion"):
                job.update(status="error", error="任务已暂停。", finished_at=_now(), _cancelled=True)
            self._record_change_locked(job)
            project_path = job.get("project_path", "")
        notify_projection(project_path, "task", "cover")
        return True

    def running_for_project(
        self, project_id: str, *, project_path: str = ""
    ) -> list[dict]:
        """Return detached snapshots of active jobs for one project."""
        with self._lock:
            return [
                self._copy_job(job)
                for job in self._jobs.values()
                if job.get("status") == "running"
                and self._matches_project(job, project_id, project_path)
            ]

    @contextmanager
    def session_deletion(self, session_id: str):
        """Commit the durable tombstone before releasing the submission fence."""
        with self._lock:
            if any(job.get("status") == "running" and job.get("session_id") == session_id
                   for job in self._jobs.values()):
                raise SessionJobsRunning("讨论或关联封面任务仍在运行，请先暂停或等待完成后再删除。")
            yield
            self._blocked_session_ids.add(session_id)

    def latest_for_project(
        self, project_id: str, *, project_path: str = "", kind: str, run_id: str
    ) -> dict | None:
        """Inspect the latest submitted operation without treating it as run state."""
        with self._lock:
            for job in reversed(self._jobs.values()):
                if (
                    job.get("kind") == kind
                    and job.get("run_id") == run_id
                    and self._matches_project(job, project_id, project_path)
                ):
                    return self._copy_job(job)
        return None

    def prepare_project_deletion(self, project_id: str, project_path: str) -> bool:
        """Atomically reject active work and fence the path from new work.

        The fence intentionally remains after deletion.  This prevents a
        request that prepared a callable just before deletion from submitting
        it afterward and recreating files. Deleted paths are never reused in
        this process; a later process can explicitly create a new catalog epoch.
        """
        with self._lock:
            running = [
                str(job["job_id"])
                for job in self._jobs.values()
                if job.get("status") == "running"
                and self._matches_project(job, project_id, project_path)
            ]
            if running:
                raise ProjectJobsRunning(project_id, running)
            new_fence = project_path not in self._blocked_project_paths
            self._blocked_project_paths.add(project_path)
            return new_fence

    def rollback_project_deletion_preparation(self, project_path: str, *, new_fence: bool) -> None:
        """Undo only this attempt's fence before any destructive commit.

        Caller must still hold the project deletion lease. A retry of an
        already-committed deletion must never remove its older fence.
        """
        if new_fence:
            with self._lock:
                self._blocked_project_paths.discard(project_path)

    @staticmethod
    def _matches_project(job: dict, project_id: str, project_path: str) -> bool:
        """Prefer the tenant-qualified path, retaining legacy id-only jobs."""
        job_path = str(job.get("project_path") or "")
        if project_path and job_path:
            return job_path == project_path
        return job.get("project_id") == project_id

    def _update(self, job_id: str, **fields) -> None:
        project_path = ""
        with self._lock:
            job = self._jobs.get(job_id)
            if job and not job.get("_cancelled"):
                if job.get("_defer_cancel_completion") and job.get("cancel_requested"):
                    # The callable has exited, including any in-flight result
                    # persistence. Only now release uniqueness/deletion fences.
                    fields.update(status="error", error="任务已暂停。", _cancelled=True)
                if "meta" in fields:
                    merged = dict(job.get("meta") or {})
                    merged.update(fields.pop("meta") or {})
                    fields["meta"] = merged
                job.update(fields)
                job["finished_at"] = _now()
                self._record_change_locked(job)
                project_path = job.get("project_path", "")
        notify_projection(project_path, "task", "cover", "delivery")

    def _record_change_locked(self, job: dict) -> None:
        """Version one root's visible operation state under the registry lock."""
        raw_path = job.get("project_path")
        if not raw_path:
            # Legacy id-only jobs cannot be safely assigned to a tenant/root.
            return
        path = Path(raw_path).resolve()
        scope = str(path.parent)
        project_id = path.name
        job_id = job["job_id"]
        self._scope_revisions[scope] += 1
        self._visible_jobs[scope][(project_id, str(job.get("kind", "")))] = job_id
        if job.get("status") == "running":
            self._active_jobs[scope].add(job_id)
        else:
            self._active_jobs[scope].discard(job_id)
        self._scope_changes[scope].append({
            "revision": self._scope_revisions[scope],
            "project_id": project_id,
            "job": deepcopy(self._copy_job(job)),
        })

    def library_snapshot(self, root: str | Path) -> dict:
        """One detached, immutable-in-use snapshot for rows, filters and ETags.

        Only latest per-kind operations plus still-active operations are read;
        historical jobs are never rescanned once per library card. Revisions
        are root-local, so activity in another library cannot reveal itself.
        """
        scope = str(Path(root).resolve())
        with self._lock:
            revision = self._scope_revisions.get(scope, 0)
            changes = list(self._scope_changes.get(scope, ()))
            ids = set(self._visible_jobs.get(scope, {}).values())
            ids.update(self._active_jobs.get(scope, ()))
            return {
                "boot_epoch": self._boot_epoch,
                "revision": revision,
                "jobs": [deepcopy(self._copy_job(self._jobs[key])) for key in sorted(ids)],
                "changes": deepcopy(changes),
                "oldest_revision": changes[0]["revision"] - 1 if changes else revision,
            }

    @staticmethod
    def _safe_result_meta(result: object) -> dict:
        cover_set_id = str(getattr(result, "cover_set_id", "") or "")
        if not cover_set_id:
            return {}
        candidates = tuple(getattr(result, "candidates", ()) or ())
        return {
            "cover_set_id": cover_set_id,
            "cover_status": str(getattr(result, "status", "") or ""),
            "ready_candidates": sum(
                getattr(candidate, "status", "") in {"ready", "selected"}
                for candidate in candidates
            ),
        }

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return self._copy_job(job)

    @staticmethod
    def _copy_job(job: dict) -> dict:
        result = dict(job)
        result['cancel_supported'] = job.get('_cancel_event') is not None and bool(job.get('_defer_cancel_completion'))
        result.pop("_unique_key", None)
        result.pop("_cancel_event", None)
        result.pop("_cancelled", None)
        result.pop("_defer_cancel_completion", None)
        result["meta"] = dict(job.get("meta") or {})
        return result


# Process-wide singleton.
runner = JobRunner()
