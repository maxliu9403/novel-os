"""Measure bounded external-change discovery using temporary synthetic books.

The known fixture summaries are seeded directly to isolate discovery cost; one
target book is fully indexed before its controlled external edit. This is not a
claim about cold backfilling every manuscript in a real 200-book collection.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--books", type=int, default=200)
    parser.add_argument("--chapters", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.books <= 200 or not 1 <= args.chapters <= 200:
        parser.error("This bounded host benchmark supports at most 200 books/chapters")
    repository = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository))
    sys.path.insert(0, str(repository / "core"))
    with TemporaryDirectory(prefix="novel-library-reconcile-") as temporary:
        base = Path(temporary)
        root = base / "projects"
        os.environ["NOVEL_OS_DB"] = f"sqlite:///{base / 'bootstrap.sqlite'}"
        os.environ["NOVEL_OS_LIBRARY_DB"] = os.environ["NOVEL_OS_DB"]
        os.environ["NOVEL_OS_PROJECTS_DIR"] = str(root)
        from api.library_catalog import LibraryCatalog
        from api.library_indexer import LibraryIndexer, SourceSnapshot
        for number in range(args.books):
            project = root / f"book-{number:04d}"
            state = project / "outputs/state/story_state.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({"metadata": {"title": project.name, "language": "English"},
                "chapters": {str(i): {"number": i, "status": "planned", "word_count": 0}
                             for i in range(1, args.chapters + 1)}}))
            manuscript = project / "outputs/manuscript"
            manuscript.mkdir()
            for chapter in range(1, args.chapters + 1):
                (manuscript / f"chapter_{chapter:03d}_draft.md").write_text("word")
        catalog = LibraryCatalog(base / "catalog.sqlite", root)
        indexer = LibraryIndexer(catalog, root, base / "thumbs", scan_budget=32)
        original = SourceSnapshot.read
        text_reads = []
        def observed(self, relative, **kwargs):
            if relative.startswith(("outputs/manuscript/", "outputs/artifacts/sha256/")):
                text_reads.append(relative)
            return original(self, relative, **kwargs)
        SourceSnapshot.read = observed
        started = time.perf_counter()
        indexer.scan_once()
        while indexer._scan_at < len(indexer._scan_ids):
            indexer.scan_once()
        scan_ms = (time.perf_counter() - started) * 1000
        discovery_reads = len(text_reads)
        target = f"book-{args.books - 1:04d}"
        # Fully index target; seed the other known synthetic counts without
        # claiming that this measures full-corpus initial manuscript backfill.
        while tokens := catalog.pending(limit=100):
            for token in tokens:
                if token["project_id"] == target:
                    summary, fingerprint = indexer.build(target, token)
                else:
                    summary = {"id": token["project_id"], "title": token["project_id"], "genre": "",
                               "chapter_count": args.chapters, "drafted_count": args.chapters,
                               "word_count": args.chapters, "status": "in_progress"}
                    fingerprint = token["fingerprint"]
                catalog.publish(token["project_id"], summary, token["epoch"], token["generation"], fingerprint)
        text_reads.clear()
        source = root / target / f"outputs/manuscript/chapter_{args.chapters:03d}_draft.md"
        source.write_text("four newly saved words")
        changed_at = time.perf_counter()
        worker = LibraryIndexer(catalog, root, base / "thumbs", scan_budget=32)
        worker.start()
        observed_at = None
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                row = catalog.get_record(target)
                if row and row.get("freshness") == "fresh" and row["summary"].get("word_count") == args.chapters + 3:
                    observed_at = time.perf_counter()
                    break
                time.sleep(.02)
        finally:
            worker.stop(timeout=10)
        result = {
            "synthetic_only": True, "books": args.books, "chapters_per_book": args.chapters,
            "known_fixture_summaries_seeded": True,
            "metadata_discovery_full_cycle_ms": round(scan_ms, 3),
            "discovery_manuscript_reads": discovery_reads,
            "external_edit_to_visible_ms": round((observed_at - changed_at) * 1000, 3) if observed_at else None,
            "external_edit_manuscript_reads": len(text_reads),
            "passed": discovery_reads == 0 and observed_at is not None and len(text_reads) == 1,
        }
        output = json.dumps(result, ensure_ascii=False, indent=2)
        print(output)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
        return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
