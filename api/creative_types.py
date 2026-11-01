"""Workspace-local creative references. Shares the application's local tenancy boundary."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from core.creative_types import (CreativeTypeError, CreativeTypeStore, DIMENSIONS, StrictModel,
                                 TypeRef, Stage, compile_snapshot, digest, encoded, validate_profile)
from core.novel_classification import catalog_payload
from . import tenancy
from .routes import get_service
from .services import ProjectService

router = APIRouter(prefix='/api/creative-types', tags=['creative-types'])


def store_for(projects: ProjectService) -> CreativeTypeStore:
    workspace = projects.workspace.id if projects.workspace else tenancy.DEFAULT_WORKSPACE_ID
    if not tenancy.valid_project_id(workspace):
        raise CreativeTypeError('工作区标识无效。', 404)
    return CreativeTypeStore(projects.root.resolve().parent / 'creative_types' / workspace)


def get_store(projects: ProjectService = Depends(get_service)) -> CreativeTypeStore:
    return store_for(projects)


class Mutation(StrictModel):
    revision: Annotated[int, Field(ge=0)] = 0
    operation_id: Annotated[str, Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')]
    profile: dict | None = None


class Preview(StrictModel):
    profile: dict | None = None
    refs: Annotated[list[TypeRef], Field(max_length=60)] = []
    stage: Stage = 'brainstorm'


def summary(record: dict) -> dict:
    active = next((v for v in record['versions'] if v['profile']['version'] == record['active_version']), None)
    p = active['profile'] if active else record['draft']
    return {k: record[k] for k in ('id', 'revision', 'status', 'origin', 'coverage', 'updated_at')} | {
        'name': p['name'], 'summary': p['summary'], 'dimension': p['dimension'], 'aliases': p['aliases'],
        'mapping_status': p['publication_mapping']['status'], 'active_version': record['active_version'],
        'publication_mapping': p['publication_mapping'], 'retired': record.get('retired', False),
        'source_sha256': active['sha256'] if active else None,
        'has_draft_changes': bool(active and digest(record['draft'] | {'version': p['version']}) != active['sha256'])}


@router.get('')
def list_types(include_retired: bool = False, store: CreativeTypeStore = Depends(get_store)):
    return {'items': [summary(r) for r in store.records().values() if include_retired or not r.get('retired')], 'dimensions': DIMENSIONS,
            'publication_catalog': catalog_payload()}


@router.post('', status_code=201)
def create_type(body: Mutation, store: CreativeTypeStore = Depends(get_store)):
    return store.mutate('create', None, revision=0, operation_id=body.operation_id, raw=body.profile)


@router.post('/preview')
def preview(body: Preview, store: CreativeTypeStore = Depends(get_store)):
    if body.profile is not None:
        if body.refs:
            raise HTTPException(422, '草稿预览和所选版本预览不能混合。')
        p = validate_profile(body.profile, enabled=True)
        rows = [{'profile': p, 'source_sha256': digest(p), 'role': 'unspecified'}]
        snapshot = {'schema': 'book-type-selection.v1', 'profiles': rows, 'snapshot_sha256': digest(rows)}
    else:
        snapshot = store.snapshot(body.refs)
    context = compile_snapshot(snapshot, body.stage)
    return {'context': context, 'sha256': digest(context), 'bytes': len(encoded(context)),
            'preview_only': True, 'source': 'draft' if body.profile else 'enabled_selection'}


@router.get('/{profile_id}')
def get_type(profile_id: str, store: CreativeTypeStore = Depends(get_store)):
    return store.get(profile_id)


@router.get('/{profile_id}/versions/{version}')
def get_version(profile_id: str, version: int, store: CreativeTypeStore = Depends(get_store)):
    record = store.get(profile_id)
    for saved in record['versions']:
        if saved['profile']['version'] == version:
            return saved
    raise HTTPException(404, '找不到此历史版本。')


@router.post('/{profile_id}/{action}')
def mutate_type(profile_id: str, action: Literal['draft', 'activate', 'disable'], body: Mutation,
                store: CreativeTypeStore = Depends(get_store)):
    return store.mutate(action, profile_id, revision=body.revision, operation_id=body.operation_id, raw=body.profile)
