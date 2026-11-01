"""User-owned creative guidance. No inference, publication mutation or story adoption.

Enabled versions and discussion snapshots are immutable content, not pointers to
editable drafts. Bundled references are a read-only initial catalog; the workspace
overrides an entry on its first explicit write. Reads never create files.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Annotated, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .novel_classification import PRIMARY_GENRES, STORY_TYPES, TONES, SETTINGS, CATALOG_VERSION

RESOURCE = Path(__file__).resolve().parents[1] / "resources/creative-types/v1.json"
CURATED_RESOURCE = RESOURCE.with_name('curated-v2.json')
PROFILE_LIMIT = 24 * 1024
CONTEXT_LIMIT = 96 * 1024
DIMENSIONS = {"marriage": "婚姻与亲密关系", "family": "家庭与代际", "growth": "成长与自我实现",
              "world": "类型与世界", "conflict": "冲突与成长", "relationship": "人物与关系",
              "tone": "情绪与文风", "audience": "读者定位"}
Stage = Literal["brainstorm", "story_design", "chapter_plan", "draft", "review"]
Text = Annotated[str, Field(min_length=1, max_length=1600)]
Identifier = Annotated[str, Field(pattern=r"^ct_[a-z0-9][a-z0-9_-]{1,76}$")]
ItemId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{1,79}$")]
TextList = Annotated[list[Text], Field(max_length=12)]


class CreativeTypeError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Guidance(StrictModel):
    id: ItemId
    text: Annotated[str, Field(max_length=1600)]
    importance: Literal["essential", "recommended", "optional"] = "recommended"
    stages: Annotated[list[Stage], Field(max_length=5)]


class Mechanism(StrictModel):
    id: ItemId
    name: Annotated[str, Field(max_length=120)]
    principle: Annotated[str, Field(max_length=1600)]
    importance: Literal["essential", "recommended", "optional"] = "recommended"
    stages: Annotated[list[Stage], Field(max_length=5)]
    trigger: TextList = []
    scope: TextList = []
    limits: TextList = []
    exceptions: TextList = []
    costs: TextList = []
    choices: TextList = []
    payoffs: TextList = []


class Applicability(StrictModel):
    readers: TextList = []
    markets: TextList = []
    languages: TextList = []
    lengths: TextList = []


class Combination(StrictModel):
    with_profile_id: Identifier
    opportunities: TextList = []
    tensions: TextList = []


class PublicationMapping(StrictModel):
    status: Literal["mapped", "unmapped"] = "unmapped"
    catalog_version: str = CATALOG_VERSION
    primary_genre_ids: Annotated[list[str], Field(max_length=4)] = []
    facet_ids: Annotated[list[str], Field(max_length=12)] = []


class Profile(StrictModel):
    schema_version: Literal["creative-type-profile.v1"] = Field(default="creative-type-profile.v1", alias='schema')
    profile_id: Identifier
    version: Annotated[int, Field(ge=1)] = 1
    name: Annotated[str, Field(max_length=100)] = ""
    aliases: Annotated[list[Annotated[str, Field(min_length=1, max_length=100)]], Field(max_length=20)] = []
    dimension: Literal["marriage", "family", "growth", "world", "conflict", "relationship", "tone", "audience"] = "world"
    summary: Annotated[str, Field(max_length=600)] = ""
    applicability: Applicability = Field(default_factory=Applicability)
    reader_promises: Annotated[list[Guidance], Field(max_length=12)] = []
    mechanisms: Annotated[list[Mechanism], Field(max_length=8)] = []
    character_dynamics: Annotated[list[Guidance], Field(max_length=12)] = []
    voice_guidance: Annotated[list[Guidance], Field(max_length=12)] = []
    pitfalls: Annotated[list[Guidance], Field(max_length=12)] = []
    diagnostics: Annotated[list[Guidance], Field(max_length=12)] = []
    combinations: Annotated[list[Combination], Field(max_length=12)] = []
    publication_mapping: PublicationMapping = Field(default_factory=PublicationMapping)


class TypeRef(StrictModel):
    profile_id: Identifier
    version: Annotated[int, Field(ge=1)]
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    role: Literal["unspecified", "primary", "supporting", "atmosphere"] = "unspecified"


GUIDANCE_KEYS = ("reader_promises", "character_dynamics", "voice_guidance", "pitfalls", "diagnostics", "mechanisms")


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def validate_profile(raw: dict, *, enabled: bool = False) -> dict:
    try:
        profile = Profile.model_validate(raw).model_dump(by_alias=True)
    except ValidationError as exc:
        issue = exc.errors(include_input=False)[0]
        raise CreativeTypeError(f"类型资料字段 {'.'.join(map(str, issue['loc']))} 无效：{issue['msg']}") from None
    profile_bytes = len(encoded(profile))
    if profile_bytes > PROFILE_LIMIT:
        raise CreativeTypeError(f"类型 {profile['name'] or profile['profile_id']} 占用 {profile_bytes} 字节，超过 {PROFILE_LIMIT} 字节上限，请精简说明；未截断内容。")
    if len(profile['aliases']) != len(set(profile['aliases'])):
        raise CreativeTypeError('同一资料的别名不可重复。')
    items = [item for key in GUIDANCE_KEYS for item in profile[key]]
    if len({item['id'] for item in items}) != len(items):
        raise CreativeTypeError("同一资料中的说明 ID 不可重复。")
    for item in items:
        if len(item['stages']) != len(set(item['stages'])):
            raise CreativeTypeError("适用阶段不可重复。")
    if enabled:
        if not profile['name'] or not profile['summary'] or not items:
            raise CreativeTypeError("启用前请填写名称、简介和至少一条创作说明。")
        if any(not i['stages'] or not i.get('text', i.get('name')) or ('principle' in i and not i['principle']) for i in items):
            raise CreativeTypeError("每条说明需要内容及适用阶段；机制还需要名称和核心作用。")
    mapping = profile['publication_mapping']
    primary = {e.id for e in PRIMARY_GENRES}
    facets = {e.id for e in (*STORY_TYPES, *TONES, *SETTINGS)}
    if mapping['catalog_version'] != CATALOG_VERSION or set(mapping['primary_genre_ids']) - primary or set(mapping['facet_ids']) - facets:
        raise CreativeTypeError("发行映射必须来自当前 H5 分类字典。")
    for key in ('primary_genre_ids', 'facet_ids'):
        if len(mapping[key]) != len(set(mapping[key])):
            raise CreativeTypeError("发行映射候选不可重复。")
    if (mapping['status'] == 'mapped') != bool(mapping['primary_genre_ids'] or mapping['facet_ids']):
        raise CreativeTypeError("发行映射状态与候选不一致。")
    if len({c['with_profile_id'] for c in profile['combinations']}) != len(profile['combinations']):
        raise CreativeTypeError("合作类型不可重复。")
    if any(c['with_profile_id'] == profile['profile_id'] for c in profile['combinations']):
        raise CreativeTypeError("组合说明不能引用自身。")
    return profile


def compile_snapshot(snapshot: dict, stage: Stage) -> dict:
    """Verify and project saved bytes; never consult the mutable workspace library."""
    if stage not in ('brainstorm', 'story_design', 'chapter_plan', 'draft', 'review'):
        raise CreativeTypeError("未知创作阶段。")
    if not isinstance(snapshot, dict) or snapshot.get('schema') != 'book-type-selection.v1' or not isinstance(snapshot.get('profiles'), list) or digest(snapshot.get('profiles')) != snapshot.get('snapshot_sha256'):
        raise CreativeTypeError("类型快照已损坏，尚未调用模型。", 409)
    if any(not isinstance(row, dict) or not isinstance(row.get('profile'), dict) or not isinstance(row['profile'].get('profile_id'), str) or row.get('role') not in ('unspecified', 'primary', 'supporting', 'atmosphere') or not isinstance(row.get('source_sha256'), str) for row in snapshot['profiles']):
        raise CreativeTypeError('类型快照字段损坏，尚未调用模型。', 409)
    ids = [row['profile']['profile_id'] for row in snapshot['profiles']]
    if len(ids) != len(set(ids)):
        raise CreativeTypeError("类型快照含重复选择。", 409)
    profiles, manifest = [], []
    for row in snapshot['profiles']:
        p = validate_profile(row['profile'], enabled=True)
        if digest(p) != row['source_sha256']:
            raise CreativeTypeError("类型来源校验失败，尚未调用模型。", 409)
        content = {k: deepcopy(p[k]) for k in ('name', 'dimension', 'summary', 'applicability')}
        for key in GUIDANCE_KEYS:
            content[key] = []
            for item in p[key]:
                included = stage in item['stages']
                manifest.append({'profile_id': p['profile_id'], 'item_id': item['id'], 'included': included})
                if included:
                    content[key].append(deepcopy(item))
        content['combinations'] = [deepcopy(c) for c in p['combinations'] if c['with_profile_id'] in ids]
        profiles.append({'profile_id': p['profile_id'], 'version': p['version'],
                         'source_sha256': row['source_sha256'], 'role': row['role'], 'content': content})
    result = {'schema': 'genre-context.v1', 'stage': stage, 'authority': 'type_guidance_not_book_facts',
              'profiles': profiles, 'projection_manifest': manifest, 'compiler_version': 'genre-context-compiler.v1'}
    context_bytes = len(encoded(result))
    if context_bytes > CONTEXT_LIMIT:
        sizes = '、'.join(f"{p['content']['name']} {len(encoded(p))} 字节" for p in profiles)
        raise CreativeTypeError(f"已选类型上下文占用 {context_bytes} 字节，超过 {CONTEXT_LIMIT} 字节（含投影清单）；各类型：{sizes}。请减少选择或精简资料；未截断内容。")
    return result


def _initial_records() -> dict:
    raw = json.loads(RESOURCE.read_text(encoding='utf-8'))
    records = {}
    for entry in raw['profiles']:
        p = validate_profile(entry['profile'], enabled=True)
        records[p['profile_id']] = {'id': p['profile_id'], 'revision': 0, 'status': 'disabled', 'retired': True,
            'origin': 'builtin', 'coverage': entry.get('coverage', 'specialized_guidance'),
            'updated_at': raw['created_at'], 'draft': p, 'active_version': p['version'],
            'versions': [{'profile': deepcopy(p), 'sha256': digest(p), 'created_at': raw['created_at']}]}
    curated = json.loads(CURATED_RESOURCE.read_text(encoding='utf-8'))
    for entry in curated['profiles']:
        p = validate_profile(entry['profile'], enabled=True)
        record = records[p['profile_id']]
        record.update(status='enabled', retired=False, draft=p, active_version=p['version'],
                      updated_at=curated['created_at'])
        record['versions'].append({'profile': deepcopy(p), 'sha256': digest(p), 'created_at': curated['created_at']})
    return records


_MUTATION_LOCK = threading.RLock()


class CreativeTypeStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / 'library.json'

    def _load(self) -> dict:
        if self.root.is_symlink() or self.root.parent.is_symlink() or self.path.is_symlink():
            raise CreativeTypeError('资料存储路径异常。', 409)
        if not self.path.exists():
            return {'schema': 'creative-type-library.v1', 'records': {}, 'operations': {}}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data['schema'] != 'creative-type-library.v1' or not isinstance(data['records'], dict) or not isinstance(data['operations'], dict):
                raise ValueError()
            for pid, record in data['records'].items():
                if record['id'] != pid or record['draft']['profile_id'] != pid or record['status'] not in ('draft', 'enabled', 'disabled') or type(record['revision']) is not int or record['revision'] < 0:
                    raise ValueError()
                if record.get('deleted_at') is not None and (not isinstance(record['deleted_at'], str) or not record['deleted_at'] or record['status'] != 'disabled'):
                    raise ValueError()
                validate_profile(record['draft'])
                version_ids = [v['profile']['version'] for v in record['versions']]
                if len(set(version_ids)) != len(version_ids) or (record['active_version'] is not None and record['active_version'] not in version_ids):
                    raise ValueError()
                if record['status'] == 'enabled' and record['active_version'] is None:
                    raise ValueError()
                for version in record['versions']:
                    if version['profile']['profile_id'] != pid or digest(version['profile']) != version['sha256']:
                        raise ValueError()
                    validate_profile(version['profile'], enabled=True)
            return data
        except (OSError, ValueError, KeyError, TypeError):
            raise CreativeTypeError('类型资料存储损坏；未用默认资料覆盖。请保留文件并检查。', 409) from None

    def records(self) -> dict:
        return {**_initial_records(), **self._load()['records']}

    def get(self, profile_id: str, *, include_deleted: bool = False) -> dict:
        record = self.records().get(profile_id)
        if record is None or (record.get('deleted_at') and not include_deleted):
            raise CreativeTypeError('找不到此作品类型。', 404)
        return deepcopy(record)

    def snapshot(self, refs: list[TypeRef], *, require_enabled: bool = True) -> dict:
        records = self.records()
        if len({r.profile_id for r in refs}) != len(refs):
            raise CreativeTypeError('不能重复选择同一个类型。')
        profiles = []
        for ref in refs:
            record = records.get(ref.profile_id)
            if not record or (require_enabled and record['status'] != 'enabled'):
                raise CreativeTypeError('所选类型已停用或不存在，请重新选择。', 409)
            version = next((v for v in record['versions'] if v['profile']['version'] == ref.version), None)
            if not version or version['sha256'] != ref.source_sha256:
                raise CreativeTypeError('所选资料版本或哈希不匹配，请检查选择。', 409)
            profiles.append({'profile': deepcopy(version['profile']), 'source_sha256': version['sha256'], 'role': ref.role})
        result = {'schema': 'book-type-selection.v1', 'profiles': profiles, 'snapshot_sha256': digest(profiles)}
        for stage in ('brainstorm', 'story_design', 'chapter_plan', 'draft', 'review'):
            compile_snapshot(result, stage)
        return result

    @contextmanager
    def _locked(self):
        with _MUTATION_LOCK:
            if self.root.is_symlink() or self.root.parent.is_symlink() or (self.root / '.lock').is_symlink():
                raise CreativeTypeError('资料存储路径异常。', 409)
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / '.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    yield self._load()
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def mutate(self, action: str, profile_id: str | None, *, revision: int, operation_id: str, raw: dict | None = None) -> dict:
        payload_hash = digest([action, profile_id, revision, raw])
        with self._locked() as data:
            previous = data['operations'].get(operation_id)
            if previous:
                if previous['request_sha256'] != payload_hash:
                    raise CreativeTypeError('同一操作标识不能用于不同修改。', 409)
                result = deepcopy(previous['result'])
                result['versions'] = deepcopy(data['records'][result['id']]['versions'][:previous['version_count']])
                return result
            records = {**_initial_records(), **data['records']}
            now = datetime.now(timezone.utc).isoformat()
            if action == 'create':
                profile_id = 'ct_' + uuid.uuid4().hex
                p = Profile(profile_id=profile_id).model_dump(by_alias=True)
                record = {'id': profile_id, 'revision': 0, 'origin': 'custom', 'coverage': 'user_guidance',
                          'status': 'draft', 'draft': p, 'active_version': None, 'versions': [], 'updated_at': now}
                if raw:
                    raw = {**raw, 'profile_id': profile_id, 'version': 1}
                    record['draft'] = validate_profile(raw)
            else:
                record = deepcopy(records.get(profile_id))
                if not record:
                    raise CreativeTypeError('找不到此作品类型。', 404)
                if record.get('deleted_at'):
                    raise CreativeTypeError('此作品类型已删除，请刷新列表。', 404)
                if revision != record['revision']:
                    raise CreativeTypeError('资料已被修改，请先对照最新版本；当前草稿尚未覆盖。', 409)
                if action in ('draft', 'activate'):
                    if raw is None or raw.get('profile_id') != profile_id:
                        raise CreativeTypeError('草稿与目标类型不一致。')
                    p = validate_profile(raw, enabled=action == 'activate')
                    for c in p['combinations']:
                        if c['with_profile_id'] not in records:
                            raise CreativeTypeError('组合说明引用了不存在的类型。')
                    p['version'] = (max((v['profile']['version'] for v in record['versions']), default=0) + 1)
                    if action == 'activate':
                        record['versions'].append({'profile': deepcopy(p), 'sha256': digest(p), 'created_at': now})
                        record.update(active_version=p['version'], status='enabled')
                    record['draft'] = p
                elif action == 'disable':
                    record['status'] = 'disabled'
                elif action == 'delete':
                    # Keep immutable versions and the override, so snapshots survive
                    # and a deleted built-in cannot reappear on the next read.
                    record.update(status='disabled', deleted_at=now)
                else:
                    raise CreativeTypeError('未知类型操作。')
            for c in record['draft']['combinations']:
                if c['with_profile_id'] not in records:
                    raise CreativeTypeError('组合说明引用了不存在的类型。')
            if action == 'delete':
                record['retired'] = True  # Older readers also hide the tombstone by default.
            else:
                record.pop('retired', None)  # Explicit edits make a legacy reference user-maintained.
            record.update(revision=record['revision'] + 1, updated_at=now)
            data['records'][profile_id] = record
            data['operations'][operation_id] = {'request_sha256': payload_hash,
                'result': deepcopy({k: v for k, v in record.items() if k != 'versions'}), 'version_count': len(record['versions'])}
            fd, name = tempfile.mkstemp(prefix='.types-', dir=self.root)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(encoded(data)); stream.flush(); os.fsync(stream.fileno())
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            return deepcopy(record)
