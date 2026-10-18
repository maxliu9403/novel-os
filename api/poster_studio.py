"""Complete-skeleton poster studio; legacy analysis stays readable and immutable."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from . import poster_tasks
from .cover_workbench import project_workflow
from .jobs import runner
from .routes import (get_service, get_cover_service, get_cover_mutation_service,
                     get_media_store, _guard_project_mutation, _cover_out, _cover_error)
from .services import ProjectService
from core import cover_poster_store as plans
from core.cover_models import CoverSet
from core.cover_sources import CoverSourceSelector, capture_source, preview_source
from core.cover_store import CoverConflict

router = APIRouter(prefix="/api/projects/{project_id}/poster", dependencies=[Depends(_guard_project_mutation)])


def enabled():
    return os.environ.get("NOVEL_OS_POSTER_STUDIO", "1").strip().lower() not in {"0", "false", "off"}


def require_enabled():
    if not enabled():
        raise HTTPException(409, "新版封面工作室已关闭；已有图片仍可查看、选择和下载。")


def get_poster_planner():
    """Reuse the native configured text adapter; never change provider or effort."""
    from core.llm_client import LLMClient
    from core.studio_settings import resolve_cover_director_settings
    settings = resolve_cover_director_settings()
    return LLMClient(provider=settings.provider or None, model=settings.model or None,
        base_url=settings.base_url or None, api_key=settings.api_key or None,
        timeout_seconds=settings.timeout_seconds, reasoning_effort=settings.reasoning_effort or None,
        max_tokens=8192 if settings.provider == "deepseek" else 12000)


def get_vision_reviewer():
    """Image-input client for optional poster advice; independent from text routes."""
    from core.llm_client import LLMClient
    from core.provider_settings import resolve_vision_route
    route = resolve_vision_route("cover_review")
    return LLMClient(provider=route["provider"] or None, model=route["model"] or None,
        base_url=route["base_url"] or None, api_key=route["api_key"] or None,
        max_tokens=route["max_tokens"], timeout_seconds=route["timeout_seconds"])


def get_poster_service_factory(store=Depends(get_media_store)):
    # Resolve settings only inside an actual job, after idempotency lookup.
    return lambda: _image_service(get_cover_service(store))


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")


class PlanRequest(Request):
    preferences: dict[str, str] = Field(default_factory=dict, max_length=12)


class PlanEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_hash: str
    directions: list[dict] = Field(min_length=4, max_length=4)


class BatchRequest(Request):
    plan_id: str
    plan_hash: str


class RetryRequest(Request):
    candidate_ids: list[str] | None = Field(default=None, max_length=5)


class RefineRequest(Request):
    prompt: str = Field(min_length=1, max_length=2000)
    keep_composition: bool = True


class TypographyRequest(Request):
    layout: dict


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layout: dict


class SelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    expected_active_revision: int | None = Field(default=None, ge=0)
    expected_sha256: str = ""


def _old_active(flow, project_id):
    return any(str(job.get("kind", "")).startswith("cover.")
               and not str(job.get("kind", "")).startswith("cover.poster.")
               for job in runner.running_for_project(project_id, project_path=str(flow.store.project_path)))


def _idle(flow, project_id):
    require_enabled()
    if _old_active(flow, project_id):
        raise CoverConflict("原封面任务仍在执行，请等待完成；已有图片可以继续选择。")


def _set(flow, project_id, set_id):
    value = flow.store.load(set_id)
    if value.project_id != project_id:
        raise FileNotFoundError("Cover set not found")
    return value


def _candidate(value, candidate_id):
    found = next((item for item in value.candidates if item.candidate_id == candidate_id), None)
    if found is None:
        raise FileNotFoundError("Cover candidate not found")
    return found


def _public_plan(plan, source_sha="", formal_title=""):
    return {**{key: value for key, value in plan.items() if key != "owner_scope"},
            "source_sha256": plan["source_content_sha256"],
            "stale": bool((source_sha and source_sha != plan["source_bundle_sha256"])
                          or (formal_title and formal_title != plan["metadata"]["title"]))}


def _public_set(flow, project_id, value):
    from . import poster_advisory
    out = _cover_out(project_id, value, active_revision=flow.store.active().revision)
    concepts = {item.concept_id: item for item in value.concepts}
    out["pipeline_kind"] = "skeleton_poster" if value.compiler_version.startswith("skeleton-poster") else "legacy"
    out["advisory"] = poster_advisory.read(flow, value.cover_set_id, runner)
    out["brief"] = {"title": value.brief.title, "author": value.brief.author, "genre": value.brief.genre}
    # Poll a compact view. Historical campaign ledgers and full attempt payloads
    # remain available in the old workbench, not retransmitted every few seconds.
    for item in out["concepts"]:
        scene = item.get("scene_plan") or {}
        item["generation_prompt"] = ""
        recipe = scene.get("_poster_recipe") or {}
        item["scene_plan"] = {"pipeline_kind": scene.get("pipeline_kind", "legacy"),
                              "_poster_recipe": {"title_layout": recipe.get("title_layout")}} if recipe else {}
    for candidate in out["candidates"]:
        concept = concepts[candidate["concept_id"]]
        recipe = concept.scene_plan.get("_poster_recipe") or concept.scene_plan.get("_compiled_recipe") or {}
        assets = (candidate.get("quality_report") or {}).get("assets") or {}
        params = candidate.get("safe_request_parameters") or {}
        candidate["poster_metadata"] = {
            "requested_model": params.get("requested_model") or candidate.get("model") or "",
            "observed_model": params.get("observed_model") or params.get("actual_model") or "",
            "typography_available": bool(assets.get("artwork_media_id")),
            "title_layout": recipe.get("title_layout"),
            "parent_candidate_id": (value.repair_lineage or {}).get("parent_candidate_id", ""),
        }
        candidate.pop("attempt_history", None)
        candidate["generation_prompt"] = ""
        candidate["quality_report"] = {key: val for key, val in (candidate.get("quality_report") or {}).items()
                                       if key in {"assets", "status", "blockers", "evidence", "advisory_status", "selection_policy"}}
    return out


@router.get("")
def overview(project_id: str, svc: ProjectService = Depends(get_service)):
    from core.cover_delivery import delivery_status
    flow = project_workflow(svc, project_id)
    detail = svc.project_detail(project_id)
    source = {"bytes": 0, "sha256": "", "preview": "", "path": "outputs/input/prompt.md"}
    bundle = ""
    try:
        preview = preview_source(flow.context, CoverSourceSelector("project_input", project_id=project_id))
        source.update(bytes=len(preview.primary_text.encode()), sha256=preview.source_content_sha256,
                      preview=preview.primary_text, bundle_sha256=preview.source_bundle_sha256,
                      documents=[{key: item.get(key) for key in ("document_id", "sha256", "byte_length")}
                                 for item in preview.documents])
        bundle = preview.source_bundle_sha256
    except (ValueError, OSError) as exc:
        source["error"] = str(exc)
    try:
        saved_sets = flow.store.list()
        active = flow.store.active()
        selected_set = next((item for item in saved_sets if item.cover_set_id == active.cover_set_id), None)
        return {"enabled": enabled(), "title": detail.title, "source": source,
                "plans": [_public_plan(item, bundle, detail.title) for item in plans.list_plans(flow)],
                "sets": [_public_set(flow, project_id, item) for item in reversed(saved_sets)],
                "tasks": poster_tasks.list_tasks(flow, runner), "legacy_active": _old_active(flow, project_id),
                "delivery": delivery_status(flow.store.project_path),
                "active_cover_set_id": active.cover_set_id,
                "active_revision": active.revision,
                "selected_candidate_id": selected_set.selected_candidate_id if selected_set else ""}
    except (ValueError, OSError) as exc:
        raise _cover_error(exc) from exc


@router.post("/plans", status_code=202)
def start_plan(project_id: str, body: PlanRequest, svc: ProjectService = Depends(get_service)):
    from core.cover_poster_planner import plan_poster
    flow = project_workflow(svc, project_id)
    if any(len(value) > 6000 for value in body.preferences.values()):
        raise HTTPException(422, "创作偏好每项最多 6000 字符。")

    def preflight():
        _idle(flow, project_id)
        detail = svc.project_detail(project_id)
        metadata = {"title": detail.title, "author": detail.author, "genre": detail.genre,
                    "language": "", "market_scope": body.preferences.get("market", ""),
                    "target_audience": body.preferences.get("audience", "")}
        snapshot = capture_source(flow.context, CoverSourceSelector("project_input", project_id=project_id))
        return {"snapshot": snapshot, "metadata": metadata}

    def operation(prepared, progress):
        planner = get_poster_planner()
        planner.diagnostic_callback = lambda event: progress({"model_event": event})
        def planning_progress(event):
            progress({**event, "stage": event.get("phase", "planning")})
        value = plan_poster(prepared["snapshot"], prepared["metadata"], body.preferences,
                            planner.complete, model=planner.model, progress=planning_progress,
                            context_window_tokens=int(os.environ["NOVEL_OS_POSTER_CONTEXT_WINDOW_TOKENS"])
                                if os.environ.get("NOVEL_OS_POSTER_CONTEXT_WINDOW_TOKENS") else None,
                            output_token_limit_supported=planner.provider_name not in {"codex", "claude_cli"})
        with flow.store.mutation_lock():
            saved = plans.save_plan(flow, value)
        progress({"completed": 1, "total": 1})
        return {"plan_id": saved["plan_id"]}
    try:
        return poster_tasks.start(flow, runner, kind="plan", request_id=body.request_id,
            inputs={"preferences": body.preferences}, preflight=preflight, operation=operation, project_id=project_id)
    except (ValueError, OSError) as exc:
        raise _cover_error(exc) from exc


@router.patch("/plans/{plan_id}")
def edit_plan(project_id: str, plan_id: str, body: PlanEdit, svc: ProjectService = Depends(get_service)):
    from core.cover_poster_planner import normalize_poster_plan
    flow = project_workflow(svc, project_id)
    try:
        require_enabled()
        with flow.store.mutation_lock():
            old = plans.load_plan(flow, plan_id)
            if old["plan_hash"] != body.expected_hash:
                raise CoverConflict("方案版本已变化，请刷新后再编辑。")
            if plans.list_plans(flow)[0]["plan_id"] != plan_id:
                raise CoverConflict("已有更新的设计方案，请切换到最新版本后再编辑。")
            previous = {item["id"]: item for item in old["directions"]}
            directions = []
            for item in body.directions:
                before = previous.get(item.get("id"))
                item = dict(item)
                if before and item.get("image_prompt") == before.get("image_prompt") and any(
                        item.get(field) != before.get(field)
                        for field in ("story_promise", "composition", "medium", "palette", "characters")):
                    # A local human edit is authoritative. Do not append the
                    # superseded model prose (e.g. 'solo portrait') to a new cast
                    # and composition. No semantic model call is needed here.
                    item["image_prompt"] = ("Follow the current structured story promise, character list, "
                        "composition, medium, palette and reserved title area. Create coherent professional "
                        "cover artwork without text; do not restore a previous composition.")
                directions.append(item)
            edited = normalize_poster_plan({**old, "directions": directions})
            for key in ("plan_hash", "approved_at", "stale"):
                edited.pop(key, None)
            edited["parent_plan_id"] = old["plan_id"]
            result = plans.save_plan(flow, edited)
        return _public_plan(result)
    except (ValueError, OSError) as exc:
        raise _cover_error(exc) from exc


def _image_service(service):
    from .poster_image_service import PosterImageService
    return PosterImageService(service)


@router.post("/batches", status_code=202)
def generate(project_id: str, body: BatchRequest, svc: ProjectService = Depends(get_service),
             image_factory=Depends(get_poster_service_factory)):
    from core.cover_poster_planner import poster_storage_objects
    flow = project_workflow(svc, project_id)
    def preflight():
        _idle(flow, project_id)
        plan = plans.load_plan(flow, body.plan_id)
        if plan["metadata"]["title"] != svc.project_detail(project_id).title:
            raise CoverConflict("正式书名已变化，请根据当前书名重新设计方向。")
        brief, concepts = poster_storage_objects(plan)
        plan = plans.approve(flow, body.plan_id, body.plan_hash)
        current = flow.store.create(CoverSet.new(project_id, brief, concepts, compiler_version="skeleton-poster.v1"))
        # Approval is tied to exact plan and source hashes; later edits get a new id.
        flow.store._write_json(flow.store.root / "poster" / "batches" / f"{current.cover_set_id}.json",
            {"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "snapshot_id": plan["snapshot_id"],
             "source_bundle_sha256": plan["source_bundle_sha256"], "request_id": body.request_id})
        return {"result": {"cover_set_id": current.cover_set_id}}
    def operation(prepared, progress):
        service = image_factory()
        result = service.generate(project_id, flow.store.project_path, prepared["result"]["cover_set_id"], progress=progress)
        outcome = _image_result(result)
        _schedule_advice(flow, result, service)
        return outcome
    try:
        return poster_tasks.start(flow, runner, kind="generate", request_id=body.request_id,
            inputs={"plan_id": body.plan_id, "plan_hash": body.plan_hash}, preflight=preflight,
            operation=operation, project_id=project_id)
    except (ValueError, OSError) as exc:
        raise _cover_error(exc) from exc


def _image_result(value):
    ready = sum(item.status in {"ready", "selected", "rejected"} for item in value.candidates)
    lineage = value.repair_lineage or {}
    target_ids = lineage.get("target_concept_ids") or ([lineage["target_concept_id"]] if lineage.get("target_concept_id") else [])
    targets = [item for item in value.candidates if not target_ids or item.concept_id in target_ids]
    if not any(item.status in {"ready", "selected", "rejected"} for item in targets):
        raise ValueError("本次未生成完整图片。失败记录已保存，可以手动重试。")
    return {"cover_set_id": value.cover_set_id, "ready": ready, "total": len(value.candidates)}


def _schedule_advice(flow, current, images):
    from . import poster_advisory
    if os.environ.get("NOVEL_OS_POSTER_ADVISORY", "1").lower() in {"0", "off", "false"}:
        return
    try:
        from core.usage_tracking import usage_activity
        with usage_activity("advisory"):
            poster_advisory.schedule(flow, runner, current, client_factory=get_vision_reviewer,
                                    image_service=images.service)
    except (ValueError, OSError):
        # Optional advice cannot undo independently persisted image output.
        pass


def _start_image_operation(project_id, set_id, candidate_id, body, kind, svc, service=None, image_factory=None):
    flow = project_workflow(svc, project_id)
    inputs = {"cover_set_id": set_id, **body.model_dump(exclude={"request_id"})}
    if candidate_id:
        inputs["candidate_id"] = candidate_id
    def preflight():
        _idle(flow, project_id)
        parent = _set(flow, project_id, set_id)
        if candidate_id:
            candidate = _candidate(parent, candidate_id)
            if candidate.status not in {"ready", "selected", "rejected"}:
                raise CoverConflict("请先恢复完整图片，再进行精修或排版。")
        return {}
    def operation(_prepared, progress):
        images = _image_service(service) if service else image_factory()
        if kind == "refine":
            result = images.refine(project_id, flow.store.project_path, set_id, candidate_id,
                                   body.prompt, body.keep_composition, progress=progress)
        elif kind == "typography":
            result = images.typography(project_id, flow.store.project_path, set_id, candidate_id,
                                       body.layout, progress=progress)
        else:
            result = images.retry(project_id, flow.store.project_path, set_id, body.candidate_ids, progress=progress)
        outcome = _image_result(result)
        if kind == "retry" and len((result.repair_lineage or {}).get("target_concept_ids", [])) == result.requested_count:
            _schedule_advice(flow, result, images)
        return outcome
    try:
        return poster_tasks.start(flow, runner, kind=kind, request_id=body.request_id, inputs=inputs,
            preflight=preflight, operation=operation, project_id=project_id)
    except (ValueError, OSError) as exc:
        raise _cover_error(exc) from exc


@router.post("/sets/{set_id}/retry", status_code=202)
def retry(project_id: str, set_id: str, body: RetryRequest, svc: ProjectService = Depends(get_service),
          image_factory=Depends(get_poster_service_factory)):
    return _start_image_operation(project_id, set_id, "", body, "retry", svc, image_factory=image_factory)


@router.post("/sets/{set_id}/candidates/{candidate_id}/refine", status_code=202)
def refine(project_id: str, set_id: str, candidate_id: str, body: RefineRequest, svc: ProjectService = Depends(get_service),
           image_factory=Depends(get_poster_service_factory)):
    return _start_image_operation(project_id, set_id, candidate_id, body, "refine", svc, image_factory=image_factory)


@router.post("/sets/{set_id}/candidates/{candidate_id}/typography", status_code=202)
def typography(project_id: str, set_id: str, candidate_id: str, body: TypographyRequest,
               svc: ProjectService = Depends(get_service), service=Depends(get_cover_mutation_service)):
    return _start_image_operation(project_id, set_id, candidate_id, body, "typography", svc, service)


@router.post("/sets/{set_id}/candidates/{candidate_id}/typography-preview")
def typography_preview(project_id: str, set_id: str, candidate_id: str, body: PreviewRequest,
                       svc: ProjectService = Depends(get_service), service=Depends(get_cover_mutation_service)):
    flow = project_workflow(svc, project_id)
    try:
        _set(flow, project_id, set_id)
        image = _image_service(service).preview_typography(project_id, flow.store.project_path, set_id, candidate_id, body.layout)
        return Response(image, media_type="image/png", headers={"Cache-Control": "no-store"})
    except (ValueError, OSError) as exc:
        raise _cover_error(exc) from exc


@router.post("/sets/{set_id}/candidates/{candidate_id}/select")
def select(project_id: str, set_id: str, candidate_id: str, body: SelectRequest,
           svc: ProjectService = Depends(get_service), service=Depends(get_cover_mutation_service)):
    from core.delivery_package import delivery_lock
    flow = project_workflow(svc, project_id)
    try:
        with delivery_lock(flow.store.project_path), flow.store.mutation_lock():
            current = _set(flow, project_id, set_id)
            candidate = _candidate(current, candidate_id)
            if body.expected_sha256 and candidate.sha256 != body.expected_sha256:
                raise CoverConflict("图片版本已变化，请重新查看后选择。")
            if current.revision != body.expected_revision and not body.expected_sha256:
                raise CoverConflict("候选状态已变化，请刷新后重新选择。")
            selected = service.select_candidate(flow.store.project_path, set_id, candidate_id,
                expected_revision=current.revision,
                expected_active_revision=body.expected_active_revision if body.expected_active_revision is not None else flow.store.active().revision,
                confirm_stale=True)
        return _public_set(flow, project_id, selected)
    except (ValueError, OSError) as exc:
        raise _cover_error(exc) from exc
