"""Bounded local text extraction for an author's editable story brief.

Conversion runs in a disposable process. No model calls, persistence, macros,
external document links, or interpretation of document content as instructions.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_UPLOAD = 10 * 1024 * 1024
MAX_CHARACTERS = 200_000
EXTRACTION_TIMEOUT = 25
EXTENSIONS = {".txt", ".md", ".markdown", ".docx", ".doc", ".pdf"}


class DocumentImportError(ValueError):
    pass


def validate_upload(filename: str, size: int) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in EXTENSIONS:
        raise DocumentImportError("请选择 TXT、MD、Word（DOC/DOCX）或 PDF 文件。")
    if size > MAX_UPLOAD:
        raise DocumentImportError("文件不能超过 10 MiB，请拆分后导入。")
    if not size:
        raise DocumentImportError("文件为空，请选择有内容的文件。")
    return suffix


def extract_document(filename: str, data: bytes) -> dict:
    suffix = validate_upload(filename, len(data))
    with tempfile.TemporaryDirectory(prefix="novelos-brief-") as folder:
        path = Path(folder) / f"input{suffix}"
        path.write_bytes(data)
        process = subprocess.Popen(
            [sys.executable, "-m", "core.document_import_worker", str(path)],
            cwd=Path(__file__).resolve().parent.parent,
            # Do not pass provider credentials or application settings to converters.
            env={
                "PATH": os.defpath,
                "LANG": "C.UTF-8",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=EXTRACTION_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise DocumentImportError(
                "文件解析超时，请拆分文件或另存为 TXT/MD 后导入。"
            ) from exc
        try:
            result = json.loads(output)
            if not isinstance(result, dict):
                raise ValueError("Expected converter result")
        except (ValueError, UnicodeError) as exc:
            raise DocumentImportError(
                "无法解析此文件，请检查文件是否损坏，或另存为 TXT/MD。"
            ) from exc
        if process.returncode or "error" in result:
            raise DocumentImportError(result.get("error") or "文件解析失败。")
        result["filename"] = filename.replace("\\", "/").rsplit("/", 1)[-1][:240]
        return result
