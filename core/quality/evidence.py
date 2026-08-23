"""Verification helpers for exact artifact-bound evidence."""

from __future__ import annotations

import hashlib
import re

from .models import EvidenceSpan


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def verify_evidence(
    text: str, actual_artifact_sha: str, evidence: EvidenceSpan
) -> bool:
    """Return whether evidence identifies content in the exact UTF-8 artifact."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(evidence, EvidenceSpan):
        raise TypeError("evidence must be an EvidenceSpan")

    recomputed_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if (
        not isinstance(actual_artifact_sha, str)
        or not _SHA256_RE.fullmatch(actual_artifact_sha)
        or recomputed_sha != actual_artifact_sha
        or actual_artifact_sha != evidence.artifact_sha256
    ):
        return False

    has_span = evidence.start is not None and evidence.end is not None
    if has_span and evidence.end > len(text):
        return False
    if evidence.quote is not None and has_span:
        return text[evidence.start : evidence.end] == evidence.quote
    if evidence.quote is not None:
        return evidence.quote in text
    return has_span and bool(text[evidence.start : evidence.end])


__all__ = ["verify_evidence"]
