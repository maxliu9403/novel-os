"""Checkpoint round trips touch only the allowlisted cover source files."""
from pathlib import Path
import json
import zipfile

import pytest

from scripts.cover_rollback import (
    COVER_FILES, GUARD_FILES, CheckpointError, create_checkpoint, inspect_checkpoint, restore_checkpoint,
)


def fixture_repo(tmp_path):
    repo = tmp_path / 'repo'
    before = {name: b'# running source\n' for name in COVER_FILES}
    after = {name: b'# candidate source\n' for name in COVER_FILES}
    before['core/cover_prompt_compiler.py'] = b'COMPILER_VERSION = "cover-compiler.v11"\n'
    before['core/cover_render_policy.py'] = b'PHOTOGRAPHIC_PROFILE = "cover-profiles.v6"\n'
    after['core/cover_prompt_compiler.py'] = b'COMPILER_VERSION = "cover-compiler.v12"\n'
    after['core/cover_render_policy.py'] = b'PHOTOGRAPHIC_PROFILE = "cover-profiles.v7"\n'
    for name, data in {**after, **{name: b'# shared contract\n' for name in GUARD_FILES}}.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    novel = repo / 'docker-data/projects/book/outputs/book.md'
    novel.parent.mkdir(parents=True)
    novel.write_text('A completed novel.')
    checkpoint = create_checkpoint(repo, before, source={'container_id': 'fixture', 'image_id': 'sha256:fixture'})
    return repo, before, after, checkpoint, novel


def test_checkpoint_round_trip_is_reversible_and_preserves_other_work(tmp_path):
    repo, before, after, checkpoint, novel = fixture_repo(tmp_path)
    status = inspect_checkpoint(repo, checkpoint)
    assert status['active_source'] == 'after'
    assert status['before']['compiler'] == 'cover-compiler.v11'
    preview = restore_checkpoint(repo, checkpoint, to='before')
    assert preview['applied'] is False
    assert {p: (repo / p).read_bytes() for p in COVER_FILES} == after
    restored = restore_checkpoint(repo, checkpoint, to='before', apply=True)
    assert restored['active_source'] == 'before'
    assert {p: (repo / p).read_bytes() for p in COVER_FILES} == before
    restore_checkpoint(repo, checkpoint, to='after', apply=True)
    assert {p: (repo / p).read_bytes() for p in COVER_FILES} == after
    assert novel.read_text() == 'A completed novel.'
    assert (repo / 'core/delivery_package.py').read_bytes() == b'# shared contract\n'
    with zipfile.ZipFile(checkpoint) as archive:
        assert not any('docker-data' in name or 'auth' in name or '.env' in name for name in archive.namelist())


@pytest.mark.parametrize('target', [COVER_FILES[0], GUARD_FILES[0]])
def test_restore_stops_before_any_write_when_later_edits_exist(tmp_path, target):
    repo, before, after, checkpoint, _ = fixture_repo(tmp_path)
    (repo / target).write_bytes(b'# important later work\n')
    files_before = {name: (repo / name).read_bytes() for name in (*COVER_FILES, *GUARD_FILES)}
    with pytest.raises(CheckpointError, match='Later edits'):
        restore_checkpoint(repo, checkpoint, to='before', apply=True)
    assert {name: (repo / name).read_bytes() for name in files_before} == files_before


def rewrite_archive(checkpoint, transform):
    with zipfile.ZipFile(checkpoint) as z:
        entries = {name: z.read(name) for name in z.namelist()}
    transform(entries)
    with zipfile.ZipFile(checkpoint, 'w') as z:
        for name, data in entries.items():
            z.writestr(name, data)


@pytest.mark.parametrize('kind', ['source', 'manifest', 'traversal', 'duplicate'])
def test_corrupt_or_unexpected_archive_is_rejected_before_restore(tmp_path, kind):
    repo, _, after, checkpoint, _ = fixture_repo(tmp_path)
    if kind == 'source':
        rewrite_archive(checkpoint, lambda e: e.update({'before/' + COVER_FILES[0]: b'# bad\n'}))
    elif kind == 'manifest':
        def corrupt(entries):
            manifest = json.loads(entries['manifest.json'])
            manifest['guards'][GUARD_FILES[0]] = 'bad'
            entries['manifest.json'] = json.dumps(manifest).encode()
        rewrite_archive(checkpoint, corrupt)
    elif kind == 'traversal':
        rewrite_archive(checkpoint, lambda e: e.update({'../outside.py': b'bad'}))
    else:
        with zipfile.ZipFile(checkpoint, 'a') as z, pytest.warns(UserWarning):
            z.writestr('before/' + COVER_FILES[0], b'# duplicate\n')
    with pytest.raises(CheckpointError):
        restore_checkpoint(repo, checkpoint, to='before', apply=True)
    assert {name: (repo / name).read_bytes() for name in COVER_FILES} == after
    assert not (tmp_path / 'outside.py').exists()


def test_symlinked_source_is_not_followed(tmp_path):
    repo, _, _, checkpoint, _ = fixture_repo(tmp_path)
    path = repo / COVER_FILES[0]
    external = tmp_path / 'external.py'
    external.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(CheckpointError, match='Symlinked'):
        restore_checkpoint(repo, checkpoint, to='before', apply=True)
    assert external.read_bytes() == b'# candidate source\n'


def test_interrupted_write_restores_originals_and_clears_lock(tmp_path, monkeypatch):
    import scripts.cover_rollback as rollback
    repo, _, after, checkpoint, _ = fixture_repo(tmp_path)
    atomic = rollback._atomic_write
    calls = []
    def fail_once(path, data):
        calls.append(path)
        if len(calls) == 2:
            raise OSError('disk write failed')
        return atomic(path, data)
    monkeypatch.setattr(rollback, '_atomic_write', fail_once)
    with pytest.raises(CheckpointError, match='Restore interrupted'):
        restore_checkpoint(repo, checkpoint, to='before', apply=True)
    assert {name: (repo / name).read_bytes() for name in COVER_FILES} == after
    assert not (repo / '.cover-checkpoints/restore.lock').exists()


def test_interrupted_mixed_known_state_can_be_recovered(tmp_path):
    repo, before, _, checkpoint, _ = fixture_repo(tmp_path)
    (repo / COVER_FILES[0]).write_bytes(before[COVER_FILES[0]])
    assert inspect_checkpoint(repo, checkpoint)['active_source'] == 'mixed'
    restore_checkpoint(repo, checkpoint, to='before', apply=True)
    assert inspect_checkpoint(repo, checkpoint)['active_source'] == 'before'


def test_restore_lock_prevents_overlapping_switches(tmp_path):
    repo, _, after, checkpoint, _ = fixture_repo(tmp_path)
    (repo / '.cover-checkpoints/restore.lock').write_text('another switch')
    with pytest.raises(CheckpointError, match='restore lock'):
        restore_checkpoint(repo, checkpoint, to='before', apply=True)
    assert {name: (repo / name).read_bytes() for name in COVER_FILES} == after
