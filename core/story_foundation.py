"""Validated hydration of an Architect story foundation into StoryState."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from commercial_story import CommercialStoryContract
from state_manager import Character, PlotThread, StoryState


def apply_story_foundation(
    state: StoryState,
    foundation: Mapping[str, Any],
    target_words: int,
    *,
    preserve_completed_chapters: bool = False,
) -> None:
    """Apply the structured foundation without performing persistence."""
    if not isinstance(foundation, Mapping):
        raise ValueError("story foundation must be an object")
    characters = foundation.get("characters")
    plot_threads = foundation.get("plot_threads")
    chapters = foundation.get("chapters")
    if not isinstance(characters, list):
        raise ValueError("story foundation characters must be a list")
    if not isinstance(plot_threads, list):
        raise ValueError("story foundation plot_threads must be a list")
    if not isinstance(chapters, list):
        raise ValueError("story foundation chapters must be a list")

    if foundation.get("title") and state.metadata.get("title") in (None, "", "Untitled"):
        state.set_metadata("title", str(foundation["title"]))
    if foundation.get("premise"):
        state.set_metadata("premise", str(foundation["premise"]))
        state.update_story_bible("premise", str(foundation["premise"]))
    state.update_story_bible("themes", list(foundation.get("themes") or []))
    state.update_story_bible("setting", dict(foundation.get("setting") or {}))
    commercial_payload = foundation.get("commercial_story_contract")
    commercial_id = foundation.get("commercial_story_contract_id")
    if commercial_payload is not None or commercial_id is not None:
        try:
            commercial_contract = CommercialStoryContract.from_dict(commercial_payload)
        except (TypeError, ValueError) as exc:
            raise ValueError("story foundation commercial story contract is invalid") from exc
        if commercial_id != commercial_contract.contract_id:
            raise ValueError("story foundation commercial story contract id is invalid")
        state.set_metadata(
            "commercial_story_contract_id", commercial_contract.contract_id
        )
        state.update_story_bible(
            "commercial_story_contract", commercial_contract.to_dict()
        )

    hydrated_characters: dict[str, Character] = {}
    for index, raw in enumerate(characters, start=1):
        if not isinstance(raw, Mapping) or not str(raw.get("name") or "").strip():
            raise ValueError(f"story foundation character {index} must have a name")
        char_id = str(raw.get("id") or f"char_{index:03d}").strip()
        if not char_id or char_id in hydrated_characters:
            raise ValueError(f"story foundation character id is invalid or duplicated: {char_id!r}")
        age = raw.get("age")
        try:
            age = int(age) if age is not None else None
        except (TypeError, ValueError):
            age = None
        hydrated_characters[char_id] = Character(
            id=char_id,
            full_name=str(raw["name"]).strip(),
            role=str(raw.get("role") or "supporting"),
            age=age,
            physical_description=str(raw.get("physical_description") or ""),
            internal_desire=str(raw.get("internal_desire") or ""),
            external_goal=str(raw.get("external_goal") or ""),
            fear=str(raw.get("fear") or ""),
            weakness=str(raw.get("weakness") or ""),
            strength=str(raw.get("strength") or ""),
            secret=str(raw.get("secret") or ""),
            notes=str(raw.get("arc") or raw.get("notes") or ""),
        )
    state.characters = hydrated_characters

    hydrated_threads: dict[str, PlotThread] = {}
    for index, raw in enumerate(plot_threads, start=1):
        if not isinstance(raw, Mapping) or not str(raw.get("name") or "").strip():
            raise ValueError(f"story foundation plot thread {index} must have a name")
        thread_id = str(raw.get("id") or f"plot_{index:03d}").strip()
        if not thread_id or thread_id in hydrated_threads:
            raise ValueError(f"story foundation plot thread id is invalid or duplicated: {thread_id!r}")
        try:
            priority = max(1, min(5, int(raw.get("priority", 3))))
        except (TypeError, ValueError):
            priority = 3
        resolution = raw.get("resolution_chapter")
        try:
            resolution = int(resolution) if resolution is not None else None
        except (TypeError, ValueError):
            resolution = None
        status = str(raw.get("status") or "active").strip().lower()
        if status not in {"active", "resolved", "abandoned", "foreshadowed"}:
            status = "active"
        try:
            last_updated = int(raw.get("last_updated_chapter") or 0)
        except (TypeError, ValueError):
            last_updated = 0
        hydrated_threads[thread_id] = PlotThread(
            id=thread_id,
            name=str(raw["name"]).strip(),
            description=str(raw.get("description") or ""),
            thread_type=str(raw.get("type") or "main"),
            status=status,
            priority=priority,
            start_chapter=int(raw.get("start_chapter") or 1),
            target_resolution_chapter=resolution,
            last_updated_chapter=last_updated,
        )
    state.plot_threads = hydrated_threads

    default_target = max(1, int(target_words) // max(1, len(chapters)))
    seen_chapters: set[int] = set()
    for raw in chapters:
        if not isinstance(raw, Mapping):
            raise ValueError("story foundation chapter must be an object")
        number = int(raw["number"])
        if number < 1 or number in seen_chapters:
            raise ValueError(f"story foundation chapter number is invalid or duplicated: {number}")
        seen_chapters.add(number)
        chapter = state.get_chapter(number) or state.create_chapter(number)
        was_complete = chapter.status == "complete"
        chapter.title = str(raw.get("title") or f"Chapter {number}")
        chapter.pov_character = str(raw.get("pov") or "")
        summary = str(raw.get("summary") or "").strip()
        if summary and summary not in chapter.plot_advances:
            chapter.plot_advances.append(summary)
        try:
            chapter.target_word_count = max(1, int(raw.get("target_words") or default_target))
        except (TypeError, ValueError):
            chapter.target_word_count = default_target
        if not (preserve_completed_chapters and was_complete):
            chapter.status = "planned"

    style = foundation.get("style") or {}
    if isinstance(style, Mapping):
        for source, target in (
            ("tone", "tone"),
            ("pov", "point_of_view"),
            ("tense", "tense"),
            ("prose_style", "prose_style"),
        ):
            if style.get(source):
                setattr(state.style_profile, target, str(style[source]))


__all__ = ["apply_story_foundation"]
