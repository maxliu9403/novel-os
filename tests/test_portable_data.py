import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tarfile

import pytest


spec = importlib.util.spec_from_file_location("portable_data", Path(__file__).parents[1] / "scripts/portable_data.py")
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


def test_roundtrip_keeps_private_versions_and_committed_wal(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "empty").mkdir()
    content = '{"active_version":7,"versions":["旧版","当前用户全文"]}'
    (source / "knowledge.json").write_text(content)
    (source / "studio_settings.secrets.json").write_text('{"key":"private-test-value"}')
    db = sqlite3.connect(source / "novel_os.db")
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE projects (name TEXT)")
        db.execute("INSERT INTO projects VALUES ('saved chapter')")
        db.commit()
        archive = tmp_path / "data.tar.gz"
        manifest = transfer.snapshot(source, archive)
        assert "novel_os.db-wal" not in manifest["files"]
        target = tmp_path / "target"
        transfer.restore(archive, target)
        assert (target / "knowledge.json").read_text() == content
        assert (target / "empty").is_dir()
        assert (target / "studio_settings.secrets.json").stat().st_mode & 0o777 == 0o600
        with sqlite3.connect(target / "novel_os.db") as restored:
            assert restored.execute("SELECT name FROM projects").fetchall() == [("saved chapter",)]
            assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        db.close()


def test_changed_source_never_publishes_mixed_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "state.json").write_text("before")
    copy = transfer.copy_tree
    def changing(src, dst):
        result = copy(src, dst)
        (Path(src) / "state.json").write_text("updated while copying")
        return result
    monkeypatch.setattr(transfer, "copy_tree", changing)
    archive = tmp_path / "snapshot.tar.gz"
    with pytest.raises(ValueError, match="Source changed"):
        transfer.snapshot(source, archive)
    assert not archive.exists()


def test_no_overwrite_or_recursive_export(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="outside"):
        transfer.snapshot(source, source / "nested/archive.tar.gz")
    archive = tmp_path / "data.tar.gz"
    transfer.snapshot(source, archive)
    with pytest.raises(ValueError, match="must not exist"):
        transfer.snapshot(source, archive)
    with pytest.raises(ValueError, match="must not exist"):
        transfer.restore(archive, source)


def test_source_symlink_is_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "secret").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="links"):
        transfer.snapshot(source, tmp_path / "data.tar.gz")


@pytest.mark.parametrize("bad", ["traversal", "symlink", "hash", "extra", "duplicate"])
def test_bad_archive_leaves_no_restored_directory(tmp_path, bad):
    archive = tmp_path / "bad.tar.gz"
    name = "../escape" if bad == "traversal" else "record.json"
    manifest = {"schema": transfer.SCHEMA, "files": {name: {"size": 2, "sha256": "bad"}}, "directories": []}
    with tarfile.open(archive, "w:gz") as tar:
        data = json.dumps(manifest).encode()
        header = tarfile.TarInfo("manifest.json")
        header.size = len(data)
        tar.addfile(header, io.BytesIO(data))
        item = tarfile.TarInfo("data/" + name)
        item.size = 2
        if bad == "symlink":
            item.type, item.linkname = tarfile.SYMTYPE, "../../escape"
            tar.addfile(item)
        else:
            tar.addfile(item, io.BytesIO(b"{}"))
        if bad == "extra":
            tar.addfile(tarfile.TarInfo("extra"), io.BytesIO())
        if bad == "duplicate":
            tar.addfile(item, io.BytesIO(b"{}"))
    destination = tmp_path / "restored"
    with pytest.raises(ValueError):
        transfer.restore(archive, destination)
    assert not destination.exists()
    assert not (tmp_path / "escape").exists()


def test_published_skills_include_corpus_study():
    from scripts.sync_skills import PUBLISHED_SKILLS
    assert set(PUBLISHED_SKILLS) == {"novel-brainstorm-workshop", "high-retention-web-novel", "novel-cover-studio", "novel-corpus-study"}
