#!/usr/bin/env python3
"""Synchronize the release's three Skills during the normal deployment flow.

This internal helper uses only the standard library. It neither invokes Codex
nor starts any service. Existing installations are backed up before replacement.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from uuid import uuid4


PUBLISHED_SKILLS = (
    "novel-brainstorm-workshop",
    "high-retention-web-novel",
    "novel-cover-studio",
)
LOCK_NAME = ".novel-os-skill-sync.lock"


class SkillSyncError(Exception):
    """A synchronization was refused or could not be completed safely."""


def _is_junk(name: str) -> bool:
    return name in {"__pycache__", ".DS_Store"} or name.endswith(".pyc")


def _no_symlink_components(path: Path) -> None:
    for part in (*reversed(path.parents), path):
        if part.is_symlink():
            raise SkillSyncError(f"Refusing symbolic link in Skill path: {part}")


def _tree_manifest(root: Path, *, ignore_junk: bool = False) -> dict:
    """Compare bytes and permissions, not timestamps; never follow symlinks."""
    _no_symlink_components(root)
    if not root.is_dir():
        raise SkillSyncError(f"Skill directory is missing or not a directory: {root}")
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in dirs + files:
            path = parent / name
            if path.is_symlink():
                raise SkillSyncError(f"Refusing symbolic link inside Skill: {path}")
            mode = path.stat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise SkillSyncError(f"Refusing non-regular Skill entry: {path}")
            if ignore_junk and _is_junk(name):
                continue
            relative = path.relative_to(root).as_posix()
            permissions = stat.S_IMODE(mode)
            if stat.S_ISDIR(mode):
                result[relative] = ("directory", permissions)
            else:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                result[relative] = ("file", permissions, digest.hexdigest())
        if ignore_junk:
            dirs[:] = [name for name in dirs if not _is_junk(name)]
    return result


def _validate_paths(source: Path, codex_home: Path) -> None:
    _no_symlink_components(source)
    _no_symlink_components(codex_home)
    targets = codex_home / "skills"
    _no_symlink_components(targets)
    _no_symlink_components(codex_home / "skill-backups")
    if (source == targets or source in targets.parents or targets in source.parents):
        raise SkillSyncError("Source Skills and installed Skills overlap; refusing to replace repository files")
    for directory in (source, codex_home, targets, codex_home / "skill-backups"):
        if directory.exists() and not directory.is_dir():
            raise SkillSyncError(f"Expected a directory: {directory}")


def _source_manifests(source: Path) -> dict:
    manifests = {}
    for name in PUBLISHED_SKILLS:
        skill = source / name
        manifests[name] = _tree_manifest(skill, ignore_junk=True)
        if not (skill / "SKILL.md").is_file():
            raise SkillSyncError(f"Release Skill is missing SKILL.md: {skill}")
    return manifests


def _changed_skills(manifests: dict, targets: Path) -> list[str]:
    changed = []
    for name in PUBLISHED_SKILLS:
        target = targets / name
        _no_symlink_components(target)
        if not target.exists() or _tree_manifest(target) != manifests[name]:
            changed.append(name)
    return changed


@contextmanager
def _directory_lock(codex_home: Path):
    lock = codex_home / LOCK_NAME
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise SkillSyncError(
            f"Another Skill synchronization may be active: {lock}. "
            "If a previous process stopped unexpectedly, verify it has ended before removing this lock."
        ) from exc
    try:
        yield
    finally:
        lock.rmdir()


def _copy_release(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, symlinks=True,
                    ignore=lambda directory, names: [name for name in names if _is_junk(name)])


def sync_skills(source: Path, codex_home: Path) -> dict:
    source = Path(os.path.abspath(source))
    codex_home = Path(os.path.abspath(codex_home))
    _validate_paths(source, codex_home)
    manifests = _source_manifests(source)  # Validate every source before any write.
    targets = codex_home / "skills"
    if not _changed_skills(manifests, targets):
        return {"skills": [{"name": name, "status": "unchanged"} for name in PUBLISHED_SKILLS],
                "backup_root": None}

    codex_home.mkdir(parents=True, exist_ok=True)
    with _directory_lock(codex_home):
        # A concurrent normal update might have completed before we acquired the lock.
        _validate_paths(source, codex_home)
        manifests = _source_manifests(source)
        changed = _changed_skills(manifests, targets)
        if not changed:
            return {"skills": [{"name": name, "status": "unchanged"} for name in PUBLISHED_SKILLS],
                    "backup_root": None}
        targets.mkdir(exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".novel-os-skills-", dir=targets))
        backup_root = None
        operations = []
        preserve_stage = False
        existing = {name: (targets / name).exists() for name in changed}
        try:
            new = stage / "new"
            previous = stage / "previous"
            new.mkdir()
            previous.mkdir()
            for name in changed:
                _copy_release(source / name, new / name)
                if _tree_manifest(new / name) != manifests[name]:
                    raise SkillSyncError(f"Source changed while staging {name}; existing Skills were retained")

            if any(existing.values()):
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
                backup_root = codex_home / "skill-backups" / f"{stamp}-{uuid4().hex[:8]}"
                backup_root.mkdir(parents=True)
                for name in changed:
                    if existing[name]:
                        before = _tree_manifest(targets / name)
                        # Back up ALL old files, including personal files and caches.
                        shutil.copytree(targets / name, backup_root / name, symlinks=True)
                        if _tree_manifest(backup_root / name) != before:
                            raise SkillSyncError(f"Could not verify complete backup for {name}")

            for name in changed:
                target = targets / name
                operation = {"name": name, "installed": False, "moved_old": False}
                operations.append(operation)
                if existing[name]:
                    _tree_manifest(target)  # Refuse links introduced since preflight.
                    os.replace(target, previous / name)
                    operation["moved_old"] = True
                elif target.exists() or target.is_symlink():
                    raise SkillSyncError(f"Installation appeared during synchronization: {target}")
                os.replace(new / name, target)
                operation["installed"] = True
        except BaseException as exc:
            # Also restore on Ctrl-C or an unexpected error: the stage may
            # already contain the only active copy of a previous installation.
            rollback_errors = []
            for operation in reversed(operations):
                target = targets / operation["name"]
                try:
                    if operation["installed"]:
                        shutil.rmtree(target)
                    if operation["moved_old"]:
                        os.replace(previous / operation["name"], target)
                except BaseException as rollback_error:
                    rollback_errors.append(str(rollback_error))
            if rollback_errors:
                preserve_stage = True
                raise SkillSyncError(
                    f"Skill synchronization failed: {exc}; automatic rollback needs attention. "
                    f"Recovery copies: {stage}; backups: {backup_root}. "
                    + "; ".join(rollback_errors)
                ) from exc
            raise SkillSyncError(
                f"Skill synchronization failed; previous installations retained or restored: {exc}. "
                f"Backup location: {backup_root or 'none needed'}"
            ) from exc
        finally:
            if not preserve_stage:
                shutil.rmtree(stage)
        return {
            "skills": [{"name": name,
                        "status": ("updated" if existing[name] else "installed") if name in changed else "unchanged"}
                       for name in PUBLISHED_SKILLS],
            "backup_root": str(backup_root) if backup_root else None,
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = sync_skills(args.source, args.codex_home)
    except (OSError, SkillSyncError) as exc:
        print(f"Skill sync error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
