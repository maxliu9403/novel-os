"""Real file extraction and API boundary checks; never invoke a model."""

import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from test_workshop_api import client  # noqa: F401

from core.document_import import (
    MAX_CHARACTERS,
    MAX_UPLOAD,
    DocumentImportError,
    extract_document,
)


def docx(xml=None, *, notes=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            xml
            or """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
          <w:p><w:r><w:t>第一幕：她发现真相。</w:t></w:r></w:p>
          <w:tbl><w:tr><w:tc><w:p><w:r><w:t>人物</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>选择</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
          <w:p><w:del><w:r><w:delText>删除的旧结局</w:delText></w:r></w:del><w:ins><w:r><w:t>她带着女儿离开。</w:t></w:r></w:ins></w:p>
        </w:body></w:document>""",
        )
        if notes:
            archive.writestr("word/footnotes.xml", notes)
    return stream.getvalue()


def pdf(pages):
    # Minimal original PDF fixture with an ordinary Helvetica text layer.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for text in pages:
        page_id = len(objects) + 1
        kids.append(f"{page_id} 0 R")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode()
        )
        stream = f"BT /F1 12 Tf 50 740 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    )
    result, offsets = b"%PDF-1.4\n", [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(result)
    result += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    result += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    return (
        result
        + f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode()
    )


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "gb18030"])
def test_text_encodings_preserve_story_and_markdown(encoding):
    text = "# 故事\r\n\r\n她要夺回家园。\r\n末尾决定：独自离开。"
    result = extract_document("大纲.MD", text.encode(encoding))
    assert result["text"] == text.replace("\r\n", "\n")
    assert result["characters"] == len(result["text"])


def test_docx_paragraph_table_changes_and_footnote():
    note = '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:footnote w:id="1"><w:p><w:r><w:t>限定：不能改变女儿身份。</w:t></w:r></w:p></w:footnote></w:footnotes>'
    result = extract_document("outline.docx", docx(notes=note))
    assert "人物 | 选择" in result["text"]
    assert "她带着女儿离开。" in result["text"]
    assert "不能改变女儿身份" in result["text"]
    assert "删除的旧结局" not in result["text"]
    assert result["warnings"]


@pytest.mark.parametrize(
    "name,data,match",
    [
        ("bad.exe", b"hi", "请选择"),
        ("empty.txt", b"", "为空"),
        ("binary.txt", b"a\x00b", "编码"),
        ("large.txt", b"a" * (MAX_UPLOAD + 1), "10 MiB"),
        ("long.txt", b"a" * (MAX_CHARACTERS + 1), "没有被截断"),
        ("fake.pdf", b"not a PDF", "有效的 PDF"),
        ("fake.doc", b"PKzip", "DOC 格式"),
        ("broken.docx", b"PKcorrupt", "解析失败"),
    ],
)
def test_invalid_inputs_are_explicit_not_truncated(name, data, match):
    with pytest.raises(DocumentImportError, match=match):
        extract_document(name, data)


def test_docx_entities_and_zip_expansion_are_rejected():
    unsafe = (
        '<!DOCTYPE a [<!ENTITY secret SYSTEM "file:///etc/passwd">]><a>&secret;</a>'
    )
    with pytest.raises(DocumentImportError):
        extract_document("external.docx", docx(unsafe))
    with pytest.raises(DocumentImportError, match="解压内容过大"):
        extract_document("large.docx", docx("x" * (33 * 1024 * 1024)))


@pytest.mark.skipif(
    not shutil.which("pdftotext"), reason="poppler installed in deployment image"
)
def test_real_pdf_all_pages_and_missing_text_warning():
    result = extract_document(
        "story.pdf", pdf(["Opening conflict", "", "Final choice"])
    )
    assert result["pages"] == 3
    assert "Opening conflict" in result["text"] and "Final choice" in result["text"]
    assert any("第 2 页" in w for w in result["warnings"])
    with pytest.raises(DocumentImportError, match="OCR"):
        extract_document("scanned.pdf", pdf([""]))
    with pytest.raises(DocumentImportError, match="500 页"):
        extract_document("too-many.pdf", pdf(["Text"] * 501))


@pytest.mark.skipif(
    not shutil.which("antiword"), reason="antiword installed in deployment image"
)
def test_real_legacy_word():
    result = extract_document(
        "story.doc",
        (Path(__file__).parent / "fixtures/document-import/story.doc").read_bytes(),
    )
    assert "Mara must choose" in result["text"]
    assert "FINAL CONSTRAINT" in result["text"]


def test_timeout_kills_converter_group(monkeypatch):
    import core.document_import as module

    class Waiting:
        pid = 123
        calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("converter", timeout)
            return b"", b""

    process, killed = Waiting(), []
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(module.os, "killpg", lambda *args: killed.append(args))
    with pytest.raises(DocumentImportError, match="超时"):
        extract_document("slow.txt", b"Text")
    assert killed[0][0] == process.pid and process.calls == 2


def test_api_is_stateless_and_returns_full_editable_text(client):  # noqa: F811
    response = client.post(
        "/api/document-import",
        files={
            "file": ("构想.md", "# 开篇\n\n结局必须保留。".encode(), "text/markdown")
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["text"].endswith("结局必须保留。")
    assert client.get("/api/workshop/sessions").json()["sessions"] == []
    assert (
        client.post(
            "/api/document-import", files={"file": ("empty.txt", b"")}
        ).status_code
        == 422
    )


def test_concurrent_import_limit(client):  # noqa: F811
    from api.document_import import _slots

    _slots.acquire()
    _slots.acquire()
    try:
        response = client.post(
            "/api/document-import", files={"file": ("story.txt", b"story")}
        )
        assert response.status_code == 429
    finally:
        _slots.release()
        _slots.release()
