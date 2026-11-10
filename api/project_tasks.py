"""Project-scoped inspection and native checkpoint resume for full-book tasks."""

from __future__ import annotations
import threading
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, StrictInt

from .jobs import ProjectJobBlocked, runner
from .project_task_summary import latest_task_manifest, parse_task_time, read_task_manifest, task_runtime, task_sidecar
from .services import ProjectNotFound, ProjectService


class FailedTaskStage(BaseModel):
    phase: str
    chapter: int | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    attempt: int = 1
    status: str


class ProjectTaskDetail(BaseModel):
    run_id: str
    status: str
    started_at: str | None = None
    updated_at: str | None = None
    current_phase: str
    current_chapter: int | None = None
    error: str | None = None
    failed_stages: list[FailedTaskStage]
    can_resume: bool
    resume_blocked_reason: str | None = None
    can_upgrade_planning: bool = False
    resume_job_id: str | None = None
    resume_job_status: str | None = None
    resume_job_error: str | None = None
    can_pause: bool = False
    cancel_requested: bool = False
    active_job_id: str | None = None
    progress: dict | None = None
    planning: dict | None = None
    stages: list[FailedTaskStage] = []


class ProjectTaskPause(BaseModel):
    job_id: str


class ProjectTaskResume(BaseModel):
    job_id: str
    status: str = "running"
    run_id: str


class AuthorChoice(BaseModel):
    issue_index: StrictInt = Field(ge=0)
    selected_quote: StrictInt = Field(ge=0, le=1)


class AuthorDecisions(BaseModel):
    request_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    choices: list[AuthorChoice] = Field(min_length=1)


class ProjectTaskResumeOptions(BaseModel):
    author_decisions: AuthorDecisions | None = None
    outline_planning_mode: Literal["staged"] | None = None


def _task(svc: ProjectService, project_id: str, run_id: str):
    try:
        project = svc.project_path(project_id)
        manifest = read_task_manifest(project, run_id)
        from pipeline_models import RunManifest
        RunManifest.from_dict(manifest)
        if any(manifest.get(key) is not None and not isinstance(manifest[key], str)
               for key in ("error", "current_phase")):
            raise ValueError("Invalid task failure fields")
        chapter = manifest.get("current_chapter")
        if chapter is not None and (not isinstance(chapter, int) or isinstance(chapter, bool)):
            raise ValueError("Invalid task chapter")
        for stage in manifest.get("stages", {}).values():
            FailedTaskStage.model_validate(stage)
        return project, manifest
    except (ProjectNotFound, FileNotFoundError) as exc:
        raise HTTPException(404, "Project task not found") from exc
    except (OSError, AttributeError, TypeError, ValueError, KeyError) as exc:
        raise HTTPException(409, "任务记录无法读取，请检查已保存的任务文件。") from exc


def _result_meta(manifest) -> dict:
    # PipelineRunner intentionally returns durable failed/paused manifests. A
    # successful Python return must never turn that outcome into JobRunner.done.
    if manifest.status != "completed":
        raise RuntimeError(manifest.error or f"任务停止，当前状态：{manifest.status}")
    return {"run_id": manifest.run_id, "run_status": manifest.status}


def register_project_task_routes(router, get_service):
    @router.get("/projects/{project_id}/tasks/{run_id}", response_model=ProjectTaskDetail)
    def detail(project_id: str, run_id: str, svc: ProjectService = Depends(get_service)):
        project, manifest = _task(svc, project_id, run_id)
        latest = latest_task_manifest(project)
        planning = task_sidecar(project, run_id, 'planning/progress.json')
        runtime = task_runtime(project, manifest, is_latest=bool(latest and latest["run_id"] == run_id), planning=planning)
        started = parse_task_time(manifest.get("created_at"))
        updated = parse_task_time(manifest.get("updated_at"))
        stages = manifest.get("stages") or {}
        from planning_upgrade import can_upgrade_planning
        progress = task_sidecar(project, run_id, "progress.json")
        if progress and runtime["active_job_id"]:
            active_job = runner.get(runtime["active_job_id"])
            job_started = parse_task_time((active_job or {}).get("started_at"))
            execution_started = parse_task_time(progress.get("started_at"))
            if not job_started or not execution_started or execution_started < job_started:
                # Resume reconciliation happens before a new runtime exists.
                # A previous crashed execution must not look like current work.
                progress = None
        if (progress and manifest.get('current_phase') == 'outline'
                and runtime['status'] in {'failed', 'paused'}):
            # Runtime's generic failure hint must not promise recovery when
            # planning exhausted its allowance or requires a source decision.
            next_action = runtime['resume_blocked_reason']
            if next_action is None and planning:
                next_action = planning.get('next_action')
            if next_action:
                progress['next_action'] = next_action
        failures = [
            FailedTaskStage(
                phase=stage.get("phase") or key, chapter=stage.get("chapter"),
                error=stage.get("error"), started_at=stage.get("started_at") or None,
                finished_at=stage.get("finished_at") or None,
                attempt=stage.get("attempt", 1), status=stage["status"],
            )
            for key, stage in stages.items()
            if isinstance(stage, dict) and stage.get("status") in {"failed", "blocked", "retryable"}
        ] if isinstance(stages, dict) else []
        return ProjectTaskDetail(
            run_id=run_id, started_at=started.isoformat() if started else None,
            updated_at=updated.isoformat() if updated else None,
            current_phase=manifest.get("current_phase") or "", current_chapter=manifest.get("current_chapter"),
            error=manifest.get("error"), failed_stages=failures,
            can_upgrade_planning=bool(runtime["can_resume"] and can_upgrade_planning(project, manifest)),
            stages=[FailedTaskStage.model_validate(stage) for stage in stages.values()],
            progress=progress,
            planning=planning, **runtime,
        )

    @router.post("/projects/{project_id}/tasks/{run_id}/pause", response_model=ProjectTaskDetail, status_code=202)
    def pause(project_id: str, run_id: str, body: ProjectTaskPause, svc: ProjectService = Depends(get_service)):
        project, _manifest = _task(svc, project_id, run_id)
        job = runner.get(body.job_id)
        if not (job and job.get('status') == 'running' and job.get('kind') in {'novel.run', 'novel.resume'}
                and job.get('run_id') == run_id and job.get('project_path') == str(project.resolve())
                and job.get('cancel_supported')):
            raise HTTPException(409, '当前运行不支持此暂停请求，或任务已发生变化。请刷新任务状态。')
        if not runner.cancel(body.job_id):
            raise HTTPException(409, '任务已结束，请刷新任务状态。')
        return detail(project_id, run_id, svc)

    @router.post("/projects/{project_id}/tasks/{run_id}/resume", response_model=ProjectTaskResume, status_code=202)
    def resume(project_id: str, run_id: str, body: ProjectTaskResumeOptions | None = None,
               svc: ProjectService = Depends(get_service)):
        # The parent router holds project_operations.mutation through submit,
        # serializing duplicate clicks with other writes and permanent deletion.
        project, manifest = _task(svc, project_id, run_id)
        active = runner.running_for_project(project_id, project_path=str(project))
        requested_mode = body.outline_planning_mode if body else None
        author_decisions = body.author_decisions if body else None
        answer_digest = None
        if author_decisions:
            from core.outline_planning import content_hash
            answer_digest = content_hash({'request_id': author_decisions.request_id,
                'choices': sorted([item.model_dump() for item in author_decisions.choices],
                                  key=lambda item: item['issue_index'])})
            if requested_mode:
                raise HTTPException(409, '已有分阶段规划的作者选择不能与切换规划方式一起提交。')
        for job in active:
            if job["kind"] == "novel.resume" and job.get("run_id") == run_id:
                if author_decisions and job.get("author_choices_sha256") != answer_digest:
                    raise HTTPException(409, "任务已在运行，不能在运行中更改采用的原文。")
                if requested_mode and job.get("outline_planning_mode") != requested_mode:
                    raise HTTPException(409, "此任务已按原方式恢复，不能在运行中切换规划方式。")
                return ProjectTaskResume(job_id=job["job_id"], run_id=run_id)
        if active:
            raise HTTPException(409, "该作品已有任务运行中，请等待当前任务结束后重试。")
        latest = latest_task_manifest(project)
        is_latest = bool(latest and latest["run_id"] == run_id)
        if author_decisions:
            from core.planning_author_decisions import save_choices
            if (not is_latest or manifest["status"] not in {"failed", "paused"}
                    or manifest["spec"].get("dry_run") or manifest.get("current_phase") != "outline"):
                raise HTTPException(409, '只能为当前作品待恢复的大纲任务确认原文。')
            from pipeline_runner import PipelineError, project_execution_lock
            try:
                with project_execution_lock(project, run_id):
                    latest_now = latest_task_manifest(project)
                    if (not latest_now or latest_now['run_id'] != run_id
                            or latest_now['status'] not in {'failed', 'paused'}
                            or latest_now.get('current_phase') != 'outline'):
                        raise ValueError('任务状态已变化。')
                    save_choices(project, run_id, author_decisions.request_id,
                                 [item.model_dump() for item in author_decisions.choices])
            except (OSError, KeyError, ValueError, TypeError, PipelineError) as exc:
                raise HTTPException(409, "无法保存此次作者选择。请刷新任务后核对原文选项。") from exc
        runtime = task_runtime(project, manifest, is_latest=is_latest)
        if not runtime["can_resume"]:
            raise HTTPException(409, runtime['resume_blocked_reason'])
        from planning_upgrade import can_upgrade_planning
        eligible = can_upgrade_planning(project, manifest)
        if requested_mode and not eligible:
            raise HTTPException(409, "仅可将尚未提交故事基础或章节的旧大纲任务切换为分阶段规划。")
        mode = "staged" if eligible else None

        cancellation = threading.Event()
        def native_resume():
            from pipeline_runner import PipelineRunner
            # The worker verifies eligibility again under the execution lock.
            options = {"outline_planning_mode": mode} if mode else {}
            return PipelineRunner(project, cancellation_event=cancellation).resume(run_id, project, **options)

        try:
            job_id = runner.submit(
                "novel.resume", native_resume,
                meta={"project_id": project_id, "project_path": str(project), "run_id": run_id,
                      "outline_planning_mode": mode or manifest["spec"].get("outline_planning_mode", "legacy"),
                      **({"author_choices_sha256": answer_digest} if answer_digest else {})},
                unique_key=f"novel.resume:{project}:{run_id}", result_mapper=_result_meta,
                cancel_event=cancellation, defer_cancel_completion=True,
            )
        except ProjectJobBlocked as exc:
            raise HTTPException(409, "该作品正在删除或已经删除，无法恢复任务。") from exc
        return ProjectTaskResume(job_id=job_id, run_id=run_id)
