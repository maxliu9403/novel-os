"""Immutable, versioned author and story quality contracts."""

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Dict, Tuple


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a nonblank string")
    return value.strip()


def _string_tuple(values: Sequence[str], field_name: str) -> Tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError(f"{field_name} must be a sequence of strings")

    normalized = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must contain only strings")
        stripped = value.strip()
        if stripped:
            normalized.append(stripped)
    return tuple(normalized)


def _schema_version(value: Any) -> int:
    if type(value) is not int or value != 1:
        raise ValueError("schema_version must be the integer 1")
    return value


def _chapter_schema_version(value: Any) -> int:
    if type(value) is not int or value not in {1, 2}:
        raise ValueError("schema_version must be the integer 1 or 2")
    return value


class _ContractValue:
    def to_dict(self) -> Dict[str, Any]:
        raise NotImplementedError

    @property
    def contract_id(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"contract:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class AuthorIntent(_ContractValue):
    premise: str
    target_audience: str
    language: str = "en-US"
    content_boundaries: Tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _schema_version(self.schema_version)
        object.__setattr__(self, "premise", _required_string(self.premise, "premise"))
        object.__setattr__(
            self,
            "target_audience",
            _required_string(self.target_audience, "target_audience"),
        )
        object.__setattr__(self, "language", _required_string(self.language, "language"))
        object.__setattr__(
            self,
            "content_boundaries",
            _string_tuple(self.content_boundaries, "content_boundaries"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "premise": self.premise,
            "target_audience": self.target_audience,
            "language": self.language,
            "content_boundaries": list(self.content_boundaries),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuthorIntent":
        schema_version = _schema_version(data.get("schema_version", 1))
        return cls(
            premise=data["premise"],
            target_audience=data["target_audience"],
            language=data.get("language", "en-US"),
            content_boundaries=data.get("content_boundaries", ()),
            schema_version=schema_version,
        )


@dataclass(frozen=True)
class StoryContract(_ContractValue):
    title: str
    genre: str
    intent: AuthorIntent
    themes: Tuple[str, ...] = ()
    non_negotiables: Tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _schema_version(self.schema_version)
        object.__setattr__(self, "title", _required_string(self.title, "title"))
        object.__setattr__(self, "genre", _required_string(self.genre, "genre"))
        if not isinstance(self.intent, AuthorIntent):
            raise ValueError("intent must be an AuthorIntent")
        object.__setattr__(self, "themes", _string_tuple(self.themes, "themes"))
        object.__setattr__(
            self,
            "non_negotiables",
            _string_tuple(self.non_negotiables, "non_negotiables"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "genre": self.genre,
            "intent": self.intent.to_dict(),
            "themes": list(self.themes),
            "non_negotiables": list(self.non_negotiables),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StoryContract":
        schema_version = _schema_version(data.get("schema_version", 1))
        return cls(
            title=data["title"],
            genre=data["genre"],
            intent=AuthorIntent.from_dict(data["intent"]),
            themes=data.get("themes", ()),
            non_negotiables=data.get("non_negotiables", ()),
            schema_version=schema_version,
        )


@dataclass(frozen=True)
class ChapterContract(_ContractValue):
    chapter: int
    goal: str
    obstacle: str
    active_choice: str
    cost: str
    irreversible_change: str
    local_payoff: str
    ending_pressure: str
    preserve_facts: Tuple[str, ...] = ()
    allowed_knowledge: Tuple[str, ...] = ()
    world_event_ids: Tuple[str, ...] = ()
    reader_jobs: Tuple[str, ...] = ()
    belonging_anchors: Tuple[str, ...] = ()
    resource_dimension: str = ""
    resource_change: str = ""
    satisfaction_type: str = ""
    hook_type: str = ""
    humiliation_scene: bool = False
    protagonist_causes_turn: bool = True
    seeded_resource_ids: Tuple[str, ...] = ()
    used_resource_ids: Tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _chapter_schema_version(self.schema_version)
        if not isinstance(self.chapter, int) or isinstance(self.chapter, bool) or self.chapter < 1:
            raise ValueError("chapter must be a positive integer")
        for field_name in (
            "goal",
            "obstacle",
            "active_choice",
            "cost",
            "irreversible_change",
            "local_payoff",
            "ending_pressure",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_string(getattr(self, field_name), field_name),
            )
        for field_name in (
            "preserve_facts",
            "allowed_knowledge",
            "world_event_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _string_tuple(getattr(self, field_name), field_name),
            )
        if self.schema_version == 1:
            object.__setattr__(self, "reader_jobs", ())
            object.__setattr__(self, "belonging_anchors", ())
            object.__setattr__(self, "resource_dimension", "")
            object.__setattr__(self, "resource_change", "")
            object.__setattr__(self, "satisfaction_type", "")
            object.__setattr__(self, "hook_type", "")
            object.__setattr__(self, "humiliation_scene", False)
            object.__setattr__(self, "protagonist_causes_turn", True)
            object.__setattr__(self, "seeded_resource_ids", ())
            object.__setattr__(self, "used_resource_ids", ())
            return

        from commercial_story import (
            HOOK_TYPES,
            READER_JOBS,
            RESOURCE_DIMENSIONS,
            SATISFACTION_TYPES,
            BELONGING_ANCHORS,
        )

        def controlled_tuple(value: Any, field_name: str, allowed: frozenset[str], maximum: int) -> Tuple[str, ...]:
            normalized = _string_tuple(value, field_name)
            if not 1 <= len(normalized) <= maximum or len(set(normalized)) != len(normalized):
                raise ValueError(f"{field_name} must contain 1-{maximum} unique values")
            if any(item not in allowed for item in normalized):
                raise ValueError(f"{field_name} has invalid value")
            return normalized

        def resource_ids(value: Any, field_name: str) -> Tuple[str, ...]:
            normalized = _string_tuple(value, field_name)
            if len(set(normalized)) != len(normalized):
                raise ValueError(f"{field_name} must contain unique values")
            if any(not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", item) for item in normalized):
                raise ValueError(f"{field_name} contains an invalid resource id")
            return normalized

        object.__setattr__(
            self, "reader_jobs", controlled_tuple(self.reader_jobs, "reader_jobs", READER_JOBS, 3)
        )
        anchors = _string_tuple(self.belonging_anchors, "belonging_anchors")
        if len(anchors) > 2 or len(set(anchors)) != len(anchors) or any(item not in BELONGING_ANCHORS for item in anchors):
            raise ValueError("belonging_anchors must contain 0-2 unique values")
        object.__setattr__(self, "belonging_anchors", anchors)
        for field_name, allowed in (
            ("resource_dimension", RESOURCE_DIMENSIONS),
            ("satisfaction_type", SATISFACTION_TYPES),
            ("hook_type", HOOK_TYPES),
        ):
            value = _required_string(getattr(self, field_name), field_name)
            if value not in allowed:
                raise ValueError(f"{field_name} has invalid value")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "resource_change", _required_string(self.resource_change, "resource_change"))
        if type(self.humiliation_scene) is not bool:
            raise ValueError("humiliation_scene must be a boolean")
        if type(self.protagonist_causes_turn) is not bool:
            raise ValueError("protagonist_causes_turn must be a boolean")
        object.__setattr__(self, "seeded_resource_ids", resource_ids(self.seeded_resource_ids, "seeded_resource_ids"))
        object.__setattr__(self, "used_resource_ids", resource_ids(self.used_resource_ids, "used_resource_ids"))

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "chapter": self.chapter,
            "goal": self.goal,
            "obstacle": self.obstacle,
            "active_choice": self.active_choice,
            "cost": self.cost,
            "irreversible_change": self.irreversible_change,
            "local_payoff": self.local_payoff,
            "ending_pressure": self.ending_pressure,
            "preserve_facts": list(self.preserve_facts),
            "allowed_knowledge": list(self.allowed_knowledge),
            "world_event_ids": list(self.world_event_ids),
            "schema_version": self.schema_version,
        }
        if self.schema_version == 2:
            payload.update(
                {
                    "reader_jobs": list(self.reader_jobs),
                    "belonging_anchors": list(self.belonging_anchors),
                    "resource_dimension": self.resource_dimension,
                    "resource_change": self.resource_change,
                    "satisfaction_type": self.satisfaction_type,
                    "hook_type": self.hook_type,
                    "humiliation_scene": self.humiliation_scene,
                    "protagonist_causes_turn": self.protagonist_causes_turn,
                    "seeded_resource_ids": list(self.seeded_resource_ids),
                    "used_resource_ids": list(self.used_resource_ids),
                }
            )
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChapterContract":
        schema_version = _chapter_schema_version(data.get("schema_version", 1))
        values = {
            "chapter": data["chapter"],
            "goal": data["goal"],
            "obstacle": data["obstacle"],
            "active_choice": data["active_choice"],
            "cost": data["cost"],
            "irreversible_change": data["irreversible_change"],
            "local_payoff": data["local_payoff"],
            "ending_pressure": data["ending_pressure"],
            "preserve_facts": data.get("preserve_facts", ()),
            "allowed_knowledge": data.get("allowed_knowledge", ()),
            "world_event_ids": data.get("world_event_ids", ()),
            "schema_version": schema_version,
        }
        if schema_version == 2:
            required = {
                "reader_jobs", "belonging_anchors", "resource_dimension", "resource_change",
                "satisfaction_type", "hook_type", "humiliation_scene", "protagonist_causes_turn",
                "seeded_resource_ids", "used_resource_ids",
            }
            missing = sorted(field for field in required if field not in data)
            if missing:
                raise ValueError("schema-v2 chapter contract is missing: " + ", ".join(missing))
            values.update({field: data[field] for field in required})
        return cls(**values)
