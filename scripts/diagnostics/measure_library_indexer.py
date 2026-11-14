"""Reproducible isolated incremental-index benchmark; never opens user books.

Run: venv/bin/python scripts/diagnostics/measure_library_indexer.py
All source files, database and thumbnails are synthetic temporary fixtures.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import statistics
import sys
from tempfile import TemporaryDirectory
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chapters", type=int, default=120)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.chapters <= 1000 or not 2 <= args.samples <= 100:
        parser.error("chapters must be 1–1000 and samples 2–100")
    repository = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository))
    sys.path.insert(0, str(repository / "core"))
    with TemporaryDirectory(prefix="novel-indexer-benchmark-") as temporary:
        base = Path(temporary)
        os.environ["NOVEL_OS_DB"] = f"sqlite:///{base / 'bootstrap.sqlite'}"
        os.environ["NOVEL_OS_LIBRARY_DB"] = os.environ["NOVEL_OS_DB"]
        os.environ["NOVEL_OS_PROJECTS_DIR"] = str(base / "projects")
        from api.library_catalog import LibraryCatalog
        from api.library_indexer import LibraryIndexer, SourceSnapshot
        project = base / "projects/book"
        state = project / "outputs/state/story_state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({
            "metadata": {"title": "Synthetic book", "language": "English"},
            "chapters": {str(i): {"number": i, "status": "planned", "word_count": 0}
                         for i in range(1, args.chapters + 1)},
        }))
        manuscript = project / "outputs/manuscript"
        manuscript.mkdir()
        for number in range(1, args.chapters + 1):
            (manuscript / f"chapter_{number:03d}_draft.md").write_text("One complete preserved chapter. " * 1000)
        def source_hashes():
            return {str(p.relative_to(project)): sha256(p.read_bytes()).hexdigest()
                    for p in project.rglob("*") if p.is_file()}
        before = source_hashes()
        catalog = LibraryCatalog(base / "catalog.sqlite", project.parent)
        indexer = LibraryIndexer(catalog, project.parent, base / "thumbs")
        started = time.perf_counter()
        indexer.scan_once()
        indexer.process_once()
        cold_ms = (time.perf_counter() - started) * 1000
        reads = []
        original = SourceSnapshot.read
        def observed(self, relative, **kwargs):
            if relative.startswith(("outputs/manuscript/", "outputs/artifacts/sha256/")):
                reads.append(relative)
            return original(self, relative, **kwargs)
        SourceSnapshot.read = observed
        timings = []
        for _ in range(args.samples):
            catalog.invalidate("book", domains=("task",))
            started = time.perf_counter()
            indexer.process_once()
            timings.append((time.perf_counter() - started) * 1000)
        unchanged_reads = len(reads)
        unchanged_sources = source_hashes() == before
        changed = manuscript / f"chapter_{args.chapters:03d}_draft.md"
        changed.write_text("A newly saved paragraph. " * 1000)
        expected = source_hashes()
        started = time.perf_counter()
        indexer.scan_once()
        indexer.process_once()
        changed_ms = (time.perf_counter() - started) * 1000
        result = {
            "synthetic_only": True, "chapters": args.chapters, "samples": args.samples,
            "cold_backfill_ms": round(cold_ms, 3),
            "task_only_p50_ms": round(statistics.median(timings), 3),
            "task_only_p95_ms": round(sorted(timings)[max(0, int(len(timings) * .95) - 1)], 3),
            "task_only_manuscript_reads": unchanged_reads,
            "one_chapter_reconcile_ms": round(changed_ms, 3),
            "one_chapter_manuscript_reads": len(reads) - unchanged_reads,
            "source_hashes_preserved": unchanged_sources and source_hashes() == expected,
            "passed": unchanged_reads == 0 and len(reads) == 1 and unchanged_sources and source_hashes() == expected,
        }
        output = json.dumps(result, ensure_ascii=False, indent=2)
        print(output)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
        return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
