"""Project-scoped inspection and native checkpoint resume for full-book tasks."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from .jobs import ProjectJobBlocked, runner
from .project_task_summary import latest_task_manifest, parse_task_time, read_task_manifest, task_runtime
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
    resume_job_id: str | None = None
    resume_job_status: str | None = None
    resume_job_error: str | None = None


class ProjectTaskResume(BaseModel):
    job_id: str
    status: str = "running"
    run_id: str


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
        runtime = task_runtime(project, manifest, is_latest=bool(latest and latest["run_id"] == run_id))
        started = parse_task_time(manifest.get("created_at"))
        updated = parse_task_time(manifest.get("updated_at"))
        stages = manifest.get("stages") or {}
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
            error=manifest.get("error"), failed_stages=failures, **runtime,
        )

    @router.post("/projects/{project_id}/tasks/{run_id}/resume", response_model=ProjectTaskResume, status_code=202)
    def resume(project_id: str, run_id: str, svc: ProjectService = Depends(get_service)):
        # The parent router holds project_operations.mutation through submit,
        # serializing duplicate clicks with other writes and permanent deletion.
        project, manifest = _task(svc, project_id, run_id)
        active = runner.running_for_project(project_id, project_path=str(project))
        for job in active:
            if job["kind"] == "novel.resume" and job.get("run_id") == run_id:
                return ProjectTaskResume(job_id=job["job_id"], run_id=run_id)
        if active:
            raise HTTPException(409, "该作品已有任务运行中，请等待当前任务结束后重试。")
        latest = latest_task_manifest(project)
        runtime = task_runtime(project, manifest, is_latest=bool(latest and latest["run_id"] == run_id))
        if not runtime["can_resume"]:
            raise HTTPException(409, "仅可恢复当前作品最新的失败或暂停创作任务。")

        def native_resume():
            from pipeline_runner import PipelineRunner
            # Same engine and run ID as deploy.sh novel-resume. No approval
            # override, force retry, replacement run, or checkpoint mutation.
            return PipelineRunner(project).resume(run_id, project)

        try:
            job_id = runner.submit(
                "novel.resume", native_resume,
                meta={"project_id": project_id, "project_path": str(project), "run_id": run_id},
                unique_key=f"novel.resume:{project}:{run_id}", result_mapper=_result_meta,
            )
        except ProjectJobBlocked as exc:
            raise HTTPException(409, "该作品正在删除或已经删除，无法恢复任务。") from exc
        return ProjectTaskResume(job_id=job_id, run_id=run_id)
