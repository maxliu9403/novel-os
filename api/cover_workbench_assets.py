"""Candidate operations for discussion covers and explicit project transfer."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from .cover_workbench import workshop_workflow, project_workflow, _workshop_set_out
from .routes import (get_service, get_cover_service, get_cover_mutation_service,
                     get_cover_review_service, get_media_store, _cover_error, _guard_project_mutation)
from .models import CoverCandidateMutation
from .services import ProjectService
from .jobs import runner
from . import db

router = APIRouter(prefix="/api", dependencies=[Depends(_guard_project_mutation)])


@router.get("/workshop/sessions/{session_id}/covers/{cover_set_id}")
def get_workshop_cover(session_id: str, cover_set_id: str, svc: ProjectService = Depends(get_service)):
    flow = workshop_workflow(svc, session_id)
    try:
        return _workshop_set_out(session_id, flow.store.load(cover_set_id), flow.store.active().revision)
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc


@router.post("/workshop/sessions/{session_id}/covers/{cover_set_id}/candidates/{candidate_id}/select")
def select_workshop_cover(session_id: str, cover_set_id: str, candidate_id: str,
                          body: CoverCandidateMutation, svc: ProjectService = Depends(get_service),
                          covers=Depends(get_cover_mutation_service)):
    flow = workshop_workflow(svc, session_id)
    try:
        flow.validate_candidate(cover_set_id, candidate_id)
        result = covers.select_candidate(flow.store.project_path, cover_set_id, candidate_id,
            expected_revision=body.expected_revision, expected_active_revision=body.expected_active_revision,
            quality_acceptance_reason=body.quality_acceptance_reason, cover_root=flow.store.root)
        return _workshop_set_out(session_id, result, flow.store.active().revision)
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc


@router.post("/workshop/sessions/{session_id}/covers/{cover_set_id}/candidates/{candidate_id}/reject")
def reject_workshop_cover(session_id: str, cover_set_id: str, candidate_id: str,
                          body: CoverCandidateMutation, svc: ProjectService = Depends(get_service),
                          covers=Depends(get_cover_mutation_service)):
    flow = workshop_workflow(svc, session_id)
    try:
        result = covers.reject_candidate(flow.store.project_path, cover_set_id, candidate_id,
                                         expected_revision=body.expected_revision, cover_root=flow.store.root)
        return _workshop_set_out(session_id, result, flow.store.active().revision)
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc


@router.post("/workshop/sessions/{session_id}/covers/{cover_set_id}/candidates/{candidate_id}/retry", status_code=202)
def retry_workshop_cover(session_id: str, cover_set_id: str, candidate_id: str,
                         body: CoverCandidateMutation, svc: ProjectService = Depends(get_service),
                         covers=Depends(get_cover_service)):
    flow = workshop_workflow(svc, session_id)
    try:
        flow.validate_candidate(cover_set_id, candidate_id, for_generation=True)
        def retry():
            flow.validate_candidate(cover_set_id, candidate_id, for_generation=True)
            return covers.retry_candidate(f"workshop-{session_id}", flow.store.project_path,
                cover_set_id, candidate_id, expected_revision=body.expected_revision,
                repair_codes=body.repair_codes, cover_root=flow.store.root)
        job_id = runner.submit("cover.retry", retry, meta={"session_id": session_id, "cover_set_id": cover_set_id})
        return runner.get(job_id)
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc


def _reevaluate(flow, covers, cover_set_id, candidate_id, body, *, project_id="", session_id=""):
    try:
        flow.validate_candidate(cover_set_id, candidate_id)
        def review():
            flow.validate_candidate(cover_set_id, candidate_id)
            return covers.reevaluate_candidate(project_id or f"workshop-{session_id}",
                flow.store.project_path, cover_set_id, candidate_id, expected_revision=body.expected_revision,
                **({"cover_root": flow.store.root} if session_id else {}))
        job_id = runner.submit("cover.reevaluate", review,
            meta={"project_id": project_id, "project_path": str(flow.store.project_path) if project_id else "",
                  "session_id": session_id, "cover_set_id": cover_set_id},
            unique_key=f"cover-review:{flow.context.owner_scope}:{candidate_id}")
        return runner.get(job_id)
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc


@router.post("/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/reevaluate", status_code=202)
def reevaluate_project_cover(project_id: str, cover_set_id: str, candidate_id: str,
                             body: CoverCandidateMutation, svc: ProjectService = Depends(get_service),
                             covers=Depends(get_cover_review_service)):
    return _reevaluate(project_workflow(svc, project_id), covers, cover_set_id, candidate_id, body, project_id=project_id)


@router.post("/workshop/sessions/{session_id}/covers/{cover_set_id}/candidates/{candidate_id}/reevaluate", status_code=202)
def reevaluate_workshop_cover(session_id: str, cover_set_id: str, candidate_id: str,
                              body: CoverCandidateMutation, svc: ProjectService = Depends(get_service),
                              covers=Depends(get_cover_review_service)):
    return _reevaluate(workshop_workflow(svc, session_id), covers, cover_set_id, candidate_id, body, session_id=session_id)


class TypographyProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1)
    proposal: dict


class TypographyApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_amendment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_layout: bool = Field(strict=True)


class TypographyApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1)
    expected_amendment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _typography_propose(flow, covers, cover_set_id, candidate_id, body, *, project_id="", session_id=""):
    try:
        flow.validate_candidate(cover_set_id, candidate_id)
        result = covers.propose_typography_amendment(project_id or f"workshop-{session_id}",
            flow.store.project_path, cover_set_id, candidate_id, expected_revision=body.expected_revision,
            proposal=body.proposal, **({"cover_root": flow.store.root} if session_id else {}))
        result = {key: value for key, value in result.items() if key != "preview_path"}
        base = f"/api/projects/{project_id}" if project_id else f"/api/workshop/sessions/{session_id}"
        result["preview_url"] = f"{base}/covers/{cover_set_id}/candidates/{candidate_id}/typography-amendments/{result['amendment_id']}/preview"
        return result
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc


def _typography_approve(flow, covers, cover_set_id, candidate_id, amendment_id, body, *, project_id="", session_id=""):
    try:
        flow.validate_candidate(cover_set_id, candidate_id)
        result = covers.approve_typography_amendment(project_id or f"workshop-{session_id}",
            flow.store.project_path, cover_set_id, candidate_id, amendment_id,
            expected_amendment_sha256=body.expected_amendment_sha256, confirm_layout=body.confirm_layout,
            **({"cover_root": flow.store.root} if session_id else {}))
        return {key: value for key, value in result.items() if key != "preview_path"}
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc


def _typography_apply(flow, covers, cover_set_id, candidate_id, amendment_id, body, *, project_id="", session_id=""):
    try:
        flow.validate_candidate(cover_set_id, candidate_id)
        def apply():
            flow.validate_candidate(cover_set_id, candidate_id)
            return covers.apply_typography_amendment(project_id or f"workshop-{session_id}",
                flow.store.project_path, cover_set_id, candidate_id, amendment_id,
                expected_revision=body.expected_revision, expected_amendment_sha256=body.expected_amendment_sha256,
                source_guard=lambda: flow.validate_candidate(cover_set_id, candidate_id),
                **({"cover_root": flow.store.root} if session_id else {}))
        job_id = runner.submit("cover.typography", apply,
            meta={"project_id": project_id, "project_path": str(flow.store.project_path) if project_id else "",
                  "session_id": session_id, "cover_set_id": cover_set_id, "candidate_id": candidate_id,
                  "amendment_id": amendment_id},
            unique_key=f"cover-typography:{flow.context.owner_scope}:{candidate_id}")
        return runner.get(job_id)
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc


@router.post("/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/typography-amendments", status_code=201)
def propose_project_typography(project_id: str, cover_set_id: str, candidate_id: str,
                               body: TypographyProposalRequest, svc: ProjectService = Depends(get_service),
                               covers=Depends(get_cover_mutation_service)):
    return _typography_propose(project_workflow(svc, project_id), covers, cover_set_id, candidate_id, body, project_id=project_id)


@router.post("/workshop/sessions/{session_id}/covers/{cover_set_id}/candidates/{candidate_id}/typography-amendments", status_code=201)
def propose_workshop_typography(session_id: str, cover_set_id: str, candidate_id: str,
                                body: TypographyProposalRequest, svc: ProjectService = Depends(get_service),
                                covers=Depends(get_cover_mutation_service)):
    return _typography_propose(workshop_workflow(svc, session_id), covers, cover_set_id, candidate_id, body, session_id=session_id)


@router.post("/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/typography-amendments/{amendment_id}/approve")
def approve_project_typography(project_id: str, cover_set_id: str, candidate_id: str, amendment_id: str,
                               body: TypographyApprovalRequest, svc: ProjectService = Depends(get_service),
                               covers=Depends(get_cover_mutation_service)):
    return _typography_approve(project_workflow(svc, project_id), covers, cover_set_id, candidate_id,
                               amendment_id, body, project_id=project_id)


@router.post("/workshop/sessions/{session_id}/covers/{cover_set_id}/candidates/{candidate_id}/typography-amendments/{amendment_id}/approve")
def approve_workshop_typography(session_id: str, cover_set_id: str, candidate_id: str, amendment_id: str,
                                body: TypographyApprovalRequest, svc: ProjectService = Depends(get_service),
                                covers=Depends(get_cover_mutation_service)):
    return _typography_approve(workshop_workflow(svc, session_id), covers, cover_set_id, candidate_id,
                               amendment_id, body, session_id=session_id)


@router.post("/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/typography-amendments/{amendment_id}/apply", status_code=202)
def apply_project_typography(project_id: str, cover_set_id: str, candidate_id: str, amendment_id: str,
                             body: TypographyApplyRequest, svc: ProjectService = Depends(get_service),
                             covers=Depends(get_cover_review_service)):
    return _typography_apply(project_workflow(svc, project_id), covers, cover_set_id, candidate_id,
                             amendment_id, body, project_id=project_id)


@router.post("/workshop/sessions/{session_id}/covers/{cover_set_id}/candidates/{candidate_id}/typography-amendments/{amendment_id}/apply", status_code=202)
def apply_workshop_typography(session_id: str, cover_set_id: str, candidate_id: str, amendment_id: str,
                              body: TypographyApplyRequest, svc: ProjectService = Depends(get_service),
                              covers=Depends(get_cover_review_service)):
    return _typography_apply(workshop_workflow(svc, session_id), covers, cover_set_id, candidate_id,
                             amendment_id, body, session_id=session_id)


def _typography_preview(flow, cover_set_id, candidate_id, amendment_id):
    from core.cover_typography_amendments import read_amendment_preview
    try:
        flow.validate_candidate(cover_set_id, candidate_id)
        data = read_amendment_preview(flow.store.root, amendment_id,
            cover_set_id=cover_set_id, candidate_id=candidate_id)
        return Response(data, media_type="image/png", headers={"Cache-Control": "private, no-store"})
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc


@router.get("/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/typography-amendments/{amendment_id}/preview")
def preview_project_typography(project_id: str, cover_set_id: str, candidate_id: str, amendment_id: str,
                               svc: ProjectService = Depends(get_service)):
    return _typography_preview(project_workflow(svc, project_id), cover_set_id, candidate_id, amendment_id)


@router.get("/workshop/sessions/{session_id}/covers/{cover_set_id}/candidates/{candidate_id}/typography-amendments/{amendment_id}/preview")
def preview_workshop_typography(session_id: str, cover_set_id: str, candidate_id: str, amendment_id: str,
                                svc: ProjectService = Depends(get_service)):
    return _typography_preview(workshop_workflow(svc, session_id), cover_set_id, candidate_id, amendment_id)


class CoverTransferRequest(BaseModel):
    session_id: str


@router.post("/projects/{project_id}/covers/import-workshop")
def import_workshop_covers(project_id: str, body: CoverTransferRequest,
                           svc: ProjectService = Depends(get_service), media=Depends(get_media_store)):
    from core.cover_transfer import transfer_workshop_covers
    source = workshop_workflow(svc, body.session_id)
    target = project_workflow(svc, project_id)
    try:
        return transfer_workshop_covers(source.context, target.context, source.store, target.store,
                                       media, db.media_add, project_id)
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc
