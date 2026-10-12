"""Plan contextual source reviews without changing frozen extraction segments.

Owned member spans partition each document exactly once. Heading context may
refer to earlier bytes, but never changes ownership or creates story evidence.
This module performs no I/O, model calls, semantic classification or truncation.
The caller checks the budget of the complete model request before dispatch.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence


REVIEW_UNIT_VERSION = "cover-source-review-units.v1"
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _source_lines(document_id: str, text: str, document_sha256: str) -> list[dict]:
    """Read actual ATX levels and fenced-code boundaries, independent of v1 paths."""
    result, stack = [], []
    offset = 0
    fence: tuple[str, int] | None = None
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        end = offset + len(raw_line.encode("utf-8"))
        heading = None
        marker = _FENCE.match(line)
        if fence is not None:
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1]
                    and not marker[2].strip(" \t")):
                fence = None
        elif marker and (marker[1][0] != "`" or "`" not in marker[2]):
            fence = (marker[1][0], len(marker[1]))
        else:
            match = _ATX.match(line)
            if match:
                level = len(match[1])
                title = re.sub(r"[ \t]+#+[ \t]*$", "", match[2] or "").strip(" \t")
                heading = {"document_id": document_id, "document_sha256": document_sha256,
                    "level": level, "title": title, "text": raw_line,
                    "byte_start": offset, "byte_end": end,
                    "sha256": hashlib.sha256(raw_line.encode("utf-8")).hexdigest()}
                while stack and stack[-1]["level"] >= level:
                    stack.pop()
                stack.append(heading)
        result.append({"byte_start": offset, "byte_end": end,
                       "heading": heading, "source_context": list(stack)})
        offset = end
    return result


def source_heading_spans(document_id: str, text: str, document_sha256: str) -> tuple[tuple[int, tuple[str, ...]], ...]:
    """Return heading changes at exact UTF-8 offsets using the review parser.

    Consumers can attribute a quotation to its actual chapter even when one
    extraction request contains multiple sections. Fenced or indented code
    cannot create fictitious source headings.
    """
    return tuple((line["byte_start"], tuple(item["title"] for item in line["source_context"]))
                 for line in _source_lines(document_id, text, document_sha256) if line["heading"])


def build_review_units(documents: Sequence[Mapping[str, Any]],
                       segments: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Group adjacent parent/child heading segments with their first body segment.

    Inputs are frozen documents (document_id/text/sha256) and the original
    segment dictionaries. A malformed or incomplete partition raises ValueError;
    it is never repaired by dropping, duplicating, reordering or slicing bytes.
    Sibling headings without intervening prose remain separate review units.
    """
    sources: dict[str, dict] = {}
    for index, document in enumerate(documents):
        document_id, text = document.get("document_id"), document.get("text")
        if not isinstance(document_id, str) or not document_id or document_id in sources or not isinstance(text, str):
            raise ValueError("Review documents require unique IDs and frozen text")
        body = text.encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        if document.get("sha256") != digest:
            raise ValueError("Review document hash does not match frozen source bytes")
        sources[document_id] = {"body": body, "sha256": digest, "index": index, "cursor": 0,
                                "lines": _source_lines(document_id, text, digest)}

    members, seen = [], set()
    last_document_index = -1
    for segment in segments:
        segment_id, document_id = segment.get("segment_id"), segment.get("document_id")
        if not isinstance(segment_id, str) or not segment_id or segment_id in seen:
            raise ValueError("Review segment IDs must be present and unique")
        if document_id not in sources:
            raise ValueError("Review segment references an unknown document")
        source = sources[document_id]
        start, end, text = segment.get("byte_start"), segment.get("byte_end"), segment.get("text")
        if (type(start) is not int or type(end) is not int or start != source["cursor"]
                or not start < end <= len(source["body"]) or source["index"] < last_document_index):
            raise ValueError("Review segments must form an ordered, exact source partition")
        if (not isinstance(text, str) or source["body"][start:end] != text.encode("utf-8")
                or segment.get("document_sha256") != source["sha256"]):
            raise ValueError("Review segment text or hash does not match its frozen source span")
        seen.add(segment_id)
        source["cursor"], last_document_index = end, source["index"]
        meaningful = []
        overlapping = []
        for line in source["lines"]:
            if line["byte_end"] <= start:
                continue
            if line["byte_start"] >= end:
                break
            overlapping.append(line)
            piece = source["body"][max(start, line["byte_start"]):min(end, line["byte_end"])].decode("utf-8")
            if piece.strip():
                meaningful.append(line)
        role = "whitespace" if not meaningful else "heading" if all(line["heading"] for line in meaningful) else "content"
        context = (meaningful or overlapping)[0]["source_context"]
        members.append({"segment_id": segment_id, "document_id": document_id,
            "document_sha256": source["sha256"], "byte_start": start, "byte_end": end,
            "text": text, "structural_role": role,
            "source_context": [dict(heading) for heading in context]})
    if any(source["cursor"] != len(source["body"]) for source in sources.values()):
        raise ValueError("Review segments do not cover every frozen source byte")

    units, pending = [], []

    def emit(group):
        identity = {"version": REVIEW_UNIT_VERSION, "members": group}
        units.append({"unit_id": "review-unit-" + _hash(identity)[:24],
            "document_id": group[0]["document_id"], "document_sha256": group[0]["document_sha256"],
            "byte_start": group[0]["byte_start"], "byte_end": group[-1]["byte_end"],
            "text": "".join(member["text"] for member in group), "members": group})

    for member in members:
        if pending and member["document_id"] != pending[-1]["document_id"]:
            emit(pending)
            pending = []
        role = member["structural_role"]
        if role == "heading":
            current_heading = member["source_context"][-1]
            previous_heading = next((item["source_context"][-1] for item in reversed(pending)
                                     if item["structural_role"] == "heading"), None)
            if (previous_heading and current_heading["byte_start"] != previous_heading["byte_start"]
                    and current_heading["level"] <= previous_heading["level"]):
                emit(pending)
                pending = []
            pending.append(member)
        elif role == "whitespace" and pending:
            pending.append(member)
        else:
            emit([*pending, member])
            pending = []
    if pending:
        emit(pending)
    return units


def review_plan_identity(units: Sequence[Mapping[str, Any]]) -> str:
    """Bind ordered ownership and exact borrowed context to the plan contract."""
    return _hash({"version": REVIEW_UNIT_VERSION, "units": units})
