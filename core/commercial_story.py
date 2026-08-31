"""Strict, immutable commercial-story contracts approved by the workshop."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
READER_JOBS = frozenset(
    {"recognition", "anger", "pity", "regret", "belonging", "agency", "hope"}
)
LIFE_STAGES = frozenset(
    {
        "pregnancy",
        "active_parenting",
        "caregiving",
        "bereavement",
        "marriage_transition",
        "career_midpoint",
        "empty_nest",
        "illness",
        "retirement_transition",
        "second_start",
    }
)
INVISIBLE_LABOR = frozenset(
    {
        "childcare",
        "eldercare",
        "domestic_management",
        "emotional_management",
        "professional_ghostwork",
        "family_finance",
        "reputation_management",
        "medical_coordination",
        "community_service",
    }
)
SACRED_ASSETS = frozenset(
    {
        "child_safety",
        "professional_identity",
        "home",
        "bodily_autonomy",
        "inheritance_memory",
        "career_work",
        "financial_security",
        "shared_commitment",
        "custody_stability",
        "community_role",
    }
)
RESOURCE_DIMENSIONS = frozenset(
    {"time", "space", "name", "labor", "body", "money", "memory", "relationship", "system", "future"}
)
BENEFICIARY_ROLES = frozenset(
    {
        "affair_partner",
        "favored_child",
        "sibling",
        "parent",
        "colleague",
        "employer",
        "community_insider",
        "institution",
        "secret_family",
        "unknown_third_party",
    }
)
PROOF_TYPES = frozenset(
    {
        "document",
        "timestamp",
        "key_or_access",
        "financial_record",
        "medical_record",
        "message_log",
        "child_behavior",
        "witness",
        "version_history",
        "physical_object",
        "technical_metadata",
    }
)
DEADLINE_TYPES = frozenset(
    {
        "public_event",
        "legal_effective_date",
        "medical_window",
        "financial_close",
        "child_harm_threshold",
        "anniversary",
        "numbered_repeat",
        "contract_renewal",
        "departure_window",
    }
)
AGENCY_SOURCES = frozenset(
    {
        "professional_skill",
        "records_habit",
        "ownership_right",
        "process_knowledge",
        "trusted_ally",
        "legal_knowledge",
        "caregiving_network",
        "community_role",
        "financial_control",
    }
)
ACTION_COSTS = frozenset(
    {
        "housing",
        "income",
        "reputation",
        "custody_stability",
        "relationship_loss",
        "medical_option",
        "career_access",
        "community_belonging",
        "physical_safety",
    }
)
BELONGING_ANCHORS = frozenset(
    {"self", "child", "work", "friend", "community", "family_of_choice", "romance"}
)
RELATIONSHIP_SHAPES = frozenset(
    {
        "spouse_triangle",
        "extended_family_intrusion",
        "parent_child_displacement",
        "workplace_erasure",
        "caregiver_abandonment",
        "community_betrayal",
        "second_chance_romance",
        "intimate_mystery",
        "institutional_adversary",
        "fantasy_bond",
    }
)
FREE_ARC_ACTIONS = frozenset(
    {"recognize", "verify", "document", "test_boundary", "protect", "withdraw", "disclose", "leave", "accept_cost", "counter_move"}
)

_OPEN = "[COMMERCIAL_STORY_JSON]"
_CLOSE = "[/COMMERCIAL_STORY_JSON]"
_BLOCK_RE = re.compile(
    r"\[COMMERCIAL_STORY_JSON\]\s*(.*?)\s*\[/COMMERCIAL_STORY_JSON\]",
    re.DOTALL,
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _exact_fields(data: Mapping[str, Any], expected: set[str], name: str) -> None:
    if not isinstance(data, Mapping) or set(data) != expected:
        raise ValueError(f"{name} has invalid fields")


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _enum(value: Any, allowed: frozenset[str], name: str) -> str:
    value = _required_string(value, name)
    if value not in allowed:
        raise ValueError(f"{name} has invalid value")
    return value


def _unique_strings(
    value: Any,
    *,
    name: str,
    minimum: int,
    maximum: int,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be an array")
    result = tuple(_required_string(item, name) for item in value)
    if not minimum <= len(result) <= maximum or len(set(result)) != len(result):
        raise ValueError(f"{name} must contain {minimum}-{maximum} unique values")
    if allowed is not None and any(item not in allowed for item in result):
        raise ValueError(f"{name} has invalid value")
    return result


@dataclass(frozen=True)
class ReaderContract:
    audience_age_band: str
    life_contexts: tuple[str, ...]
    emotional_jobs: Mapping[str, int]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReaderContract":
        _exact_fields(
            data,
            {"audience_age_band", "life_contexts", "emotional_jobs"},
            "reader_contract",
        )
        jobs = data["emotional_jobs"]
        _exact_fields(jobs, set(READER_JOBS), "emotional_jobs")
        normalized_jobs: dict[str, int] = {}
        for key in sorted(READER_JOBS):
            value = jobs[key]
            if type(value) is not int or not 0 <= value <= 5:
                raise ValueError("emotional_jobs values must be integers from 0 through 5")
            normalized_jobs[key] = value
        if sum(value > 0 for value in normalized_jobs.values()) < 3:
            raise ValueError("emotional_jobs requires at least three active jobs")
        return cls(
            audience_age_band=_required_string(
                data["audience_age_band"], "audience_age_band"
            ),
            life_contexts=_unique_strings(
                data["life_contexts"], name="life_contexts", minimum=1, maximum=5
            ),
            emotional_jobs=MappingProxyType(normalized_jobs),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "audience_age_band": self.audience_age_band,
            "life_contexts": list(self.life_contexts),
            "emotional_jobs": dict(self.emotional_jobs),
        }


@dataclass(frozen=True)
class PremiseEngine:
    protagonist_life_stage: str
    protagonist_desire_beyond_escape: str
    invisible_labor: str
    sacred_asset: str
    boundary_transfer: str
    beneficiary_role: str
    proof_type: str
    deadline_type: str
    agency_source: str
    agency_seeded_in_chapter: int
    action_cost: str
    belonging_anchors: tuple[str, ...]
    relationship_shape: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PremiseEngine":
        fields = {
            "protagonist_life_stage",
            "protagonist_desire_beyond_escape",
            "invisible_labor",
            "sacred_asset",
            "boundary_transfer",
            "beneficiary_role",
            "proof_type",
            "deadline_type",
            "agency_source",
            "agency_seeded_in_chapter",
            "action_cost",
            "belonging_anchors",
            "relationship_shape",
        }
        _exact_fields(data, fields, "premise_engine")
        seeded = data["agency_seeded_in_chapter"]
        if type(seeded) is not int or seeded not in {1, 2}:
            raise ValueError("agency_seeded_in_chapter must be 1 or 2")
        return cls(
            protagonist_life_stage=_enum(
                data["protagonist_life_stage"], LIFE_STAGES, "protagonist_life_stage"
            ),
            protagonist_desire_beyond_escape=_required_string(
                data["protagonist_desire_beyond_escape"],
                "protagonist_desire_beyond_escape",
            ),
            invisible_labor=_enum(data["invisible_labor"], INVISIBLE_LABOR, "invisible_labor"),
            sacred_asset=_enum(data["sacred_asset"], SACRED_ASSETS, "sacred_asset"),
            boundary_transfer=_enum(
                data["boundary_transfer"], RESOURCE_DIMENSIONS, "boundary_transfer"
            ),
            beneficiary_role=_enum(
                data["beneficiary_role"], BENEFICIARY_ROLES, "beneficiary_role"
            ),
            proof_type=_enum(data["proof_type"], PROOF_TYPES, "proof_type"),
            deadline_type=_enum(data["deadline_type"], DEADLINE_TYPES, "deadline_type"),
            agency_source=_enum(data["agency_source"], AGENCY_SOURCES, "agency_source"),
            agency_seeded_in_chapter=seeded,
            action_cost=_enum(data["action_cost"], ACTION_COSTS, "action_cost"),
            belonging_anchors=_unique_strings(
                data["belonging_anchors"],
                name="belonging_anchors",
                minimum=2,
                maximum=4,
                allowed=BELONGING_ANCHORS,
            ),
            relationship_shape=_enum(
                data["relationship_shape"], RELATIONSHIP_SHAPES, "relationship_shape"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protagonist_life_stage": self.protagonist_life_stage,
            "protagonist_desire_beyond_escape": self.protagonist_desire_beyond_escape,
            "invisible_labor": self.invisible_labor,
            "sacred_asset": self.sacred_asset,
            "boundary_transfer": self.boundary_transfer,
            "beneficiary_role": self.beneficiary_role,
            "proof_type": self.proof_type,
            "deadline_type": self.deadline_type,
            "agency_source": self.agency_source,
            "agency_seeded_in_chapter": self.agency_seeded_in_chapter,
            "action_cost": self.action_cost,
            "belonging_anchors": list(self.belonging_anchors),
            "relationship_shape": self.relationship_shape,
        }


@dataclass(frozen=True)
class ConflictStep:
    level: int
    resource_dimension: str
    protagonist_action: str
    observable_consequence: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConflictStep":
        _exact_fields(
            data,
            {"level", "resource_dimension", "protagonist_action", "observable_consequence"},
            "conflict_step",
        )
        level = data["level"]
        if type(level) is not int or level not in range(1, 6):
            raise ValueError("conflict level must be 1 through 5")
        return cls(
            level=level,
            resource_dimension=_enum(
                data["resource_dimension"], RESOURCE_DIMENSIONS, "resource_dimension"
            ),
            protagonist_action=_required_string(data["protagonist_action"], "protagonist_action"),
            observable_consequence=_required_string(
                data["observable_consequence"], "observable_consequence"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "resource_dimension": self.resource_dimension,
            "protagonist_action": self.protagonist_action,
            "observable_consequence": self.observable_consequence,
        }


@dataclass(frozen=True)
class FreeTrialArc:
    chapter_count: int
    recognition_event: str
    pattern_proof: str
    first_boundary_test: str
    local_payoff: str
    irreversible_choice: str
    visible_cost: str
    next_concrete_expectation: str
    action_sequence: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FreeTrialArc":
        fields = {
            "chapter_count",
            "recognition_event",
            "pattern_proof",
            "first_boundary_test",
            "local_payoff",
            "irreversible_choice",
            "visible_cost",
            "next_concrete_expectation",
            "action_sequence",
        }
        _exact_fields(data, fields, "free_trial_arc")
        chapter_count = data["chapter_count"]
        if type(chapter_count) is not int or chapter_count not in {3, 4}:
            raise ValueError("free_trial_arc.chapter_count must be 3 or 4")
        values = {
            name: _required_string(data[name], f"free_trial_arc.{name}")
            for name in fields - {"chapter_count", "action_sequence"}
        }
        return cls(
            chapter_count=chapter_count,
            action_sequence=_unique_strings(
                data["action_sequence"],
                name="action_sequence",
                minimum=3,
                maximum=8,
                allowed=FREE_ARC_ACTIONS,
            ),
            **values,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_count": self.chapter_count,
            "recognition_event": self.recognition_event,
            "pattern_proof": self.pattern_proof,
            "first_boundary_test": self.first_boundary_test,
            "local_payoff": self.local_payoff,
            "irreversible_choice": self.irreversible_choice,
            "visible_cost": self.visible_cost,
            "next_concrete_expectation": self.next_concrete_expectation,
            "action_sequence": list(self.action_sequence),
        }


@dataclass(frozen=True)
class QualityBudgets:
    consecutive_humiliation_scenes_max: int = 2
    identical_hook_type_max: int = 1
    unseeded_rescue_max: int = 0
    child_voice_age_check: bool = True
    institutional_plausibility_check: bool = True
    protagonist_causes_major_turn: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QualityBudgets":
        expected = cls().to_dict()
        _exact_fields(data, set(expected), "quality_budgets")
        if dict(data) != expected:
            raise ValueError("quality_budgets must use the fixed schema-v1 values")
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {
            "consecutive_humiliation_scenes_max": self.consecutive_humiliation_scenes_max,
            "identical_hook_type_max": self.identical_hook_type_max,
            "unseeded_rescue_max": self.unseeded_rescue_max,
            "child_voice_age_check": self.child_voice_age_check,
            "institutional_plausibility_check": self.institutional_plausibility_check,
            "protagonist_causes_major_turn": self.protagonist_causes_major_turn,
        }


@dataclass(frozen=True)
class CommercialStoryContract:
    reader_contract: ReaderContract
    premise_engine: PremiseEngine
    conflict_ladder: tuple[ConflictStep, ...]
    free_trial_arc: FreeTrialArc
    quality_budgets: QualityBudgets
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommercialStoryContract":
        fields = {
            "schema_version",
            "reader_contract",
            "premise_engine",
            "conflict_ladder",
            "free_trial_arc",
            "quality_budgets",
        }
        if not isinstance(data, Mapping):
            raise ValueError("commercial story contract must be an object")
        if "contract_id" in data:
            raise ValueError("contract_id must not be authored in the commercial story block")
        _exact_fields(data, fields, "commercial_story_contract")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("schema_version must be the integer 1")
        ladder_value = data["conflict_ladder"]
        if not isinstance(ladder_value, Sequence) or isinstance(
            ladder_value, (str, bytes)
        ):
            raise ValueError("conflict_ladder must be an array")
        ladder = tuple(ConflictStep.from_dict(item) for item in ladder_value)
        if len(ladder) != 5 or tuple(step.level for step in ladder) != (1, 2, 3, 4, 5):
            raise ValueError("conflict_ladder requires five conflict levels in order")
        return cls(
            reader_contract=ReaderContract.from_dict(data["reader_contract"]),
            premise_engine=PremiseEngine.from_dict(data["premise_engine"]),
            conflict_ladder=ladder,
            free_trial_arc=FreeTrialArc.from_dict(data["free_trial_arc"]),
            quality_budgets=QualityBudgets.from_dict(data["quality_budgets"]),
        )

    @property
    def contract_id(self) -> str:
        digest = hashlib.sha256(_canonical_json(self.to_dict()).encode("utf-8")).hexdigest()
        return f"commercial-story:{digest}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "reader_contract": self.reader_contract.to_dict(),
            "premise_engine": self.premise_engine.to_dict(),
            "conflict_ladder": [item.to_dict() for item in self.conflict_ladder],
            "free_trial_arc": self.free_trial_arc.to_dict(),
            "quality_budgets": self.quality_budgets.to_dict(),
        }


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def parse_commercial_story_block(prompt: str) -> CommercialStoryContract | None:
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    opening_count = prompt.count(_OPEN)
    closing_count = prompt.count(_CLOSE)
    if opening_count == closing_count == 0:
        return None
    matches = _BLOCK_RE.findall(prompt)
    if opening_count != 1 or closing_count != 1 or len(matches) != 1:
        raise ValueError("commercial prompt must contain exactly one complete block")
    try:
        payload = json.loads(matches[0], object_pairs_hook=_strict_object)
    except json.JSONDecodeError as exc:
        raise ValueError("commercial story block must contain valid JSON") from exc
    return CommercialStoryContract.from_dict(payload)


def commercial_story_block(contract: CommercialStoryContract) -> str:
    if not isinstance(contract, CommercialStoryContract):
        raise TypeError("contract must be CommercialStoryContract")
    return (
        f"{_OPEN}\n"
        + json.dumps(contract.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        + f"\n{_CLOSE}"
    )
