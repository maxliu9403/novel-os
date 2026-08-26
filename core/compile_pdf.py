"""Dependency-free PDF output for compiled manuscripts.

The renderer uses the PDF standard's built-in ``STSong-Light`` CID font with
``UniGB-UCS2-H`` encoding. That keeps Chinese text readable without shipping a
large font binary or requiring a platform-specific command-line converter.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List

from compile_book import Block, CompiledBook
from styles import Style, StyleSheet

PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MARGIN_X = 64
MARGIN_TOP = 64
MARGIN_BOTTOM = 58


def _pdf_text(text: str) -> str:
    """Encode Unicode text as a PDF hexadecimal string."""
    return f"<{(text or '').encode('utf-16-be').hex().upper()}>"


def _char_width(char: str, size: float) -> float:
    if char == "\t":
        return size * 2
    if char.isspace():
        return size * 0.28
    if ord(char) < 128:
        return size * 0.52
    # Full-width and CJK characters occupy one em in the CID font.
    return size if unicodedata.east_asian_width(char) in {"W", "F"} else size * 0.72


def _wrap(text: str, max_width: float, size: float) -> List[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return [""]

    lines: List[str] = []
    current: List[str] = []
    width = 0.0
    for char in text:
        char_width = _char_width(char, size)
        if current and width + char_width > max_width:
            lines.append("".join(current).rstrip())
            current = []
            width = 0.0
            if char.isspace():
                continue
        current.append(char)
        width += char_width
    if current:
        lines.append("".join(current).rstrip())
    return lines or [""]


def _text_x(text: str, style: Style, available: float, left: float) -> float:
    width = sum(_char_width(char, style.size_pt) for char in text)
    if style.align == "center":
        return left + max(0.0, (available - width) / 2)
    if style.align == "right":
        return left + max(0.0, available - width)
    return left


def _iter_blocks(book: CompiledBook, sheet: StyleSheet) -> Iterable[tuple[Block, Style, str]]:
    yield Block(kind="chapter_title", text=book.title), sheet.get("title"), book.title
    byline = " · ".join(part for part in (book.author, book.genre) if part)
    if byline:
        yield Block(kind="subtitle", text=byline), sheet.get("subtitle"), byline
    for block in book.blocks:
        text = sheet.scene_break_marker if block.kind == "scene_break" else block.text
        yield block, sheet.get(block.kind), text


def _page_operations(book: CompiledBook, sheet: StyleSheet) -> List[List[str]]:
    pages: List[List[str]] = [[]]
    operations = pages[0]
    y = PAGE_HEIGHT - MARGIN_TOP
    content_width = PAGE_WIDTH - 2 * MARGIN_X

    def new_page() -> None:
        nonlocal operations, y
        if operations:
            operations = []
            pages.append(operations)
        y = PAGE_HEIGHT - MARGIN_TOP

    def write_block(block: Block, style: Style, text: str) -> None:
        nonlocal y
        if style.page_break_before and operations:
            new_page()

        indent = max(0.0, style.first_line_indent_em * style.size_pt)
        if block.kind == "block_quote":
            indent += style.size_pt * 1.5
        available = max(80.0, content_width - indent)
        lines = _wrap(text, available, style.size_pt)
        line_height = max(style.size_pt * 0.8, style.size_pt * style.line_height)
        before = max(0.0, style.space_before_em * style.size_pt)
        after = max(0.0, style.space_after_em * style.size_pt)
        required = before + len(lines) * line_height + after
        if y - required < MARGIN_BOTTOM and operations:
            new_page()
        y -= before

        for index, line in enumerate(lines):
            line_indent = indent if index == 0 else (style.size_pt * 1.5 if block.kind == "block_quote" else 0.0)
            left = MARGIN_X + line_indent
            line_available = content_width - line_indent
            x = _text_x(line, style, line_available, left)
            operations.extend([
                "BT",
                f"/F1 {style.size_pt:g} Tf",
                f"1 0 0 1 {x:.2f} {y:.2f} Tm",
                f"{_pdf_text(line)} Tj",
                "ET",
            ])
            y -= line_height
        y -= after

    for block, style, text in _iter_blocks(book, sheet):
        write_block(block, style, text)
    return pages


class _PdfObjects:
    def __init__(self) -> None:
        self.items: List[bytes | None] = [None]

    def add(self, value: str | bytes) -> int:
        data = value.encode("ascii") if isinstance(value, str) else value
        self.items.append(data)
        return len(self.items) - 1


def _build_pdf(objects: _PdfObjects, pages: List[List[str]], title: str) -> bytes:
    descendant_id = objects.add(
        "<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> "
        "/DW 1000 >>"
    )
    font_id = objects.add(
        f"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
        f"/Encoding /UniGB-UCS2-H /DescendantFonts [{descendant_id} 0 R] >>"
    )
    pages_id = objects.add(b"")
    page_ids: List[int] = []

    for operations in pages:
        stream = ("\n".join(operations) + "\n").encode("ascii")
        content_id = objects.add(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
            + stream + b"endstream"
        )
        page_ids.append(objects.add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /ProcSet [/PDF /Text] /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ))

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.items[pages_id] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>"
    ).encode("ascii")
    catalog_id = objects.add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")
    info_id = objects.add(f"<< /Title {_pdf_text(title)} >>")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, value in enumerate(objects.items[1:], start=1):
        assert value is not None
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(value)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects.items)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects.items)} /Root {catalog_id} 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def render_pdf(book: CompiledBook, sheet: StyleSheet) -> bytes:
    """Render a compiled book as a multi-page PDF document."""
    return _build_pdf(_PdfObjects(), _page_operations(book, sheet), book.title)
