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


def latest_task_manifest(project: Path, *, strict: bool = False) -> dict | None:
    """Select a production run; strict provenance readers must not skip corruption."""
    candidates = []
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    for path in (project / "outputs" / "runs").glob("*/run.json"):
        try:
            data = read_task_manifest(project, path.parent.name)
        except (OSError, ValueError):
            if strict:
                raise ValueError("Cannot establish latest task from an unreadable run record")
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


def task_runtime(project: Path, manifest: dict, *, is_latest: bool, planning: dict | None = None) -> dict:
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
    matching = next((job for job in active if job.get('kind') in {'novel.run', 'novel.resume'}
                     and job.get('run_id') == manifest['run_id']), None)
    planning = (planning or task_sidecar(project, manifest['run_id'], 'planning/progress.json')) if manifest.get('current_phase') == 'outline' else None
    needs_design = bool(planning and planning.get('status') == 'failed'
                        and (planning.get('recovery') or {}).get('retryable') is False)
    from core.pipeline_request_recovery import projection as request_projection
    recovery = request_projection(project, manifest)
    request_blocked = bool(recovery and not recovery['can_resume'])
    can_resume = bool(
        is_latest and manifest["status"] in RESUMABLE_STATUSES
        and not manifest["spec"].get("dry_run") and not active
        and _valid_resume_contract(manifest) and not needs_design and not request_blocked
    )
    blocked_reason = None
    if not can_resume:
        if active:
            blocked_reason = '该作品已有任务运行中，请等待当前任务结束后重试。'
        elif not is_latest or manifest['status'] not in RESUMABLE_STATUSES or manifest['spec'].get('dry_run'):
            blocked_reason = '仅可恢复当前作品最新的失败或暂停创作任务。'
        elif request_blocked:
            blocked_reason = recovery['message']
        elif needs_design:
            blocked_reason = ((planning.get('recovery') or {}).get('reason') or planning.get('next_action')
                              or '已保存的大纲需要检查；当前修订额度不可继续，普通恢复不会追加模型调用。')
        else:
            blocked_reason = '任务记录无法读取，请检查已保存的任务文件。'
    return {
        "status": "running" if resuming or matching else manifest["status"],
        "can_resume": can_resume,
        "resume_blocked_reason": blocked_reason,
        "resume_job_id": resume["job_id"] if resume else None,
        "resume_job_status": resume["status"] if resume else None,
        "resume_job_error": resume.get("error") if resume else None,
        "active_job_id": matching['job_id'] if matching else None,
        "can_pause": bool(matching and matching.get('cancel_supported') and not matching.get('cancel_requested')),
        "cancel_requested": bool(matching and matching.get('cancel_requested')),
        "request_recovery": recovery,
    }


def task_sidecar(project: Path, run_id: str, relative: str) -> dict | None:
    """Read bounded diagnostics only from the validated task directory."""
    root = project / 'outputs' / 'runs' / run_id
    path = root / relative
    try:
        if path.resolve() != path.absolute() or not path.is_file() or path.stat().st_size > 512_000:
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('run_id') != run_id or data.get('schema_version') != 1:
            return None
        if relative == 'progress.json':
            # Do not expose arbitrary extra fields added to an on-disk file.
            allowed = {'schema_version', 'execution_id', 'run_id', 'status', 'phase', 'chapter',
                       'started_at', 'updated_at', 'phase_started_at', 'heartbeat_at',
                       'last_upstream_activity_at', 'last_model_activity_at', 'last_output_at',
                       'request_timeout_seconds', 'stage_timeout_seconds', 'message', 'next_action'}
            safe = {key: data[key] for key in allowed if key in data and isinstance(data[key], (str, int, float, type(None)))}
            request = data.get('request')
            safe['request'] = {key: request[key] for key in ('request_id', 'provider', 'model', 'started_at', 'status',
                'output_chars', 'model_activity_count', 'error_type', 'timeout_seconds') if key in request
                and isinstance(request[key], (str, int, float, type(None)))} if isinstance(request, dict) else None
            milestones = data.get('milestones') if isinstance(data.get('milestones'), list) else []
            safe['milestones'] = [{key: entry[key] for key in ('at', 'phase', 'chapter', 'status', 'message') if key in entry
                and isinstance(entry[key], (str, int, type(None)))} for entry in milestones[-80:] if isinstance(entry, dict)]
            return safe
        if relative == 'planning/progress.json':
            safe = {key: data[key] for key in ('schema_version', 'planning_schema', 'run_id', 'identity_hash',
                'status', 'active_step', 'updated_at', 'completed_batches', 'total_batches', 'chapter_count',
                'message', 'next_action') if key in data and isinstance(data[key], (str, int, type(None)))}
            for name, fields, limit in (('steps', ('id', 'label', 'status', 'summary'), 20),
                                        ('milestones', ('id', 'time', 'label', 'summary'), 100)):
                entries = data.get(name) if isinstance(data.get(name), list) else []
                safe[name] = [{key: entry[key][:2000] for key in fields if isinstance(entry.get(key), str)}
                              for entry in entries[-limit:] if isinstance(entry, dict)]
            reuse = data.get('workshop_reuse')
            safe['workshop_reuse'] = {key: reuse[key] for key in ('used', 'reason') if key in reuse
                and isinstance(reuse[key], (bool, str))} if isinstance(reuse, dict) else None
            recovery = data.get('recovery')
            safe['recovery'] = {'retryable': recovery['retryable'],
                'action': str(recovery.get('action', ''))[:80], 'reason': str(recovery.get('reason', ''))[:2000]} \
                if isinstance(recovery, dict) and type(recovery.get('retryable')) is bool else None
            from core.planning_recovery import (
                candidate_citation_recovery_available, coordinated_repair_available, evidence_recovery_available,
                metadata_recovery_available, residual_recovery_availability, source_restoration_available,
                specialized_review_availability,
            )
            from core.planning_author_decisions import ACTION, decision_status
            if isinstance(recovery, dict) and recovery.get('action') == ACTION:
                try:
                    questions, answered = decision_status(project, run_id, data)
                except (ValueError, OSError, KeyError, TypeError):
                    message = '无法核对已保存的原文及作者选择，请检查任务记录。'
                    safe['recovery'] = {'retryable': False, 'action': 'inspect_saved_design', 'reason': message}
                    safe['next_action'] = message
                    return safe
                for name in ('completed', 'deferred'):
                    indices = data.get(name + '_issue_indices')
                    if isinstance(indices, list) and all(type(i) is int and i >= 0 for i in indices):
                        safe[name + '_issue_count'] = len(set(indices))
                if answered:
                    message = '已保存作者的原文选择，恢复后将据此继续修订。'
                    safe['recovery'] = {'retryable': True, 'action': 'continue_author_choice', 'reason': message}
                    safe['next_action'] = message
                else:
                    safe['author_decisions'] = questions
                return safe
            residual = residual_recovery_availability(project, run_id, data)
            if residual is not None:
                message = ('已保留最近一次修订的完整细纲，恢复后将从终审列出的问题继续修订。' if residual else
                           '无法核对最近一次修订细纲与终审的检查点，请检查任务记录。')
                safe['recovery'] = {'retryable': residual,
                                    'action': 'repair_saved_residuals' if residual else 'inspect_saved_design',
                                    'reason': message}
                safe['next_action'] = message
                return safe
            old_review = specialized_review_availability(project, run_id, data)
            if old_review is False:
                message = '已保存修订与复核依据不匹配，请检查任务记录。'
                safe['recovery'] = {'retryable': False, 'action': 'inspect_saved_design', 'reason': message}
                safe['next_action'] = message
                return safe
            if old_review or evidence_recovery_available(project, run_id, data):
                message = '已保存全书细纲。恢复后将核对原文与候选假设，继续修订受影响的章节。'
                safe['recovery'] = {'retryable': True, 'action': 'recheck_saved_evidence', 'reason': message}
                safe['next_action'] = message
                return safe
            if candidate_citation_recovery_available(project, run_id, data):
                message = '已核对修订方案引用的字段值。继续后将复用已保存的方案，修订对应问题并重新核查。'
                safe['recovery'] = {'retryable': True, 'action': 'continue_saved_citation_plan', 'reason': message}
                safe['next_action'] = message
                return safe
            if metadata_recovery_available(project, run_id, data):
                message = ('已保存的修订内容可在本地校正来源引用，不重新生成章节或追加修订调用。'
                           '恢复后仍须核查完整大纲；核查和后续创作会产生模型用量。')
                safe['recovery'] = {'retryable': True, 'action': 'repair_saved_metadata', 'reason': message}
                safe['next_action'] = message
            elif coordinated_repair_available(project, run_id, data):
                message = ('新版支持协调修订全局与章节问题。恢复后复用已保存的全局设计和全部章节批次，'
                           '执行一次有界协调修订并重新核查完整大纲；通过后继续正文，会产生模型用量。')
                safe['recovery'] = {'retryable': True, 'action': 'repair_saved_design', 'reason': message}
                safe['next_action'] = message
            elif source_restoration_available(project, run_id, data):
                message = ('新版支持按骨架补全遗漏。恢复后复用已保存章节，执行一次来源补全并完整核查；'
                           '通过后继续正文，会产生模型用量。')
                safe['recovery'] = {'retryable': True, 'action': 'restore_source_omissions', 'reason': message}
                safe['next_action'] = message
            return safe
        return None
    except (OSError, ValueError, TypeError):
        return None


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
