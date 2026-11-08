"""Scene-based length estimates and author-confirmed execution allocations.

Estimates are editorial advice, not literary scores. Only a confirmed allocation
is executable. This module has no model calls, storage or publication authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = 'story-capacity.v1'
MARKER = 'STORY_CAPACITY_JSON'


def language_key(value: str) -> str:
    text = value.strip().casefold()
    if re.search(r'\benglish\b|^en(?:[-_]|$)|英文|英语', text):
        return 'en'
    if re.search(r'\bchinese\b|^zh(?:[-_]|$)|中文|汉语|汉字', text):
        return 'zh'
    return text


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)


class Scene(Record):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    title: str = Field(min_length=1, max_length=160)
    partition_label: str | None = Field(default=None, min_length=1, max_length=160)
    source_quote: str = Field(min_length=8, max_length=800)
    presentation: Literal['on_page', 'summary']
    reader_value: str = Field(min_length=1, max_length=800)
    minimum: int = Field(ge=1)
    maximum: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=800)

    @model_validator(mode='after')
    def ordered(self):
        if self.maximum < self.minimum:
            raise ValueError('场景篇幅范围的上限不能小于下限。')
        return self


class Chapter(Record):
    number: int = Field(ge=1, le=511)
    scene_ids: list[str] = Field(min_length=1, max_length=60)
    target_words: int = Field(ge=1)


class Option(Record):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    title: str = Field(min_length=1, max_length=160)
    tradeoff: str = Field(min_length=1, max_length=1200)
    recommended: bool = False
    total_words: int = Field(ge=1)
    chapters: list[Chapter] = Field(min_length=1, max_length=511)


class Plan(Record):
    language: str = Field(min_length=1, max_length=80)
    count_unit: Literal['words', 'characters']
    rationale: str = Field(min_length=1, max_length=1600)
    scenes: list[Scene] = Field(min_length=1, max_length=511)
    options: list[Option] = Field(min_length=1, max_length=3)

    @model_validator(mode='after')
    def allocation(self):
        expected_unit = {'en': 'words', 'zh': 'characters'}.get(language_key(self.language))
        if expected_unit and expected_unit != self.count_unit:
            raise ValueError('计量单位与正文语言不一致（英文按词，中文按字）。')
        scenes = {s.id: s for s in self.scenes}
        if len(scenes) != len(self.scenes) or len({o.id for o in self.options}) != len(self.options):
            raise ValueError('场景与篇幅方案的 ID 必须唯一。')
        if sum(o.recommended for o in self.options) > 1:
            raise ValueError('只能推荐一个篇幅方案。')
        for option in self.options:
            if [c.number for c in option.chapters] != list(range(1, len(option.chapters) + 1)):
                raise ValueError('章节必须从 1 开始连续排列。')
            refs = [sid for c in option.chapters for sid in c.scene_ids]
            if len(refs) != len(scenes) or set(refs) != set(scenes):
                raise ValueError('每个方案必须完整安排全部场景，各一次；跨章场景请分段估算。')
            if sum(c.target_words for c in option.chapters) != option.total_words:
                raise ValueError('分章篇幅之和必须等于全书篇幅。')
            for chapter in option.chapters:
                if not (sum(scenes[s].minimum for s in chapter.scene_ids) <= chapter.target_words
                        <= sum(scenes[s].maximum for s in chapter.scene_ids)):
                    raise ValueError('分章篇幅需在所含场景的估算范围内。')
        return self


def validate_plan(raw: dict, source: str | None = None) -> dict:
    if len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode()) > 192_000:
        raise ValueError('篇幅建议过长，请按必要场景简洁规划。')
    plan = Plan.model_validate(raw)
    if source is not None and any(scene.source_quote not in source for scene in plan.scenes):
        raise ValueError('篇幅建议的来源引文未在本轮已提供的故事中找到。')
    # Omit the optional partition annotation on old plans so existing hashes
    # and execution contracts retain their exact canonical representation.
    return plan.model_dump(exclude_none=True)


def contract(plan: dict, option_id: str, source_sha256: str) -> dict:
    value = validate_plan(plan)
    option = next((o for o in value['options'] if o['id'] == option_id), None)
    if option is None:
        raise ValueError('找不到选中的篇幅方案。')
    body = {'schema': VERSION, 'source_sha256': source_sha256,
            'plan_sha256': digest(value), 'language': value['language'], 'count_unit': value['count_unit'],
            'scenes': value['scenes'], 'option': option}
    return {**body, 'sha256': digest(body)}


def validate_contract(raw: dict) -> dict:
    if not isinstance(raw, dict) or set(raw) != {'schema', 'source_sha256', 'plan_sha256', 'language',
                                              'count_unit', 'scenes', 'option', 'sha256'}:
        raise ValueError('篇幅执行合同字段不完整。')
    body = {k: v for k, v in raw.items() if k != 'sha256'}
    if raw['schema'] != VERSION or digest(body) != raw['sha256']:
        raise ValueError('篇幅执行合同校验失败。')
    for key in ('source_sha256', 'plan_sha256'):
        if not isinstance(raw[key], str) or not re.fullmatch(r'[0-9a-f]{64}', raw[key]):
            raise ValueError('篇幅执行合同缺少来源身份。')
    validate_plan({k: raw[k] for k in ('language', 'count_unit', 'scenes')} |
                  {'rationale': 'Author-confirmed allocation', 'options': [raw['option']]})
    return raw


def read_contract(source: str) -> dict | None:
    starts = len(re.findall(rf'\[{MARKER}\]', source))
    ends = len(re.findall(rf'\[/{MARKER}\]', source))
    if not starts and not ends:
        return None
    match = re.search(rf'\[{MARKER}\]\s*(.*?)\s*\[/{MARKER}\]', source, re.S)
    if starts != 1 or ends != 1 or match is None:
        raise ValueError('篇幅执行合同必须唯一且完整。')
    def unique(pairs):
        value = {}
        for k, v in pairs:
            if k in value:
                raise ValueError('篇幅合同包含重复字段。')
            value[k] = v
        return value
    return validate_contract(json.loads(match[1], object_pairs_hook=unique))


def bind_contract(source: str, allocation: dict) -> str:
    validate_contract(allocation)
    # The server owns this block. Do not retain a model-created authority copy.
    source = re.sub(rf'\[{MARKER}\].*?\[/{MARKER}\]\s*', '', source, flags=re.S)
    if f'[{MARKER}]' in source or f'[/{MARKER}]' in source:
        raise ValueError('模型返回了不完整的篇幅合同标记。')
    block = f'[{MARKER}]\n' + json.dumps(allocation, ensure_ascii=False, indent=2) + f'\n[/{MARKER}]\n\n'
    marker = re.search(r'^##\s+2\.', source, re.M)
    if marker is None:
        raise ValueError('缺少骨架第 2 节，无法绑定篇幅合同。')
    return source[:marker.start()] + block + source[marker.start():]
