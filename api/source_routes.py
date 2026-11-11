"""Workspace-scoped, durable whole-story route design.

This is a separate protocol from SourceStudy: existing analysis requests and
their frozen checkpoints never enter this router. HTTP owns tenancy and job
dispatch; SourceStoryDesign owns durable operation identities and recovery.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import threading
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import Field, StringConstraints

from core.creation_mode import SourceMode
from core.creative_types import StrictModel, TypeRef, active_document, digest
from core.reference_assets import atomic_json

from .creative_types import store_for as knowledge_store
from .jobs import runner
from .routes import get_service
from .services import ProjectService
from .source_library import store_for as source_store

router = APIRouter(prefix="/api/source-books", tags=["source-story-routes"])

Operation = Literal[
    "initial_routes", "revise_routes", "expand_routes", "revisit_source"
]
OperationId = Annotated[
    str, Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
]
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ExactText = Annotated[str, StringConstraints(strip_whitespace=False)]
_DISPATCH_LOCK = threading.RLock()
_COMPLETE = {"ready", "complete", "completed"}


class RequestedAction(StrictModel):
    operation: Operation
    target_route_ids: list[str] = Field(default_factory=list)
    requested_count: int | None = Field(default=None, ge=1)


class RouteRunRequest(StrictModel):
    operation_id: OperationId
    operation: Operation = "initial_routes"
    mode: SourceMode = "source_development"
    author_context: dict = Field(default_factory=dict)
    feedback: ExactText = ""
    base_route_set_sha256: Sha | None = None
    target_route_ids: list[str] = Field(default_factory=list)
    requested_count: int | None = Field(default=None, ge=1)
    requested_actions: list[RequestedAction] = Field(default_factory=list)


class ResumeRequest(StrictModel):
    """Explicit resume always uses the current connection for unfinished work."""


def design_for(projects: ProjectService, identifier: str):
    """Shared by route selection handoff; resolves only the current workspace."""
    from core.source_story_design import SourceStoryDesign

    library = source_store(projects)
    library.get(identifier)
    return SourceStoryDesign(library, identifier)


def catalog_for(projects: ProjectService) -> list[dict]:
    """Freeze enabled directory descriptions, never sliced author guide prose."""
    rows = []
    for record in knowledge_store(projects).records().values():
        if record.get("deleted_at") or record.get("status") != "enabled":
            continue
        active = next(
            (v for v in record["versions"] if v["version"] == record["active_version"]),
            None,
        )
        if active is None:
            continue
        doc = active_document(record)
        publication = doc.get("publication") or {}
        description = (
            publication.get("definition")
            or doc.get("sections", {}).get("核心承诺")
            or doc["name"]
        )
        rows.append({
            "id": record["id"], "kind": record["kind"], "type": record["kind"],
            "name": doc["name"], "description": description,
            "version": active["version"], "sha256": active["sha256"],
            "source_sha256": active["sha256"],
            "audience_channels": doc.get("audience_channels", []),
        })
    return sorted(rows, key=lambda row: row["id"])


def _validate_author_selection(context: dict, catalog: list[dict]) -> None:
    """A creation draft may have a partial selection; explicit refs stay exact."""
    channel = context.get("audience_channel")
    if channel is not None and channel not in {"female", "male"}:
        raise ValueError("读者方向无效。")
    refs = context.get("creative_types")
    if refs is None:
        return
    if not isinstance(refs, list):
        raise ValueError("题材选择必须是版本引用列表。")
    allowed = {row["id"]: row for row in catalog}
    seen: set[str] = set()
    counts = {"genre": 0, "tag": 0}
    for raw in refs:
        ref = TypeRef.model_validate(raw)
        row = allowed.get(ref.id)
        if row is None or (row["version"], row["sha256"]) != (
            ref.version, ref.source_sha256
        ):
            raise ValueError("所选题材已停用或版本已变化，请保留草稿并重新选择。")
        if ref.id in seen:
            raise ValueError("题材选择重复。")
        if channel and channel not in row.get("audience_channels", []):
            raise ValueError("所选题材尚未配置该读者方向，请保留草稿并调整选择。")
        seen.add(ref.id)
        counts[row["kind"]] += 1
    if counts["genre"] > 1 or counts["tag"] > 2:
        raise ValueError("选择一个 Genre 和至多两个 Tags；未选完的创建草稿仍可分析。")


def _job_path(projects: ProjectService, identifier: str, run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("路线运行标识无效。")
    path = source_store(projects).path(identifier) / "story-design" / "api-jobs" / f"{run_id}.json"
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("路线任务路径异常。")
    return path


def _job(projects: ProjectService, identifier: str, run_id: str) -> dict | None:
    path = _job_path(projects, identifier, run_id)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text("utf-8"))
        if record["run_id"] != run_id or not isinstance(record["job_id"], str):
            raise ValueError()
        return runner.get(record["job_id"])
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("路线任务关联记录损坏；原设计结果保持不变。") from None


def public_state(projects: ProjectService, identifier: str, value: dict) -> dict:
    """GET is read-only: worker loss is a projection, not a state migration."""
    result = deepcopy(value)
    job = _job(projects, identifier, value["run_id"])
    active = bool(job and job.get("status") == "running")
    result.update(
        job_id=job.get("job_id") if job else None,
        worker_active=active,
        cancel_requested=bool(job and job.get("cancel_requested")),
        interrupted=not active and value.get("status") in {"queued", "running"},
        checked_at=datetime.now(UTC).isoformat(),
    )
    if job and job.get("status") == "error" and value.get("status") in {"queued", "running"}:
        # The client can fail before core.run starts. Do not leak arbitrary
        # provider exception text (URLs/credentials) or claim it is still live.
        result["error"] = result.get("error") or "路线工作线程已停止；已保存资料和进度，可检查连接后继续。"
    return result


def _model_binding(route: dict) -> dict:
    return {
        **{key: route.get(key) for key in (
            "provider", "model", "reasoning_effort", "max_tokens", "timeout_seconds",
            "connection_id", "planning_input_bytes",
        )},
        "endpoint_sha256": digest(route.get("base_url") or ""),
    }


def _resolve_route() -> dict:
    from core.provider_settings import resolve_text_route
    return resolve_text_route("workshop")


def _client(route: dict, cancel: threading.Event):
    from core.llm_client import LLMClient
    return LLMClient(
        provider=route["provider"], model=route.get("model") or None,
        base_url=route.get("base_url") or None, api_key=route.get("api_key") or None,
        max_tokens=route.get("max_tokens"), timeout_seconds=route.get("timeout_seconds"),
        reasoning_effort=route.get("reasoning_effort") or None,
        planning_input_bytes=route.get("planning_input_bytes"), cancellation_event=cancel,
    )


def _dispatch(projects, identifier, design, state, route):
    from core.usage_tracking import usage_scope

    run_id = state["run_id"]
    with _DISPATCH_LOCK:
        existing = _job(projects, identifier, run_id)
        if existing and existing.get("status") == "running":
            return public_state(projects, identifier, design.status(run_id))
        cancel = threading.Event()

        def operation():
            client = _client(route, cancel)
            with usage_scope(source_store(projects).path(identifier), "brainstorming"):
                return design.run(run_id, client, cancel)

        job_id = runner.submit(
            "source.routes", operation,
            meta={"source_id": identifier, "route_run_id": run_id},
            unique_key=str(_job_path(projects, identifier, run_id)),
            cancel_event=cancel, defer_cancel_completion=True,
            result_mapper=lambda _: {"source_id": identifier, "route_run_id": run_id},
        )
        atomic_json(_job_path(projects, identifier, run_id), {"run_id": run_id, "job_id": job_id})
        return public_state(projects, identifier, design.status(run_id))


def _design_or_404(projects, identifier):
    try:
        return design_for(projects, identifier)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{identifier}/route-runs", status_code=202)
def create_run(identifier: str, body: RouteRunRequest, projects: ProjectService = Depends(get_service)):
    design = _design_or_404(projects, identifier)
    request = body.model_dump(exclude_none=True)
    try:
        # Idempotent retries must remain readable when settings/catalog change.
        # The core still verifies the full request digest before returning it.
        if any(row.get("request", {}).get("operation_id") == body.operation_id for row in design.list_runs()):
            existing = design.prepare(request, model={}, catalog=[])
            return public_state(projects, identifier, existing)
        catalog = catalog_for(projects)
        _validate_author_selection(body.author_context, catalog)
        route = deepcopy(_resolve_route())
        state = design.prepare(request, model=_model_binding(route), catalog=catalog)
        if state.get("status") in _COMPLETE:
            return public_state(projects, identifier, state)
        return _dispatch(projects, identifier, design, state, route)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{identifier}/route-runs")
def list_runs(identifier: str, projects: ProjectService = Depends(get_service)):
    design = _design_or_404(projects, identifier)
    try:
        return {"items": [public_state(projects, identifier, row) for row in design.list_runs()]}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{identifier}/route-runs/{run_id}")
def get_run(identifier: str, run_id: str, projects: ProjectService = Depends(get_service)):
    design = _design_or_404(projects, identifier)
    try:
        return public_state(projects, identifier, design.status(run_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{identifier}/route-runs/{run_id}/pause")
def pause_run(identifier: str, run_id: str, projects: ProjectService = Depends(get_service)):
    design = _design_or_404(projects, identifier)
    try:
        design.status(run_id)
        job = _job(projects, identifier, run_id)
        if job:
            runner.cancel(job["job_id"])
        # Core records cancellation durably; an in-flight response is retained.
        paused = design.pause(run_id)
        return public_state(projects, identifier, paused or design.status(run_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{identifier}/route-runs/{run_id}/resume", status_code=202)
def resume_run(
    identifier: str, run_id: str,
    body: ResumeRequest = Body(default_factory=ResumeRequest),
    projects: ProjectService = Depends(get_service),
):
    design = _design_or_404(projects, identifier)
    try:
        state = design.status(run_id)
        if state.get("status") in _COMPLETE:
            return public_state(projects, identifier, state)
        job = _job(projects, identifier, run_id)
        if job and job.get("status") == "running":
            return public_state(projects, identifier, state)
        return _dispatch(projects, identifier, design, state, deepcopy(_resolve_route()))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{identifier}/route-sets")
def list_route_sets(identifier: str, projects: ProjectService = Depends(get_service)):
    design = _design_or_404(projects, identifier)
    try:
        runs = [public_state(projects, identifier, row) for row in design.list_runs()]
        runs.sort(key=lambda row: (row.get("created_at", ""), row["run_id"]), reverse=True)
        items = sorted(design.list_route_sets(), key=lambda row: row.get("created_at", ""), reverse=True)
        return {
            "items": items, "runs": runs,
            "active_run": runs[0] if runs and runs[0].get("status") not in _COMPLETE else None,
        }
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{identifier}/route-sets/{sha}")
def get_route_set(identifier: str, sha: str, projects: ProjectService = Depends(get_service)):
    design = _design_or_404(projects, identifier)
    try:
        result = design.get_route_set(sha)
        understanding = design.get_understanding(result["understanding_sha256"])
        view = {key: understanding.get(key) for key in (
            "narrative_endpoint", "opening_analysis", "summary_projection", "open_threads",
        )}
        if view["summary_projection"] is None:
            view["summary_projection"] = understanding.get("story_summary")
        return {
            **result,
            "understanding": view,
        }
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
