"""Disposable converter entry point; never import this into a web worker."""

from __future__ import annotations

import json
import os
import re
import resource
import subprocess
import sys
import zipfile
from pathlib import Path

from .document_import import MAX_CHARACTERS, DocumentImportError

MAX_EXPANDED = 32 * 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def text_file(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ["utf-16"]
    else:
        encodings = ["utf-8-sig", "gb18030"]
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except UnicodeError:
            continue
        if any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
            break
        return text
    raise DocumentImportError("无法识别文字编码，请另存为 UTF-8 文本后导入。")


def word_document(path: Path) -> tuple[str, list[str]]:
    from defusedxml import ElementTree as ET

    def inline(node):
        if node.tag in (W + "del", W + "moveFrom"):
            return ""
        if node.tag == W + "t":
            return node.text or ""
        if node.tag == W + "tab":
            return "\t"
        if node.tag in (W + "br", W + "cr"):
            return "\n"
        return "".join(inline(child) for child in node)

    def blocks(node):
        if node.tag == W + "p":
            return [inline(node)]
        if node.tag == W + "tbl":
            return [
                " | ".join(" / ".join(blocks(cell)) for cell in row.findall(W + "tc"))
                for row in node.findall(W + "tr")
            ]
        return [part for child in node for part in blocks(child)]

    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 2000 or sum(x.file_size for x in members) > MAX_EXPANDED:
            raise DocumentImportError("Word 解压内容过大，请拆分或另存为纯文本。")
        names = [x.filename for x in members]
        if len(names) != len(set(names)) or "word/document.xml" not in names:
            raise DocumentImportError("文件不是有效的 DOCX 文档。")
        if any(x.flag_bits & 1 for x in members):
            raise DocumentImportError("请先移除 Word 文件密码再导入。")
        root = ET.fromstring(archive.read("word/document.xml"), forbid_dtd=True)
        body = root.find(W + "body")
        if body is None:
            raise DocumentImportError("Word 文档缺少正文。")
        parts = blocks(body)
        for name, label, element in (
            ("footnotes", "脚注", "footnote"),
            ("endnotes", "尾注", "endnote"),
        ):
            member = f"word/{name}.xml"
            if member in names:
                notes = ET.fromstring(archive.read(member), forbid_dtd=True)
                for note in notes.findall(W + element):
                    if note.get(W + "type") in ("separator", "continuationSeparator"):
                        continue
                    parts.extend([f"[{label} {note.get(W + 'id', '')}]", *blocks(note)])
    return "\n\n".join(parts), [
        "已提取正文、表格和脚注/尾注；图片、页眉页脚和批注未导入。修订按接受后的文字读取。"
    ]


def convert(command: list[str], path: Path) -> str:
    output = path.parent / "converted.txt"
    try:
        with output.open("wb") as stream:
            result = subprocess.run(
                command, stdout=stream, stderr=subprocess.PIPE, check=False
            )
    except FileNotFoundError as exc:
        raise DocumentImportError(
            "服务缺少文档解析组件，请管理员安装 poppler-utils 和 antiword。"
        ) from exc
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace").lower()
        if "password" in error or "encrypted" in error:
            raise DocumentImportError("文件已加密，请先移除密码再导入。")
        raise DocumentImportError(
            "无法读取此文件，请确认格式与扩展名一致且文件未损坏。"
        )
    if output.stat().st_size > MAX_OUTPUT:
        raise DocumentImportError("提取文字过多，请拆分文件后导入。")
    return output.read_text(encoding="utf-8")


def pdf_document(path: Path) -> tuple[str, list[str], int]:
    if not path.read_bytes()[:1024].lstrip().startswith(b"%PDF-"):
        raise DocumentImportError("文件不是有效的 PDF。")
    info = convert(["pdfinfo", str(path)], path)
    count = re.search(r"^Pages:\s+(\d+)", info, re.M)
    if not count:
        raise DocumentImportError("无法读取 PDF 页数，请检查文档。")
    pages = int(count[1])
    if pages > 500:
        raise DocumentImportError("PDF 最多支持 500 页，请拆分后导入。")
    text = convert(["pdftotext", "-enc", "UTF-8", str(path), "-"], path)
    parts = text.split("\f")
    if parts and not parts[-1].strip():
        parts.pop()
    empty = [str(i + 1) for i, page in enumerate(parts) if not page.strip()]
    if not text.strip():
        raise DocumentImportError(
            "PDF 没有可提取的文字，可能是扫描版。请先进行 OCR 识别，或导入 TXT/Word。"
        )
    warnings = ["PDF 按文字阅读顺序提取；复杂分栏、表格和图片中的文字请在预览中核对。"]
    if empty:
        warnings.append(
            f"有 {len(empty)} 页未提取到文字（第 {', '.join(empty[:20])}{'…' if len(empty) > 20 else ''} 页），可能为空白或扫描页；本次未进行 OCR。"
        )
    return "\n\n".join(parts), warnings, pages


def extract(path: Path) -> dict:
    suffix = path.suffix.lower()
    warnings, pages = [], None
    if suffix in (".txt", ".md", ".markdown"):
        text = text_file(path.read_bytes())
    elif suffix == ".docx":
        text, warnings = word_document(path)
    elif suffix == ".doc":
        if not path.read_bytes().startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
            raise DocumentImportError(
                "文件不是 Word DOC 格式，请用 Word 另存为 DOCX 后导入。"
            )
        text = convert(["antiword", "-m", "UTF-8.txt", "-w", "0", str(path)], path)
        warnings = ["旧版 Word 已转换为文字，请核对表格和特殊符号；图片与批注未导入。"]
    else:
        text, warnings, pages = pdf_document(path)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n").strip()
    if not text:
        raise DocumentImportError("文件中没有可导入的文字。")
    if len(text) > MAX_CHARACTERS:
        raise DocumentImportError(
            "提取文字超过 20 万字符，请拆分后导入；内容没有被截断。"
        )
    return {"text": text, "characters": len(text), "pages": pages, "warnings": warnings}


def main():
    # These limits are inherited by native converters. The caller also kills the
    # entire process group on wall timeout, so malformed inputs cannot pin API work.
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    os.umask(0o077)
    try:
        result = extract(Path(sys.argv[1]))
    except DocumentImportError as exc:
        result = {"error": str(exc)}
    except Exception:
        result = {"error": "文件解析失败，请检查文件是否损坏或加密，或另存为 TXT/MD。"}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
