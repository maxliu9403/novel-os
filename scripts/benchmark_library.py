#!/usr/bin/env python3
"""Reproducible HTTP read-model benchmark; never opens a live project root.

Run: venv/bin/python scripts/benchmark_library.py --samples 60 --output report.json
Synthetic persisted summaries represent 100 chapters each. Real manuscript
indexing/incremental read counts are separately covered by indexer regressions.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))


def percentile(values, fraction):
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]


def run(counts, samples):
    with tempfile.TemporaryDirectory(prefix="novel-library-benchmark-") as directory:
        work = Path(directory)
        # api.__init__ creates its default app, so isolation precedes imports.
        os.environ.update(NOVEL_OS_DB=f"sqlite:///{work / 'default.sqlite'}",
            NOVEL_OS_LIBRARY_DB=f"sqlite:///{work / 'default.sqlite'}",
            NOVEL_OS_SETTINGS_PATH=str(work / "settings.json"),
            NOVEL_OS_PROJECTS_DIR=str(work / "empty"))
        from fastapi.testclient import TestClient
        from api.main import create_app
        from api.services import ProjectService
        from api.jobs import JobRunner
        from api import library_routes

        results = []
        for count in counts:
            base = work / str(count)
            base.mkdir()
            books = base / "books"
            books.mkdir()
            os.environ["NOVEL_OS_LIBRARY_DB"] = f"sqlite:///{base / 'catalog.sqlite'}"
            app = create_app(projects_root=books, db_url=f"sqlite:///{base / 'catalog.sqlite'}")
            catalog, indexer = app.state.library_runtime.for_service(ProjectService(books))
            for i in range(count):
                project_id = f"book-{i:04}"
                token = catalog.discover(project_id)
                catalog.publish(project_id, {
                    "id": project_id, "title": f"Performance Story {i:04}", "author": "Fixture",
                    "genre": "Romance", "language": "英文", "chapter_count": 100,
                    "drafted_count": 100, "word_count": 250_000, "status": "completed",
                    "task_started_at": "2026-11-01T00:00:00+00:00",
                    "cover": {"status": "selected", "thumbnail_url": f"/fixture/{i}/256.webp"},
                }, token["epoch"], token["generation"])
            catalog.mark_scan_complete()
            jobs = JobRunner()
            client = TestClient(app)  # Do not start the background source adapter.
            denied = 0

            def forbidden(*args, **kwargs):
                nonlocal denied
                denied += 1
                raise AssertionError("A library request attempted a canonical source read")

            def measure(mode):
                values, bytes_received = [], 0
                for _ in range(samples):
                    started = time.perf_counter()
                    response = client.get("/api/library", params={"limit": 20})
                    elapsed = (time.perf_counter() - started) * 1000
                    if response.status_code != 200:
                        raise AssertionError(response.text)
                    assert len(response.json()["items"]) == min(count, 20)
                    bytes_received = len(response.content)
                    values.append(elapsed)
                return {"mode": mode, "books": count, "chapters_per_book": 100,
                    "samples": samples, "p50_ms": round(statistics.median(values), 3),
                    "p95_ms": round(percentile(values, .95), 3), "max_ms": round(max(values), 3),
                    "response_bytes": bytes_received, "canonical_reads": denied}

            stop = threading.Event()
            errors = []
            def updates():
                iteration = 0
                while not stop.is_set():
                    try:
                        project_id = f"book-{iteration % count:04}"
                        record = catalog.invalidate(project_id, domains=("task",))
                        catalog.publish(project_id, record["summary"], record["epoch"], record["generation"])
                        # CPU pressure akin to parsing a large checkpoint. This
                        # is a synthetic load, not a claim about live creation.
                        checkpoint = json.dumps({"chapters": [{"text": "x" * 2048}] * 100})
                        json.loads(checkpoint)
                        iteration += 1
                        stop.wait(.01)
                    except Exception as exc:
                        errors.append(str(exc))
                        return

            with patch.object(library_routes, "runner", jobs), \
                 patch.object(ProjectService, "_load", forbidden), \
                 patch.object(indexer, "build", forbidden), \
                 patch.object(Path, "read_bytes", forbidden), \
                 patch.object(Path, "read_text", forbidden):
                results.append(measure("idle"))
                worker = threading.Thread(target=updates)
                worker.start()
                try:
                    results.append(measure("concurrent_summary_writes_and_checkpoint_parsing"))
                finally:
                    stop.set()
                    worker.join(5)
            if errors:
                raise AssertionError(errors)
            client.close()
            app.state.library_runtime.stop()
        return {"kind": "isolated-synthetic-http-benchmark", "results": results,
                "passed": all(r["canonical_reads"] == 0 and r["p95_ms"] <= (200 if r["mode"] == "idle" else 500) for r in results)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=60)
    parser.add_argument("--sizes", type=int, nargs="+", default=[21, 200, 1000])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 5 or any(size < 1 for size in args.sizes):
        parser.error("Use at least five samples and positive library sizes")
    result = run(args.sizes, args.samples)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    print(rendered)
    raise SystemExit(0 if result["passed"] else 1)
