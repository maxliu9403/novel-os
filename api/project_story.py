"""Read-only project story projection and adopted-skeleton source transport."""
from __future__ import annotations

from email.utils import formatdate
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from core.project_story_insights import adopted_story_source, project_story_insights

from .routes import get_service
from .services import ProjectNotFound, ProjectService


router = APIRouter(prefix="/api/projects", tags=["project-story"])


class StorySourceRef(BaseModel):
    kind: Literal["adopted_skeleton", "outline_fallback", "state_id_map"]
    path: str
    section: str | None
    line_start: int | None
    line_end: int | None


class StoryDependency(BaseModel):
    kind: Literal["adopted_skeleton", "outline_fallback", "state_id_map"]
    path: str
    sha256: str
    bytes: int
    modified_ns: int


class StorySource(BaseModel):
    available: bool
    path: str
    sha256: str
    bytes: int
    modified_ns: int
    media_type: Literal["text/markdown"]
    dependencies: list[StoryDependency]


class StoryJsonBlock(BaseModel):
    kind: str
    raw: str
    data: Any | None
    valid: bool
    warning: str | None
    source: StorySourceRef


class StorySection(BaseModel):
    id: str
    number: int | None
    title: str
    markdown: str
    json_blocks: list[StoryJsonBlock]
    source: StorySourceRef


class StoryChapter(BaseModel):
    number: int
    title: str
    summary: str
    source: StorySourceRef


class StoryRelationshipText(BaseModel):
    text: str
    source: StorySourceRef


class StoryCharacter(BaseModel):
    id: str
    name: str
    role: str
    description: str
    relationships: list[StoryRelationshipText]
    source: StorySourceRef


class StoryRelationshipEdge(BaseModel):
    id: str
    source_character_id: str
    target_character_id: str
    label: str
    description: str
    source: StorySourceRef


class StoryNarrative(BaseModel):
    title: str
    text: str
    source: StorySourceRef


class StoryUnstructuredSection(BaseModel):
    kind: Literal["characters", "relationships", "chapters"]
    title: str
    text: str
    source: StorySourceRef


class ProjectStory(BaseModel):
    project_id: str
    chapters: list[StoryChapter]
    characters: list[StoryCharacter]
    relationship_edges: list[StoryRelationshipEdge]
    narrative: StoryNarrative | None
    sections: list[StorySection]
    unstructured_sections: list[StoryUnstructuredSection]
    source: StorySource
    warnings: list[str]


def _project(svc: ProjectService, project_id: str):
    try:
        return svc._project_dir(project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc


@router.get("/{project_id}/story", response_model=ProjectStory)
def get_project_story(
    project_id: str,
    svc: ProjectService = Depends(get_service),
) -> ProjectStory:
    project = _project(svc, project_id)
    return ProjectStory.model_validate(project_story_insights(project, project_id))


def _etag_matches(value: str | None, etag: str) -> bool:
    if not value:
        return False
    for candidate in value.split(","):
        candidate = candidate.strip()
        if candidate == "*" or candidate == etag or candidate.removeprefix("W/") == etag:
            return True
    return False


@router.get("/{project_id}/story/source")
def get_project_story_source(
    project_id: str,
    request: Request,
    svc: ProjectService = Depends(get_service),
) -> Response:
    project = _project(svc, project_id)
    source = adopted_story_source(project)
    if source is None:
        raise HTTPException(status_code=404, detail="Adopted story skeleton is not available")
    data, sha256, modified_ns = source
    etag = f'"{sha256}"'
    headers = {
        "ETag": etag,
        "X-Source-SHA256": sha256,
        "Last-Modified": formatdate(modified_ns / 1_000_000_000, usegmt=True),
        "Content-Disposition": 'inline; filename="prompt.md"',
    }
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type="text/markdown", headers=headers)
