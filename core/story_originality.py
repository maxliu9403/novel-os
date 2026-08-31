"""Deterministic structural-originality fingerprints for commercial stories.

The module intentionally operates on controlled categories and ordered action
codes. Story prose, character identity, titles, and source-corpus material are
outside its data model.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from commercial_story import (
    ACTION_COSTS,
    AGENCY_SOURCES,
    BELONGING_ANCHORS,
    BENEFICIARY_ROLES,
    DEADLINE_TYPES,
    FREE_ARC_ACTIONS,
    INVISIBLE_LABOR,
    LIFE_STAGES,
    PROOF_TYPES,
    RELATIONSHIP_SHAPES,
    RESOURCE_DIMENSIONS,
    SACRED_ASSETS,
    CommercialStoryContract,
)
from project_identity import (
    ProjectIdentityError,
    ensure_project_instance_id,
    load_project_instance_id,
)


SCHEMA_VERSION = 1
CATALOG_PATH = Path(__file__).with_name("data") / "commercial_pattern_catalog.v1.json"
FINGERPRINT_RELATIVE = Path("outputs/input/story-fingerprint.json")
REPORT_RELATIVE = Path("outputs/quality/story-originality-report.json")

DIMENSION_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("protagonist_life_stage", 0.08),
    ("invisible_labor", 0.10),
    ("sacred_asset", 0.12),
    ("boundary_transfer", 0.12),
    ("beneficiary_role", 0.06),
    ("proof_type", 0.11),
    ("deadline_type", 0.07),
    ("agency_source", 0.10),
    ("action_cost", 0.08),
    ("belonging_anchors", 0.08),
    ("relationship_shape", 0.08),
)

_DIMENSION_VALUES = {
    "protagonist_life_stage": LIFE_STAGES,
    "invisible_labor": INVISIBLE_LABOR,
    "sacred_asset": SACRED_ASSETS,
    "boundary_transfer": RESOURCE_DIMENSIONS,
    "beneficiary_role": BENEFICIARY_ROLES,
    "proof_type": PROOF_TYPES,
    "deadline_type": DEADLINE_TYPES,
    "agency_source": AGENCY_SOURCES,
    "action_cost": ACTION_COSTS,
    "relationship_shape": RELATIONSHIP_SHAPES,
}
_CATALOG_PATTERN_FIELDS = {
    "pattern_id",
    *(name for name, _weight in DIMENSION_WEIGHTS),
    "action_sequence",
    "source_count",
    "risk_note",
}


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonblank string")
    return value.strip()


def _controlled(value: Any, allowed: frozenset[str], field: str) -> str:
    normalized = _required_string(value, field)
    if normalized not in allowed:
        raise ValueError(f"{field} has invalid value")
    return normalized


def _controlled_sequence(
    value: Any,
    allowed: frozenset[str],
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an array")
    result = tuple(_controlled(item, allowed, field) for item in value)
    if not minimum <= len(result) <= maximum or len(set(result)) != len(result):
        raise ValueError(f"{field} must contain {minimum}-{maximum} unique values")
    return result


@dataclass(frozen=True)
class StoryFingerprint:
    contract_id: str
    contract_sha256: str
    protagonist_life_stage: str
    invisible_labor: str
    sacred_asset: str
    boundary_transfer: str
    beneficiary_role: str
    proof_type: str
    deadline_type: str
    agency_source: str
    action_cost: str
    belonging_anchors: tuple[str, ...]
    relationship_shape: str
    action_sequence: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("story fingerprint schema version is invalid")
        _required_string(self.contract_id, "contract_id")
        if len(self.contract_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.contract_sha256
        ):
            raise ValueError("contract_sha256 must be a lowercase SHA-256 digest")
        for field, allowed in _DIMENSION_VALUES.items():
            _controlled(getattr(self, field), allowed, field)
        _controlled_sequence(
            self.belonging_anchors,
            BELONGING_ANCHORS,
            "belonging_anchors",
            minimum=2,
            maximum=4,
        )
        _controlled_sequence(
            self.action_sequence,
            FREE_ARC_ACTIONS,
            "action_sequence",
            minimum=3,
            maximum=8,
        )

    @classmethod
    def from_contract(cls, contract: CommercialStoryContract) -> "StoryFingerprint":
        if not isinstance(contract, CommercialStoryContract):
            raise TypeError("contract must be CommercialStoryContract")
        premise = contract.premise_engine
        return cls(
            contract_id=contract.contract_id,
            contract_sha256=contract.contract_id.rsplit(":", 1)[1],
            protagonist_life_stage=premise.protagonist_life_stage,
            invisible_labor=premise.invisible_labor,
            sacred_asset=premise.sacred_asset,
            boundary_transfer=premise.boundary_transfer,
            beneficiary_role=premise.beneficiary_role,
            proof_type=premise.proof_type,
            deadline_type=premise.deadline_type,
            agency_source=premise.agency_source,
            action_cost=premise.action_cost,
            belonging_anchors=tuple(sorted(premise.belonging_anchors)),
            relationship_shape=premise.relationship_shape,
            action_sequence=contract.free_trial_arc.action_sequence,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StoryFingerprint":
        expected = {
            "schema_version",
            "contract_id",
            "contract_sha256",
            "dimensions",
            "action_sequence",
            "signature_sha256",
            "fingerprint_id",
        }
        if not isinstance(data, Mapping) or set(data) != expected:
            raise ValueError("story fingerprint has invalid fields")
        dimensions = data["dimensions"]
        dimension_fields = {name for name, _weight in DIMENSION_WEIGHTS}
        if not isinstance(dimensions, Mapping) or set(dimensions) != dimension_fields:
            raise ValueError("story fingerprint dimensions have invalid fields")
        value = cls(
            schema_version=data["schema_version"],
            contract_id=data["contract_id"],
            contract_sha256=data["contract_sha256"],
            action_sequence=tuple(data["action_sequence"]),
            belonging_anchors=tuple(dimensions["belonging_anchors"]),
            **{
                field: dimensions[field]
                for field in dimension_fields - {"belonging_anchors"}
            },
        )
        if data["signature_sha256"] != value.signature_sha256:
            raise ValueError("story fingerprint signature hash mismatch")
        if data["fingerprint_id"] != value.fingerprint_id:
            raise ValueError("story fingerprint id mismatch")
        if value.contract_id.startswith("commercial-story:") and (
            value.contract_id.rsplit(":", 1)[1] != value.contract_sha256
        ):
            raise ValueError("story fingerprint contract hash mismatch")
        return value

    @property
    def dimensions(self) -> dict[str, Any]:
        return {
            name: list(value) if name == "belonging_anchors" else value
            for name, _weight in DIMENSION_WEIGHTS
            for value in (getattr(self, name),)
        }

    @property
    def signature_sha256(self) -> str:
        return _sha256_bytes(
            _canonical_json_bytes(
                {
                    "dimensions": self.dimensions,
                    "action_sequence": list(self.action_sequence),
                }
            )
        )

    @property
    def fingerprint_id(self) -> str:
        return f"story-fingerprint:{self.signature_sha256}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id,
            "contract_sha256": self.contract_sha256,
            "fingerprint_id": self.fingerprint_id,
            "dimensions": self.dimensions,
            "action_sequence": list(self.action_sequence),
            "signature_sha256": self.signature_sha256,
        }


@dataclass(frozen=True)
class OriginalityReference:
    reference_id: str
    reference_sha256: str
    fingerprint: StoryFingerprint

    @classmethod
    def create(
        cls, reference_id: str, fingerprint: StoryFingerprint
    ) -> "OriginalityReference":
        reference_id = _required_string(reference_id, "reference_id")
        digest = _sha256_bytes(_canonical_json_bytes(fingerprint.to_dict()))
        return cls(reference_id, digest, fingerprint)

    def identity_dict(self) -> dict[str, str]:
        return {
            "reference_id": self.reference_id,
            "reference_sha256": self.reference_sha256,
        }


@dataclass(frozen=True)
class OriginalityReferenceSet:
    references: tuple[OriginalityReference, ...]
    reference_set_sha256: str

    @classmethod
    def capture(
        cls,
        references: Iterable[
            StoryFingerprint | tuple[str, StoryFingerprint] | OriginalityReference
        ],
    ) -> "OriginalityReferenceSet":
        normalized: list[OriginalityReference] = []
        for item in references:
            if isinstance(item, OriginalityReference):
                reference = item
            elif isinstance(item, StoryFingerprint):
                reference = OriginalityReference.create(item.fingerprint_id, item)
            else:
                reference = OriginalityReference.create(item[0], item[1])
            normalized.append(reference)
        normalized.sort(key=lambda item: (item.reference_id, item.reference_sha256))
        identities = [item.identity_dict() for item in normalized]
        if len({item["reference_id"] for item in identities}) != len(identities):
            raise ValueError("originality reference ids must be unique")
        return cls(
            references=tuple(normalized),
            reference_set_sha256=_sha256_bytes(_canonical_json_bytes(identities)),
        )

    @property
    def reference_ids(self) -> tuple[str, ...]:
        return tuple(item.reference_id for item in self.references)

    def to_dict(self) -> dict[str, Any]:
        return {
            "references": [item.identity_dict() for item in self.references],
            "reference_set_sha256": self.reference_set_sha256,
        }


@dataclass(frozen=True)
class OriginalityFinding:
    code: str
    severity: str
    reference_id: str
    reference_sha256: str
    dimension_similarity: float
    action_sequence_similarity: float
    matched_dimensions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "reference_id": self.reference_id,
            "reference_sha256": self.reference_sha256,
            "dimension_similarity": self.dimension_similarity,
            "action_sequence_similarity": self.action_sequence_similarity,
            "matched_dimensions": list(self.matched_dimensions),
        }


@dataclass(frozen=True)
class OriginalityReport:
    status: str
    candidate_fingerprint_sha256: str
    reference_set_sha256: str
    dimension_similarity: float
    action_sequence_similarity: float
    findings: tuple[OriginalityFinding, ...]
    foundation_sha256: str = ""
    catalog_sha256: str = ""
    sibling_reference_set_sha256: str = ""
    schema_version: int = SCHEMA_VERSION

    @property
    def report_id(self) -> str:
        return "story-originality:" + _sha256_bytes(
            _canonical_json_bytes(self._payload())
        )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "candidate_fingerprint_sha256": self.candidate_fingerprint_sha256,
            "foundation_sha256": self.foundation_sha256,
            "catalog_sha256": self.catalog_sha256,
            "sibling_reference_set_sha256": self.sibling_reference_set_sha256,
            "reference_set_sha256": self.reference_set_sha256,
            "dimension_similarity": self.dimension_similarity,
            "action_sequence_similarity": self.action_sequence_similarity,
            "findings": [item.to_dict() for item in self.findings],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "report_id": self.report_id}


def _dimension_similarity(
    candidate: StoryFingerprint, reference: StoryFingerprint
) -> tuple[float, tuple[str, ...]]:
    score = 0.0
    matched: list[str] = []
    for field, weight in DIMENSION_WEIGHTS:
        left = getattr(candidate, field)
        right = getattr(reference, field)
        if field == "belonging_anchors":
            union = set(left) | set(right)
            similarity = len(set(left) & set(right)) / len(union)
            if similarity == 1.0:
                matched.append(field)
            score += weight * similarity
        elif left == right:
            matched.append(field)
            score += weight
    return round(score, 6), tuple(matched)


def _lcs_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    rows = [0] * (len(right) + 1)
    for left_item in left:
        previous = 0
        for index, right_item in enumerate(right, start=1):
            current = rows[index]
            if left_item == right_item:
                rows[index] = previous + 1
            else:
                rows[index] = max(rows[index], rows[index - 1])
            previous = current
    denominator = max(len(left), len(right))
    return round(rows[-1] / denominator, 6) if denominator else 1.0


def compare_fingerprints(
    candidate: StoryFingerprint,
    references: Sequence[StoryFingerprint] | OriginalityReferenceSet,
    *,
    foundation_sha256: str = "",
    catalog_sha256: str = "",
    sibling_reference_set_sha256: str = "",
) -> OriginalityReport:
    reference_set = (
        references
        if isinstance(references, OriginalityReferenceSet)
        else OriginalityReferenceSet.capture(references)
    )
    findings: list[OriginalityFinding] = []
    closest = (0.0, 0.0)
    for reference in reference_set.references:
        dimension_score, matched = _dimension_similarity(
            candidate, reference.fingerprint
        )
        action_score = _lcs_similarity(
            candidate.action_sequence, reference.fingerprint.action_sequence
        )
        closest = max(closest, (dimension_score, action_score))
        if candidate.signature_sha256 == reference.fingerprint.signature_sha256:
            code, severity = "duplicate_story_signature", "blocked"
        elif dimension_score >= 0.84 and action_score >= 0.75:
            code, severity = "high_structure_and_action_overlap", "blocked"
        elif dimension_score >= 0.72:
            code, severity = "high_dimension_overlap", "warning"
        else:
            continue
        findings.append(
            OriginalityFinding(
                code=code,
                severity=severity,
                reference_id=reference.reference_id,
                reference_sha256=reference.reference_sha256,
                dimension_similarity=dimension_score,
                action_sequence_similarity=action_score,
                matched_dimensions=matched,
            )
        )
    severity_order = {"blocked": 0, "warning": 1}
    findings.sort(
        key=lambda item: (
            severity_order[item.severity],
            -item.dimension_similarity,
            -item.action_sequence_similarity,
            item.reference_id,
        )
    )
    status = (
        "blocked"
        if any(item.severity == "blocked" for item in findings)
        else "warning" if findings else "pass"
    )
    return OriginalityReport(
        status=status,
        candidate_fingerprint_sha256=_sha256_bytes(
            _canonical_json_bytes(candidate.to_dict())
        ),
        reference_set_sha256=reference_set.reference_set_sha256,
        dimension_similarity=closest[0],
        action_sequence_similarity=closest[1],
        findings=tuple(findings),
        foundation_sha256=foundation_sha256,
        catalog_sha256=catalog_sha256,
        sibling_reference_set_sha256=sibling_reference_set_sha256,
    )


def _catalog_references() -> tuple[tuple[OriginalityReference, ...], str]:
    raw = CATALOG_PATH.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, Mapping) or set(payload) != {"schema_version", "patterns"}:
        raise ValueError("commercial pattern catalog has invalid fields")
    if payload["schema_version"] != SCHEMA_VERSION or not isinstance(
        payload["patterns"], list
    ):
        raise ValueError("commercial pattern catalog schema is invalid")
    references: list[OriginalityReference] = []
    for pattern in payload["patterns"]:
        if not isinstance(pattern, Mapping) or set(pattern) != _CATALOG_PATTERN_FIELDS:
            raise ValueError("commercial pattern catalog entry has invalid fields")
        pattern_id = _required_string(pattern["pattern_id"], "pattern_id")
        source_count = pattern["source_count"]
        if type(source_count) is not int or source_count < 2:
            raise ValueError("commercial pattern source_count must be at least 2")
        _required_string(pattern["risk_note"], "risk_note")
        entry_sha = _sha256_bytes(_canonical_json_bytes(pattern))
        fingerprint = StoryFingerprint(
            contract_id=f"catalog-pattern:{pattern_id}",
            contract_sha256=entry_sha,
            action_sequence=tuple(pattern["action_sequence"]),
            belonging_anchors=tuple(sorted(pattern["belonging_anchors"])),
            **{
                field: pattern[field]
                for field, _weight in DIMENSION_WEIGHTS
                if field != "belonging_anchors"
            },
        )
        references.append(
            OriginalityReference.create(f"catalog:{pattern_id}", fingerprint)
        )
    return tuple(references), _sha256_bytes(raw)


def _load_valid_sibling_fingerprint(
    sibling: Path,
) -> tuple[str, StoryFingerprint] | None:
    fingerprint_path = sibling / FINGERPRINT_RELATIVE
    foundation_path = sibling / "outputs/input/foundation.json"
    if not fingerprint_path.is_file() or not foundation_path.is_file():
        return None
    try:
        instance_id = load_project_instance_id(sibling)
        foundation = json.loads(foundation_path.read_text(encoding="utf-8"))
        contract = CommercialStoryContract.from_dict(
            foundation["commercial_story_contract"]
        )
        if foundation.get("commercial_story_contract_id") != contract.contract_id:
            return None
        stored = StoryFingerprint.from_dict(
            json.loads(fingerprint_path.read_text(encoding="utf-8"))
        )
        expected = StoryFingerprint.from_contract(contract)
        if stored != expected:
            return None
        return f"sibling:{instance_id}", stored
    except (
        KeyError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ProjectIdentityError,
    ):
        return None


@dataclass(frozen=True)
class OriginalityEvaluationContext:
    references: OriginalityReferenceSet
    foundation_sha256: str
    catalog_sha256: str
    sibling_reference_set_sha256: str

    @property
    def input_hashes(self) -> dict[str, str]:
        return {
            "foundation_sha256": self.foundation_sha256,
            "catalog_sha256": self.catalog_sha256,
            "sibling_reference_set_sha256": self.sibling_reference_set_sha256,
        }


def capture_originality_context(project: Path) -> OriginalityEvaluationContext:
    project = project.resolve()
    foundation_path = project / "outputs/input/foundation.json"
    if not foundation_path.is_file():
        raise ValueError("story foundation is missing")
    current_instance_id = ensure_project_instance_id(project)
    catalog_references, catalog_sha256 = _catalog_references()
    sibling_references: list[tuple[str, StoryFingerprint]] = []
    projects_root = project.parent
    if projects_root.is_dir():
        for sibling in sorted(projects_root.iterdir(), key=lambda path: path.name):
            if not sibling.is_dir() or sibling.resolve() == project:
                continue
            loaded = _load_valid_sibling_fingerprint(sibling)
            if loaded is None or loaded[0] == f"sibling:{current_instance_id}":
                continue
            sibling_references.append(loaded)
    siblings = OriginalityReferenceSet.capture(sibling_references)
    combined = OriginalityReferenceSet.capture(
        [*catalog_references, *siblings.references]
    )
    return OriginalityEvaluationContext(
        references=combined,
        foundation_sha256=_sha256_path(foundation_path),
        catalog_sha256=catalog_sha256,
        sibling_reference_set_sha256=siblings.reference_set_sha256,
    )


def originality_input_hashes(project: Path) -> dict[str, str]:
    return capture_originality_context(project).input_hashes


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f"{path.name}.tmp-", dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def evaluate_story_originality(
    project: Path,
    contract: CommercialStoryContract,
    *,
    context: OriginalityEvaluationContext | None = None,
) -> OriginalityReport:
    project = project.resolve()
    if not isinstance(contract, CommercialStoryContract):
        raise TypeError("contract must be CommercialStoryContract")
    foundation_path = project / "outputs/input/foundation.json"
    foundation = json.loads(foundation_path.read_text(encoding="utf-8"))
    bound_contract = CommercialStoryContract.from_dict(
        foundation["commercial_story_contract"]
    )
    if (
        bound_contract != contract
        or foundation.get("commercial_story_contract_id") != contract.contract_id
    ):
        raise ValueError("story foundation commercial contract binding is invalid")
    context = context or capture_originality_context(project)
    if _sha256_path(foundation_path) != context.foundation_sha256:
        raise ValueError("story foundation changed after originality inputs were captured")
    fingerprint = StoryFingerprint.from_contract(contract)
    report = compare_fingerprints(
        fingerprint,
        context.references,
        foundation_sha256=context.foundation_sha256,
        catalog_sha256=context.catalog_sha256,
        sibling_reference_set_sha256=context.sibling_reference_set_sha256,
    )
    _atomic_json(project / FINGERPRINT_RELATIVE, fingerprint.to_dict())
    _atomic_json(project / REPORT_RELATIVE, report.to_dict())
    return report


__all__ = [
    "CATALOG_PATH",
    "DIMENSION_WEIGHTS",
    "OriginalityEvaluationContext",
    "OriginalityFinding",
    "OriginalityReferenceSet",
    "OriginalityReport",
    "StoryFingerprint",
    "capture_originality_context",
    "compare_fingerprints",
    "evaluate_story_originality",
    "originality_input_hashes",
]
