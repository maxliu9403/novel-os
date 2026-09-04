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


class JobRunner:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

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
            result = dict(job)
            result.pop("_unique_key", None)
            result["meta"] = dict(job.get("meta") or {})
            return result


# Process-wide singleton.
runner = JobRunner()
