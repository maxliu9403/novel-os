"""Granular assembly recovery extends legacy stage checkpoints without rewriting them."""
from copy import deepcopy
import json

import pytest

from api import workshop_checkpoints as checkpoints
from api.workshop_assembly import ASSEMBLY_PARTS, ASSEMBLY_PROTOCOL_VERSION
from test_workshop_checkpoints import RUN, directory, record


def parts(count=0):
    return {"protocol_version": ASSEMBLY_PROTOCOL_VERSION,
            "active_part": ASSEMBLY_PARTS[count] if count < len(ASSEMBLY_PARTS) else None,
            "revisions": [],
            "completed": {part: {"content": {"source": "完整故事", "part": part},
                                 "content_md": f"# {part}\n完整的小块。",
                                 "saved_at": "2026-10-13T02:00:00+00:00"}
                          for part in ASSEMBLY_PARTS[:count]}}


def test_legacy_resume_appends_parts_preserving_old_identity_and_hashes(tmp_path):
    path = directory(tmp_path)
    checkpoints.save_checkpoint(path, RUN, record(2))
    legacy = checkpoints.load_checkpoint(path, RUN)
    assert "assembly_parts" not in legacy
    assert "assembly" not in checkpoints.project_checkpoint(legacy, "error")
    for count in range(len(ASSEMBLY_PARTS) + 1):
        resumed = deepcopy(legacy)
        resumed["assembly_parts"] = parts(count)
        checkpoints.save_checkpoint(path, RUN, resumed)
        saved = checkpoints.load_checkpoint(path, RUN)
        assert saved["completed"] == legacy["completed"]
        assert {key: saved[key] for key in checkpoints._IDENTITY} == {
            key: legacy[key] for key in checkpoints._IDENTITY}
        checkpoints.save_checkpoint(path, RUN, saved)
    before = (path / f"{RUN}.json").read_bytes()
    projected = checkpoints.project_checkpoint(saved, "error")["assembly"]
    assert projected["completed_parts"] == len(ASSEMBLY_PARTS)
    assert projected["active_part"] is None
    assert all(part["status"] == "saved" for part in projected["parts"])
    assert (path / f"{RUN}.json").read_bytes() == before


@pytest.mark.parametrize("status", ["running", "error", "paused"])
def test_part_projection_distinguishes_saved_and_current_model_work(tmp_path, status):
    data = record(2)
    data["assembly_parts"] = parts(2)
    data["assembly_parts"]["completed"][ASSEMBLY_PARTS[0]]["content"]["internal"] = "PRIVATE_SCHEMA"
    path = directory(tmp_path)
    checkpoints.save_checkpoint(path, RUN, data)
    projected = checkpoints.project_checkpoint(checkpoints.load_checkpoint(path, RUN), status)
    assembly = projected["assembly"]
    assert assembly["total_parts"] == len(ASSEMBLY_PARTS)
    assert assembly["completed_parts"] == 2
    assert assembly["active_part"] == ASSEMBLY_PARTS[2]
    assert [part["part"] for part in assembly["parts"]] == list(ASSEMBLY_PARTS)
    assert assembly["parts"][2]["status"] == ("running" if status == "running" else "pending")
    assert assembly["parts"][3]["status"] == "pending"
    assert "PRIVATE_SCHEMA" not in json.dumps(projected)
    assert all("content" not in part and "sha256" not in part for part in assembly["parts"])


@pytest.mark.parametrize("mutate", [
    lambda data: data["assembly_parts"].update(protocol_version="unsupported"),
    lambda data: data["assembly_parts"].update(extra="unknown"),
    lambda data: data["assembly_parts"].update(active_part=ASSEMBLY_PARTS[4]),
    lambda data: data["assembly_parts"]["completed"].update(unknown={}),
    lambda data: data["assembly_parts"]["completed"].update({ASSEMBLY_PARTS[3]: data["assembly_parts"]["completed"].pop(ASSEMBLY_PARTS[0])}),
    lambda data: data["completed"].pop("chapter_plan"),
    lambda data: data["assembly_parts"]["completed"][ASSEMBLY_PARTS[0]].update(content_md=""),
])
def test_malformed_partial_protocol_fails_closed_before_write(tmp_path, mutate):
    data = record(2)
    data["assembly_parts"] = parts(1)
    mutate(data)
    with pytest.raises(checkpoints.CheckpointError):
        checkpoints.save_checkpoint(directory(tmp_path), RUN, data)
    assert not directory(tmp_path).exists()


@pytest.mark.parametrize("mutation", ["content", "markdown", "remove", "block", "identity", "source_stage"])
def test_partial_artifacts_cannot_be_rewritten_removed_or_replayed(tmp_path, mutation):
    path = directory(tmp_path)
    data = record(2)
    data["assembly_parts"] = parts(2)
    checkpoints.save_checkpoint(path, RUN, data)
    before = (path / f"{RUN}.json").read_bytes()
    changed = deepcopy(data)  # No hashes, so replacement hashes alone cannot bypass append-only history.
    if mutation == "content":
        changed["assembly_parts"]["completed"][ASSEMBLY_PARTS[0]]["content"] = {"replaced": True}
    elif mutation == "markdown":
        changed["assembly_parts"]["completed"][ASSEMBLY_PARTS[1]]["content_md"] = "Replacement"
    elif mutation == "remove":
        changed["assembly_parts"] = parts(1)
    elif mutation == "block":
        changed.pop("assembly_parts")
    elif mutation == "identity":
        changed["source_revision"] += 1
    else:
        changed["completed"]["chapter_plan"]["content"]["new"] = "different plan"
    with pytest.raises(checkpoints.CheckpointError):
        checkpoints.save_checkpoint(path, RUN, changed)
    assert (path / f"{RUN}.json").read_bytes() == before


def test_disk_part_tampering_and_cross_run_copy_fail_integrity_check(tmp_path):
    path = directory(tmp_path)
    data = record(2)
    data["assembly_parts"] = parts(1)
    checkpoints.save_checkpoint(path, RUN, data)
    saved = checkpoints.load_checkpoint(path, RUN)
    tampered = deepcopy(saved)
    tampered["assembly_parts"]["completed"][ASSEMBLY_PARTS[0]]["content"]["source"] = "PRIVATE_CHANGED"
    (path / f"{RUN}.json").write_text(json.dumps(tampered))
    with pytest.raises(checkpoints.CheckpointError, match="integrity") as error:
        checkpoints.load_checkpoint(path, RUN)
    assert "PRIVATE_CHANGED" not in str(error.value)
    other = record(2)
    other["run_id"] = "c" * 32
    checkpoints.save_checkpoint(path, other["run_id"], other)
    copied = checkpoints.load_checkpoint(path, other["run_id"])
    copied["assembly_parts"] = saved["assembly_parts"]
    with pytest.raises(checkpoints.CheckpointError, match="integrity"):
        checkpoints.save_checkpoint(path, other["run_id"], copied)


def test_final_assembly_requires_all_parts_and_is_bound_to_their_hash_chain(tmp_path):
    data = record(3)
    data["assembly_parts"] = parts(1)
    with pytest.raises(checkpoints.CheckpointError):
        checkpoints.save_checkpoint(directory(tmp_path), RUN, data)
    data["assembly_parts"] = parts(len(ASSEMBLY_PARTS))
    path = directory(tmp_path)
    checkpoints.save_checkpoint(path, RUN, data)
    complete = checkpoints.load_checkpoint(path, RUN)
    assert checkpoints.project_checkpoint(complete, "ready")["status"] == "complete"
    legacy = record(3)
    second = tmp_path / "legacy"
    checkpoints.save_checkpoint(second, RUN, legacy)
    legacy_complete = checkpoints.load_checkpoint(second, RUN)
    assert complete["completed"]["story_design"] == legacy_complete["completed"]["story_design"]
    assert complete["completed"]["chapter_plan"] == legacy_complete["completed"]["chapter_plan"]
    assert complete["completed"]["assembly"]["sha256"] != legacy_complete["completed"]["assembly"]["sha256"]


def test_partial_payloads_keep_storage_limit_and_atomic_failure_guarantees(tmp_path, monkeypatch):
    path = directory(tmp_path)
    data = record(2)
    data["assembly_parts"] = parts(1)
    checkpoints.save_checkpoint(path, RUN, data)
    before = (path / f"{RUN}.json").read_bytes()
    data["assembly_parts"] = parts(2)
    monkeypatch.setattr(checkpoints, "MAX_CHECKPOINT_BYTES", len(before))
    with pytest.raises(checkpoints.CheckpointError, match="no content was truncated"):
        checkpoints.save_checkpoint(path, RUN, data)
    assert (path / f"{RUN}.json").read_bytes() == before


def test_targeted_revisions_preserve_originals_and_project_latest_complete_result(tmp_path):
    path = directory(tmp_path)
    data = record(2)
    data["assembly_parts"] = parts(len(ASSEMBLY_PARTS))
    checkpoints.save_checkpoint(path, RUN, data)
    before = checkpoints.load_checkpoint(path, RUN)
    data["assembly_parts"]["active_part"] = ASSEMBLY_PARTS[0]
    checkpoints.save_checkpoint(path, RUN, data)
    projected = checkpoints.project_checkpoint(checkpoints.load_checkpoint(path, RUN), "running")["assembly"]
    assert projected["completed_parts"] == len(ASSEMBLY_PARTS)
    assert projected["parts"][0]["status"] == "running"
    revised = {"part": ASSEMBLY_PARTS[0], "content": {"repaired": "metadata"},
               "content_md": "已修复规格", "saved_at": "2026-10-13T02:05:00+00:00"}
    data["assembly_parts"]["revisions"].append(revised)
    data["assembly_parts"]["active_part"] = None
    checkpoints.save_checkpoint(path, RUN, data)
    saved = checkpoints.load_checkpoint(path, RUN)
    assert saved["assembly_parts"]["completed"] == before["assembly_parts"]["completed"]
    assert saved["completed"] == before["completed"]
    assert checkpoints.project_checkpoint(saved, "paused")["assembly"]["parts"][0]["content_md"] == "已修复规格"
    old_bytes = (path / f"{RUN}.json").read_bytes()
    for revision_change in ([], [{**revised, "content_md": "替代旧修订"}]):
        changed = deepcopy(data)
        changed["assembly_parts"]["revisions"] = revision_change
        with pytest.raises(checkpoints.CheckpointError):
            checkpoints.save_checkpoint(path, RUN, changed)
        assert (path / f"{RUN}.json").read_bytes() == old_bytes


def test_revisions_require_complete_parts_and_reject_reordering_or_tampering(tmp_path):
    data = record(2)
    data["assembly_parts"] = parts(2)
    artifact = {"part": ASSEMBLY_PARTS[0], **deepcopy(data["assembly_parts"]["completed"][ASSEMBLY_PARTS[0]])}
    data["assembly_parts"]["revisions"] = [artifact]
    with pytest.raises(checkpoints.CheckpointError, match="revisions"):
        checkpoints.save_checkpoint(directory(tmp_path), RUN, data)
    data["assembly_parts"] = parts(len(ASSEMBLY_PARTS))
    data["assembly_parts"]["revisions"] = [artifact, {**artifact, "part": ASSEMBLY_PARTS[1]}]
    path = directory(tmp_path)
    checkpoints.save_checkpoint(path, RUN, data)
    saved = checkpoints.load_checkpoint(path, RUN)
    saved["assembly_parts"]["revisions"].reverse()
    (path / f"{RUN}.json").write_text(json.dumps(saved))
    with pytest.raises(checkpoints.CheckpointError, match="integrity"):
        checkpoints.load_checkpoint(path, RUN)
