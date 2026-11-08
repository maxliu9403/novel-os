#!/usr/bin/env python3
"""Inventory local research sources and verify declared reading coverage.

This tool does not perform semantic reading, assign quality scores, or call a
model. Output must be outside the source tree; originals are never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys


def digest(path: Path) -> tuple[int, str]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            hasher.update(chunk)
    return size, hasher.hexdigest()


def scoped_file(root: Path, relative: str) -> Path:
    if root.is_symlink():
        raise ValueError("symlink book directory")
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        raise ValueError("invalid source path")
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"source path escapes book: {relative}")
    path = root
    for part in rel.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"symlink is not a research source: {relative}")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError(f"not a regular source file: {relative}")
    return resolved


def record(root: Path, relative: str, role: str, **extra) -> dict:
    path = scoped_file(root, relative)
    size, sha = digest(path)
    return {"path": str(path), "relative_path": relative, "role": role,
            "bytes": size, "sha256": sha, **extra}


def chapter_paths(root: Path) -> list[str]:
    """Discover provided Markdown without assuming manifest order or story role."""
    if root.is_symlink():
        raise ValueError("symlink book directory")
    directory = root / "chapters"
    if directory.is_symlink():
        raise ValueError("symlink chapter directory")
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError("chapters must be a directory")
    paths = []
    for path in sorted(directory.rglob("*.md")):
        relative = path.relative_to(root).as_posix()
        scoped_file(root, relative)
        paths.append(relative)
    return paths


def inventory(root: Path) -> dict:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("source root must be a directory")
    books, errors, ids = [], [], set()
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        if folder.is_symlink():
            errors.append({"book": folder.name, "error": "symlink book directory"})
            continue
        try:
            meta_path = scoped_file(folder, "metadata.json")
            chapter_path = scoped_file(folder, "chapters.json")
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            chapters = json.loads(chapter_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict) or not isinstance(chapters, list):
                raise ValueError("metadata must be an object; chapters must be a list")
            book_id = str(meta.get("book_id") or "").strip()
            if not book_id or book_id in ids:
                raise ValueError("missing or duplicate book_id")
            tags, genres = meta.get("tags", []), meta.get("genres", [])
            if any(not isinstance(values, list) or any(not isinstance(x, str) for x in values)
                   for values in (tags, genres)):
                raise ValueError("genres and tags must be string arrays")
            files = [record(folder, "metadata.json", "metadata"),
                     record(folder, "chapters.json", "chapter_manifest"),
                     record(folder, "synopsis.md", "synopsis")]
            paths, indexes, chapter_ids = set(), set(), set()
            for chapter in chapters:
                if not isinstance(chapter, dict):
                    raise ValueError("chapter entry must be an object")
                index, relative = chapter.get("index"), chapter.get("path")
                chapter_id = str(chapter.get("chapter_id") or "").strip()
                if type(index) is not int or index < 1 or index in indexes:
                    raise ValueError("chapter index missing, invalid or duplicated")
                if not chapter_id or chapter_id in chapter_ids:
                    raise ValueError("chapter_id missing or duplicated")
                entry = record(folder, relative, "chapter", index=index, chapter_id=chapter_id)
                if entry["path"] in paths:
                    raise ValueError("duplicate normalized chapter path")
                paths.add(entry["path"])
                indexes.add(index)
                chapter_ids.add(chapter_id)
                files.append(entry)
            files[3:] = sorted(files[3:], key=lambda value: value["index"])
            warnings = []
            if meta.get("free_chapter_count") != len(chapters):
                warnings.append("metadata free_chapter_count differs from provided files")
            if sorted(indexes) != list(range(1, len(indexes) + 1)):
                warnings.append("provided chapter indexes are not contiguous from 1")
            if isinstance(meta.get("chapters"), list) and meta["chapters"] != chapters:
                warnings.append("metadata chapter list differs from chapters.json")
            discovered = chapter_paths(folder)
            unlisted = [relative for relative in discovered
                        if str(scoped_file(folder, relative)) not in paths]
            if unlisted:
                warnings.append({"unlisted_chapter_files": unlisted})
                # They remain unordered, but cannot disappear from full-reading coverage.
                files.extend(record(folder, relative, "unlisted_chapter") for relative in unlisted)
            optional = []
            for name in ("overview.md", "reading_guide.md", "cover.jpg", "cover.jpeg", "cover.png"):
                candidate = folder / name
                if candidate.exists() or candidate.is_symlink():
                    optional.append(record(folder, name, "optional_source"))
            ids.add(book_id)
            books.append({"book_id": book_id, "title": meta.get("novel_name", folder.name),
                          "root": str(folder), "genres": genres, "tags": tags,
                          "files": files, "chapter_paths": discovered,
                          "optional_files": optional, "warnings": warnings})
        except (ValueError, OSError, TypeError) as exc:
            errors.append({"book": folder.name, "error": str(exc)})
    frequencies = Counter(tag for book in books for tag in set(book["tags"]))
    return {"schema": "novel-corpus-inventory.v1", "root": str(root), "books": books,
            "book_directories": sorted(p.name for p in root.iterdir() if p.is_dir()),
            "errors": errors, "counts": {"books": len(books),
            "chapter_files": sum(f["role"] in {"chapter", "unlisted_chapter"} for b in books for f in b["files"]),
            "tags": len(frequencies)}, "tag_frequencies": dict(frequencies.most_common()),
            "semantic_reading_performed": False}


def coverage(inv: dict, ledger_paths: list[Path]) -> dict:
    if inv.get("schema") != "novel-corpus-inventory.v1":
        raise ValueError("unsupported inventory schema")
    declarations, errors = {}, []
    try:
        current_directories = sorted(p.name for p in Path(inv["root"]).iterdir() if p.is_dir())
        if current_directories != inv.get("book_directories"):
            errors.append({"error": "corpus directory scope changed or was not captured; rebuild inventory"})
    except OSError as exc:
        errors.append({"error": str(exc)})
    for ledger_path in ledger_paths:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if ledger.get("schema") != "novel-corpus-reading.v1" or not isinstance(ledger.get("files"), list):
            raise ValueError(f"unsupported reading ledger: {ledger_path}")
        for entry in ledger["files"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                errors.append({"ledger": str(ledger_path), "error": "malformed file declaration"})
                continue
            path = entry["path"]
            if path in declarations and declarations[path] != entry:
                errors.append({"path": path, "error": "conflicting reading declarations"})
            declarations[path] = entry
    books = []
    expected_paths = set()
    for book in inv["books"]:
        missing, matched = [], []
        try:
            if chapter_paths(Path(book["root"])) != book.get("chapter_paths"):
                missing.append({"path": book["root"], "reason": "chapter directory scope changed or was not captured; rebuild inventory"})
            for source in book["files"]:
                if source["role"] not in {"metadata", "chapter_manifest"}:
                    continue
                fresh = scoped_file(Path(book["root"]), source["relative_path"])
                if digest(fresh) != (source["bytes"], source["sha256"]):
                    missing.append({"path": source["path"], "reason": "source identity or manifest changed after inventory"})
        except (OSError, ValueError) as exc:
            missing.append({"path": book["root"], "reason": str(exc)})
        required = [f for f in book["files"] if f["role"] in {"synopsis", "chapter", "unlisted_chapter"}]
        for source in required:
            path = source["path"]
            expected_paths.add(path)
            entry = declarations.get(path)
            reason = None
            if entry is None:
                reason = "not declared read"
            elif (entry.get("fully_read") is not True or entry.get("book_id") != book["book_id"]
                  or not isinstance(entry.get("observation"), str) or not entry["observation"].strip()):
                reason = "incomplete reading declaration"
            elif entry.get("sha256") != source["sha256"]:
                reason = "declaration hash differs from inventory"
            else:
                try:
                    fresh = scoped_file(Path(book["root"]), source["relative_path"])
                    if digest(fresh) != (source["bytes"], source["sha256"]):
                        reason = "source changed after inventory"
                except (OSError, ValueError) as exc:
                    reason = str(exc)
            if reason:
                missing.append({"path": path, "reason": reason})
            else:
                matched.append(path)
        books.append({"book_id": book["book_id"], "title": book["title"],
                      "required_files": len(required), "matched_files": len(matched),
                      "declared_complete": bool(required) and not missing, "gaps": missing})
    for extra in sorted(set(declarations) - expected_paths):
        errors.append({"path": extra, "error": "declaration outside required corpus scope"})
    inv_errors = inv.get("errors", [])
    return {"schema": "novel-corpus-coverage.v1", "books": books,
            "total_books": len(books), "complete_books": sum(b["declared_complete"] for b in books),
            "required_files": sum(b["required_files"] for b in books),
            "matched_files": sum(b["matched_files"] for b in books),
            "complete": bool(books) and not errors and not inv_errors and all(b["declared_complete"] for b in books),
            "errors": errors, "inventory_errors": inv_errors,
            "meaning": "Source hashes validate declared coverage, not literary understanding or actual reading."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("root", type=Path)
    inv.add_argument("--output", type=Path, required=True)
    cov = sub.add_parser("coverage")
    cov.add_argument("inventory", type=Path)
    cov.add_argument("--ledger", type=Path, nargs="+", required=True)
    cov.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            root = args.root.resolve(strict=True)
            if args.output.resolve().is_relative_to(root):
                raise ValueError("output must be outside the source corpus")
            result = inventory(root)
        else:
            source_inv = json.loads(args.inventory.read_text(encoding="utf-8"))
            if args.output.resolve().is_relative_to(Path(source_inv["root"]).resolve()):
                raise ValueError("output must be outside the source corpus")
            if args.output.resolve() in {args.inventory.resolve(), *(p.resolve() for p in args.ledger)}:
                raise ValueError("output must not overwrite inventory or reading ledgers")
            result = coverage(source_inv, args.ledger)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: result[k] for k in ("counts", "total_books", "complete_books", "matched_files", "required_files", "complete") if k in result}, ensure_ascii=False))
        if result.get("errors"):
            print(f"{len(result['errors'])} errors; see {args.output}", file=sys.stderr)
        return 1 if result.get("errors") or result.get("complete") is False else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
