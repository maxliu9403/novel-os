"""Deployment Skill sync stays inside temporary homes; no real Codex writes."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/sync_skills.py"
SPEC = importlib.util.spec_from_file_location("novel_skill_sync", SCRIPT)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def release(tmp_path):
    source = tmp_path / "repo/skills"
    for name in sync.PUBLISHED_SKILLS:
        skill = source / name
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        (skill / "references/contract.md").write_text("release contract\n", encoding="utf-8")
    return source, tmp_path / "codex-home"


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes())
            for p in root.rglob("*") if p.is_file()}


def test_install_all_three_without_copying_junk_or_touching_other_skills(tmp_path):
    source, home = release(tmp_path)
    first = source / sync.PUBLISHED_SKILLS[0]
    (first / "__pycache__").mkdir()
    (first / "__pycache__/cache.pyc").write_bytes(b"cache")
    (first / ".DS_Store").write_bytes(b"finder")
    (first / "stray.pyc").write_bytes(b"cache")
    other = home / "skills/personal-skill"
    other.mkdir(parents=True)
    (other / "keep.md").write_text("personal", encoding="utf-8")
    before_other = snapshot(other)
    result = sync.sync_skills(source, home)
    assert [item["status"] for item in result["skills"]] == ["installed"] * 3
    assert result["backup_root"] is None
    assert snapshot(other) == before_other
    for name in sync.PUBLISHED_SKILLS:
        target = home / "skills" / name
        assert (target / "references/contract.md").read_text() == "release contract\n"
        assert not (target / "__pycache__").exists()
        assert not (target / ".DS_Store").exists()
        assert not (target / "stray.pyc").exists()


def test_identical_content_is_a_true_no_write_no_backup_operation(tmp_path, monkeypatch):
    source, home = release(tmp_path)
    sync.sync_skills(source, home)
    before = snapshot(home)

    def forbidden(*args, **kwargs):
        raise AssertionError("Identical Skills must not perform filesystem mutations")

    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(shutil, "copytree", forbidden)
    monkeypatch.setattr(sync.os, "replace", forbidden)
    result = sync.sync_skills(source, home)
    assert {item["status"] for item in result["skills"]} == {"unchanged"}
    assert result["backup_root"] is None
    assert snapshot(home) == before


def test_changed_skill_is_fully_backed_up_and_stale_files_removed(tmp_path):
    source, home = release(tmp_path)
    sync.sync_skills(source, home)
    name = sync.PUBLISHED_SKILLS[0]
    target = home / "skills" / name
    (target / "personal.md").write_text("my local edit", encoding="utf-8")
    (target / "__pycache__").mkdir()
    (target / "__pycache__/old.pyc").write_bytes(b"private-cache")
    (source / name / "SKILL.md").write_text("# Updated release\n", encoding="utf-8")
    result = sync.sync_skills(source, home)
    backup = Path(result["backup_root"]) / name
    assert (backup / "personal.md").read_text() == "my local edit"
    assert (backup / "__pycache__/old.pyc").read_bytes() == b"private-cache"
    assert (backup / "SKILL.md").read_text() == f"# {name}\n"
    assert not (target / "personal.md").exists()
    assert not (target / "__pycache__").exists()
    assert (target / "SKILL.md").read_text() == "# Updated release\n"
    assert [item["status"] for item in result["skills"]] == ["updated", "unchanged", "unchanged"]


def test_prevalidates_every_source_before_creating_home(tmp_path):
    source, home = release(tmp_path)
    (source / sync.PUBLISHED_SKILLS[-1] / "SKILL.md").unlink()
    with pytest.raises(sync.SkillSyncError, match="SKILL.md"):
        sync.sync_skills(source, home)
    assert not home.exists()


@pytest.mark.parametrize("location", ["source-root", "source-child", "target-root", "target-child", "backup-root", "home"])
def test_symlinks_are_refused_without_modifying_link_destinations(tmp_path, location):
    source, home = release(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("untouched", encoding="utf-8")
    if location == "source-root":
        linked = tmp_path / "linked-source"
        linked.symlink_to(source, target_is_directory=True)
        source = linked
    elif location == "source-child":
        (source / sync.PUBLISHED_SKILLS[0] / "outside").symlink_to(outside, target_is_directory=True)
    elif location == "home":
        home.symlink_to(outside, target_is_directory=True)
    else:
        (home / "skills").mkdir(parents=True)
        if location == "target-root":
            (home / "skills" / sync.PUBLISHED_SKILLS[0]).symlink_to(outside, target_is_directory=True)
        elif location == "backup-root":
            (home / "skill-backups").symlink_to(outside, target_is_directory=True)
        else:
            target = home / "skills" / sync.PUBLISHED_SKILLS[0]
            target.mkdir()
            (target / "outside").symlink_to(outside, target_is_directory=True)
    before = snapshot(outside)
    with pytest.raises(sync.SkillSyncError, match="symbolic link"):
        sync.sync_skills(source, home)
    assert snapshot(outside) == before
    assert not (home / sync.LOCK_NAME).exists()


def test_source_and_target_overlap_is_refused_without_source_damage(tmp_path):
    source, unused_home = release(tmp_path)
    before = snapshot(source)
    with pytest.raises(sync.SkillSyncError, match="overlap"):
        sync.sync_skills(source, source.parent)
    assert snapshot(source) == before


def test_existing_lock_reports_conflict_without_changing_installations(tmp_path):
    source, home = release(tmp_path)
    (home / sync.LOCK_NAME).mkdir(parents=True)
    with pytest.raises(sync.SkillSyncError, match="Another Skill synchronization"):
        sync.sync_skills(source, home)
    assert (home / sync.LOCK_NAME).is_dir()
    assert not (home / "skills").exists()


def test_failed_staging_keeps_old_installations(tmp_path, monkeypatch):
    source, home = release(tmp_path)
    sync.sync_skills(source, home)
    before = snapshot(home / "skills")
    (source / sync.PUBLISHED_SKILLS[0] / "SKILL.md").write_text("new", encoding="utf-8")

    def failure(*args, **kwargs):
        raise OSError("simulated staging disk error")

    monkeypatch.setattr(sync, "_copy_release", failure)
    with pytest.raises(sync.SkillSyncError, match="retained or restored"):
        sync.sync_skills(source, home)
    assert snapshot(home / "skills") == before
    assert not (home / sync.LOCK_NAME).exists()
    assert not list((home / "skills").glob(".novel-os-skills-*"))


@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt])
def test_failed_second_replacement_rolls_back_first_and_current_skill(tmp_path, monkeypatch, failure_type):
    source, home = release(tmp_path)
    sync.sync_skills(source, home)
    before = snapshot(home / "skills")
    for name in sync.PUBLISHED_SKILLS:
        (source / name / "SKILL.md").write_text("new", encoding="utf-8")
    real_replace = sync.os.replace

    def fail_second_install(old, new):
        if Path(old).parent.name == "new" and Path(old).name == sync.PUBLISHED_SKILLS[1]:
            raise failure_type("simulated replacement error")
        return real_replace(old, new)

    monkeypatch.setattr(sync.os, "replace", fail_second_install)
    with pytest.raises(sync.SkillSyncError, match="retained or restored"):
        sync.sync_skills(source, home)
    assert snapshot(home / "skills") == before
    for name in sync.PUBLISHED_SKILLS:
        assert len(list((home / "skill-backups").glob(f"*/{name}/SKILL.md"))) == 1
    assert not (home / sync.LOCK_NAME).exists()


def test_fresh_install_failure_rolls_back_previously_installed_names(tmp_path, monkeypatch):
    source, home = release(tmp_path)
    real_replace = sync.os.replace

    def failure(old, new):
        if Path(old).parent.name == "new" and Path(old).name == sync.PUBLISHED_SKILLS[1]:
            raise OSError("simulated failure")
        return real_replace(old, new)

    monkeypatch.setattr(sync.os, "replace", failure)
    with pytest.raises(sync.SkillSyncError):
        sync.sync_skills(source, home)
    assert not any((home / "skills" / name).exists() for name in sync.PUBLISHED_SKILLS)
    assert not (home / "skill-backups").exists()


def test_cli_uses_explicit_temporary_home_and_reports_clear_error(tmp_path):
    source, home = release(tmp_path)
    args = [sys.executable, str(SCRIPT), "--source", str(source), "--codex-home", str(home)]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    assert len(json.loads(result.stdout)["skills"]) == 3
    assert not result.stderr
    (source / sync.PUBLISHED_SKILLS[2] / "SKILL.md").unlink()
    before = snapshot(home)
    failed = subprocess.run(args, capture_output=True, text=True)
    assert failed.returncode == 1
    assert "Skill sync error:" in failed.stderr and "SKILL.md" in failed.stderr
    assert snapshot(home) == before


def test_failed_backup_never_replaces_existing_skill(tmp_path, monkeypatch):
    source, home = release(tmp_path)
    sync.sync_skills(source, home)
    before = snapshot(home / "skills")
    (source / sync.PUBLISHED_SKILLS[0] / "SKILL.md").write_text("new", encoding="utf-8")
    real_copy = shutil.copytree

    def fail_backup(old, new, *args, **kwargs):
        if "skill-backups" in Path(new).parts:
            raise OSError("simulated backup disk error")
        return real_copy(old, new, *args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", fail_backup)
    with pytest.raises(sync.SkillSyncError, match="retained or restored"):
        sync.sync_skills(source, home)
    assert snapshot(home / "skills") == before
    assert not (home / sync.LOCK_NAME).exists()


def test_failed_rollback_preserves_recovery_directories_and_backup(tmp_path, monkeypatch):
    source, home = release(tmp_path)
    sync.sync_skills(source, home)
    name = sync.PUBLISHED_SKILLS[0]
    (source / name / "SKILL.md").write_text("new", encoding="utf-8")
    real_replace = sync.os.replace

    def fail_install_and_restore(old, new):
        if Path(old).parent.name in {"new", "previous"}:
            raise OSError("simulated installation and recovery error")
        return real_replace(old, new)

    monkeypatch.setattr(sync.os, "replace", fail_install_and_restore)
    with pytest.raises(sync.SkillSyncError, match="Recovery copies"):
        sync.sync_skills(source, home)
    retained = list((home / "skills").glob(f".novel-os-skills-*/previous/{name}/SKILL.md"))
    assert len(retained) == 1
    assert retained[0].read_text() == f"# {name}\n"
    backup = list((home / "skill-backups").glob(f"*/{name}/SKILL.md"))
    assert len(backup) == 1 and backup[0].read_text() == f"# {name}\n"
