"""Canon delta proposal isolation and persistence tests."""

import copy
import hashlib
import json
import os
import stat
import threading
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import proposals as proposals_module
from canon import CanonDeltaProposal, apply_canon_proposal, build_canon_proposal
from proposals import ProposalStore
from state_manager import StoryState


SOURCE_SHA = "a" * 64
ALLOWED_AGENTS = ("scribe", "editor", "continuity_guardian", "style_curator")


def _state_snapshot(state: StoryState):
    return copy.deepcopy(
        {
            "metadata": state.metadata,
            "story_bible": state.story_bible,
            "characters": state.characters,
            "codex": state.codex,
            "relationships": state.relationships,
            "collections": state.collections,
            "continuity_exemptions": state.continuity_exemptions,
            "compile_styles": state.compile_styles,
            "plot_threads": state.plot_threads,
            "chapters": state.chapters,
            "timeline": state.timeline,
            "style_profile": state.style_profile,
            "binder": state.binder.to_list(),
            "session_log": state.session_log,
        }
    )


def test_build_proposal_does_not_mutate_story_state(tmp_path: Path):
    state = StoryState(str(tmp_path / "project"))
    before = _state_snapshot(state)

    proposal = build_canon_proposal(
        state,
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        text=(
            "[SCRIBE_STATE_UPDATE]\n"
            "Key_Events: Door opens\n"
            "[/SCRIBE_STATE_UPDATE]"
        ),
    )

    assert list(proposal.delta["key_events"]) == ["Door opens"]
    assert _state_snapshot(state) == before


def test_proposal_is_deeply_immutable_and_round_trips_with_stable_identity():
    first = CanonDeltaProposal(
        chapter=2,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"nested": {"items": ["alpha", {"value": "beta"}]}},
        timestamp="2026-08-23T01:02:03+00:00",
    )
    second = CanonDeltaProposal(
        chapter=2,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"nested": {"items": ["alpha", {"value": "beta"}]}},
        timestamp="2026-08-23T04:05:06+00:00",
    )

    assert first.proposal_id == second.proposal_id
    assert CanonDeltaProposal.from_dict(first.to_dict()) == first
    with pytest.raises(TypeError):
        first.delta["nested"]["items"][1]["value"] = "changed"
    with pytest.raises(FrozenInstanceError):
        first.chapter = 3


def test_proposal_recursively_freezes_tuple_contents():
    mutable_items = ["alpha"]
    proposal = CanonDeltaProposal(
        chapter=2,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"nested": (mutable_items,)},
    )

    mutable_items.append("changed")

    assert proposal.delta["nested"] == (("alpha",),)
    with pytest.raises(AttributeError):
        proposal.delta["nested"][0].append("changed")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"chapter": 0}, "chapter"),
        ({"agent_name": "  "}, "agent_name"),
        ({"source_artifact_sha": "A" * 64}, "source_artifact_sha"),
        ({"source_artifact_sha": "a" * 63}, "source_artifact_sha"),
        ({"schema_version": True}, "schema_version"),
        ({"schema_version": 2}, "schema_version"),
    ],
)
def test_proposal_rejects_invalid_identity_fields(overrides, message):
    values = {
        "chapter": 1,
        "agent_name": "scribe",
        "source_artifact_sha": SOURCE_SHA,
        "delta": {"key_events": ["Door opens"]},
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        CanonDeltaProposal(**values)


@pytest.mark.parametrize("agent_name", ["architect", "", "unknown_agent"])
def test_proposal_rejects_agents_that_cannot_propose_canon(agent_name):
    with pytest.raises(ValueError, match="agent_name"):
        CanonDeltaProposal(
            chapter=1,
            agent_name=agent_name,
            source_artifact_sha=SOURCE_SHA,
            delta={"key_events": ["Door opens"]},
        )


@pytest.mark.parametrize(
    "bad_value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        b"bytes",
        {"set"},
        (value for value in ["generator"]),
        object(),
    ],
)
def test_proposal_rejects_non_strict_json_values(bad_value):
    with pytest.raises(ValueError, match="JSON-compatible"):
        CanonDeltaProposal(
            chapter=1,
            agent_name="scribe",
            source_artifact_sha=SOURCE_SHA,
            delta={"bad": bad_value},
        )


def test_proposal_rejects_non_string_mapping_keys():
    with pytest.raises(ValueError, match="JSON-compatible"):
        CanonDeltaProposal(
            chapter=1,
            agent_name="scribe",
            source_artifact_sha=SOURCE_SHA,
            delta={"nested": {1: "not a JSON object key"}},
        )


@pytest.mark.parametrize("agent_name", ALLOWED_AGENTS)
def test_allowed_agents_can_build_proposals(agent_name):
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name=agent_name,
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    assert proposal.agent_name == agent_name


@pytest.mark.parametrize("actual_sha", ["A" * 64, "b" * 64])
def test_apply_sha_mismatch_leaves_state_unchanged(tmp_path: Path, actual_sha: str):
    state = StoryState(str(tmp_path / "project"))
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    before = _state_snapshot(state)

    with pytest.raises(ValueError, match="artifact sha"):
        apply_canon_proposal(state, proposal, actual_sha)

    assert _state_snapshot(state) == before


def test_apply_matching_sha_uses_legacy_state_behavior(tmp_path: Path):
    state = StoryState(str(tmp_path / "project"))
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )

    changes = apply_canon_proposal(state, proposal, SOURCE_SHA)

    assert state.get_chapter(1).plot_advances == ["Door opens"]
    assert any("Door opens" in change for change in changes)


@pytest.mark.parametrize(
    ("agent_name", "text"),
    [
        ("architect", "No recognized state update."),
        ("scribe", "[SCRIBE_STATE_UPDATE]\n[/SCRIBE_STATE_UPDATE]"),
    ],
)
def test_empty_or_unknown_updates_do_not_build_proposals(
    tmp_path: Path, agent_name: str, text: str
):
    state = StoryState(str(tmp_path / "project"))

    with pytest.raises(ValueError, match="state update"):
        build_canon_proposal(state, 1, agent_name, SOURCE_SHA, text)


def test_proposal_store_save_load_and_duplicate_save_are_idempotent(tmp_path: Path):
    store = ProposalStore(tmp_path / "project")
    first = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
        timestamp="2026-08-23T01:02:03+00:00",
    )
    duplicate = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
        timestamp="2026-08-23T04:05:06+00:00",
    )

    persisted = store.save(first)
    duplicate_result = store.save(duplicate)

    assert persisted == first
    assert duplicate_result == first
    assert store.load(first.proposal_id, expected_source_artifact_sha=SOURCE_SHA) == first
    record_path = (
        tmp_path
        / "project"
        / "outputs"
        / "state"
        / "proposals"
        / f"{first.proposal_id}.json"
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert set(record) == {"proposal", "record_sha256"}
    assert record["proposal"]["timestamp"] == first.timestamp
    assert not list(record_path.parent.glob("*.tmp"))


@pytest.mark.parametrize("tampered_field", ["delta", "timestamp", "record_sha256"])
def test_proposal_store_rejects_tampered_records(
    tmp_path: Path, tampered_field: str
):
    store = ProposalStore(tmp_path / tampered_field)
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    store.save(proposal)
    path = store.directory / f"{proposal.proposal_id}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    if tampered_field == "delta":
        record["proposal"]["delta"]["key_events"] = ["Window closes"]
    elif tampered_field == "timestamp":
        record["proposal"]["timestamp"] = "2026-08-23T04:05:06+00:00"
    else:
        record["record_sha256"] = "0" * 64
    path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ValueError, match="record"):
        store.load(proposal.proposal_id)


@pytest.mark.parametrize("proposal_id", ["../story_state", "proposal-bad", "", "a" * 64])
def test_proposal_store_rejects_invalid_ids(tmp_path: Path, proposal_id: str):
    store = ProposalStore(tmp_path / "project")

    with pytest.raises(ValueError, match="proposal_id"):
        store.load(proposal_id)


def test_proposal_store_rejects_wrong_source_sha(tmp_path: Path):
    store = ProposalStore(tmp_path / "project")
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    store.save(proposal)

    with pytest.raises(ValueError, match="source artifact"):
        store.load(proposal.proposal_id, expected_source_artifact_sha="b" * 64)


def test_proposal_store_rejects_record_with_missing_embedded_id(tmp_path: Path):
    store = ProposalStore(tmp_path / "project")
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    store.save(proposal)
    path = store.directory / f"{proposal.proposal_id}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["proposal"]["proposal_id"] = ""
    canonical_proposal = json.dumps(
        record["proposal"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    record["record_sha256"] = hashlib.sha256(canonical_proposal).hexdigest()
    path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ValueError, match="proposal_id"):
        store.load(proposal.proposal_id)


@pytest.mark.parametrize("storage_mode", ["secure", "compat"])
def test_proposal_store_rejects_symlinked_project_root_before_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    storage_mode: str,
):
    if storage_mode == "secure" and not proposals_module._SECURE_DIR_FD:
        pytest.skip("secure directory descriptors are unavailable")
    if storage_mode == "compat":
        monkeypatch.setattr(proposals_module, "_SECURE_DIR_FD", False)

    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.symlink_to(outside, target_is_directory=True)
    store = ProposalStore(project)
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )

    with pytest.raises(ValueError, match="symlink"):
        store.save(proposal)

    outside_record = (
        outside / "outputs/state/proposals" / f"{proposal.proposal_id}.json"
    )
    assert not outside_record.exists()


@pytest.mark.parametrize("storage_mode", ["secure", "compat"])
def test_proposal_store_rejects_symlinked_project_root_before_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    storage_mode: str,
):
    if storage_mode == "secure" and not proposals_module._SECURE_DIR_FD:
        pytest.skip("secure directory descriptors are unavailable")

    outside = tmp_path / "outside"
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    ProposalStore(outside).save(proposal)
    outside_record = (
        outside / "outputs/state/proposals" / f"{proposal.proposal_id}.json"
    )
    assert outside_record.is_file()

    if storage_mode == "compat":
        monkeypatch.setattr(proposals_module, "_SECURE_DIR_FD", False)
    project = tmp_path / "project"
    project.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        ProposalStore(project).load(proposal.proposal_id)


@pytest.mark.parametrize("component", ["outputs", "state", "proposals"])
def test_proposal_store_rejects_symlinked_parent_before_save(tmp_path: Path, component: str):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "outputs").mkdir(parents=True)
    if component == "outputs":
        (project / "outputs").rmdir()
        (project / "outputs").symlink_to(outside, target_is_directory=True)
    else:
        (project / "outputs" / ("state" if component == "state" else "state/proposals")).mkdir(
            parents=True
        )
        target = project / "outputs" / "state"
        if component == "proposals":
            target = target / "proposals"
        target.rmdir()
        target.symlink_to(outside, target_is_directory=True)

    store = ProposalStore(project)
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    with pytest.raises(ValueError, match="symlink"):
        store.save(proposal)
    assert not list(outside.glob("*.json"))


def test_proposal_store_rejects_symlinked_parent_before_load(tmp_path: Path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "outputs" / "state").mkdir(parents=True)
    (project / "outputs" / "state" / "proposals").symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(ValueError, match="symlink"):
        ProposalStore(project).load("proposal-" + "a" * 64)


def test_proposal_store_reports_uncertain_commit_after_link_directory_sync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = ProposalStore(tmp_path / "project")
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    original_fsync = proposals_module.os.fsync

    def fail_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("simulated proposal directory fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(proposals_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(Exception) as raised:
        store.save(proposal)

    uncertain_type = getattr(proposals_module, "ProposalCommitUncertain", None)
    assert uncertain_type is not None
    assert isinstance(raised.value, uncertain_type)
    assert raised.value.operation == "proposal_save"
    assert raised.value.proposal_id == proposal.proposal_id
    assert store.load(proposal.proposal_id) == proposal


def test_secure_store_reports_uncertain_commit_after_directory_close_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    if not proposals_module._SECURE_DIR_FD:
        pytest.skip("secure directory descriptors are unavailable")

    store = ProposalStore(tmp_path / "project")
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    original_link = proposals_module.os.link
    original_close = proposals_module.os.close
    published_directory_fd = None

    def record_link(*args, **kwargs):
        nonlocal published_directory_fd
        result = original_link(*args, **kwargs)
        published_directory_fd = kwargs["dst_dir_fd"]
        return result

    def fail_published_directory_close(descriptor: int) -> None:
        if published_directory_fd is not None and descriptor == published_directory_fd:
            original_close(descriptor)
            raise OSError("simulated post-publication directory close failure")
        original_close(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(proposals_module.os, "link", record_link)
        patch.setattr(proposals_module.os, "close", fail_published_directory_close)

        with pytest.raises(proposals_module.ProposalCommitUncertain) as raised:
            store.save(proposal)

        assert raised.value.operation == "proposal_save"
        assert raised.value.proposal_id == proposal.proposal_id

    assert store.load(proposal.proposal_id) == proposal


def test_proposal_store_pre_link_failure_leaves_no_record_or_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = ProposalStore(tmp_path / "project")
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    original_fsync = proposals_module.os.fsync

    def fail_regular_file_fsync(descriptor: int) -> None:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("simulated proposal temp fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(proposals_module.os, "fsync", fail_regular_file_fsync)

    with pytest.raises(OSError, match="temp fsync failure") as raised:
        store.save(proposal)

    uncertain_type = getattr(proposals_module, "ProposalCommitUncertain")
    assert not isinstance(raised.value, uncertain_type)
    target = store.directory / f"{proposal.proposal_id}.json"
    assert not target.exists()
    assert list(store.directory.glob("*.tmp")) == []


def test_secure_store_preserves_uncertain_error_when_post_link_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = ProposalStore(tmp_path / "project")
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    original_unlink = proposals_module.os.unlink

    def fail_temp_unlink(path, *args, **kwargs):
        if str(path).endswith(".tmp"):
            raise OSError("simulated persistent temp unlink failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(proposals_module.os, "unlink", fail_temp_unlink)

    with pytest.raises(proposals_module.ProposalCommitUncertain) as raised:
        store.save(proposal)

    assert raised.value.operation == "proposal_save"
    assert raised.value.proposal_id == proposal.proposal_id
    assert store.load(proposal.proposal_id) == proposal


def test_compat_store_preserves_uncertain_error_when_post_link_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(proposals_module, "_SECURE_DIR_FD", False)
    store = ProposalStore(tmp_path / "project")
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    original_unlink = Path.unlink

    def fail_temp_unlink(path: Path, *args, **kwargs):
        if path.name.endswith(".tmp"):
            raise OSError("simulated persistent temp unlink failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_temp_unlink)

    with pytest.raises(proposals_module.ProposalCommitUncertain) as raised:
        store.save(proposal)

    assert raised.value.operation == "proposal_save"
    assert raised.value.proposal_id == proposal.proposal_id
    assert store.load(proposal.proposal_id) == proposal


def test_concurrent_identical_proposal_saves_publish_one_record(tmp_path: Path):
    project = tmp_path / "project"
    proposals = [
        CanonDeltaProposal(
            chapter=1,
            agent_name="scribe",
            source_artifact_sha=SOURCE_SHA,
            delta={"key_events": ["Door opens"]},
            timestamp=timestamp,
        )
        for timestamp in (
            "2026-08-23T01:02:03+00:00",
            "2026-08-23T04:05:06+00:00",
        )
    ]
    start = threading.Barrier(3)
    results = []
    errors = []

    def save(proposal: CanonDeltaProposal) -> None:
        try:
            start.wait(timeout=2)
            results.append(ProposalStore(project).save(proposal))
        except Exception as exc:  # pragma: no cover - surfaced by assertions
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(proposal,)) for proposal in proposals]
    for thread in threads:
        thread.start()
    start.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=3)

    assert not errors
    assert not any(thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert results[0] == results[1]
    records = list((project / "outputs/state/proposals").glob("*.json"))
    assert len(records) == 1


def test_proposal_store_load_does_not_follow_record_swapped_to_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = ProposalStore(tmp_path / "project")
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    store.save(proposal)
    record = store.directory / f"{proposal.proposal_id}.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(record.read_bytes())
    swapped = False

    original_is_symlink = Path.is_symlink
    original_open = proposals_module.os.open

    def swap_record() -> None:
        nonlocal swapped
        if swapped:
            return
        swapped = True
        record.unlink()
        record.symlink_to(outside)

    def is_symlink_then_swap(path: Path) -> bool:
        result = original_is_symlink(path)
        if path == record and not result:
            swap_record()
        return result

    def open_after_swap(path, flags, *args, **kwargs):
        if path == record.name and kwargs.get("dir_fd") is not None:
            swap_record()
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(Path, "is_symlink", is_symlink_then_swap)
    monkeypatch.setattr(proposals_module.os, "open", open_after_swap)

    with pytest.raises((OSError, ValueError)):
        store.load(proposal.proposal_id)


def test_proposal_store_load_does_not_follow_parent_swapped_to_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project = tmp_path / "project"
    store = ProposalStore(project)
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha=SOURCE_SHA,
        delta={"key_events": ["Door opens"]},
    )
    store.save(proposal)

    state_dir = project / "outputs" / "state"
    moved_state = project / "outputs" / "state-original"
    outside_state = tmp_path / "outside-state"
    outside_proposals = outside_state / "proposals"
    outside_proposals.mkdir(parents=True)
    record_name = f"{proposal.proposal_id}.json"
    (outside_proposals / record_name).write_bytes(
        (state_dir / "proposals" / record_name).read_bytes()
    )
    swapped = False

    original_is_dir = Path.is_dir
    original_open = proposals_module.os.open

    def swap_parent() -> None:
        nonlocal swapped
        if swapped:
            return
        swapped = True
        state_dir.rename(moved_state)
        state_dir.symlink_to(outside_state, target_is_directory=True)

    def is_dir_then_swap(path: Path) -> bool:
        result = original_is_dir(path)
        if path == state_dir and result:
            swap_parent()
        return result

    def open_after_swap(path, flags, *args, **kwargs):
        if path == "state" and kwargs.get("dir_fd") is not None:
            swap_parent()
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(Path, "is_dir", is_dir_then_swap)
    monkeypatch.setattr(proposals_module.os, "open", open_after_swap)

    with pytest.raises((OSError, ValueError)):
        store.load(proposal.proposal_id)
