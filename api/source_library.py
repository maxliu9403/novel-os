"""Source book intake and durable reading, scoped to the existing workspace."""

import json
import threading
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field

from core.creation_mode import SourceMode
from core.creative_types import StrictModel
from core.reference_assets import ReferenceAssets
from core.source_library import (
    MAX_EXPANDED,
    MAX_FILES,
    MAX_TEXT,
    MAX_UPLOAD,
    SourceLibrary,
    unpack_zip,
)
from core.source_study import SourceStudy
from core.source_versions import SourceVersions

from .creative_types import store_for as knowledge_store
from .jobs import runner
from .routes import get_service
from .services import ProjectService
from .source_route_status import design_summary

router = APIRouter(prefix="/api/source-books", tags=["source-books"])


def store_for(projects):
    root = knowledge_store(projects).root
    return SourceLibrary(root.parent.parent / "source_books" / root.name)


def get_store(projects: ProjectService = Depends(get_service)):
    return store_for(projects)


def state(library, identifier):
    study = SourceStudy(library, identifier)
    value = study.status()
    job_path = study.root / "job.json"
    if value["status"] in ("queued", "running") and not job_path.exists():
        age = (
            datetime.now(UTC) - datetime.fromisoformat(value["updated_at"])
        ).total_seconds()
        if age > 15:
            value = study.interrupt()
    if value["status"] in ("queued", "running") and job_path.exists():
        job_id = json.loads(job_path.read_text())["job_id"]
        job = runner.get(job_id)
        if job is None or job.get("status") != "running":
            value = study.interrupt()
    job = None
    if job_path.exists():
        job = runner.get(json.loads(job_path.read_text())["job_id"])
    if job and not value.get("started_at"):
        value["started_at"] = job.get("started_at")
        value["finished_at"] = job.get("finished_at")
    value.update(
        worker_active=bool(job and job.get("status") == "running"),
        cancel_requested=bool(job and job.get("cancel_requested")),
        checked_at=datetime.now(UTC).isoformat(),
    )
    return value


def public(library, manifest):
    return {
        k: manifest[k]
        for k in (
            "id",
            "source_sha256",
            "title",
            "book_id",
            "chapter_count",
            "supplement_count",
            "warnings",
            "scope",
        )
    } | {
        "study": state(library, manifest["id"]),
        "design": design_summary(library, manifest["id"], runner),
        "cover_url": f"/api/source-books/{manifest['id']}/cover"
        if manifest.get("cover_asset")
        else None,
    }


@router.get("")
def list_books(library: SourceLibrary = Depends(get_store)):
    return {"items": [public(library, m) for m in library.list()]}


@router.post("", status_code=201)
async def upload(
    files: list[UploadFile] = File(...), library: SourceLibrary = Depends(get_store)
):
    try:
        if len(files) == 1 and (files[0].filename or "").lower().endswith(".zip"):
            size = files[0].size
            if size is None or size > MAX_UPLOAD:
                raise ValueError("ZIP 上限为 128 MiB。")
            uploaded = unpack_zip(files[0].file)
        else:
            if len(files) > MAX_FILES:
                raise ValueError("最多 5000 个文件。")
            uploaded = {}
            total = 0
            for file in files:
                name = file.filename or ""
                limit = (
                    12 * 1024 * 1024
                    if name.lower().endswith((".png", ".jpg", ".jpeg"))
                    else MAX_TEXT
                )
                data = await file.read(limit + 1)
                total += len(data)
                if len(data) > limit or total > MAX_EXPANDED:
                    raise ValueError("资料超过文件或总容量限制。")
                if name in uploaded:
                    raise ValueError("文件路径重复。")
                uploaded[name] = data
        return public(library, library.import_files(uploaded))
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        for file in files:
            await file.close()


@router.get("/{identifier}")
def get_book(
    identifier: str,
    version: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
    library: SourceLibrary = Depends(get_store),
):
    try:
        manifest = library.get(identifier)
        versions = SourceVersions(library, identifier).summaries()
        selected = version or (versions[0]["analysis_sha256"] if versions else None)
        return {
            **public(library, manifest),
            "synopsis": manifest["synopsis"],
            "units": manifest["units"],
            "metadata": manifest["metadata"],
            "versions": versions,
            "result": SourceStudy(library, identifier).result(selected)
            if selected
            else None,
        }
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{identifier}/usage")
def usage(identifier: str, projects: ProjectService = Depends(get_service)):
    from .workshop import WorkshopService

    library = store_for(projects)
    try:
        library.get(identifier)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    sessions = [
        s
        for s in WorkshopService(projects).list_sessions(limit=100000)["sessions"]
        if (s.get("source") or {}).get("source_id") == identifier
    ]
    books = [
        p.model_dump(include={"id", "title", "status", "source"})
        for p in projects.list_projects()
        if p.source and p.source["source_id"] == identifier
    ]
    return {
        "sessions": sessions,
        "projects": books,
        "counts": {
            "discussions": len(sessions),
            "projects": len(books),
            "completed": sum(p["status"] == "completed" for p in books),
            "in_progress": sum(p["status"] != "completed" for p in books),
        },
    }


@router.get("/{identifier}/cover")
def cover(identifier: str, library: SourceLibrary = Depends(get_store)):
    try:
        manifest = library.get(identifier)
        asset = manifest.get("cover_asset")
        if not asset:
            raise ValueError("没有来源封面。")
        store = ReferenceAssets(library.path(identifier) / "images")
        return FileResponse(
            store.path(asset["asset_id"]), media_type=asset["content_type"]
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class StudyRequest(StrictModel):
    restart: bool = False


@router.post("/{identifier}/analyze", status_code=202)
def analyze(
    identifier: str,
    body: StudyRequest = Body(default_factory=StudyRequest),
    library: SourceLibrary = Depends(get_store),
):
    from core import provider_settings
    from core.llm_client import LLMClient
    from core.usage_tracking import usage_scope

    try:
        library.get(identifier)
        current = state(library, identifier)
        if current["status"] in ("running", "queued") or current.get("worker_active"):
            return current
        if (
            current["status"] == "complete"
            and not body.restart
            and SourceStudy(library, identifier).result().get("continuation_plan")
        ):
            return current
        route = provider_settings.resolve_text_route("workshop")
        binding = {
            k: route.get(k)
            for k in (
                "provider",
                "model",
                "reasoning_effort",
                "max_tokens",
                "timeout_seconds",
                "connection_id",
            )
        }
        from core.creative_types import digest

        binding["endpoint_sha256"] = digest(route.get("base_url") or "")
        study = SourceStudy(library, identifier)
        study.prepare(binding, restart=body.restart, with_plan=True)
        cancel = threading.Event()

        def operation():
            client = LLMClient(
                provider=route["provider"],
                model=route["model"] or None,
                base_url=route["base_url"] or None,
                api_key=route["api_key"] or None,
                max_tokens=route["max_tokens"],
                timeout_seconds=route["timeout_seconds"],
                reasoning_effort=route.get("reasoning_effort") or None,
                cancellation_event=cancel,
                diagnostic_callback=study.observe,
            )
            with usage_scope(library.path(identifier), "brainstorming"):
                return study.run(client.complete, cancel.is_set)

        try:
            job_id = runner.submit(
                "source.study",
                operation,
                unique_key=str(study.root),
                cancel_event=cancel,
                defer_cancel_completion=True,
                result_mapper=lambda _: {"source_id": identifier},
            )
        except Exception:
            study.interrupt()
            raise
        study.attach_job(job_id)
        return study.status()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{identifier}/cancel")
def cancel(identifier: str, library: SourceLibrary = Depends(get_store)):
    try:
        study = SourceStudy(library, identifier)
        path = study.root / "job.json"
        if path.exists():
            runner.cancel(json.loads(path.read_text())["job_id"])
        return state(library, identifier)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class SourceSelection(StrictModel):
    source_id: str = Field(pattern=r"^source-[0-9a-f]{64}$")
    analysis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: SourceMode
    use_cover: bool = True


def freeze(projects, selection: dict | None):
    try:
        return _freeze(projects, selection)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _freeze(projects, selection: dict | None):
    from core.creation_mode import source_policy

    if selection is None:
        return None
    selected = SourceSelection.model_validate(selection)
    library = store_for(projects)
    manifest = library.get(selected.source_id)
    result = SourceStudy(library, selected.source_id).result(selected.analysis_sha256)
    return {
        **selected.model_dump(),
        "title": manifest["title"],
        "source_sha256": manifest["source_sha256"],
        "analysis": result["analysis"],
        **(
            {
                "continuation_plan": result["continuation_plan"],
                "planning_status": "proposal_not_adopted",
            }
            if result.get("continuation_plan")
            else {}
        ),
        "coverage": result["coverage"],
        "cover_asset": manifest.get("cover_asset") if selected.use_cover else None,
        "rule": source_policy(selected.model_dump()),
    }


def carry_cover(projects, project, source):
    from core.creation_mode import cover_intent

    if not source or not source.get("cover_asset"):
        return
    library = store_for(projects)
    asset = source["cover_asset"]
    original = ReferenceAssets(library.path(source["source_id"]) / "images").read(
        asset["asset_id"]
    )
    store = ReferenceAssets(project / "outputs/private/references")
    saved = store.put(original, origin="source_book")
    store.select(
        saved["asset_id"],
        cover_intent(source["mode"]),
    )
