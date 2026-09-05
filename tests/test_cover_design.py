from __future__ import annotations

import json

from core.cover_design import collect_visual_evidence
from core.cover_models_v2 import CoverBriefV2
from tests.test_cover_models_v2 import two_character_fixture


def _project(tmp_path):
    project = tmp_path / "book"
    inputs = project / "outputs" / "input"
    publication = project / "outputs" / "publication"
    manuscript = project / "outputs" / "manuscript"
    state = project / "outputs" / "state"
    for path in (inputs, publication, manuscript, state):
        path.mkdir(parents=True, exist_ok=True)
    inputs.joinpath("prompt.md").write_text(
        "# Story\n\nMara closes the apartment door while the shared brass key rests in her palm.\n\n"
        "The child's backpack waits beside a repaired dining chair, turning an ordinary routine into a boundary.",
        encoding="utf-8",
    )
    inputs.joinpath("foundation.json").write_text(json.dumps({
        "premise": "A damaged relationship turns the family doorway into a test of belonging.",
        "plot_threads": [{
            "type": "main",
            "description": "Mara replaces verbal promises with one visible boundary at home.",
        }],
    }), encoding="utf-8")
    state.joinpath("story_state.json").write_text("{}", encoding="utf-8")
    publication.joinpath("publication-copy.json").write_text(json.dumps({
        "reader_heading": "The Key He Thought Was Still His",
        "hook_lead": "Can one removed key protect a child without closing every future door?",
        "spoiler_free_blurb": "A familiar doorway becomes the place where a partner finally acts.",
        "whole_book_core_conflict": "Belonging and access are no longer the same promise.",
    }), encoding="utf-8")
    chapters = (
        "Mara watches the hallway light touch the key before she closes the door.",
        "At the kitchen table, an empty hook on the key rail becomes impossible to ignore.",
        "The repaired chair remains by the window as she returns the old ring to a drawer.",
    )
    for index, chapter in enumerate(chapters, start=1):
        manuscript.joinpath(f"chapter_{index:03d}_final.md").write_text(
            f"# Chapter {index}\n\n{chapter}", encoding="utf-8",
        )
    return project


def test_visual_evidence_collects_prompt_preface_and_final_prose_with_source_refs(tmp_path) -> None:
    project = _project(tmp_path)
    brief = CoverBriefV2.from_dict(two_character_fixture(), source_prompt_sha256="a" * 64)

    ledger = collect_visual_evidence(project, brief)

    source_types = {item.source_type for item in ledger.items}
    assert {"publication_intro", "source_prompt", "final_chapter", "story_node"} <= source_types
    assert any("removed key" in item.summary for item in ledger.items)
    assert any(item.spoiler_level == "late_spoiler" for item in ledger.items)
    assert all(item.source_ref for item in ledger.items)
    assert all(reference.startswith("evidence:ev-") for reference in ledger.allowed_refs)


def test_visual_evidence_bundle_hash_changes_when_reader_intro_changes(tmp_path) -> None:
    project = _project(tmp_path)
    brief = CoverBriefV2.from_dict(two_character_fixture(), source_prompt_sha256="a" * 64)
    first = collect_visual_evidence(project, brief)
    publication = project / "outputs" / "publication" / "publication-copy.json"
    payload = json.loads(publication.read_text(encoding="utf-8"))
    payload["hook_lead"] = "A changed reader hook now emphasizes the empty chair."
    publication.write_text(json.dumps(payload), encoding="utf-8")

    second = collect_visual_evidence(project, brief)

    assert second.source_bundle_sha256 != first.source_bundle_sha256


def test_visual_evidence_ignores_intermediate_candidate_finals(tmp_path) -> None:
    project = _project(tmp_path)
    candidate = project / "outputs" / "manuscript" / "chapter_001_candidate_final.md"
    candidate.write_text(
        "# Candidate\n\nA rejected red umbrella dominates this discarded version.",
        encoding="utf-8",
    )
    brief = CoverBriefV2.from_dict(two_character_fixture(), source_prompt_sha256="a" * 64)

    ledger = collect_visual_evidence(project, brief)

    assert not any("candidate_final" in path for path in ledger.source_files)
    assert not any("red umbrella" in item.summary for item in ledger.items)
