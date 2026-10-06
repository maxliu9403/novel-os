"""Source-bound cast coverage for story-readable covers (v9)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cover_models_v2 import CoverBriefV2

STORY_POLICY_VERSION = "cover-story.v1"
STORY_PROFILE = "cover-profiles.v9"


def uses_story_policy(profile: str) -> bool:
    return profile.casefold() == STORY_PROFILE


def _role(value: str) -> str:
    return re.sub(r"[-_\s]+", " ", value.casefold()).strip()


def _named_pressure(name: str, text: str) -> bool:
    # Subject-bound assertions, not a bag of names and conflict keywords. A
    # betrayed spouse or supportive child mentioned nearby is not an opponent.
    subject = rf"(?<!\w){re.escape(name.strip())}(?!\w)"
    action = r"(?:abandons?|betrays?|excludes?|threatens?|evicts?|controls?|blackmails?|conceals?|withholds?)\b"
    return bool(name.strip() and (
        re.search(subject + rf"(?:\s+{action}|['’]s\s+(?:betrayal|abandonment|exclusion|coercion|threat)\b)", text, re.IGNORECASE)
        or re.search(action + rf"\s+{subject}", text, re.IGNORECASE)
    ))


def story_cast_policy(brief: CoverBriefV2, count: int) -> dict[str, Any]:
    """Use explicit narrative roles/relationship edges; never infer kinship from names."""
    characters = brief.principal_characters
    heroes = [
        item.character_id for item in characters
        if re.search(r"\b(protagonist|heroine|hero|lead|main character)\b", _role(item.narrative_role))
        and not re.search(r"\b(antagonist|villain)\b", _role(item.narrative_role))
    ] or [brief.reader_anchor_character.character_id]
    conflict_evidence = "\n".join([
        brief.core_conflict,
        *(f"{link.relationship}. {link.power_balance}. {link.visible_tension}" for link in brief.relationship_map),
    ])
    pressure = [
        item.character_id for item in characters
        if item.character_id not in heroes
        and (
            re.search(r"\b(antagonist|villain|opposing force|opponent)\b", _role(item.narrative_role))
            or _named_pressure(item.name, conflict_evidence)
        )
    ]
    primary_pressure = [
        item.character_id for item in characters if item.character_id in pressure
        and not re.search(r"\b(secondary|minor)\b", _role(item.narrative_role))
    ] or pressure
    known = {item.character_id for item in characters}
    linked = set()
    for link in brief.relationship_map:
        endpoints = {link.from_character_id, link.to_character_id} & known
        if endpoints.intersection(heroes):
            linked.update(endpoints)
    central = set(heroes) | set(pressure) | linked
    relational = len(central) >= 2
    return {
        "version": STORY_POLICY_VERSION,
        "focal_character_ids": heroes,
        "pressure_character_ids": pressure,
        "primary_pressure_character_ids": primary_pressure,
        "central_character_ids": [item.character_id for item in characters if item.character_id in central],
        "minimum_relationship_plans": max(1, count - 1) if relational else 0,
        "minimum_distinct_cast_sets": 2 if len(central) >= 3 else (1 if relational else 0),
        "require_colead_scene": len(heroes) >= 2,
        "require_direct_pressure_scene": bool(pressure),
    }
