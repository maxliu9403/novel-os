"""The story cache must never retain a mixed in-place file write."""
from pathlib import Path

import pytest

from core import safe_project_io


def test_snapshot_retries_when_the_source_changes_during_read(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.md"
    source.write_bytes(b"old source")
    original_read = safe_project_io.os.read
    changed = False

    def read_and_change(descriptor, size):
        nonlocal changed
        chunk = original_read(descriptor, size)
        if chunk and not changed:
            changed = True
            source.write_bytes(b"complete replacement source")
        return chunk

    monkeypatch.setattr(safe_project_io.os, "read", read_and_change)
    result = safe_project_io.read_project_snapshot(tmp_path, ("source.md",))

    assert result.data == b"complete replacement source"
    assert result.modified_ns == source.stat().st_mtime_ns


def test_snapshot_stops_after_three_changing_reads(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.md"
    source.write_bytes(b"source")
    original_read = safe_project_io.os.read
    attempts = 0

    def read_and_change_at_eof(descriptor, size):
        nonlocal attempts
        chunk = original_read(descriptor, size)
        if not chunk:
            attempts += 1
            with source.open("ab") as stream:
                stream.write(b"!")
        return chunk

    monkeypatch.setattr(safe_project_io.os, "read", read_and_change_at_eof)
    with pytest.raises(safe_project_io.ProjectPathError, match="changed repeatedly"):
        safe_project_io.read_project_snapshot(tmp_path, ("source.md",))
    assert attempts == 3


def test_snapshot_rejects_symlinked_parent_and_missing_is_explicit(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.md").write_bytes(b"private")
    (project / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(safe_project_io.ProjectPathError):
        safe_project_io.read_project_snapshot(project, ("linked", "source.md"))
    assert safe_project_io.read_project_snapshot(project, ("missing",), missing_ok=True) is None
