"""Versioned read-model API. Authority operations remain on project routes."""
from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from .jobs import runner
from .library_catalog import CatalogCursorError
from .routes import get_service
from .services import ProjectService
from .tenancy import valid_project_id

router = APIRouter(prefix="/api/library", tags=["library"])


def _scope(request: Request, service: ProjectService):
    return request.app.state.library_runtime.for_service(service)


def _json(request: Request, payload: dict):
    etag = payload.get("etag", "")
    if etag and not etag.startswith('"'):
        etag = f'"{etag}"'
    headers = {"Cache-Control": "private, no-cache", "Vary": "Authorization, Cookie"}
    if etag:
        headers["ETag"] = etag
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
    return JSONResponse(payload, headers=headers)


@router.get("/context")
def library_context(request: Request, service: ProjectService = Depends(get_service)):
    catalog, _ = _scope(request, service)
    return JSONResponse({"scope": catalog.scope}, headers={"Cache-Control": "private, no-store"})


@router.get("")
def library_query(
    request: Request,
    q: str = Query("", max_length=240),
    status: Literal["all", "writing", "completed", "attention", "draft"] = "all",
    cover: Literal["all", "not_started", "processing", "awaiting_review", "needs_attention", "selected"] = "all",
    sort: Literal["recent"] = "recent",
    cursor: str | None = Query(None, max_length=4096),
    limit: int = Query(20, ge=1, le=50),
    service: ProjectService = Depends(get_service),
):
    catalog, _ = _scope(request, service)
    try:
        payload = catalog.query(q=q, status=status, cover=cover, sort=sort,
                                cursor=cursor, limit=limit,
                                live_snapshot=runner.library_snapshot(service.base_dir))
    except CatalogCursorError as exc:
        raise HTTPException(status_code=409, detail={"reset_required": True, "message": str(exc)}) from exc
    return _json(request, payload)


@router.get("/changes")
def library_changes(
    request: Request,
    after: str = Query(..., max_length=4096),
    limit: int = Query(100, ge=1, le=200),
    service: ProjectService = Depends(get_service),
):
    catalog, _ = _scope(request, service)
    return _json(request, catalog.changes(after, limit=limit,
                 live_snapshot=runner.library_snapshot(service.base_dir)))


@router.get("/navigation")
def library_navigation(
    request: Request,
    q: str = Query("", max_length=240),
    limit: int = Query(20, ge=1, le=50),
    service: ProjectService = Depends(get_service),
):
    catalog, _ = _scope(request, service)
    return _json(request, catalog.navigation(q=q, limit=limit,
                 live_snapshot=runner.library_snapshot(service.base_dir)))


@router.get("/{project_id}/thumbnails/{digest}/{width}.webp")
def library_thumbnail(
    project_id: str, digest: str, width: int, request: Request,
    service: ProjectService = Depends(get_service),
):
    if not valid_project_id(project_id) or not re.fullmatch(r"[0-9a-f]{64}", digest) or width not in (160, 256, 384):
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    catalog, indexer = _scope(request, service)
    record = catalog.get_record(project_id)
    if not record or record.get("tombstone"):
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    try:
        path = indexer.thumbnail_path(project_id, digest, width)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Thumbnail not found") from exc
    if path is None:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(path, media_type="image/webp", headers={
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })
