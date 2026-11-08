"""Author-facing Markdown intake. Identity is separate from unconstrained prose.

Only the metadata envelope is generated/upgraded; body bytes are never rewritten.
Legacy snapshot parsing stays in creative_types and retains its original compiler.
"""
from __future__ import annotations

import re

import yaml
from pydantic import ValidationError

from .creative_types import (
    CreativeTypeError, FreeformHeader, parse_document, read_document_header,
    validate_markdown_size,
)


def document_body(markdown: str) -> str:
    if markdown.startswith('---'):
        return read_document_header(markdown)[1]
    return markdown


def document_view(markdown: str) -> dict:
    return {**parse_document(markdown), 'body': document_body(markdown)}


def _edited_body(markdown: str, header: dict) -> str:
    """A full-file paste is prose editing, never a change to the saved identity."""
    while re.match(r'\A\ufeff?---\r?\n', markdown) and re.search(
        r'^schema:\s*novel-knowledge\.v[123]\s*$',
        markdown.split('---', 2)[1], re.M,
    ):
        pasted, body = read_document_header(markdown.removeprefix('\ufeff'))
        if pasted['id'] != header['id'] or pasted['kind'] != header['kind']:
            raise CreativeTypeError('文件属于另一份资料，请通过「导入 MD」选择对应资料。')
        markdown = body
    return markdown


def prepare_markdown(
    markdown: str, *, base_markdown: str | None = None, identity: dict | None = None,
) -> dict:
    """Preview an import/edit without storing or activating anything.

    Missing file identity asks the UI for name/kind/Genre, never guesses an ID.
    Editing a body uses the saved envelope; author prose cannot rename the record.
    """
    validate_markdown_size(markdown)
    if base_markdown is not None and identity is not None:
        raise CreativeTypeError('正文编辑与新建资料信息不能混用。')
    if base_markdown is not None:
        doc = parse_document(base_markdown)
        header = {k: v for k, v in doc.items() if k in FreeformHeader.model_fields or k == 'schema'}
        body = _edited_body(markdown, header)
    elif identity is not None:
        header, body = identity.copy(), markdown
    elif re.match(r'\A\ufeff?---\r?\n', markdown) and re.search(
        r'^\s*(schema|id|kind):', markdown.split('---', 2)[1], re.M,
    ):
        # BOM is an encoding signature, not part of author prose.
        header, body = read_document_header(markdown.removeprefix('\ufeff'))
        if header['schema'] == 'novel-knowledge.v3':
            return {'needs_identity': False, 'markdown': markdown.removeprefix('\ufeff'),
                    'document': document_view(markdown.removeprefix('\ufeff'))}
    else:
        try:
            # Existing standalone bilingual Tag files already declare identity.
            doc = parse_document(markdown)
        except CreativeTypeError:
            return {'needs_identity': True, 'body': markdown}
        header = {k: v for k, v in doc.items() if k in FreeformHeader.model_fields or k == 'schema'}
        body = markdown
    try:
        header = FreeformHeader.model_validate({**header, 'schema': 'novel-knowledge.v3'}).model_dump(by_alias=True)
    except ValidationError as exc:
        raise CreativeTypeError('资料信息：' + exc.errors(include_input=False)[0]['msg']) from None
    prepared = '---\n' + yaml.safe_dump(header, allow_unicode=True, sort_keys=False) + '---\n' + body
    validate_markdown_size(prepared)
    return {'needs_identity': False, 'markdown': prepared, 'document': document_view(prepared)}
