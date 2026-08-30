"""Versioned, canon-bound contracts for cinematic cover direction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REAL_PLACE_MARKERS = {
    "new york", "los angeles", "london", "paris", "beijing", "shanghai",
    "tokyo", "singapore", "hong kong", "sydney", "toronto", "chicago",
}
_ASSUMPTION_STATUSES = {"pending_confirmation", "approved"}
COVER_REPAIR_CODES = frozenset({
    "age_mismatch",
    "missing_character",
    "generic_ai_face",
    "weak_story_action",
    "genre_drift",
    "thumbnail_clutter",
    "reader_promise_mismatch",
    "title_failure",
})


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

    def validate_concepts(self, concepts: Any) -> bool:
        if not isinstance(concepts, (list, tuple)) or not 3 <= len(concepts) <= 5:
            raise ValueError("Cover concept count must be between 3 and 5")
        strategies = {str(item.visual_strategy).casefold() for item in concepts}
        if len(strategies) != len(concepts):
            raise ValueError("Cover concepts must use distinct visual strategies")
        for concept in concepts:
            if self.title not in str(concept.generation_prompt):
                raise ValueError("Every cover prompt must contain the exact title")
        return True

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

    def __post_init__(self) -> None:
        for name in (
            "hook_type", "first_glance_subject", "open_question", "identity_anchor",
            "genre_signal", "reader_promise", "target_emotion", "misleading_risk",
            "expected_thumbnail_read",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"VisualHook.{name} is required")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VisualHook":
        return cls(
            hook_type=_text(data.get("hook_type"), "visual_hook.hook_type"),
            first_glance_subject=_text(data.get("first_glance_subject"), "visual_hook.first_glance_subject"),
            open_question=_text(data.get("open_question"), "visual_hook.open_question"),
            identity_anchor=_text(data.get("identity_anchor"), "visual_hook.identity_anchor"),
            genre_signal=_text(data.get("genre_signal"), "visual_hook.genre_signal"),
            reader_promise=_text(data.get("reader_promise"), "visual_hook.reader_promise"),
            target_emotion=_text(data.get("target_emotion"), "visual_hook.target_emotion"),
            misleading_risk=_text(data.get("misleading_risk"), "visual_hook.misleading_risk"),
            expected_thumbnail_read=_text(data.get("expected_thumbnail_read"), "visual_hook.expected_thumbnail_read"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "hook_type": self.hook_type,
            "first_glance_subject": self.first_glance_subject,
            "open_question": self.open_question,
            "identity_anchor": self.identity_anchor,
            "genre_signal": self.genre_signal,
            "reader_promise": self.reader_promise,
            "target_emotion": self.target_emotion,
            "misleading_risk": self.misleading_risk,
            "expected_thumbnail_read": self.expected_thumbnail_read,
        }


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

    def __post_init__(self) -> None:
        for name in (
            "concept_id", "visual_strategy", "focal_character_id", "moment_before",
            "frozen_action", "moment_after", "blocking", "primary_prop", "shot_scale",
            "camera_height", "lens", "depth_plan", "motivated_lighting", "color_script",
            "title_safe_zone",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"CoverScenePlan.{name} is required")
        if not self.cast:
            raise ValueError("CoverScenePlan.cast is required")
        if not self.story_evidence_refs:
            raise ValueError("CoverScenePlan.story_evidence_refs is required")
        if not self.gaze_graph:
            raise ValueError("CoverScenePlan.gaze_graph is required")
        if not self.environment_anchors:
            raise ValueError("CoverScenePlan.environment_anchors is required")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, index: int = 0) -> "CoverScenePlan":
        prefix = f"plans[{index}]"
        def values(name: str) -> tuple[str, ...]:
            return _texts(data.get(name), f"{prefix}.{name}")
        return cls(
            concept_id=_text(data.get("concept_id"), f"{prefix}.concept_id"),
            visual_strategy=_text(data.get("visual_strategy"), f"{prefix}.visual_strategy"),
            story_evidence_refs=values("story_evidence_refs"),
            cast=values("cast"),
            focal_character_id=_text(data.get("focal_character_id"), f"{prefix}.focal_character_id"),
            moment_before=_text(data.get("moment_before"), f"{prefix}.moment_before"),
            frozen_action=_text(data.get("frozen_action"), f"{prefix}.frozen_action"),
            moment_after=_text(data.get("moment_after"), f"{prefix}.moment_after"),
            gaze_graph=values("gaze_graph"),
            blocking=_text(data.get("blocking"), f"{prefix}.blocking"),
            environment_anchors=values("environment_anchors"),
            primary_prop=_text(data.get("primary_prop"), f"{prefix}.primary_prop"),
            shot_scale=_text(data.get("shot_scale"), f"{prefix}.shot_scale"),
            camera_height=_text(data.get("camera_height"), f"{prefix}.camera_height"),
            lens=_text(data.get("lens"), f"{prefix}.lens"),
            depth_plan=_text(data.get("depth_plan"), f"{prefix}.depth_plan"),
            motivated_lighting=_text(data.get("motivated_lighting"), f"{prefix}.motivated_lighting"),
            color_script=_text(data.get("color_script"), f"{prefix}.color_script"),
            title_safe_zone=_text(data.get("title_safe_zone"), f"{prefix}.title_safe_zone"),
            visual_hook=VisualHook.from_dict(
                _mapping(data.get("visual_hook"), f"{prefix}.visual_hook")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "visual_strategy": self.visual_strategy,
            "story_evidence_refs": list(self.story_evidence_refs),
            "cast": list(self.cast),
            "focal_character_id": self.focal_character_id,
            "moment_before": self.moment_before,
            "frozen_action": self.frozen_action,
            "moment_after": self.moment_after,
            "gaze_graph": list(self.gaze_graph),
            "blocking": self.blocking,
            "environment_anchors": list(self.environment_anchors),
            "primary_prop": self.primary_prop,
            "shot_scale": self.shot_scale,
            "camera_height": self.camera_height,
            "lens": self.lens,
            "depth_plan": self.depth_plan,
            "motivated_lighting": self.motivated_lighting,
            "color_script": self.color_script,
            "title_safe_zone": self.title_safe_zone,
            "visual_hook": self.visual_hook.to_dict(),
        }


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
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("ArtDirectionSet.schema_version must be 1")
        if not _SHA256.fullmatch(self.brief_sha256):
            raise ValueError("ArtDirectionSet.brief_sha256 must be a lowercase SHA-256 digest")
        if not 3 <= len(self.plans) <= 5:
            raise ValueError("ArtDirectionSet plans must contain between 3 and 5 items")
        if self.status not in {"awaiting_approval", "approved", "stale", "rejected"}:
            raise ValueError(f"Unknown ArtDirectionSet status '{self.status}'")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, brief_sha256: str | None = None) -> "ArtDirectionSet":
        raw_plans = data.get("plans") or []
        if not isinstance(raw_plans, (list, tuple)):
            raise ValueError("ArtDirectionSet.plans must be a list")
        raw_assumptions = data.get("visual_assumptions") or []
        if not isinstance(raw_assumptions, (list, tuple)):
            raise ValueError("ArtDirectionSet.visual_assumptions must be a list")
        return cls(
            schema_version=int(data.get("schema_version") or 1),
            director_model=_text(data.get("director_model"), "art_direction.director_model"),
            brief_sha256=_sha(
                brief_sha256 or data.get("brief_sha256"), "ArtDirectionSet.brief_sha256"
            ),
            profile_version=_text(data.get("profile_version"), "art_direction.profile_version"),
            plans=tuple(
                CoverScenePlan.from_dict(_mapping(item, "plans"), index=index)
                for index, item in enumerate(raw_plans)
            ),
            visual_assumptions=tuple(
                VisualAssumption.from_dict(_mapping(item, "visual_assumptions"), index=index)
                for index, item in enumerate(raw_assumptions)
            ),
            status=str(data.get("status") or "awaiting_approval"),
            direction_id=str(data.get("direction_id") or "").strip(),
            direction_sha256=str(data.get("direction_sha256") or "").strip(),
            created_at=str(data.get("created_at") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "director_model": self.director_model,
            "brief_sha256": self.brief_sha256,
            "profile_version": self.profile_version,
            "plans": [item.to_dict() for item in self.plans],
            "visual_assumptions": [item.to_dict() for item in self.visual_assumptions],
            "status": self.status,
            "direction_id": self.direction_id,
            "direction_sha256": self.direction_sha256,
            "created_at": self.created_at,
        }

    def content_hash(self) -> str:
        import hashlib
        import json

        payload = dict(self.to_dict())
        payload.pop("direction_sha256", None)
        payload.pop("direction_id", None)
        payload.pop("status", None)
        if not payload.get("created_at"):
            payload.pop("created_at", None)
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class CompiledCoverPrompt:
    text: str
    compiler_version: str
    revision: int = 1
    modules: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityFinding:
    code: str
    severity: str
    message: str
    evidence: str = ""

    def __post_init__(self) -> None:
        if not self.code.strip() or self.severity not in {"blocker", "warning", "info"}:
            raise ValueError("QualityFinding requires a code and a valid severity")

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QualityFinding":
        return cls(
            code=_text(data.get("code"), "quality_finding.code"),
            severity=_text(data.get("severity"), "quality_finding.severity"),
            message=_text(data.get("message"), "quality_finding.message"),
            evidence=_text(data.get("evidence"), "quality_finding.evidence", required=False),
        )


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
    findings: tuple[QualityFinding, ...] = ()
    evaluator_provider: str = ""
    evaluator_model: str = ""
    evaluated_at: str = ""

    _DIMENSIONS: ClassVar[tuple[str, ...]] = (
        "canon_fidelity", "required_cast_coverage", "age_and_environment_fidelity",
        "photorealism", "anatomy_and_physics", "cinematic_storytelling", "genre_emotion",
        "thumbnail_clarity", "hook_promise_alignment", "title_legibility_advisory",
    )

    def __post_init__(self) -> None:
        if self.status not in {"blocked", "human_review_required", "recommended_for_human_review"}:
            raise ValueError(f"Unknown cover quality status '{self.status}'")
        for dimension in self._DIMENSIONS:
            value = getattr(self, dimension)
            if value is not None and not 0 <= value <= 100:
                raise ValueError(f"{dimension} must be between 0 and 100")
        unknown = set(self.repair_codes) - COVER_REPAIR_CODES
        if unknown:
            raise ValueError(f"Unknown cover repair code: {', '.join(sorted(unknown))}")
        if self.blockers and self.status != "blocked":
            raise ValueError("Cover quality blockers require blocked status")
        required = (
            self.canon_fidelity,
            self.required_cast_coverage,
            self.age_and_environment_fidelity,
            self.photorealism,
            self.anatomy_and_physics,
        )
        if self.status == "recommended_for_human_review" and not all(
            score is not None and score >= 80 for score in required
        ):
            raise ValueError("Recommended cover quality requires all five fidelity scores at 80 or above")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverQualityReport":
        findings_raw = data.get("findings") or ()
        if not isinstance(findings_raw, (list, tuple)):
            raise ValueError("cover quality findings must be a list")
        values: dict[str, Any] = {
            dimension: (int(data[dimension]) if data.get(dimension) is not None else None)
            for dimension in cls._DIMENSIONS
        }
        values.update({
            "status": _text(data.get("status"), "quality_report.status"),
            "blockers": _texts(data.get("blockers"), "quality_report.blockers"),
            "repair_codes": _texts(data.get("repair_codes"), "quality_report.repair_codes"),
            "evidence": _texts(data.get("evidence"), "quality_report.evidence"),
            "findings": tuple(QualityFinding.from_dict(item) for item in findings_raw),
            "evaluator_provider": _text(data.get("evaluator_provider"), "quality_report.evaluator_provider", required=False),
            "evaluator_model": _text(data.get("evaluator_model"), "quality_report.evaluator_model", required=False),
            "evaluated_at": _text(data.get("evaluated_at"), "quality_report.evaluated_at", required=False),
        })
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            **{dimension: getattr(self, dimension) for dimension in self._DIMENSIONS},
            "blockers": list(self.blockers),
            "repair_codes": list(self.repair_codes),
            "evidence": list(self.evidence),
            "findings": [item.to_dict() for item in self.findings],
            "evaluator_provider": self.evaluator_provider,
            "evaluator_model": self.evaluator_model,
            "evaluated_at": self.evaluated_at,
        }


@dataclass(frozen=True)
class CoverGenerationAttempt:
    attempt_id: str
    prompt_revision: int
    status: str
    generation_prompt: str
    repair_codes: tuple[str, ...] = ()
    image_sha256: str = ""
    request_id: str = ""
    model: str = ""
    relative_path: str = ""
    media_id: str = ""
    width: int = 0
    height: int = 0
    content_type: str = ""
    safe_request_parameters: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    quality_report: CoverQualityReport | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.attempt_id.strip() or self.prompt_revision < 1 or self.status not in {"ready", "failed"}:
            raise ValueError("Invalid cover generation attempt")
        if not self.generation_prompt.strip():
            raise ValueError("Cover generation attempt prompt is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "prompt_revision": self.prompt_revision,
            "status": self.status,
            "generation_prompt": self.generation_prompt,
            "repair_codes": list(self.repair_codes),
            "image_sha256": self.image_sha256,
            "request_id": self.request_id,
            "model": self.model,
            "relative_path": self.relative_path,
            "media_id": self.media_id,
            "width": self.width,
            "height": self.height,
            "content_type": self.content_type,
            "safe_request_parameters": dict(self.safe_request_parameters),
            "error": self.error,
            "quality_report": self.quality_report.to_dict() if self.quality_report else None,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverGenerationAttempt":
        report = data.get("quality_report")
        return cls(
            attempt_id=_text(data.get("attempt_id"), "generation_attempt.attempt_id"),
            prompt_revision=int(data.get("prompt_revision") or 1),
            status=_text(data.get("status"), "generation_attempt.status"),
            generation_prompt=_text(data.get("generation_prompt"), "generation_attempt.generation_prompt"),
            repair_codes=_texts(data.get("repair_codes"), "generation_attempt.repair_codes"),
            image_sha256=_text(data.get("image_sha256"), "generation_attempt.image_sha256", required=False),
            request_id=_text(data.get("request_id"), "generation_attempt.request_id", required=False),
            model=_text(data.get("model"), "generation_attempt.model", required=False),
            relative_path=_text(data.get("relative_path"), "generation_attempt.relative_path", required=False),
            media_id=_text(data.get("media_id"), "generation_attempt.media_id", required=False),
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
            content_type=_text(data.get("content_type"), "generation_attempt.content_type", required=False),
            safe_request_parameters=dict(data.get("safe_request_parameters") or {}),
            error=_text(data.get("error"), "generation_attempt.error", required=False),
            quality_report=CoverQualityReport.from_dict(report) if isinstance(report, Mapping) else None,
            created_at=_text(data.get("created_at"), "generation_attempt.created_at", required=False),
        )
