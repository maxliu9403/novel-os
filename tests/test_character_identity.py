"""Canonical identities survive persistence, prompt budgeting and state updates."""

from dataclasses import asdict

import pytest

from canon_ledger import canonical_canon_sha
from context_pack import build_context_pack, format_context_pack
from continuity_engine import check_character_identity, check_hostile_pairs_co_present
from state_manager import Character, RelationshipEdge, StoryState
from state_parser import _resolve_character_id
from story_foundation import apply_story_foundation


def _state(tmp_path):
    return StoryState(str(tmp_path / "project"))


def _character(**overrides):
    return Character(**{
        "id": "char_001", "full_name": "林澜", "role": "protagonist",
        "gender": "female", "pronouns": "她", "aliases": ["阿澜"], **overrides,
    })


def _foundation():
    return {
        "characters": [{
            "id": "char_001", "name": "林澜", "role": "protagonist",
            "gender": "female", "pronouns": "她", "aliases": ["阿澜"],
        }],
        "plot_threads": [{"name": "隐藏的信", "description": "林澜寻找真相"}],
        "chapters": [{"number": 1, "title": "信", "pov": "林澜"}],
    }


def test_foundation_identity_persists_and_is_supplied_to_guardian(tmp_path):
    state = _state(tmp_path)
    apply_story_foundation(state, _foundation(), 2000)
    before = canonical_canon_sha(state)
    state.save_state()
    reloaded = _state(tmp_path)

    assert reloaded.characters["char_001"].identity_dict() == _character().identity_dict()
    assert canonical_canon_sha(reloaded) == before
    assert reloaded.get_continuity_context(1)["character_identities"]["char_001"]["pronouns"] == "她"


def test_legacy_characters_do_not_infer_identity_or_change_semantic_hash(tmp_path):
    state = _state(tmp_path)
    char = Character(id="legacy", full_name="Alex", role="protagonist")
    legacy = asdict(char)
    for key in ("gender", "pronouns", "aliases"):
        legacy.pop(key)
    state.characters[char.id] = Character.from_dict(legacy)

    assert state.characters[char.id].to_dict() == legacy
    assert char.gender == char.pronouns == ""
    before = canonical_canon_sha(state)
    state.save_state()
    assert canonical_canon_sha(_state(tmp_path)) == before
    report = check_character_identity(state)
    assert report[0].category == "character_identity_unspecified"
    assert report[0].severity == "warning"


@pytest.mark.parametrize("field,value", [
    ("full_name", "林岚"), ("gender", "male"), ("pronouns", "他"),
    ("aliases", ["阿岚"]),
])
def test_scene_updates_cannot_silently_change_identity(tmp_path, field, value):
    state = _state(tmp_path)
    char = _character()
    state.add_character(char)

    with pytest.raises(ValueError, match="identity is locked"):
        state.update_character(char.id, {"current_location": "海边", field: value})

    assert char.identity_dict() == _character().identity_dict()
    assert char.current_location == ""
    state.update_character(char.id, {"current_location": "海边"})
    assert char.current_location == "海边"


def test_explicit_author_identity_edit_is_logged_and_not_reverted_by_foundation(tmp_path):
    state = _state(tmp_path)
    apply_story_foundation(state, _foundation(), 2000)
    state.update_character("char_001", {
        "full_name": "林岚", "gender": "nonbinary", "pronouns": "TA", "aliases": ["小岚"],
    }, allow_identity_change=True)
    identity = state.characters["char_001"].identity_dict()

    apply_story_foundation(state, _foundation(), 2000)

    assert state.characters["char_001"].identity_dict() == identity
    assert state.chapters[1].pov_character == "林岚"
    assert state.get_character_by_name("林澜") is None
    assert any(item["action"] == "character_identity_changed" for item in state.session_log)
    assert any(item["action"] == "foundation_identity_preserved" for item in state.session_log)


def test_foundation_replay_preserves_explicitly_cleared_identity_after_reload(tmp_path):
    state = _state(tmp_path)
    apply_story_foundation(state, _foundation(), 2000)
    state.update_character("char_001", {
        "gender": "", "pronouns": "", "aliases": [],
    }, allow_identity_change=True)
    state.save_state()
    reloaded = _state(tmp_path)

    apply_story_foundation(reloaded, _foundation(), 2000)

    assert reloaded.characters["char_001"].gender == ""
    assert reloaded.characters["char_001"].pronouns == ""
    assert reloaded.characters["char_001"].aliases == []


def test_identity_cannot_be_replaced_using_duplicate_id(tmp_path):
    state = _state(tmp_path)
    state.add_character(_character())
    with pytest.raises(ValueError, match="already exists"):
        state.add_character(_character(full_name="林岚"))
    with pytest.raises(ValueError, match="id is immutable"):
        state.update_character("char_001", {"id": "char_002"}, allow_identity_change=True)


@pytest.mark.parametrize("identity", [
    {"gender": ["female"]}, {"pronouns": ["她"]},
    {"aliases": "阿澜"}, {"aliases": [""]},
])
def test_identity_rejects_invalid_field_shapes(identity):
    with pytest.raises(ValueError, match="character"):
        _character(**identity)


def test_aliases_and_short_chinese_names_are_retrieved_from_late_prose(tmp_path):
    state = _state(tmp_path)
    state.add_character(_character())
    state.add_character(_character(id="char_002", full_name="沈舟", gender="male", pronouns="他", aliases=[]))
    state.create_chapter(9, "重逢")
    pack = build_context_pack(state, 9, purpose="guardian", chapter_text="雨" * 9000 + "阿澜遇见沈舟。")
    rendered = format_context_pack(pack)

    assert {item["id"] for item in pack.cast} == {"char_001", "char_002"}
    assert "**林澜**" in rendered
    assert "pronouns=她" in rendered and "pronouns=他" in rendered
    assert "aliases=阿澜" in rendered
    assert "Identity lock" in rendered
    assert state.get_character_by_name("阿澜").id == "char_001"


def test_ambiguous_names_never_assign_state_to_first_character(tmp_path):
    state = _state(tmp_path)
    state.add_character(_character(full_name="Alex Holt", aliases=["A"]))
    state.add_character(_character(id="char_002", full_name="Sam Holt", aliases=["A"]))

    assert _resolve_character_id(state, "Holt") is None
    assert _resolve_character_id(state, "A") is None
    assert _resolve_character_id(state, "Alex") == "char_001"
    assert _resolve_character_id(state, "char_002") == "char_002"
    assert state.get_character_by_name("A") is None
    assert any(item.category == "character_identity_ambiguous" for item in check_character_identity(state))


def test_relationship_checks_resolve_declared_aliases_and_character_ids(tmp_path):
    state = _state(tmp_path)
    state.add_character(_character())
    state.add_character(_character(id="char_002", full_name="沈舟", gender="male", pronouns="他", aliases=[]))
    state.relationships["r1"] = RelationshipEdge(
        id="r1", source_id="char_001", target_id="char_002", label="enemy",
    )
    chapter = state.create_chapter(1, "重逢")
    chapter.characters_present = ["阿澜", "char_002"]
    assert check_hostile_pairs_co_present(state, 1)
