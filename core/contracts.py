"""Immutable, versioned author and story quality contracts."""

import hashlib
import json
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
    schema_version: int = 1

    def __post_init__(self) -> None:
        _schema_version(self.schema_version)
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

    def to_dict(self) -> Dict[str, Any]:
        return {
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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChapterContract":
        schema_version = _schema_version(data.get("schema_version", 1))
        return cls(
            chapter=data["chapter"],
            goal=data["goal"],
            obstacle=data["obstacle"],
            active_choice=data["active_choice"],
            cost=data["cost"],
            irreversible_change=data["irreversible_change"],
            local_payoff=data["local_payoff"],
            ending_pressure=data["ending_pressure"],
            preserve_facts=data.get("preserve_facts", ()),
            allowed_knowledge=data.get("allowed_knowledge", ()),
            world_event_ids=data.get("world_event_ids", ()),
            schema_version=schema_version,
        )
