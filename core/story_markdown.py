"""Source-preserving Markdown structure for read-only story projections.

This is deliberately a structural reader, not a Markdown renderer. Code examples
never become headings, table rows or transport markers. Original bytes decoded
as text, line endings and one-based source references remain available intact.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import re
from typing import Any, Iterable, Sequence


PROMPT_PATH = "outputs/input/prompt.md"
_HEADING = re.compile(r"^[ ]{0,3}(#{1,6})[ \t]+(.+?)\s*$")
_FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})([^\r\n]*)$")
_ENGLISH_CHAPTER = re.compile(
    r"^chapter\s+(\d{1,4})(?:\s*[-–—:：.]\s*(.*)|\s+(.*))?$", re.IGNORECASE
)
_CHINESE_CHAPTER = re.compile(
    r"^第\s*([零〇一二三四五六七八九十百两\d]{1,8})\s*章\s*[-–—:：、.]?\s*(.*)$"
)
_NUMBERED_CHAPTER = re.compile(r"^(\d{1,4})\s*[.)、．:：-]\s*(.+)$")


@dataclass(frozen=True)
class HeadingSection:
    level: int
    title: str
    heading_index: int
    end_index: int
    body: str

    @property
    def heading_line(self) -> int:
        return self.heading_index + 1

    @property
    def end_line(self) -> int:
        return self.end_index


@dataclass(frozen=True)
class CodeFence:
    opening_index: int
    end_index: int  # exclusive, includes closing fence when present
    info: str
    marker: str
    closed: bool

    @property
    def body_end_index(self) -> int:
        return self.end_index - 1 if self.closed else self.end_index


@dataclass(frozen=True)
class Document:
    text: str
    lines: tuple[str, ...]
    structural_lines: tuple[str, ...]
    headings: tuple[HeadingSection, ...]
    fences: tuple[CodeFence, ...]


def _placeholder(line: str) -> str:
    return line[len(line.rstrip("\r\n")):]


def _sections(lines: tuple[str, ...], structural: tuple[str, ...]) -> tuple[HeadingSection, ...]:
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(structural):
        match = _HEADING.match(line.rstrip("\r\n"))
        if match:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", match.group(2)).strip()
            headings.append((index, len(match.group(1)), title))
    ends = [len(lines)] * len(headings)
    stack: list[int] = []
    for position, (index, level, _title) in enumerate(headings):
        while stack and headings[stack[-1]][1] >= level:
            ends[stack.pop()] = index
        stack.append(position)
    return tuple(
        HeadingSection(level, title, index, ends[position], "".join(lines[index + 1:ends[position]]))
        for position, (index, level, title) in enumerate(headings)
    )


@lru_cache(maxsize=16)
def parse_markdown(text: str) -> Document:
    """Return immutable structure, masking code without moving any source line."""
    lines = tuple(text.splitlines(keepends=True))
    structural = list(lines)
    fences: list[CodeFence] = []
    index = 0
    while index < len(lines):
        line = lines[index].rstrip("\r\n")
        # Four-column indentation denotes a code example for our flat document
        # reader. expandtabs also handles a leading tab without altering source.
        if line and len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip(" ")) >= 4:
            structural[index] = _placeholder(lines[index])
            index += 1
            continue
        match = _FENCE.match(line)
        if not match or (match.group(1).startswith("`") and "`" in match.group(2)):
            index += 1
            continue
        marker, info = match.groups()
        closing = re.compile(r"^[ ]{0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}[ \t]*$")
        cursor = index + 1
        while cursor < len(lines) and not closing.fullmatch(lines[cursor].rstrip("\r\n")):
            cursor += 1
        closed = cursor < len(lines)
        end = cursor + 1 if closed else len(lines)
        fences.append(CodeFence(index, end, info.strip(), marker, closed))
        for code_index in range(index, end):
            structural[code_index] = _placeholder(lines[code_index])
        index = end
    structural_lines = tuple(structural)
    return Document(text, lines, structural_lines, _sections(lines, structural_lines), tuple(fences))


def heading_sections(text: str) -> list[HeadingSection]:
    return list(parse_markdown(text).headings)


def normalized(value: str) -> str:
    return re.sub(r"[\s_*/`#—–:：、／\\.()-]+", "", value).casefold()


def chinese_number(value: str) -> int | None:
    if value.isdigit():
        number = int(value)
        return number if number > 0 else None
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    total = current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        elif char == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    number = total + current
    return number if number > 0 else None


def module_number(title: str) -> tuple[int | None, str]:
    clean = title.strip()
    match = re.match(r"^(\d{1,3})\s*[.、:：]\s*(.+)$", clean)
    if match:
        return int(match.group(1)), match.group(2).strip()
    match = re.match(r"^([一二三四五六七八九十百两]+)\s*[.、:：]\s*(.+)$", clean)
    if match:
        return chinese_number(match.group(1)), match.group(2).strip()
    return None, clean


def chapter_title(title: str, *, allow_numbered: bool = False) -> tuple[int, str] | None:
    clean = re.sub(r"[*_`]", "", title).strip()
    match = _ENGLISH_CHAPTER.match(clean)
    if match and int(match.group(1)) > 0:
        return int(match.group(1)), (match.group(2) or match.group(3) or "").strip()
    match = _CHINESE_CHAPTER.match(clean)
    if match:
        number = chinese_number(match.group(1))
        return (number, match.group(2).strip()) if number else None
    if allow_numbered:
        match = _NUMBERED_CHAPTER.match(clean)
        if match and int(match.group(1)) > 0:
            return int(match.group(1)), match.group(2).strip()
    return None


def source_ref(kind: str, path: str, section: HeadingSection | None = None,
               *, title: str | None = None, line_start: int | None = None,
               line_end: int | None = None) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": path,
        "section": title if title is not None else section.title if section else None,
        "line_start": line_start if line_start is not None else section.heading_line if section else None,
        "line_end": line_end if line_end is not None else section.end_line if section else None,
    }


def _json_block(kind: str, raw: str, source: dict[str, Any], *, closed: bool,
                close: str) -> tuple[dict[str, Any], str | None]:
    warning = None
    try:
        data = json.loads(raw)
        error = None
    except (TypeError, ValueError) as exc:
        data, error = None, str(exc)
        warning = f"Invalid JSON block {kind} at {source['path']}:{source['line_start']}; raw text was preserved."
    if not closed:
        data, error = None, f"Missing closing marker {close}."
        warning = f"Unclosed JSON block {kind} at {source['path']}:{source['line_start']}; raw text was preserved."
    return {"kind": kind, "raw": raw, "data": data, "valid": error is None,
            "warning": error, "source": source}, warning


def json_blocks(markdown: str, base_line: int, section_title: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Read JSON transports, never interpreting markers inside code examples."""
    document = parse_markdown(markdown)
    fences = {fence.opening_index: fence for fence in document.fences}
    blocks: list[dict[str, Any]] = []
    warnings: list[str] = []
    index = 0
    while index < len(document.lines):
        fence = fences.get(index)
        if fence:
            if fence.info.casefold() not in {"json", "application/json"}:
                index = fence.end_index
                continue
            kind, close = "json_fence", fence.marker
            body_end, end, closed = fence.body_end_index, fence.end_index, fence.closed
        else:
            stripped = document.structural_lines[index].strip()
            if re.fullmatch(r"\[[A-Za-z][A-Za-z0-9_.-]*JSON[A-Za-z0-9_.-]*\]", stripped):
                kind = stripped[1:-1]
                close = f"[/{kind}]"
            elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*_BEGIN", stripped):
                kind = stripped.removesuffix("_BEGIN")
                close = f"{kind}_END"
            else:
                index += 1
                continue
            closing = next((cursor for cursor in range(index + 1, len(document.lines))
                            if document.structural_lines[cursor].strip() == close), None)
            closed = closing is not None
            body_end = closing if closing is not None else len(document.lines)
            end = body_end + 1 if closed else body_end
        raw = "".join(document.lines[index + 1:body_end])
        source = source_ref("adopted_skeleton", PROMPT_PATH, title=section_title,
                            line_start=base_line + index, line_end=base_line + end - 1)
        block, warning = _json_block(kind, raw, source, closed=closed, close=close)
        blocks.append(block)
        if warning:
            warnings.append(warning)
        index = end
    return blocks, warnings


def split_table_row(line: str) -> list[str]:
    return [cell.replace("\\|", "|").strip()
            for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def tables(lines: Sequence[str], start: int, end: int) -> Iterable[tuple[list[str], list[tuple[int, list[str]]]]]:
    # Normalize once across the complete document so a fenced block that opens
    # before `start` cannot leak its example rows into a section-level lookup.
    structural = parse_markdown("".join(lines)).structural_lines
    index, end = max(0, start), min(end, len(structural))
    while index + 1 < end:
        if "|" not in structural[index] or "|" not in structural[index + 1]:
            index += 1
            continue
        headers = split_table_row(structural[index])
        divider = split_table_row(structural[index + 1])
        if not headers or len(headers) != len(divider) or not all(
            re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in divider
        ):
            index += 1
            continue
        rows: list[tuple[int, list[str]]] = []
        cursor = index + 2
        while cursor < end and "|" in structural[cursor]:
            cells = split_table_row(structural[cursor])
            if len(cells) == len(headers):
                rows.append((cursor, cells))
            cursor += 1
        yield headers, rows
        index = cursor
