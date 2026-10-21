"""Complete deterministic evidence-review replies for unrelated cover tests.

These are protocol fixtures, not a substitute for actual source adjudication.
Evidence receipt validation tests must supply their own adversarial responses.
"""
from typing import Any, Mapping


def supported_review(request: Mapping[str, Any], *, accepted: bool = True,
                     findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "accepted": accepted,
        "findings": list(findings) if findings is not None else [],
        "evidence_reviews": [{
            "claim_id": claim["claim_id"],
            "verdict": "supported",
            "evidence_refs": list(claim["evidence_refs"]),
            "reasoning": "The cited source supports this fixture claim.",
        } for claim in request["evidence_claims"]],
    }
