"""Read-only manuscript projection for the in-app book reader.

The reader deliberately bypasses ``ProjectService._load``.  It reads the
persisted story index and manuscript authorities without ingesting legacy
files, advancing artifact heads, or creating artifact locks/directories.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from .routes import get_service
from .services import ProjectNotFound, ProjectService


router = APIRouter(prefix="/api/projects", tags=["project-reader"])

ReaderVersion = Literal["final", "revised", "draft"]


class ProjectReaderChapterSummary(BaseModel):
    number: int
    title: str
    status: str
    available: bool


class ProjectReaderIndex(BaseModel):
    title: str
    author: str
    chapters: list[ProjectReaderChapterSummary]


class ProjectReaderChapter(BaseModel):
    number: int
    title: str
    status: str
    version: ReaderVersion | None
    markdown: str


from core.manuscript_reader import (
    ReaderSourceError, _ChapterRecord, _SelectedProse, _load_reader_state,
    _artifact_snapshot, _selected_prose,
)


def _project(svc: ProjectService, project_id: str) -> Path:
    try:
        return svc.project_path(project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc


def _source_http_error(error: ReaderSourceError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "reader_source_invalid", "message": str(error)},
    )


def _display_title(chapter: _ChapterRecord, prose: _SelectedProse) -> str:
    return chapter.title or prose.header_title or f"Chapter {chapter.number}"


@router.get("/{project_id}/reader", response_model=ProjectReaderIndex)
def get_project_reader(
    project_id: str,
    svc: ProjectService = Depends(get_service),
) -> ProjectReaderIndex:
    project = _project(svc, project_id)
    try:
        state = _load_reader_state(project, project_id)
        with _artifact_snapshot(project) as snapshot:
            chapters = []
            for chapter in state.chapters:
                prose = _selected_prose(project, chapter.number, snapshot)
                chapters.append(
                    ProjectReaderChapterSummary(
                        number=chapter.number,
                        title=_display_title(chapter, prose),
                        status=chapter.status,
                        available=bool(prose.markdown),
                    )
                )
        return ProjectReaderIndex(
            title=state.title,
            author=state.author,
            chapters=chapters,
        )
    except ReaderSourceError as exc:
        raise _source_http_error(exc) from exc


@router.get(
    "/{project_id}/reader/chapters/{number}",
    response_model=ProjectReaderChapter,
)
def get_project_reader_chapter(
    project_id: str,
    number: int,
    svc: ProjectService = Depends(get_service),
) -> ProjectReaderChapter:
    project = _project(svc, project_id)
    try:
        state = _load_reader_state(project, project_id)
        chapter = next(
            (candidate for candidate in state.chapters if candidate.number == number),
            None,
        )
        if chapter is None:
            raise HTTPException(status_code=404, detail="Chapter not found")
        with _artifact_snapshot(project) as snapshot:
            prose = _selected_prose(project, chapter.number, snapshot)
        return ProjectReaderChapter(
            number=chapter.number,
            title=_display_title(chapter, prose),
            status=chapter.status,
            version=prose.version,
            markdown=prose.markdown,
        )
    except ReaderSourceError as exc:
        raise _source_http_error(exc) from exc
