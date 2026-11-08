"""Append-only readership guidance for author-owned knowledge Markdown.

The original document is opaque. Only a final, explicitly delimited module is
interpreted; quoted/fenced examples never declare a document's audience. The
writer records the exact original-byte receipt so subsequent guide edits cannot
rewrite the author's existing material.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

import yaml

AudienceChannel = Literal["female", "male"]
CHANNELS = ("female", "male")
START = "<!-- novel-os:audience-guidance:start -->"
END = "<!-- novel-os:audience-guidance:end -->"
HEADINGS = {"female": "女频写作指导", "male": "男频写作指导"}


class AudienceGuidanceError(ValueError):
    pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _top_level_lines(text: str):
    """Yield unquoted, unfenced lines and their original character positions."""
    fence, position = None, 0
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        token = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if token and token[1][0] == fence[0] and len(token[1]) >= len(fence) and not token[2].strip():
                fence = None
        elif token:
            fence = token[1]
        elif not line.startswith((">", "    ", "\t")):
            yield position, line
        position += len(raw)


class _MetadataLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise AudienceGuidanceError("频道声明不支持 YAML 引用。")
        return super().compose_node(parent, index)


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise AudienceGuidanceError("频道声明字段必须唯一且为文本。")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_MetadataLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(frozen=True)
class AudienceGuidance:
    original: str
    audience_channels: tuple[AudienceChannel, ...]
    guides: dict[str, str]

    def public(self) -> dict:
        return {
            "schema": "audience-guidance.v1",
            "audience_channels": list(self.audience_channels),
            "guides": dict(self.guides),
            "original_sha256": _sha(self.original),
        }


def validate_guides(channels: list[str], guides: dict[str, str]) -> None:
    if not isinstance(channels, list) or not channels or any(c not in CHANNELS for c in channels) or len(set(channels)) != len(channels):
        raise AudienceGuidanceError("请选择不重复的女频／男频适用频道。")
    if not isinstance(guides, dict) or set(guides) - set(CHANNELS) or any(not isinstance(v, str) for v in guides.values()):
        raise AudienceGuidanceError("频道写作指导格式无效。")
    if any(not guides.get(c, "").strip() for c in channels):
        raise AudienceGuidanceError("每个适用频道都需要填写对应写作指导。")


def parse_audience_guidance(markdown: str) -> AudienceGuidance | None:
    markers = [(pos, line) for pos, line in _top_level_lines(markdown) if line in (START, END)]
    if not markers:
        return None
    if len(markers) != 2 or markers[0][1] != START or markers[1][1] != END:
        raise AudienceGuidanceError("频道指导块必须唯一且完整。")
    start, end = markers[0][0], markers[1][0]
    if markdown[end + len(END):].strip():
        raise AudienceGuidanceError("频道指导块必须位于文件末尾，原说明保留在它之前。")
    body = markdown[start + len(START):end]
    header = re.match(r"\s*## 频道写作指导\s*\r?\n+```yaml\r?\n(.*?)\r?\n```[ \t]*\r?\n", body, re.S)
    if not header or len(header[1].encode("utf-8")) > 2048:
        raise AudienceGuidanceError("频道指导块缺少有效的频道声明。")
    try:
        metadata = yaml.load(header[1], Loader=_MetadataLoader)
    except (yaml.YAMLError, RecursionError):
        raise AudienceGuidanceError("频道声明的 YAML 格式无效。") from None
    if not isinstance(metadata, dict) or metadata.get("schema") != "audience-guidance.v1" or set(metadata) - {"schema", "audience_channels", "original_sha256", "original_bytes"}:
        raise AudienceGuidanceError("频道声明格式无效。")
    prose = body[header.end():]
    headings = [(pos, channel) for pos, line in _top_level_lines(prose) for channel, title in HEADINGS.items() if line == "### " + title]
    if not headings or prose[:headings[0][0]].strip() or len({c for _, c in headings}) != len(headings):
        raise AudienceGuidanceError("请分别填写唯一的女频／男频写作指导分节。")
    guides = {}
    for i, (pos, channel) in enumerate(headings):
        tail = headings[i + 1][0] if i + 1 < len(headings) else len(prose)
        guides[channel] = prose[pos + len("### " + HEADINGS[channel]):tail].strip()
    validate_guides(metadata.get("audience_channels"), guides)
    original = markdown[:start]
    receipt = {k for k in metadata if k.startswith("original_")}
    if receipt:
        length = metadata.get("original_bytes")
        if receipt != {"original_sha256", "original_bytes"} or type(length) is not int or not 0 <= length <= len(original.encode("utf-8")):
            raise AudienceGuidanceError("频道指导块的原文校验信息无效。")
        try:
            restored = original.encode("utf-8")[:length].decode("utf-8")
        except UnicodeError:
            raise AudienceGuidanceError("频道指导块的原文字节边界无效。") from None
        if original[len(restored):].strip() or _sha(restored) != metadata["original_sha256"]:
            raise AudienceGuidanceError("原说明已改变，请核对后重新保存频道指导。")
        original = restored
    return AudienceGuidance(original, tuple(metadata["audience_channels"]), guides)


def replace_audience_guidance(markdown: str, channels: list[str], guides: dict[str, str]) -> str:
    """Append/update only the module, preserving every original byte."""
    validate_guides(channels, guides)
    current = parse_audience_guidance(markdown)
    original = current.original if current else markdown
    metadata = {
        "schema": "audience-guidance.v1", "audience_channels": channels,
        "original_sha256": _sha(original), "original_bytes": len(original.encode("utf-8")),
    }
    module = START + "\n## 频道写作指导\n\n```yaml\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "```\n\n"
    module += "\n\n".join("### " + HEADINGS[c] + "\n\n" + guides[c].strip() for c in CHANNELS if c in guides)
    result = original + ("" if original.endswith("\n\n") else "\n" if original.endswith("\n") else "\n\n") + module + "\n" + END + "\n"
    parsed = parse_audience_guidance(result)
    if parsed is None or parsed.original != original:
        raise AudienceGuidanceError("频道指导包含冲突的结构标记。")
    return result


def audience_metadata(markdown: str) -> dict:
    """Readiness is explicit; an invalid module never disables the whole library."""
    try:
        module = parse_audience_guidance(markdown)
    except AudienceGuidanceError as exc:
        return {"audience_channels": [], "audience_ready": False, "audience_issue": str(exc), "audience_guidance": None}
    return {
        "audience_channels": list(module.audience_channels) if module else [],
        "audience_ready": module is not None,
        "audience_issue": None if module else "尚未配置频道写作指导。",
        "audience_guidance": module.public() if module else None,
    }


def channel_interpretation(channel: AudienceChannel) -> str:
    label = "女频" if channel == "female" else "男频"
    return (
        f"本书已由作者选择{label}读者频道。这是市场阅读体验，不指定主角性别、性向、视角或结局。"
        "最新明确作者补充优先；保留此前未被推翻的要求。先遵守本书故事事实和作者选择，"
        "再把原说明与所选频道指导组合成具体故事。原文的默认频道/性别称谓由所选频道指导校准，"
        "不得借此改写作者明确的人物设定或抹去类型的核心阅读承诺。"
        "文档提及其他 Tag 的组合仅在那些 Tag 被作者选择时作为组合要求；未选择的 Tag 不是新增选择，"
        "不要自动加载、隐性替换或宣称采用。若推荐增换 Tag，请明确说明并等待作者选择。"
        "已选 Tags 要共同改变同一故事的选择、代价和回报，不能轮流贴标签或照搬固定章节节拍。"
    )
