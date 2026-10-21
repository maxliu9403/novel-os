"""Author-confirmed narrative length, volume, and series contracts.

Novel classification describes what a story is.  This module describes how it
is packaged.  The author owns that decision: inference is only a recommendation
until the prompt supplies an explicit chapter target or a confirmed contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

try:  # Package imports in tests; top-level imports in the legacy CLI.
    from .novel_classification import NovelClassification
except ImportError:  # pragma: no cover
    from novel_classification import NovelClassification


SCHEMA_VERSION = "narrative-format.v1"
SERIALIZATION_SCHEMA_VERSION = "novel-serialization.v1"
MODES = frozenset({
    "short_novel",
    "standalone_long",
    "multi_volume",
    "series_installment",
})
LONG_MODES = frozenset({"standalone_long", "multi_volume", "series_installment"})
SELECTION_SOURCES = frozenset({
    "author_selected",
    "recommended_then_confirmed",
    "prompt_explicit",
    "engine_recommended",
    "legacy_migration",
})
CONFIRMATION_STATUSES = frozenset({"confirmed", "pending_confirmation"})
RECOMMENDATIONS = frozenset({"short_preferred", "flexible", "long_preferred"})
REQUESTED_FITS = frozenset({
    "strong_fit",
    "supported_with_expansion",
    "high_extension_risk",
    "compressed_scope",
})
_FORMAT_BLOCK = re.compile(
    r"\[NARRATIVE_FORMAT_JSON\]\s*(.*?)\s*\[/NARRATIVE_FORMAT_JSON\]",
    re.IGNORECASE | re.DOTALL,
)
_DIMENSIONS = (
    "conflict_renewal",
    "character_arc_capacity",
    "reveal_ladder",
    "subplot_capacity",
    "world_expansion",
    "volume_payoff_capacity",
)
_DIMENSION_WEIGHTS = {
    "conflict_renewal": 0.25,
    "character_arc_capacity": 0.20,
    "reveal_ladder": 0.15,
    "subplot_capacity": 0.15,
    "world_expansion": 0.15,
    "volume_payoff_capacity": 0.10,
}


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _integer(value: Any, name: str, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _bounded_score(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError(f"{name} must be an integer from 0 to 100")
    return value


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a string list")
    result = tuple(str(item).strip() for item in value if str(item).strip())
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique values")
    return result


@dataclass(frozen=True)
class LongFormSuitability:
    score: int
    recommendation: str
    requested_fit: str
    dimensions: Mapping[str, int]
    strengths: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    confidence: float = 0.7

    def __post_init__(self) -> None:
        _bounded_score(self.score, "suitability score")
        if self.recommendation not in RECOMMENDATIONS:
            raise ValueError("suitability recommendation is unknown")
        if self.requested_fit not in REQUESTED_FITS:
            raise ValueError("suitability requested_fit is unknown")
        if set(self.dimensions) != set(_DIMENSIONS):
            raise ValueError("suitability dimensions are incomplete")
        normalized = {
            name: _bounded_score(self.dimensions[name], f"dimension {name}")
            for name in _DIMENSIONS
        }
        object.__setattr__(self, "dimensions", normalized)
        object.__setattr__(self, "strengths", _string_tuple(self.strengths, "strengths"))
        object.__setattr__(self, "risks", _string_tuple(self.risks, "risks"))
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("suitability confidence must be numeric")
        if not 0 <= float(self.confidence) <= 1:
            raise ValueError("suitability confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", round(float(self.confidence), 3))

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "recommendation": self.recommendation,
            "requested_fit": self.requested_fit,
            "dimensions": dict(self.dimensions),
            "strengths": list(self.strengths),
            "risks": list(self.risks),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LongFormSuitability":
        if not isinstance(value, Mapping):
            raise ValueError("long_form_suitability must be an object")
        return cls(
            score=value["score"],
            recommendation=str(value["recommendation"]),
            requested_fit=str(value["requested_fit"]),
            dimensions=dict(value["dimensions"]),
            strengths=tuple(value.get("strengths") or ()),
            risks=tuple(value.get("risks") or ()),
            confidence=value.get("confidence", 0.7),
        )


@dataclass(frozen=True)
class VolumeSpan:
    volume_id: str
    volume_number: int
    chapter_start: int
    chapter_end: int
    target_words: int
    structural_role: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"volume_[0-9]{2,3}", self.volume_id):
            raise ValueError("volume_id must use volume_NN format")
        _integer(self.volume_number, "volume_number")
        _integer(self.chapter_start, "chapter_start")
        _integer(self.chapter_end, "chapter_end")
        if self.chapter_end < self.chapter_start:
            raise ValueError("volume chapter range is reversed")
        _integer(self.target_words, "volume target_words")
        object.__setattr__(
            self, "structural_role", _required(self.structural_role, "structural_role")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "volume_id": self.volume_id,
            "volume_number": self.volume_number,
            "chapter_start": self.chapter_start,
            "chapter_end": self.chapter_end,
            "target_words": self.target_words,
            "structural_role": self.structural_role,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "VolumeSpan":
        return cls(
            volume_id=str(value["volume_id"]),
            volume_number=value["volume_number"],
            chapter_start=value["chapter_start"],
            chapter_end=value["chapter_end"],
            target_words=value["target_words"],
            structural_role=str(value["structural_role"]),
        )


@dataclass(frozen=True)
class NarrativeFormat:
    mode: str
    total_chapters: int
    target_words: int
    volumes: tuple[VolumeSpan, ...]
    long_form_suitability: LongFormSuitability
    selection_source: str
    confirmation_status: str
    series_id: str = ""
    series_title: str = ""
    series_book_number: int | None = None
    planned_books: int | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("narrative format schema_version is unsupported")
        if self.mode not in MODES:
            raise ValueError("narrative format mode is unknown")
        _integer(self.total_chapters, "total_chapters")
        if type(self.target_words) is not int or self.target_words < self.total_chapters:
            raise ValueError("target_words must be at least total_chapters")
        if not self.volumes:
            raise ValueError("narrative format must contain a volume structure")
        if self.selection_source not in SELECTION_SOURCES:
            raise ValueError("selection_source is unknown")
        if self.confirmation_status not in CONFIRMATION_STATUSES:
            raise ValueError("confirmation_status is unknown")
        expected_start = 1
        for expected_number, volume in enumerate(self.volumes, start=1):
            if volume.volume_number != expected_number:
                raise ValueError("volume numbers must be contiguous from 1")
            if volume.volume_id != f"volume_{expected_number:02d}":
                raise ValueError("volume ids must match their volume numbers")
            if volume.chapter_start != expected_start:
                raise ValueError("volume chapter ranges must be contiguous from 1")
            expected_start = volume.chapter_end + 1
        if expected_start - 1 != self.total_chapters:
            raise ValueError("volume structure must cover every chapter exactly once")
        if self.mode in {"short_novel", "standalone_long"} and len(self.volumes) != 1:
            raise ValueError("single-book format must contain one volume span")
        if self.mode == "multi_volume" and len(self.volumes) < 2:
            raise ValueError("multi_volume format must contain at least two volumes")
        if self.mode == "series_installment":
            object.__setattr__(self, "series_id", _required(self.series_id, "series_id"))
            object.__setattr__(self, "series_title", _required(self.series_title, "series_title"))
            _integer(self.series_book_number, "series_book_number")
            _integer(self.planned_books, "planned_books")
            if self.series_book_number > self.planned_books:
                raise ValueError("series_book_number exceeds planned_books")
        elif any((self.series_id, self.series_title, self.series_book_number, self.planned_books)):
            raise ValueError("series fields belong only to series_installment mode")

    @property
    def is_long_form(self) -> bool:
        return self.mode in LONG_MODES

    @property
    def chapter_target_words(self) -> int:
        return max(1, round(self.target_words / self.total_chapters))

    def _identity(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "total_chapters": self.total_chapters,
            "target_words": self.target_words,
            "volumes": [item.to_dict() for item in self.volumes],
            "series": {
                "series_id": self.series_id,
                "title": self.series_title,
                "book_number": self.series_book_number,
                "planned_books": self.planned_books,
            },
        }

    @property
    def format_id(self) -> str:
        encoded = json.dumps(
            self._identity(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return f"narrative-format:{hashlib.sha256(encoded).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._identity(),
            "format_id": self.format_id,
            "is_long_form": self.is_long_form,
            "chapter_target_words": self.chapter_target_words,
            "volume_count": len(self.volumes),
            "selection_source": self.selection_source,
            "confirmation_status": self.confirmation_status,
            "long_form_suitability": self.long_form_suitability.to_dict(),
            "audit_policy": {
                "interval_chapters": 5,
                "volume_end_required": self.is_long_form,
                "whole_book_required": self.is_long_form,
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NarrativeFormat":
        if not isinstance(value, Mapping):
            raise ValueError("narrative format must be an object")
        series = value.get("series") or {}
        if not isinstance(series, Mapping):
            raise ValueError("narrative format series must be an object")
        result = cls(
            schema_version=str(value.get("schema_version") or SCHEMA_VERSION),
            mode=str(value["mode"]),
            total_chapters=value["total_chapters"],
            target_words=value["target_words"],
            volumes=tuple(VolumeSpan.from_dict(item) for item in value["volumes"]),
            long_form_suitability=LongFormSuitability.from_dict(
                value["long_form_suitability"]
            ),
            selection_source=str(value["selection_source"]),
            confirmation_status=str(value["confirmation_status"]),
            series_id=str(series.get("series_id") or ""),
            series_title=str(series.get("title") or ""),
            series_book_number=series.get("book_number"),
            planned_books=series.get("planned_books"),
        )
        supplied_id = value.get("format_id")
        if supplied_id is not None and supplied_id != result.format_id:
            raise ValueError("format_id does not match narrative format fields")
        if "is_long_form" in value and value["is_long_form"] is not result.is_long_form:
            raise ValueError("is_long_form does not match narrative format mode")
        if "volume_count" in value and value["volume_count"] != len(result.volumes):
            raise ValueError("volume_count does not match volume structure")
        return result

    def with_confirmation(
        self, selection_source: str, confirmation_status: str = "confirmed"
    ) -> "NarrativeFormat":
        return replace(
            self,
            selection_source=selection_source,
            confirmation_status=confirmation_status,
        )

    def volume_for_chapter(self, chapter: int) -> VolumeSpan:
        _integer(chapter, "chapter")
        for volume in self.volumes:
            if volume.chapter_start <= chapter <= volume.chapter_end:
                return volume
        raise ValueError(f"chapter {chapter} is outside the narrative format")


def _clamp(value: int) -> int:
    return max(0, min(100, value))


def _classification(value: NovelClassification | Mapping[str, Any]) -> NovelClassification:
    return value if isinstance(value, NovelClassification) else NovelClassification.from_dict(value)


def assess_long_form_suitability(
    classification: NovelClassification | Mapping[str, Any],
    *,
    chapters: int,
    premise: str = "",
    raw_prompt: str = "",
) -> LongFormSuitability:
    """Produce an advisory story-capacity score; it never overrides the author."""

    value = _classification(classification)
    scores = {
        "conflict_renewal": 48,
        "character_arc_capacity": 54,
        "reveal_ladder": 43,
        "subplot_capacity": 46,
        "world_expansion": 38,
        "volume_payoff_capacity": 46,
    }

    def adjust(**changes: int) -> None:
        for name, amount in changes.items():
            scores[name] = _clamp(scores[name] + amount)

    primary_adjustments = {
        "womens_fiction": dict(character_arc_capacity=10, subplot_capacity=8),
        "family_drama": dict(character_arc_capacity=9, subplot_capacity=10),
        "romance": dict(character_arc_capacity=8, volume_payoff_capacity=4),
        "mystery": dict(reveal_ladder=22, conflict_renewal=6),
        "thriller": dict(reveal_ladder=16, conflict_renewal=13),
        "fantasy": dict(world_expansion=24, subplot_capacity=9),
        "supernatural": dict(world_expansion=22, conflict_renewal=8),
    }
    adjust(**primary_adjustments.get(value.primary_genre_id, {}))
    story_adjustments = {
        "werewolf": dict(world_expansion=18, conflict_renewal=13, subplot_capacity=8),
        "mafia": dict(conflict_renewal=15, subplot_capacity=10, world_expansion=8),
        "royal_intrigue": dict(reveal_ladder=17, subplot_capacity=15, world_expansion=10),
        "dark_romance": dict(conflict_renewal=9, character_arc_capacity=7),
        "revenge": dict(conflict_renewal=8, reveal_ladder=6, volume_payoff_capacity=10),
        "domestic_betrayal": dict(character_arc_capacity=8, world_expansion=-10),
        "marriage_crisis": dict(character_arc_capacity=10, world_expansion=-11),
        "second_chance": dict(character_arc_capacity=7, conflict_renewal=-3),
    }
    for story_type in value.story_type_ids:
        adjust(**story_adjustments.get(story_type, {}))

    text = f"{premise}\n{raw_prompt}".casefold()
    cue_adjustments = {
        "multi-generational": dict(character_arc_capacity=7, subplot_capacity=8),
        "multigenerational": dict(character_arc_capacity=7, subplot_capacity=8),
        "multiple generations": dict(character_arc_capacity=7, subplot_capacity=8),
        "family secret": dict(reveal_ladder=8, subplot_capacity=4),
        "community": dict(subplot_capacity=5, world_expansion=4),
        "inheritance": dict(reveal_ladder=5, subplot_capacity=4),
        "conspiracy": dict(reveal_ladder=8, conflict_renewal=6),
        "多代": dict(character_arc_capacity=7, subplot_capacity=8),
        "家族秘密": dict(reveal_ladder=8, subplot_capacity=4),
        "阴谋": dict(reveal_ladder=8, conflict_renewal=6),
        "one night": dict(subplot_capacity=-5, world_expansion=-5),
        "single incident": dict(conflict_renewal=-6, subplot_capacity=-5),
        "一夜": dict(subplot_capacity=-5, world_expansion=-5),
    }
    matched_cues = 0
    for cue, changes in cue_adjustments.items():
        if cue in text:
            matched_cues += 1
            adjust(**changes)

    score = round(sum(scores[name] * _DIMENSION_WEIGHTS[name] for name in _DIMENSIONS))
    recommendation = (
        "long_preferred" if score >= 70 else "flexible" if score >= 52 else "short_preferred"
    )
    if chapters <= 50:
        requested_fit = "compressed_scope" if recommendation == "long_preferred" else "strong_fit"
    elif score >= 70:
        requested_fit = "strong_fit"
    elif score >= 52:
        requested_fit = "supported_with_expansion"
    else:
        requested_fit = "high_extension_risk"

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    strengths = tuple(name for name, amount in ranked[:2] if amount >= 58)
    risks = tuple(name for name, amount in reversed(ranked) if amount < 50)[:3]
    if chapters > 50 and requested_fit != "strong_fit":
        risks += ("long_form_requires_distinct_volume_engines",)
    return LongFormSuitability(
        score=score,
        recommendation=recommendation,
        requested_fit=requested_fit,
        dimensions=scores,
        strengths=strengths,
        risks=tuple(dict.fromkeys(risks)),
        confidence=min(0.92, 0.62 + 0.04 * len(value.story_type_ids) + 0.02 * matched_cues),
    )


def _volume_roles(count: int) -> list[str]:
    if count == 4:
        return [
            "rupture_and_awakening",
            "boundary_and_countermove",
            "identity_and_cost",
            "reckoning_and_new_order",
        ]
    if count == 1:
        return ["complete_book_arc"]
    roles = ["opening_disruption"]
    roles.extend(f"escalation_and_reversal_{number}" for number in range(2, count))
    roles.append("final_reckoning")
    return roles


def _volume_spans(chapters: int, target_words: int, count: int) -> tuple[VolumeSpan, ...]:
    base, remainder = divmod(chapters, count)
    word_base, word_remainder = divmod(target_words, count)
    roles = _volume_roles(count)
    start = 1
    spans: list[VolumeSpan] = []
    for index in range(1, count + 1):
        chapter_count = base + (1 if index <= remainder else 0)
        volume_words = word_base + (1 if index <= word_remainder else 0)
        end = start + chapter_count - 1
        spans.append(VolumeSpan(
            volume_id=f"volume_{index:02d}",
            volume_number=index,
            chapter_start=start,
            chapter_end=end,
            target_words=volume_words,
            structural_role=roles[index - 1],
        ))
        start = end + 1
    return tuple(spans)


def infer_narrative_format(
    classification: NovelClassification | Mapping[str, Any],
    *,
    chapters: int,
    target_words: int,
    premise: str = "",
    raw_prompt: str = "",
    explicit_length: bool = False,
    source: str | None = None,
    mode: str | None = None,
    volume_count: int | None = None,
    series_id: str = "",
    series_title: str = "",
    series_book_number: int | None = None,
    planned_books: int | None = None,
) -> NarrativeFormat:
    _integer(chapters, "chapters")
    if type(target_words) is not int or target_words < chapters:
        raise ValueError("target_words must be at least chapters")
    selected_mode = mode or (
        "short_novel" if chapters <= 50 else "standalone_long" if chapters < 60 else "multi_volume"
    )
    if selected_mode not in MODES:
        raise ValueError("narrative format mode is unknown")
    if volume_count is not None:
        _integer(volume_count, "volume_count")
    if selected_mode in {"short_novel", "standalone_long"}:
        if volume_count not in {None, 1}:
            raise ValueError("single-volume mode requires volume_count 1")
        count = 1
    elif selected_mode == "multi_volume":
        count = volume_count or max(2, math.ceil(chapters / 20))
        if count < 2:
            raise ValueError("multi_volume requires at least two volumes")
    else:
        count = volume_count or 1
    selection_source = source or ("prompt_explicit" if explicit_length else "engine_recommended")
    confirmation_status = "confirmed" if explicit_length else "pending_confirmation"
    return NarrativeFormat(
        mode=selected_mode,
        total_chapters=chapters,
        target_words=target_words,
        volumes=_volume_spans(chapters, target_words, count),
        long_form_suitability=assess_long_form_suitability(
            classification, chapters=chapters, premise=premise, raw_prompt=raw_prompt
        ),
        selection_source=selection_source,
        confirmation_status=confirmation_status,
        series_id=series_id,
        series_title=series_title,
        series_book_number=series_book_number,
        planned_books=planned_books,
    )


def parse_narrative_format_block(prompt: str) -> NarrativeFormat | None:
    matches = _FORMAT_BLOCK.findall(str(prompt or ""))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("prompt must contain exactly one NARRATIVE_FORMAT_JSON block")
    body = matches[0].strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, re.IGNORECASE | re.DOTALL)
    if fenced:
        body = fenced.group(1)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("NARRATIVE_FORMAT_JSON block contains invalid JSON") from exc
    value = NarrativeFormat.from_dict(payload)
    if value.confirmation_status != "confirmed":
        raise ValueError("locked narrative format must be author-confirmed")
    return value


def narrative_format_from_metadata(
    metadata: Mapping[str, Any],
    *,
    classification: NovelClassification | Mapping[str, Any],
    premise: str = "",
) -> NarrativeFormat:
    existing = metadata.get("narrative_format")
    if isinstance(existing, Mapping):
        return NarrativeFormat.from_dict(existing)
    raw_chapters = metadata.get("target_chapters")
    chapters = int(raw_chapters) if raw_chapters is not None else 32
    target_words = int(metadata.get("target_word_count") or chapters * 2500)
    return infer_narrative_format(
        classification,
        chapters=chapters,
        target_words=max(chapters, target_words),
        premise=premise or str(metadata.get("premise") or ""),
        explicit_length=metadata.get("target_chapters") is not None,
        source="legacy_migration",
    )


def volume_contract_template(format_contract: NarrativeFormat) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for volume in format_contract.volumes:
        span = volume.chapter_end - volume.chapter_start + 1
        climax = volume.chapter_end if span <= 3 else volume.chapter_end - 1
        result.append({
            **volume.to_dict(),
            "title": "<distinct volume title>",
            "central_conflict": "<this volume's distinct form of pressure>",
            "volume_promise": "<reader value this volume must deliver>",
            "protagonist_shift": "<irreversible belief, agency, or relationship change>",
            "climax_chapter": climax,
            "payoff": "<observable payoff delivered before the volume closes>",
            "carryover_hook": (
                "<new consequence that opens the next volume>"
                if volume.volume_number < len(format_contract.volumes)
                else ""
            ),
        })
    return result


def _normalized_story_value(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", value.casefold())


def _story_required(value: Any, name: str) -> str:
    text = _required(value, name)
    if text == "..." or (text.startswith("<") and text.endswith(">")):
        raise ValueError(f"{name} must replace the template placeholder")
    return text


def validate_volume_contracts(
    format_contract: NarrativeFormat,
    raw_contracts: Any,
    *,
    allow_legacy_defaults: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(raw_contracts, list):
        if not allow_legacy_defaults:
            raise ValueError("long-form foundation must define volume_contracts")
        raw_contracts = []
    if any(not isinstance(item, Mapping) for item in raw_contracts):
        raise ValueError("volume_contracts must contain objects")
    raw_ids = [str(item.get("volume_id") or "") for item in raw_contracts]
    if len(raw_ids) != len(set(raw_ids)):
        raise ValueError("volume_contracts contain duplicate volume_id values")
    by_id = {
        str(item.get("volume_id") or ""): item
        for item in raw_contracts
        if isinstance(item, Mapping)
    }
    result: list[dict[str, Any]] = []
    distinct: dict[str, set[str]] = {
        "central_conflict": set(), "volume_promise": set(), "payoff": set()
    }
    for span in format_contract.volumes:
        raw = by_id.get(span.volume_id)
        if raw is None:
            if not allow_legacy_defaults and format_contract.is_long_form:
                raise ValueError(f"volume contract is missing: {span.volume_id}")
            raw = {
                "volume_id": span.volume_id,
                "title": f"Volume {span.volume_number}",
                "central_conflict": f"Story movement for chapters {span.chapter_start}-{span.chapter_end}",
                "volume_promise": "Advance the central story with a visible change",
                "protagonist_shift": "The protagonist reaches the next durable stage",
                "climax_chapter": span.chapter_end,
                "payoff": "The volume's immediate story question changes state",
                "carryover_hook": (
                    "The next stage of the central conflict begins"
                    if span.volume_number < len(format_contract.volumes) else ""
                ),
            }
        for field, expected in (
            ("volume_number", span.volume_number),
            ("chapter_start", span.chapter_start),
            ("chapter_end", span.chapter_end),
        ):
            if field in raw and raw[field] != expected:
                raise ValueError(f"{span.volume_id} changed locked {field}")
        title = _story_required(raw.get("title"), f"{span.volume_id}.title")
        central_conflict = _story_required(
            raw.get("central_conflict"), f"{span.volume_id}.central_conflict"
        )
        volume_promise = _story_required(
            raw.get("volume_promise"), f"{span.volume_id}.volume_promise"
        )
        protagonist_shift = _story_required(
            raw.get("protagonist_shift"), f"{span.volume_id}.protagonist_shift"
        )
        payoff = _story_required(raw.get("payoff"), f"{span.volume_id}.payoff")
        climax = _integer(raw.get("climax_chapter"), f"{span.volume_id}.climax_chapter")
        earliest_climax = span.chapter_start + max(
            0, math.floor((span.chapter_end - span.chapter_start + 1) * 0.65) - 1
        )
        if not earliest_climax <= climax <= span.chapter_end:
            raise ValueError(
                f"{span.volume_id}.climax_chapter must be in the final 35% of its range"
            )
        carryover = str(raw.get("carryover_hook") or "").strip()
        if span.volume_number < len(format_contract.volumes) and not carryover:
            raise ValueError(f"{span.volume_id}.carryover_hook is required")
        if carryover and (
            carryover == "..." or (carryover.startswith("<") and carryover.endswith(">"))
        ):
            raise ValueError(
                f"{span.volume_id}.carryover_hook must replace the template placeholder"
            )
        for field, text in (
            ("central_conflict", central_conflict),
            ("volume_promise", volume_promise),
            ("payoff", payoff),
        ):
            normalized = _normalized_story_value(text)
            if len(format_contract.volumes) > 1 and normalized in distinct[field]:
                raise ValueError(f"volume contracts repeat the same {field}")
            distinct[field].add(normalized)
        midpoint = span.chapter_start + (span.chapter_end - span.chapter_start) // 2
        obligations = [
            {"event_id": f"{span.volume_id}_promise", "kind": "promise", "due_chapter": span.chapter_start},
            {"event_id": f"{span.volume_id}_midpoint", "kind": "midpoint_revaluation", "due_chapter": midpoint},
            {"event_id": f"{span.volume_id}_climax", "kind": "climax", "due_chapter": climax},
            {"event_id": f"{span.volume_id}_payoff", "kind": "payoff", "due_chapter": span.chapter_end},
        ]
        if carryover:
            obligations.append({
                "event_id": f"{span.volume_id}_carryover",
                "kind": "carryover_hook",
                "due_chapter": span.chapter_end,
            })
        result.append({
            **span.to_dict(),
            "title": title,
            "central_conflict": central_conflict,
            "volume_promise": volume_promise,
            "protagonist_shift": protagonist_shift,
            "climax_chapter": climax,
            "payoff": payoff,
            "carryover_hook": carryover,
            "obligations": obligations,
        })
    if len(by_id) != len(result):
        extra = sorted(set(by_id) - {item.volume_id for item in format_contract.volumes})
        if extra:
            raise ValueError("unknown volume contracts: " + ", ".join(extra))
    return result


def chapter_binding(
    format_contract: NarrativeFormat,
    chapter: int,
    volume_contracts: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    span = format_contract.volume_for_chapter(chapter)
    volume_contract = next(
        (item for item in (volume_contracts or ()) if item.get("volume_id") == span.volume_id),
        {},
    )
    climax = int(volume_contract.get("climax_chapter") or span.chapter_end)
    midpoint = span.chapter_start + (span.chapter_end - span.chapter_start) // 2
    if chapter == span.chapter_start:
        role = "setup"
    elif chapter == climax:
        role = "climax"
    elif chapter > climax:
        role = "aftermath"
    elif chapter == midpoint:
        role = "turning_point"
    else:
        role = "escalation"
    return {
        "volume_id": span.volume_id,
        "volume_number": span.volume_number,
        "chapter_in_volume": chapter - span.chapter_start + 1,
        "volume_role": role,
        "series_id": format_contract.series_id,
        "series_book_number": format_contract.series_book_number,
    }


def bind_foundation_serialization(
    foundation: Mapping[str, Any],
    format_contract: NarrativeFormat,
) -> dict[str, Any]:
    value = dict(foundation)
    supplied = value.get("narrative_format")
    if supplied is not None and NarrativeFormat.from_dict(supplied).format_id != format_contract.format_id:
        raise ValueError("story foundation changed the confirmed narrative format")
    volumes = validate_volume_contracts(
        format_contract,
        value.get("volume_contracts"),
        allow_legacy_defaults=not format_contract.is_long_form,
    )
    chapters = value.get("chapters")
    if not isinstance(chapters, list):
        raise ValueError("story foundation chapters must be a list")
    bound: list[dict[str, Any]] = []
    for raw in chapters:
        if not isinstance(raw, Mapping):
            raise ValueError("story foundation chapter must be an object")
        item = dict(raw)
        item.update(chapter_binding(format_contract, int(item["number"]), volumes))
        bound.append(item)
    value["narrative_format"] = format_contract.to_dict()
    value["volume_contracts"] = volumes
    value["chapters"] = bound
    return value


def serialization_payload(
    format_contract: NarrativeFormat,
    volume_contracts: Any,
    *,
    allow_legacy_defaults: bool = False,
) -> dict[str, Any]:
    volumes = validate_volume_contracts(
        format_contract,
        volume_contracts,
        allow_legacy_defaults=allow_legacy_defaults,
    )
    identity = {
        "schema_version": SERIALIZATION_SCHEMA_VERSION,
        "format": format_contract.to_dict(),
        "volumes": volumes,
    }
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        **identity,
        "serialization_id": f"serialization:{hashlib.sha256(encoded).hexdigest()}",
    }


def validate_serialization(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != SERIALIZATION_SCHEMA_VERSION:
        raise ValueError("serialization schema_version is unsupported")
    format_contract = NarrativeFormat.from_dict(value["format"])
    canonical = serialization_payload(format_contract, value["volumes"])
    if value.get("serialization_id") != canonical["serialization_id"]:
        raise ValueError("serialization_id does not match serialization fields")
    return canonical


def serialization_from_state(
    metadata: Mapping[str, Any],
    story_bible: Mapping[str, Any],
    *,
    classification: NovelClassification | Mapping[str, Any],
) -> dict[str, Any]:
    format_contract = narrative_format_from_metadata(
        metadata,
        classification=classification,
        premise=str(story_bible.get("premise") or ""),
    )
    return serialization_payload(
        format_contract,
        story_bible.get("volume_contracts"),
        allow_legacy_defaults=True,
    )


def obligations_for_chapter(
    volume_contracts: Sequence[Mapping[str, Any]], chapter: int
) -> tuple[dict[str, Any], ...]:
    return tuple(
        dict(obligation)
        for volume in volume_contracts
        for obligation in (volume.get("obligations") or ())
        if isinstance(obligation, Mapping) and obligation.get("due_chapter") == chapter
    )


def canonical_json_bytes(value: NarrativeFormat | Mapping[str, Any]) -> bytes:
    contract = value if isinstance(value, NarrativeFormat) else NarrativeFormat.from_dict(value)
    return json.dumps(
        contract.to_dict(), ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def markdown_front_matter_fields(serialization: Mapping[str, Any]) -> list[str]:
    value = validate_serialization(serialization)
    format_contract = value["format"]
    return [
        f"serialization_schema: {json.dumps(value['schema_version'])}",
        f"serialization_id: {json.dumps(value['serialization_id'])}",
        f"narrative_format_id: {json.dumps(format_contract['format_id'])}",
        f"narrative_mode: {json.dumps(format_contract['mode'])}",
        f"total_chapters: {format_contract['total_chapters']}",
        f"volume_count: {format_contract['volume_count']}",
        f"series_id: {json.dumps((format_contract.get('series') or {}).get('series_id') or '')}",
    ]


def format_catalog_payload() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "modes": [
            {"id": "short_novel", "labels": {"zh-CN": "短篇小说", "en-US": "Short novel"}},
            {"id": "standalone_long", "labels": {"zh-CN": "单本长篇", "en-US": "Standalone long novel"}},
            {"id": "multi_volume", "labels": {"zh-CN": "单本多卷", "en-US": "Multi-volume novel"}},
            {"id": "series_installment", "labels": {"zh-CN": "系列分册", "en-US": "Series installment"}},
        ],
        "decision_policy": {
            "author_confirmation_required_in_workshop": True,
            "explicit_chapter_target_counts_as_author_choice": True,
            "engine_suitability_is_advisory": True,
            "default_volume_target_chapters": 20,
            "long_form_threshold_chapters": 51,
        },
    }


__all__ = [
    "LONG_MODES",
    "MODES",
    "NarrativeFormat",
    "LongFormSuitability",
    "VolumeSpan",
    "assess_long_form_suitability",
    "bind_foundation_serialization",
    "canonical_json_bytes",
    "chapter_binding",
    "format_catalog_payload",
    "infer_narrative_format",
    "markdown_front_matter_fields",
    "narrative_format_from_metadata",
    "obligations_for_chapter",
    "parse_narrative_format_block",
    "serialization_from_state",
    "serialization_payload",
    "validate_serialization",
    "validate_volume_contracts",
    "volume_contract_template",
]
