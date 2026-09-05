"""DOCX and EPUB output (PLAN.md P6).

Both are ZIP-of-XML formats written directly, with no document library. The
tests here check the parts a reader will actually reject a file over - archive
layout, required entries, well-formed XML - rather than eyeballing markup.
"""

import sys
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

import pytest

_CORE = Path(__file__).resolve().parents[1] / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from compile_book import gather, inline_runs, render_bytes  # noqa: E402
from compile_docx import render_docx  # noqa: E402
from compile_epub import render_epub  # noqa: E402
from styles import Style, StyleSheet  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _book():
    return gather(
        title="The Pier", author="M", genre="Literary",
        chapters=[
            {"number": 1, "title": "Arrival",
             "text": "She waited.\n\nThe tide came **in**.\n\n---\n\nHe did not."},
            {"number": 2, "title": "Departure", "text": "They left."},
        ],
    )


@dataclass(frozen=True)
class _PublicationCopyFixture:
    language: str = "en-US"
    reader_heading: str = "Before the Story"
    hook_lead: str = "Claire must confront Ethan before his lies cost their daughter."
    spoiler_free_blurb: str = (
        "Claire discovers that Ethan's lies reach far beyond one missed dinner. "
        "As money disappears & promises fail, she records the truth and must "
        "decide what kind of home will protect their daughter."
    )

    def to_dict(self):
        return {
            "language": self.language,
            "reader_heading": self.reader_heading,
            "hook_lead": self.hook_lead,
            "spoiler_free_blurb": self.spoiler_free_blurb,
        }


def _book_with_publication_copy():
    return gather(
        title="The Pier",
        author="M",
        genre="Literary",
        publication_copy=_PublicationCopyFixture(),
        chapters=[{"number": 1, "title": "Arrival", "text": "# Arrival\n\nBody."}],
    )


def _zip(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(data))


# ------------------------------------------------------- inline tokenizer

def test_plain_text_is_one_run():
    runs = inline_runs("just words")
    assert len(runs) == 1
    assert (runs[0].text, runs[0].bold, runs[0].italic) == ("just words", False, False)


def test_bold_and_italic_are_split_out():
    runs = inline_runs("a **b** c *d*")
    assert [(r.text, r.bold, r.italic) for r in runs] == [
        ("a ", False, False),
        ("b", True, False),
        (" c ", False, False),
        ("d", False, True),
    ]


def test_the_tokenizer_never_loses_characters():
    for text in ["a **b** c", "*x*", "no marks", "**only bold**", ""]:
        assert "".join(r.text for r in inline_runs(text)) == (
            text.replace("**", "").replace("*", "")
        )


# ------------------------------------------------------------------- docx

def test_docx_is_a_zip_with_the_parts_a_reader_requires():
    z = _zip(render_docx(_book(), StyleSheet()))
    assert set(z.namelist()) >= {
        "[Content_Types].xml", "_rels/.rels", "word/document.xml",
    }


def test_docx_uses_stable_zip_metadata_for_identical_compiled_input():
    rendered = render_docx(_book(), StyleSheet())

    assert render_docx(_book(), StyleSheet()) == rendered
    assert {
        info.date_time for info in _zip(rendered).infolist()
    } == {(1980, 1, 1, 0, 0, 0)}


def test_docx_parts_are_well_formed_xml():
    z = _zip(render_docx(_book(), StyleSheet()))
    for name in z.namelist():
        ElementTree.fromstring(z.read(name))  # raises if malformed


def test_docx_contains_the_prose_in_reading_order():
    z = _zip(render_docx(_book(), StyleSheet()))
    doc = z.read("word/document.xml").decode("utf-8")
    assert doc.index("Arrival") < doc.index("Departure")
    assert "She waited." in doc and "They left." in doc


def test_docx_carries_the_title_and_byline():
    doc = _zip(render_docx(_book(), StyleSheet())).read(
        "word/document.xml").decode("utf-8")
    assert "The Pier" in doc
    assert "Literary" in doc


def test_docx_sizes_are_half_points():
    sheet = StyleSheet()
    sheet.styles["body"] = Style(size_pt=13)
    doc = _zip(render_docx(_book(), sheet)).read("word/document.xml").decode("utf-8")
    assert 'w:sz w:val="26"' in doc


def test_docx_marks_emphasis_as_runs_not_asterisks():
    doc = _zip(render_docx(_book(), StyleSheet())).read(
        "word/document.xml").decode("utf-8")
    assert "<w:b/>" in doc
    assert "**" not in doc


def test_docx_starts_chapters_on_a_new_page():
    doc = _zip(render_docx(_book(), StyleSheet())).read(
        "word/document.xml").decode("utf-8")
    assert "<w:pageBreakBefore/>" in doc


def test_docx_uses_the_scene_break_marker():
    sheet = StyleSheet(scene_break_marker="~ ~ ~")
    doc = _zip(render_docx(_book(), sheet)).read("word/document.xml").decode("utf-8")
    assert "~ ~ ~" in doc


def test_docx_escapes_prose_rather_than_trusting_it():
    book = gather(title="A & B", author="", genre="", chapters=[
        {"number": 1, "title": "One", "text": "5 < 6 & <w:p> is not markup here"},
    ])
    doc = _zip(render_docx(book, StyleSheet())).read("word/document.xml").decode("utf-8")
    ElementTree.fromstring(doc)
    assert "&amp;" in doc and "&lt;w:p&gt;" in doc


def test_docx_preserves_significant_whitespace():
    doc = _zip(render_docx(_book(), StyleSheet())).read(
        "word/document.xml").decode("utf-8")
    assert 'xml:space="preserve"' in doc


def test_docx_paragraph_count_matches_the_blocks():
    book = _book()
    root = ElementTree.fromstring(
        _zip(render_docx(book, StyleSheet())).read("word/document.xml"))
    paragraphs = root.findall(f".//{W}p")
    # every block, plus the title and the byline
    assert len(paragraphs) == len(book.blocks) + 2


def test_docx_projects_structured_copy_before_chapter_one():
    doc = _zip(render_docx(_book_with_publication_copy(), StyleSheet())).read(
        "word/document.xml"
    ).decode("utf-8")
    assert (
        doc.index("Introduction")
        < doc.index("Before the Story")
        < doc.index("Claire must confront Ethan")
        < doc.index("Claire discovers")
        < doc.index("Arrival")
    )


# ------------------------------------------------------------------- epub

def test_epub_mimetype_is_first_and_stored_uncompressed():
    """Readers sniff it at a fixed offset; compress it and the file is invalid."""
    data = render_epub(_book(), StyleSheet())
    z = _zip(data)
    first = z.infolist()[0]
    assert first.filename == "mimetype"
    assert first.compress_type == zipfile.ZIP_STORED
    assert z.read("mimetype") == b"application/epub+zip"


def test_epub_has_the_required_parts():
    z = _zip(render_epub(_book(), StyleSheet()))
    names = set(z.namelist())
    assert {"META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml",
            "OEBPS/style.css"} <= names


def test_epub_xml_parts_are_well_formed():
    z = _zip(render_epub(_book(), StyleSheet()))
    for name in z.namelist():
        if name.endswith((".xml", ".xhtml", ".opf")):
            ElementTree.fromstring(z.read(name))


def test_epub_has_one_document_per_chapter():
    z = _zip(render_epub(_book(), StyleSheet()))
    chapters = [n for n in z.namelist() if n.startswith("OEBPS/chap")]
    assert len(chapters) == 2


def test_every_spine_item_is_declared_in_the_manifest():
    """A spine entry with no manifest item is the classic invalid EPUB."""
    z = _zip(render_epub(_book(), StyleSheet()))
    opf = ElementTree.fromstring(z.read("OEBPS/content.opf"))
    ns = {"opf": "http://www.idpf.org/2007/opf"}
    manifest = {i.get("id") for i in opf.findall(".//opf:manifest/opf:item", ns)}
    spine = {i.get("idref") for i in opf.findall(".//opf:spine/opf:itemref", ns)}
    assert spine and spine <= manifest


def test_epub_navigation_lists_every_chapter_title():
    nav = _zip(render_epub(_book(), StyleSheet())).read(
        "OEBPS/nav.xhtml").decode("utf-8")
    assert "Arrival" in nav and "Departure" in nav


def test_epub_keeps_story_lead_before_chapter_one_without_renaming_navigation():
    book = gather(title="T", author="", genre="", chapters=[
        {
            "number": 1,
            "title": "第一章 抉择",
            "text": "## STORY_LEAD: 序\n\n她决定拿回一切。\n\n# 第一章 抉择\n\n门开了。",
        },
    ])

    z = _zip(render_epub(book, StyleSheet()))
    page = z.read("OEBPS/chap001.xhtml").decode("utf-8")
    nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
    body = page.split("<body>", 1)[1]
    assert body.index("序") < body.index("第一章 抉择")
    assert "STORY_LEAD" not in page
    assert "第一章 抉择" in nav
    assert ">序</a>" not in nav


def test_epub_styles_come_from_the_sheet():
    sheet = StyleSheet()
    sheet.styles["body"] = Style(size_pt=17)
    css = _zip(render_epub(_book(), sheet)).read("OEBPS/style.css").decode("utf-8")
    assert "font-size:17pt" in css
    assert ".block-quote{" in css


def test_epub_escapes_prose():
    book = gather(title="A & B", author="", genre="", chapters=[
        {"number": 1, "title": "One", "text": "<script>alert(1)</script>"},
    ])
    z = _zip(render_epub(book, StyleSheet()))
    page = z.read("OEBPS/chap001.xhtml").decode("utf-8")
    ElementTree.fromstring(page)
    assert "<script>" not in page


def test_epub_uses_intro_description_language_and_chapter_only_navigation():
    book = _book_with_publication_copy()
    z = _zip(render_epub(book, StyleSheet()))
    names = z.namelist()

    assert names.index("OEBPS/intro.xhtml") < names.index("OEBPS/chap001.xhtml")
    intro = z.read("OEBPS/intro.xhtml").decode("utf-8")
    assert intro.index("Introduction") < intro.index("Before the Story")
    assert intro.index("Before the Story") < intro.index("Claire must confront Ethan")
    assert intro.index("Claire must confront Ethan") < intro.index("Claire discovers")

    opf = ElementTree.fromstring(z.read("OEBPS/content.opf"))
    ns = {
        "opf": "http://www.idpf.org/2007/opf",
        "dc": "http://purl.org/dc/elements/1.1/",
    }
    assert opf.findtext(".//dc:description", namespaces=ns) == (
        book.publication_copy.spoiler_free_blurb
    )
    assert opf.findtext(".//dc:language", namespaces=ns) == "en-US"
    assert [
        item.get("idref") for item in opf.findall(".//opf:spine/opf:itemref", ns)
    ] == ["intro", "c1"]

    nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
    assert "Arrival" in nav
    assert "intro.xhtml" not in nav
    assert "Before the Story" not in nav


def test_epub_is_byte_stable_for_identical_compiled_input():
    book = _book_with_publication_copy()
    assert render_epub(book, StyleSheet()) == render_epub(book, StyleSheet())


# --------------------------------------------------------------- dispatch

@pytest.mark.parametrize("fmt", ["html", "markdown", "docx", "epub", "pdf"])
def test_render_bytes_produces_something_for_every_format(fmt):
    out = render_bytes(_book(), StyleSheet(), fmt)
    assert isinstance(out, bytes) and len(out) > 0


def test_pdf_is_a_unicode_document_with_page_objects():
    book = gather(
        title="孩子出生那天，我签了离婚协议", author="", genre="",
        chapters=[{"number": 1, "title": "开篇", "text": "她抱着孩子走进雨里。"}],
    )
    pdf = render_bytes(book, StyleSheet(), "pdf")

    assert pdf.startswith(b"%PDF-1.4")
    assert b"/Type /Catalog" in pdf
    assert b"/Subtype /Type0" in pdf
    assert b"/MediaBox [0 0 595 842]" in pdf
    assert "孩子出生那天".encode("utf-16-be").hex().upper().encode("ascii") in pdf


def test_pdf_projects_structured_copy_before_chapter_one():
    pdf = render_bytes(_book_with_publication_copy(), StyleSheet(), "pdf")

    encoded = [
        value.encode("utf-16-be").hex().upper().encode("ascii")
        for value in (
            "Introduction",
            "Before the Story",
            "Claire must confront Ethan before his lies cost their daughter.",
            "Claire discovers that Ethan's lies reach far beyond one missed dinner.",
            "Arrival",
        )
    ]
    assert pdf.index(encoded[0]) < pdf.index(encoded[1])
    assert pdf.index(encoded[1]) < pdf.index(encoded[2])
    assert pdf.index(encoded[2]) < pdf.index(encoded[3])
    assert pdf.index(encoded[3]) < pdf.index(encoded[4])


def test_render_bytes_rejects_an_unknown_format_listing_all_of_them():
    with pytest.raises(ValueError) as e:
        render_bytes(_book(), StyleSheet(), "rtf")
    message = str(e.value)
    for fmt in ("html", "markdown", "docx", "epub", "pdf"):
        assert fmt in message
