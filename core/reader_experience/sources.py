"""Shared reader authority, immutable prose snapshots and exact quote resolution."""
from __future__ import annotations
import json
import re
from functools import lru_cache
from pathlib import Path
from core.manuscript_reader import _load_reader_state, _artifact_snapshot, _selected_prose
from core.safe_project_io import read_project_text
from core.prose_sanitize import readable_prose_mapping
from .models import EXTRACTOR, canonical, digest, text_sha


@lru_cache(maxsize=1)
def tokenizer():
    import tiktoken
    return tiktoken.get_encoding("cl100k_base")


def tokens(value):
    text = value if isinstance(value, str) else canonical(value).decode()
    return len(tokenizer().encode(text, disallowed_special=()))


def snapshot(project: Path, project_id: str, options, *, mappings=True):
    state = _load_reader_state(project, project_id)
    raw_state = json.loads(read_project_text(project, ("outputs", "state", "story_state.json")))
    # Only count metadata: the rest of state (including future knowledge) is never sent.
    metadata = raw_state.get("metadata", {})
    genres = metadata.get("genres") or metadata.get("genre") or []
    genres = [genres] if isinstance(genres, str) else genres
    if not isinstance(genres, list) or any(not isinstance(g, str) for g in genres):
        raise ValueError("作品公开类型格式无效，请先核对作品设置。")
    known = max((c.number for c in state.chapters), default=0)
    declared = metadata.get("target_chapters") or metadata.get("estimated_chapters") or metadata.get("chapter_count")
    expected = max(known, declared if type(declared) is int and 0 < declared <= 10000 else 0)
    last = options.last_chapter or expected
    if last < options.first_chapter or not expected:
        raise ValueError("所选范围没有章节，请先生成正文。")
    chapters, missing = [], []
    with _artifact_snapshot(project) as authority:
        records = {c.number: c for c in state.chapters}
        for number in range(options.first_chapter, last + 1):
            selected = _selected_prose(project, number, authority) if number in records else None
            if not selected or not selected.markdown:
                missing.append(number)
                continue
            raw, body = selected.raw, selected.markdown
            mapping = []
            if mappings:
                cleaned, mapping = readable_prose_mapping(raw)
                if cleaned != body:
                    raise ValueError("阅读器正文清理版本与来源映射不一致")
            chapters.append({"number": number, "version": selected.version, "source": selected.source,
                             "raw_sha256": text_sha(raw), "body_sha256": text_sha(body), "body": body,
                             "mapping": mapping})
    if not chapters:
        raise ValueError("所选范围没有可阅读正文；空白 final 不会回退到旧稿。")
    fingerprint = digest([{k: c[k] for k in ("number", "version", "source", "raw_sha256", "body_sha256")} for c in chapters])
    return {"extractor": EXTRACTOR, "offset_unit": "unicode_codepoints", "project_id": project_id,
            "title": state.title, "expected_chapters": expected, "first_chapter": options.first_chapter,
            "public_genres": genres,
            "last_chapter": last, "missing_chapters": missing, "chapters": chapters,
            "complete_book": options.first_chapter == 1 and last == expected and not missing,
            "source_sha256": digest({"fingerprint": fingerprint, "expected": expected, "last": last, "missing": missing})}


def units_for(source, limit):
    """Never sample/truncate prose. Prefer paragraph boundaries, split huge paragraphs."""
    units = []
    for chapter in source["chapters"]:
        body, start = chapter["body"], 0
        boundaries = [m.end() for m in re.finditer(r"\n\s*\n", body)]
        while start < len(body):
            low, high = start + 1, min(len(body), start + limit * 6)
            while high < len(body) and tokens(body[start:high]) <= limit:
                high = min(len(body), start + (high - start) * 2)
            # Binary search uses actual reference-token counts, not byte quotas.
            while low <= high:
                mid = (low + high) // 2
                if tokens(body[start:mid]) <= limit:
                    low = mid + 1
                else:
                    high = mid - 1
            end = max(start + 1, high)
            candidates = [b for b in boundaries if start < b <= end]
            if end < len(body) and candidates:
                end = candidates[-1]
            units.append({"id": f"u{len(units)+1:04d}", "chapter": chapter["number"], "start": start,
                          "end": end, "body_sha256": chapter["body_sha256"], "text": body[start:end]})
            start = end
    return units


def resolve_quote(quote, source, units, allowed_ids):
    unit = next((u for u in units if u["id"] == quote["unit_id"] and u["id"] in allowed_ids), None)
    if unit is None:
        raise ValueError("引用不属于本次阅读段落")
    body = next(c["body"] for c in source["chapters"] if c["number"] == unit["chapter"])
    text = quote["quote"]
    start = quote.get("start")
    if start is None:
        first = body.find(text, unit["start"], unit["end"])
        if first < 0 or body.find(text, first + 1, unit["end"]) >= 0:
            raise ValueError("引用缺失或重复，必须给出准确 Unicode 原文位置")
        start = first
    end = start + len(text)
    if not unit["start"] <= start < end <= unit["end"] or body[start:end] != text:
        raise ValueError("引用与正文不一致")
    result = {"chapter": unit["chapter"], "unit_id": unit["id"], "start": start, "end": end,
              "quote": text, "body_sha256": unit["body_sha256"]}
    return {"id": "e_" + digest(result)[:24], **result}
