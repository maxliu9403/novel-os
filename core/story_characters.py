"""Read source-backed character profiles through bounded format adapters.

The state supplies stable identifiers and display names only. Roles and profile
content always belong to the cited adopted source. Unrecognized prose is left
untouched for the complete skeleton view rather than guessed into a profile.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable

from core.story_markdown import (
    HeadingSection, chapter_title, json_blocks, normalized, parse_markdown,
    source_ref, tables,
)

PROMPT_PATH = "outputs/input/prompt.md"
_CHARACTER_KEYS = {"characters", "character_ledger", "character_profiles", "character_profiles_by_id"}
_FIELD_LABELS = {
    "id", "name", "fullname", "role", "tier", "publicidentity", "age",
    "motivation", "motivations", "desire", "need", "goal", "goals", "fear", "flaw",
    "background", "personality", "arc", "evidence", "description", "strengths",
    "relationships", "speech", "姓名", "年龄", "身份", "角色定位", "公开身份",
    "性格", "动机", "欲望", "目标", "恐惧", "缺点", "人物弧线", "关系", "说明",
}
_ENGLISH_PROFILE = re.compile(
    r"(?:(?<=^)|(?<=[。.!?])\s+)([A-Z][A-Za-z'’-]*(?:\s+(?:and\s+)?[A-Z][A-Za-z'’-]*){0,3})"
    r"(?:\s*,\s*\d{1,3})?\s*:\s*", re.MULTILINE,
)
_LINE_PROFILE = re.compile(r"^\s*(?:[-*+]\s*)?(?:\*\*)?([^\n：:|]{1,60}?)(?:\*\*)?\s*[：:]\s*(.+?)\s*$")


@dataclass(frozen=True)
class _Profile:
    name: str
    role: str
    description: str
    source: dict[str, Any]
    explicit_id: str | None = None


def is_character_section(section: HeadingSection) -> bool:
    title = normalized(section.title)
    return any(token in title for token in (
        "人物身份", "人物档案", "人物设定", "人物台账", "角色档案", "角色设定",
        "characterprofile", "characterbible", "characterledger", "charactercore", "characters",
        "characterpersonalities",
    ))


def is_relationship_section(section: HeadingSection) -> bool:
    title = normalized(section.title)
    return any(token in title for token in (
        "人物关系", "角色关系", "关系台账", "关系及变化", "婚姻亲子与女性关系",
        "relationships", "relationshipledger",
    ))


def state_characters(state: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Extract identity mapping without carrying state facts into source profiles."""
    values = state.get("characters")
    by_name: dict[str, str] = {}
    by_id: dict[str, dict[str, str]] = {}
    if isinstance(values, dict):
        for key, raw in values.items():
            if not isinstance(key, str) or not isinstance(raw, dict):
                continue
            name = raw.get("full_name") or raw.get("name")
            if isinstance(name, str) and name.strip():
                by_name[name.strip().casefold()] = key
                by_id[key] = {"name": name.strip()}
    return by_name, by_id


def slug(value: str) -> str:
    name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return "character_" + (name or hashlib.sha256(value.encode()).hexdigest()[:12])


def character_id(name: str, by_name: dict[str, str], by_id: dict[str, dict[str, str]],
                 explicit_ids: set[str]) -> str:
    folded = name.strip().casefold()
    if folded in by_name:
        return by_name[folded]
    matches = [key for key, item in by_id.items()
               if item["name"].casefold().startswith(folded + " ")
               or folded.startswith(item["name"].casefold() + " ")]
    if len(matches) == 1:
        return matches[0]
    tokens = set(re.findall(r"\w+", folded))
    matches = [key for key in explicit_ids
               if (suffix := key.removeprefix("char_").replace("_", " ").casefold()) == folded
               or suffix in tokens]
    return matches[0] if len(matches) == 1 else slug(name)


def explicit_character_ids(sections: list[HeadingSection]) -> set[str]:
    ids: set[str] = set()
    for section in sections:
        if is_relationship_section(section):
            body = "".join(parse_markdown(section.body).structural_lines)
            ids.update(value for value in re.findall(r"`([A-Za-z][A-Za-z0-9_-]{1,80})`", body)
                       if value.startswith("char_") or "_" in value)
    return ids


def _source(section: HeadingSection, **lines: int) -> dict[str, Any]:
    return source_ref("adopted_skeleton", PROMPT_PATH, section, **lines)


def _name_allowed(name: str) -> bool:
    value = normalized(name)
    return bool(value) and value not in _FIELD_LABELS and not any(token in value for token in (
        "关系", "规则", "记录", "说明", "以下", "每名", "档案至少", "核心关系的变化",
        "必须", "不能", "可以", "需要", "应当", "不得", "至少",
        "relationshiprules", "stablecharacterreferences", "supportingcast",
    ))


def _looks_like_name(name: str) -> bool:
    """Profile headings contain names, not prose labels or scene descriptions."""
    if re.fullmatch(r"[\u3400-\u9fff·]{2,16}", name):
        return True
    words = name.split()
    return 0 < len(words) <= 6 and all(
        word in {"and", "de", "del", "da", "van", "von", "bin", "al"}
        or (word[0].isupper() and all(char.isalpha() or char in "'’-" for char in word))
        for word in words
    )


def _role(description: str) -> str:
    """Read explicitly labelled source roles, including historical YAML fields."""
    document = parse_markdown(description)
    # YAML fences are the established profile storage format. Other fenced
    # examples (Markdown, Python, etc.) cannot contribute live profile facts.
    lines = list(document.structural_lines)
    for fence in document.fences:
        if fence.closed and fence.info.casefold() in {"yaml", "yml"}:
            lines[fence.opening_index + 1:fence.body_end_index] = document.lines[fence.opening_index + 1:fence.body_end_index]
    description = "".join(lines)
    for labels in (("role", "角色定位"), ("tier",), (
        "public_identity", "public identity", "Age / public identity", "身份", "公开身份",
        "身份与独立目标", "公开身份与生活基础",
    )):
        for label in labels:
            match = re.search(
                rf"(?im)^\s*(?:[-*+]\s*)?(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*[：:]\s*(.+?)\s*$",
                description,
            )
            if match:
                return match.group(1).strip().strip("*`\"'")
    for fence in document.fences:
        if not fence.closed or fence.info.casefold() != "json":
            continue
        try:
            record = json.loads("".join(document.lines[fence.opening_index + 1:fence.body_end_index]))
        except (TypeError, ValueError):
            continue
        if isinstance(record, dict):
            role = _record_role(record)
            if role:
                return role
    return ""


def _record_role(record: dict[str, Any]) -> str:
    return next((record[field].strip() for field in ("role", "tier", "public_identity", "身份")
                 if isinstance(record.get(field), str) and record[field].strip()), "")


def _heading_profile(title: str) -> tuple[str, str, str | None]:
    clean = re.sub(r"[*`]", "", title).strip()
    prefix = re.fullmatch(r"((?:char(?:acter)?_)[A-Za-z0-9_-]+|[A-Z]\d{1,4})\s*[|｜·]\s*(.+)", clean)
    if prefix:
        return prefix.group(2).strip(), "", prefix.group(1)
    explicit = re.search(r"(?:[-–—]\s*|\()((?:char(?:acter)?_)[A-Za-z0-9_-]+)\)?\s*$", clean, re.I)
    if explicit:
        return clean[:explicit.start()].rstrip(" -–—("), "", explicit.group(1)
    parts = re.split(r"\s*(?:—+|–|\s-\s)\s*", clean, maxsplit=1)
    name = re.sub(r"\s*[,，]\s*\d{1,3}(?=\s*(?:,?\s+and\s+|$))", "", parts[0]).strip()
    name = re.sub(r",\s+and\s+", " and ", name)
    return name, parts[1].strip() if len(parts) > 1 else "", None


def _table_profiles(lines: list[str], section: HeadingSection) -> Iterable[_Profile]:
    for headers, rows in tables(lines, section.heading_index + 1, section.end_index):
        fields = [normalized(item) for item in headers]
        index = lambda labels: next((i for i, value in enumerate(fields) if value in labels), None)
        name_i = index({"姓名", "人物", "角色", "name", "character"})
        if name_i is None:
            continue
        id_i = index({"id", "人物id", "角色id"})
        role_i = index({"身份", "角色定位", "role", "职业", "tier", "publicidentity"})
        desc_i = next((i for i, value in enumerate(fields) if any(
            token in value for token in ("简介", "档案", "描述", "说明", "description", "profile"))), None)
        for line, cells in rows:
            name = re.sub(r"[*_`]", "", cells[name_i]).strip()
            if not _name_allowed(name):
                continue
            description = cells[desc_i].strip() if desc_i is not None else ""
            if not description:
                description = "；".join(f"{headers[i]}：{cell}" for i, cell in enumerate(cells)
                                       if i not in {name_i, id_i, role_i} and cell.strip())
            yield _Profile(name, cells[role_i].strip() if role_i is not None else "", description,
                           _source(section, line_start=line + 1, line_end=line + 1),
                           re.sub(r"[*_`]", "", cells[id_i]).strip() or None if id_i is not None else None)


def _heading_profiles(section: HeadingSection, headings: list[HeadingSection]) -> Iterable[_Profile]:
    for child in headings:
        if child.level != section.level + 1 or not section.heading_index < child.heading_index < section.end_index:
            continue
        name, heading_role, explicit_id = _heading_profile(child.title)
        if (_name_allowed(name) and (explicit_id or _looks_like_name(name))
                and not chapter_title(name) and child.body.strip()):
            yield _Profile(name, _role(child.body) or heading_role, child.body.strip(), _source(child), explicit_id)


def _character_json_records(data: Any, *, direct_list: bool = False) -> Iterable[dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            if direct_list and isinstance(item, dict):
                yield item
            yield from _character_json_records(item)
    elif isinstance(data, dict):
        for key, value in data.items():
            if key.casefold() in _CHARACTER_KEYS:
                if isinstance(value, list):
                    yield from (item for item in value if isinstance(item, dict))
                elif isinstance(value, dict):
                    for character_key, item in value.items():
                        if isinstance(item, dict):
                            yield {"id": str(character_key), **item}
            yield from _character_json_records(value)


def _json_profiles(markdown: str, base_line: int, title: str, *, direct_list: bool = False) -> Iterable[_Profile]:
    blocks, _ = json_blocks(markdown, base_line, title)
    for block in blocks:
        if not block["valid"]:
            continue
        for record in _character_json_records(block["data"], direct_list=direct_list):
            name = record.get("name") or record.get("full_name")
            if not isinstance(name, str) or not _name_allowed(name):
                continue
            role = _record_role(record)
            key = record.get("id") or record.get("character_id")
            yield _Profile(name.strip(), role, json.dumps(record, ensure_ascii=False, indent=2),
                           block["source"], str(key).strip() if key else None)


def _inline_profiles(section: HeadingSection) -> Iterable[_Profile]:
    body = "".join(parse_markdown(section.body).structural_lines)
    matches = [match for match in _ENGLISH_PROFILE.finditer(body) if _name_allowed(match.group(1))]
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(body)
        name, description = match.group(1).strip(), body[match.end():end].strip()
        if description and _name_allowed(name):
            lead = re.split(r"[;；。\n]", description, maxsplit=1)[0].strip()
            content_end = len(body[:end].rstrip())
            yield _Profile(name, _role(description) or (lead if len(lead) <= 140 else ""), description,
                           _source(section, line_start=section.heading_line + body[:match.start(1)].count("\n") + 1,
                                   line_end=section.heading_line + body[:content_end].count("\n") + 1))
    if matches:
        return
    for offset, line in enumerate(body.splitlines()):
        match = _LINE_PROFILE.match(line)
        if not match:
            continue
        name, description = re.sub(r"[*_`]", "", match.group(1)).strip(), match.group(2).strip()
        name, heading_role, explicit_id = _heading_profile(name)
        if _name_allowed(name) and not name.isdigit():
            lead = re.split(r"[;；。\n]", description, maxsplit=1)[0].strip()
            number = section.heading_line + offset + 1
            yield _Profile(name, _role(description) or heading_role or (lead if len(lead) <= 140 else ""), description,
                           _source(section, line_start=number, line_end=number), explicit_id)


def _numbered_profile(section: HeadingSection) -> _Profile | None:
    """Historical peers use an explicit ``number. name / alias — identity`` title."""
    match = re.fullmatch(r"\s*\d{1,3}\s*[.)、．]\s*(.+?)\s*(?:—+|–|\s-\s)\s*(.+)", section.title)
    if not match or not section.body.strip():
        return None
    name = match.group(1).split("/", 1)[0].strip()
    name = re.sub(r"\s*[,，]\s*\d{1,3}$", "", name)
    # Positive name shape excludes sentence/chapter titles containing a cast name.
    if not _looks_like_name(name):
        return None
    if not _name_allowed(name):
        return None
    return _Profile(name, _role(section.body) or match.group(2).strip(), section.body.strip(), _source(section))


def _peer_profiles(headings: list[HeadingSection]) -> Iterable[_Profile]:
    """Only adapt peers immediately belonging to an explicitly marked ledger."""
    for index, section in enumerate(headings):
        if not is_character_section(section):
            continue
        started = False
        for candidate in headings[index + 1:]:
            if candidate.level < section.level:
                break
            if candidate.level > section.level:
                continue
            profile = _numbered_profile(candidate)
            if profile:
                started = True
                yield profile
                continue
            title = normalized(candidate.title)
            prelude = title in {"叙述与人物规模", "叙述与人物范围", "narrationandcastscope", "castscope"}
            if started or not prelude:
                break


def _display_names(name: str, by_id: dict[str, dict[str, str]]) -> list[str]:
    candidates = re.split(r"\s+and\s+", name)
    if len(candidates) == 2:
        if " " not in candidates[0] and " " in candidates[1]:
            candidates[0] += " " + candidates[1].rsplit(" ", 1)[1]
        if all(sum(item["name"].casefold() == part.casefold()
                   or item["name"].casefold().startswith(part.casefold() + " ")
                   for item in by_id.values()) == 1 for part in candidates):
            return candidates
    return [name]


def parse_characters(text: str, headings: list[HeadingSection], state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[_Profile] = []
    lines = parse_markdown(text).structural_lines
    for section in headings:
        if not is_character_section(section):
            continue
        structured = [*_table_profiles(lines, section), *_heading_profiles(section, headings),
                      *_json_profiles(section.body, section.heading_line + 1, section.title, direct_list=True)]
        rows.extend(structured or _inline_profiles(section))
    rows.extend(_json_profiles(text, 1, "Story Skeleton"))
    rows.extend(_peer_profiles(headings))
    by_name, by_id = state_characters(state)
    explicit_ids = explicit_character_ids(headings)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        for name in _display_names(row.name, by_id):
            key = row.explicit_id or character_id(name, by_name, by_id, explicit_ids)
            if key not in seen:
                seen.add(key)
                result.append({"id": key, "name": by_id.get(key, {}).get("name") or name,
                               "role": row.role, "description": row.description, "relationships": [], "source": row.source})
    return result
