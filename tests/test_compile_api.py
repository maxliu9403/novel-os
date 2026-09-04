"""Styles and compile over the wire (P5.2 / P6)."""

import hashlib
import json
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from tests.test_publication_copy import HOOK, valid_copy_payload

CH1 = "She waited at the rail.\n\nThe tide came in.\n\n---\n\nHe did not come.\n"
CH2 = "They left before dawn.\n"


def _project_client(tmp_path):
    root = tmp_path / "projects"
    proj = root / "book"
    (proj / "outputs" / "state").mkdir(parents=True)
    (proj / "outputs" / "manuscript").mkdir(parents=True)
    (proj / "outputs" / "state" / "story_state.json").write_text(json.dumps({
        "metadata": {"title": "The Pier", "genre": "Literary", "author": "M"},
        "characters": {}, "plot_threads": {},
        "chapters": {
            "1": {"number": 1, "title": "Arrival", "status": "drafted"},
            "2": {"number": 2, "title": "Departure", "status": "drafted"},
        },
        "timeline": [], "style_profile": {}, "session_log": [],
    }), encoding="utf-8")
    ms = proj / "outputs" / "manuscript"
    (ms / "chapter_001_final.md").write_text(CH1, encoding="utf-8")
    (ms / "chapter_002_final.md").write_text(CH2, encoding="utf-8")
    app = create_app(projects_root=root,
                     db_url=f"sqlite:///{(tmp_path / 'c.db').as_posix()}")
    return TestClient(app), proj


@pytest.fixture
def client(tmp_path):
    return _project_client(tmp_path)[0]


# ------------------------------------------------------------------ styles

def test_styles_come_back_with_defaults_filled_in(client):
    body = client.get("/api/projects/book/styles").json()
    assert body["scene_break_marker"] == "* * *"
    assert body["styles"]["chapter_title"]["bold"] is True
    assert body["styles"]["body"]["first_line_indent_em"] > 0


def test_saving_styles_persists_them(client):
    body = client.get("/api/projects/book/styles").json()
    body["styles"]["body"]["size_pt"] = 13.5
    body["scene_break_marker"] = "~ ~ ~"

    assert client.put("/api/projects/book/styles", json=body).status_code == 200

    again = client.get("/api/projects/book/styles").json()
    assert again["styles"]["body"]["size_pt"] == 13.5
    assert again["scene_break_marker"] == "~ ~ ~"


def test_a_nonsense_stylesheet_is_rejected_whole(client):
    body = client.get("/api/projects/book/styles").json()
    body["styles"]["body"]["size_pt"] = 400

    r = client.put("/api/projects/book/styles", json=body)
    assert r.status_code == 400
    assert "size" in r.json()["detail"]

    # Nothing was written: the good value is still there.
    assert client.get("/api/projects/book/styles").json()[
        "styles"]["body"]["size_pt"] == 12.0


def test_styles_404_for_an_unknown_project(client):
    assert client.get("/api/projects/nope/styles").status_code == 404


# ----------------------------------------------------------------- compile

def test_compile_returns_a_downloadable_html_book(client):
    r = client.get("/api/projects/book/compile?format=html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'filename="book.html"' in r.headers["content-disposition"]
    assert "<title>The Pier</title>" in r.text
    assert "Arrival" in r.text and "Departure" in r.text


def test_compile_walks_every_chapter_in_order(client):
    text = client.get("/api/projects/book/compile?format=html").text
    assert text.index("Arrival") < text.index("Departure")
    assert text.index("She waited") < text.index("They left")


def test_saved_styles_reach_the_compiled_book(client):
    body = client.get("/api/projects/book/styles").json()
    body["styles"]["body"]["size_pt"] = 21
    body["scene_break_marker"] = "~ ~ ~"
    client.put("/api/projects/book/styles", json=body)

    text = client.get("/api/projects/book/compile?format=html").text
    assert "font-size:21pt" in text
    assert "~ ~ ~" in text


def test_markdown_compile_is_offered_too(client):
    r = client.get("/api/projects/book/compile?format=markdown")
    assert r.status_code == 200
    assert 'filename="book.md"' in r.headers["content-disposition"]
    assert r.text.startswith("# The Pier")


def test_downloaded_markdown_and_epub_include_publication_intro_before_chapter_one(
    tmp_path,
):
    client, project = _project_client(tmp_path)
    payload = valid_copy_payload()
    payload["title"] = "The Pier"
    payload["whole_book_core_conflict"]["evidence"] = {
        "opening": [{"chapter": 1, "source_quote": "She waited at the rail."}],
        "middle": [{"chapter": 1, "source_quote": "The tide came in."}],
        "late": [{"chapter": 2, "source_quote": "They left before dawn."}],
    }
    payload["source"]["chapters"] = [
        {
            "number": 1,
            "revision_id": "legacy-final-001",
            "sha256": hashlib.sha256(CH1.encode("utf-8")).hexdigest(),
        },
        {
            "number": 2,
            "revision_id": "legacy-final-002",
            "sha256": hashlib.sha256(CH2.encode("utf-8")).hexdigest(),
        },
    ]
    publication = project / "outputs/publication/publication-copy.json"
    publication.parent.mkdir(parents=True)
    publication.write_text(json.dumps(payload), encoding="utf-8")

    markdown = client.get("/api/projects/book/compile?format=markdown")
    epub = client.get("/api/projects/book/compile?format=epub")

    assert markdown.status_code == 200, markdown.text
    assert markdown.text.index("## Introduction") < markdown.text.index("## Arrival")
    assert HOOK in markdown.text
    assert epub.status_code == 200, epub.text
    with zipfile.ZipFile(BytesIO(epub.content)) as archive:
        intro = archive.read("OEBPS/intro.xhtml").decode("utf-8")
        chapter_one = archive.read("OEBPS/chap001.xhtml").decode("utf-8")
    assert "Introduction" in intro
    assert HOOK in intro
    assert "She waited at the rail." not in intro
    assert "She waited at the rail." in chapter_one


def test_docx_and_epub_are_served_as_downloadable_binaries(client):
    for fmt, ext, magic in [("docx", "docx", b"PK"), ("epub", "epub", b"PK")]:
        r = client.get(f"/api/projects/book/compile?format={fmt}")
        assert r.status_code == 200, fmt
        assert f'filename="book.{ext}"' in r.headers["content-disposition"]
        # Both are ZIP containers.
        assert r.content.startswith(magic), fmt


def test_pdf_is_served_as_a_downloadable_document(client):
    r = client.get("/api/projects/book/compile?format=pdf")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert 'filename="book.pdf"' in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF-1.4")


def test_compile_unicode_project_uses_rfc5987_filename(tmp_path):
    project_id = "孩子出生那天我签了离婚协议"
    root = tmp_path / "projects"
    state_dir = root / project_id / "outputs" / "state"
    state_dir.mkdir(parents=True)
    (root / project_id / "outputs" / "manuscript").mkdir()
    (state_dir / "story_state.json").write_text(json.dumps({
        "metadata": {"title": project_id, "genre": "现实情感", "author": ""},
        "characters": {}, "plot_threads": {},
        "chapters": {"1": {"number": 1, "title": "开篇", "status": "drafted"}},
        "timeline": [], "style_profile": {}, "session_log": [],
    }), encoding="utf-8")
    (root / project_id / "outputs" / "manuscript" / "chapter_001_final.md").write_text(
        "第一章\n\n正文。\n", encoding="utf-8"
    )

    app = create_app(
        projects_root=root,
        db_url=f"sqlite:///{(tmp_path / 'unicode.db').as_posix()}",
    )
    response = TestClient(app).get(f"/api/projects/{project_id}/compile?format=epub")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert 'filename="novel.epub"' in disposition
    assert "filename*=UTF-8''" in disposition
    assert "%E5%AD%A9%E5%AD%90" in disposition
    assert response.content.startswith(b"PK")


def test_an_unknown_format_is_a_400_that_lists_the_options(client):
    r = client.get("/api/projects/book/compile?format=rtf")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "docx" in detail and "epub" in detail and "pdf" in detail


def test_compile_404_for_an_unknown_project(client):
    assert client.get("/api/projects/nope/compile").status_code == 404
