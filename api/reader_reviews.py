"""Independent manuscript review endpoints. GET never starts/resumes model work."""
from __future__ import annotations
import os
from contextlib import contextmanager
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from core.reader_experience import ReaderReviews, ReviewOptions, ReviewConflict
from core.manuscript_reader import ReaderSourceError
from core.project_identity import ProjectIdentityError
from core.safe_project_io import ProjectPathError
from .routes import get_service
from .services import ProjectService, ProjectNotFound
from .jobs import runner, ProjectJobBlocked
from .project_operations import project_operations, ProjectMutationBlocked

router = APIRouter(prefix="/api/projects/{project_id}/reader-reviews", tags=["reader-reviews"])


class CreateReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    options: ReviewOptions
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


@contextmanager
def errors():
    try:
        yield
    except (ProjectNotFound, KeyError) as error:
        raise HTTPException(404, "作品或评议不存在") from error
    except (ReviewConflict, ReaderSourceError, ProjectIdentityError, ProjectPathError, ProjectJobBlocked, ProjectMutationBlocked) as error:
        raise HTTPException(409, str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error)) from error


def runtime(svc, project_id):
    return ReaderReviews(svc.project_path(project_id), project_id)


def dispatch(review, review_id, svc):
    lease = review.claim(review_id)
    def work():
        # Registry fences deletion throughout the full worker and cancellation drain.
        try:
            svc.project_path(review.project_id)
        except Exception:
            os.close(lease)
            raise
        return review.run(review_id, lease)
    try:
        runner.submit("reader_review", work, {"project_id": review.project_id, "project_path": str(review.project)},
                      result_mapper=lambda item: {"review_id": review_id, "review_status": item["status"]})
    except Exception:
        os.close(lease)
        raise


@router.post("/preview")
def preview(project_id: str, options: ReviewOptions, svc: ProjectService = Depends(get_service)):
    with errors():
        return runtime(svc, project_id).preview(options)


@router.post("", status_code=202)
def create(project_id: str, body: CreateReview, svc: ProjectService = Depends(get_service)):
    with errors():
        review = runtime(svc, project_id)
        with project_operations.mutation(review.project):
            review_id, created = review.create(body.options, body.idempotency_key, body.proposal_sha256)
            if created:
                dispatch(review, review_id, svc)
        return review.get(review_id, freshness=False)


@router.get("")
def history(project_id: str, svc: ProjectService = Depends(get_service)):
    with errors():
        return {"reviews": runtime(svc, project_id).list()}


@router.get("/{review_id}")
def detail(project_id: str, review_id: str, svc: ProjectService = Depends(get_service)):
    with errors():
        return runtime(svc, project_id).get(review_id)


@router.get("/{review_id}/source")
def source(project_id: str, review_id: str, chapter: int = Query(ge=1), svc: ProjectService = Depends(get_service)):
    with errors():
        return runtime(svc, project_id).source(review_id, chapter)


@router.post("/{review_id}/pause")
def pause(project_id: str, review_id: str, svc: ProjectService = Depends(get_service)):
    with errors():
        review = runtime(svc, project_id)
        with project_operations.mutation(review.project):
            return review.pause(review_id)


@router.post("/{review_id}/resume", status_code=202)
def resume(project_id: str, review_id: str, svc: ProjectService = Depends(get_service)):
    with errors():
        review = runtime(svc, project_id)
        with project_operations.mutation(review.project):
            dispatch(review, review_id, svc)
        return review.get(review_id, freshness=False)
