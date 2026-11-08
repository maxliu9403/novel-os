"""Coverage must not certify missing, stale, or out-of-scope source material."""
import json
from pathlib import Path
import tempfile
import unittest

from corpus_audit import coverage, inventory


class CorpusCoverageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "corpus"
        self.book = self.root / "Example__1"
        (self.book / "chapters").mkdir(parents=True)
        self.write("synopsis.md", "A person makes a difficult choice.")
        self.write("chapters/001.md", "A choice with consequences.")
        self.write("metadata.json", json.dumps({
            "book_id": "1", "novel_name": "Example", "genres": ["Romance"],
            "tags": ["Independent"], "free_chapter_count": 1,
        }))
        self.write("chapters.json", json.dumps([
            {"index": 1, "chapter_id": "c1", "path": "chapters/001.md"}
        ]))

    def write(self, relative, text):
        (self.book / relative).write_text(text, encoding="utf-8")

    def ledger(self, snapshot, omitted=()):
        path = self.base / "ledger.json"
        path.write_text(json.dumps({
            "schema": "novel-corpus-reading.v1", "files": [
                {"book_id": b["book_id"], "path": f["path"], "sha256": f["sha256"],
                 "fully_read": True, "observation": "Synthetic test declaration."}
                for b in snapshot["books"] for f in b["files"]
                if f["role"] not in {"metadata", "chapter_manifest"}
                and f["relative_path"] not in omitted
            ]
        }), encoding="utf-8")
        return path

    def test_complete_declaration_and_missing_read(self):
        snapshot = inventory(self.root)
        self.assertTrue(coverage(snapshot, [self.ledger(snapshot)])["complete"])
        report = coverage(snapshot, [self.ledger(snapshot, {"chapters/001.md"})])
        self.assertFalse(report["complete"])
        self.assertEqual(report["matched_files"], 1)

    def test_unlisted_markdown_requires_its_own_reading(self):
        self.write("chapters/002.md", "Previously unlisted material.")
        snapshot = inventory(self.root)
        report = coverage(snapshot, [self.ledger(snapshot, {"chapters/002.md"})])
        self.assertFalse(report["complete"])
        self.assertEqual(report["required_files"], 3)
        self.assertTrue(coverage(snapshot, [self.ledger(snapshot)])["complete"])

    def test_new_chapter_or_book_invalidates_current_scope(self):
        snapshot = inventory(self.root)
        ledger = self.ledger(snapshot)
        self.write("chapters/002.md", "New material after inventory.")
        self.assertFalse(coverage(snapshot, [ledger])["complete"])
        (self.book / "chapters/002.md").unlink()
        (self.root / "NewBook").mkdir()
        self.assertFalse(coverage(snapshot, [ledger])["complete"])

    def test_manifest_metadata_and_text_drift_are_detected(self):
        for relative in ["chapters.json", "metadata.json", "chapters/001.md", "synopsis.md"]:
            with self.subTest(relative=relative):
                snapshot = inventory(self.root)
                ledger = self.ledger(snapshot)
                original = (self.book / relative).read_text()
                self.write(relative, original + "\n")
                self.assertFalse(coverage(snapshot, [ledger])["complete"])
                self.write(relative, original)

    def test_escaped_and_symlink_sources_are_rejected(self):
        outside = self.base / "outside.md"
        outside.write_text("Outside the book.")
        self.write("chapters.json", json.dumps([
            {"index": 1, "chapter_id": "c1", "path": "../../outside.md"}
        ]))
        self.assertTrue(inventory(self.root)["errors"])
        self.write("chapters.json", json.dumps([
            {"index": 1, "chapter_id": "c1", "path": "chapters/001.md"}
        ]))
        (self.book / "chapters/001.md").unlink()
        (self.book / "chapters/001.md").symlink_to(outside)
        self.assertTrue(inventory(self.root)["errors"])


if __name__ == "__main__":
    unittest.main()
