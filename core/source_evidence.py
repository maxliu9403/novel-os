"""Code-owned text ranges. Splitting preserves every character and source qualifier."""
from __future__ import annotations

from .creative_types import digest
from .source_routes import SourceRouteProtocolError


def segment(unit: dict, start: int = 0, end: int | None = None) -> dict:
    end = len(unit["text"]) if end is None else end
    body = {k: v for k, v in unit.items() if k not in {"text", "id", "start", "end"}}
    body.update(start=start, end=end, text=unit["text"][start:end])
    return {"id": "span_" + digest(body)[:24], **body}


def source_snapshot(library, source_id: str) -> dict:
    manifest = library.get(source_id)
    units = library.texts(source_id)
    spans = [segment(unit) for unit in units]
    return {"source_id": source_id, "source_sha256": manifest["source_sha256"],
            "source_index": [{k: v for k, v in item.items() if k != "text"} |
                             {"text_sha256": digest(item["text"])} for item in spans],
            "segments": spans}


def bisect_segment(span: dict) -> tuple[dict, dict]:
    """Split in Unicode codepoints, preferring a line break, without losing delimiters."""
    text = span["text"]
    if len(text) < 2:
        raise ValueError("单字符来源无法继续分割。")
    middle = len(text) // 2
    boundary = text.rfind("\n", middle // 2, middle)
    if boundary >= 0:
        middle = boundary + 1
    base = {k: v for k, v in span.items() if k not in {"id", "text", "start", "end"}}
    base["parent_id"] = span["id"]
    left = {**base, "start": span["start"], "end": span["start"] + middle, "text": text[:middle]}
    right = {**base, "start": span["start"] + middle, "end": span["end"], "text": text[middle:]}
    return ({"id": "span_" + digest(left)[:24], **left}, {"id": "span_" + digest(right)[:24], **right})


def verify_coverage(original: list[dict], parts: list[dict]):
    for unit in original:
        matching = sorted((p for p in parts if p["path"] == unit["path"]), key=lambda p: p["start"])
        cursor = 0
        for part in matching:
            if part["start"] != cursor or part["end"] - part["start"] != len(part["text"]):
                raise ValueError("来源覆盖出现缺口或重叠。")
            cursor = part["end"]
        if "".join(p["text"] for p in matching) != unit["text"]:
            raise ValueError("来源分片没有逐字符保全原文。")
    if {p["path"] for p in parts} != {p["path"] for p in original}:
        raise ValueError("来源分片包含未知文件。")


def bind_observations(content: dict, spans: list[dict], *, prior_ids: set[str] | None = None) -> list[dict]:
    allowed = {item["id"] for item in spans}
    existing = prior_ids or set()
    local_ids = set()
    ledger = []
    for item in content["observations"]:
        if item["local_id"] in local_ids or not set(item["source_refs"]) <= allowed:
            raise SourceRouteProtocolError("来源观察的局部编号重复或引用越界。")
        if not set(item["supersedes"]) <= existing:
            raise SourceRouteProtocolError("来源纠正引用了不存在的旧观察。")
        local_ids.add(item["local_id"])
        body = {k: v for k, v in item.items() if k != "local_id"}
        ledger.append({"id": "evidence_" + digest(body)[:24], **body})
    return ledger
