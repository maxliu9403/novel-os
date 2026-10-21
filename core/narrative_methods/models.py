"""P1 contracts: advisory results never grant promotion or rewrite authority."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

METHOD_IDS = ("en-functional-prose.v1", "en-agency-payoff.v1")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MethodPolicy:
    schema: str = "novel-method-policy.v1"
    mode: str = "advisory"
    method_ids: tuple[str, ...] = METHOD_IDS
    scope: str = "free_trial_window"
    repair_policy: str = "none"
    review_max_chars: int = 16000
    context_max_chars: int = 16000
    schema_repair_attempts: int = 1
    review_timeout_seconds: int = 300

    def __post_init__(self):
        if self.schema != "novel-method-policy.v1" or self.mode not in {"off", "advisory"}:
            raise ValueError("P1 supports only off/advisory with novel-method-policy.v1")
        if self.scope != "free_trial_window" or self.repair_policy != "none":
            raise ValueError("P1 reviews only the approved free-trial window; rewriting is disabled")
        if not isinstance(self.method_ids, (tuple, list)) or not self.method_ids:
            raise ValueError("method_ids must be a nonempty list")
        if any(m not in METHOD_IDS for m in self.method_ids) or len(set(self.method_ids)) != len(self.method_ids):
            raise ValueError("unknown or duplicate method_id")
        object.__setattr__(self, "method_ids", tuple(self.method_ids))
        for name in ("review_max_chars", "context_max_chars"):
            value = getattr(self, name)
            if type(value) is not int or not 1000 <= value <= 64000:
                raise ValueError(f"{name} must be an integer from 1000 to 64000")
        if type(self.schema_repair_attempts) is not int or self.schema_repair_attempts not in {0, 1}:
            raise ValueError("schema_repair_attempts must be 0 or 1")
        if type(self.review_timeout_seconds) is not int or not 30 <= self.review_timeout_seconds <= 1800:
            raise ValueError("review_timeout_seconds must be an integer from 30 to 1800")

    def to_dict(self) -> dict:
        result = asdict(self)
        result["method_ids"] = list(self.method_ids)
        return result

    @classmethod
    def from_dict(cls, value: dict) -> MethodPolicy:
        if not isinstance(value, dict):
            raise ValueError("method policy must be an object")
        try:
            return cls(**value)
        except TypeError as exc:
            raise ValueError("unknown or malformed method policy fields") from exc


@dataclass(frozen=True)
class ReviewInput:
    revision_id: str
    chapter: int
    text: str
    language: str
    free_trial_end: int | None
    contracts: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.revision_id or type(self.chapter) is not int or self.chapter < 1:
            raise ValueError("review needs an exact revision and positive chapter")
        if not isinstance(self.text, str) or not isinstance(self.contracts, dict):
            raise ValueError("review text/contracts have invalid types")
        if self.free_trial_end is not None and (type(self.free_trial_end) is not int or self.free_trial_end < 1):
            raise ValueError("invalid approved free-trial end")

    @property
    def binding(self):
        return {"revision_id": self.revision_id, "chapter": self.chapter,
                "candidate_sha256": text_sha(self.text), "contracts_sha256": digest(self.contracts)}


@dataclass(frozen=True)
class GuidancePacket:
    status: str
    reason: str = ""
    production_guidance: str = ""
    review_system: str = ""
    review_user: str = ""
    rule_ids: tuple[str, ...] = ()
    binding: dict = field(default_factory=dict)

    @property
    def sha256(self):
        return digest(asdict(self))
