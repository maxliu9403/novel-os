"""Deterministic chapter-source views; the adopted standalone Markdown stays intact.

Recognized six-section and historical 17-section skeletons can be projected.
Chapter execution selects its source span; story-first delivery splits global
design from the complete chapter map. Unknown layouts fall back to the full
source rather than guessing which content can be omitted.
"""
from __future__ import annotations

import hashlib
import json
import re

VERSION = "skeleton-projection.v1"
RUNTIME_NOTE = """<!-- NOVEL_OS_RUNTIME_PROTOCOL_V1 -->
通用执行协议由 Novel OS 的版本化 Agent 提示与代码校验提供；本节上方仅维护本书特有的文风要求。
剧情线状态仅使用 active / resolved / abandoned / foreshadowed；具体结局意义写入选择与后果。
人物弧光阶段仅使用 beginning / middle / climax / resolution；语义结果只有在正文实现后记录，未实现时省略 outcome 字段。
伏笔须区分 recalled / paid / intentional_open；已提醒不等于已回收。计划、已发生事实与角色知情范围分别记录。
正式正文、状态记录和交付包由各自输出协议处理；不把设计记录、协议或评议混入公开正文。
<!-- /NOVEL_OS_RUNTIME_PROTOCOL_V1 -->"""


def _headings(text, pattern):
    """Markdown headings outside both backtick and tilde fences."""
    found, offset, fence = [], 0, None
    for line in text.splitlines(keepends=True):
        mark = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if mark:
            token, tail = mark.groups()
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not tail.strip():
                fence = None
        elif fence is None:
            match = re.match(pattern, line)
            if match:
                found.append((offset, offset + len(line), match))
        offset += len(line)
    return found if fence is None else []


def story_parts(source: str) -> dict | None:
    """Split a code-rendered story-first skeleton without rewriting story text.

    Only the known six-section layout and terminal code-owned contracts qualify.
    Unknown extensions, malformed contracts and chapter gaps retain the full source
    at the caller. This is a request view, never a replacement for the saved file.
    """
    if not source.startswith("<!-- story-first.v1 -->\n"):
        return None
    sections = _headings(source, r"^##\s+(\d+)\.\s+[^\n]+")
    if ([int(s[2][1]) for s in sections] != list(range(1, 7))
            or len(_headings(source, r"^##\s+")) != 6):
        return None
    # Contracts must be at the end, not inside prose or a Markdown code fence.
    marker = r"\[(NARRATIVE_FORMAT_JSON|NOVEL_CLASSIFICATION_JSON)\]"
    starts = _headings(source, r"^" + marker + r"\s*$")
    if len(starts) != 2 or starts[0][0] <= sections[-1][1]:
        return None
    tail = source[starts[0][0]:]
    blocks = re.fullmatch(
        r"\[NARRATIVE_FORMAT_JSON\]\s*(.*?)\s*\[/NARRATIVE_FORMAT_JSON\]\s*"
        r"\[NOVEL_CLASSIFICATION_JSON\]\s*(.*?)\s*\[/NOVEL_CLASSIFICATION_JSON\]\s*",
        tail, re.DOTALL)
    if blocks is None:
        return None
    try:
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate field")
                value[key] = item
            return value
        def invalid_constant(_):
            raise ValueError("non-finite number")
        contracts = [json.loads(b, object_pairs_hook=unique, parse_constant=invalid_constant) for b in blocks.groups()]
        if not all(isinstance(c, dict) for c in contracts):
            return None
    except (ValueError, RecursionError):
        return None
    chapter_text = source[sections[3][0]:sections[4][0]]
    chapters = _headings(chapter_text, r"^###\s+Chapter\s+([0-9]+)\b")
    count = contracts[0].get("total_chapters")
    header_count = re.findall(r"^Chapters:\s*(\d+)\s*$", source[:sections[0][0]], re.MULTILINE)
    if (type(count) is not int or not 1 <= count <= 511
            or header_count != [str(count)]
            or [int(c[2][1]) for c in chapters] != list(range(1, count + 1))
            or len(_headings(chapter_text, r"^###\s+")) != count):
        return None
    return {
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "source_bytes": len(source.encode()), "chapter_count": count,
        "header": source[:sections[0][0]],
        "global_text": source[sections[0][0]:sections[3][0]] + source[sections[4][0]:starts[0][0]],
        "chapter_text": chapter_text,
        "chapter_index": [chapter_text[start:end].strip() for start, end, _ in chapters],
        "narrative_format": contracts[0], "publication_metadata": contracts[1],
    }


def _chapter_layout(source: str, chapter_count: int) -> tuple:
    """One strict boundary parser shared by source views and chapter locators."""
    sections = _headings(source, r"^##\s+(\d+)\.\s+[^\n]+")
    all_sections = _headings(source, r"^##\s+")
    compact = source.startswith("<!-- story-first.v1 -->\n")
    if ([int(s[2][1]) for s in sections] != list(range(1, 7 if compact else 18))
            or [s[0] for s in all_sections] != [s[0] for s in sections]):
        raise ValueError("Unrecognized section layout; preserve complete source")
    start, end = sections[3 if compact else 11][1], sections[4 if compact else 12][0]
    body = source[start:end]
    headings = _headings(body, r"^###\s+Chapter\s+([0-9]+)\b")
    if [int(h[2][1]) for h in headings] != list(range(1, chapter_count + 1)):
        raise ValueError("Unrecognized chapter layout; preserve complete source")
    # Assembly owns this extension heading. It describes the entire map, not
    # just the final chapter, so retain it even when that chapter is omitted.
    extensions = _headings(body, r"^### 完整章节规划补充记录\s*$")
    if len(extensions) > 1 or (extensions and extensions[0][0] <= headings[-1][0]):
        raise ValueError("Ambiguous map extension; preserve complete source")
    known = {h[0] for h in headings + extensions}
    if any(h[0] not in known for h in _headings(body, r"^###\s+")):
        raise ValueError("Unknown chapter-map subsection; preserve complete source")
    chapter_end = extensions[0][0] if extensions else len(body)
    return start, end, body, headings, chapter_end


def chapter_locations(source: str, chapter_count: int) -> dict[int, dict]:
    """Locate complete numbered source plans, never infer story equivalence.

    Character offsets address the exact frozen Python string, not UTF-8 bytes.
    Unknown/ambiguous layouts or empty chapter bodies cannot supply locations.
    """
    try:
        start, _, body, headings, chapter_end = _chapter_layout(source, chapter_count)
    except ValueError:
        return {}
    locations = {}
    for index, (offset, heading_end, match) in enumerate(headings):
        stop = headings[index + 1][0] if index + 1 < len(headings) else chapter_end
        if not body[heading_end:stop].strip():
            return {}
        locations[int(match[1])] = {
            "quote": body[offset:heading_end].rstrip("\r\n"),
            "source_chars": [start + offset, start + stop],
        }
    return locations


def global_source_index(source: str, chapter_count: int) -> dict | None:
    """Preserve all non-chapter text and every title. Only chapter bodies are deferred."""
    if source.startswith("<!-- story-first.v1 -->") and story_parts(source) is None:
        return None
    try:
        start, end, body, headings, chapter_end = _chapter_layout(source, chapter_count)
    except ValueError:
        return None
    pieces = [source[:start], body[:headings[0][0]]]
    for i, (offset, heading_end, _) in enumerate(headings):
        stop = headings[i + 1][0] if i + 1 < len(headings) else chapter_end
        if not body[heading_end:stop].strip():
            return None
        pieces.append(body[offset:heading_end])
    pieces.extend([body[chapter_end:], source[end:]])
    text = "".join(pieces)
    return {"schema": VERSION, "mode": "global_sections_and_chapter_titles", "text": text,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(), "source_bytes": len(source.encode()),
            "projected_bytes": len(text.encode()), "chapter_count": chapter_count,
            "chapter_bodies_deferred": list(range(1, chapter_count + 1))}


def chapter_source(source: str, requested: list[int], chapter_count: int) -> dict:
    """Keep every global section and requested/adjacent original chapter plans.

    This is disclosed selection, not a summary or a complete-source claim.
    Unknown extension text outside chapter spans is never discarded.
    """
    base = {"schema": VERSION, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "source_bytes": len(source.encode()), "requested_chapters": list(requested)}

    def full(reason):
        return {**base, "mode": "complete", "reason": reason, "text": source,
                "included_chapters": list(range(1, chapter_count + 1)), "omitted_chapters": [],
                "projected_bytes": len(source.encode())}

    if not requested or any(type(n) is not int or not 1 <= n <= chapter_count for n in requested):
        return full("No valid bounded chapter selection")
    try:
        start, end, body, headings, chapter_end = _chapter_layout(source, chapter_count)
    except ValueError as exc:
        return full(str(exc))
    included = sorted({n for r in requested for n in (r - 1, r, r + 1) if 1 <= n <= chapter_count})
    omitted = [n for n in range(1, chapter_count + 1) if n not in included]
    if not omitted:
        return full("All chapter plans needed")
    chunks = [body[:headings[0][0]]]
    for index, (offset, _, match) in enumerate(headings):
        stop = headings[index + 1][0] if index + 1 < len(headings) else chapter_end
        if int(match[1]) in included:
            chunks.append(body[offset:stop])
    chunks.append(body[chapter_end:])
    text = source[:start] + "".join(chunks) + source[end:]
    return {**base, "mode": "chapter_selection", "text": text,
            "included_chapters": included, "omitted_chapters": omitted,
            "projected_bytes": len(text.encode()),
            "reason": "All global sections retained verbatim; only distant chapter-plan bodies omitted. "
                      "Use the frozen whole-book design for future obligations. This is NOT occurred prose or a complete skeleton."}
