"""Stateless conversion endpoint; author confirms extracted text in the form."""

import threading

from fastapi import APIRouter, File, HTTPException, UploadFile

from core.document_import import (
    MAX_UPLOAD,
    DocumentImportError,
    extract_document,
    validate_upload,
)

router = APIRouter(prefix="/api/document-import", tags=["document-import"])
_slots = threading.BoundedSemaphore(2)


@router.post("")
def import_document(file: UploadFile = File(...)):
    acquired = False
    try:
        validate_upload(file.filename or "", file.size or 0)
        acquired = _slots.acquire(blocking=False)
        if not acquired:
            raise HTTPException(429, "正在解析其他文件，请稍后重试。")
        data = file.file.read(MAX_UPLOAD + 1)
        return extract_document(file.filename or "", data)
    except DocumentImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        file.file.close()
        if acquired:
            _slots.release()
