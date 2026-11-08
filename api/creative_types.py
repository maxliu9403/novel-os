"""Workspace-scoped Markdown Genre/Tag library. No model calls on edits."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field, StringConstraints

from core.creative_types import (
    CreativeTypeError,
    CreativeTypeStore,
    Identifier,
    Name,
    Stage,
    StrictModel,
    TypeRef,
    compile_snapshot,
    digest,
    encoded,
    parse_document,
    summary,
)
from core.knowledge_markdown import document_view, prepare_markdown

from . import tenancy
from .routes import get_service
from .services import ProjectService

router = APIRouter(prefix="/api/creative-types", tags=["creative-types"])


def store_for(projects: ProjectService) -> CreativeTypeStore:
    workspace = (
        projects.workspace.id if projects.workspace else tenancy.DEFAULT_WORKSPACE_ID
    )
    if not tenancy.valid_project_id(workspace):
        raise CreativeTypeError("工作区标识无效。", 404)
    return CreativeTypeStore(
        projects.root.resolve().parent / "creative_types" / workspace
    )


def get_store(projects: ProjectService = Depends(get_service)) -> CreativeTypeStore:
    return store_for(projects)


class Mutation(StrictModel):
    version: Annotated[int, Field(ge=1)] | None = None
    revision: Annotated[int, Field(ge=0)] = 0
    operation_id: Annotated[
        str, Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    ]
    markdown: (
        Annotated[
            str, Field(max_length=24576), StringConstraints(strip_whitespace=False)
        ]
        | None
    ) = None


class Preview(StrictModel):
    markdown: (
        Annotated[
            str, Field(max_length=24576), StringConstraints(strip_whitespace=False)
        ]
        | None
    ) = None
    refs: Annotated[list[TypeRef], Field(max_length=21)] = []
    stage: Stage = "brainstorm"


class DocumentIdentity(StrictModel):
    id: Identifier
    kind: Literal["genre", "tag"]
    name: Name
    genre_ids: Annotated[list[Identifier], Field(max_length=30)] = []


class PrepareMarkdown(StrictModel):
    markdown: Annotated[str, Field(max_length=24576), StringConstraints(strip_whitespace=False)]
    base_markdown: Annotated[str, Field(max_length=24576), StringConstraints(strip_whitespace=False)] | None = None
    identity: DocumentIdentity | None = None


@router.get("")
def list_types(store: CreativeTypeStore = Depends(get_store)):
    return {
        "items": [
            summary(r) for r in store.records().values() if not r.get("deleted_at")
        ]
    }


@router.get("/template")
def document_template(
    kind: Literal["genre", "tag"], id: str, name: str, genre_id: str | None = None
):
    result = prepare_markdown("", identity={
        "kind": kind, "id": id, "name": name,
        "genre_ids": [genre_id] if kind == "tag" and genre_id else [],
    })
    return {"markdown": result["markdown"]}


@router.post("/prepare")
def prepare_import(body: PrepareMarkdown):
    return prepare_markdown(
        body.markdown, base_markdown=body.base_markdown,
        identity=body.identity.model_dump() if body.identity else None,
    )


@router.post("", status_code=201)
def create_type(body: Mutation, store: CreativeTypeStore = Depends(get_store)):
    return store.mutate(
        "create",
        None,
        revision=0,
        operation_id=body.operation_id,
        markdown=body.markdown,
    )


@router.post("/preview")
def preview(body: Preview, store: CreativeTypeStore = Depends(get_store)):
    if body.markdown is not None:
        if body.refs:
            raise HTTPException(422, "草稿预览和所选版本预览不能混合。")
        doc = parse_document(body.markdown)
        return {
            "document": doc,
            "bytes": len(body.markdown.encode()),
            "preview_only": True,
        }
    snapshot = store.snapshot(body.refs)
    context = compile_snapshot(snapshot, body.stage)
    return {
        "context": context,
        "sha256": digest(context),
        "bytes": len(encoded(context)),
        "preview_only": True,
    }


@router.get("/{identifier}")
def get_type(identifier: str, store: CreativeTypeStore = Depends(get_store)):
    record = store.get(identifier)
    return {**record, "document": document_view(record["draft"])}


@router.get("/{identifier}/markdown")
def download_document(identifier: str, store: CreativeTypeStore = Depends(get_store)):
    record = store.get(identifier)
    return Response(
        record["draft"],
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{record["id"]}.md"'},
    )


@router.get("/{identifier}/versions/{version}")
def get_version(
    identifier: str, version: int, store: CreativeTypeStore = Depends(get_store)
):
    record = store.get(identifier, include_deleted=True)
    for saved in record["versions"]:
        if saved["version"] == version:
            return saved
    raise HTTPException(404, "找不到此历史版本。")


@router.post("/{identifier}/{action}")
def mutate_type(
    identifier: str,
    action: Literal["draft", "activate", "disable", "delete", "restore"],
    body: Mutation,
    store: CreativeTypeStore = Depends(get_store),
):
    record = store.mutate(
        action,
        identifier,
        revision=body.revision,
        operation_id=body.operation_id,
        markdown=body.markdown,
        version=body.version,
    )
    return {**record, "document": document_view(record["draft"])}
