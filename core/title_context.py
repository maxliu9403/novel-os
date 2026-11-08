"""Deterministic naming projection. Select whole story sections, never summarize with an LLM."""
from __future__ import annotations

import hashlib
import json
import re

from .story_markdown import module_number, parse_markdown
from .author_requirements import build_requirements, validate_requirements

MAX_CONTEXT_BYTES = 64_000
PROJECTION_VERSION = "title-context.v2"


def encode(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _without_contracts(text: str) -> str:
    # Only transport contracts are removed; narrative facts and story JSON remain intact.
    text = re.sub(r"\[(NARRATIVE_FORMAT_JSON|NOVEL_CLASSIFICATION_JSON)\].*?\[/\1\]", "", text, flags=re.S)
    text = re.sub(r"<!-- NOVEL_OS_RUNTIME_PROTOCOL_V1 -->.*?<!-- /NOVEL_OS_RUNTIME_PROTOCOL_V1 -->", "", text, flags=re.S)
    return re.sub(r"COVER_HANDOFF_BEGIN.*?COVER_HANDOFF_END", "", text, flags=re.S).strip()


def build_title_context(session: dict) -> dict:
    source = session["prompt_md"]
    doc = parse_markdown(source)
    sections = [s for s in doc.headings if s.level == 2]
    compact = any("故事承诺与创作决定" in s.title for s in sections)
    chosen = {1: "promise", 2: "relationships", 3: "story", 5: "payoffs", 6: "ending"} if compact else {
        2: "audience", 3: "story", 4: "constraints", 5: "characters", 6: "relationships", 13: "ending",
    }
    sources = []
    recognized = compact or any("故事核心与完整梗概" in s.title for s in sections)
    if recognized:
        for section in sections:
            number, _ = module_number(section.title)
            if number not in chosen:
                continue
            content = _without_contracts(section.body)
            if content:
                sources.append({"id": chosen[number], "purpose": "private_spoiler_boundary" if chosen[number] in {"ending", "payoffs"} else "story_facts",
                                "text": content})
        chapter_number = 4 if compact else 12
        chapter_section = next((s for s in sections if module_number(s.title)[0] == chapter_number), None)
        if chapter_section:
            first = next((s for s in doc.headings if s.level == 3 and chapter_section.heading_index < s.heading_index < chapter_section.end_index), None)
            if first:
                sources.append({"id": "opening", "purpose": "opening_hook", "text": _without_contracts(first.body)})
    else:
        # Small hand-authored skeletons have no standardized modules. No lossy arbitrary slicing.
        content = _without_contracts(source)
        if len(content.encode("utf-8")) > 16_000:
            raise ValueError("未识别此骨架的故事摘要结构，无法安全提取命名资料。请先整理故事梗概与人物关系，再推荐书名。")
        sources.append({"id": "story", "purpose": "story_facts", "text": content})
    if not sources or not any(s["id"] in {"story", "promise"} for s in sources):
        raise ValueError("骨架缺少故事主线，无法推荐可靠的书名。")

    header_end = sections[0].heading_index if sections else len(doc.lines)
    header = "".join(doc.lines[:header_end])
    metadata = {}
    for label in ("Genre", "Audience", "Language", "Market", "Region"):
        match = re.search(r"^[ \t]*(?:[-*]\s*)?(?:\*\*)?" + label + r"(?:\*\*)?\s*[:：]\s*(.+)$", header, re.M | re.I)
        fallback = session.get("input", {}).get(label.lower())
        if match or isinstance(fallback, str) and fallback.strip():
            metadata[label.lower()] = match.group(1).strip() if match else fallback.strip()

    history = []
    for request in session.get("naming", {}).get("requests", []):
        item = {key: request[key].strip() for key in ("instructions", "current_title")
                if isinstance(request.get(key), str) and request[key].strip()}
        if item and (not history or history[-1] != item):
            history.append(item)
    previous = [item["title"] for item in session.get("naming", {}).get("candidates", []) if isinstance(item.get("title"), str)]
    source_sha = hashlib.sha256(source.encode()).hexdigest()
    requirements = build_requirements("naming", [
        {"id": f"naming-request-{index + 1}", "text": encode(item), "source": "naming.requests"}
        for index, item in enumerate(history)
    ], source_sha256=source_sha)
    context = {"metadata": metadata, "sources": sources, "author_requests": history,
               "previous_titles": previous, "author_requirements_sha256": requirements["sha256"]}
    size = len(encode(context).encode("utf-8"))
    if size > MAX_CONTEXT_BYTES:
        raise ValueError(f"命名资料为 {size:,} 字节，超过 {MAX_CONTEXT_BYTES:,} 字节上限；请精简故事摘要或重复命名要求。内容未截断，也未调用模型。")
    return {"projection": PROJECTION_VERSION, "source_sha256": source_sha,
            "context": context, "context_sha256": hashlib.sha256(encode(context).encode()).hexdigest(),
            "author_requirements": requirements,
            "context_bytes": size, "skeleton_bytes": len(source.encode("utf-8"))}


def request_context(session: dict) -> dict:
    request = session.get("naming", {}).get("requests", [{}])[-1]
    projection = request.get("title_context") or build_title_context(session)
    if projection["source_sha256"] != hashlib.sha256(session["prompt_md"].encode()).hexdigest():
        raise ValueError("命名资料与当前骨架不一致，请重新确认骨架。")
    if projection.get("projection") == PROJECTION_VERSION:
        requirements = validate_requirements(projection.get("author_requirements"), scope="naming",
                                             source_sha256=projection["source_sha256"])
        if (projection.get("context_sha256") != hashlib.sha256(encode(projection["context"]).encode()).hexdigest()
                or projection["context"].get("author_requirements_sha256") != requirements["sha256"]):
            raise ValueError("命名要求或资料快照已变化，请重新提交；已有书名建议保留。")
    return projection
