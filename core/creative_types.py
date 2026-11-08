"""Versioned Genre/Tag Markdown, its workspace store and deterministic compiler.

Markdown is the only editable content. Parsed documents are projections, never a
second source of truth. Frozen selections need no access to the mutable library.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .knowledge_guidance import GENERATIVE_STAGE_SECTIONS, interpretation
from .publication_taxonomy import (
    PublicMetadata,
    Taxonomy,
    public_entry,
    public_metadata,
)

RESOURCE = Path(__file__).resolve().parents[1] / "resources/novel-knowledge"
DOCUMENT_LIMIT = 24 * 1024
CONTEXT_LIMIT = 96 * 1024
SECTIONS = ("核心承诺", "故事机制", "发散空间", "组合原则", "场景与表达", "简介与兑现")
Stage = Literal[
    "brainstorm",
    "story_design",
    "chapter_plan",
    "draft",
    "review",
    "publication",
    "cover",
]
STAGE_SECTIONS = {
    "brainstorm": ("核心承诺", "故事机制", "发散空间", "组合原则", "简介与兑现"),
    "story_design": SECTIONS,
    "chapter_plan": ("核心承诺", "故事机制", "组合原则", "场景与表达", "简介与兑现"),
    "draft": ("核心承诺", "故事机制", "场景与表达"),
    "review": ("核心承诺", "故事机制", "场景与表达", "简介与兑现"),
    "publication": ("核心承诺", "简介与兑现"),
    "cover": ("核心承诺", "封面表达"),
}
Identifier = Annotated[str, Field(pattern=r"^(genre|tag)\.[a-z][a-z0-9_-]{1,74}$")]
Name = Annotated[str, Field(min_length=1, max_length=100)]
Role = Literal[
    "conflict", "relationship", "character", "world", "setting",
    "mood", "voice", "pacing", "ending",
]


class CreativeTypeError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Header(StrictModel):
    schema_version: Literal["novel-knowledge.v1"] = Field(alias="schema")
    id: Identifier
    kind: Literal["genre", "tag"]
    name: Name
    publication: PublicMetadata | None = None
    aliases: Annotated[list[Name], Field(max_length=20)] = []
    genre_ids: Annotated[list[Identifier], Field(max_length=30)] = []

    @model_validator(mode="after")
    def identity(self):
        if not self.id.startswith(self.kind + "."):
            raise ValueError("ID 前缀必须与 kind 一致")
        if self.kind == "genre" and self.genre_ids:
            raise ValueError("Genre 不填写 genre_ids")
        if self.kind == "tag" and (
            not self.genre_ids
            or any(not x.startswith("genre.") for x in self.genre_ids)
        ):
            raise ValueError("Tag 至少关联一个 Genre")
        if len(set(self.genre_ids)) != len(self.genre_ids):
            raise ValueError("Genre 关联不可重复")
        labels = [s.casefold() for s in [self.name, *self.aliases]]
        if len(set(labels)) != len(labels):
            raise ValueError("名称和别名不可重复")
        return self


class TypeRef(StrictModel):
    id: Identifier
    version: Annotated[int, Field(ge=1)]
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class GenerativeHeader(Header):
    schema_version: Literal["novel-knowledge.v2"] = Field(alias="schema")
    roles: Annotated[list[Role], Field(max_length=3)] = []

    @model_validator(mode="after")
    def contributions(self):
        if self.schema_version == "novel-knowledge.v2" and self.kind == "tag" and not self.roles:
            raise ValueError("Tag 请声明 1–3 个创作作用 roles")
        if self.kind == "genre" and self.roles:
            raise ValueError("Genre 不填写 Tag 的 roles")
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("创作作用 roles 不可重复")
        return self


class FreeformHeader(GenerativeHeader):
    """Identity stays validated; authors own the Markdown body and its headings."""

    schema_version: Literal["novel-knowledge.v3"] = Field(alias="schema")


def encoded(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def text_sha(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


class _HeaderLoader(yaml.SafeLoader):
    """A small metadata map; reject aliases, duplicate keys and YAML object tags."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise CreativeTypeError("文件头不支持 YAML 引用。")
        return super().compose_node(parent, index)


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise CreativeTypeError("文件头字段必须唯一且为文本。")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_HeaderLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def validate_markdown_size(markdown: str) -> None:
    if not isinstance(markdown, str) or len(markdown.encode("utf-8")) > DOCUMENT_LIMIT:
        raise CreativeTypeError(
            f"Markdown 超过 {DOCUMENT_LIMIT} 字节上限；内容未截断。"
        )


def read_document_header(markdown: str) -> tuple[dict, str]:
    """Validate identity without imposing a contract on author prose."""
    validate_markdown_size(markdown)
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", markdown, re.S)
    if not match or len(match[1].encode()) > 8192:
        raise CreativeTypeError("缺少合法的 YAML 文件头（---）。")
    try:
        data = yaml.load(match[1], Loader=_HeaderLoader)
        models = {"novel-knowledge.v2": GenerativeHeader, "novel-knowledge.v3": FreeformHeader}
        model = models.get(data.get("schema"), Header) if isinstance(data, dict) else Header
        header = model.model_validate(data).model_dump(by_alias=True)
    except (yaml.YAMLError, ValidationError, RecursionError) as exc:
        detail = (
            exc.errors(include_input=False)[0]["msg"]
            if isinstance(exc, ValidationError)
            else "YAML 格式无效"
        )
        raise CreativeTypeError(f"文件头：{detail}") from None
    return header, markdown[match.end():]


def parse_document(markdown: str, *, enabled: bool = False) -> dict:
    validate_markdown_size(markdown)
    if not markdown.startswith("---"):
        return _parse_tag_prompt(markdown, enabled=enabled)
    header, body = read_document_header(markdown)
    if header["schema"] == "novel-knowledge.v3":
        if enabled and not body.strip():
            raise CreativeTypeError("请填写创作说明正文后再启用。")
        return {
            **header, "format": "markdown-guide.v1", "body": body,
            "sections": {}, "missing_sections": [], "appendices": [],
        }
    sections, preamble = _sections(body)
    if preamble:
        raise CreativeTypeError("正文请写在 ## 分节内。")
    return _document(header, sections, enabled=enabled)


def _sections(body: str) -> tuple[dict[str, str], str]:
    """Read headings for validation/preview; never reserialize the source Markdown."""
    sections, current, lines, fence, preamble = {}, None, [], None, ""
    for line in body.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        heading = re.fullmatch(r"##[ \t]+(.+?)[ \t]*", line) if fence is None else None
        if heading:
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            else:
                preamble = "\n".join(lines).strip()
            current = heading[1]
            if current in sections:
                raise CreativeTypeError(f"分节重复：{current}")
            lines = []
        else:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    else:
        preamble = "\n".join(lines).strip()
    return sections, preamble


def _document(header: dict, sections: dict, *, enabled: bool) -> dict:
    missing = [name for name in SECTIONS if not sections.get(name)]
    if enabled and missing:
        raise CreativeTypeError("启用前请完成：" + "、".join(missing))
    return {
        **header,
        "sections": sections,
        "missing_sections": missing,
        "appendices": [k for k in sections if k not in (*SECTIONS, "封面表达")],
    }


def _parse_tag_prompt(markdown: str, *, enabled: bool) -> dict:
    """The author's standalone Tag dialect: bilingual H1 + linked Genre quote.

    Metadata is derived without inserting a YAML header or changing a byte.
    All sections (including future additions) remain executable creative guidance.
    """
    title = re.match(
        r"\A\ufeff?# ([^\r\n]+?) +([A-Za-z][A-Za-z0-9 /'-]*)\r?\n", markdown
    )
    sections, preamble = _sections(markdown)
    links = re.findall(
        r"^>[^\r\n]*?关联 Genre[：:]\s*([A-Za-z][A-Za-z0-9 /'-]*?)(?=\s*·|\s*$)",
        preamble,
        re.M,
    )
    if not title or len(links) != 1:
        raise CreativeTypeError(
            "需要 YAML 文件头，或 Tag Markdown 的「# 中文名 English Name」"
            "与「> 关联 Genre：English Name」声明。"
        )

    def identifier(kind: str, label: str) -> str:
        return kind + "." + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")

    name, label = title[1].strip(), title[2].strip()
    try:
        header = Header.model_validate(
            {
                "schema": "novel-knowledge.v1",
                "id": identifier("tag", label),
                "kind": "tag",
                "name": name,
                "aliases": [label] if name.casefold() != label.casefold() else [],
                "genre_ids": [identifier("genre", links[0].strip())],
                "publication": {
                    "labels": {"zh-CN": name, "en-US": label},
                    "definition": name,
                },
            }
        ).model_dump(by_alias=True)
    except ValidationError as exc:
        raise CreativeTypeError(
            "Tag Markdown：" + exc.errors(include_input=False)[0]["msg"]
        ) from None
    return {
        **_document(header, sections, enabled=enabled),
        "format": "tag-prompt.v1",
        "preamble": preamble,
        "appendices": [],
    }


def _compiler_version(docs: list[dict]) -> str:
    if any(d.get("schema") == "novel-knowledge.v3" for d in docs):
        return "knowledge-compiler.v4"
    if any(d.get("schema") == "novel-knowledge.v2" for d in docs):
        return "knowledge-compiler.v3"
    return (
        "knowledge-compiler.v2"
        if any(d.get("format") == "tag-prompt.v1" for d in docs)
        else "knowledge-compiler.v1"
    )


def template(
    kind: Literal["genre", "tag"],
    identifier: str,
    name: str,
    genre_ids: list[str] | None = None,
) -> str:
    header = {
        "schema": "novel-knowledge.v1",
        "id": identifier,
        "kind": kind,
        "name": name,
        "aliases": [],
        "publication": {"labels": {"zh-CN": name}, "definition": name},
    }
    if kind == "tag":
        header["genre_ids"] = genre_ids or []
    return (
        "---\n"
        + yaml.safe_dump(header, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + "\n\n".join("## " + k + "\n" for k in (*SECTIONS, "封面表达"))
        + "\n"
    )


def _documents(snapshot: dict) -> list[dict]:
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema") != "selection-snapshot.v1"
    ):
        raise CreativeTypeError("创作选择快照格式无效。", 409)
    body = {k: v for k, v in snapshot.items() if k != "snapshot_sha256"}
    if digest(body) != snapshot.get("snapshot_sha256"):
        raise CreativeTypeError("创作选择快照校验失败。", 409)
    rows = snapshot.get("documents")
    if not isinstance(rows, list) or not 2 <= len(rows) <= 21:
        raise CreativeTypeError("请选择一个 Genre 和 1–20 个 Tag。")
    docs = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or type(row.get("version")) is not int
            or row["version"] < 1
        ):
            raise CreativeTypeError("资料版本无效。", 409)
        md = row.get("markdown")
        doc = parse_document(md, enabled=True)
        if row.get("id") != doc["id"] or row.get("source_sha256") != text_sha(md):
            raise CreativeTypeError("资料内容与来源身份不一致。", 409)
        docs.append(doc)
    if snapshot.get("compiler_version") != _compiler_version(docs):
        raise CreativeTypeError("冻结资料与编译版本不一致。", 409)
    ids = [d["id"] for d in docs]
    genres = [d for d in docs if d["kind"] == "genre"]
    if (
        len(set(ids)) != len(ids)
        or len(genres) != 1
        or snapshot.get("genre_id") != genres[0]["id"]
    ):
        raise CreativeTypeError("每本书必须且只能选择一个 Genre。")
    tag_ids = [d["id"] for d in docs if d["kind"] == "tag"]
    if snapshot.get("tag_ids") != tag_ids or any(
        genres[0]["id"] not in d["genre_ids"] for d in docs if d["kind"] == "tag"
    ):
        raise CreativeTypeError("所选 Tag 不属于当前 Genre。")
    public = Taxonomy.model_validate(snapshot.get("publication_taxonomy")).model_dump()
    entries = [public["genre"], *public["tags"]]
    by_id = {d["id"]: d for d in docs}
    if set(e["id"] for e in entries) != set(by_id):
        raise CreativeTypeError("公开目录与选择不一致。")
    for entry in entries:
        if {k: entry[k] for k in ("labels", "definition")} != public_metadata(
            by_id[entry["id"]]
        ):
            raise CreativeTypeError("公开目录与冻结资料不一致。")
    return docs


def compile_snapshot(snapshot: dict, stage: Stage) -> dict:
    """Compile frozen guidance according to its declared document dialect."""
    if stage not in STAGE_SECTIONS:
        raise CreativeTypeError("未知创作阶段。")
    docs = _documents(snapshot)
    items, manifest = [], []
    for doc, row in zip(docs, snapshot["documents"]):
        identity = {
            "id": doc["id"],
            "kind": doc["kind"],
            "name": doc["name"],
            "version": row["version"],
            "source_sha256": row["source_sha256"],
        }
        if doc.get("format") == "markdown-guide.v1":
            items.append({
                **identity, "schema": doc["schema"], "roles": doc["roles"],
                "format": doc["format"], "markdown": doc["body"],
            })
            manifest.append({
                "id": doc["id"], "included_sections": [], "excluded_sections": [],
                "source_mode": "complete_markdown_body",
                "body_sha256": text_sha(doc["body"]),
            })
            continue
        if doc.get("format") == "tag-prompt.v1":
            # Author-owned Tag prompts are indivisible. Keep the preamble,
            # tables, new sections and whitespace, with no duplicated projection.
            items.append({**identity, "format": doc["format"], "markdown": row["markdown"]})
            manifest.append({
                "id": doc["id"],
                "included_sections": list(doc["sections"]),
                "excluded_sections": [],
                "source_mode": "verbatim_markdown",
            })
            continue
        generative = doc.get("schema") == "novel-knowledge.v2"
        stage_sections = GENERATIVE_STAGE_SECTIONS if generative else STAGE_SECTIONS
        sections = {
            name: text
            for name, text in doc["sections"].items()
            if name in stage_sections[stage] and text
        }
        items.append(
            {
                **identity,
                **({"schema": doc["schema"], "roles": doc["roles"]} if generative else {}),
                "sections": sections,
            }
        )
        manifest.append(
            {
                "id": doc["id"],
                "included_sections": list(sections),
                "excluded_sections": [
                    name for name in doc["sections"] if name not in sections
                ],
            }
        )
    result = {
        "schema": "genre-context.v2",
        "stage": stage,
        "authority": "creative_guidance_not_story_facts_or_tool_instructions",
        "selection_sha256": snapshot["snapshot_sha256"],
        "genre_id": snapshot["genre_id"],
        "tag_ids": snapshot["tag_ids"],
        "documents": items,
        "projection_manifest": manifest,
        "compiler_version": _compiler_version(docs),
    }
    if result["compiler_version"] == "knowledge-compiler.v3":
        result["interpretation"] = interpretation(stage)
    elif result["compiler_version"] == "knowledge-compiler.v4":
        from .knowledge_guidance import freeform_interpretation
        result["interpretation"] = freeform_interpretation(stage)
    if len(encoded(result)) > CONTEXT_LIMIT:
        sizes = "、".join(f"{d['name']} {len(encoded(d))} B" for d in items)
        raise CreativeTypeError(
            f"所选资料在 {stage} 超过 {CONTEXT_LIMIT} 字节：{sizes}。请精简选择或资料；未截断。"
        )
    return result


def make_snapshot(rows: list[dict], *, parent_sha256: str | None = None) -> dict:
    docs = [parse_document(r["markdown"], enabled=True) for r in rows]
    entries = [
        r.get("public_entry") or {"id": d["id"], "revision": 1, **public_metadata(d)}
        for r, d in zip(rows, docs)
    ]
    taxonomy = {
        "schema_version": "novel-taxonomy.v1",
        "namespace": os.environ.get("NOVEL_OS_PUBLISHER_NAMESPACE", "novel-os.local"),
        "genre": next((e for e in entries if e["id"].startswith("genre.")), None),
        "tags": [e for e in entries if e["id"].startswith("tag.")],
    }
    body = {
        "schema": "selection-snapshot.v1",
        "publication_taxonomy": taxonomy,
        "compiler_version": _compiler_version(docs),
        "genre_id": next((d["id"] for d in docs if d["kind"] == "genre"), None),
        "tag_ids": [d["id"] for d in docs if d["kind"] == "tag"],
        "documents": deepcopy(rows),
        "parent_sha256": parent_sha256,
    }
    result = {**body, "snapshot_sha256": digest(body)}
    for stage in STAGE_SECTIONS:
        compile_snapshot(result, stage)
    return result


def _initial_records() -> dict:
    records = {}
    for path in sorted(RESOURCE.glob("*/*.md")):
        md = path.read_text(encoding="utf-8")
        doc = parse_document(md, enabled=True)
        if doc["id"] in records:
            raise CreativeTypeError("内置资料 ID 重复。", 500)
        records[doc["id"]] = {
            "id": doc["id"],
            "kind": doc["kind"],
            "revision": 0,
            "status": "enabled",
            "origin": "builtin",
            "updated_at": "2026-11-05T00:00:00Z",
            "draft": md,
            "active_version": 1,
            "versions": [
                {
                    "version": 1,
                    "markdown": md,
                    "sha256": text_sha(md),
                    "created_at": "2026-11-05T00:00:00Z",
                }
            ],
        }
    return records


def active_document(record: dict) -> dict:
    version = next(
        (v for v in record["versions"] if v["version"] == record["active_version"]),
        None,
    )
    return parse_document(version["markdown"] if version else record["draft"])


def summary(record: dict) -> dict:
    active = next(
        (v for v in record["versions"] if v["version"] == record["active_version"]),
        None,
    )
    doc = active_document(record)
    return {
        k: record[k]
        for k in (
            "id",
            "kind",
            "revision",
            "status",
            "origin",
            "updated_at",
            "active_version",
        )
    } | {
        "name": doc["name"],
        "aliases": doc["aliases"],
        "genre_ids": doc["genre_ids"],
        "promise": doc["sections"].get("核心承诺", doc.get("body", "")[:600]),
        "source_sha256": active["sha256"] if active else None,
        "has_draft_changes": bool(active and record["draft"] != active["markdown"]),
        "missing_sections": doc["missing_sections"],
    }


_MUTATION_LOCK = threading.RLock()


def _atomic_json(path: Path, data: dict):
    fd, name = tempfile.mkstemp(prefix=".knowledge-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded(data))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class CreativeTypeStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "knowledge.json"

    def _load(self) -> dict:
        if (
            self.root.is_symlink()
            or self.root.parent.is_symlink()
            or self.path.is_symlink()
        ):
            raise CreativeTypeError("资料存储路径异常。", 409)
        if not self.path.exists():
            return {"schema": "knowledge-library.v1", "records": {}, "operations": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                data["schema"] != "knowledge-library.v1"
                or not isinstance(data["records"], dict)
                or not isinstance(data["operations"], dict)
            ):
                raise ValueError()
            for identifier, record in data["records"].items():
                doc = parse_document(record["draft"])
                if (
                    record["id"] != identifier
                    or doc["id"] != identifier
                    or doc["kind"] != record["kind"]
                    or type(record["revision"]) is not int
                    or record["revision"] < 0
                ):
                    raise ValueError()
                if record["status"] not in ("draft", "enabled", "disabled"):
                    raise ValueError()
                ids = []
                for v in record["versions"]:
                    saved = parse_document(v["markdown"], enabled=True)
                    if (
                        saved["id"] != identifier
                        or text_sha(v["markdown"]) != v["sha256"]
                        or type(v["version"]) is not int
                        or v["version"] < 1
                    ):
                        raise ValueError()
                    ids.append(v["version"])
                if len(set(ids)) != len(ids) or (
                    record["active_version"] is not None
                    and record["active_version"] not in ids
                ):
                    raise ValueError()
                if record["status"] == "enabled" and (
                    record["active_version"] is None or record.get("deleted_at")
                ):
                    raise ValueError()
            return data
        except (OSError, ValueError, KeyError, TypeError):
            raise CreativeTypeError("知识库文件损坏；未用默认资料覆盖。", 409) from None

    def records(self) -> dict:
        return {**_initial_records(), **self._load()["records"]}

    def get(self, identifier: str, *, include_deleted: bool = False) -> dict:
        record = self.records().get(identifier)
        if record is None or (record.get("deleted_at") and not include_deleted):
            raise CreativeTypeError("找不到此资料。", 404)
        return deepcopy(record)

    def snapshot(
        self, refs: list[TypeRef], *, parent_sha256: str | None = None
    ) -> dict:
        records, rows = self.records(), []
        for ref in refs:
            record = records.get(ref.id)
            if not record or record.get("deleted_at") or record["status"] != "enabled":
                raise CreativeTypeError("所选资料已停用或不存在，请重新选择。", 409)
            version = next(
                (v for v in record["versions"] if v["version"] == ref.version), None
            )
            if not version or version["sha256"] != ref.source_sha256:
                raise CreativeTypeError("资料版本已变化，请核对后重新选择。", 409)
            rows.append(
                {
                    "id": ref.id,
                    "version": ref.version,
                    "markdown": version["markdown"],
                    "source_sha256": version["sha256"],
                    "public_entry": public_entry(record["versions"], ref.version),
                }
            )
        return make_snapshot(rows, parent_sha256=parent_sha256)

    @contextmanager
    def _locked(self):
        with _MUTATION_LOCK:
            if (
                self.root.is_symlink()
                or self.root.parent.is_symlink()
                or (self.root / ".lock").is_symlink()
            ):
                raise CreativeTypeError("资料存储路径异常。", 409)
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / ".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    yield self._load()
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _associations(self, doc: dict, records: dict):
        for genre_id in doc["genre_ids"]:
            record = records.get(genre_id)
            if (
                not record
                or record.get("deleted_at")
                or record["kind"] != "genre"
                or record["status"] != "enabled"
            ):
                raise CreativeTypeError(f"关联 Genre 不存在或未启用：{genre_id}")
        labels = {s.casefold() for s in [doc["name"], *doc["aliases"]]}
        for record in records.values():
            if (
                record["id"] == doc["id"]
                or record.get("deleted_at")
                or record["status"] != "enabled"
                or record["kind"] != doc["kind"]
            ):
                continue
            other = active_document(record)
            shares_genre = doc["kind"] == "genre" or set(other["genre_ids"]) & set(
                doc["genre_ids"]
            )
            if shares_genre and labels & {
                s.casefold() for s in [other["name"], *other["aliases"]]
            }:
                raise CreativeTypeError(
                    f"名称或别名与 {other['name']} 冲突，请明确区分。"
                )

    def import_documents(
        self, markdowns: list[str], *, expected_revisions: dict[str, int | None]
    ) -> dict:
        """Atomically activate an explicit import, retaining immutable versions.

        Stable IDs define replacement. Name collisions with a different ID fail
        instead of silently merging identities. The caller previews revisions;
        any concurrent edit rejects the entire batch. Identical reimports are no-ops.
        """
        if not 1 <= len(markdowns) <= 100:
            raise CreativeTypeError("一次导入需要 1–100 份资料。")
        docs = [parse_document(md, enabled=True) for md in markdowns]
        ids = [doc["id"] for doc in docs]
        if len(set(ids)) != len(ids) or set(expected_revisions) != set(ids):
            raise CreativeTypeError("导入 ID 重复或版本核对清单不完整。", 409)
        with self._locked() as data:
            records = {**_initial_records(), **data["records"]}
            before = deepcopy(records)
            now = datetime.now(UTC).isoformat()
            results, changed = [], []
            for doc, md in zip(docs, markdowns):
                identifier = doc["id"]
                record = records.get(identifier)
                if (record["revision"] if record else None) != expected_revisions[identifier]:
                    raise CreativeTypeError(f"资料已被修改，请重新核对：{identifier}", 409)
                if record and record.get("deleted_at"):
                    raise CreativeTypeError(f"此 ID 曾被删除，请使用新 ID：{identifier}", 409)
                active = next(
                    (v for v in record["versions"] if v["version"] == record["active_version"]),
                    None,
                ) if record else None
                unchanged = bool(
                    record and record["status"] == "enabled"
                    and record["draft"] == md and active and active["markdown"] == md
                )
                action = "unchanged" if unchanged else "replaced" if record else "created"
                if not unchanged:
                    record = deepcopy(record) if record else {
                        "id": identifier, "kind": doc["kind"], "revision": 0,
                        "origin": "custom", "versions": [],
                    }
                    # An identical active version can be reused when discarding
                    # a changed draft or re-enabling; don't fabricate a version.
                    number = active["version"] if active and active["markdown"] == md else None
                    if number is None:
                        number = max((v["version"] for v in record["versions"]), default=0) + 1
                        record["versions"].append({
                            "version": number, "markdown": md,
                            "sha256": text_sha(md), "created_at": now,
                        })
                    record.update(
                        draft=md, active_version=number, status="enabled",
                        revision=record["revision"] + 1, updated_at=now,
                    )
                    records[identifier] = record
                    changed.append(identifier)
                results.append({
                    "id": identifier, "action": action,
                    "version": record["active_version"], "source_sha256": text_sha(md),
                })
            for doc in docs:
                self._associations(doc, records)
            backup = None
            if changed:
                backups = self.root / "import-backups"
                if backups.is_symlink():
                    raise CreativeTypeError("资料备份路径异常。", 409)
                backups.mkdir(exist_ok=True)
                prior = {**data, "records": before}
                backup = f"import-backups/{digest(prior)}.json"
                _atomic_json(self.root / backup, prior)
                for identifier in changed:
                    data["records"][identifier] = records[identifier]
                _atomic_json(self.path, data)
            return {"items": results, "backup": backup}

    def mutate(
        self,
        action: str,
        identifier: str | None,
        *,
        revision: int,
        operation_id: str,
        markdown: str | None = None,
        version: int | None = None,
    ) -> dict:
        request_sha = digest([action, identifier, revision, markdown] + ([version] if action == "restore" else []))
        with self._locked() as data:
            previous = data["operations"].get(operation_id)
            if previous:
                if previous["request_sha256"] != request_sha:
                    raise CreativeTypeError("同一操作标识不能用于不同修改。", 409)
                return deepcopy(previous["result"])
            records = {**_initial_records(), **data["records"]}
            now = datetime.now(UTC).isoformat()
            if action == "create":
                doc = parse_document(markdown)
                identifier = doc["id"]
                if identifier in records:
                    raise CreativeTypeError(
                        "此稳定 ID 已存在或曾被删除，请使用新的 ID。", 409
                    )
                record = {
                    "id": identifier,
                    "kind": doc["kind"],
                    "revision": 0,
                    "origin": "custom",
                    "status": "draft",
                    "draft": markdown,
                    "active_version": None,
                    "versions": [],
                    "updated_at": now,
                }
            else:
                record = deepcopy(records.get(identifier))
                if not record or record.get("deleted_at"):
                    raise CreativeTypeError("找不到此资料。", 404)
                if revision != record["revision"]:
                    raise CreativeTypeError("资料已被修改；请刷新后对照当前草稿。", 409)
                if action == "restore":
                    saved = next((v for v in record["versions"] if v["version"] == version), None)
                    if not saved or text_sha(saved["markdown"]) != saved["sha256"]:
                        raise CreativeTypeError("历史版本不存在或校验失败。", 409)
                    doc = parse_document(saved["markdown"], enabled=True)
                    self._associations(doc, records)
                    record.update(draft=saved["markdown"], active_version=version, status="enabled")
                elif action in ("draft", "activate"):
                    doc = parse_document(markdown, enabled=action == "activate")
                    if doc["id"] != identifier or doc["kind"] != record["kind"]:
                        raise CreativeTypeError("保存时不能改变稳定 ID 或资料种类。")
                    if action == "activate":
                        self._associations(doc, records)
                        number = (
                            max((v["version"] for v in record["versions"]), default=0)
                            + 1
                        )
                        record["versions"].append(
                            {
                                "version": number,
                                "markdown": markdown,
                                "sha256": text_sha(markdown),
                                "created_at": now,
                            }
                        )
                        record.update(active_version=number, status="enabled")
                    record["draft"] = markdown
                elif action in ("disable", "delete"):
                    if record["kind"] == "genre":
                        linked = [
                            active_document(r)["name"]
                            for r in records.values()
                            if r["kind"] == "tag"
                            and r["status"] == "enabled"
                            and not r.get("deleted_at")
                            and identifier in active_document(r)["genre_ids"]
                        ]
                        if linked:
                            raise CreativeTypeError(
                                "请先停用或解除这些 Tag 的关联：" + "、".join(linked),
                                409,
                            )
                    # Existing frozen stories do not depend on the live library.
                    record["status"] = "disabled"
                    if action == "delete":
                        record["deleted_at"] = now
                else:
                    raise CreativeTypeError("未知资料操作。")
            record.update(revision=record["revision"] + 1, updated_at=now)
            data["records"][identifier] = record
            data["operations"][operation_id] = {
                "request_sha256": request_sha,
                "result": deepcopy(record),
            }
            _atomic_json(self.path, data)
            return deepcopy(record)
