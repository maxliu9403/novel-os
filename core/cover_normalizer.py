"""Normalize legacy and v1 cover inputs into the v2 story-facts contract."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from .cover_models import CoverBrief
from .cover_models_v2 import CoverBriefV2


_YAML_FENCE = re.compile(r"```ya?ml\s*\n(?P<body>.*?)\n```", re.IGNORECASE | re.DOTALL)
_EXACT_OCCUPATION_FIELDS = (
    "occupation_and_status",
    "occupation",
    "profession",
    "job_title",
)
_OCCUPATION_FIELDS = (
    *_EXACT_OCCUPATION_FIELDS,
    "public_identity",
)


def _assumption(field: str, proposed_value: str, reason: str, *, critical: bool = False) -> dict[str, Any]:
    return {
        "field": field,
        "proposed_value": proposed_value,
        "reason": reason,
        "status": "pending_confirmation",
        "critical": critical,
    }


def normalize_cover_brief(
    source: CoverBrief | CoverBriefV2 | Mapping[str, Any],
    *,
    source_prompt_sha256: str,
    foundation_sha256: str = "",
) -> CoverBriefV2:
    if isinstance(source, CoverBriefV2):
        return CoverBriefV2.from_dict(
            source.to_dict(),
            source_prompt_sha256=source_prompt_sha256,
            foundation_sha256=foundation_sha256 or source.foundation_sha256,
        )
    if isinstance(source, CoverBrief):
        payload = source.to_dict()
    else:
        payload = dict(source)
    if int(payload.get("schema_version") or 1) == 2:
        return CoverBriefV2.from_dict(
            payload,
            source_prompt_sha256=source_prompt_sha256,
            foundation_sha256=foundation_sha256 or str(payload.get("foundation_sha256") or ""),
        )
    protagonist = dict(payload.get("protagonist") or {})
    visual_identity = str(protagonist.get("visual_identity") or "").strip()
    agency_signal = str(protagonist.get("agency_signal") or "").strip()
    world_signals = [str(value).strip() for value in payload.get("world_signals") or [] if str(value).strip()]
    lived_place = ", ".join(world_signals) or "the approved fictional story setting"
    assumptions = [
        _assumption(
            "principal_characters[0].age",
            "derive from approved story facts",
            "v1 handoff has no structured numerical age or age band",
            critical=True,
        ),
        _assumption(
            "principal_characters[0].occupation_and_status",
            str(protagonist.get("role") or "approved protagonist role"),
            "v1 protagonist role is the only durable identity anchor",
            critical=True,
        ),
        _assumption(
            "lived_environment.primary_spaces",
            lived_place,
            "v1 handoff has no structured lived environment spaces",
            critical=True,
        ),
    ]
    v2_payload: dict[str, Any] = {
        "schema_version": 2,
        "title": payload.get("title"),
        "author": payload.get("author"),
        "language": payload.get("language"),
        "genre": payload.get("genre"),
        "target_audience": payload.get("target_audience"),
        "market_scope": payload.get("market_scope"),
        "core_task": payload.get("core_task"),
        "core_conflict": payload.get("core_conflict"),
        "emotional_promise": payload.get("emotional_promise"),
        "principal_characters": [{
            "character_id": "protagonist",
            "name": "the protagonist",
            "narrative_role": protagonist.get("role") or "protagonist",
            "must_appear": True,
            "age": None,
            "age_band": "",
            "gender_presentation": "",
            "physical_identity": visual_identity,
            "occupation_and_status": str(protagonist.get("role") or ""),
            "daily_wardrobe": visual_identity,
            "lived_environment": lived_place,
            "current_emotional_state": str(payload.get("emotional_promise") or ""),
            "agency_signal": agency_signal,
            "relationships": [],
            "source_refs": ["v1.protagonist"],
        }],
        "relationship_map": [],
        "lived_environment": {
            "era": "approved story era",
            "fictional_place": "approved fictional setting",
            "primary_spaces": [lived_place],
            "economic_signals": [],
            "cultural_signals": [],
            "weather_and_season": "",
            "environment_truths": [],
        },
        "decisive_story_nodes": [{
            "node_id": "decisive_story_node",
            "description": payload.get("decisive_story_node"),
            "evidence_refs": ["v1.decisive_story_node"],
        }],
        "secondary_signals": [{
            "signal_id": "secondary_signal",
            "description": dict(payload.get("secondary_task") or {}).get("visual_signal") or "approved secondary story signal",
            "story_function": dict(payload.get("secondary_task") or {}).get("story_function") or "approved secondary task",
        }],
        "genre_emotion_profile": {
            "primary_genre": payload.get("genre"),
            "submode": "neutral_baseline",
            "emotional_temperature": payload.get("emotional_promise"),
            "desired_viewer_feeling": payload.get("emotional_promise"),
            "relationship_motion": payload.get("relationship_or_power_contrast"),
            "prohibited_shortcuts": [],
        },
        "commercial_visual_goal": {
            "market": payload.get("market_scope"),
            "audience_segment": payload.get("target_audience"),
            "display_context": "mobile_thumbnail",
            "thumbnail_reference_width": 120,
            "thumbnail_reference_height": 180,
            "first_glance_priority": payload.get("core_conflict"),
            "reader_identification": visual_identity or "the approved protagonist",
            "truthful_story_promise": payload.get("emotional_promise"),
        },
        "title_direction": payload.get("title_direction") or {
            "hierarchy": "large",
            "preferred_zone": "top",
            "readability": "mobile_thumbnail",
        },
        "forbidden_elements": payload.get("forbidden_elements") or [],
        "visual_assumptions": assumptions,
    }
    return CoverBriefV2.from_dict(
        v2_payload,
        source_prompt_sha256=source_prompt_sha256,
        foundation_sha256=foundation_sha256,
        allow_pending_required_facts=True,
    )


def normalize_legacy_project(project: Path, prompt_text: str) -> CoverBriefV2:
    """Adapt a legacy project and recover confirmed V2 facts from durable canon."""
    from .cover_handoff import _legacy_cover_brief, _read_json_object

    legacy = _legacy_cover_brief(project, prompt_text)
    normalized = normalize_cover_brief(
        legacy,
        source_prompt_sha256=legacy.source_prompt_sha256,
        foundation_sha256=legacy.foundation_sha256,
    )
    inputs = project / "outputs" / "input"
    foundation = _read_json_object(inputs / "foundation.json")
    state = _read_json_object(project / "outputs" / "state" / "story_state.json")
    return _apply_confirmed_legacy_facts(
        normalized,
        foundation=foundation,
        state=state,
        prompt_text=prompt_text,
    )


def _apply_confirmed_legacy_facts(
    brief: CoverBriefV2,
    *,
    foundation: Mapping[str, Any],
    state: Mapping[str, Any],
    prompt_text: str,
) -> CoverBriefV2:
    payload = brief.to_dict()
    characters = _objects(foundation.get("characters"))
    protagonist = _role_character(characters, "protagonist") or (
        characters[0] if characters else {}
    )
    state_characters = _objects(state.get("characters"))
    state_character = _matching_character(state_characters, protagonist)
    if not protagonist:
        protagonist = _role_character(state_characters, "protagonist") or (
            state_characters[0] if state_characters else {}
        )
        state_character = protagonist

    prompt_facts = _prompt_character_facts(prompt_text, protagonist or state_character)
    character_payload = dict(payload["principal_characters"][0])
    character_id = _text(
        protagonist.get("id"),
        state_character.get("id"),
        character_payload.get("character_id"),
    )
    character_name = _text(
        protagonist.get("name"),
        protagonist.get("full_name"),
        state_character.get("name"),
        state_character.get("full_name"),
        character_payload.get("name"),
    )
    if character_id:
        character_payload["character_id"] = character_id
    if character_name:
        character_payload["name"] = character_name

    age = _valid_age(
        protagonist.get("age"),
        state_character.get("age"),
        prompt_facts.get("age"),
    )
    if age is not None:
        character_payload["age"] = age
        character_payload["age_band"] = ""
        _approve_assumption(
            payload,
            "principal_characters[0].age",
            str(age),
            "confirmed by durable character canon",
        )

    occupation = _first_field(protagonist, _OCCUPATION_FIELDS) or _first_field(
        state_character, _OCCUPATION_FIELDS
    ) or _text(prompt_facts.get("occupation_and_status"))
    if occupation:
        character_payload["occupation_and_status"] = occupation
        _approve_assumption(
            payload,
            "principal_characters[0].occupation_and_status",
            occupation,
            "confirmed by a structured story field",
        )

    setting = _object(foundation.get("setting"))
    if not setting:
        setting = _object(_object(state.get("story_bible")).get("setting"))
    primary_location = _text(setting.get("primary_location"))
    current_location = _text(
        state_character.get("current_location"),
        protagonist.get("current_location"),
    )
    spaces = _dedupe_texts(
        primary_location,
        current_location,
        protagonist.get("lived_environment"),
        prompt_facts.get("lived_environment"),
    )
    lived_environment = dict(payload["lived_environment"])
    if spaces:
        lived_environment["primary_spaces"] = spaces
        lived_environment["fictional_place"] = _text(
            setting.get("primary_location"), spaces[0]
        )
        lived_environment["era"] = _text(
            setting.get("time_period"), lived_environment.get("era")
        )
        world_rules = setting.get("world_rules")
        environment_truths = _dedupe_texts(
            *(world_rules if isinstance(world_rules, (list, tuple)) else ())
        )
        if current_location and _text(state_character.get("arc_stage")).casefold() == "resolution":
            environment_truths.append(
                f"Resolution-state location: {current_location}. Use it only for ending-era scenes."
            )
        lived_environment["environment_truths"] = environment_truths
        character_payload["lived_environment"] = ", ".join(spaces)
        _approve_assumption(
            payload,
            "lived_environment.primary_spaces",
            ", ".join(spaces),
            "confirmed by durable setting and character-location canon",
        )
    emotional_state = _text(state_character.get("emotional_state"))
    if emotional_state:
        character_payload["current_emotional_state"] = emotional_state

    source_refs = list(character_payload.get("source_refs") or [])
    if protagonist:
        source_refs.append("outputs/input/foundation.json:characters")
    if state_character:
        source_refs.append("outputs/state/story_state.json:characters")
    if prompt_facts:
        source_refs.append("outputs/input/prompt.md:structured_yaml")
    character_payload["source_refs"] = _dedupe_texts(*source_refs)
    payload["principal_characters"][0] = character_payload
    payload["lived_environment"] = lived_environment
    return CoverBriefV2.from_dict(
        payload,
        source_prompt_sha256=brief.source_prompt_sha256,
        foundation_sha256=brief.foundation_sha256,
        allow_pending_required_facts=True,
    )


def _prompt_character_facts(
    prompt_text: str,
    character: Mapping[str, Any],
) -> dict[str, Any]:
    documents = _yaml_documents(prompt_text)
    aliases = _character_aliases(character)
    matching_documents = [
        document for document in documents
        if _normalized_key(document.get("id")) in aliases
    ]
    age = _valid_age(*(
        value
        for document in matching_documents
        for value in (document.get("age"), document.get("age_at_opening"))
    ))
    occupation = ""
    for document in matching_documents:
        occupation = _first_field(document, _EXACT_OCCUPATION_FIELDS)
        if occupation:
            break
    if not occupation:
        for document in documents:
            baseline = document.get("financial_baseline")
            if not isinstance(baseline, Mapping):
                continue
            for key, raw in baseline.items():
                if _normalized_key(key) not in aliases or not isinstance(raw, Mapping):
                    continue
                occupation = _first_field(raw, _EXACT_OCCUPATION_FIELDS)
                if occupation:
                    break
            if occupation:
                break
    if not occupation:
        for document in matching_documents:
            occupation = _text(document.get("public_identity"))
            if occupation:
                break
    lived_environment = ""
    for document in matching_documents:
        lived_environment = _text(
            document.get("lived_environment"),
            document.get("current_location"),
            document.get("home"),
        )
        if lived_environment:
            break
    result: dict[str, Any] = {}
    if age is not None:
        result["age"] = age
    if occupation:
        result["occupation_and_status"] = occupation
    if lived_environment:
        result["lived_environment"] = lived_environment
    return result


def _yaml_documents(prompt_text: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for match in _YAML_FENCE.finditer(prompt_text):
        try:
            value = yaml.safe_load(match.group("body"))
        except yaml.YAMLError:
            continue
        if isinstance(value, Mapping):
            documents.append(dict(value))
    return documents


def _approve_assumption(
    payload: dict[str, Any],
    field: str,
    proposed_value: str,
    reason: str,
) -> None:
    assumptions = payload.get("visual_assumptions") or []
    for assumption in assumptions:
        if assumption.get("field") != field:
            continue
        assumption.update({
            "proposed_value": proposed_value,
            "reason": reason,
            "status": "approved",
        })
        return


def _objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        values = value.values()
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        return []
    return [dict(item) for item in values if isinstance(item, Mapping)]


def _object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _role_character(characters: list[dict[str, Any]], role: str) -> dict[str, Any]:
    wanted = role.casefold()
    return next(
        (
            character for character in characters
            if _text(character.get("role")).casefold() == wanted
        ),
        {},
    )


def _matching_character(
    characters: list[dict[str, Any]],
    target: Mapping[str, Any],
) -> dict[str, Any]:
    target_id = _normalized_key(target.get("id"))
    target_name = _normalized_key(target.get("name") or target.get("full_name"))
    for character in characters:
        if target_id and _normalized_key(character.get("id")) == target_id:
            return character
        name = _normalized_key(character.get("name") or character.get("full_name"))
        if target_name and name == target_name:
            return character
    return _role_character(characters, "protagonist")


def _character_aliases(character: Mapping[str, Any]) -> set[str]:
    character_id = _normalized_key(character.get("id"))
    name = _text(character.get("name"), character.get("full_name"))
    normalized_name = _normalized_key(name)
    first_name = _normalized_key(name.split()[0]) if name else ""
    return {value for value in (character_id, normalized_name, first_name) if value}


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def _valid_age(*values: Any) -> int | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            age = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= age <= 120:
            return age
    return None


def _first_field(source: Mapping[str, Any], fields: tuple[str, ...]) -> str:
    return _text(*(source.get(field) for field in fields))


def _dedupe_texts(*values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        folded = text.casefold()
        if text and folded not in seen:
            result.append(text)
            seen.add(folded)
    return result


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""
