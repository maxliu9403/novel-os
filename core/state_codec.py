"""Canonical in-memory StoryState snapshot encoding for canon transactions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from document_tree import Binder
from state_manager import (
    ChapterState,
    Character,
    CodexEntry,
    Collection,
    PlotThread,
    RelationshipEdge,
    StoryState,
    StyleProfile,
    TimelineEvent,
)


STATE_PAYLOAD_FIELDS = {
    "metadata",
    "story_bible",
    "characters",
    "codex",
    "relationships",
    "collections",
    "continuity_exemptions",
    "compile_styles",
    "plot_threads",
    "chapters",
    "binder",
    "timeline",
    "style_profile",
    "session_log",
    "last_saved",
}


def blank_story_state(project_path: Path | str) -> StoryState:
    """Create an empty in-memory state without reading or writing project files."""
    state = StoryState.__new__(StoryState)
    state.project_path = Path(project_path)
    state.state_dir = state.project_path / "outputs" / "state"
    state.state_file = state.state_dir / "story_state.json"
    state.metadata = {}
    state.story_bible = {}
    state.characters = {}
    state.codex = {}
    state.relationships = {}
    state.collections = {}
    state.continuity_exemptions = {}
    state.compile_styles = {}
    state.plot_threads = {}
    state.chapters = {}
    state.timeline = []
    state.style_profile = StyleProfile()
    state.binder = Binder()
    state.session_log = []
    return state


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def state_payload(state: StoryState, saved_at: str) -> dict[str, Any]:
    """Return the complete canonical payload used by transaction journals."""
    state.sync_binder()
    return {
        "metadata": state.metadata,
        "story_bible": state.story_bible,
        "characters": {key: item.to_dict() for key, item in state.characters.items()},
        "codex": {key: item.to_dict() for key, item in state.codex.items()},
        "relationships": {
            key: item.to_dict() for key, item in state.relationships.items()
        },
        "collections": {key: item.to_dict() for key, item in state.collections.items()},
        "continuity_exemptions": dict(state.continuity_exemptions),
        "compile_styles": dict(state.compile_styles),
        "plot_threads": {
            key: item.to_dict() for key, item in state.plot_threads.items()
        },
        "chapters": {key: item.to_dict() for key, item in state.chapters.items()},
        "binder": state.binder.to_list(),
        "timeline": [item.to_dict() for item in state.timeline],
        "style_profile": state.style_profile.to_dict(),
        "session_log": list(state.session_log),
        "last_saved": saved_at,
    }


def state_from_payload(payload: Mapping[str, Any]) -> StoryState:
    """Validate and rebuild an in-memory StoryState from a journal snapshot."""
    if not isinstance(payload, Mapping) or set(payload) != STATE_PAYLOAD_FIELDS:
        raise ValueError("state payload has invalid fields")
    try:
        data = json.loads(_json_bytes(payload))
        state = StoryState.__new__(StoryState)
        state.metadata = dict(data["metadata"])
        state.story_bible = dict(data["story_bible"])
        state.characters = {
            key: Character.from_dict(value)
            for key, value in data["characters"].items()
        }
        state.codex = {
            key: CodexEntry.from_dict(value)
            for key, value in data["codex"].items()
        }
        state.relationships = {
            key: RelationshipEdge.from_dict(value)
            for key, value in data["relationships"].items()
        }
        state.collections = {
            key: Collection.from_dict(value)
            for key, value in data["collections"].items()
        }
        state.continuity_exemptions = dict(data["continuity_exemptions"])
        state.compile_styles = dict(data["compile_styles"])
        state.plot_threads = {
            key: PlotThread.from_dict(value)
            for key, value in data["plot_threads"].items()
        }
        state.chapters = {
            int(key): ChapterState.from_dict(value)
            for key, value in data["chapters"].items()
        }
        state.binder = Binder.from_list(data["binder"])
        state.timeline = [TimelineEvent.from_dict(value) for value in data["timeline"]]
        state.style_profile = StyleProfile.from_dict(dict(data["style_profile"]))
        state.session_log = list(data["session_log"])
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"state payload is invalid: {exc}") from exc
    return state


__all__ = [
    "STATE_PAYLOAD_FIELDS",
    "blank_story_state",
    "state_from_payload",
    "state_payload",
]
