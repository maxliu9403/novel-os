"""Checkpoint recovery preserves complete artifacts without replay or truncation."""
from copy import deepcopy
import json
import os
import stat

import pytest

from api import workshop_checkpoints as checkpoints


RUN = "a" * 32
SESSION = "b" * 32


def record(count=0):
    return {
        "schema_version": checkpoints.SCHEMA_VERSION, "run_id": RUN,
        "session_id": SESSION, "source_revision": 12,
        "input_sha256": checkpoints.make_fingerprint({"story": "完整故事", "messages": ["继续"]}, "skill.v1"),
        "skill_version": "skill.v1", "narrative_mode": "short_novel",
        "active_stage": checkpoints.STAGES[count] if count < len(checkpoints.STAGES) else None,
        "completed": {stage: {"content": {"decisions": [stage, "完整保存的因果与伏笔"]},
                              "content_md": f"# {stage}\n完整保存的故事内容。",
                              "saved_at": "2026-10-12T10:00:00+00:00"}
                      for stage in checkpoints.STAGES[:count]},
    }


def directory(tmp_path):
    return tmp_path / "delivery"


def test_missing_reads_are_pure_and_invalid_run_ids_cannot_escape(tmp_path):
    path = directory(tmp_path)
    assert checkpoints.load_checkpoint(path, RUN) is None
    assert not path.exists()
    for run_id in ("../escape", "a/b", "A" * 32, "a" * 31, 42):
        with pytest.raises(checkpoints.CheckpointError):
            checkpoints.load_checkpoint(path, run_id)
    assert not path.exists()


def test_fingerprint_covers_complete_input_and_skill_without_key_order_dependence():
    context = {"message": "推理" * 80000, "conversation": [{"role": "user", "text": "继续"}]}
    original = deepcopy(context)
    digest = checkpoints.make_fingerprint(context, "v1")
    assert digest == checkpoints.make_fingerprint(dict(reversed(list(context.items()))), "v1")
    assert digest != checkpoints.make_fingerprint({**context, "message": context["message"] + "末尾"}, "v1")
    assert digest != checkpoints.make_fingerprint(context, "v2")
    assert context == original
    with pytest.raises(checkpoints.CheckpointError):
        checkpoints.make_fingerprint({"number": float("nan")}, "v1")


def test_save_append_load_and_get_projection_survive_fresh_reads(tmp_path):
    path = directory(tmp_path)
    initial = record()
    checkpoints.save_checkpoint(path, RUN, initial)
    assert checkpoints.load_checkpoint(path, RUN) == initial
    for count in range(1, 4):
        next_record = record(count)
        original = deepcopy(next_record)
        checkpoints.save_checkpoint(path, RUN, next_record)
        assert next_record == original  # Caller state is not unexpectedly mutated.
        saved = checkpoints.load_checkpoint(path, RUN)
        assert all(len(artifact["sha256"]) == 64 for artifact in saved["completed"].values())
        assert saved["completed"][checkpoints.STAGES[count - 1]]["content"] == next_record["completed"][checkpoints.STAGES[count - 1]]["content"]
        checkpoints.save_checkpoint(path, RUN, saved)  # Idempotent re-save.
    before = (path / f"{RUN}.json").stat().st_mtime_ns
    complete = checkpoints.project_checkpoint(checkpoints.load_checkpoint(path, RUN), "error", 12)
    assert complete["status"] == "complete"
    assert complete["active_stage"] is None and complete["resumable"]
    assert not checkpoints.project_checkpoint(checkpoints.load_checkpoint(path, RUN), "ready")["resumable"]
    assert complete["completed_stages"] == list(checkpoints.STAGES)
    assert all(stage["status"] == "saved" for stage in complete["stages"])
    assert (path / f"{RUN}.json").stat().st_mtime_ns == before
    assert list(path.iterdir()) == [path / f"{RUN}.json"]
    assert stat.S_IMODE((path / f"{RUN}.json").stat().st_mode) == 0o600


@pytest.mark.parametrize("status,resumable", [("running", False), ("paused", True), ("error", True), ("draft", False)])
def test_projection_only_exposes_saved_markdown_and_honest_state(tmp_path, status, resumable):
    path = directory(tmp_path)
    data = record(1)
    data["completed"]["story_design"]["content"]["internal"] = "PRIVATE_VALIDATOR_CONTEXT"
    checkpoints.save_checkpoint(path, RUN, data)
    saved = checkpoints.load_checkpoint(path, RUN)
    projected = checkpoints.project_checkpoint(saved, status, 12)
    assert projected["resumable"] is resumable
    assert projected["completed_stages"] == ["story_design"]
    assert projected["stages"][1]["status"] == ("running" if status == "running" else "pending")
    assert projected["stages"][0]["content_md"] == data["completed"]["story_design"]["content_md"]
    assert "PRIVATE_VALIDATOR_CONTEXT" not in json.dumps(projected)
    assert "input_sha256" not in projected and "skill_version" not in projected
    assert all("sha256" not in stage and "content" not in stage for stage in projected["stages"])
    with pytest.raises(checkpoints.CheckpointError, match="revision"):
        checkpoints.project_checkpoint(saved, status, 13)


def test_empty_saved_record_does_not_claim_a_resumable_result(tmp_path):
    path = directory(tmp_path)
    checkpoints.save_checkpoint(path, RUN, record())
    saved = checkpoints.load_checkpoint(path, RUN)
    assert not checkpoints.project_checkpoint(saved, "paused")["resumable"]


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(run_id="c" * 32),
    lambda data: data.update(schema_version="workshop.delivery.v2"),
    lambda data: data.update(source_revision=True),
    lambda data: data.update(input_sha256="bad"),
    lambda data: data.update(active_stage="assembly"),
    lambda data: data["completed"].update(assembly=data["completed"].pop("story_design")),
    lambda data: data["completed"]["story_design"].update(saved_at="2026-10-12T10:00:00"),
    lambda data: data["completed"]["story_design"].update(content=[]),
    lambda data: data["completed"]["story_design"].update(content_md="  "),
    lambda data: data["completed"]["story_design"].update(sha256="0" * 64),
])
def test_invalid_identity_stage_order_or_artifact_is_rejected_before_write(tmp_path, mutation):
    data = record(1)
    mutation(data)
    with pytest.raises(checkpoints.CheckpointError):
        checkpoints.save_checkpoint(directory(tmp_path), RUN, data)
    assert not directory(tmp_path).exists()


@pytest.mark.parametrize("field", ["content", "content_md", "saved_at"])
def test_artifact_tampering_fails_closed_and_does_not_overwrite(tmp_path, field):
    path = directory(tmp_path)
    checkpoints.save_checkpoint(path, RUN, record(2))
    target = path / f"{RUN}.json"
    corrupted = json.loads(target.read_text())
    replacements = {"content": {"changes": "PRIVATE STORY"}, "content_md": "PRIVATE STORY", "saved_at": "2026-10-12T11:00:00Z"}
    corrupted["completed"]["story_design"][field] = replacements[field]
    target.write_text(json.dumps(corrupted))
    before = target.read_bytes()
    with pytest.raises(checkpoints.CheckpointError, match="integrity") as exc:
        checkpoints.load_checkpoint(path, RUN)
    assert "PRIVATE STORY" not in str(exc.value)
    with pytest.raises(checkpoints.CheckpointError):
        checkpoints.save_checkpoint(path, RUN, record(3))
    assert target.read_bytes() == before


def test_copying_artifact_from_another_identity_breaks_hash_chain(tmp_path):
    path = directory(tmp_path)
    checkpoints.save_checkpoint(path, RUN, record(1))
    copied = checkpoints.load_checkpoint(path, RUN)
    copied["run_id"] = "c" * 32
    (path / f"{'c' * 32}.json").write_text(json.dumps(copied))
    with pytest.raises(checkpoints.CheckpointError, match="integrity"):
        checkpoints.load_checkpoint(path, "c" * 32)
    with pytest.raises(checkpoints.CheckpointError, match="another run"):
        checkpoints.save_checkpoint(path, "d" * 32, copied)


@pytest.mark.parametrize("change", ["remove", "replace", "session", "revision", "fingerprint", "skill"])
def test_saved_history_and_bound_identity_cannot_be_rewritten(tmp_path, change):
    path = directory(tmp_path)
    checkpoints.save_checkpoint(path, RUN, record(1))
    before = (path / f"{RUN}.json").read_bytes()
    data = record(2)
    if change == "remove":
        data = record()
    elif change == "replace":
        data["completed"]["story_design"]["content_md"] = "Replacement"
    else:
        field, value = {"session": ("session_id", "c" * 32), "revision": ("source_revision", 13),
                        "fingerprint": ("input_sha256", "0" * 64), "skill": ("skill_version", "skill.v2")}[change]
        data[field] = value
    with pytest.raises(checkpoints.CheckpointError):
        checkpoints.save_checkpoint(path, RUN, data)
    assert (path / f"{RUN}.json").read_bytes() == before


@pytest.mark.parametrize("kind", ["directory", "parent", "file", "hardlink", "fifo", "traversal"])
def test_filesystem_aliases_and_non_regular_files_are_rejected(tmp_path, kind):
    path = directory(tmp_path)
    target_dir = tmp_path / "actual"
    target_dir.mkdir()
    target = target_dir / "untouched"
    target.write_text("PRIVATE")
    if kind == "directory":
        path.symlink_to(target_dir, target_is_directory=True)
    elif kind == "parent":
        (tmp_path / "alias").symlink_to(target_dir, target_is_directory=True)
        path = tmp_path / "alias" / "delivery"
    elif kind == "traversal":
        path = tmp_path / "actual" / ".." / "delivery"
    else:
        path.mkdir()
        checkpoint_file = path / f"{RUN}.json"
        if kind == "file":
            checkpoint_file.symlink_to(target)
        elif kind == "hardlink":
            os.link(target, checkpoint_file)
        else:
            os.mkfifo(checkpoint_file)
    with pytest.raises(checkpoints.CheckpointError):
        checkpoints.load_checkpoint(path, RUN)
    with pytest.raises(checkpoints.CheckpointError):
        checkpoints.save_checkpoint(path, RUN, record())
    assert target.read_text() == "PRIVATE"


def test_large_records_are_never_truncated_on_write_or_read(tmp_path, monkeypatch):
    path = directory(tmp_path)
    checkpoints.save_checkpoint(path, RUN, record(1))
    target = path / f"{RUN}.json"
    original = target.read_bytes()
    monkeypatch.setattr(checkpoints, "MAX_CHECKPOINT_BYTES", len(original) + 10)
    with pytest.raises(checkpoints.CheckpointError, match="no content was truncated"):
        checkpoints.save_checkpoint(path, RUN, record(2))
    assert target.read_bytes() == original
    target.write_bytes(original + b" " * 11)
    with pytest.raises(checkpoints.CheckpointError, match="byte limit"):
        checkpoints.load_checkpoint(path, RUN)


@pytest.mark.parametrize("payload", [b'{"run_id":"a", "run_id":"b"}', b"{incomplete", b"\xff", b'{"n":NaN}'])
def test_malformed_existing_files_are_not_overwritten(tmp_path, payload):
    path = directory(tmp_path)
    path.mkdir()
    target = path / f"{RUN}.json"
    target.write_bytes(payload)
    with pytest.raises(checkpoints.CheckpointError):
        checkpoints.save_checkpoint(path, RUN, record())
    assert target.read_bytes() == payload


def test_failed_atomic_replace_preserves_previous_result_and_cleans_temp(tmp_path, monkeypatch):
    path = directory(tmp_path)
    checkpoints.save_checkpoint(path, RUN, record(1))
    target = path / f"{RUN}.json"
    original = target.read_bytes()

    def fail(*_args, **_kwargs):
        raise OSError("PRIVATE DISK PATH")

    monkeypatch.setattr(checkpoints.os, "replace", fail)
    with pytest.raises(checkpoints.CheckpointError) as exc:
        checkpoints.save_checkpoint(path, RUN, record(2))
    assert "PRIVATE" not in str(exc.value)
    assert target.read_bytes() == original
    assert list(path.iterdir()) == [target]


def test_save_flushes_file_before_replace_and_directory_after(tmp_path, monkeypatch):
    path = directory(tmp_path)
    checkpoints.save_checkpoint(path, RUN, record())
    events = []
    fsync, replace = checkpoints.os.fsync, checkpoints.os.replace

    def observe_fsync(fd):
        events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        return fsync(fd)

    def observe_replace(*args, **kwargs):
        events.append("replace")
        return replace(*args, **kwargs)

    monkeypatch.setattr(checkpoints.os, "fsync", observe_fsync)
    monkeypatch.setattr(checkpoints.os, "replace", observe_replace)
    checkpoints.save_checkpoint(path, RUN, record(1))
    assert events == ["file", "replace", "directory"]
