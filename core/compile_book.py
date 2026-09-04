"""Compile the manuscript into a finished document (PLAN.md P6).

Two stages, deliberately separated:

1. **Gather** - walk the chapters, pick the most finished prose for each, and
   parse it into a flat list of typed blocks (heading, paragraph, scene break,
   block quote). Nothing about appearance happens here.
2. **Render** - turn those blocks into a target format, consulting the
   stylesheet for every appearance decision.

Keeping them apart is what makes formats cheap to add: DOCX and EPUB are new
renderers over the same `CompiledBook`, not new walks of the manuscript. It is
also what makes the gather step testable without asserting on markup.

The compiler reads the *reject-all* projection of Final, per the storage rule -
a pending track-change is a proposal, and a proposal must never be exported as
though the author had accepted it.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from styles import StyleSheet, Style

if TYPE_CHECKING:
    from publication_copy import PublicationCopy

BlockKind = str  # chapter title, story lead, hook, prose, quote, or scene break

_SCENE_BREAK = re.compile(r"^\s*(?:(?:[-*_]\s*){3,}|\*\s*\*\s*\*)\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_STORY_LEAD_HEADING = re.compile(
    r"^(#{1,6})\s+STORY_LEAD\s*:\s*(.+?)\s*$", re.IGNORECASE,
)
_QUOTE = re.compile(r"^>\s?(.*)$")


@dataclass
class Block:
    kind: BlockKind
    text: str = ""
    # Chapter number, when this block belongs to one - lets renderers build
    # navigation without re-walking the manuscript.
    chapter: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "chapter": self.chapter}


@dataclass
class CompiledBook:
    title: str = "Untitled"
    author: str = ""
    genre: str = ""
    language: str = "en-US"
    publication_copy: Optional["PublicationCopy"] = None
    blocks: List[Block] = field(default_factory=list)
    chapters: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(
            len(b.text.split()) for b in self.blocks
            if b.kind not in (
                "scene_break", "chapter_title", "story_lead_label",
                "story_lead_title", "story_hook", "story_lead",
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "genre": self.genre,
            "language": self.language,
            "publication_copy": (
                self.publication_copy.to_dict() if self.publication_copy else None
            ),
            "word_count": self.word_count,
            "chapters": list(self.chapters),
            "blocks": [b.to_dict() for b in self.blocks],
        }


def parse_chapter(text: str, chapter: Optional[int] = None) -> List[Block]:
    """Split one chapter's markdown into typed blocks.

    The first paragraph after a heading or a scene break gets its own kind,
    because typography does not indent it - that is the single most visible
    difference between a manuscript that looks typeset and one that does not.
    """
    blocks: List[Block] = []
    at_paragraph_start = True  # true after a heading or break
    in_story_lead = False

    for raw in re.split(r"\n\s*\n", text or ""):
        chunk = raw.strip()
        if not chunk:
            continue

        if _SCENE_BREAK.match(chunk):
            blocks.append(Block(kind="scene_break", chapter=chapter))
            at_paragraph_start = True
            continue

        story_lead_heading = _STORY_LEAD_HEADING.match(chunk)
        if story_lead_heading:
            blocks.append(Block(
                kind="story_lead_title",
                text=story_lead_heading.group(2).strip(),
                chapter=chapter,
            ))
            at_paragraph_start = True
            in_story_lead = True
            continue

        heading = _HEADING.match(chunk)
        if heading:
            blocks.append(Block(
                kind="chapter_title", text=heading.group(2).strip(), chapter=chapter,
            ))
            at_paragraph_start = True
            in_story_lead = False
            continue

        lines = chunk.split("\n")
        if all(_QUOTE.match(line) for line in lines):
            body = " ".join(
                (_QUOTE.match(line).group(1) or "").strip() for line in lines  # type: ignore[union-attr]
            )
            blocks.append(Block(kind="block_quote", text=body.strip(), chapter=chapter))
            at_paragraph_start = False
            continue

        blocks.append(Block(
            kind=(
                "story_lead"
                if in_story_lead
                else ("first_paragraph" if at_paragraph_start else "body")
            ),
            text=" ".join(line.strip() for line in lines).strip(),
            chapter=chapter,
        ))
        at_paragraph_start = False

    return blocks


def _reader_guide_label(language: str) -> str:
    folded = str(language or "").strip().casefold()
    if (
        folded.startswith("zh")
        or "chinese" in folded
        or "中文" in folded
        or "汉语" in folded
        or "漢語" in folded
    ):
        return "导读"
    return "Introduction"


def _publication_blocks(copy: "PublicationCopy") -> List[Block]:
    return [
        Block("story_lead_label", _reader_guide_label(copy.language)),
        Block("story_lead_title", copy.reader_heading),
        Block("story_hook", copy.hook_lead),
        Block("story_lead", copy.spoiler_free_blurb),
    ]


def gather(
    *,
    title: str,
    author: str,
    genre: str,
    chapters: List[Dict[str, Any]],
    publication_copy: Optional["PublicationCopy"] = None,
) -> CompiledBook:
    """Assemble the book from per-chapter prose.

    `chapters` is a list of {number, title, text} in reading order. Taking prose
    rather than a StoryState keeps this function pure and trivially testable;
    the service layer decides which stage each chapter's text comes from.
    """
    book = CompiledBook(
        title=title or "Untitled",
        author=author,
        genre=genre,
        language=publication_copy.language if publication_copy else "en-US",
        publication_copy=publication_copy,
    )
    if publication_copy:
        book.blocks.extend(_publication_blocks(publication_copy))

    for entry in chapters:
        number = entry.get("number")
        text = (entry.get("text") or "").strip()
        if not text:
            continue

        parsed = parse_chapter(text, chapter=number)
        if publication_copy and any(
            block.kind == "story_lead_title" for block in parsed
        ):
            raise ValueError(
                "structured publication copy and STORY_LEAD marker are both present"
            )
        # A story lead may precede chapter one, so find the actual chapter title
        # instead of assuming the first heading owns navigation.
        chapter_title = next(
            (block for block in parsed if block.kind == "chapter_title"), None,
        )
        if chapter_title is None:
            heading = (entry.get("title") or "").strip() or f"Chapter {number}"
            insert_at = next(
                (
                    index for index, block in enumerate(parsed)
                    if block.kind not in ("story_lead_title", "story_lead")
                ),
                len(parsed),
            )
            chapter_title = Block(kind="chapter_title", text=heading, chapter=number)
            parsed.insert(insert_at, chapter_title)

        book.chapters.append({
            "number": number,
            "title": chapter_title.text,
        })
        book.blocks.extend(parsed)

    return book


# ------------------------------------------------------------------- renderers

def _num(value: float) -> str:
    """Trim trailing zeros so 21.0 renders as 21.

    Sizes arrive as ints from Python and floats from JSON; without this the same
    stylesheet produces different CSS depending on which door it came through.
    """
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _css(style: Style) -> str:
    parts = [
        f"font-size:{_num(style.size_pt)}pt",
        f"line-height:{_num(style.line_height)}",
        f"text-align:{style.align}",
        f"margin:{_num(style.space_before_em)}em 0 {_num(style.space_after_em)}em",
    ]
    if style.first_line_indent_em:
        parts.append(f"text-indent:{_num(style.first_line_indent_em)}em")
    if style.bold:
        parts.append("font-weight:700")
    if style.italic:
        parts.append("font-style:italic")
    if style.small_caps:
        parts.append("font-variant:small-caps")
    if style.page_break_before:
        parts.append("page-break-before:always")
        parts.append("break-before:page")
    family = {
        "serif": "Georgia, 'Times New Roman', serif",
        "sans": "system-ui, sans-serif",
        "mono": "ui-monospace, monospace",
    }.get(style.font, "Georgia, serif")
    parts.append(f"font-family:{family}")
    return ";".join(parts)


@dataclass
class Run:
    """A stretch of text with uniform emphasis."""
    text: str
    bold: bool = False
    italic: bool = False


_INLINE = re.compile(r"(\*\*.+?\*\*|(?<!\*)\*[^*]+?\*(?!\*))", re.S)


def inline_runs(text: str) -> List[Run]:
    """Split markdown emphasis into runs.

    Every renderer needs this - HTML wants tags, DOCX wants <w:r> elements with
    <w:b/> - so the parsing happens once here rather than three times in three
    slightly different ways.
    """
    runs: List[Run] = []
    for piece in _INLINE.split(text or ""):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            runs.append(Run(piece[2:-2], bold=True))
        elif piece.startswith("*") and piece.endswith("*") and len(piece) > 2:
            runs.append(Run(piece[1:-1], italic=True))
        else:
            runs.append(Run(piece))
    return runs


def _inline(text: str) -> str:
    """Escape, then restore the few inline marks markdown carries."""
    out = []
    for run in inline_runs(text):
        body = html.escape(run.text)
        if run.bold:
            body = f"<strong>{body}</strong>"
        if run.italic:
            body = f"<em>{body}</em>"
        out.append(body)
    return "".join(out)


def render_html(book: CompiledBook, sheet: StyleSheet) -> str:
    """A single self-contained HTML file - printable, and the basis for PDF.

    Self-contained on purpose: an export a writer emails to an agent cannot
    depend on a stylesheet that does not travel with it.
    """
    title = html.escape(book.title)
    language = html.escape(book.language or "en-US", quote=True)
    out: List[str] = [
        "<!doctype html>",
        f'<html lang="{language}"><head><meta charset="utf-8">',
        f"<title>{title}</title>",
        "<style>body{max-width:38em;margin:4em auto;padding:0 1.5em;color:#111;"
        "background:#fff}@media print{body{margin:0;max-width:none}}</style>",
        "</head><body>",
        f'<h1 style="{_css(sheet.get("title"))}">{title}</h1>',
    ]
    byline = " · ".join(p for p in (book.author, book.genre) if p)
    if byline:
        out.append(f'<p style="{_css(sheet.get("subtitle"))}">{html.escape(byline)}</p>')

    for block in book.blocks:
        style = sheet.get(block.kind)
        css = _css(style)
        if block.kind == "scene_break":
            out.append(
                f'<p style="{css}" role="separator">'
                f"{html.escape(sheet.scene_break_marker)}</p>"
            )
        elif block.kind in ("chapter_title", "story_lead_label", "story_lead_title"):
            out.append(f'<h2 style="{css}">{_inline(block.text)}</h2>')
        elif block.kind == "block_quote":
            out.append(f'<blockquote style="{css}">{_inline(block.text)}</blockquote>')
        else:
            out.append(f'<p style="{css}">{_inline(block.text)}</p>')

    out.append("</body></html>")
    return "\n".join(out)


def render_markdown(book: CompiledBook, sheet: StyleSheet) -> str:
    """Plain markdown - no styling, for writers who take it elsewhere."""
    out: List[str] = [f"# {book.title}", ""]
    byline = " · ".join(p for p in (book.author, book.genre) if p)
    if byline:
        out += [f"*{byline}*", ""]
    for block in book.blocks:
        if block.kind == "scene_break":
            out += [sheet.scene_break_marker, ""]
        elif block.kind in ("chapter_title", "story_lead_label", "story_lead_title"):
            out += [f"## {block.text}", ""]
        elif block.kind == "block_quote":
            out += [f"> {block.text}", ""]
        elif block.kind == "story_hook":
            out += [f"*{block.text}*", ""]
        else:
            out += [block.text, ""]
    return "\n".join(out).rstrip() + "\n"


RENDERERS: Dict[str, Callable[[CompiledBook, StyleSheet], str]] = {
    "html": render_html,
    "markdown": render_markdown,
}

# DOCX, EPUB, and PDF produce bytes, not text. They live in their own modules
# and are imported lazily so the text formats never pay for loading them.
BINARY_FORMATS = ("docx", "epub", "pdf")

FORMATS = tuple(sorted(RENDERERS)) + BINARY_FORMATS

# What each format is served as, so the route does not have to know.
CONTENT_TYPES = {
    "html": "text/html; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
}

EXTENSIONS = {
    "html": "html", "markdown": "md", "docx": "docx", "epub": "epub", "pdf": "pdf",
}


def _unknown(fmt: str) -> ValueError:
    return ValueError(
        f"Unknown format '{fmt}'. Available: {', '.join(sorted(FORMATS))}."
    )


def render(book: CompiledBook, sheet: StyleSheet, fmt: str) -> str:
    """Render a text format. Use `render_bytes` for anything servable."""
    renderer = RENDERERS.get(fmt)
    if renderer is None:
        raise _unknown(fmt)
    return renderer(book, sheet)


def render_bytes(book: CompiledBook, sheet: StyleSheet, fmt: str) -> bytes:
    """Render any supported format to the bytes that go over the wire."""
    if fmt in RENDERERS:
        return render(book, sheet, fmt).encode("utf-8")
    if fmt == "docx":
        from compile_docx import render_docx

        return render_docx(book, sheet)
    if fmt == "epub":
        from compile_epub import render_epub

        return render_epub(book, sheet)
    if fmt == "pdf":
        from compile_pdf import render_pdf

        return render_pdf(book, sheet)
    raise _unknown(fmt)
