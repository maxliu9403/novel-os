"""In-memory background job runner for long agent phases.

Agent phases (write/edit/validate…) call the LLM and take 30–90s, so they must
never run inline in a request. We run them on a daemon thread and expose status
for the UI to poll. Single-process, single-user no external queue needed.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional


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


class JobRunner:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        # Resolved paths, rather than display ids, keep independent workspace
        # roots isolated. Deleted paths stay fenced from already-prepared work;
        # project creation allocates a fresh path instead of reusing one.
        self._blocked_project_paths: set[str] = set()

    def submit(
        self,
        kind: str,
        fn: Callable[[], object],
        meta: Optional[dict] = None,
        *,
        result_mapper: Callable[[object], dict] | None = None,
        unique_key: str = "",
    ) -> str:
        with self._lock:
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
            }

        def run() -> None:
            try:
                result = fn()
                result_meta = (
                    result_mapper(result)
                    if result_mapper is not None
                    else self._safe_result_meta(result)
                )
                self._update(job_id, status="done", meta=result_meta)
            except Exception as e:  # noqa: BLE001 - surface any agent failure to the UI
                self._update(job_id, status="error", error=f"{type(e).__name__}: {e}")

        threading.Thread(target=run, daemon=True).start()
        return job_id

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

    def prepare_project_deletion(self, project_id: str, project_path: str) -> None:
        """Atomically reject active work and fence the path from new work.

        The fence intentionally remains after deletion.  This prevents a
        request that prepared a callable just before deletion from submitting
        it afterward and recreating files.  Deleted ids are never reused, so
        project creation must not clear this fence.
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
            self._blocked_project_paths.add(project_path)

    @staticmethod
    def _matches_project(job: dict, project_id: str, project_path: str) -> bool:
        """Prefer the tenant-qualified path, retaining legacy id-only jobs."""
        job_path = str(job.get("project_path") or "")
        if project_path and job_path:
            return job_path == project_path
        return job.get("project_id") == project_id

    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                if "meta" in fields:
                    merged = dict(job.get("meta") or {})
                    merged.update(fields.pop("meta") or {})
                    fields["meta"] = merged
                job.update(fields)
                job["finished_at"] = _now()

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
        result.pop("_unique_key", None)
        result["meta"] = dict(job.get("meta") or {})
        return result


# Process-wide singleton.
runner = JobRunner()
