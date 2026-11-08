"""Validate serialized shapes before constructing narrative domain objects.

Domain dataclasses retain cross-field/business invariants. This boundary reports
all missing or mistyped fields together without coercing model text into facts.
Derived fields are checked by NarrativeFormat, never supplied by these models.
"""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class InputRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


Nonblank = Annotated[str, Field(min_length=1, pattern=r"\S")]
PositiveInt = Annotated[int, Field(ge=1)]
Score = Annotated[int, Field(ge=0, le=100)]


class DimensionsInput(InputRecord):
    conflict_renewal: Score
    character_arc_capacity: Score
    reveal_ladder: Score
    subplot_capacity: Score
    world_expansion: Score
    volume_payoff_capacity: Score


class SuitabilityInput(InputRecord):
    score: Score
    recommendation: Nonblank
    requested_fit: Nonblank
    dimensions: DimensionsInput
    strengths: list[Nonblank] = Field(default_factory=list)
    risks: list[Nonblank] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0, le=1)


class VolumeInput(InputRecord):
    volume_id: Nonblank
    volume_number: PositiveInt
    chapter_start: PositiveInt
    chapter_end: PositiveInt
    target_words: PositiveInt
    structural_role: Nonblank


class SeriesInput(InputRecord):
    series_id: str = ""
    title: str = ""
    book_number: PositiveInt | None = None
    planned_books: PositiveInt | None = None


class FormatInput(InputRecord):
    mode: Nonblank
    total_chapters: PositiveInt
    target_words: PositiveInt
    volumes: list[VolumeInput] = Field(min_length=1)
    long_form_suitability: SuitabilityInput
    selection_source: Nonblank
    confirmation_status: Nonblank
    series: SeriesInput = Field(default_factory=SeriesInput)
    schema_version: Nonblank = "narrative-format.v1"


def validate_input(model: type[InputRecord], value, path: str) -> None:
    """ValueError is the shared domain boundary; never leak Pydantic input values."""
    try:
        model.model_validate(value)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        # Locations can only contain declared model fields and list indices.
        # Omit error messages as they may include supplied values in future schemas.
        issues = []
        for error in errors[:24]:
            location = path + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error["loc"])
            issues.append(f"{location}: {error['type']}")
        if len(errors) > 24:
            issues.append(f"另有 {len(errors) - 24} 项结构错误")
        raise ValueError("分卷／系列格式不完整或字段类型无效：" + "; ".join(issues)) from None
