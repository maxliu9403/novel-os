"""Canonical hashing tests for semantic StoryState data."""

import json

import pytest

from canon_ledger import canonical_canon_bytes, canonical_canon_sha
from state_manager import Character, RelationshipEdge, StoryState


def _state(tmp_path):
    state = StoryState(str(tmp_path / "project"))
    state.metadata = {"title": "North Door", "last_saved": "first"}
    state.story_bible = {"rules": {"doors": "A locked door stays locked."}}
    state.characters["mara"] = Character(
        id="mara", full_name="Mara Vale", role="protagonist"
    )
    state.relationships["mara-oren"] = RelationshipEdge(
        id="mara-oren", source_id="mara", target_id="oren", label="rival"
    )
    state.create_chapter(1, "The Door")
    return state


def test_canonical_canon_is_stable_across_volatile_and_presentation_changes(tmp_path):
    state = _state(tmp_path)
    before = canonical_canon_bytes(state)

    state.metadata["last_saved"] = "later"
    state.metadata["created_at"] = "later"
    state.metadata["project_path"] = "/tmp/presentation-only"
    state.compile_styles["body"] = {"font": "Serif"}
    state.session_log.append({"timestamp": "later", "action": "opened"})
    state.chapters[1].last_modified = "later"
    state.chapters[1].last_evaluation_id = "evaluation-runtime"
    state.chapters[1].quality_scores["voice"] = 9.0
    state.chapters[1].status = "complete"
    state.chapters[1].word_count = 9000
    state.chapters[1].continuity_checks = {
        "status": "PASS",
        "validated_at": "later",
    }

    assert canonical_canon_bytes(state) == before
    assert canonical_canon_sha(state) == canonical_canon_sha(state)
    payload = json.loads(before.decode("utf-8"))
    assert payload["schema_version"] == 1
    assert "compile_styles" not in payload
    assert "session_log" not in payload


@pytest.mark.parametrize(
    "mutate",
    [
        lambda state: setattr(state.characters["mara"], "secret", "She knew."),
        lambda state: state.chapters[1].new_information.append("The key is false."),
        lambda state: setattr(state.relationships["mara-oren"], "status", "strained"),
        lambda state: state.story_bible["rules"].update(doors="Locks remember."),
        lambda state: state.continuity_exemptions.update(
            {"door-rule": {"reason": "unreliable narrator", "at": "volatile"}}
        ),
    ],
)
def test_canonical_canon_changes_for_semantic_facts(tmp_path, mutate):
    state = _state(tmp_path)
    before = canonical_canon_sha(state)

    mutate(state)

    assert canonical_canon_sha(state) != before


def test_canonical_canon_rejects_non_json_semantics(tmp_path):
    state = _state(tmp_path)
    state.story_bible["unsupported"] = object()

    with pytest.raises(ValueError, match="JSON"):
        canonical_canon_bytes(state)


def test_save_reload_and_noop_save_preserve_canon_hash(tmp_path):
    state = _state(tmp_path)
    before = canonical_canon_sha(state)

    state.save_state()
    reloaded = StoryState(str(tmp_path / "project"))
    reloaded.save_state()

    assert canonical_canon_sha(reloaded) == before
