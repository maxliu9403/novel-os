#!/usr/bin/env python3
"""Build tiny, model-free H5 protocol fixtures from the production exporter.

Run with the project's virtualenv. No project books or source novels are read.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from compile_book import gather  # noqa: E402
from compile_epub import render_epub  # noqa: E402
from styles import StyleSheet  # noqa: E402

from core.creative_types import CreativeTypeStore, TypeRef  # noqa: E402
from core.delivery_package import build_delivery_package  # noqa: E402
from core.h5_acceptance import build_acceptance_plan, inspect_package  # noqa: E402
from core.narrative_format import infer_narrative_format, serialization_payload  # noqa: E402
from core.publication_taxonomy import classification  # noqa: E402


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def build(destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="novelos-taxonomy-sample-") as temporary:
        root = Path(temporary)
        store = CreativeTypeStore(root / "knowledge")
        refs = []
        for identifier in ("genre.romance", "tag.betrayal"):
            record = store.get(identifier)
            version = record["versions"][-1]
            refs.append(
                TypeRef(
                    id=identifier,
                    version=version["version"],
                    source_sha256=version["sha256"],
                )
            )
        chosen = classification(store.snapshot(refs)["publication_taxonomy"], 2)
        public = chosen.to_dict()
        serialization = serialization_payload(
            infer_narrative_format(
                chosen, chapters=2, target_words=2000, explicit_length=True
            ),
            None,
            allow_legacy_defaults=True,
        )
        book = gather(
            title="A Different Choice",
            author="Fixture Author",
            genre="Romance",
            chapters=[
                {
                    "number": 1,
                    "title": "The promise",
                    "text": "She found his letter and chose to ask him herself.",
                },
                {
                    "number": 2,
                    "title": "The choice",
                    "text": "His answer changed what a promise meant to them.",
                },
            ],
            classification=public,
            serialization=serialization,
        )
        project = root / "project"
        deliverables = project / "outputs/deliverables"
        publication = project / "outputs/publication"
        deliverables.mkdir(parents=True)
        publication.mkdir(parents=True)
        (deliverables / "book.epub").write_bytes(render_epub(book, StyleSheet()))
        write_json(publication / "novel-classification.json", public)
        write_json(publication / "novel-serialization.json", serialization)
        package = build_delivery_package(project)
        good = destination / "valid-dynamic-taxonomy.zip"
        bad = destination / "invalid-classification-hash.zip"
        shutil.copyfile(package.archive_path, good)
        with ZipFile(good) as source, ZipFile(bad, "w", ZIP_DEFLATED) as target:
            for info in source.infolist():
                raw = source.read(info.filename)
                if info.filename.endswith("novel-classification.json"):
                    value = json.loads(raw)
                    value["taxonomy"]["genre"]["definition"] = (
                        "Changed without updating the manifest digest"
                    )
                    raw = json.dumps(value).encode()
                target.writestr(info, raw)
        inspect_package(good)
        try:
            inspect_package(bad)
        except ValueError as exc:
            error = getattr(exc, "code", None)
            if error != "file_digest":
                raise
        else:
            raise AssertionError("Broken fixture was accepted")
        plan = build_acceptance_plan(
            [
                {
                    "id": "dynamic_initial",
                    "action": "initial",
                    "package": str(good),
                    "free_trial_end": 1,
                },
                {
                    "id": "dynamic_repeat",
                    "action": "repeat",
                    "package": str(good),
                    "baseline": str(good),
                    "free_trial_end": 1,
                },
                {
                    "id": "invalid_hash",
                    "action": "reject_invalid",
                    "package": str(bad),
                    "baseline": str(good),
                    "free_trial_end": 1,
                    "expected_error": error,
                },
            ]
        )
        write_json(destination / "acceptance-plan.json", plan)
        write_json(destination / "classification.example.json", public)
        return {
            "valid_bytes": good.stat().st_size,
            "invalid_error": error,
            "cases": len(plan["cases"]),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs/examples/h5-import/dynamic-taxonomy"
    )
    args = parser.parse_args()
    print(json.dumps(build(args.output.resolve())))
