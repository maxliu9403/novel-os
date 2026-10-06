"""Regression tests for durable agent state updates and continuity metadata."""

from pathlib import Path

from continuity_engine import run_all
from context_pack import build_context_pack, format_context_pack
from state_manager import Character, PlotThread, StoryState
from state_parser import ingest_agent_output
from canon import CanonDeltaProposal, apply_canon_proposal
from commercial_fixtures import verified_reader_value_update
import pytest


def _state(tmp_path: Path) -> StoryState:
    project = tmp_path / "project"
    state = StoryState(str(project))
    state.add_character(Character(id="char_001", full_name="Celia Holt", role="supporting"))
    state.add_plot_thread(
        PlotThread(
            id="plot_001",
            name="Acquisition",
            description="The workers acquire the agency.",
            thread_type="main",
            target_resolution_chapter=3,
            start_chapter=1,
        )
    )
    state.add_plot_thread(
        PlotThread(
            id="plot_002",
            name="Restitution",
            description="Historical wage claims are handled transparently.",
            thread_type="subplot",
            target_resolution_chapter=3,
            start_chapter=1,
        )
    )
    return state


def test_verified_reader_value_update_is_replayed_as_canon(tmp_path):
    state = _state(tmp_path)
    source_sha = "a" * 64
    update = verified_reader_value_update()
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="continuity_guardian",
        source_artifact_sha=source_sha,
        delta={"reader_value_updates": [update]},
    )

    apply_canon_proposal(state, proposal, source_sha)

    restored = state.chapters[1].reader_value_updates[0]
    assert restored["resource_dimension"] == "name"
    assert restored["satisfaction_type"] == "boundary"
    assert restored["hook_type"] == "consequence"


def test_scribe_cannot_self_certify_reader_value(tmp_path):
    state = _state(tmp_path)
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha="b" * 64,
        delta={"reader_value_updates": [verified_reader_value_update()]},
    )

    with pytest.raises(ValueError, match="continuity_guardian"):
        apply_canon_proposal(state, proposal, "b" * 64)


def test_reader_value_update_replay_is_idempotent_and_report_binding_is_strict(tmp_path):
    state = _state(tmp_path)
    source_sha = "a" * 64
    update = verified_reader_value_update()
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="continuity_guardian",
        source_artifact_sha=source_sha,
        delta={"reader_value_updates": [update]},
    )

    apply_canon_proposal(state, proposal, source_sha)
    apply_canon_proposal(state, proposal, source_sha)
    assert len(state.chapters[1].reader_value_updates) == 1

    conflicting = verified_reader_value_update(candidate_sha256="d" * 64)
    conflict = CanonDeltaProposal(
        chapter=1,
        agent_name="continuity_guardian",
        source_artifact_sha=source_sha,
        delta={"reader_value_updates": [conflicting]},
    )
    with pytest.raises(ValueError, match="different candidate"):
        apply_canon_proposal(state, conflict, source_sha)


def test_agent_plot_thread_updates_are_applied_and_persisted(tmp_path):
    state = _state(tmp_path)
    output = """
[CONTINUITY_STATE_UPDATE]
Plot_Thread_Updates:
  - plot_001 | status=resolved | milestone=Worker-owned acquisition closed | chapter=3
  - plot_002 | status=active | target_resolution_chapter=none | milestone=Claims process remains active | chapter=4
[/CONTINUITY_STATE_UPDATE]
"""

    changes = ingest_agent_output(state, 4, "continuity_guardian", output)

    assert state.plot_threads["plot_001"].status == "resolved"
    assert state.plot_threads["plot_001"].last_updated_chapter == 3
    assert state.plot_threads["plot_001"].milestones[-1]["description"] == "Worker-owned acquisition closed"
    assert state.plot_threads["plot_002"].status == "active"
    assert state.plot_threads["plot_002"].target_resolution_chapter is None
    assert state.plot_threads["plot_002"].last_updated_chapter == 4
    assert state.get_chapter(4).plot_thread_updates[0]["thread_id"] == "plot_001"
    assert any("plot_001" in line for line in changes)

    state.save_state()
    reloaded = StoryState(str(tmp_path / "project"))
    assert reloaded.plot_threads["plot_001"].status == "resolved"
    assert reloaded.plot_threads["plot_002"].target_resolution_chapter is None


def test_agent_cannot_reopen_terminal_plot_thread_without_explicit_reopen(tmp_path):
    state = _state(tmp_path)
    state.plot_threads["plot_001"].status = "resolved"
    state.plot_threads["plot_001"].last_updated_chapter = 3

    output = """
[CONTINUITY_STATE_UPDATE]
Plot_Thread_Updates:
  - plot_001 | status=active | milestone=Guardian restated the historical thread | chapter=4
[/CONTINUITY_STATE_UPDATE]
"""

    changes = ingest_agent_output(state, 4, "continuity_guardian", output)

    assert state.plot_threads["plot_001"].status == "resolved"
    assert state.plot_threads["plot_001"].last_updated_chapter == 3
    assert any("reopen" in line.lower() for line in changes)


def test_agent_can_reopen_terminal_plot_thread_only_with_explicit_flag(tmp_path):
    state = _state(tmp_path)
    state.plot_threads["plot_001"].status = "resolved"

    output = """
[CONTINUITY_STATE_UPDATE]
Plot_Thread_Updates:
  - plot_001 | status=active | reopen=true | milestone=Author explicitly reopens the thread | chapter=4
[/CONTINUITY_STATE_UPDATE]
"""

    ingest_agent_output(state, 4, "continuity_guardian", output)

    assert state.plot_threads["plot_001"].status == "active"
    assert state.plot_threads["plot_001"].last_updated_chapter == 4


def test_character_reference_updates_absence_tracking_without_fake_appearance(tmp_path):
    state = _state(tmp_path)
    state.create_chapter(1).status = "drafted"
    state.characters["char_001"].last_appearance_chapter = 1
    output = """
[CONTINUITY_STATE_UPDATE]
Character_References:
  - char_001 | chapter=5 | note=Legacy is referenced; character remains in neurological care.
[/CONTINUITY_STATE_UPDATE]
"""

    ingest_agent_output(state, 5, "continuity_guardian", output)

    character = state.characters["char_001"]
    assert character.last_appearance_chapter == 1
    assert character.last_reference_chapter == 5
    assert "neurological care" in character.absence_note
    assert state.get_chapter(5).character_references[0]["character_id"] == "char_001"

    state.create_chapter(6).status = "drafted"
    findings = run_all(state, Path(tmp_path / "project"), as_of_chapter=6)
    assert not any(f.category == "absent_character" and f.entity_id == "char_001" for f in findings)


def test_ongoing_thread_without_resolution_deadline_is_not_overdue(tmp_path):
    state = _state(tmp_path)
    state.plot_threads["plot_001"].status = "resolved"
    state.plot_threads["plot_001"].last_updated_chapter = 3
    state.plot_threads["plot_002"].target_resolution_chapter = None
    state.plot_threads["plot_002"].last_updated_chapter = 4
    state.create_chapter(5).status = "drafted"

    findings = run_all(state, Path(tmp_path / "project"), as_of_chapter=5)

    assert not any(f.category == "overdue_thread" for f in findings)
    assert not any(f.entity_id == "plot_002" and f.category == "dormant_thread" for f in findings)


def test_foreshadowing_can_be_resolved_by_stable_source_id(tmp_path):
    state = _state(tmp_path)
    first = state.create_chapter(1)
    first.status = "drafted"
    first.foreshadowing_planted = ["A worker vote will decide whether the cooperative proceeds."]
    later = state.create_chapter(5)
    later.status = "drafted"
    output = """
[SCRIBE_STATE_UPDATE]
Foreshadowing_Resolved:
  - id=ch1:fs1 | note=The caregivers voted to proceed under worker governance.
[/SCRIBE_STATE_UPDATE]
"""

    ingest_agent_output(state, 5, "scribe", output)

    findings = run_all(state, Path(tmp_path / "project"), as_of_chapter=5)
    assert not any(f.category == "unresolved_foreshadowing" for f in findings)


def test_foreshadowing_can_be_resolved_by_contract_setup_id_without_accepting_free_text(tmp_path):
    state = _state(tmp_path)
    later = state.create_chapter(5)
    later.status = "drafted"
    output = """
[SCRIBE_STATE_UPDATE]
Foreshadowing_Resolved:
  - id=ch2:setup:payoff_05 | note=The recommendation and unauthorized future service are separated in the bid record.
  - id=ch1:fs1 with prose | note=This text must not become a stable source id.
[/SCRIBE_STATE_UPDATE]
"""

    ingest_agent_output(state, 5, "scribe", output)

    assert "ch2:setup:payoff_05" in later.foreshadowing_resolved_ids
    assert "ch1:fs1 with prose" not in later.foreshadowing_resolved_ids
    assert any("ch1:fs1 with prose" in note for note in later.foreshadowing_resolved)


def test_context_pack_exposes_recent_foreshadowing_ids(tmp_path):
    state = _state(tmp_path)
    first = state.create_chapter(1)
    first.status = "drafted"
    first.foreshadowing_planted = ["A worker vote will decide whether the cooperative proceeds."]
    state.create_chapter(5).status = "drafted"

    pack = build_context_pack(state, 5, purpose="guardian")
    rendered = format_context_pack(pack)

    assert "ch1:fs1" in rendered


def test_context_pack_exposes_exact_plot_thread_ids_and_deadlines(tmp_path):
    state = _state(tmp_path)
    state.plot_threads["plot_001"].last_updated_chapter = 2
    state.create_chapter(3).status = "drafted"

    rendered = format_context_pack(
        build_context_pack(state, 3, purpose="guardian")
    )

    assert "plot_001" in rendered
    assert "target ch3" in rendered
    assert "last advanced ch2" in rendered
