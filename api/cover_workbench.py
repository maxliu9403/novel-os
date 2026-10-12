"""Owner-scoped skeleton cover adapters; no live novel execution is involved."""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from . import tenancy, db
from .jobs import runner
from .services import ProjectService
from .routes import (get_service, get_cover_art_director, get_cover_service,
                     get_cover_mutation_service, get_cover_review_service, get_media_store, _guard_project_mutation,
                     _cover_project, _cover_error, _direction_out, _cover_out)
from .models import CoverDirectionCreate, CoverDirectionApproval, CoverGenerateRequest, CoverCandidateMutation
from core.cover_store import CoverStore, CoverConflict
from core.cover_workflow import CoverWorkflow

router = APIRouter(prefix="/api", dependencies=[Depends(_guard_project_mutation)])
_ANALYSIS_DISPATCH_LOCK = threading.Lock()
_ANALYSIS_DISPATCHES: dict[str, str] = {}
_ANALYSIS_JOBS: dict[tuple[str, str, str], str] = {}


def project_workflow(svc: ProjectService, project_id: str) -> CoverWorkflow:
    from core.cover_sources import CoverSourceContext
    from .workshop import WorkshopService
    project = _cover_project(svc, project_id)
    workshop = WorkshopService(svc)
    workspace_id = svc.workspace.id if svc.workspace else tenancy.DEFAULT_WORKSPACE_ID
    store = CoverStore(project)
    context = CoverSourceContext(cover_root=store.root,
        owner_scope=f"{workspace_id}:project:{project_id}",
        project_resolver=svc.project_path, workshop_resolver=workshop._path,
        workshop_guard=workshop._locked)
    return CoverWorkflow(context, store)


def workshop_workflow(svc: ProjectService, session_id: str) -> CoverWorkflow:
    from core.cover_sources import CoverSourceContext
    from .workshop import WorkshopService
    workshop = WorkshopService(svc)
    with workshop._locked(session_id):
        path = workshop._path(session_id)
    workspace_id = svc.workspace.id if svc.workspace else tenancy.DEFAULT_WORKSPACE_ID
    store = CoverStore(path, cover_root=path / "covers")
    context = CoverSourceContext(cover_root=store.root,
        owner_scope=f"{workspace_id}:workshop:{session_id}",
        project_resolver=svc.project_path, workshop_resolver=workshop._path,
        workshop_guard=workshop._locked)
    return CoverWorkflow(context, store)


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_selector: dict | None = None
    snapshot_id: str = ""
    analysis_id: str = ""
    force: bool = False


def _selector(value, *, project_id="", session_id=""):
    from core.cover_sources import CoverSourceSelector
    default = ({"source_type": "project_input", "project_id": project_id} if project_id else
               {"source_type": "workshop_current", "session_id": session_id})
    payload = dict(value or default)
    # A workshop may only analyze its own discussion. A project can explicitly
    # select a discussion in its workspace through the scoped resolver.
    if session_id and payload.get("session_id") != session_id:
        raise HTTPException(400, "Source does not belong to this discussion")
    if payload.get("source_type") == "project_input" and payload.get("project_id") != project_id:
        raise HTTPException(400, "Source does not belong to this project")
    try:
        return CoverSourceSelector(**payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _preview(workflow, selector):
    from core.cover_sources import preview_source
    try:
        result = preview_source(workflow.context, selector).to_dict()
        result["analyses"] = _list_analyses(workflow)
        return result
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


def _list_analyses(workflow):
    from core.cover_analysis import load_analysis
    root = workflow.store.root / "analyses"
    result = []
    paths = [path for path in root.glob("analysis-*/analysis.json")
             if not path.is_symlink() and not path.parent.is_symlink()]
    for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            if path.is_symlink() or path.parent.is_symlink():
                continue
            payload = load_analysis(workflow.context, path.parent.name).to_dict()
            result.append({**{key: payload.get(key) for key in
                           ("analysis_id", "snapshot_id", "coverage_status", "created_at", "updated_at", "phase", "coverage", "last_error")},
                           "model": payload.get("configuration", {}).get("model", ""),
                           **_analysis_runtime(workflow, payload)})
        except (ValueError, OSError):
            continue
    return result


def _analysis_runtime(workflow, payload):
    """Project process-local execution onto immutable/durable analysis data.

    Persisted pending/partial state alone is not evidence of a living worker.
    Conversely, a resumed job can be running before it clears an old failure.
    No runtime field is saved or included in the completed analysis digest.
    """
    from core.cover_sources import load_snapshot

    snapshot = load_snapshot(workflow.context, payload["snapshot_id"])
    analysis_id = payload["analysis_id"]
    key = (str(workflow.context.cover_root.resolve()), workflow.context.owner_scope, analysis_id)
    with _ANALYSIS_DISPATCH_LOCK:
        indexed_job_id = _ANALYSIS_JOBS.get(key, "")
        job = runner.get(indexed_job_id) if indexed_job_id else None
    if job:
        meta = job.get("meta") or {}
        job_analysis = job.get("analysis_id") or meta.get("analysis_id")
        job_snapshot = job.get("snapshot_id") or meta.get("snapshot_id")
        if (job_analysis != analysis_id or job.get("kind") not in (None, "cover.analyze")
                or (job_snapshot and job_snapshot != snapshot.snapshot_id)
                or job.get("status") not in {"running", "done", "error"}):
            job = None
    job_id = str(job.get("job_id") or indexed_job_id) if job else None
    job_status = job.get("status") if job else None
    job_error = str(job["error"]) if job and job.get("error") else None
    last_error = payload.get("last_error") or {}
    if job_status == "running":
        execution_status = "running"
    elif payload.get("coverage_status") == "complete":
        execution_status = "complete"
    elif (payload.get("phase") == "cancelled" or last_error.get("code") == "analysis_cancelled"
          # Deferred cancellation becomes terminal only after the worker exits.
          or (job_status == "error" and job_error == "任务已暂停。")):
        execution_status = "paused"
    elif (job_status == "error" or payload.get("phase") == "failed"
          or payload.get("coverage_status") == "failed"):
        execution_status = "failed"
    else:
        execution_status = "interrupted"
    return {"job_id": job_id, "job_status": job_status, "job_error": job_error,
            "cancel_requested": bool(job and job.get("cancel_requested")),
            "execution_status": execution_status,
            "source_selector": snapshot.selector.to_dict(),
            "source_bundle_sha256": snapshot.source_bundle_sha256}


@router.get("/projects/{project_id}/covers/source")
def project_source(project_id: str, source_type: str = "project_input", session_id: str = "",
                   archive_revision: int | None = None, svc: ProjectService = Depends(get_service)):
    payload = {"source_type": source_type, "archive_revision": archive_revision,
               "project_id": project_id if source_type == "project_input" else "", "session_id": session_id}
    return _preview(project_workflow(svc, project_id), _selector(payload, project_id=project_id))


@router.get("/workshop/sessions/{session_id}/covers/source")
def workshop_source(session_id: str, source_type: str = "workshop_current",
                    archive_revision: int | None = None, svc: ProjectService = Depends(get_service)):
    return _preview(workshop_workflow(svc, session_id), _selector(
        {"source_type": source_type, "session_id": session_id, "archive_revision": archive_revision}, session_id=session_id))


def _start_analysis(workflow, body, director, *, project_id="", session_id=""):
    from core.cover_sources import capture_source, load_snapshot
    from core.cover_analysis import CoverStoryAnalyzer, reserve_analysis, load_analysis, record_dispatch_failure
    from core.cover_workflow import digest
    try:
        if sum(bool(x) for x in (body.snapshot_id, body.source_selector, body.analysis_id)) > 1:
            raise ValueError("Choose an existing analysis, snapshot or source selector, not multiple sources")
        if body.analysis_id and body.force:
            raise ValueError("Forced reanalysis creates a new identity; it cannot resume an existing analysis")
        previous = load_analysis(workflow.context, body.analysis_id) if body.analysis_id else None
        snapshot = (load_snapshot(workflow.context, previous.snapshot_id) if previous else
                    load_snapshot(workflow.context, body.snapshot_id) if body.snapshot_id else
                    capture_source(workflow.context, _selector(body.source_selector,
                                                               project_id=project_id, session_id=session_id)))
        analyzer = CoverStoryAnalyzer(complete=director._complete, model=director.model,
                                      **getattr(director, "analysis_configuration", {}))
        dispatch_key = digest([str(workflow.context.cover_root.resolve()), workflow.context.owner_scope,
                               snapshot.source_bundle_sha256, analyzer.configuration, body.analysis_id])
        # Coalesce before allocating the durable ID. JobRunner.submit's own
        # coalescing occurs after caller-side allocation and would leave an
        # orphan pending analysis that no worker can ever finish.
        with _ANALYSIS_DISPATCH_LOCK:
            running = runner.get(_ANALYSIS_DISPATCHES.get(dispatch_key, "")) if not body.force else None
            if running and running.get("status") == "running":
                return running
            analysis_id = previous.analysis_id if previous else reserve_analysis(workflow.context, snapshot)
            cancel_event = threading.Event()
            try:
                job_id = runner.submit("cover.analyze", lambda: analyzer.analyze(
                    workflow.context, snapshot, force=body.force, analysis_id=analysis_id,
                    cancel_event=cancel_event),
                    meta={"project_id": project_id, "project_path": str(workflow.store.project_path) if project_id else "",
                          "session_id": session_id, "analysis_id": analysis_id, "snapshot_id": snapshot.snapshot_id},
                    unique_key=f"cover-analysis:{analysis_id}",
                    cancel_event=cancel_event,
                    defer_cancel_completion=True,
                    result_mapper=lambda result: {"analysis_id": result.analysis_id, "coverage_status": result.to_dict().get("coverage_status")})
            except Exception as exc:
                record_dispatch_failure(workflow.context, analysis_id, exc)
                raise
            if not body.force:
                _ANALYSIS_DISPATCHES[dispatch_key] = job_id
            _ANALYSIS_JOBS[(str(workflow.context.cover_root.resolve()), workflow.context.owner_scope, analysis_id)] = job_id
            return runner.get(job_id)
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


@router.post("/projects/{project_id}/covers/analyses", status_code=202)
def analyze_project(project_id: str, body: AnalysisRequest, svc: ProjectService = Depends(get_service),
                    director=Depends(get_cover_art_director)):
    return _start_analysis(project_workflow(svc, project_id), body, director, project_id=project_id)


@router.post("/workshop/sessions/{session_id}/covers/analyses", status_code=202)
def analyze_workshop(session_id: str, body: AnalysisRequest, svc: ProjectService = Depends(get_service),
                     director=Depends(get_cover_art_director)):
    return _start_analysis(workshop_workflow(svc, session_id), body, director, session_id=session_id)


def _analysis(workflow, analysis_id):
    from core.cover_analysis import load_analysis
    try:
        payload = load_analysis(workflow.context, analysis_id).to_dict()
        return {**payload, **_analysis_runtime(workflow, payload)}
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


@router.get("/projects/{project_id}/covers/analyses/{analysis_id}")
def project_analysis(project_id: str, analysis_id: str, svc: ProjectService = Depends(get_service)):
    return _analysis(project_workflow(svc, project_id), analysis_id)


@router.get("/workshop/sessions/{session_id}/covers/analyses/{analysis_id}")
def workshop_analysis(session_id: str, analysis_id: str, svc: ProjectService = Depends(get_service)):
    return _analysis(workshop_workflow(svc, session_id), analysis_id)


def _cancel_analysis(workflow, analysis_id):
    # Verify the durable owner-scoped record before looking up a process-local
    # job. A restarted server has no running worker to cancel.
    _analysis(workflow, analysis_id)
    key = (str(workflow.context.cover_root.resolve()), workflow.context.owner_scope, analysis_id)
    with _ANALYSIS_DISPATCH_LOCK:
        job_id = _ANALYSIS_JOBS.get(key, "")
        job = runner.get(job_id)
        job_analysis = (job.get("analysis_id") or (job.get("meta") or {}).get("analysis_id")) if job else None
        if not job or job_analysis != analysis_id or not runner.cancel(job_id):
            raise HTTPException(409, "此分析当前没有可暂停的运行任务。")
        return {"analysis_id": analysis_id, "job_id": job_id, "cancel_requested": True}


@router.post("/projects/{project_id}/covers/analyses/{analysis_id}/cancel")
def cancel_project_analysis(project_id: str, analysis_id: str, svc: ProjectService = Depends(get_service)):
    return _cancel_analysis(project_workflow(svc, project_id), analysis_id)


@router.post("/workshop/sessions/{session_id}/covers/analyses/{analysis_id}/cancel")
def cancel_workshop_analysis(session_id: str, analysis_id: str, svc: ProjectService = Depends(get_service)):
    return _cancel_analysis(workshop_workflow(svc, session_id), analysis_id)


def create_bound_direction(workflow, body, director, *, progress_sink=None):
    if body.brief is not None or body.direction is not None or body.source_prompt_sha256 or body.foundation_sha256:
        raise HTTPException(400, "分析来源与手工方向输入不能混用。")
    try:
        direction = workflow.plan(body.analysis_id, director, count=body.count,
                                  freshness_policy=body.freshness_policy, progress_sink=progress_sink)
        return _direction_out(direction, brief=workflow.store.load_direction_brief(direction.direction_id))
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


def _direction_preflight(workflow, body):
    """Reject invalid/stale inputs before paying for a background model call."""
    from core.cover_analysis import load_analysis, derive_analysis_context
    from core.cover_analysis_reviews import assert_analysis_usable
    from core.cover_sources import load_snapshot, check_source_head
    from core.cover_handoff import resolve_cover_brief_v2

    if not 3 <= body.count <= 5:
        raise ValueError("Cover direction count must be between 3 and 5")
    if not body.analysis_id:
        if (body.brief is None) != (body.direction is None):
            raise ValueError("Cover brief and structured direction must be supplied together")
        if body.brief is None:
            brief = resolve_cover_brief_v2(workflow.store.project_path)
            pending = brief.pending_critical_assumptions()
            if pending:
                raise CoverConflict("Cover story facts need confirmation before art direction: "
                                    + ", ".join(item.field for item in pending))
        return
    if body.brief is not None or body.direction is not None or body.source_prompt_sha256 or body.foundation_sha256:
        raise ValueError("分析来源与手工方向输入不能混用。")
    if body.freshness_policy not in {"require_current_head", "pinned_snapshot"}:
        raise ValueError("Invalid source freshness policy")
    analysis = load_analysis(workflow.context, body.analysis_id)
    payload = analysis.to_dict()
    assert_analysis_usable(workflow.context, body.analysis_id, payload)
    if payload.get("coverage_status") != "complete":
        raise CoverConflict("全文分析尚未完成，不能创建封面方向。")
    brief, _ = derive_analysis_context(workflow.context, analysis)
    if brief.blocking_pending_critical_assumptions():
        raise CoverConflict("骨架仍有关键故事事实待确认，请先修订来源并重新分析。")
    snapshot = load_snapshot(workflow.context, payload["snapshot_id"])
    if body.freshness_policy == "require_current_head":
        check_source_head(workflow.context, snapshot)


def _start_direction_task(workflow, body, director, svc, *, project_id="", session_id=""):
    from . import cover_direction_tasks

    if session_id and not body.analysis_id:
        raise HTTPException(409, "请先完成骨架全文分析。")

    def operation():
        if body.analysis_id:
            result = create_bound_direction(workflow, body, director,
                                            progress_sink=cover_direction_tasks.current_progress_sink())
            # A source/review change during the long model request cannot
            # silently produce a usable direction for the former skeleton.
            workflow.validate(result["direction_id"], require_latest=False)
            return result
        from .routes import create_cover_direction
        return create_cover_direction(project_id, body, svc, director)

    try:
        return cover_direction_tasks.start(workflow, runner, body, operation,
            preflight=lambda: _direction_preflight(workflow, body),
            project_id=project_id, session_id=session_id)
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


def _direction_task(workflow, task_id=""):
    from . import cover_direction_tasks
    try:
        return (cover_direction_tasks.get(workflow, task_id, runner) if task_id
                else cover_direction_tasks.latest(workflow, runner))
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


@router.post("/projects/{project_id}/covers/direction-tasks", status_code=202)
def start_project_direction_task(project_id: str, body: CoverDirectionCreate,
                                 svc: ProjectService = Depends(get_service),
                                 director=Depends(get_cover_art_director)):
    return _start_direction_task(project_workflow(svc, project_id), body, director, svc, project_id=project_id)


@router.get("/projects/{project_id}/covers/direction-tasks/latest")
def latest_project_direction_task(project_id: str, svc: ProjectService = Depends(get_service)):
    return _direction_task(project_workflow(svc, project_id))


@router.get("/projects/{project_id}/covers/direction-tasks/{task_id}")
def get_project_direction_task(project_id: str, task_id: str, svc: ProjectService = Depends(get_service)):
    return _direction_task(project_workflow(svc, project_id), task_id)


@router.post("/workshop/sessions/{session_id}/covers/direction-tasks", status_code=202)
def start_workshop_direction_task(session_id: str, body: CoverDirectionCreate,
                                  svc: ProjectService = Depends(get_service),
                                  director=Depends(get_cover_art_director)):
    return _start_direction_task(workshop_workflow(svc, session_id), body, director, svc, session_id=session_id)


@router.get("/workshop/sessions/{session_id}/covers/direction-tasks/latest")
def latest_workshop_direction_task(session_id: str, svc: ProjectService = Depends(get_service)):
    return _direction_task(workshop_workflow(svc, session_id))


@router.get("/workshop/sessions/{session_id}/covers/direction-tasks/{task_id}")
def get_workshop_direction_task(session_id: str, task_id: str, svc: ProjectService = Depends(get_service)):
    return _direction_task(workshop_workflow(svc, session_id), task_id)


def approve_bound_direction(workflow, direction_id, body):
    try:
        direction = workflow.approve(direction_id, **body.model_dump())
        return _direction_out(direction, brief=workflow.store.load_direction_brief(direction_id))
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


def generate_bound_covers(workflow, body, covers, *, project_id="", session_id=""):
    from . import cover_generation_tasks
    if body.brief is not None or body.concepts is not None:
        raise HTTPException(400, "Bound directions cannot be combined with replacement facts or concepts")
    try:
        count = len(workflow.store.load_direction(body.direction_id).plans)
        def generate():
            brief, concepts, kwargs = workflow.generation_arguments(body.direction_id, body.approved_direction_sha256)
            if session_id:
                kwargs["cover_root"] = workflow.store.root
            return covers.generate(project_id or f"workshop-{session_id}", workflow.store.project_path,
                                   brief, concepts, **kwargs)
        # An active owner task is returned before checking an already-reserved
        # approval. The worker rechecks source/approval before reserving budget.
        task = cover_generation_tasks.start(workflow, runner, operation=generate,
            preflight=lambda: workflow.generation_arguments(
                body.direction_id, body.approved_direction_sha256, reserve=False),
            count=count, direction_id=body.direction_id, project_id=project_id, session_id=session_id)
        return cover_generation_tasks.as_job(task, runner)
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


def _generation_task(workflow, task_id=""):
    from . import cover_generation_tasks
    try:
        return (cover_generation_tasks.get(workflow, task_id, runner) if task_id
                else cover_generation_tasks.latest(workflow, runner))
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


@router.get("/projects/{project_id}/covers/generation-tasks/latest")
def latest_project_generation_task(project_id: str, svc: ProjectService = Depends(get_service)):
    return _generation_task(project_workflow(svc, project_id))


@router.get("/projects/{project_id}/covers/generation-tasks/{task_id}")
def get_project_generation_task(project_id: str, task_id: str, svc: ProjectService = Depends(get_service)):
    return _generation_task(project_workflow(svc, project_id), task_id)


@router.get("/workshop/sessions/{session_id}/covers/generation-tasks/latest")
def latest_workshop_generation_task(session_id: str, svc: ProjectService = Depends(get_service)):
    return _generation_task(workshop_workflow(svc, session_id))


@router.get("/workshop/sessions/{session_id}/covers/generation-tasks/{task_id}")
def get_workshop_generation_task(session_id: str, task_id: str, svc: ProjectService = Depends(get_service)):
    return _generation_task(workshop_workflow(svc, session_id), task_id)


@router.get("/workshop/sessions/{session_id}/covers/directions")
def workshop_directions(session_id: str, svc: ProjectService = Depends(get_service)):
    flow = workshop_workflow(svc, session_id)
    return [_direction_out(item, brief=flow.store.load_direction_brief(item.direction_id))
            for item in reversed(flow.store.list_directions())]


@router.post("/workshop/sessions/{session_id}/covers/directions")
def plan_workshop(session_id: str, body: CoverDirectionCreate, svc: ProjectService = Depends(get_service),
                  director=Depends(get_cover_art_director)):
    if not body.analysis_id:
        raise HTTPException(409, "请先完成骨架全文分析。")
    return create_bound_direction(workshop_workflow(svc, session_id), body, director)


@router.post("/workshop/sessions/{session_id}/covers/directions/{direction_id}/approve")
def approve_workshop(session_id: str, direction_id: str, body: CoverDirectionApproval,
                     svc: ProjectService = Depends(get_service)):
    return approve_bound_direction(workshop_workflow(svc, session_id), direction_id, body)


@router.post("/workshop/sessions/{session_id}/covers/generate", status_code=202)
def generate_workshop(session_id: str, body: CoverGenerateRequest, svc: ProjectService = Depends(get_service),
                      covers=Depends(get_cover_service)):
    return generate_bound_covers(workshop_workflow(svc, session_id), body, covers, session_id=session_id)


def _workshop_set_out(session_id, cover_set, active_revision=0):
    payload = _cover_out(f"workshop-{session_id}", cover_set, active_revision=active_revision)
    prefix = f"/api/projects/workshop-{session_id}/media/"
    def workshop_urls(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"url", "artwork_url"} and isinstance(item, str) and item.startswith(prefix) and item.endswith("/raw"):
                    value[key] = f"/api/workshop/sessions/{session_id}/covers/media/{item[len(prefix):-4]}"
                else:
                    workshop_urls(item)
        elif isinstance(value, list):
            for item in value:
                workshop_urls(item)
    workshop_urls(payload)
    for item in payload["candidates"]:
        if item.get("media_id"):
            item["url"] = f"/api/workshop/sessions/{session_id}/covers/media/{item['media_id']}"
    return payload


@router.get("/workshop/sessions/{session_id}/covers")
def workshop_covers(session_id: str, svc: ProjectService = Depends(get_service)):
    store = workshop_workflow(svc, session_id).store
    return [_workshop_set_out(session_id, item, store.active().revision) for item in reversed(store.list())]


@router.get("/workshop/sessions/{session_id}/covers/media/{media_id}")
def workshop_media(session_id: str, media_id: str, svc: ProjectService = Depends(get_service),
                   media=Depends(get_media_store)):
    flow = workshop_workflow(svc, session_id)
    # Only media referenced by this owner are accessible, including attempts.
    records = [candidate for item in flow.store.list() for candidate in item.candidates]
    def artwork_id(report):
        return (report or {}).get("assets", {}).get("artwork_media_id")
    allowed = any(c.media_id == media_id or artwork_id(c.quality_report) == media_id or
                  any(a.get("media_id") == media_id or artwork_id(a.get("quality_report")) == media_id
                      for a in c.attempt_history) for c in records)
    record = db.media_get(f"workshop-{session_id}", media_id) if allowed else None
    if not record:
        raise HTTPException(404, "Cover media not found")
    data = media.read(f"workshop-{session_id}", record.sha, record.ext)
    if not data:
        raise HTTPException(404, "Cover image not found")
    return Response(data, media_type=record.content_type)

class CoverRepairStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1)
    refinement_prompt: str | None = Field(default=None, strict=True, min_length=1, max_length=2000)


class CoverRepairApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_image_calls: int = Field(strict=True, ge=0, le=1)


def _start_repair(flow, cover_set_id, candidate_id, body, director, *, project_id="", session_id=""):
    from . import cover_repair_tasks
    try:
        return cover_repair_tasks.start(flow, runner, director, cover_set_id=cover_set_id,
            candidate_id=candidate_id, expected_revision=body.expected_revision,
            refinement_prompt=body.refinement_prompt,
            project_id=project_id, session_id=session_id)
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


def _repair_task(flow, task_id=""):
    from . import cover_repair_tasks
    try:
        return cover_repair_tasks.get(flow, task_id, runner) if task_id else cover_repair_tasks.latest(flow, runner)
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


def _approve_repair(flow, task_id, body, covers, *, project_id="", session_id=""):
    from . import cover_repair_tasks
    try:
        return cover_repair_tasks.approve(flow, runner, covers, task_id,
            **body.model_dump(), project_id=project_id, session_id=session_id,
            image_service_factory=lambda: get_cover_service(covers.media_store))
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


def _retry_repair(flow, task_id, *, project_id="", session_id=""):
    from . import cover_repair_tasks
    try:
        return cover_repair_tasks.retry(flow, runner, task_id, project_id=project_id, session_id=session_id)
    except cover_repair_tasks.RefinementRetryConflict as exc:
        raise HTTPException(409, detail=exc.detail) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


def _repair_for_candidate(flow, cover_set_id, candidate_id):
    from . import cover_repair_tasks
    try:
        return cover_repair_tasks.for_candidate(flow, runner, cover_set_id, candidate_id)
    except (ValueError, FileNotFoundError) as exc:
        raise _cover_error(exc) from exc


@router.post("/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/repair-tasks", status_code=202)
def start_project_repair(project_id: str, cover_set_id: str, candidate_id: str, body: CoverRepairStart,
                         svc: ProjectService = Depends(get_service), director=Depends(get_cover_art_director)):
    return _start_repair(project_workflow(svc, project_id), cover_set_id, candidate_id, body, director, project_id=project_id)


@router.post("/workshop/sessions/{session_id}/covers/{cover_set_id}/candidates/{candidate_id}/repair-tasks", status_code=202)
def start_workshop_repair(session_id: str, cover_set_id: str, candidate_id: str, body: CoverRepairStart,
                          svc: ProjectService = Depends(get_service), director=Depends(get_cover_art_director)):
    return _start_repair(workshop_workflow(svc, session_id), cover_set_id, candidate_id, body, director, session_id=session_id)


@router.get("/projects/{project_id}/covers/repair-tasks/latest")
def latest_project_repair(project_id: str, svc: ProjectService = Depends(get_service)):
    return _repair_task(project_workflow(svc, project_id))


@router.get("/workshop/sessions/{session_id}/covers/repair-tasks/latest")
def latest_workshop_repair(session_id: str, svc: ProjectService = Depends(get_service)):
    return _repair_task(workshop_workflow(svc, session_id))


@router.get("/projects/{project_id}/covers/repair-tasks/for-candidate")
def project_repair_for_candidate(project_id: str, cover_set_id: str, candidate_id: str,
                                 svc: ProjectService = Depends(get_service)):
    return _repair_for_candidate(project_workflow(svc, project_id), cover_set_id, candidate_id)


@router.get("/workshop/sessions/{session_id}/covers/repair-tasks/for-candidate")
def workshop_repair_for_candidate(session_id: str, cover_set_id: str, candidate_id: str,
                                  svc: ProjectService = Depends(get_service)):
    return _repair_for_candidate(workshop_workflow(svc, session_id), cover_set_id, candidate_id)


@router.get("/projects/{project_id}/covers/repair-tasks/{task_id}")
def get_project_repair(project_id: str, task_id: str, svc: ProjectService = Depends(get_service)):
    return _repair_task(project_workflow(svc, project_id), task_id)


@router.get("/workshop/sessions/{session_id}/covers/repair-tasks/{task_id}")
def get_workshop_repair(session_id: str, task_id: str, svc: ProjectService = Depends(get_service)):
    return _repair_task(workshop_workflow(svc, session_id), task_id)


@router.post("/projects/{project_id}/covers/repair-tasks/{task_id}/retry")
def retry_project_repair(project_id: str, task_id: str, svc: ProjectService = Depends(get_service)):
    return _retry_repair(project_workflow(svc, project_id), task_id, project_id=project_id)


@router.post("/workshop/sessions/{session_id}/covers/repair-tasks/{task_id}/retry")
def retry_workshop_repair(session_id: str, task_id: str, svc: ProjectService = Depends(get_service)):
    return _retry_repair(workshop_workflow(svc, session_id), task_id, session_id=session_id)


@router.post("/projects/{project_id}/covers/repair-tasks/{task_id}/approve", status_code=202)
def approve_project_repair(project_id: str, task_id: str, body: CoverRepairApproval,
                           svc: ProjectService = Depends(get_service), covers=Depends(get_cover_review_service)):
    return _approve_repair(project_workflow(svc, project_id), task_id, body, covers, project_id=project_id)


@router.post("/workshop/sessions/{session_id}/covers/repair-tasks/{task_id}/approve", status_code=202)
def approve_workshop_repair(session_id: str, task_id: str, body: CoverRepairApproval,
                            svc: ProjectService = Depends(get_service), covers=Depends(get_cover_review_service)):
    return _approve_repair(workshop_workflow(svc, session_id), task_id, body, covers, session_id=session_id)
