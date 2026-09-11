"""Read-only EPUB delivery contract probe; exit 1 when the MIME bug is present.

Usage: venv/bin/python scripts/diagnostics/repro_delivery_epub_mime.py PACKAGE.zip
Does not rebuild a project, alter a ZIP, or invoke the H5 application's validator.
"""

from __future__ import annotations

import copy
import hashlib
import json
from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from core.delivery_package import _manifest_entry
from core.h5_import import epub_index, object_digest


def main() -> int:
    path = Path(sys.argv[1]).resolve()
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("package-manifest.json"))
        sidecar = json.loads(archive.read(manifest["h5_import"]["path"]))
        epub_path = sidecar["epub"]["path"]
        entry = next(item for item in manifest["files"] if item["path"] == epub_path)
        epub_bytes = archive.read(epub_path)
        assert len(archive.namelist()) == len(set(archive.namelist()))
        for item in manifest["files"]:
            data = archive.read(item["path"])
            assert item["size"] == len(data), item["path"]
            assert item["sha256"] == hashlib.sha256(data).hexdigest(), item["path"]
        assert manifest["package_revision_sha256"] == object_digest(manifest["files"])
        index = epub_index(
            epub_bytes,
            json.loads(archive.read(manifest["serialization"]["path"])),
            json.loads(archive.read(manifest["classification"]["path"])),
        )
        assert sidecar["chapter_count"] == len(sidecar["chapters"]) == index["chapter_count"]
        assert sidecar["non_chapter_items"] == index["non_chapter_items"]
        assert manifest["book_id"] == sidecar["book_id"]
        for record, expected in zip(sidecar["chapters"], index["chapters"]):
            assert record["chapter_id"] == f"{sidecar['book_id']}:chapter:{record['number']}"
            assert {k: v for k, v in record.items() if k != "chapter_id"} == expected
        with ZipFile(BytesIO(epub_bytes)) as epub:
            internal_mime = epub.read("mimetype").decode("ascii")
            assert internal_mime == "application/epub+zip"

    cases = []
    with TemporaryDirectory(prefix="novel-epub-mime-") as directory:
        source = Path(directory) / "book.epub"
        source.write_bytes(epub_bytes)
        for name, guessed in (
            ("missing_system_mapping", None),
            ("wrong_system_mapping", "application/zip"),
            ("correct_system_mapping", "application/epub+zip"),
        ):
            with patch("core.delivery_package.mimetypes.guess_type", return_value=(guessed, None)):
                actual = _manifest_entry(source.parent, source, None)["media_type"]
            cases.append({"case": name, "media_type": actual,
                          "result": "PASS" if actual == "application/epub+zip" else "FAIL"})

    corrected = copy.deepcopy(manifest)
    next(item for item in corrected["files"] if item["path"] == epub_path)["media_type"] = "application/epub+zip"
    corrected_revision = object_digest(corrected["files"])
    mime_ok = entry["media_type"] == "application/epub+zip"
    print(json.dumps({
        "mode": "read_only_diagnosis_not_h5_end_to_end_validation",
        "zip": str(path), "zip_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "book_id": manifest["book_id"], "file_count": len(manifest["files"]),
        "file_integrity_and_epub_map": "PASS", "chapter_count": index["chapter_count"],
        "non_chapter_kinds": [item["kind"] for item in index["non_chapter_items"]],
        "epub_descriptor": entry, "epub_internal_mimetype": internal_mime,
        "manifest_epub_mime": "PASS" if mime_ok else "FAIL",
        "package_revision_sha256": manifest["package_revision_sha256"],
        "in_memory_mime_only_revision_sha256": corrected_revision,
        "packager_environment_cases": cases,
        "source_zip_modified": False,
    }, indent=2, ensure_ascii=False))
    return 0 if mime_ok and all(item["result"] == "PASS" for item in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
