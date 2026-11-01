"""Author-owned H5 selection. Creative profiles cannot change publication IDs.

Legacy API clients/sessions may omit the guided-flow marker. The new UI always
uses it; missing/invalid selections fail before persistence or inference.
"""
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.novel_classification import CATALOG_VERSION, NovelClassification, infer_classification


class BookClassificationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    catalog_version: Literal["novel-types.2026-10"] = CATALOG_VERSION
    primary_genre_id: str
    secondary_genre_ids: list[str] = Field(default_factory=list, max_length=2)
    story_type_ids: list[str] = Field(default_factory=list, max_length=3)
    tone_ids: list[str] = Field(default_factory=list, max_length=3)
    setting_ids: list[str] = Field(default_factory=list, max_length=2)
    audience_channel: Literal["female", "male", "general"] = "general"

    @model_validator(mode="after")
    def validate_catalog(self):
        self.canonical()
        return self

    def canonical(self, chapters: int | None = None) -> NovelClassification:
        shape = infer_classification(chapters=chapters)
        return NovelClassification(**self.model_dump(), length_form=shape.length_form,
                                   chapter_band=shape.chapter_band,
                                   source="author_confirmed", confidence=1.0)


def selection_labels(value: dict) -> list[str]:
    from core.novel_classification import catalog_payload
    selected = BookClassificationSelection.model_validate(value).canonical().filter_type_ids
    labels = {item['id']: item['labels']['zh-CN']
              for entries in catalog_payload()['axes'].values() for item in entries}
    return [labels[id] for id in selected]


def bind_classification(prompt: str, initial_input: dict) -> str:
    """Render the author's exact IDs, not a model-generated mapping. Idempotent.

    Only this machine block and the display Genre header change. Story prose and
    original model responses remain available. Length follows the chapter header and is checked
again by the existing handoff/export validators.
"""
    raw = initial_input.get('publication_classification')
    if raw is None:
        return prompt
    chapter = re.search(r'^\s*(?:[-*]\s*)?(?:\*\*)?Chapters(?:\*\*)?\s*:\s*(\d+)\s*$', prompt, re.I | re.M)
    chapters = int(chapter[1]) if chapter else initial_input.get('chapters')
    value = BookClassificationSelection.model_validate(raw).canonical(chapters).to_dict()
    block = '[NOVEL_CLASSIFICATION_JSON]\n' + json.dumps(value, ensure_ascii=False, indent=2) + '\n[/NOVEL_CLASSIFICATION_JSON]'
    pattern = re.compile(r'\[NOVEL_CLASSIFICATION_JSON\].*?\[/NOVEL_CLASSIFICATION_JSON\]', re.S | re.I)
    matches = list(pattern.finditer(prompt))
    if len(matches) != 1:
        raise ValueError('骨架必须包含唯一的发行分类区块，才能绑定用户所选 H5 分类。')
    prompt = pattern.sub(lambda _: block, prompt)
    # The legacy display string must agree with the locked IDs too; it is used
    # by the library and CLI even though the machine block is authoritative.
    header, separator, rest = prompt.partition('\n## ')
    labels = value['display_labels']['en-US']
    genre = ' / '.join([labels['primary_genre'], *labels['secondary_genres'], *labels['story_types']])
    header = re.sub(r'^(\s*(?:[-*]\s*)?(?:\*\*)?Genre(?:\*\*)?\s*:\s*)[^\n]+',
                    lambda m: m[1] + genre, header, flags=re.M | re.I)
    return header + separator + rest
