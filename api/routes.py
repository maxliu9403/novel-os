import base64
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from fastapi import Response
from fastapi.responses import PlainTextResponse

from . import db, media as media_lib, richtext, tenancy
from .cover_service import CoverService, CoverServiceError
from .jobs import ProjectJobBlocked, ProjectJobsRunning, runner
from .models import (
    AddCharacter, AddCodexEntry, AddComment, AddRelationship, ChapterDetail, ChapterStages,
    CodexProposal, ContinuityExemption, ExemptFinding, BookShape, StyleSheetOut,
    ChapterSummary, CharacterSummary, CodexEntryOut, Comment, ConsequenceAccept,
    ConsequenceAcceptResult, ConsequencePreview, ConsequencePreviewRequest, ContinuityReport,
    ContinueParagraph, ContinueResult, CreateProject, CreateSnapshot, FinalDoc, FinalDocSave,
    FinalResult, FinalSave, Job, MediaOut, ProjectDeletionPreview,
    ProjectDeletionResult, ProjectDetail, ProjectSummary, RelationshipOut,
    RunPhase, CoverGenerateRequest, CoverCandidateMutation, SearchHit, CollectionOut,
    CreateCollection, SetPortrait, SnapshotMeta, SnapshotText, StageDiff, StageReviewRequest,
    StageReviewResult, StudioCoverStatus, StudioCoverUpdate, StudioLlmStatus,
    StudioLlmUpdate, UpdateComment, UpdateProject,
    BinderMoveRequest, BinderPatchRequest, SynopsisRefreshResult, UpdateMedia,
    ProjectStatistics, OutlinerMetricsRefreshResult, UpdateCodexEntry,
    ArtifactRevisionOut, ChapterQualityOut, EvaluationReportOut, PromotionReceiptOut,
    CoverDirectionCreate, CoverDirectionApproval, CoverStoryFactsUpdate,
    ImageProfileOut, ImageProfileUpdate,
    ProviderConnectionInput, ProviderConnectionOut, ProviderTestResult,
    StudioModelConfigurationOut, TextModelTestRequest, TextRouteOut, TextRoutesUpdate,
)
from .version import __version__
from .services import (
    BadRequest, ChapterNotFound, NoSourceArtifact, ProjectNotFound, ProjectService,
    PromotionConflict, PromotionIntegrityFailure, PromotionUnavailable,
)
from .project_deletion import (
    ProjectDeletionConfirmationError,
    ProjectDeletionError,
    ProjectDeletionService,
)
from .project_operations import ProjectMutationBlocked, project_operations


def get_service() -> ProjectService:
    root = Path(os.environ.get("NOVEL_OS_PROJECTS_DIR", "./projects"))
    return ProjectService(root)


def _guard_project_mutation(
    request: Request,
    svc: ProjectService = Depends(get_service),
):
    """Gate every path-scoped synchronous write through one dependency.

    The permanent project DELETE owns its exclusive lease inside the deletion
    module, so it is the only project mutation intentionally excluded here.
    """
    project_id = request.path_params.get("project_id")
    path_parts = request.url.path.strip("/").split("/")
    stage_projection_get = (
        request.method == "GET"
        and len(path_parts) == 6
        and path_parts[:2] == ["api", "projects"]
        and path_parts[3] == "chapters"
        and path_parts[5] == "stages"
    )
    if (
        request.method not in {"POST", "PUT", "PATCH", "DELETE"}
        and not stage_projection_get
    ) or not project_id:
        yield
        return

    if (
        request.method == "DELETE"
        and path_parts[:2] == ["api", "projects"]
        and len(path_parts) == 3
    ):
        yield
        return

    try:
        project_path = tenancy.project_dir(svc.root, project_id, svc.workspace)
    except tenancy.TenancyError:
        # Keep malformed ids indistinguishable from missing projects; the
        # handler's ordinary resolver supplies the established 404 response.
        yield
        return

    try:
        with project_operations.mutation(project_path):
            yield
    except ProjectMutationBlocked as exc:
        raise _project_job_blocked_http() from exc


router = APIRouter(
    prefix="/api",
    dependencies=[Depends(_guard_project_mutation)],
)


def _promotion_http_error(error: Exception) -> HTTPException:
    if isinstance(error, PromotionUnavailable):
        return HTTPException(
            status_code=503,
            detail=(
                "Promotion outcome needs reconciliation; refresh chapter quality "
                "before another mutation."
            ),
        )
    if isinstance(error, PromotionIntegrityFailure):
        return HTTPException(
            status_code=409,
            detail=f"Promotion integrity conflict: {error}",
        )
    return HTTPException(status_code=409, detail=f"Promotion conflict: {error}")


def _project_job_blocked_http() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "project_deleting",
            "message": "小说正在删除或已被删除，无法启动新任务。",
        },
    )


def get_media_store() -> media_lib.MediaStore:
    root = Path(os.environ.get("NOVEL_OS_MEDIA_DIR", "./media"))
    return media_lib.LocalMediaStore(root)


def get_cover_service(
    store: media_lib.MediaStore = Depends(get_media_store),
) -> CoverService:
    from core.cover_quality import build_llm_visual_evaluator, unavailable_evaluator
    from core.image_client import ImageClientError, build_image_generation_client
    from core.llm_client import LLMClient, LLMError
    from core.studio_settings import resolve_cover_director_settings, resolve_cover_settings

    try:
        client = build_image_generation_client(resolve_cover_settings())
    except (ValueError, ImageClientError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        review_settings = resolve_cover_director_settings()
        review_client = LLMClient(
            provider=review_settings.provider or None,
            model=review_settings.model or None,
            base_url=review_settings.base_url or None,
            api_key=review_settings.api_key or None,
            timeout_seconds=review_settings.timeout_seconds,
            reasoning_effort=review_settings.reasoning_effort or None,
        )
        visual_evaluator = build_llm_visual_evaluator(review_client)
    except (LLMError, ValueError) as exc:
        visual_evaluator = unavailable_evaluator(str(exc))
    return CoverService(
        image_client=client,
        media_store=store,
        media_add=db.media_add,
        visual_evaluator=visual_evaluator,
    )


def get_cover_mutation_service(
    store: media_lib.MediaStore = Depends(get_media_store),
) -> CoverService:
    """Build cover management operations without resolving billable provider settings."""
    return CoverService(media_store=store, media_add=db.media_add)


def get_cover_art_director():
    """Resolve story direction through the configured text-model boundary."""
    from core.cover_director import CoverArtDirector
    from core.llm_client import LLMClient, LLMError
    from core.studio_settings import resolve_cover_director_settings

    try:
        settings = resolve_cover_director_settings()
        client = LLMClient(
            provider=settings.provider or None,
            model=settings.model or None,
            base_url=settings.base_url or None,
            api_key=settings.api_key or None,
            timeout_seconds=settings.timeout_seconds,
            # Keep the Cover Director's route-level effort independent from
            # the interactive Codex profile mounted into the container.
            reasoning_effort=settings.reasoning_effort or None,
        )
    except (LLMError, ValueError) as exc:
        message = str(exc)

        def unavailable(_system: str, _user: str) -> str:
            raise LLMError(message)

        return CoverArtDirector(complete=unavailable, model="unavailable")
    return CoverArtDirector(
        complete=client.complete,
        model=client.model or client.provider,
    )


def _content_disposition(project_id: str, extension: str) -> str:
    """Build a browser-compatible attachment name for any project id.

    HTTP header values are Latin-1 in Starlette, while project ids may be
    Unicode. Keep the legacy ASCII form when it is safe and add an RFC 5987
    UTF-8 name otherwise, with an ASCII fallback for older clients.
    """
    filename = f"{project_id}.{extension}"
    if filename.isascii() and not any(char in filename for char in '\"\\\r\n'):
        return f'attachment; filename="{filename}"'

    fallback_stem = "".join(
        char if char.isascii() and (char.isalnum() or char in "._-") else "-"
        for char in project_id
    ).strip(".-")
    fallback = f"{fallback_stem or 'novel'}.{extension}"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/studio/llm", response_model=StudioLlmStatus)
def get_studio_llm():
    from core import studio_settings
    return studio_settings.llm_status()


@router.put("/studio/llm", response_model=StudioLlmStatus)
def put_studio_llm(body: StudioLlmUpdate):
    from core import studio_settings
    patch: dict = {}
    if body.preset is not None:
        if body.preset not in studio_settings.PRESETS:
            raise HTTPException(status_code=400, detail=f"Unknown preset '{body.preset}'")
        patch["preset"] = body.preset
    if body.provider is not None:
        patch["NOVEL_OS_LLM_PROVIDER"] = body.provider.strip() or None
    if body.model is not None:
        patch["NOVEL_OS_MODEL"] = body.model.strip() or None
    if body.api_key is not None:
        key = body.api_key.strip()
        patch["NOVEL_OS_API_KEY"] = key or None
        # Also stash on OpenRouter / Anthropic when those presets are used
        if body.preset == "mature" or body.provider == "openrouter":
            patch["OPENROUTER_API_KEY"] = key or None
        if body.preset == "quality" or body.provider == "anthropic":
            patch["ANTHROPIC_API_KEY"] = key or None
        if body.preset == "fast" or body.provider == "openai":
            patch["OPENAI_API_KEY"] = key or None
    if body.base_url is not None:
        patch["NOVEL_OS_BASE_URL"] = body.base_url.strip() or None
    if body.onboarding_completed is not None:
        patch["onboarding_completed"] = body.onboarding_completed
    studio_settings.save_settings(patch)
    return studio_settings.llm_status()


@router.get("/studio/cover", response_model=StudioCoverStatus)
def get_studio_cover():
    from core import studio_settings
    return studio_settings.cover_status()


@router.put("/studio/cover", response_model=StudioCoverStatus)
def put_studio_cover(body: StudioCoverUpdate):
    from core import studio_settings

    field_keys = {
        "base_url": "NOVEL_OS_COVER_BASE_URL",
        "api_key": "NOVEL_OS_COVER_API_KEY",
        "model": "NOVEL_OS_COVER_MODEL",
        "size": "NOVEL_OS_COVER_SIZE",
        "quality": "NOVEL_OS_COVER_QUALITY",
        "output_format": "NOVEL_OS_COVER_FORMAT",
        "count": "NOVEL_OS_COVER_COUNT",
        "timeout_seconds": "NOVEL_OS_COVER_TIMEOUT_SECONDS",
        "director_provider": "NOVEL_OS_COVER_DIRECTOR_PROVIDER",
        "director_model": "NOVEL_OS_COVER_DIRECTOR_MODEL",
        "director_base_url": "NOVEL_OS_COVER_DIRECTOR_BASE_URL",
        "director_api_key": "NOVEL_OS_COVER_DIRECTOR_API_KEY",
        "director_timeout_seconds": "NOVEL_OS_COVER_DIRECTOR_TIMEOUT_SECONDS",
        "director_reasoning_effort": "NOVEL_OS_COVER_DIRECTOR_REASONING_EFFORT",
    }
    patch: dict[str, object | None] = {}
    for field, key in field_keys.items():
        value = getattr(body, field)
        if value is None:
            continue
        patch[key] = value.strip() if isinstance(value, str) else value
        if isinstance(patch[key], str) and not patch[key]:
            patch[key] = None

    candidate = studio_settings.load_settings()
    for key, value in patch.items():
        if value is None:
            candidate.pop(key, None)
        else:
            candidate[key] = value
    try:
        studio_settings.resolve_cover_settings(candidate)
        studio_settings.resolve_cover_director_settings(candidate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    studio_settings.save_settings(patch)
    return studio_settings.cover_status()


def _provider_settings_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/studio/models", response_model=StudioModelConfigurationOut)
def get_studio_model_configuration():
    from core import provider_settings

    return provider_settings.configuration_status()


@router.post(
    "/studio/providers",
    response_model=ProviderConnectionOut,
    status_code=201,
)
def create_studio_provider(body: ProviderConnectionInput):
    from core import provider_settings

    try:
        return provider_settings.save_connection(body.model_dump())
    except provider_settings.ProviderSettingsError as exc:
        raise _provider_settings_error(exc) from exc


@router.patch("/studio/providers/{connection_id}", response_model=ProviderConnectionOut)
def update_studio_provider(connection_id: str, body: ProviderConnectionInput):
    from core import provider_settings

    try:
        return provider_settings.save_connection(body.model_dump(), connection_id)
    except provider_settings.ProviderSettingsError as exc:
        raise _provider_settings_error(exc) from exc


@router.delete("/studio/providers/{connection_id}", status_code=204)
def delete_studio_provider(connection_id: str):
    from core import provider_settings

    try:
        provider_settings.delete_connection(connection_id)
    except provider_settings.ProviderSettingsError as exc:
        raise _provider_settings_error(exc) from exc
    return Response(status_code=204)


@router.post(
    "/studio/providers/{connection_id}/test",
    response_model=ProviderTestResult,
)
def test_studio_provider(connection_id: str):
    from core import provider_settings

    try:
        return provider_settings.test_connection(connection_id)
    except provider_settings.ProviderSettingsError as exc:
        raise _provider_settings_error(exc) from exc


@router.get("/studio/providers/{connection_id}/models", response_model=list[str])
def get_studio_provider_models(connection_id: str, refresh: bool = False):
    from core import provider_settings

    configuration = provider_settings.configuration_status()
    for connection in configuration["connections"]:
        if connection["id"] == connection_id:
            if refresh:
                try:
                    return provider_settings.refresh_connection_models(connection_id)
                except provider_settings.ProviderModelDiscoveryError as exc:
                    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
                except provider_settings.ProviderSettingsError as exc:
                    raise HTTPException(status_code=404, detail="Provider connection not found") from exc
            return connection["discovered_models"]
    raise HTTPException(status_code=404, detail="Provider connection not found")


@router.put("/studio/model-routes", response_model=list[TextRouteOut])
def put_studio_model_routes(body: TextRoutesUpdate):
    from core import provider_settings

    try:
        return provider_settings.save_text_routes(
            route.model_dump() for route in body.routes
        )
    except provider_settings.ProviderSettingsError as exc:
        raise _provider_settings_error(exc) from exc


def _mapping_job_result(result: object) -> dict:
    if not isinstance(result, dict):
        raise TypeError("Model test returned an invalid result")
    return result


@router.post(
    "/studio/model-routes/{route_id}/test",
    response_model=Job,
    status_code=202,
)
def test_studio_text_route(route_id: str, body: TextModelTestRequest):
    from core import provider_settings
    from core.llm_client import LLMClient

    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Test prompt is required")
    if len(prompt) > 4000:
        raise HTTPException(status_code=400, detail="Test prompt is too long")
    try:
        route = provider_settings.resolve_text_route(route_id)
    except provider_settings.ProviderSettingsError as exc:
        raise _provider_settings_error(exc) from exc

    def run_test() -> dict:
        started = time.monotonic()
        client = LLMClient(
            provider=route["provider"] or None,
            model=route["model"] or None,
            base_url=route["base_url"] or None,
            api_key=route["api_key"] or None,
            max_tokens=min(int(route["max_tokens"]), 1024),
            timeout_seconds=120,
            reasoning_effort="low",
        )
        reply = client.complete(
            "You are a model connectivity test. Answer the user's prompt directly and briefly.",
            prompt,
        )
        return {
            "route_id": route_id,
            "provider": route["provider"],
            "model": route["model"],
            "reply": reply,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }

    fingerprint = hashlib.sha256(
        f"{route_id}\0{route['connection_id']}\0{route['model']}\0{prompt}".encode("utf-8")
    ).hexdigest()
    job_id = runner.submit(
        "studio_text_test",
        run_test,
        meta={"route_id": route_id},
        result_mapper=_mapping_job_result,
        unique_key=f"studio:text:{fingerprint}",
    )
    return runner.get(job_id)


@router.get("/studio/image-profiles/cover", response_model=ImageProfileOut)
def get_studio_cover_profile():
    from core import provider_settings

    return provider_settings.image_profile_status("cover")


@router.put("/studio/image-profiles/cover", response_model=ImageProfileOut)
def put_studio_cover_profile(body: ImageProfileUpdate):
    from core import provider_settings

    try:
        return provider_settings.save_image_profile("cover", body.model_dump())
    except (provider_settings.ProviderSettingsError, ValueError) as exc:
        raise _provider_settings_error(exc) from exc


@router.post(
    "/studio/image-profiles/cover/test",
    response_model=Job,
    status_code=202,
)
def test_studio_cover_profile():
    from core.image_client import ImageClientError, build_image_generation_client
    from core.studio_settings import resolve_cover_settings

    try:
        settings = resolve_cover_settings()
    except (ValueError, ImageClientError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    def run_test() -> dict:
        generated = build_image_generation_client(settings).generate(
            "A clean editorial book-cover test image with a single folded sheet of paper "
            "on a neutral desk, no title, no logos, portrait composition."
        )
        encoded = base64.b64encode(generated.data).decode("ascii")
        return {
            "ok": True,
            "data_url": f"data:{generated.content_type};base64,{encoded}",
            "width": generated.width,
            "height": generated.height,
            "model": generated.model,
            "request_id": generated.request_id,
        }

    fingerprint = hashlib.sha256(repr(settings).encode("utf-8")).hexdigest()
    job_id = runner.submit(
        "studio_image_test",
        run_test,
        meta={"profile_id": "cover"},
        result_mapper=_mapping_job_result,
        unique_key=f"studio:image:{fingerprint}",
    )
    return runner.get(job_id)


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects(svc: ProjectService = Depends(get_service)):
    return svc.list_projects()


@router.get("/novel-classification/catalog")
def novel_classification_catalog():
    """Return the engine-owned identifiers accepted by project metadata."""
    from novel_classification import catalog_payload

    return catalog_payload()


@router.get("/narrative-format/catalog")
def narrative_format_catalog():
    """Return author-selectable length and serialization modes."""
    from narrative_format import format_catalog_payload

    return format_catalog_payload()


@router.patch("/projects/{project_id}", response_model=ProjectDetail)
def update_project(project_id: str, body: UpdateProject, svc: ProjectService = Depends(get_service)):
    try:
        return svc.update_project(
            project_id,
            content_rating=body.content_rating,
            title=body.title,
            author=body.author,
            genre=body.genre,
            genres=body.genres,
            premise=body.premise,
            target_word_count=body.target_word_count,
            session_word_target=body.session_word_target,
            classification=body.classification,
            narrative_format=body.narrative_format,
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/projects", response_model=ProjectSummary, status_code=201)
def create_project(body: CreateProject, svc: ProjectService = Depends(get_service)):
    try:
        return svc.create_project(
            body.title, body.genre, body.author,
            genres=body.genres, premise=body.premise,
            classification=body.classification,
            narrative_format=body.narrative_format,
            method_mode=body.method_mode,
        )
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ProjectMutationBlocked as exc:
        raise _project_job_blocked_http() from exc


@router.post("/projects/sample", response_model=ProjectSummary, status_code=201)
def create_sample_project(svc: ProjectService = Depends(get_service)):
    """Idempotent first-run sample manuscript with Codex + chapter 1 draft."""
    try:
        return svc.create_sample_project()
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ProjectMutationBlocked as exc:
        raise _project_job_blocked_http() from exc


@router.get(
    "/projects/{project_id}/deletion-preview",
    response_model=ProjectDeletionPreview,
)
def project_deletion_preview(
    project_id: str,
    svc: ProjectService = Depends(get_service),
    store: media_lib.MediaStore = Depends(get_media_store),
):
    try:
        return ProjectDeletionService(svc, store, runner).preview(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"未找到小说“{project_id}”。")


@router.delete("/projects/{project_id}", response_model=ProjectDeletionResult)
def delete_project(
    project_id: str,
    confirm_title: str,
    svc: ProjectService = Depends(get_service),
    store: media_lib.MediaStore = Depends(get_media_store),
):
    try:
        return ProjectDeletionService(svc, store, runner).delete(
            project_id,
            confirm_title=confirm_title,
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"未找到小说“{project_id}”。")
    except ProjectDeletionConfirmationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_title_confirmation_mismatch",
                "message": "小说标题已变化或确认标题不匹配，请重新预览后再删除。",
                "current_title": exc.expected_title,
            },
        ) from exc
    except ProjectJobsRunning as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_jobs_running",
                "message": "项目仍有运行中的任务，请等待任务完成后重试。",
                "running_job_ids": list(exc.job_ids),
            },
        ) from exc
    except ProjectDeletionError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "project_deletion_incomplete",
                "message": "小说数据未能完整删除，请重试。",
                "reason": str(exc),
            },
        ) from exc


def _continuity_report(raw: list[dict]) -> ContinuityReport:
    findings = raw
    return ContinuityReport(
        findings=findings,  # type: ignore[arg-type]
        critical=sum(1 for f in findings if f.get("severity") == "critical"),
        warning=sum(1 for f in findings if f.get("severity") == "warning"),
        info=sum(1 for f in findings if f.get("severity") == "info"),
    )


@router.get("/projects/{project_id}/continuity", response_model=ContinuityReport)
def project_continuity(project_id: str, svc: ProjectService = Depends(get_service)):
    try:
        return _continuity_report(svc.continuity_findings(project_id))
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.get("/projects/{project_id}/shape", response_model=BookShape)
def book_shape(project_id: str, svc: ProjectService = Depends(get_service)):
    """The shape of the book: per-chapter movement and any sagging runs."""
    try:
        return svc.book_shape(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.get("/projects/{project_id}/continuity/exemptions",
            response_model=list[ContinuityExemption])
def list_exemptions(project_id: str, svc: ProjectService = Depends(get_service)):
    try:
        return svc.list_exemptions(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.post("/projects/{project_id}/continuity/exemptions",
             response_model=ContinuityExemption, status_code=201)
def exempt_finding(project_id: str, body: ExemptFinding,
                   svc: ProjectService = Depends(get_service)):
    """Mark a finding intentional so it stops being reported to anyone.

    The filter lives in the engine, so the Guardian stops raising it too - the
    AI must not argue with a call the writer has already made.
    """
    try:
        return svc.exempt_finding(project_id, body.key, body.reason)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/projects/{project_id}/continuity/exemptions/{key:path}",
               status_code=204)
def unexempt_finding(project_id: str, key: str,
                     svc: ProjectService = Depends(get_service)):
    try:
        if not svc.unexempt_finding(project_id, key):
            raise HTTPException(status_code=404, detail=f"No exemption for '{key}'")
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.get("/projects/{project_id}/chapters/{number}/continuity", response_model=ContinuityReport)
def chapter_continuity(project_id: str, number: int, svc: ProjectService = Depends(get_service)):
    try:
        return _continuity_report(svc.continuity_findings(project_id, chapter=number))
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.post("/projects/{project_id}/characters", response_model=list[CharacterSummary], status_code=201)
def add_character(project_id: str, body: AddCharacter, svc: ProjectService = Depends(get_service)):
    try:
        return svc.add_character(project_id, body.name, body.role)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/projects/{project_id}/run", response_model=Job, status_code=202)
def run_phase(project_id: str, body: RunPhase, svc: ProjectService = Depends(get_service)):
    try:
        fn = svc.make_phase_job(project_id, body.stage, body.params)
        project_path = str(svc.project_path(project_id))
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        job_id = runner.submit(
            body.stage,
            fn,
            meta={"project_id": project_id, "project_path": project_path},
        )
    except ProjectJobBlocked as exc:
        raise _project_job_blocked_http() from exc
    return runner.get(job_id)


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str):
    job = runner.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/projects/{project_id}/export", response_class=PlainTextResponse)
def export_markdown(project_id: str, svc: ProjectService = Depends(get_service)):
    try:
        return svc.export_markdown(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.get("/projects/{project_id}/styles", response_model=StyleSheetOut)
def get_styles(project_id: str, svc: ProjectService = Depends(get_service)):
    """Named compile styles (P5.2), with defaults filled in."""
    try:
        return svc.get_styles(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.put("/projects/{project_id}/styles", response_model=StyleSheetOut)
def put_styles(project_id: str, body: StyleSheetOut,
               svc: ProjectService = Depends(get_service)):
    try:
        return svc.save_styles(project_id, body.model_dump())
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/projects/{project_id}/compile")
def compile_book(project_id: str, format: str = "html",
                 svc: ProjectService = Depends(get_service)):
    """Compile the whole manuscript through the stylesheet (P6)."""
    try:
        body, content_type, ext = svc.compile_book(project_id, format)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(
        content=body,
        media_type=content_type,
        headers={
            "Content-Disposition": _content_disposition(project_id, ext),
        },
    )


@router.get("/projects/{project_id}/statistics", response_model=ProjectStatistics)
def project_statistics(project_id: str, svc: ProjectService = Depends(get_service)):
    """Word frequency, echoes, reading time (PLAN.md P4 Style Curator)."""
    try:
        return ProjectStatistics(**svc.manuscript_statistics(project_id))
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


# ----- Tier 1: snapshots (version history) DB-backed

def _ensure_chapter_or_404(svc: ProjectService, project_id: str, number: int):
    try:
        svc.ensure_chapter(project_id, number)
    except (ProjectNotFound, ChapterNotFound) as e:
        raise _not_found(project_id, number, e)


@router.get("/projects/{project_id}/chapters/{number}/snapshots", response_model=list[SnapshotMeta])
def list_snapshots(project_id: str, number: int, svc: ProjectService = Depends(get_service)):
    _ensure_chapter_or_404(svc, project_id, number)
    return db.snapshots_list(project_id, number)


@router.post("/projects/{project_id}/chapters/{number}/snapshots", response_model=SnapshotMeta, status_code=201)
def create_snapshot(project_id: str, number: int, body: CreateSnapshot,
                    svc: ProjectService = Depends(get_service)):
    _ensure_chapter_or_404(svc, project_id, number)
    text = svc.get_final_text(project_id, number)
    if text is None:
        raise HTTPException(status_code=409, detail="No Final to snapshot yet.")
    return db.snapshot_create(project_id, number, text, body.label, "final")


@router.get("/projects/{project_id}/chapters/{number}/snapshots/{snap_id}", response_model=SnapshotText)
def get_snapshot(project_id: str, number: int, snap_id: str,
                 svc: ProjectService = Depends(get_service)):
    _ensure_chapter_or_404(svc, project_id, number)
    snap = db.snapshot_get(project_id, number, snap_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snap


@router.post("/projects/{project_id}/chapters/{number}/snapshots/{snap_id}/restore", response_model=FinalResult)
def restore_snapshot(project_id: str, number: int, snap_id: str,
                     svc: ProjectService = Depends(get_service)):
    _ensure_chapter_or_404(svc, project_id, number)
    snap = db.snapshot_get(project_id, number, snap_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    current = svc.get_final_text(project_id, number)
    if current is not None:
        db.snapshot_create(project_id, number, current, "Before restore", "final")
    try:
        wc, _receipt = svc._promote_api_candidate(
            project_id,
            number,
            snap.text,
            source="snapshot_restore",
            reason="snapshot_restore",
            decision_metadata={"snapshot_id": snap.id, "scope": "chapter.final"},
        )
    except (PromotionConflict, PromotionIntegrityFailure, PromotionUnavailable) as e:
        raise _promotion_http_error(e)
    return FinalResult(final=snap.text, word_count=wc)


@router.delete("/projects/{project_id}/chapters/{number}/snapshots/{snap_id}", status_code=204)
def delete_snapshot(project_id: str, number: int, snap_id: str,
                    svc: ProjectService = Depends(get_service)):
    _ensure_chapter_or_404(svc, project_id, number)
    if not db.snapshot_delete(project_id, number, snap_id):
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return Response(status_code=204)


# ----- Tier 1: comments / annotations DB-backed

@router.get("/projects/{project_id}/chapters/{number}/comments", response_model=list[Comment])
def list_comments(project_id: str, number: int, svc: ProjectService = Depends(get_service)):
    _ensure_chapter_or_404(svc, project_id, number)
    return db.comments_list(project_id, number)


@router.post("/projects/{project_id}/chapters/{number}/comments", response_model=Comment, status_code=201)
def add_comment(project_id: str, number: int, body: AddComment,
                svc: ProjectService = Depends(get_service)):
    _ensure_chapter_or_404(svc, project_id, number)
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="Comment body is required.")

    from_pos, to_pos = body.from_pos, body.to_pos
    anchor_status = "ok"
    # Pre-P1 (and quote-only) notes: try to locate the quote in the Final so the
    # comment becomes text-anchored. Failures are kept, flagged unresolved.
    if (from_pos is None or to_pos is None) and body.quote.strip():
        try:
            doc = svc.get_final_doc(project_id, number)
            span = richtext.find_quote(doc, body.quote)
            if span:
                from_pos, to_pos = span
            else:
                anchor_status = "unresolved"
        except (ProjectNotFound, ChapterNotFound):
            anchor_status = "unresolved"

    return db.comment_add(
        project_id, number, body.body, body.quote,
        from_pos=from_pos, to_pos=to_pos, anchor_status=anchor_status,
        persona=body.persona or "author",
    )


@router.patch("/projects/{project_id}/chapters/{number}/comments/{cid}", response_model=Comment)
def update_comment(project_id: str, number: int, cid: str, body: UpdateComment,
                   svc: ProjectService = Depends(get_service)):
    _ensure_chapter_or_404(svc, project_id, number)
    c = db.comment_update(project_id, number, cid, body.resolved)
    if c is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    return c


@router.delete("/projects/{project_id}/chapters/{number}/comments/{cid}", status_code=204)
def delete_comment(project_id: str, number: int, cid: str,
                   svc: ProjectService = Depends(get_service)):
    _ensure_chapter_or_404(svc, project_id, number)
    if not db.comment_delete(project_id, number, cid):
        raise HTTPException(status_code=404, detail="Comment not found")
    return Response(status_code=204)


@router.get("/projects/{project_id}/binder")
def binder_tree(project_id: str, svc: ProjectService = Depends(get_service)):
    """Nested document tree (PLAN.md P4). Flat chapter endpoints remain the writing path."""
    try:
        return svc.binder_tree(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.post("/projects/{project_id}/binder/move")
def binder_move(
    project_id: str,
    body: BinderMoveRequest,
    svc: ProjectService = Depends(get_service),
):
    """Reorder or reparent a binder node. Does not renumber chapters."""
    if not (body.node_id or "").strip():
        raise HTTPException(status_code=400, detail="node_id is required.")
    try:
        return svc.move_binder_node(
            project_id,
            body.node_id.strip(),
            body.parent_id,
            max(0, int(body.index)),
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/projects/{project_id}/binder/{node_id}")
def binder_patch(
    project_id: str,
    node_id: str,
    body: BinderPatchRequest,
    svc: ProjectService = Depends(get_service),
):
    """Update synopsis / title / label on a binder node (corkboard)."""
    try:
        return svc.patch_binder_node(
            project_id,
            node_id,
            body.model_dump(exclude_unset=True),
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/projects/{project_id}/chapters/{number}/synopsis/refresh",
    response_model=SynopsisRefreshResult,
)
def refresh_synopsis(
    project_id: str,
    number: int,
    svc: ProjectService = Depends(get_service),
):
    """Architect (or heuristic fallback) refreshes the corkboard synopsis."""
    try:
        return SynopsisRefreshResult(**svc.refresh_synopsis(project_id, number))
    except (ProjectNotFound, ChapterNotFound) as e:
        raise _not_found(project_id, number, e)
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/projects/{project_id}/outliner/metrics/refresh",
    response_model=OutlinerMetricsRefreshResult,
)
def refresh_outliner_metrics(
    project_id: str,
    chapter: int | None = None,
    svc: ProjectService = Depends(get_service),
):
    """Compute tension / emotional intensity / pacing for outliner columns."""
    try:
        return OutlinerMetricsRefreshResult(**svc.refresh_outliner_metrics(project_id, chapter))
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except ChapterNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


# --------------------------------------------------------------------------- media

def _media_out(m: db.Media) -> MediaOut:
    return MediaOut(
        id=m.id, project_id=m.project_id, filename=m.filename,
        content_type=m.content_type, size=m.size, width=m.width, height=m.height,
        kind=m.kind, alt=m.alt, url=f"/api/projects/{m.project_id}/media/{m.id}/raw",
        created_at=m.created_at,
    )


def _ensure_project_or_404(svc: ProjectService, project_id: str) -> None:
    try:
        svc.project_detail(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.get("/projects/{project_id}/media", response_model=list[MediaOut])
def list_media(project_id: str, kind: str | None = None,
               svc: ProjectService = Depends(get_service)):
    _ensure_project_or_404(svc, project_id)
    return [_media_out(m) for m in db.media_list(project_id, kind)]


@router.post("/projects/{project_id}/media", response_model=MediaOut, status_code=201)
async def upload_media(project_id: str,
                       file: UploadFile = File(...),
                       kind: str = Form("general"),
                       alt: str = Form(""),
                       svc: ProjectService = Depends(get_service),
                       store: media_lib.MediaStore = Depends(get_media_store)):
    _ensure_project_or_404(svc, project_id)
    data = await file.read()
    try:
        ext = media_lib.validate(data, file.content_type or "")
    except media_lib.MediaError as e:
        raise HTTPException(status_code=e.status, detail=str(e))

    sha = media_lib.digest(data)
    width, height = media_lib.dimensions(data)
    store.put(project_id, sha, ext, data)
    row = db.media_add(
        project_id=project_id, sha=sha, ext=ext,
        filename=media_lib.clean_filename(file.filename or ""),
        content_type=file.content_type or "", size=len(data),
        width=width, height=height, kind=kind, alt=alt,
    )
    return _media_out(row)


@router.get("/projects/{project_id}/media/{media_id}/raw")
def get_media_raw(project_id: str, media_id: str,
                  store: media_lib.MediaStore = Depends(get_media_store)):
    m = db.media_get(project_id, media_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Media not found")
    data = store.read(project_id, m.sha, m.ext)
    if data is None:
        raise HTTPException(status_code=404, detail="Media blob missing")
    # Content-addressed, so the bytes at this id can never change.
    return Response(
        content=data,
        media_type=m.content_type or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.delete("/projects/{project_id}/media/{media_id}", status_code=204)
def delete_media(project_id: str, media_id: str,
                 svc: ProjectService = Depends(get_service),
                 store: media_lib.MediaStore = Depends(get_media_store)):
    _ensure_project_or_404(svc, project_id)
    m = db.media_delete(project_id, media_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Media not found")
    store.delete(project_id, m.sha, m.ext)
    return Response(status_code=204)


@router.patch("/projects/{project_id}/media/{media_id}", response_model=MediaOut)
def patch_media(
    project_id: str,
    media_id: str,
    body: UpdateMedia,
    svc: ProjectService = Depends(get_service),
):
    """Update caption / kind (research moodboard notes)."""
    _ensure_project_or_404(svc, project_id)
    m = db.media_update(
        project_id, media_id,
        alt=body.alt, kind=body.kind,
    )
    if m is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return _media_out(m)


# -------------------------------------------------------------------------- covers

def _cover_project(svc: ProjectService, project_id: str) -> Path:
    try:
        return svc.project_path(project_id)
    except ProjectNotFound as exc:
        raise HTTPException(
            status_code=404, detail=f"Project '{project_id}' not found"
        ) from exc


def _cover_store(svc: ProjectService, project_id: str):
    from core.cover_store import CoverConflict, CoverStore

    return CoverStore(_cover_project(svc, project_id))


def _cover_out(project_id: str, cover_set, *, active_revision: int = 0) -> dict:
    body = cover_set.to_dict()
    body["active_revision"] = active_revision
    candidates = []
    for candidate in body["candidates"]:
        item = dict(candidate)
        media_id = item.get("media_id")
        item["url"] = (
            f"/api/projects/{project_id}/media/{media_id}/raw" if media_id else None
        )
        candidates.append(item)
    body["candidates"] = candidates
    return body


def _direction_out(direction, *, brief: dict | None = None) -> dict:
    body = direction.to_dict()
    if brief:
        body["brief"] = brief
    return body


def _cover_error(exc: Exception) -> HTTPException:
    from core.cover_store import CoverConflict
    from core.image_client import ImageClientError

    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CoverConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ImageClientError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/projects/{project_id}/covers/story-facts")
def get_cover_story_facts(
    project_id: str,
    svc: ProjectService = Depends(get_service),
):
    from core.cover_handoff import resolve_cover_brief_v2
    from core.cover_story_facts import response_payload
    from core.cover_store import CoverConflict

    project = _cover_project(svc, project_id)
    try:
        return response_payload(resolve_cover_brief_v2(project))
    except CoverConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="Project Prompt is missing") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/projects/{project_id}/covers/story-facts")
def put_cover_story_facts(
    project_id: str,
    body: CoverStoryFactsUpdate,
    svc: ProjectService = Depends(get_service),
):
    from core.cover_story_facts import (
        confirm_missing_story_facts,
        response_payload,
    )
    from core.cover_store import CoverConflict

    project = _cover_project(svc, project_id)
    try:
        confirmed = confirm_missing_story_facts(
            project,
            expected_revision_sha256=body.expected_revision_sha256,
            characters=[
                item.model_dump(exclude_none=True) for item in body.characters
            ],
            primary_spaces=body.primary_spaces,
        )
        return response_payload(confirmed)
    except CoverConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="Project Prompt is missing") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/projects/{project_id}/covers/directions",
    status_code=201,
)
def create_cover_direction(
    project_id: str,
    body: CoverDirectionCreate,
    svc: ProjectService = Depends(get_service),
    director=Depends(get_cover_art_director),
):
    from core.cover_director import CoverDirectionError
    from core.cover_design import collect_visual_evidence
    from core.cover_handoff import resolve_cover_brief_v2
    from core.cover_models_v2 import ArtDirectionSet, CoverBriefV2
    from core.cover_novelty import recent_direction_fingerprints
    from core.cover_validator import validate_direction
    from core.cover_store import CoverConflict, CoverStore

    project = _cover_project(svc, project_id)
    try:
        recent_fingerprints = recent_direction_fingerprints(
            svc.base_dir, exclude_project_id=project_id,
        )
        if (body.brief is None) != (body.direction is None):
            raise ValueError("Cover brief and structured direction must be supplied together")
        if body.brief is None:
            brief = resolve_cover_brief_v2(project)
            pending = brief.pending_critical_assumptions()
            if pending:
                raise CoverConflict(
                    "Cover story facts need confirmation before art direction: "
                    + ", ".join(item.field for item in pending)
                )
            evidence_ledger = collect_visual_evidence(project, brief)
            direction = director.plan(
                brief,
                count=body.count,
                evidence_ledger=evidence_ledger,
                recent_fingerprints=recent_fingerprints,
            )
        else:
            source_sha = body.source_prompt_sha256 or hashlib.sha256(
                json.dumps(body.brief, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            brief = CoverBriefV2.from_dict(
                body.brief,
                source_prompt_sha256=source_sha,
                foundation_sha256=body.foundation_sha256,
            )
            assert body.direction is not None
            direction = ArtDirectionSet.from_dict(
                body.direction,
                brief_sha256=brief.source_prompt_sha256,
            )
        findings = validate_direction(
            brief, direction,
            recent_fingerprints=(
                recent_fingerprints
                if direction.profile_version.casefold().startswith(("cover-profiles.v4", "cover-profiles.v5", "cover-profiles.v6", "cover-profiles.v7", "cover-profiles.v8", "cover-profiles.v9"))
                else ()
            ),
        )
        if findings:
            raise ValueError(
                "Cover direction validation failed: "
                + "; ".join(
                    f"{item.code}[{item.evidence}]" if item.evidence else item.code
                    for item in findings
                )
            )
        created = CoverStore(project).create_direction(direction, brief=brief.to_dict())
    except CoverConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CoverDirectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="Project Prompt is missing") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _direction_out(created, brief=CoverStore(project).load_direction_brief(created.direction_id))


@router.get("/projects/{project_id}/covers/directions")
def list_cover_directions(project_id: str, svc: ProjectService = Depends(get_service)):
    store = _cover_store(svc, project_id)
    return [
        _direction_out(direction, brief=store.load_direction_brief(direction.direction_id))
        for direction in reversed(store.list_directions())
    ]


@router.post(
    "/projects/{project_id}/covers/directions/{direction_id}/approve",
)
def approve_cover_direction(
    project_id: str,
    direction_id: str,
    body: CoverDirectionApproval,
    svc: ProjectService = Depends(get_service),
):
    from core.cover_store import CoverConflict, CoverStore

    project = _cover_project(svc, project_id)
    try:
        approved = CoverStore(project).approve_direction(
            direction_id,
            expected_brief_sha256=body.expected_brief_sha256,
            approved_direction_sha256=body.approved_direction_sha256,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc
    return _direction_out(approved, brief=CoverStore(project).load_direction_brief(direction_id))


@router.post(
    "/projects/{project_id}/covers/generate",
    response_model=Job,
    status_code=202,
)
def generate_covers(
    project_id: str,
    body: CoverGenerateRequest,
    svc: ProjectService = Depends(get_service),
    covers: CoverService = Depends(get_cover_service),
):
    from core.cover_models import CoverBrief, CoverConcept
    from core.cover_models_v2 import CoverBriefV2
    from core.cover_design import collect_visual_evidence
    from core.cover_handoff import build_cover_concepts, resolve_cover_brief
    from core.cover_prompt_compiler import COMPILER_VERSION, scene_to_cover_concept
    from core.cover_store import CoverConflict, CoverStore
    from core.cover_validator import validate_direction

    project = _cover_project(svc, project_id)
    compiler_version = ""
    try:
        if body.direction_id:
            if body.concepts is not None:
                raise ValueError(
                    "v2 cover generation requires a direction_id without concepts"
                )
            direction_store = CoverStore(project)
            direction = direction_store.require_latest_direction(body.direction_id)
            stored_payload = direction_store.load_direction_brief(body.direction_id)
            if not stored_payload:
                raise HTTPException(
                    status_code=409,
                    detail="Cover direction has no story-facts snapshot",
                )
            stored_brief = CoverBriefV2.from_dict(
                stored_payload,
                source_prompt_sha256=str(
                    stored_payload.get("source_prompt_sha256") or direction.brief_sha256
                ),
                foundation_sha256=str(stored_payload.get("foundation_sha256") or ""),
            )
            prompt_path = project / "outputs" / "input" / "prompt.md"
            if prompt_path.is_file():
                from core.cover_handoff import resolve_cover_brief_v2

                brief = resolve_cover_brief_v2(project)
            elif body.brief is not None:
                source_sha = body.source_prompt_sha256 or hashlib.sha256(
                    json.dumps(body.brief, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
                brief = CoverBriefV2.from_dict(
                    body.brief,
                    source_prompt_sha256=source_sha,
                    foundation_sha256=body.foundation_sha256,
                )
            else:
                brief = stored_brief
            if stored_brief.source_prompt_sha256 != direction.brief_sha256:
                raise HTTPException(
                    status_code=409,
                    detail="Cover direction story-facts snapshot is invalid",
                )
            if direction.brief_sha256 != brief.source_prompt_sha256:
                direction_store.mark_direction_stale(
                    body.direction_id, brief.source_prompt_sha256
                )
                raise HTTPException(
                    status_code=409,
                    detail="Cover direction is stale for the current story facts",
                )
            if direction.evidence_ledger is not None:
                current_ledger = collect_visual_evidence(project, brief)
                if (
                    current_ledger.source_bundle_sha256
                    != direction.evidence_ledger.source_bundle_sha256
                ):
                    direction_store.mark_direction_stale(body.direction_id, force=True)
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Cover direction is stale because the prompt, preface, publication copy, "
                            "story state, or final manuscript changed"
                        ),
                    )
            if direction.status != "approved":
                raise HTTPException(
                    status_code=409,
                    detail="Cover direction must be approved before generation",
                )
            if not body.approved_direction_sha256:
                raise HTTPException(
                    status_code=409,
                    detail="Cover direction approval hash is required",
                )
            if body.approved_direction_sha256 != direction.direction_sha256:
                raise HTTPException(status_code=409, detail="Cover direction approval hash mismatch")
            findings = validate_direction(brief, direction)
            if findings:
                raise HTTPException(status_code=409, detail="Cover direction is stale or invalid")
            concepts = [
                scene_to_cover_concept(
                    brief,
                    plan,
                    visual_identity=direction.visual_identity,
                    evidence_ledger=direction.evidence_ledger,
                    conflict_contract=direction.core_conflict_visual_contract,
                )
                for plan in direction.plans
            ]
            brief.validate_concepts(concepts)
            compiler_version = COMPILER_VERSION
        else:
            if (body.brief is None) != (body.concepts is None):
                raise ValueError("Cover brief and concepts must be supplied together")
            if body.brief is not None and body.concepts is not None:
                source_sha = body.source_prompt_sha256 or hashlib.sha256(
                    json.dumps(body.brief, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
                brief = CoverBrief.from_dict(
                    body.brief,
                    source_prompt_sha256=source_sha,
                    foundation_sha256=body.foundation_sha256,
                )
                concepts = [CoverConcept.from_dict(item) for item in body.concepts]
            else:
                prompt_path = project / "outputs" / "input" / "prompt.md"
                if not prompt_path.is_file():
                    raise ValueError(
                        "Project Prompt is missing; generate covers with ./deploy.sh novel-cover PROMPT"
                    )
                brief = resolve_cover_brief(project)
                if isinstance(brief, CoverBriefV2):
                    raise HTTPException(
                        status_code=409,
                        detail="Versioned cover facts require an approved art direction before generation",
                    )
                count = body.count
                if count is None:
                    from core.studio_settings import resolve_cover_settings

                    count = resolve_cover_settings().count
                concepts = build_cover_concepts(brief, count=count)
            brief.validate_concepts(concepts)
    except CoverConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        job_id = runner.submit(
            "cover.generate",
            lambda: covers.generate(
                project_id, project, brief, concepts, compiler_version=compiler_version,
            ),
            meta={
                "project_id": project_id,
                "project_path": str(project),
                "direction_id": body.direction_id,
            },
        )
    except ProjectJobBlocked as exc:
        raise _project_job_blocked_http() from exc
    return runner.get(job_id)


@router.get("/projects/{project_id}/covers")
def list_covers(project_id: str, svc: ProjectService = Depends(get_service)):
    store = _cover_store(svc, project_id)
    active_revision = store.active().revision
    return [
        _cover_out(project_id, item, active_revision=active_revision)
        for item in reversed(store.list())
    ]


@router.get("/projects/{project_id}/covers/{cover_set_id}")
def get_cover(
    project_id: str,
    cover_set_id: str,
    svc: ProjectService = Depends(get_service),
):
    try:
        cover_set = _cover_store(svc, project_id).load(cover_set_id)
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc
    if cover_set.project_id != project_id:
        raise HTTPException(status_code=404, detail="Cover set not found")
    active_revision = _cover_store(svc, project_id).active().revision
    return _cover_out(project_id, cover_set, active_revision=active_revision)


@router.get("/projects/{project_id}/covers/{cover_set_id}/quality")
def get_cover_quality(
    project_id: str,
    cover_set_id: str,
    svc: ProjectService = Depends(get_service),
):
    """Return advisory quality reports without changing candidate state."""
    try:
        cover_set = _cover_store(svc, project_id).load(cover_set_id)
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc
    if cover_set.project_id != project_id:
        raise HTTPException(status_code=404, detail="Cover set not found")
    reports = []
    for candidate in cover_set.candidates:
        report = dict(candidate.quality_report or {})
        reports.append({
            "candidate_id": candidate.candidate_id,
            "status": report.get("status", "human_review_required"),
            "report": report,
            "attempt_count": len(candidate.attempt_history),
        })
    return {
        "cover_set_id": cover_set.cover_set_id,
        "revision": cover_set.revision,
        "status": cover_set.status,
        "reports": reports,
    }


@router.post(
    "/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/select"
)
def select_cover_candidate(
    project_id: str,
    cover_set_id: str,
    candidate_id: str,
    body: CoverCandidateMutation,
    svc: ProjectService = Depends(get_service),
    covers: CoverService = Depends(get_cover_mutation_service),
):
    project = _cover_project(svc, project_id)
    try:
        cover_set = _cover_store(svc, project_id).load(cover_set_id)
        if cover_set.project_id != project_id:
            raise FileNotFoundError("Cover set not found")
        selected = covers.select_candidate(
            project,
            cover_set_id,
            candidate_id,
            expected_revision=body.expected_revision,
            expected_active_revision=body.expected_active_revision,
            confirm_stale=body.confirm_stale,
        )
    except (FileNotFoundError, ValueError, CoverServiceError) as exc:
        raise _cover_error(exc) from exc
    active_revision = _cover_store(svc, project_id).active().revision
    return _cover_out(project_id, selected, active_revision=active_revision)


@router.post(
    "/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/reject"
)
def reject_cover_candidate(
    project_id: str,
    cover_set_id: str,
    candidate_id: str,
    body: CoverCandidateMutation,
    svc: ProjectService = Depends(get_service),
    covers: CoverService = Depends(get_cover_mutation_service),
):
    project = _cover_project(svc, project_id)
    try:
        rejected = covers.reject_candidate(
            project,
            cover_set_id,
            candidate_id,
            expected_revision=body.expected_revision,
        )
    except (FileNotFoundError, ValueError, CoverServiceError) as exc:
        raise _cover_error(exc) from exc
    active_revision = _cover_store(svc, project_id).active().revision
    return _cover_out(project_id, rejected, active_revision=active_revision)


@router.post(
    "/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/retry",
    response_model=Job,
    status_code=202,
)
def retry_cover_candidate(
    project_id: str,
    cover_set_id: str,
    candidate_id: str,
    body: CoverCandidateMutation,
    svc: ProjectService = Depends(get_service),
    covers: CoverService = Depends(get_cover_service),
):
    project = _cover_project(svc, project_id)
    try:
        current = _cover_store(svc, project_id).load(cover_set_id)
    except (FileNotFoundError, ValueError) as exc:
        raise _cover_error(exc) from exc
    if current.project_id != project_id:
        raise HTTPException(status_code=404, detail="Cover set not found")
    if body.repair_codes:
        candidate = next(
            (item for item in current.candidates if item.candidate_id == candidate_id),
            None,
        )
        if candidate is None:
            raise HTTPException(status_code=404, detail="Cover candidate not found")
        report = candidate.quality_report or {}
        allowed = {str(item) for item in report.get("repair_codes") or ()}
        requested = {str(item).strip() for item in body.repair_codes if str(item).strip()}
        if not requested or not requested.issubset(allowed):
            raise HTTPException(
                status_code=400,
                detail="Retry repair codes must be reported by the candidate quality report",
            )
    try:
        job_id = runner.submit(
            "cover.retry",
            lambda: covers.retry_candidate(
                project_id,
                project,
                cover_set_id,
                candidate_id,
                expected_revision=body.expected_revision,
                repair_codes=body.repair_codes,
            ),
            meta={
                "project_id": project_id,
                "project_path": str(project),
                "cover_set_id": cover_set_id,
            },
        )
    except ProjectJobBlocked as exc:
        raise _project_job_blocked_http() from exc
    return runner.get(job_id)


@router.get("/projects/{project_id}/deliverables/package")
def download_delivery_package(
    project_id: str,
    svc: ProjectService = Depends(get_service),
):
    path = _cover_project(svc, project_id) / "outputs/deliverables/book-package.zip"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Delivery package not found")
    return Response(
        content=path.read_bytes(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="book-package.zip"'},
    )


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def project_detail(project_id: str, svc: ProjectService = Depends(get_service)):
    try:
        return svc.project_detail(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.get("/projects/{project_id}/chapters", response_model=list[ChapterSummary])
def list_chapters(project_id: str, svc: ProjectService = Depends(get_service)):
    try:
        return svc.list_chapters(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.get("/projects/{project_id}/chapters/{number}", response_model=ChapterDetail)
def chapter_detail(project_id: str, number: int, svc: ProjectService = Depends(get_service)):
    try:
        return svc.chapter_detail(project_id, number)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except ChapterNotFound:
        raise HTTPException(status_code=404, detail=f"Chapter {number} not found")


@router.get("/projects/{project_id}/characters", response_model=list[CharacterSummary])
def list_characters(project_id: str, svc: ProjectService = Depends(get_service)):
    try:
        return svc.list_characters(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.get("/projects/{project_id}/codex", response_model=list[CodexEntryOut])
def list_codex(project_id: str, entry_type: str | None = None,
               svc: ProjectService = Depends(get_service)):
    try:
        return svc.list_codex(project_id, entry_type)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/projects/{project_id}/codex/proposals",
            response_model=list[CodexProposal])
def codex_proposals(project_id: str, min_mentions: int = 3, limit: int = 60,
                    svc: ProjectService = Depends(get_service)):
    """Codex candidates found in the manuscript (PLAN.md P2.2).

    Read-only. Accepting one is an ordinary POST to /codex, so nothing here can
    write to the world model without a human deciding to.
    """
    try:
        return svc.codex_proposals(project_id, min_mentions=min_mentions, limit=limit)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/projects/{project_id}/search", response_model=list[SearchHit])
def search_project(project_id: str, q: str = "", limit: int = 24,
                   svc: ProjectService = Depends(get_service)):
    """Keyword search over Codex, chapters, and relationships (no vectors)."""
    try:
        return svc.search(project_id, q, limit=limit)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.get("/projects/{project_id}/collections", response_model=list[CollectionOut])
def list_collections(project_id: str, svc: ProjectService = Depends(get_service)):
    try:
        return svc.list_collections(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.post("/projects/{project_id}/collections", response_model=list[CollectionOut], status_code=201)
def create_collection(project_id: str, body: CreateCollection,
                      svc: ProjectService = Depends(get_service)):
    try:
        return svc.add_collection(
            project_id, name=body.name, query=body.query,
            kinds=body.kinds, notes=body.notes,
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/projects/{project_id}/collections/{collection_id}", status_code=204)
def delete_collection(project_id: str, collection_id: str,
                      svc: ProjectService = Depends(get_service)):
    try:
        svc.delete_collection(project_id, collection_id)
        return Response(status_code=204)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/projects/{project_id}/collections/{collection_id}/results", response_model=list[SearchHit])
def collection_results(project_id: str, collection_id: str, limit: int = 40,
                       svc: ProjectService = Depends(get_service)):
    try:
        return svc.collection_results(project_id, collection_id, limit=limit)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/projects/{project_id}/codex", response_model=list[CodexEntryOut], status_code=201)
def add_codex_entry(project_id: str, body: AddCodexEntry,
                    svc: ProjectService = Depends(get_service)):
    try:
        return svc.add_codex_entry(
            project_id, body.entry_type, body.name,
            summary=body.summary, notes=body.notes, role=body.role, tags=body.tags,
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/projects/{project_id}/codex/{entry_id}", response_model=CodexEntryOut)
def update_codex_entry(project_id: str, entry_id: str, body: UpdateCodexEntry,
                       svc: ProjectService = Depends(get_service)):
    """Edit an existing Codex entry.

    Only the fields present in the request are applied, so editing one thing
    cannot blank the others.
    """
    try:
        return svc.update_codex_entry(
            project_id, entry_id, body.model_dump(exclude_unset=True),
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/projects/{project_id}/codex/{entry_id}/portrait", response_model=CodexEntryOut)
def set_codex_portrait(project_id: str, entry_id: str, body: SetPortrait,
                       svc: ProjectService = Depends(get_service)):
    try:
        return svc.set_portrait(project_id, entry_id, body.media_id, body.entry_type)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/projects/{project_id}/relationships", response_model=list[RelationshipOut])
def list_relationships(project_id: str, entry_id: str | None = None,
                       svc: ProjectService = Depends(get_service)):
    try:
        return svc.list_relationships(project_id, entry_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


@router.post("/projects/{project_id}/relationships", response_model=list[RelationshipOut], status_code=201)
def add_relationship(project_id: str, body: AddRelationship,
                     svc: ProjectService = Depends(get_service)):
    try:
        return svc.add_relationship(
            project_id, body.source_id, body.target_id, body.label,
            notes=body.notes, directed=body.directed, since_chapter=body.since_chapter,
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/projects/{project_id}/relationships/{edge_id}", status_code=204)
def delete_relationship(project_id: str, edge_id: str, svc: ProjectService = Depends(get_service)):
    try:
        svc.delete_relationship(project_id, edge_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(status_code=204)


@router.get("/projects/{project_id}/state")
def raw_state(project_id: str, svc: ProjectService = Depends(get_service)):
    try:
        return svc.raw_state(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


# ----- M2: pipeline stages + editable Final

def _not_found(project_id: str, number: int, e: Exception):
    if isinstance(e, ProjectNotFound):
        return HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return HTTPException(status_code=404, detail=f"Chapter {number} not found")


@router.get("/projects/{project_id}/chapters/{number}/stages", response_model=ChapterStages)
def chapter_stages(project_id: str, number: int, svc: ProjectService = Depends(get_service)):
    try:
        return svc.chapter_stages(project_id, number)
    except (ProjectNotFound, ChapterNotFound) as e:
        raise _not_found(project_id, number, e)


@router.get(
    "/projects/{project_id}/chapters/{number}/stages/diff",
    response_model=StageDiff,
)
def chapter_stage_diff(
    project_id: str,
    number: int,
    from_stage: str = "draft",
    to_stage: str = "revised",
    svc: ProjectService = Depends(get_service),
):
    """Compare two pipeline stages (P3.2 provenance ribbon)."""
    try:
        return svc.stage_diff(project_id, number, from_stage, to_stage)
    except (ProjectNotFound, ChapterNotFound) as e:
        raise _not_found(project_id, number, e)
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/projects/{project_id}/chapters/{number}/stages/{stage}/review",
    response_model=StageReviewResult,
)
def review_stage(
    project_id: str,
    number: int,
    stage: str,
    body: StageReviewRequest,
    svc: ProjectService = Depends(get_service),
):
    """Accept or reject an AI draft/revised stage (P3.3)."""
    try:
        result = svc.review_stage(project_id, number, stage, body.decision)
        return StageReviewResult(**result)
    except (ProjectNotFound, ChapterNotFound) as e:
        raise _not_found(project_id, number, e)
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (PromotionConflict, PromotionIntegrityFailure, PromotionUnavailable) as e:
        raise _promotion_http_error(e)


@router.post("/projects/{project_id}/chapters/{number}/final/promote", response_model=FinalResult)
def promote_final(project_id: str, number: int, force: bool = False,
                  svc: ProjectService = Depends(get_service)):
    try:
        text = svc.promote_final(project_id, number, force=force)
        return FinalResult(final=text, word_count=len(text.split()))
    except (ProjectNotFound, ChapterNotFound) as e:
        raise _not_found(project_id, number, e)
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NoSourceArtifact:
        raise HTTPException(
            status_code=409,
            detail="Nothing to promote no draft or revised text exists yet.",
        )
    except (PromotionConflict, PromotionIntegrityFailure, PromotionUnavailable) as e:
        raise _promotion_http_error(e)


@router.put("/projects/{project_id}/chapters/{number}/final", response_model=FinalResult)
def save_final(project_id: str, number: int, body: FinalSave,
               svc: ProjectService = Depends(get_service)):
    try:
        wc = svc.save_final(project_id, number, body.text)
        text = svc.get_final_text(project_id, number) or ""
        return FinalResult(final=text, word_count=wc)
    except (ProjectNotFound, ChapterNotFound) as e:
        raise _not_found(project_id, number, e)
    except (PromotionConflict, PromotionIntegrityFailure, PromotionUnavailable) as e:
        raise _promotion_http_error(e)


@router.post(
    "/projects/{project_id}/chapters/{number}/continue",
    response_model=ContinueResult,
)
def continue_paragraph(project_id: str, number: int, body: ContinueParagraph,
                       svc: ProjectService = Depends(get_service)):
    """Chat: author says what should happen next; Scribe returns one paragraph."""
    try:
        result = svc.continue_paragraph(project_id, number, body.instruction)
        return ContinueResult(**result)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except ChapterNotFound:
        raise HTTPException(status_code=404, detail=f"Chapter {number} not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/projects/{project_id}/chapters/{number}/consequence/preview",
    response_model=ConsequencePreview,
)
def consequence_preview(project_id: str, number: int, body: ConsequencePreviewRequest,
                        svc: ProjectService = Depends(get_service)):
    """Rewrite a selection and preview deterministic + predicted story ripple."""
    try:
        result = svc.preview_consequence(
            project_id, number, body.selection, body.instruction,
            before_context=body.before_context, after_context=body.after_context,
        )
        return ConsequencePreview(**result)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except ChapterNotFound:
        raise HTTPException(status_code=404, detail=f"Chapter {number} not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/projects/{project_id}/chapters/{number}/consequence/accept",
    response_model=ConsequenceAcceptResult,
)
def consequence_accept(project_id: str, number: int, body: ConsequenceAccept,
                       svc: ProjectService = Depends(get_service)):
    """Accept rewrite into Final and apply world-state delta together."""
    try:
        result = svc.accept_consequence(
            project_id, number, body.preview_id, body.rewritten, body.doc,
            state_delta=body.state_delta,
        )
        return ConsequenceAcceptResult(**result)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except ChapterNotFound:
        raise HTTPException(status_code=404, detail=f"Chapter {number} not found")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (PromotionConflict, PromotionIntegrityFailure, PromotionUnavailable) as e:
        raise _promotion_http_error(e)


@router.get("/projects/{project_id}/chapters/{number}/final/doc", response_model=FinalDoc)
def get_final_doc(project_id: str, number: int, svc: ProjectService = Depends(get_service)):
    """Final as a ProseMirror document. Pre-P1 finals convert from markdown on
    read; nothing is rewritten until the writer saves."""
    try:
        d = svc.get_final_doc(project_id, number)
    except (ProjectNotFound, ChapterNotFound) as e:
        raise _not_found(project_id, number, e)
    markdown = richtext.to_markdown(d)
    return FinalDoc(doc=d, markdown=markdown, word_count=richtext.word_count(d))


@router.put("/projects/{project_id}/chapters/{number}/final/doc", response_model=FinalDoc)
def save_final_doc(project_id: str, number: int, body: FinalDocSave,
                   svc: ProjectService = Depends(get_service)):
    try:
        svc.save_final_doc(project_id, number, body.doc)
    except (ProjectNotFound, ChapterNotFound) as e:
        raise _not_found(project_id, number, e)
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (PromotionConflict, PromotionIntegrityFailure, PromotionUnavailable) as e:
        raise _promotion_http_error(e)
    markdown = richtext.to_markdown(body.doc)
    return FinalDoc(doc=body.doc, markdown=markdown, word_count=richtext.word_count(body.doc))


@router.get(
    "/projects/{project_id}/chapters/{number}/quality",
    response_model=ChapterQualityOut,
)
def chapter_quality(
    project_id: str,
    number: int,
    svc: ProjectService = Depends(get_service),
):
    try:
        return ChapterQualityOut(**svc.quality_projection(project_id, number))
    except (ProjectNotFound, ChapterNotFound) as e:
        raise _not_found(project_id, number, e)


@router.get(
    "/projects/{project_id}/chapters/{number}/artifacts/revisions",
    response_model=list[ArtifactRevisionOut],
)
def chapter_artifact_revisions(
    project_id: str,
    number: int,
    svc: ProjectService = Depends(get_service),
):
    try:
        return [
            ArtifactRevisionOut(**row)
            for row in svc.quality_projection(project_id, number)["artifact_revisions"]
        ]
    except (ProjectNotFound, ChapterNotFound) as e:
        raise _not_found(project_id, number, e)


@router.get(
    "/projects/{project_id}/chapters/{number}/quality/receipts/{receipt_id}",
    response_model=PromotionReceiptOut,
)
def chapter_quality_receipt(
    project_id: str,
    number: int,
    receipt_id: str,
    svc: ProjectService = Depends(get_service),
):
    try:
        receipt = svc.quality_receipt(project_id, number, receipt_id)
    except (ProjectNotFound, ChapterNotFound) as e:
        raise _not_found(project_id, number, e)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Promotion receipt not found")
    return PromotionReceiptOut(**receipt)


# Shares the existing project mutation and tenancy guards; no H5 routes are changed.
from .method_routes import register_method_routes

register_method_routes(router, get_service)
