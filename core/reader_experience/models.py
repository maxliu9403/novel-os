"""Reader-review contracts. Literary judgements are advisory; evidence is verified."""
from __future__ import annotations
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

VERSION = "reader-experience.v1"
EXTRACTOR = "manuscript-reader.v1"
DIMENSIONS = ("plot", "character", "prose", "world", "theme", "emotion", "engagement", "expectation")
ACTIVE = {"queued", "reading", "synthesizing", "verifying"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def text_sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReviewOptions(Strict):
    first_chapter: int = Field(default=1, ge=1, le=10000)
    last_chapter: int | None = Field(default=None, ge=1, le=10000)
    language: str = Field(default="中文", min_length=1, max_length=50)
    audience: str = Field(default="", max_length=1000)
    # Public-facing expectations only; never fetch private skeletons or run plans.
    public_description: str = Field(default="", max_length=3000)
    segment_tokens: int = Field(default=8000, ge=1000, le=12000)
    input_tokens: int = Field(default=24000, ge=8000, le=64000)
    output_tokens: int = Field(default=6000, ge=2000, le=12000)
    max_calls: int = Field(default=40, ge=2, le=256)
    request_seconds: int = Field(default=300, ge=30, le=900)
    total_seconds: int = Field(default=1800, ge=60, le=43200)
    max_estimated_cny: float | None = Field(default=None, gt=0, le=10000)


class Quote(Strict):
    unit_id: str
    quote: str = Field(min_length=1, max_length=1600)
    # Absolute Unicode codepoint offset in cleaned chapter; omit only if unique.
    start: int | None = Field(default=None, ge=0)


class Fact(Strict):
    text: str = Field(min_length=1, max_length=1000)
    evidence: list[Quote] = Field(min_length=1, max_length=3)


class Observation(Strict):
    dimension: Literal["plot", "character", "prose", "world", "theme", "emotion", "engagement"]
    observation: str = Field(min_length=1, max_length=1200)
    reader_impact: str = Field(min_length=1, max_length=1000)
    suggested_action: str = Field(default="", max_length=1000)
    preserve: str = Field(default="", max_length=1000)
    uncertainty: str = Field(default="", max_length=1000)
    evidence: list[Quote] = Field(min_length=1, max_length=3)


class Reading(Strict):
    facts: list[Fact] = Field(max_length=12)
    strengths: list[Observation] = Field(max_length=5)
    issues: list[Observation] = Field(max_length=5)
    # A bounded, explicitly lossy facts-only context for the next segment.
    continuity_summary: str = Field(max_length=4500)


class Finding(Strict):
    dimension: Literal["plot", "character", "prose", "world", "theme", "emotion", "engagement", "expectation"]
    scope: Literal["local", "cross_chapter"]
    observation: str = Field(min_length=1, max_length=1500)
    reader_impact: str = Field(min_length=1, max_length=1000)
    suggested_action: str = Field(default="", max_length=1000)
    preserve: str = Field(default="", max_length=1000)
    uncertainty: str = Field(default="", max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=6)


class Dimension(Strict):
    dimension: Literal["plot", "character", "prose", "world", "theme", "emotion", "engagement", "expectation"]
    status: Literal["strength", "improve", "disagreement", "insufficient", "not_applicable"]
    explanation: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=6)


class Report(Strict):
    strengths: list[Finding] = Field(max_length=5)
    issues: list[Finding] = Field(max_length=5)
    dimensions: list[Dimension] = Field(min_length=8, max_length=8)
    uncertainties: list[str] = Field(max_length=12)


class Digest(Strict):
    covered_units: list[str]
    facts: str = Field(max_length=4500)
    observations: str = Field(max_length=4500)
    evidence_ids: list[str] = Field(max_length=40)
    uncertainties: list[str] = Field(max_length=10)


def parse_reply(raw, schema):
    # Accept a sole fenced object, never fish an object out of explanatory prose.
    text = raw.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    return schema.model_validate_json(text).model_dump()
