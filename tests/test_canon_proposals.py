"""Canon delta proposal isolation and persistence tests."""

import copy
import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

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
