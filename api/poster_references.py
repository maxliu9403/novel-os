"""One private reference-image store for upload and imported source-book covers."""

from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from core.reference_assets import MAX_BYTES, ReferenceAssets

from .cover_workbench import project_workflow
from .routes import _guard_project_mutation, get_service
from .services import ProjectService

router = APIRouter(
    prefix="/api/projects/{project_id}/poster/reference",
    dependencies=[Depends(_guard_project_mutation)],
)


def store_for(flow):
    return ReferenceAssets(
        flow.store.project_path / "outputs" / "private" / "references"
    )


def public_selection(project_id, value):
    if value is None:
        return None
    return {
        **value,
        "url": f"/api/projects/{project_id}/poster/reference/{value['asset_id']}",
    }


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str | None = None
    mode: Literal["visual_inspiration", "adaptation_reference"] = "visual_inspiration"


@router.post("")
async def upload(
    project_id: str,
    file: UploadFile = File(...),
    svc: ProjectService = Depends(get_service),
):
    flow = project_workflow(svc, project_id)
    try:
        data = await file.read(MAX_BYTES + 1)
        with flow.store.mutation_lock():
            store = store_for(flow)
            asset = store.put(data)
            return public_selection(project_id, store.select(asset["asset_id"]))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()


@router.post("/selection")
def select(
    project_id: str, body: Selection, svc: ProjectService = Depends(get_service)
):
    flow = project_workflow(svc, project_id)
    try:
        with flow.store.mutation_lock():
            return public_selection(
                project_id, store_for(flow).select(body.asset_id, body.mode)
            )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{asset_id}")
def image(project_id: str, asset_id: str, svc: ProjectService = Depends(get_service)):
    store = store_for(project_workflow(svc, project_id))
    try:
        asset = store.get(asset_id)
        return FileResponse(
            store.path(asset_id),
            media_type=asset["content_type"],
            headers={
                "Cache-Control": "private, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
