"""EPUB 3 output, written directly (PLAN.md P6).

Like DOCX, an .epub is a ZIP of XML - and unlike DOCX its payload is XHTML,
which the compiler already knows how to produce. So this is mostly packaging,
and it costs no dependency.

Two rules of the format that are easy to get wrong and fatal when you do:

* the ``mimetype`` entry must be **first in the archive and stored
  uncompressed**, because readers sniff it at a fixed offset;
* every document listed in the spine must also be declared in the manifest.

One file per chapter rather than one big document: that is what gives a reader
working chapter navigation and sane progress, and it is why the compiler tracks
which chapter each block belongs to.
"""

from __future__ import annotations

import json
import uuid
import zipfile
from io import BytesIO
from typing import Dict, List
from xml.sax.saxutils import escape, quoteattr

from compile_book import Block, CompiledBook, inline_runs
from styles import Style, StyleSheet

_FONTS = {
    "serif": "Georgia, 'Times New Roman', serif",
    "sans": "sans-serif",
    "mono": "monospace",
}

_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _num(value: float) -> str:
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _rule(selector: str, style: Style) -> str:
    parts = [
        f"font-family:{_FONTS.get(style.font, 'Georgia, serif')}",
        f"font-size:{_num(style.size_pt)}pt",
        f"line-height:{_num(style.line_height)}",
        f"text-align:{style.align}",
        f"margin:{_num(style.space_before_em)}em 0 {_num(style.space_after_em)}em",
        f"text-indent:{_num(style.first_line_indent_em)}em",
    ]
    if style.bold:
        parts.append("font-weight:700")
    if style.italic:
        parts.append("font-style:italic")
    if style.small_caps:
        parts.append("font-variant:small-caps")
    if style.page_break_before:
        parts.append("page-break-before:always")
    return f"{selector}{{{';'.join(parts)}}}"


def _stylesheet(sheet: StyleSheet) -> str:
    """One class per role, so the XHTML carries meaning rather than inline CSS."""
    return "\n".join(
        _rule(f".{role.replace('_', '-')}", sheet.get(role))
        for role in ("title", "subtitle", "story_lead_title", "story_hook",
                     "story_lead",
                     "chapter_title", "body", "first_paragraph", "block_quote",
                     "scene_break")
    )


def _inline_xhtml(text: str) -> str:
    out = []
    for run in inline_runs(text):
        body = escape(run.text)
        if run.bold:
            body = f"<strong>{body}</strong>"
        if run.italic:
            body = f"<em>{body}</em>"
        out.append(body)
    return "".join(out)


def _page(title: str, body: str, language: str) -> str:
    language_attr = quoteattr(language or "en-US")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        f"xml:lang={language_attr} lang={language_attr}>"
        f"<head><title>{escape(title)}</title>"
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>'
        f"<body>{body}</body></html>"
    )


def _blocks_to_xhtml(blocks: List[Block], sheet: StyleSheet) -> str:
    out: List[str] = []
    for block in blocks:
        css_class = block.kind.replace("_", "-")
        if block.kind == "scene_break":
            out.append(
                f'<p class="{css_class}" role="separator">'
                f"{escape(sheet.scene_break_marker)}</p>"
            )
        elif block.kind in ("chapter_title", "story_lead_title"):
            out.append(f'<h2 class="{css_class}">{_inline_xhtml(block.text)}</h2>')
        elif block.kind == "block_quote":
            out.append(
                f'<blockquote class="{css_class}">{_inline_xhtml(block.text)}</blockquote>'
            )
        else:
            out.append(f'<p class="{css_class}">{_inline_xhtml(block.text)}</p>')
    return "".join(out)


def _chapter_files(book: CompiledBook, sheet: StyleSheet) -> Dict[str, str]:
    """One XHTML document per chapter, so navigation and progress work."""
    grouped: Dict[int, List[Block]] = {}
    for block in book.blocks:
        key = block.chapter
        if isinstance(key, int) and not isinstance(key, bool):
            grouped.setdefault(key, []).append(block)

    files: Dict[str, str] = {}
    for index, chapter in enumerate(book.chapters, start=1):
        key = chapter.get("number")
        blocks = grouped.get(key, [])
        heading = next(
            (b.text for b in blocks if b.kind == "chapter_title"),
            chapter.get("title") or f"Chapter {index}",
        )
        files[f"chap{index:03d}.xhtml"] = _page(
            heading, _blocks_to_xhtml(blocks, sheet), book.language,
        )
    return files


def _package(
    book: CompiledBook,
    chapter_files: List[str],
    has_intro: bool,
    book_id: str,
) -> str:
    intro_manifest = (
        '<item id="intro" href="intro.xhtml" media-type="application/xhtml+xml"/>'
        if has_intro else ""
    )
    manifest = "".join(
        f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>'
        for i, name in enumerate(chapter_files, start=1)
    )
    intro_spine = '<itemref idref="intro"/>' if has_intro else ""
    spine = "".join(
        f'<itemref idref="c{i}"/>' for i in range(1, len(chapter_files) + 1)
    )
    description = (
        f"<dc:description>{escape(book.publication_copy.spoiler_free_blurb)}</dc:description>"
        if book.publication_copy else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="bookid">urn:uuid:{book_id}</dc:identifier>'
        f"<dc:title>{escape(book.title)}</dc:title>"
        f"<dc:language>{escape(book.language)}</dc:language>"
        + (f"<dc:creator>{escape(book.author)}</dc:creator>" if book.author else "")
        + description
        + '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>'
        "</metadata>"
        "<manifest>"
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="css" href="style.css" media-type="text/css"/>'
        f"{intro_manifest}{manifest}</manifest>"
        f"<spine>{intro_spine}{spine}</spine></package>"
    )


def _nav(book: CompiledBook, files: List[str]) -> str:
    items = "".join(
        f'<li><a href="{name}">{escape(chapter.get("title") or f"Chapter {i}")}</a></li>'
        for i, (name, chapter) in enumerate(zip(files, book.chapters), start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" '
        f"xml:lang={quoteattr(book.language)} lang={quoteattr(book.language)}>"
        f"<head><title>{escape(book.title)}</title></head><body>"
        '<nav epub:type="toc" id="toc"><h1>Contents</h1>'
        f"<ol>{items}</ol></nav></body></html>"
    )


def _write_member(
    archive: zipfile.ZipFile,
    name: str,
    data: str | bytes,
    *,
    compress_type: int = zipfile.ZIP_DEFLATED,
) -> None:
    info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
    info.compress_type = compress_type
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def render_epub(book: CompiledBook, sheet: StyleSheet) -> bytes:
    """An EPUB 3 of the compiled manuscript."""
    chapters = _chapter_files(book, sheet)
    names = list(chapters)
    intro_blocks = [block for block in book.blocks if block.chapter is None]
    intro = (
        _page(
            book.publication_copy.reader_heading if book.publication_copy else book.title,
            _blocks_to_xhtml(intro_blocks, sheet),
            book.language,
        )
        if intro_blocks else None
    )
    canonical_book = json.dumps(
        book.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    book_id = str(uuid.uuid5(uuid.NAMESPACE_URL, canonical_book))

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        # Must be first and stored: readers sniff it at a fixed offset.
        _write_member(
            z, "mimetype", "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        _write_member(z, "META-INF/container.xml", _CONTAINER)
        _write_member(z, "OEBPS/style.css", _stylesheet(sheet))
        _write_member(
            z, "OEBPS/content.opf", _package(book, names, intro is not None, book_id),
        )
        _write_member(z, "OEBPS/nav.xhtml", _nav(book, names))
        if intro is not None:
            _write_member(z, "OEBPS/intro.xhtml", intro)
        for name, body in chapters.items():
            _write_member(z, f"OEBPS/{name}", body)
    return buffer.getvalue()
