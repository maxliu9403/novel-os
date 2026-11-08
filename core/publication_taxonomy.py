"""Public catalogue projection, independent of private writing instructions.

One namespace owns stable IDs. Revisions describe public metadata, not changes
in writing guidance. All export formats serialize the same validated object.
"""

from __future__ import annotations

import json
import re

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class PublicMetadata(Strict):
    labels: dict[str, Annotated[str, Field(min_length=1, max_length=100)]]
    definition: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def labels_valid(self):
        if not self.labels or set(self.labels) - {"zh-CN", "en-US"}:
            raise ValueError("公开名称支持 zh-CN、en-US，至少填写一种。")
        return self


class Entry(PublicMetadata):
    id: str = Field(pattern=r"^(genre|tag)\.[a-z][a-z0-9_-]{1,74}$")
    revision: int = Field(ge=1)


class Taxonomy(Strict):
    schema_version: Literal["novel-taxonomy.v1"]
    namespace: str = Field(
        pattern=r"^[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+$", max_length=120
    )
    genre: Entry
    tags: list[Entry] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def identities(self):
        if not self.genre.id.startswith("genre.") or any(
            not t.id.startswith("tag.") for t in self.tags
        ):
            raise ValueError("公开目录的 Genre / Tag ID 类型不一致。")
        if len({t.id for t in self.tags}) != len(self.tags):
            raise ValueError("公开目录 Tag 不可重复。")
        return self


def public_metadata(document: dict) -> dict:
    value = document.get("publication") or {
        "labels": {"zh-CN": document["name"]},
        "definition": document["name"],
    }
    return PublicMetadata.model_validate(value).model_dump()


def public_entry(versions: list[dict], selected_version: int) -> dict:
    from .creative_types import parse_document

    previous, revision, selected = None, 0, None
    for version in sorted(versions, key=lambda v: v["version"]):
        doc = parse_document(version["markdown"], enabled=True)
        public = public_metadata(doc)
        if public != previous:
            revision += 1
            previous = public
        if version["version"] == selected_version:
            selected = {"id": doc["id"], "revision": revision, **public}
            break
    if selected is None:
        raise ValueError("找不到公开目录对应的资料版本。")
    return selected


def creation_channel(initial_input: dict) -> str | None:
    """Read the explicit channel; only frozen legacy input may omit it."""
    channel = initial_input.get('audience_channel')
    if ((initial_input.get('creation_flow') == 'genre_tags.v2' and channel is None)
            or channel is not None and channel not in {'male', 'female'}):
        raise ValueError('缺少有效的作者确认频道，请重新确认基础设定。')
    return channel


def classification(taxonomy: dict, chapters: int | None = None, *, audience_channel: str | None = None):
    from .novel_classification import NovelClassification, _chapter_shape

    value = Taxonomy.model_validate(taxonomy).model_dump()
    if audience_channel is not None and audience_channel not in {'male', 'female'}:
        raise ValueError('新创作的发行频道必须为 male 或 female。')
    form, band = _chapter_shape(chapters)
    return NovelClassification(
        primary_genre_id=value["genre"]["id"],
        story_type_ids=tuple(t["id"] for t in value["tags"]),
        taxonomy=value,
        catalog_version="novel-taxonomy.v1",
        length_form=form,
        chapter_band=band,
        source="author_confirmed",
        confidence=1.0,
        audience_channel=audience_channel or 'general',
    )


def export_target(classification: dict | None) -> dict:
    from .h5_contract import PROFILE

    if (
        classification
        and classification.get("schema_version") == "novel-classification.v2"
    ):
        from .creative_types import digest

        public = Taxonomy.model_validate(classification["taxonomy"]).model_dump()
        return {
            "target_profile": "novel-h5-export.dynamic-taxonomy.v1",
            "catalog_sha256": digest(public),
            "validator_revision": "novel-os:dynamic-taxonomy.v1",
            "required_h5_capabilities": [
                "novel-classification.v2",
                "novel-taxonomy.v1",
            ],
        }
    return {
        "target_profile": PROFILE.id,
        "catalog_sha256": PROFILE.catalog_sha256,
        "validator_revision": PROFILE.validator_revision,
    }


def bind_classification(prompt: str, initial_input: dict) -> str:
    channel = creation_channel(initial_input)
    taxonomy = initial_input.get("publication_taxonomy")
    if taxonomy is None:
        return prompt
    chapter = re.search(
        r"^\s*(?:[-*]\s*)?(?:\*\*)?Chapters(?:\*\*)?\s*:\s*(\d+)\s*$",
        prompt,
        re.I | re.M,
    )
    value = classification(
        taxonomy, int(chapter[1]) if chapter else initial_input.get("chapters"),
        audience_channel=channel,
    ).to_dict()
    block = (
        "[NOVEL_CLASSIFICATION_JSON]\n"
        + json.dumps(value, ensure_ascii=False, indent=2)
        + "\n[/NOVEL_CLASSIFICATION_JSON]"
    )
    pattern = re.compile(
        r"\[NOVEL_CLASSIFICATION_JSON\].*?\[/NOVEL_CLASSIFICATION_JSON\]", re.S | re.I
    )
    matches = list(pattern.finditer(prompt))
    if len(matches) > 1 or (
        not matches
        and (
            "[NOVEL_CLASSIFICATION_JSON]" in prompt
            or "[/NOVEL_CLASSIFICATION_JSON]" in prompt
        )
    ):
        raise ValueError(
            "骨架必须包含唯一且完整的发行分类区块，才能绑定作者所选 Genre / Tags。"
        )
    prompt = (
        pattern.sub(lambda _: block, prompt)
        if matches
        else prompt.rstrip() + "\n\n" + block + "\n"
    )
    header, separator, rest = prompt.partition("\n## ")
    labels = value["display_labels"]["en-US"]
    genre = " / ".join([labels["primary_genre"], *labels["story_types"]])
    header = re.sub(
        r"^(\s*(?:[-*]\s*)?(?:\*\*)?Genre(?:\*\*)?\s*:\s*)[^\n]+",
        lambda m: m[1] + genre,
        header,
        flags=re.M | re.I,
    )
    return header + separator + rest
