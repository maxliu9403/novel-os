"""Pending review feedback bound to the plans that were actually reviewed.

These checkpoints are repair context, never an approval or evidence receipt.
The caller verifies provider references before constructing semantic findings;
every revised portfolio still requires a fresh independent review.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

try:
    from .cover_models_v2 import ArtDirectionSet
    from .cover_validator import ValidationFinding
except ImportError:  # pragma: no cover - legacy CLI imports
    from cover_models_v2 import ArtDirectionSet
    from cover_validator import ValidationFinding


_SEMANTIC_CODES = frozenset({"campaign_semantic_review", "campaign_evidence_unsupported"})


def _json(value: Any) -> str:
    def check_keys(item: Any) -> None:
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            for child in item.values():
                check_keys(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check_keys(child)
    try:
        check_keys(value)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Direction feedback requires finite UTF-8 JSON with string object keys") from exc


def _validate_finding(finding: ValidationFinding) -> None:
    if not isinstance(finding, ValidationFinding) or any(
        not isinstance(getattr(finding, key), str) for key in ("code", "severity", "message", "evidence")
    ):
        raise ValueError("Direction feedback requires typed findings with text fields")
    if not finding.code.strip() or not finding.message.strip():
        raise ValueError("Direction feedback requires a concrete code and message")


@dataclass(frozen=True)
class CampaignSemanticFinding(ValidationFinding):
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_finding(self)
        if self.code != "campaign_semantic_review" or self.severity != "blocker":
            raise ValueError("Campaign semantic findings must be campaign_semantic_review blockers")
        if not isinstance(self.evidence_refs, tuple) or not self.evidence_refs or any(
            not isinstance(ref, str) or not ref.strip() for ref in self.evidence_refs
        ):
            raise ValueError("Campaign semantic findings require a nonempty tuple of evidence references")
        _json([self.code, self.message, self.evidence, self.evidence_refs])


def serialize_repair_findings(findings: Sequence[ValidationFinding]) -> list[dict[str, Any]]:
    """Keep the legacy repair shape, extending only typed semantic critiques."""
    records = []
    for finding in findings:
        _validate_finding(finding)
        record = {"code": finding.code, "message": finding.message, "concept_or_evidence": finding.evidence}
        if isinstance(finding, CampaignSemanticFinding):
            record["evidence_refs"] = list(finding.evidence_refs)
        if isinstance(finding, CommercialSemanticFinding):
            record.update(category=finding.category, claim=finding.claim, field=finding.field)
        records.append(record)
    return json.loads(_json(records))


def semantic_review_checkpoint(
    direction: ArtDirectionSet, findings: Sequence[ValidationFinding], attempt: int,
) -> dict[str, Any]:
    """Snapshot a completed review's feedback without granting it authority."""
    if type(attempt) is not int or attempt <= 0:
        raise ValueError("Semantic review attempt must be a positive integer")
    findings = tuple(findings)
    records = serialize_repair_findings(findings)
    if any(finding.code not in _SEMANTIC_CODES or finding.severity != "blocker" for finding in findings):
        raise ValueError("Semantic checkpoints accept only independent semantic review blockers")
    plans = _json([plan.to_dict() for plan in direction.plans])
    return json.loads(_json({
        "review_attempt": attempt,
        "reviewed_plans_sha256": hashlib.sha256(plans.encode("utf-8")).hexdigest(),
        "findings": records,
    }))


COMMERCIAL_REVIEW_POLICY = "cover-commercial-review.v1"
COMMERCIAL_BLOCKER_CATEGORIES = frozenset({"material_story_contradiction", "author_constraint", "source_integrity"})
COMMERCIAL_ADVISORY_CATEGORIES = frozenset({"marketing_appeal", "visual_ambiguity", "composition_craft", "thumbnail_readability"})


@dataclass(frozen=True)
class CommercialSemanticFinding(CampaignSemanticFinding):
    category: str = ""
    claim: str = ""
    field: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if (self.category not in COMMERCIAL_BLOCKER_CATEGORIES
                or not isinstance(self.claim, str) or not self.claim.strip()
                or not isinstance(self.field, str) or not self.field.strip()):
            raise ValueError("Commercial blockers require a fixed hard category, factual claim and field")


def commercial_validation_advisories(direction, findings):
    """Project application-classified warnings; never reclassify hard failures."""
    result = []
    for finding in findings:
        if finding.severity != "warning":
            continue
        identifier = next((p.concept_id for p in direction.plans
                           if finding.evidence == p.concept_id or finding.evidence.startswith(p.concept_id + ":")), "portfolio")
        plans = [p for p in direction.plans if identifier == "portfolio" or p.concept_id == identifier]
        refs = list(dict.fromkeys(ref for p in plans for ref in p.story_evidence_refs
                                  if ref.startswith("evidence:")))
        result.append({"category": "composition_craft", "concept_id": identifier,
                       "message": finding.message, "code": finding.code,
                       "evidence_refs": refs, "source": "local_validation"})
    return result
