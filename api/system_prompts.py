"""Editable capability prompts; saves never invoke a model or change a running task."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.system_prompts import SystemPromptError, SystemPromptStore
from . import tenancy
from .routes import get_service
from .services import ProjectService

router = APIRouter(prefix="/api/system-prompts", tags=["system-prompts"])


def store_for(projects: ProjectService) -> SystemPromptStore:
    workspace = projects.workspace.id if projects.workspace else tenancy.DEFAULT_WORKSPACE_ID
    if not tenancy.valid_project_id(workspace):
        raise HTTPException(404, "工作区标识无效。")
    return SystemPromptStore(projects.root.resolve().parent / "system_prompts" / workspace)


def get_store(projects: ProjectService = Depends(get_service)) -> SystemPromptStore:
    return store_for(projects)


class SavePrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    markdown: str = Field(min_length=1, max_length=12000)
    revision: int = Field(ge=0)
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


@router.get("/title-design")
def get_title_prompt(store: SystemPromptStore = Depends(get_store)):
    return store.get()


@router.put("/title-design")
def save_title_prompt(body: SavePrompt, store: SystemPromptStore = Depends(get_store)):
    try:
        return store.save(body.markdown, body.revision, body.operation_id)
    except SystemPromptError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
