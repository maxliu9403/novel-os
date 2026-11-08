"""Lossless presentation of a chapter plan: scene intent is not a fact checklist.

Only new, explicitly frozen execution policies use this view. No model call,
canon edit, inferred plot, keyword genre score, or silent text truncation.
"""
from __future__ import annotations

import hashlib
import json

from core.story_markdown import parse_markdown

VERSION = "scene-execution.v1"


def outline_view(text: str) -> dict:
    document = parse_markdown(text)
    roles = ["scene_plan"] * len(document.lines)
    # Exact standard section names only. Unknown layouts remain complete input.
    continuity = [s for s in document.headings if s.title.casefold() == "continuity notes"]
    starts = [i for i, line in enumerate(document.structural_lines)
              if line.strip() == "[CHAPTER_CONTRACT]"]
    ends = [i for i, line in enumerate(document.structural_lines)
            if line.strip() == "[/CHAPTER_CONTRACT]"]
    contract = None
    if len(starts) == len(ends) == 1 and starts[0] < ends[0]:
        contract = (starts[0], ends[0] + 1)
    for section in continuity:
        for i in range(section.heading_index, section.end_index):
            roles[i] = "continuity_constraints"
    if contract:
        for i in range(*contract):
            roles[i] = "chapter_contract"
    segments, start = [], 0
    while start < len(roles):
        end = start + 1
        while end < len(roles) and roles[end] == roles[start]:
            end += 1
        segments.append({"role": roles[start], "start_line": start + 1,
                         "end_line": end, "text": "".join(document.lines[start:end])})
        start = end
    return {"schema": VERSION, "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "coverage": "complete_original_outline", "segments": segments}


def render_outline(text: str) -> str:
    return ("[SCENE_EXECUTION_BRIEF]\n" + json.dumps(outline_view(text), ensure_ascii=False)
            + "\n[/SCENE_EXECUTION_BRIEF]")
