"""Explicit, atomic import of author-owned Markdown into a workspace library.

Usage: python -m core.knowledge_import DIRECTORY --store WORKSPACE_LIBRARY [--apply]
Without --apply this only parses and previews identities/revisions. No LLM calls.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .creative_types import CreativeTypeError, CreativeTypeStore, parse_document, text_sha


def read_import(directory: Path) -> list[tuple[Path, str]]:
    paths = sorted(p for p in directory.glob("*.md") if p.name.casefold() != "readme.md")
    if not paths:
        raise CreativeTypeError("目录中没有 Tag / Genre Markdown 文件。")
    # Decode bytes directly: read_text() would normalize CRLF and change hashes.
    try:
        return [(p, p.read_bytes().decode("utf-8")) for p in paths]
    except UnicodeDecodeError:
        raise CreativeTypeError("Markdown 必须使用 UTF-8 编码；未修改源文件。") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        files = read_import(args.directory)
        docs = [parse_document(md, enabled=True) for _, md in files]
        store = CreativeTypeStore(args.store)
        records = store.records()
        revisions = {d["id"]: records[d["id"]]["revision"] if d["id"] in records else None for d in docs}
        result = {
            "mode": "preview",
            "items": [
                {"file": p.name, "id": d["id"], "name": d["name"],
                 "expected_revision": revisions[d["id"]], "source_sha256": text_sha(md)}
                for (p, md), d in zip(files, docs)
            ],
        }
        if args.apply:
            result = {"mode": "applied", **store.import_documents(
                [md for _, md in files], expected_revisions=revisions,
            )}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (CreativeTypeError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
