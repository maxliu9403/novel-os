"""Deterministic, read-only projection of an adopted story skeleton.

``outputs/input/prompt.md`` is the primary authority.  ``outline.json`` may
only fill genuinely absent structured records, and ``story_state.json`` is
used to retain established character ids.  No manuscript, ``*.raw`` response,
model, or canonical write participates in this projection.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

from .safe_project_io import read_project_snapshot
from .story_characters import (
    character_id as _character_id, explicit_character_ids as _explicit_character_ids,
    is_character_section as _is_character_section, is_relationship_section as _is_relationship_section,
    parse_characters as _parse_characters, state_characters as _state_characters,
)
from .story_markdown import (
    HeadingSection, chapter_title as _chapter_title,
    heading_sections as _heading_sections, json_blocks as _json_blocks,
    module_number as _module_number, normalized as _normalized,
    parse_markdown, source_ref as _source, tables as _tables,
)


PROMPT_PATH = "outputs/input/prompt.md"
OUTLINE_PATH = "outputs/outline.json"
STATE_PATH = "outputs/state/story_state.json"

_NUMBERED_CHAPTER = re.compile(r"^(\d{1,4})\s*[.)、．:：-]\s*(.+)$")


@dataclass(frozen=True)
class Snapshot:
    relative_path: str
    data: bytes
    sha256: str
    modified_ns: int

    @property
    def text(self) -> str | None:
        try:
            return self.data.decode("utf-8")
        except UnicodeDecodeError:
            return None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_snapshot(project: Path, relative: str) -> tuple[Snapshot | None, str | None]:
    """Use the shared read-only file boundary for a consistent source snapshot."""
    try:
        value = read_project_snapshot(Path(project).absolute(), Path(relative).parts, missing_ok=True)
    except (OSError, ValueError) as exc:
        return None, f"Unsafe or unreadable source was ignored: {relative}. {exc}"
    if value is None:
        return None, None
    return Snapshot(relative, value.data, _sha(value.data), value.modified_ns), None


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _document_sections(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    lines = _lines(text)
    headings = []
    for section in parse_markdown(text).headings:
        if section.level == 2:
            number, title = _module_number(section.title)
            headings.append((section.heading_index, number, title, section.title))
    numbered = [item for item in headings if item[1] is not None]
    selected = numbered if numbered else headings
    if not selected and text:
        blocks, warnings = _json_blocks(text, 1, "Story Skeleton")
        return [{
            "id": "source",
            "number": None,
            "title": "Story Skeleton",
            "markdown": text,
            "json_blocks": blocks,
            "source": _source("adopted_skeleton", PROMPT_PATH, title="Story Skeleton",
                              line_start=1, line_end=len(lines)),
        }], warnings

    result: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    for position, (index, number, title, raw_title) in enumerate(selected):
        end = selected[position + 1][0] if position + 1 < len(selected) else len(lines)
        markdown = "".join(lines[index + 1:end])
        base_line = index + 2  # first body line, one based
        blocks, block_warnings = _json_blocks(markdown, base_line, raw_title)
        warnings.extend(block_warnings)
        section_id = f"section-{number}" if number is not None else re.sub(
            r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", title)
            .encode("ascii", "ignore").decode().casefold()).strip("-") or f"section-line-{index + 1}"
        if section_id in seen_ids:
            section_id = f"{section_id}-line-{index + 1}"
        seen_ids.add(section_id)
        result.append({
            "id": section_id,
            "number": number,
            "title": title,
            "markdown": markdown,
            "json_blocks": blocks,
            "source": _source("adopted_skeleton", PROMPT_PATH, title=raw_title,
                              line_start=index + 1, line_end=end),
        })
    return result, warnings


def _is_chapter_container(section: HeadingSection) -> bool:
    title = _normalized(section.title)
    return any(token in title for token in (
        "完整分章", "分章细纲", "章节细纲", "章节大纲", "逐章大纲", "逐章故事设计", "前三章",
        "chapteroutline", "chapterplan", "chaptersummaries",
    ))


def _is_narrative_section(section: HeadingSection) -> bool:
    title = _normalized(section.title)
    return any(token in title for token in (
        "故事核心", "完整梗概", "故事梗概", "故事简介", "剧情梗概", "完整故事", "全书故事走向",
        "synopsis", "storysummary", "storyoverview",
    ))


def _clean_chapter_body(body: str) -> str:
    cleaned = [re.sub(r"^\s*>\s?", "", line).rstrip()
               for line in body.strip().splitlines()]
    return "\n".join(cleaned).strip()


def _chapter_table_rows(text: str, container: HeadingSection) -> list[dict[str, Any]]:
    lines = _lines(text)
    result: list[dict[str, Any]] = []
    for headers, rows in _tables(lines, container.heading_index + 1, container.end_index):
        names = [_normalized(item) for item in headers]
        number_index = next((i for i, name in enumerate(names)
                             if name in {"章", "章节", "章号", "chapter", "number", "no"}), None)
        title_index = next((i for i, name in enumerate(names)
                            if name in {"标题", "章名", "title"}), None)
        summary_index = next((i for i, name in enumerate(names)
                              if any(token in name for token in (
                                  "简介", "摘要", "梗概", "事件", "内容", "目标", "summary",
                                  "description", "outline"))), None)
        if number_index is None or (title_index is None and summary_index is None):
            continue
        for line_index, cells in rows:
            parsed = _chapter_title(cells[number_index], allow_numbered=True)
            if parsed:
                number = parsed[0]
            else:
                raw_number = re.search(r"\d{1,4}", cells[number_index])
                if not raw_number or int(raw_number.group()) <= 0:
                    continue
                number = int(raw_number.group())
            title = cells[title_index].strip() if title_index is not None else ""
            summary = cells[summary_index].strip() if summary_index is not None else ""
            result.append({
                "number": number,
                "title": title or f"Chapter {number}",
                "summary": summary,
                "source": _source("adopted_skeleton", PROMPT_PATH, container,
                                  line_start=line_index + 1, line_end=line_index + 1),
            })
    return result


def _parse_chapters(text: str, headings: list[HeadingSection]) -> list[dict[str, Any]]:
    containers = [section for section in headings if _is_chapter_container(section)]
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    chapter_sections: list[HeadingSection] = []
    for section in headings:
        container = next((item for item in containers
                          if item.heading_index < section.heading_index < item.end_index), None)
        parsed = _chapter_title(section.title, allow_numbered=container is not None)
        if not parsed:
            continue
        chapter_sections.append(section)
        number, title = parsed
        summary = _clean_chapter_body(section.body)
        score = (100 if container else 0) + (20 if summary else 0) + (10 if title else 0)
        candidates.append((number, score, {
            "number": number,
            "title": title or f"Chapter {number}",
            "summary": summary,
            "source": _source("adopted_skeleton", PROMPT_PATH, section),
        }))
    for container in containers:
        for item in _chapter_table_rows(text, container):
            line_index = item["source"]["line_start"] - 1
            if not any(section.heading_index < line_index < section.end_index for section in chapter_sections):
                candidates.append((item["number"], 90 + (20 if item["summary"] else 0), item))

    lines = parse_markdown(text).structural_lines
    for container in containers:
        for line_index in range(container.heading_index + 1, container.end_index):
            # Numbered scenes/goals inside a chapter remain that chapter's body.
            if any(section.heading_index < line_index < section.end_index for section in chapter_sections):
                continue
            line = lines[line_index].strip()
            match = _NUMBERED_CHAPTER.match(line)
            if not match:
                continue
            number = int(match.group(1))
            value = match.group(2).strip()
            title, separator, summary = value.partition(" — ")
            if not separator:
                title, separator, summary = value.partition(": ")
            item = {
                "number": number,
                "title": title.strip() or f"Chapter {number}",
                "summary": summary.strip(),
                "source": _source("adopted_skeleton", PROMPT_PATH, container,
                                  line_start=line_index + 1, line_end=line_index + 1),
            }
            candidates.append((number, 70 + (10 if summary else 0), item))

    chosen: dict[int, tuple[int, dict[str, Any]]] = {}
    for number, score, item in candidates:
        if number <= 0:
            continue
        previous = chosen.get(number)
        if previous is None or score > previous[0]:
            chosen[number] = score, item
    return [chosen[number][1] for number in sorted(chosen)]


def _json_dict(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _valid_json_object(text: str | None) -> bool:
    if text is None:
        return False
    try:
        return isinstance(json.loads(text), dict)
    except (TypeError, ValueError):
        return False


def _id_pairs(value: str) -> list[tuple[str, str]]:
    if not re.search(r"↔|<->|→|->", value):
        return []
    ids = re.findall(r"`([A-Za-z][A-Za-z0-9_-]{1,80})`", value)
    return [(ids[0], target) for target in ids[1:]] if len(ids) >= 2 else []


def _name_pair(value: str) -> tuple[str, str] | None:
    clean = re.sub(r"[*_`]", "", value).strip()
    match = re.search(r"([^|;；]{1,60}?)\s*(?:↔|<->|→|->)\s*([^|;；]{1,60})", clean)
    return (match.group(1).strip(), match.group(2).strip()) if match else None


def _parse_relationships(text: str, headings: list[HeadingSection],
                         characters: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    sections = [section for section in headings if _is_relationship_section(section)]
    by_name, by_id = _state_characters(state)
    explicit_ids = _explicit_character_ids(headings)
    known = {item["name"].casefold(): item["id"] for item in characters}
    lines = parse_markdown(text).structural_lines
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def resolve_name(name: str) -> str:
        direct = known.get(name.casefold())
        return direct or _character_id(name, by_name, by_id, explicit_ids)

    def append(left: str, right: str, label: str, description: str,
               source: dict[str, Any], *, ids: bool) -> None:
        source_id, target_id = (left, right) if ids else (resolve_name(left), resolve_name(right))
        key = tuple(sorted((source_id, target_id)))
        if source_id == target_id or key in seen:
            return
        seen.add(key)
        result.append({
            "id": "relationship_" + hashlib.sha256(
                f"{source_id}\0{target_id}".encode("utf-8")).hexdigest()[:16],
            "source_character_id": source_id,
            "target_character_id": target_id,
            "label": label.strip(),
            "description": description.strip(),
            "source": source,
        })

    for section in sections:
        for headers, table_rows in _tables(lines, section.heading_index + 1, section.end_index):
            for line_index, cells in table_rows:
                pairs: list[tuple[str, str]] = []
                ids = False
                for cell in cells:
                    pairs = _id_pairs(cell)
                    if pairs:
                        ids = True
                        break
                    pair = _name_pair(cell)
                    if pair:
                        pairs = [pair]
                        break
                if not pairs:
                    continue
                description = "\n".join(
                    f"{headers[index]}: {cell}" for index, cell in enumerate(cells) if cell.strip())
                first = cells[0]
                label = re.sub(r"`[^`]+`(?:\s*(?:↔|<->|→|->)\s*`[^`]+`)+", "", first)
                label = re.sub(r"^[^·]*·\s*", "", label).strip(" ·")
                source = _source("adopted_skeleton", PROMPT_PATH, section,
                                 line_start=line_index + 1, line_end=line_index + 1)
                for pair in pairs:
                    append(*pair, label, description, source, ids=ids)

        body_lines = section.body.splitlines(keepends=True)
        bold = [(index, re.fullmatch(r"\s*\*\*([^*\n]+)\*\*\s*", line.rstrip("\r\n")))
                for index, line in enumerate(parse_markdown(section.body).structural_lines)]
        bold = [(index, match) for index, match in bold if match]
        for position, (line_offset, match) in enumerate(bold):
            assert match is not None
            heading = match.group(1).strip()
            name_pair = _name_pair(heading)
            if not name_pair:
                continue
            end_offset = bold[position + 1][0] if position + 1 < len(bold) else len(body_lines)
            description = "".join(body_lines[line_offset + 1:end_offset]).strip()
            fact_lines = "".join(parse_markdown(description).structural_lines)
            id_pairs = _id_pairs(fact_lines)
            pairs = id_pairs or [name_pair]
            ids = bool(id_pairs)
            label_match = re.search(r"IDs and shape:\s*.*?;\s*([^.;\n]+)", fact_lines)
            label = label_match.group(1).strip() if label_match else ""
            start_line = section.heading_line + line_offset + 1
            end_line = section.heading_line + end_offset
            source = _source("adopted_skeleton", PROMPT_PATH, section,
                             line_start=start_line, line_end=end_line)
            for pair in pairs:
                append(*pair, label, description, source, ids=ids)
    return result


def _outline_value(outline: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in outline:
            return outline[key]
    for container_name in ("story_bible", "outline", "data"):
        container = outline.get(container_name)
        if isinstance(container, dict):
            for key in keys:
                if key in container:
                    return container[key]
    return None


def _fallback_chapters(chapters: list[dict[str, Any]], outline: dict[str, Any]) -> list[dict[str, Any]]:
    values = _outline_value(outline, "chapter_summaries", "chapters")
    if isinstance(values, dict):
        values = list(values.values())
    if not isinstance(values, list):
        return chapters
    existing = {item["number"] for item in chapters}
    for index, raw in enumerate(values, 1):
        if not isinstance(raw, dict):
            continue
        number = raw.get("number", index)
        if (not isinstance(number, int) or isinstance(number, bool)
                or number <= 0 or number in existing):
            continue
        title = str(raw.get("title") or raw.get("name") or "").strip()
        summary = str(raw.get("summary") or raw.get("description") or raw.get("outline") or "").strip()
        if not summary and isinstance(raw.get("scenes"), list):
            summary = "\n".join(
                str(item.get("summary") or item.get("description") or "").strip()
                for item in raw["scenes"] if isinstance(item, dict)).strip()
        if not title and not summary:
            continue
        chapters.append({
            "number": number,
            "title": title or f"Chapter {number}",
            "summary": summary,
            "source": _source("outline_fallback", OUTLINE_PATH),
        })
        existing.add(number)
    chapters.sort(key=lambda item: item["number"])
    return chapters


def _outline_character_values(outline: dict[str, Any]) -> list[dict[str, Any]]:
    values = _outline_value(outline, "character_profiles", "characters")
    if isinstance(values, dict):
        return [{"id": key, **value} if isinstance(value, dict)
                else {"id": key, "description": value} for key, value in values.items()]
    return [value for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def _fallback_characters(characters: list[dict[str, Any]], outline: dict[str, Any],
                         state: dict[str, Any]) -> list[dict[str, Any]]:
    by_name, by_id = _state_characters(state)
    seen = {item["id"] for item in characters}
    seen_names = {item["name"].strip().casefold() for item in characters}
    for raw in _outline_character_values(outline):
        name = str(raw.get("name") or raw.get("full_name") or "").strip()
        if not name or name.casefold() in seen_names:
            continue
        character_id = str(raw.get("id") or _character_id(name, by_name, by_id, set()))
        if character_id in seen:
            continue
        characters.append({
            "id": character_id,
            "name": name,
            "role": str(raw.get("role") or "").strip(),
            "description": str(raw.get("description") or raw.get("profile")
                               or raw.get("notes") or "").strip(),
            "relationships": [],
            "source": _source("outline_fallback", OUTLINE_PATH),
        })
        seen.add(character_id)
        seen_names.add(name.casefold())
    return characters


def _fallback_relationships(edges: list[dict[str, Any]], outline: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    values = _outline_value(outline, "relationship_edges", "relationships")
    if isinstance(values, dict):
        values = list(values.values())
    if isinstance(values, list):
        candidates.extend(value for value in values if isinstance(value, dict))
    for character in _outline_character_values(outline):
        source_id = character.get("id")
        relationships = character.get("relationships")
        if not isinstance(source_id, str):
            continue
        if isinstance(relationships, dict):
            for target_id, value in relationships.items():
                if isinstance(value, dict):
                    candidates.append({"source_id": source_id, "target_id": target_id, **value})
                elif isinstance(target_id, str):
                    candidates.append({"source_id": source_id, "target_id": target_id, "label": value})
        elif isinstance(relationships, list):
            for value in relationships:
                if isinstance(value, dict):
                    candidates.append({"source_id": source_id, **value})

    seen = {tuple(sorted((item["source_character_id"], item["target_character_id"])))
            for item in edges}
    for raw in candidates:
        left = raw.get("source_character_id") or raw.get("source_id") or raw.get("from")
        right = raw.get("target_character_id") or raw.get("target_id") or raw.get("to")
        if not isinstance(left, str) or not isinstance(right, str) or not left or not right or left == right:
            continue
        key = tuple(sorted((left, right)))
        if key in seen:
            continue
        seen.add(key)
        edges.append({
            "id": str(raw.get("id") or "relationship_" + hashlib.sha256(
                f"{left}\0{right}".encode("utf-8")).hexdigest()[:16]),
            "source_character_id": left,
            "target_character_id": right,
            "label": str(raw.get("label") or raw.get("type") or "").strip(),
            "description": str(raw.get("description") or raw.get("notes") or "").strip(),
            "source": _source("outline_fallback", OUTLINE_PATH),
        })
    return edges


def _attach_relationships(characters: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    by_id = {item["id"]: item for item in characters}
    for edge in edges:
        text = edge["description"] or edge["label"]
        if not text:
            continue
        value = {"text": text, "source": edge["source"]}
        for character_id in (edge["source_character_id"], edge["target_character_id"]):
            if character_id in by_id:
                by_id[character_id]["relationships"].append(value)


def _unstructured(headings: list[HeadingSection], chapters: list[dict[str, Any]],
                  characters: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    groups = (
        ("characters", [item for item in headings if _is_character_section(item)], characters),
        ("relationships", [item for item in headings if _is_relationship_section(item)], edges),
        ("chapters", [item for item in headings if _is_chapter_container(item)], chapters),
    )
    for kind, sections, structured in groups:
        references = [item["source"] for item in structured
                      if item["source"]["kind"] == "adopted_skeleton"]
        for section in sections:
            if not section.body.strip():
                continue
            # A partially recognized ledger still needs an original-text view.
            # Presence of one card (or an outline fallback) is not full coverage.
            body_lines = parse_markdown(section.body).structural_lines
            uncovered = any(
                line.strip() and not any(
                    ref.get("line_start") is not None and ref.get("line_end") is not None
                    and ref["line_start"] <= section.heading_line + offset + 1 <= ref["line_end"]
                    for ref in references
                )
                for offset, line in enumerate(body_lines)
            )
            if not references or uncovered:
                result.append({
                    "kind": kind,
                    "title": section.title,
                    "text": section.body,
                    "source": _source("adopted_skeleton", PROMPT_PATH, section),
                })
    return result


@lru_cache(maxsize=48)
def _cached_parse(prompt_sha: str, prompt_modified_ns: int, prompt_text: str | None,
                  outline_sha: str, outline_modified_ns: int, outline_text: str | None,
                  state_sha: str, state_modified_ns: int, state_text: str | None) -> dict[str, Any]:
    # Hashes and mtimes intentionally participate in the cache identity.
    del prompt_sha, prompt_modified_ns, outline_sha, outline_modified_ns, state_sha, state_modified_ns
    skeleton = prompt_text or ""
    headings = _heading_sections(skeleton)
    outline = _json_dict(outline_text)
    state = _json_dict(state_text)
    sections, json_warnings = _document_sections(skeleton)
    chapters = _fallback_chapters(_parse_chapters(skeleton, headings), outline)
    characters = _fallback_characters(_parse_characters(skeleton, headings, state), outline, state)
    relationships = _fallback_relationships(
        _parse_relationships(skeleton, headings, characters, state), outline)
    _attach_relationships(characters, relationships)
    narrative_section = next((item for item in headings
                              if _is_narrative_section(item) and item.body.strip()), None)
    narrative = ({
        "title": narrative_section.title,
        "text": narrative_section.body,
        "source": _source("adopted_skeleton", PROMPT_PATH, narrative_section),
    } if narrative_section else None)
    return {
        "chapters": chapters,
        "characters": characters,
        "relationship_edges": relationships,
        "narrative": narrative,
        "sections": sections,
        "unstructured_sections": _unstructured(headings, chapters, characters, relationships),
        "json_warnings": json_warnings,
    }


def _dependency(kind: str, snapshot: Snapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "kind": kind,
        "path": snapshot.relative_path,
        "sha256": snapshot.sha256,
        "bytes": len(snapshot.data),
        "modified_ns": snapshot.modified_ns,
    }


def project_story_insights(project: Path, project_id: str) -> dict[str, Any]:
    """Return the complete source projection without touching project state."""
    project = Path(project).absolute()
    prompt, prompt_problem = _read_snapshot(project, PROMPT_PATH)
    outline, outline_problem = _read_snapshot(project, OUTLINE_PATH)
    state, state_problem = _read_snapshot(project, STATE_PATH)
    warnings = [problem for problem in (prompt_problem, outline_problem, state_problem) if problem]

    prompt_text = prompt.text if prompt else None
    outline_text = outline.text if outline else None
    state_text = state.text if state else None
    if prompt is None and not prompt_problem:
        warnings.append("Adopted skeleton is not available.")
    elif prompt is not None and prompt_text is None:
        warnings.append("Adopted skeleton is not valid UTF-8.")
    if outline is not None and not _valid_json_object(outline_text):
        warnings.append("Outline fallback could not be parsed; its file was left unchanged.")
    if state is not None and not _valid_json_object(state_text):
        warnings.append("Character identity map could not be parsed; its file was left unchanged.")

    parsed = deepcopy(_cached_parse(
        prompt.sha256 if prompt else "", prompt.modified_ns if prompt else 0, prompt_text,
        outline.sha256 if outline else "", outline.modified_ns if outline else 0, outline_text,
        state.sha256 if state else "", state.modified_ns if state else 0, state_text,
    ))
    warnings.extend(parsed.pop("json_warnings"))
    dependencies = [item for item in (
        _dependency("adopted_skeleton", prompt),
        _dependency("outline_fallback", outline),
        _dependency("state_id_map", state),
    ) if item is not None]
    parsed.update({
        "project_id": project_id,
        "source": {
            "available": prompt is not None and prompt_text is not None,
            "path": PROMPT_PATH,
            "sha256": prompt.sha256 if prompt else "",
            "bytes": len(prompt.data) if prompt else 0,
            "modified_ns": prompt.modified_ns if prompt else 0,
            "media_type": "text/markdown",
            "dependencies": dependencies,
        },
        "warnings": list(dict.fromkeys(warnings)),
    })
    return parsed


def adopted_story_source(project: Path) -> tuple[bytes, str, int] | None:
    """Return exact readable Markdown bytes, content hash, and mtime."""
    snapshot, _problem = _read_snapshot(Path(project).absolute(), PROMPT_PATH)
    if snapshot is None or snapshot.text is None:
        return None
    return snapshot.data, snapshot.sha256, snapshot.modified_ns
