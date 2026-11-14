import os
import inspect
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db
from .media import LocalMediaStore
from .routes import router, get_media_store, get_service
from .project_reader import router as project_reader_router
from .project_story import router as project_story_router
from .services import (
    ProjectService,
    PromotionConflict,
    PromotionIntegrityFailure,
    PromotionUnavailable,
)
from .version import __version__
from .workshop import router as workshop_router
from .jobs import SessionJobBlocked


async def _session_job_blocked_response(_request, exc: SessionJobBlocked):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


#: Vite's default, and the next port it falls back to when that one is taken -
#: which is exactly when a developer hits an unexplained "Failed to fetch".
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
)


async def _promotion_error_response(_request, error: Exception) -> JSONResponse:
    if isinstance(error, PromotionUnavailable):
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Promotion outcome needs reconciliation; refresh chapter "
                    "quality before another mutation."
                )
            },
        )
    if isinstance(error, PromotionIntegrityFailure):
        detail = f"Promotion integrity conflict: {error}"
    else:
        detail = f"Promotion conflict: {error}"
    return JSONResponse(status_code=409, content={"detail": detail})


def _cors_origins() -> list[str]:
    """Browser origins allowed to call the API.

    Configurable because the origin was hardcoded to a single dev port: run the
    frontend anywhere else - a second checkout, a LAN address, a port Vite
    picked because 5173 was busy - and every request failed with a CORS error
    that says nothing about CORS.
    """
    raw = os.environ.get("NOVEL_OS_CORS_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or list(DEFAULT_CORS_ORIGINS)


def create_app(projects_root: Optional[Path] = None, db_url: Optional[str] = None,
               media_root: Optional[Path] = None) -> FastAPI:
    database_url = db_url or os.environ.get("NOVEL_OS_DB") or "sqlite:///./novel_os.db"
    db.configure(database_url)

    # Apply Studio LLM settings (if any) before first agent job.
    try:
        from core import provider_settings, studio_settings
        studio_settings.apply_to_environ(studio_settings.load_settings())
        provider_settings.apply_default_route_to_environ()
    except Exception:
        pass

    from .library_runtime import LibraryRuntime
    library_runtime = LibraryRuntime(database_url)

    @asynccontextmanager
    async def lifespan(application):
        # Discovery/backfill is a bounded background job, never a startup
        # barrier or fallback in GET /library. Resolve overrides for tests and
        # tenant-specific app factories just like the project routes do.
        provider = application.dependency_overrides.get(get_service, get_service)
        service = provider()
        if inspect.isawaitable(service):
            service = await service
        library_runtime.start(service)
        try:
            yield
        finally:
            library_runtime.stop()

    app = FastAPI(title="Novel OS API", version=__version__, lifespan=lifespan)
    app.state.library_runtime = library_runtime
    from core.creative_types import CreativeTypeError
    from .creative_types import router as creative_types_router
    async def creative_type_error(_request, error: CreativeTypeError):
        return JSONResponse(status_code=error.status, content={'detail': str(error)})
    app.add_exception_handler(CreativeTypeError, creative_type_error)
    app.add_exception_handler(SessionJobBlocked, _session_job_blocked_response)
    app.include_router(creative_types_router)
    from .system_prompts import router as system_prompts_router
    app.include_router(system_prompts_router)
    from .source_library import router as source_library_router
    app.include_router(source_library_router)
    from .source_routes import router as source_routes_router
    app.include_router(source_routes_router)
    from .workshop_source_handoff import router as workshop_source_handoff_router
    app.include_router(workshop_source_handoff_router)
    from .document_import import router as document_import_router
    app.include_router(document_import_router)
    for error_type in (
        PromotionConflict,
        PromotionIntegrityFailure,
        PromotionUnavailable,
    ):
        app.add_exception_handler(error_type, _promotion_error_response)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    from .cover_workbench import router as cover_workbench_router
    app.include_router(cover_workbench_router)
    from .cover_workbench_assets import router as cover_workbench_assets_router
    app.include_router(cover_workbench_assets_router)
    from .poster_studio import router as poster_studio_router
    app.include_router(poster_studio_router)
    from .poster_references import router as poster_references_router
    app.include_router(poster_references_router)
    app.include_router(project_reader_router)
    app.include_router(project_story_router)
    app.include_router(router)
    from .library_routes import router as library_router
    app.include_router(library_router)
    app.include_router(workshop_router)
    from .costs import router as costs_router
    app.include_router(costs_router)
    if projects_root is not None:
        app.dependency_overrides[get_service] = lambda: ProjectService(projects_root)
    if media_root is not None:
        app.dependency_overrides[get_media_store] = lambda: LocalMediaStore(media_root)
    return app


app = create_app()
