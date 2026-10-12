"""Read-only owner-scoped costs and explicit global reference price settings."""
import json
from fastapi import APIRouter, Depends, HTTPException

from core.usage_costs import costs_payload, load_pricing, owner_records, save_pricing
from .routes import get_service
from .services import ProjectNotFound, ProjectService
from .workshop import WorkshopService, get_workshop_service

router = APIRouter(prefix="/api")


def _session(svc, session_id):
    # WorkshopService.get reconciles jobs and can write state. Accounting is a
    # read-only projection and must not interrupt or "repair" a workshop turn.
    path = svc._path(session_id) / "session.json"
    try:
        if path.is_symlink():
            raise ValueError("Unsafe session file")
        value = json.loads(path.read_text())
        if value.get("id") != session_id:
            raise ValueError("Mismatched session")
        return value
    except (OSError, ValueError) as exc:
        raise HTTPException(404, "Workshop session not found") from exc


@router.get("/studio/pricing")
def get_pricing():
    try:
        return load_pricing()
    except (ValueError, OSError) as exc:
        raise HTTPException(409, "Pricing settings are invalid; save valid reference prices") from exc


@router.put("/studio/pricing")
def put_pricing(body: dict):
    try:
        return save_pricing(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/projects/{project_id}/costs")
def project_costs(project_id: str, svc: ProjectService = Depends(get_service)):
    try:
        project = svc.project_path(project_id)
    except ProjectNotFound as exc:
        raise HTTPException(404, "Project not found") from exc
    rows = owner_records(project, project=True)
    workshop = WorkshopService(svc)
    linked = []
    # Resolve only this workspace's sessions, and reuse its existing safe resolver.
    for path in sorted(workshop.root.glob("*/session.json")):
        try:
            session = _session(workshop, path.parent.name)
        except (HTTPException, ValueError, OSError):
            continue
        if session.get("project_id") == project_id:
            linked.append(session["id"])
            rows.extend(owner_records(workshop._path(session["id"])))
    return costs_payload(rows, get_pricing(), project_id=project_id, linked_session_ids=linked)


@router.get("/workshop/sessions/{session_id}/costs")
def workshop_costs(session_id: str, svc: WorkshopService = Depends(get_workshop_service)):
    session = _session(svc, session_id)
    return costs_payload(owner_records(svc._path(session_id)), get_pricing(),
                         session_id=session_id, project_id=session.get("project_id"))
