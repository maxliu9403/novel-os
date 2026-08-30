"""Versioned, canon-bound contracts for cinematic cover direction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REAL_PLACE_MARKERS = {
    "new york", "los angeles", "london", "paris", "beijing", "shanghai",
    "tokyo", "singapore", "hong kong", "sydney", "toronto", "chicago",
}
_ASSUMPTION_STATUSES = {"pending_confirmation", "approved"}


def _text(value: Any, field_name: str, *, required: bool = True) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"cover_brief_v2.{field_name} is required")
    return result


def _texts(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"cover_brief_v2.{field_name} must be a list")
    return tuple(_text(item, field_name) for item in value if str(item or "").strip())


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"cover_brief_v2.{field_name} must be an object")
    return value


def _sha(value: Any, field_name: str, *, optional: bool = False) -> str:
    result = str(value or "").strip()
    if optional and not result:
        return ""
    if not _SHA256.fullmatch(result):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return result


def _real_place(value: str) -> bool:
    folded = value.casefold()
    return any(marker in folded for marker in _REAL_PLACE_MARKERS)


@dataclass(frozen=True)
class PrincipalCharacter:
    character_id: str
    name: str
    narrative_role: str
    must_appear: bool
    age: int | None
    age_band: str
    gender_presentation: str
    physical_identity: str
    occupation_and_status: str
    daily_wardrobe: str
    lived_environment: str
    current_emotional_state: str
    agency_signal: str
    relationships: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        index: int = 0,
        allow_pending_required_facts: bool = False,
    ) -> "PrincipalCharacter":
        prefix = f"principal_characters[{index}]"
        age_value = data.get("age")
        age: int | None
        if age_value in (None, ""):
            age = None
        else:
            try:
                age = int(age_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{prefix}.age must be an integer") from exc
            if age < 1 or age > 120:
                raise ValueError(f"{prefix}.age must be between 1 and 120")
        age_band = _text(data.get("age_band"), f"{prefix}.age_band", required=False)
        must_appear = bool(data.get("must_appear", True))
        if must_appear and age is None and not age_band and not allow_pending_required_facts:
            raise ValueError(f"{prefix} requires age or age_band")
        occupation = _text(
            data.get("occupation_and_status"),
            f"{prefix}.occupation_and_status",
            required=False,
        )
        wardrobe = _text(data.get("daily_wardrobe"), f"{prefix}.daily_wardrobe", required=False)
        lived = _text(data.get("lived_environment"), f"{prefix}.lived_environment", required=False)
        if must_appear and not occupation and not allow_pending_required_facts:
            raise ValueError(f"{prefix} requires lived identity (occupation_and_status)")
        if must_appear and not (wardrobe or lived) and not allow_pending_required_facts:
            raise ValueError(f"{prefix} requires lived identity (daily_wardrobe or lived_environment)")
        return cls(
            character_id=_text(data.get("character_id"), f"{prefix}.character_id"),
            name=_text(data.get("name"), f"{prefix}.name"),
            narrative_role=_text(data.get("narrative_role"), f"{prefix}.narrative_role"),
            must_appear=must_appear,
            age=age,
            age_band=age_band,
            gender_presentation=_text(
                data.get("gender_presentation"), f"{prefix}.gender_presentation", required=False
            ),
            physical_identity=_text(
                data.get("physical_identity"), f"{prefix}.physical_identity", required=False
            ),
            occupation_and_status=occupation,
            daily_wardrobe=wardrobe,
            lived_environment=lived,
            current_emotional_state=_text(
                data.get("current_emotional_state"), f"{prefix}.current_emotional_state", required=False
            ),
            agency_signal=_text(data.get("agency_signal"), f"{prefix}.agency_signal"),
            relationships=_texts(data.get("relationships"), f"{prefix}.relationships"),
            source_refs=_texts(data.get("source_refs"), f"{prefix}.source_refs"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "name": self.name,
            "narrative_role": self.narrative_role,
            "must_appear": self.must_appear,
            "age": self.age,
            "age_band": self.age_band,
            "gender_presentation": self.gender_presentation,
            "physical_identity": self.physical_identity,
            "occupation_and_status": self.occupation_and_status,
            "daily_wardrobe": self.daily_wardrobe,
            "lived_environment": self.lived_environment,
            "current_emotional_state": self.current_emotional_state,
            "agency_signal": self.agency_signal,
            "relationships": list(self.relationships),
            "source_refs": list(self.source_refs),
        }


@dataclass(frozen=True)
class RelationshipLink:
    from_character_id: str
    to_character_id: str
    relationship: str
    power_balance: str
    visible_tension: str
    shared_risk: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, index: int = 0) -> "RelationshipLink":
        prefix = f"relationship_map[{index}]"
        return cls(
            from_character_id=_text(data.get("from_character_id"), f"{prefix}.from_character_id"),
            to_character_id=_text(data.get("to_character_id"), f"{prefix}.to_character_id"),
            relationship=_text(data.get("relationship"), f"{prefix}.relationship"),
            power_balance=_text(data.get("power_balance"), f"{prefix}.power_balance"),
            visible_tension=_text(data.get("visible_tension"), f"{prefix}.visible_tension"),
            shared_risk=_text(data.get("shared_risk"), f"{prefix}.shared_risk", required=False),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "from_character_id": self.from_character_id,
            "to_character_id": self.to_character_id,
            "relationship": self.relationship,
            "power_balance": self.power_balance,
            "visible_tension": self.visible_tension,
            "shared_risk": self.shared_risk,
        }


@dataclass(frozen=True)
class LivedEnvironment:
    era: str
    fictional_place: str
    primary_spaces: tuple[str, ...]
    economic_signals: tuple[str, ...]
    cultural_signals: tuple[str, ...]
    weather_and_season: str
    environment_truths: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LivedEnvironment":
        place = _text(data.get("fictional_place"), "lived_environment.fictional_place")
        if _real_place(place):
            raise ValueError("cover_brief_v2 lived environment must use fictional places")
        spaces = _texts(data.get("primary_spaces"), "lived_environment.primary_spaces")
        if not spaces:
            raise ValueError("cover_brief_v2.lived_environment.primary_spaces is required")
        return cls(
            era=_text(data.get("era"), "lived_environment.era"),
            fictional_place=place,
            primary_spaces=spaces,
            economic_signals=_texts(data.get("economic_signals"), "lived_environment.economic_signals"),
            cultural_signals=_texts(data.get("cultural_signals"), "lived_environment.cultural_signals"),
            weather_and_season=_text(
                data.get("weather_and_season"), "lived_environment.weather_and_season", required=False
            ),
            environment_truths=_texts(data.get("environment_truths"), "lived_environment.environment_truths"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "era": self.era,
            "fictional_place": self.fictional_place,
            "primary_spaces": list(self.primary_spaces),
            "economic_signals": list(self.economic_signals),
            "cultural_signals": list(self.cultural_signals),
            "weather_and_season": self.weather_and_season,
            "environment_truths": list(self.environment_truths),
        }


@dataclass(frozen=True)
class StoryNode:
    node_id: str
    description: str
    evidence_refs: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, index: int = 0) -> "StoryNode":
        prefix = f"decisive_story_nodes[{index}]"
        return cls(
            node_id=_text(data.get("node_id"), f"{prefix}.node_id"),
            description=_text(data.get("description"), f"{prefix}.description"),
            evidence_refs=_texts(data.get("evidence_refs"), f"{prefix}.evidence_refs"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "description": self.description,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class SecondarySignal:
    signal_id: str
    description: str
    story_function: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, index: int = 0) -> "SecondarySignal":
        prefix = f"secondary_signals[{index}]"
        return cls(
            signal_id=_text(data.get("signal_id"), f"{prefix}.signal_id"),
            description=_text(data.get("description"), f"{prefix}.description"),
            story_function=_text(data.get("story_function"), f"{prefix}.story_function"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "signal_id": self.signal_id,
            "description": self.description,
            "story_function": self.story_function,
        }


@dataclass(frozen=True)
class GenreEmotionProfile:
    primary_genre: str
    submode: str
    emotional_temperature: str
    desired_viewer_feeling: str
    relationship_motion: str
    prohibited_shortcuts: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GenreEmotionProfile":
        return cls(
            primary_genre=_text(data.get("primary_genre"), "genre_emotion_profile.primary_genre"),
            submode=_text(data.get("submode"), "genre_emotion_profile.submode", required=False),
            emotional_temperature=_text(
                data.get("emotional_temperature"), "genre_emotion_profile.emotional_temperature"
            ),
            desired_viewer_feeling=_text(
                data.get("desired_viewer_feeling"), "genre_emotion_profile.desired_viewer_feeling"
            ),
            relationship_motion=_text(
                data.get("relationship_motion"), "genre_emotion_profile.relationship_motion"
            ),
            prohibited_shortcuts=_texts(
                data.get("prohibited_shortcuts"), "genre_emotion_profile.prohibited_shortcuts"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_genre": self.primary_genre,
            "submode": self.submode,
            "emotional_temperature": self.emotional_temperature,
            "desired_viewer_feeling": self.desired_viewer_feeling,
            "relationship_motion": self.relationship_motion,
            "prohibited_shortcuts": list(self.prohibited_shortcuts),
        }


@dataclass(frozen=True)
class CommercialVisualGoal:
    market: str
    audience_segment: str
    display_context: str
    thumbnail_reference_width: int
    thumbnail_reference_height: int
    first_glance_priority: str
    reader_identification: str
    truthful_story_promise: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommercialVisualGoal":
        try:
            width = int(data.get("thumbnail_reference_width") or 0)
            height = int(data.get("thumbnail_reference_height") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("commercial_visual_goal thumbnail dimensions must be integers") from exc
        if width <= 0 or height <= 0:
            raise ValueError("commercial_visual_goal thumbnail dimensions are required")
        return cls(
            market=_text(data.get("market"), "commercial_visual_goal.market"),
            audience_segment=_text(data.get("audience_segment"), "commercial_visual_goal.audience_segment"),
            display_context=_text(data.get("display_context"), "commercial_visual_goal.display_context"),
            thumbnail_reference_width=width,
            thumbnail_reference_height=height,
            first_glance_priority=_text(
                data.get("first_glance_priority"), "commercial_visual_goal.first_glance_priority"
            ),
            reader_identification=_text(
                data.get("reader_identification"), "commercial_visual_goal.reader_identification"
            ),
            truthful_story_promise=_text(
                data.get("truthful_story_promise"), "commercial_visual_goal.truthful_story_promise"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "audience_segment": self.audience_segment,
            "display_context": self.display_context,
            "thumbnail_reference_width": self.thumbnail_reference_width,
            "thumbnail_reference_height": self.thumbnail_reference_height,
            "first_glance_priority": self.first_glance_priority,
            "reader_identification": self.reader_identification,
            "truthful_story_promise": self.truthful_story_promise,
        }


@dataclass(frozen=True)
class VisualAssumption:
    field: str
    proposed_value: str
    reason: str
    status: str = "pending_confirmation"
    critical: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, index: int = 0) -> "VisualAssumption":
        status = _text(data.get("status") or "pending_confirmation", f"visual_assumptions[{index}].status")
        if status not in _ASSUMPTION_STATUSES:
            raise ValueError(f"Unknown visual assumption status '{status}'")
        return cls(
            field=_text(data.get("field"), f"visual_assumptions[{index}].field"),
            proposed_value=_text(data.get("proposed_value"), f"visual_assumptions[{index}].proposed_value"),
            reason=_text(data.get("reason"), f"visual_assumptions[{index}].reason"),
            status=status,
            critical=bool(data.get("critical", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "proposed_value": self.proposed_value,
            "reason": self.reason,
            "status": self.status,
            "critical": self.critical,
        }


@dataclass(frozen=True)
class CoverBriefV2:
    schema_version: int
    title: str
    author: str
    language: str
    genre: str
    target_audience: str
    market_scope: str
    core_task: str
    core_conflict: str
    emotional_promise: str
    principal_characters: tuple[PrincipalCharacter, ...]
    relationship_map: tuple[RelationshipLink, ...]
    lived_environment: LivedEnvironment
    decisive_story_nodes: tuple[StoryNode, ...]
    secondary_signals: tuple[SecondarySignal, ...]
    genre_emotion_profile: GenreEmotionProfile
    commercial_visual_goal: CommercialVisualGoal
    title_direction: dict[str, str]
    forbidden_elements: tuple[str, ...]
    visual_assumptions: tuple[VisualAssumption, ...]
    source_prompt_sha256: str
    foundation_sha256: str = ""

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        source_prompt_sha256: str,
        foundation_sha256: str = "",
        allow_pending_required_facts: bool = False,
    ) -> "CoverBriefV2":
        if int(data.get("schema_version") or 0) != 2:
            raise ValueError("cover_brief_v2.schema_version must be 2")
        characters_raw = data.get("principal_characters")
        if not isinstance(characters_raw, (list, tuple)) or not characters_raw:
            raise ValueError("cover_brief_v2.principal_characters is required")
        characters = tuple(
            PrincipalCharacter.from_dict(
                _mapping(item, "principal_characters"),
                index=index,
                allow_pending_required_facts=allow_pending_required_facts,
            )
            for index, item in enumerate(characters_raw)
        )
        character_ids = [item.character_id for item in characters]
        if len(set(character_ids)) != len(character_ids):
            raise ValueError("cover_brief_v2 principal character ids must be unique")
        relationships_raw = data.get("relationship_map") or []
        if not isinstance(relationships_raw, (list, tuple)):
            raise ValueError("cover_brief_v2.relationship_map must be a list")
        relationships = tuple(
            RelationshipLink.from_dict(_mapping(item, "relationship_map"), index=index)
            for index, item in enumerate(relationships_raw)
        )
        nodes_raw = data.get("decisive_story_nodes") or []
        if not isinstance(nodes_raw, (list, tuple)) or not nodes_raw:
            raise ValueError("cover_brief_v2.decisive_story_nodes is required")
        nodes = tuple(
            StoryNode.from_dict(_mapping(item, "decisive_story_nodes"), index=index)
            for index, item in enumerate(nodes_raw)
        )
        signals_raw = data.get("secondary_signals") or []
        if not isinstance(signals_raw, (list, tuple)):
            raise ValueError("cover_brief_v2.secondary_signals must be a list")
        assumptions_raw = data.get("visual_assumptions") or []
        if not isinstance(assumptions_raw, (list, tuple)):
            raise ValueError("cover_brief_v2.visual_assumptions must be a list")
        title_direction_raw = _mapping(data.get("title_direction") or {}, "title_direction")
        title_direction = {
            str(key): str(value or "").strip()
            for key, value in title_direction_raw.items()
            if str(value or "").strip()
        }
        if not title_direction:
            raise ValueError("cover_brief_v2.title_direction is required")
        return cls(
            schema_version=2,
            title=_text(data.get("title"), "title"),
            author=_text(data.get("author"), "author", required=False),
            language=_text(data.get("language"), "language"),
            genre=_text(data.get("genre"), "genre"),
            target_audience=_text(data.get("target_audience"), "target_audience"),
            market_scope=_text(data.get("market_scope"), "market_scope"),
            core_task=_text(data.get("core_task"), "core_task"),
            core_conflict=_text(data.get("core_conflict"), "core_conflict"),
            emotional_promise=_text(data.get("emotional_promise"), "emotional_promise"),
            principal_characters=characters,
            relationship_map=relationships,
            lived_environment=LivedEnvironment.from_dict(
                _mapping(data.get("lived_environment"), "lived_environment")
            ),
            decisive_story_nodes=nodes,
            secondary_signals=tuple(
                SecondarySignal.from_dict(_mapping(item, "secondary_signals"), index=index)
                for index, item in enumerate(signals_raw)
            ),
            genre_emotion_profile=GenreEmotionProfile.from_dict(
                _mapping(data.get("genre_emotion_profile"), "genre_emotion_profile")
            ),
            commercial_visual_goal=CommercialVisualGoal.from_dict(
                _mapping(data.get("commercial_visual_goal"), "commercial_visual_goal")
            ),
            title_direction=title_direction,
            forbidden_elements=_texts(data.get("forbidden_elements"), "forbidden_elements"),
            visual_assumptions=tuple(
                VisualAssumption.from_dict(_mapping(item, "visual_assumptions"), index=index)
                for index, item in enumerate(assumptions_raw)
            ),
            source_prompt_sha256=_sha(source_prompt_sha256, "source_prompt_sha256"),
            foundation_sha256=_sha(foundation_sha256, "foundation_sha256", optional=True),
        )

    @property
    def required_characters(self) -> tuple[PrincipalCharacter, ...]:
        return tuple(item for item in self.principal_characters if item.must_appear)

    def pending_critical_assumptions(self) -> tuple[VisualAssumption, ...]:
        return tuple(
            item for item in self.visual_assumptions
            if item.critical and item.status == "pending_confirmation"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "author": self.author,
            "language": self.language,
            "genre": self.genre,
            "target_audience": self.target_audience,
            "market_scope": self.market_scope,
            "core_task": self.core_task,
            "core_conflict": self.core_conflict,
            "emotional_promise": self.emotional_promise,
            "principal_characters": [item.to_dict() for item in self.principal_characters],
            "relationship_map": [item.to_dict() for item in self.relationship_map],
            "lived_environment": self.lived_environment.to_dict(),
            "decisive_story_nodes": [item.to_dict() for item in self.decisive_story_nodes],
            "secondary_signals": [item.to_dict() for item in self.secondary_signals],
            "genre_emotion_profile": self.genre_emotion_profile.to_dict(),
            "commercial_visual_goal": self.commercial_visual_goal.to_dict(),
            "title_direction": dict(self.title_direction),
            "forbidden_elements": list(self.forbidden_elements),
            "visual_assumptions": [item.to_dict() for item in self.visual_assumptions],
            "source_prompt_sha256": self.source_prompt_sha256,
            "foundation_sha256": self.foundation_sha256,
        }


@dataclass(frozen=True)
class VisualHook:
    hook_type: str
    first_glance_subject: str
    open_question: str
    identity_anchor: str
    genre_signal: str
    reader_promise: str
    target_emotion: str
    misleading_risk: str
    expected_thumbnail_read: str


@dataclass(frozen=True)
class CoverScenePlan:
    concept_id: str
    visual_strategy: str
    story_evidence_refs: tuple[str, ...]
    cast: tuple[str, ...]
    focal_character_id: str
    moment_before: str
    frozen_action: str
    moment_after: str
    gaze_graph: tuple[str, ...]
    blocking: str
    environment_anchors: tuple[str, ...]
    primary_prop: str
    shot_scale: str
    camera_height: str
    lens: str
    depth_plan: str
    motivated_lighting: str
    color_script: str
    title_safe_zone: str
    visual_hook: VisualHook


@dataclass(frozen=True)
class ArtDirectionSet:
    schema_version: int
    director_model: str
    brief_sha256: str
    profile_version: str
    plans: tuple[CoverScenePlan, ...]
    visual_assumptions: tuple[VisualAssumption, ...] = ()
    status: str = "awaiting_approval"
    direction_id: str = ""
    direction_sha256: str = ""


@dataclass(frozen=True)
class CompiledCoverPrompt:
    text: str
    compiler_version: str
    revision: int = 1
    modules: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CoverQualityReport:
    status: str
    canon_fidelity: int | None = None
    required_cast_coverage: int | None = None
    age_and_environment_fidelity: int | None = None
    photorealism: int | None = None
    anatomy_and_physics: int | None = None
    cinematic_storytelling: int | None = None
    genre_emotion: int | None = None
    thumbnail_clarity: int | None = None
    hook_promise_alignment: int | None = None
    title_legibility_advisory: int | None = None
    blockers: tuple[str, ...] = ()
    repair_codes: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
