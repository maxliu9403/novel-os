"""Normalize legacy and v1 cover inputs into the v2 story-facts contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from .cover_models import CoverBrief
from .cover_models_v2 import CoverBriefV2
from .cover_prose_facts import biography_identity


_STRUCTURED_FENCE = re.compile(
    r"```(?P<format>ya?ml|json)\s*\n(?P<body>.*?)\n```", re.IGNORECASE | re.DOTALL
)
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
_NARRATIVE_RELATIONSHIP_ROLES = (
    "antagonist",
    "opposing force",
    "co-protagonist",
    "co protagonist",
    "core_partner",
    "core partner",
    "deuteragonist",
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
    characters = _keyed_objects(foundation.get("characters"))
    protagonist = _role_character(characters, "protagonist") or (
        characters[0] if characters else {}
    )
    state_characters = _keyed_objects(state.get("characters"))
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
    else:
        age_band = _text(
            protagonist.get("age_band"),
            state_character.get("age_band"),
            prompt_facts.get("age_band"),
        )
        if age_band:
            character_payload["age_band"] = age_band
            _approve_assumption(
                payload,
                "principal_characters[0].age",
                age_band,
                "confirmed by a structured character age band",
            )

    canon_occupation = _first_field(protagonist, _OCCUPATION_FIELDS) or _first_field(
        state_character, _OCCUPATION_FIELDS
    )
    occupation = canon_occupation or _text(prompt_facts.get("occupation_and_status"))
    if occupation:
        character_payload["occupation_and_status"] = occupation
        _approve_assumption(
            payload,
            "principal_characters[0].occupation_and_status",
            occupation,
            (
                f"quoted from explicit character biography at {prompt_facts['occupation_source_ref']}"
                if not canon_occupation and prompt_facts.get("occupation_source_ref")
                else "confirmed by a structured story field"
            ),
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
        source_refs.extend(prompt_facts.get("source_refs") or [])
    character_payload["source_refs"] = _dedupe_texts(*source_refs)
    principal_characters = [character_payload]
    for foundation_character, durable_state_character in _durable_character_pairs(
        characters, state_characters,
    ):
        if _same_character(
            foundation_character or durable_state_character,
            protagonist or state_character,
        ):
            continue
        optional_character = _optional_character_payload(
            foundation_character,
            durable_state_character,
            prompt_text=prompt_text,
        )
        if optional_character:
            principal_characters.append(optional_character)
    payload["principal_characters"] = principal_characters
    payload["relationship_map"] = _legacy_relationship_map(
        foundation=foundation,
        state=state,
        prompt_text=prompt_text,
        character_ids={item["character_id"] for item in principal_characters},
    )
    payload["lived_environment"] = lived_environment
    return CoverBriefV2.from_dict(
        payload,
        source_prompt_sha256=brief.source_prompt_sha256,
        foundation_sha256=brief.foundation_sha256,
        allow_pending_required_facts=True,
    )


def _optional_character_payload(
    foundation_character: Mapping[str, Any],
    state_character: Mapping[str, Any],
    *,
    prompt_text: str,
) -> dict[str, Any]:
    durable_character = foundation_character or state_character
    character_id = _text(
        foundation_character.get("id"), state_character.get("id"),
    )
    character_name = _text(
        foundation_character.get("name"),
        foundation_character.get("full_name"),
        state_character.get("name"),
        state_character.get("full_name"),
    )
    narrative_role = _text(
        foundation_character.get("role"), state_character.get("role"),
    )
    if not character_id or not character_name or not narrative_role:
        return {}

    prompt_facts = _prompt_character_facts(
        prompt_text, durable_character, allow_current_location=False,
    )
    age = _valid_age(
        foundation_character.get("age"),
        state_character.get("age"),
        prompt_facts.get("age"),
    )
    age_band = "" if age is not None else _text(
        foundation_character.get("age_band"),
        state_character.get("age_band"),
        prompt_facts.get("age_band"),
    )
    occupation = (
        _first_field(foundation_character, _OCCUPATION_FIELDS)
        or _first_field(state_character, _OCCUPATION_FIELDS)
        or _text(prompt_facts.get("occupation_and_status"))
    )
    lived_environment = _text(
        foundation_character.get("lived_environment"),
        prompt_facts.get("lived_environment"),
        state_character.get("lived_environment"),
    )
    source_refs: list[str] = []
    if foundation_character:
        source_refs.append(f"outputs/input/foundation.json:characters.{character_id}")
    if state_character:
        source_refs.append(f"outputs/state/story_state.json:characters.{character_id}")
    source_refs.extend(prompt_facts.get("source_refs") or [])
    return {
        "character_id": character_id,
        "name": character_name,
        "narrative_role": narrative_role,
        "must_appear": False,
        "age": age,
        "age_band": age_band,
        "gender_presentation": _text(
            foundation_character.get("gender_presentation"),
            state_character.get("gender_presentation"),
        ),
        "physical_identity": _text(
            foundation_character.get("physical_identity"),
            foundation_character.get("physical_description"),
            state_character.get("physical_identity"),
            state_character.get("physical_description"),
        ),
        "occupation_and_status": occupation,
        "daily_wardrobe": _text(
            foundation_character.get("daily_wardrobe"),
            state_character.get("daily_wardrobe"),
        ),
        "lived_environment": lived_environment,
        # State files often describe the end of the completed manuscript. Do
        # not turn that resolution emotion into opening cover characterization.
        "current_emotional_state": _text(
            foundation_character.get("current_emotional_state"),
            foundation_character.get("emotional_state"),
        ),
        "agency_signal": _text(
            foundation_character.get("external_goal"),
            state_character.get("external_goal"),
            foundation_character.get("strength"),
            state_character.get("strength"),
            foundation_character.get("arc"),
            state_character.get("notes"),
            narrative_role,
        ),
        "relationships": _character_relationships(
            foundation_character.get("relationships"),
            state_character.get("relationships"),
        ),
        "source_refs": _dedupe_texts(*source_refs),
    }


def _prompt_character_facts(
    prompt_text: str,
    character: Mapping[str, Any],
    *,
    allow_current_location: bool = True,
) -> dict[str, Any]:
    documents = _structured_documents(prompt_text)
    nested_character_documents: list[dict[str, Any]] = []
    for document in documents:
        for key in ("characters", "principal_characters", "supporting_characters"):
            value = document.get(key)
            if isinstance(value, Mapping):
                nested_character_documents.extend(
                    dict(item) for item in value.values() if isinstance(item, Mapping)
                )
            elif isinstance(value, (list, tuple)):
                nested_character_documents.extend(
                    dict(item) for item in value if isinstance(item, Mapping)
                )
    documents = [*documents, *nested_character_documents]
    aliases = _character_aliases(character)
    matching_documents = [
        document for document in documents
        if _document_matches_character(document, character)
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
            document.get("current_location") if allow_current_location else "",
            document.get("home"),
        )
        if lived_environment:
            break
    result: dict[str, Any] = {}
    if age is not None:
        result["age"] = age
    else:
        age_band = _text(*(document.get("age_band") for document in matching_documents))
        if age_band:
            result["age_band"] = age_band
    if occupation:
        result["occupation_and_status"] = occupation
    if lived_environment:
        result["lived_environment"] = lived_environment
    source_refs = ["outputs/input/prompt.md:structured_character_facts"] if result else []
    if not occupation:
        recovered = biography_identity(prompt_text, character)
        if recovered:
            result["occupation_and_status"], result["occupation_source_ref"] = recovered
            source_refs.append(recovered[1])
    if source_refs:
        result["source_refs"] = source_refs
    return result


def _document_matches_character(
    document: Mapping[str, Any],
    character: Mapping[str, Any],
) -> bool:
    character_id = _normalized_key(character.get("id"))
    document_id = _normalized_key(document.get("id") or document.get("character_id"))
    if character_id and document_id:
        return character_id == document_id
    character_name = _normalized_key(character.get("name") or character.get("full_name"))
    document_name = _normalized_key(document.get("name") or document.get("full_name"))
    return bool(character_name and document_name and character_name == document_name)


def _legacy_relationship_map(
    *,
    foundation: Mapping[str, Any],
    state: Mapping[str, Any],
    prompt_text: str,
    character_ids: set[str],
) -> list[dict[str, str]]:
    prompt_records: list[Mapping[str, Any]] = []
    for document in _structured_documents(prompt_text):
        relationship_map = document.get("relationship_map")
        if isinstance(relationship_map, (list, tuple)):
            prompt_records.extend(
                item for item in relationship_map if isinstance(item, Mapping)
            )
        if document.get("from_character_id") and document.get("to_character_id"):
            prompt_records.append(document)
    record_groups = (
        prompt_records,
        _objects(foundation.get("relationships")),
        _keyed_objects(state.get("relationships")),
    )
    result: list[dict[str, str]] = []
    seen: set[frozenset[str]] = set()
    for records in record_groups:
        for record in records:
            normalized = _legacy_relationship(record, character_ids)
            if not normalized:
                continue
            key = frozenset((
                normalized["from_character_id"], normalized["to_character_id"],
            ))
            if key not in seen:
                result.append(normalized)
                seen.add(key)
    if result:
        return result
    return _narrative_role_relationships(
        _keyed_objects(foundation.get("characters")),
        _keyed_objects(state.get("characters")),
        character_ids,
    )


def _narrative_role_relationships(
    foundation_characters: list[dict[str, Any]],
    state_characters: list[dict[str, Any]],
    character_ids: set[str],
) -> list[dict[str, str]]:
    pairs = _durable_character_pairs(foundation_characters, state_characters)
    lead_pair = next(
        (
            pair for pair in pairs
            if _text(pair[0].get("role"), pair[1].get("role")).casefold()
            == "protagonist"
        ),
        pairs[0] if pairs else ({}, {}),
    )
    lead_id = _text(lead_pair[0].get("id"), lead_pair[1].get("id"))
    if not lead_id or lead_id not in character_ids:
        return []
    result: list[dict[str, str]] = []
    for foundation_character, state_character in pairs:
        character_id = _text(
            foundation_character.get("id"), state_character.get("id"),
        )
        if not character_id or character_id == lead_id or character_id not in character_ids:
            continue
        role = _text(
            foundation_character.get("role"), state_character.get("role"),
        )
        folded_role = role.casefold()
        if not any(marker in folded_role for marker in _NARRATIVE_RELATIONSHIP_ROLES):
            continue
        goal = _text(
            foundation_character.get("external_goal"),
            state_character.get("external_goal"),
        )
        arc = _text(
            foundation_character.get("arc"),
            state_character.get("arc"),
            state_character.get("notes"),
        )
        if not goal and not arc:
            continue
        result.append({
            "from_character_id": lead_id,
            "to_character_id": character_id,
            "relationship": role,
            "power_balance": goal or arc,
            "visible_tension": arc or goal,
            "shared_risk": "",
        })
    return result


def _legacy_relationship(
    record: Mapping[str, Any],
    character_ids: set[str],
) -> dict[str, str]:
    linked_characters = record.get("characters")
    if not isinstance(linked_characters, (list, tuple)):
        linked_characters = ()
    from_id = _text(
        record.get("from_character_id"),
        record.get("source_id"),
        linked_characters[0] if len(linked_characters) > 0 else "",
    )
    to_id = _text(
        record.get("to_character_id"),
        record.get("target_id"),
        linked_characters[1] if len(linked_characters) > 1 else "",
    )
    opening_state = _text(record.get("opening_state"), record.get("initial_state"))
    relationship = _text(
        record.get("relationship"), record.get("label"), opening_state,
    )
    power_balance = _text(
        record.get("power_balance"),
        record.get("leverage"),
        record.get("boundary"),
        opening_state,
        record.get("status"),
    )
    visible_tension = _text(
        record.get("visible_tension"),
        opening_state,
        record.get("notes"),
        relationship,
    )
    if (
        not from_id or not to_id
        or from_id not in character_ids or to_id not in character_ids
        or not relationship or not power_balance or not visible_tension
    ):
        return {}
    return {
        "from_character_id": from_id,
        "to_character_id": to_id,
        "relationship": relationship,
        "power_balance": power_balance,
        "visible_tension": visible_tension,
        "shared_risk": _text(record.get("shared_risk"), record.get("final_state")),
    }


def _structured_documents(prompt_text: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for match in _STRUCTURED_FENCE.finditer(prompt_text):
        try:
            value = (
                json.loads(match.group("body"))
                if match.group("format").casefold() == "json"
                else yaml.safe_load(match.group("body"))
            )
        except (json.JSONDecodeError, yaml.YAMLError):
            continue
        if isinstance(value, Mapping):
            documents.append(dict(value))
        elif isinstance(value, list):
            documents.extend(dict(item) for item in value if isinstance(item, Mapping))
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


def _keyed_objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        result: list[dict[str, Any]] = []
        for key, item in value.items():
            if not isinstance(item, Mapping):
                continue
            payload = dict(item)
            if not _text(payload.get("id")):
                payload["id"] = str(key)
            result.append(payload)
        return result
    return _objects(value)


def _durable_character_pairs(
    foundation_characters: list[dict[str, Any]],
    state_characters: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    matched_state_indexes: set[int] = set()
    for foundation_character in foundation_characters:
        state_character: dict[str, Any] = {}
        for index, candidate in enumerate(state_characters):
            if index not in matched_state_indexes and _same_character(
                foundation_character, candidate,
            ):
                state_character = candidate
                matched_state_indexes.add(index)
                break
        result.append((foundation_character, state_character))
    result.extend(
        ({}, character)
        for index, character in enumerate(state_characters)
        if index not in matched_state_indexes
    )
    return result


def _same_character(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_id = _normalized_key(left.get("id"))
    right_id = _normalized_key(right.get("id"))
    if left_id and right_id:
        return left_id == right_id
    left_name = _normalized_key(left.get("name") or left.get("full_name"))
    right_name = _normalized_key(right.get("name") or right.get("full_name"))
    return bool(left_name and right_name and left_name == right_name)


def _character_relationships(*values: Any) -> list[str]:
    relationships: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            relationships.extend(str(item) for item in value.values())
        elif isinstance(value, (list, tuple)):
            relationships.extend(str(item) for item in value)
    return _dedupe_texts(*relationships)


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
    return {}


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
