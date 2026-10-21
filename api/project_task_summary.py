"""Read-only project-card projection of durable full-book task records."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .jobs import runner

RUN_STATUSES = {"pending", "running", "paused", "completed", "failed", "cancelled"}
RESUMABLE_STATUSES = {"failed", "paused"}
_RUN_ID = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def read_task_manifest(project: Path, run_id: str) -> dict:
    """Resolve within this project; never follow a run or output directory alias."""
    if not _RUN_ID.fullmatch(run_id):
        raise FileNotFoundError("Task not found")
    path = project / "outputs" / "runs" / run_id / "run.json"
    if any(part.is_symlink() for part in (path, *list(path.parents)[:3])):
        raise FileNotFoundError("Task not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict) or data.get("run_id") != run_id
        or not isinstance(data.get("spec"), dict)
        or not isinstance(data.get("status"), str) or data["status"] not in RUN_STATUSES
    ):
        raise ValueError("Invalid task manifest")
    return data


def latest_task_manifest(project: Path) -> dict | None:
    candidates = []
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    for path in (project / "outputs" / "runs").glob("*/run.json"):
        try:
            data = read_task_manifest(project, path.parent.name)
        except (OSError, ValueError):
            continue
        if data["spec"].get("dry_run"):
            continue
        candidates.append((parse_task_time(data.get("created_at")) or oldest, data["run_id"], data))
    return max(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None


def _valid_resume_contract(manifest: dict) -> bool:
    try:
        from pipeline_models import RunManifest
        if any(manifest.get(key) is not None and not isinstance(manifest[key], str)
               for key in ("error", "current_phase")):
            return False
        RunManifest.from_dict(manifest)
        return True
    except (AttributeError, TypeError, ValueError, KeyError):
        return False


def task_runtime(project: Path, manifest: dict, *, is_latest: bool) -> dict:
    """Overlay a submitted worker only while active; the manifest stays canonical."""
    project_path = str(project.resolve())
    active = runner.running_for_project(project.name, project_path=project_path)
    resume = runner.latest_for_project(project.name, project_path=project_path,
                                       kind="novel.resume", run_id=manifest["run_id"])
    if resume and resume["status"] != "running":
        updated = parse_task_time(manifest.get("updated_at"))
        finished = parse_task_time(resume.get("finished_at"))
        if (updated and finished and updated > finished) or (
            manifest["status"] == "completed" and resume["status"] == "error"
        ):
            # A later CLI resume can supersede this process-local attempt.
            resume = None
    resuming = bool(resume and resume["status"] == "running")
    return {
        "status": "running" if resuming else manifest["status"],
        "can_resume": bool(
            is_latest and manifest["status"] in RESUMABLE_STATUSES
            and not manifest["spec"].get("dry_run") and not active
            and _valid_resume_contract(manifest)
        ),
        "resume_job_id": resume["job_id"] if resume else None,
        "resume_job_status": resume["status"] if resume else None,
        "resume_job_error": resume.get("error") if resume else None,
    }


def parse_task_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


@dataclass(frozen=True)
class ProjectTaskSummary:
    status: str
    task_id: str | None = None
    task_started_at: str | None = None
    task_can_resume: bool = False
    task_error: str | None = None
    task_phase: str | None = None
    task_resume_job_id: str | None = None


def project_task_summary(project: Path, metadata: dict, chapters: list[Any]) -> ProjectTaskSummary:
    """Keep status, ID and start bound to the same latest production run.

    File mtime, updated_at and stage times all change during retry, resume or
    export. Only manifest.created_at records the task's original start.
    """
    manifest = latest_task_manifest(project)
    if manifest:
        started = parse_task_time(manifest.get("created_at"))
        runtime = task_runtime(project, manifest, is_latest=True)
        return ProjectTaskSummary(
            runtime["status"], manifest["run_id"], started.isoformat() if started else None,
            runtime["can_resume"],
            manifest.get("error") if isinstance(manifest.get("error"), str) else None,
            manifest.get("current_phase") if isinstance(manifest.get("current_phase"), str) else None,
            runtime["resume_job_id"],
        )

    # Imported/legacy projects have no pipeline task. Do not invent its ID or
    # substitute a workshop session / project creation time for its start.
    explicit = metadata.get("status")
    if isinstance(explicit, str) and explicit in {"completed", "failed", "paused", "cancelled", "interrupted"}:
        return ProjectTaskSummary(explicit)
    try:
        target = max(len(chapters), int(metadata.get("target_chapters") or 0))
    except (ValueError, TypeError):
        target = len(chapters)
    if chapters and len(chapters) >= target and all(c.status == "complete" for c in chapters):
        return ProjectTaskSummary("completed")
    if any(c.status != "planned" or c.word_count > 0 for c in chapters):
        return ProjectTaskSummary("in_progress")
    return ProjectTaskSummary("draft")
