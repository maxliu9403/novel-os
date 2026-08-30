"""Normalize legacy and v1 cover inputs into the v2 story-facts contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .cover_models import CoverBrief
from .cover_models_v2 import CoverBriefV2


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
    """Adapt the existing legacy reader without duplicating its extraction rules."""
    from .cover_handoff import _legacy_cover_brief

    legacy = _legacy_cover_brief(project, prompt_text)
    return normalize_cover_brief(
        legacy,
        source_prompt_sha256=legacy.source_prompt_sha256,
        foundation_sha256=legacy.foundation_sha256,
    )
