"""Immutable proposals for agent-authored canon deltas."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

from state_parser import apply_to_state, parse_agent_output


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROPOSAL_ID_RE = re.compile(r"^proposal-[0-9a-f]{64}$")
_ALLOWED_AGENT_NAMES = frozenset(
    {"architect", "scribe", "editor", "continuity_guardian", "style_curator"}
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _validate_json_value(value: Any) -> None:
    """Reject values outside the strict JSON value domain recursively."""
    if value is None or type(value) in (bool, str, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("delta must contain JSON-compatible values")
        return
    if type(value) is dict:
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("delta must contain JSON-compatible values")
            _validate_json_value(item)
        return
    if type(value) in (list, tuple):
        for item in value:
            _validate_json_value(item)
        return
    raise ValueError("delta must contain JSON-compatible values")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _normalize_delta(delta: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(delta)
    if "key_events" in normalized and isinstance(normalized["key_events"], str):
        normalized["key_events"] = [normalized["key_events"]]
    return normalized


def _identity_dict(
    *,
    schema_version: int,
    chapter: int,
    agent_name: str,
    source_artifact_sha: str,
    delta: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "agent_name": agent_name,
        "chapter": chapter,
        "delta": _thaw(delta),
        "schema_version": schema_version,
        "source_artifact_sha": source_artifact_sha,
    }


def _proposal_id(identity: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    return f"proposal-{digest}"


@dataclass(frozen=True)
class CanonDeltaProposal:
    chapter: int
    agent_name: str
    source_artifact_sha: str
    delta: Mapping[str, Any]
    schema_version: int = 1
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    proposal_id: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.chapter, bool) or not isinstance(self.chapter, int) or self.chapter <= 0:
            raise ValueError("chapter must be a positive integer")
        if not isinstance(self.agent_name, str) or not self.agent_name.strip():
            raise ValueError("agent_name must be nonblank")
        if self.agent_name not in _ALLOWED_AGENT_NAMES:
            raise ValueError("agent_name is not permitted to propose canon")
        if not isinstance(self.source_artifact_sha, str) or not _SHA256_RE.fullmatch(
            self.source_artifact_sha
        ):
            raise ValueError("source_artifact_sha must be lowercase 64 hex")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != 1
        ):
            raise ValueError("schema_version must be 1")
        if not isinstance(self.delta, Mapping):
            raise ValueError("delta must be an object")
        if not isinstance(self.timestamp, str):
            raise ValueError("timestamp must be a UTC ISO-8601 string")
        try:
            parsed_timestamp = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp must be a UTC ISO-8601 string") from exc
        if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("timestamp must be UTC")

        normalized_delta = _normalize_delta(self.delta)
        _validate_json_value(normalized_delta)
        frozen_delta = _freeze(normalized_delta)
        object.__setattr__(self, "delta", frozen_delta)
        try:
            identity = _identity_dict(
                schema_version=self.schema_version,
                chapter=self.chapter,
                agent_name=self.agent_name,
                source_artifact_sha=self.source_artifact_sha,
                delta=frozen_delta,
            )
            expected_id = _proposal_id(identity)
        except (TypeError, ValueError) as exc:
            raise ValueError("delta must contain JSON-compatible values") from exc
        if not isinstance(self.proposal_id, str):
            raise ValueError("proposal_id must be a string")
        if self.proposal_id and (
            not _PROPOSAL_ID_RE.fullmatch(self.proposal_id) or self.proposal_id != expected_id
        ):
            raise ValueError("proposal_id does not match proposal identity")
        object.__setattr__(self, "proposal_id", expected_id)

    def to_dict(self) -> dict[str, Any]:
        """Return a stable, JSON-compatible representation."""
        return {
            "proposal_id": self.proposal_id,
            "chapter": self.chapter,
            "agent_name": self.agent_name,
            "source_artifact_sha": self.source_artifact_sha,
            "delta": _thaw(self.delta),
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CanonDeltaProposal":
        """Validate and restore a proposal from its JSON representation."""
        if not isinstance(data, Mapping):
            raise ValueError("proposal must be an object")
        expected_keys = {
            "proposal_id",
            "chapter",
            "agent_name",
            "source_artifact_sha",
            "delta",
            "schema_version",
            "timestamp",
        }
        if set(data) != expected_keys:
            raise ValueError("proposal has invalid fields")
        proposal_id = data["proposal_id"]
        if not isinstance(proposal_id, str) or not _PROPOSAL_ID_RE.fullmatch(proposal_id):
            raise ValueError("proposal_id has invalid format")
        return cls(**dict(data))


def build_canon_proposal(
    state: Any,
    chapter: int,
    agent_name: str,
    source_artifact_sha: str,
    text: str,
) -> CanonDeltaProposal:
    """Build an immutable proposal from agent output without touching state."""
    del state
    delta = parse_agent_output(agent_name, text)
    if not delta:
        raise ValueError("agent output has no meaningful state update")
    return CanonDeltaProposal(
        chapter=chapter,
        agent_name=agent_name,
        source_artifact_sha=source_artifact_sha,
        delta=delta,
    )


def apply_canon_proposal(
    state: Any,
    proposal: CanonDeltaProposal,
    actual_artifact_sha: str,
) -> list[str]:
    """Apply a proposal only when it matches the exact persisted artifact."""
    if not isinstance(actual_artifact_sha, str) or not _SHA256_RE.fullmatch(actual_artifact_sha):
        raise ValueError("actual artifact sha must be lowercase 64 hex")
    if actual_artifact_sha != proposal.source_artifact_sha:
        raise ValueError("actual artifact sha does not match proposal source")
    delta = _thaw(proposal.delta)
    if "proposal_chain" in delta:
        if set(delta) != {"proposal_chain"} or not isinstance(
            delta["proposal_chain"], list
        ) or not delta["proposal_chain"]:
            raise ValueError("proposal_chain must be a nonempty proposal array")
        changes: list[str] = []
        expected_fields = {
            "proposal_id",
            "agent_name",
            "source_artifact_sha",
            "delta",
        }
        for entry in delta["proposal_chain"]:
            if not isinstance(entry, dict) or set(entry) != expected_fields:
                raise ValueError("proposal_chain entry has invalid fields")
            nested = CanonDeltaProposal(
                chapter=proposal.chapter,
                agent_name=entry["agent_name"],
                source_artifact_sha=entry["source_artifact_sha"],
                delta=entry["delta"],
                proposal_id=entry["proposal_id"],
            )
            if "proposal_chain" in nested.delta:
                raise ValueError("proposal_chain entries cannot contain proposal chains")
            changes.extend(
                apply_to_state(
                    state,
                    nested.chapter,
                    _thaw(nested.delta),
                    source=nested.agent_name,
                )
            )
        return changes
    return apply_to_state(
        state,
        proposal.chapter,
        delta,
        source=proposal.agent_name,
    )
