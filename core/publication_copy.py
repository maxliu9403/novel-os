"""Strict, immutable contracts for reader-facing whole-book publication copy."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any


SCHEMA_VERSION = 1
POLICY_VERSION = "publication-copy-policy.v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_ENGLISH_WORD = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")
_UNICODE_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_SENTENCE_END = re.compile(r"[.!?\u3002\uff01\uff1f]+(?=\s|$)")
_SENTENCE_BREAK = re.compile(r"[.!?\u3002\uff01\uff1f]+[\"')\]]*\s+\S")
_HTML = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_URL = re.compile(r"(?:https?://|www\.|mailto:)", re.IGNORECASE)
_MARKDOWN = re.compile(
    r"^\s{0,3}(?:#{1,6}\s|[-*+]\s|>\s|```)|"
    r"!?\[[^\]]+\]\([^)]+\)|(?:\*\*|__|~~|`[^`]+`)|"
    r"(?<!\w)(?:\*[^*\n]+\*|_[^_\n]+_)(?!\w)|"
    r"^\s*[-*_]{3,}\s*$",
    re.MULTILINE,
)
_PSEUDO_METADATA = re.compile(
    r"(?im)^\s*(?:title|author|genre|chapter|synopsis|blurb|description)\s*:"
)
_PROMOTIONAL_CTA = re.compile(
    r"\b(?:buy|order|download|read|click|subscribe|sign up)\s+(?:it\s+)?(?:now|here|today)\b|"
    r"\b(?:read|discover)\s+more\b|\bstart reading\b|\bget your copy\b|"
    r"\bavailable now\b",
    re.IGNORECASE,
)
_GENERIC_OPENINGS = (
    "a story about",
    "a story of",
    "everything changes",
    "secrets threaten to unravel",
    "will love conquer all",
)
_GENERIC_WORDS = frozenset(
    {
        "a",
        "about",
        "all",
        "and",
        "betrayal",
        "changes",
        "conquer",
        "everything",
        "forever",
        "love",
        "of",
        "one",
        "secrets",
        "story",
        "threaten",
        "to",
        "tonight",
        "unravel",
        "unfolds",
        "will",
    }
)

_TOP_LEVEL_FIELDS = {
    "schema_version",
    "title",
    "language",
    "reader_heading",
    "hook_lead",
    "spoiler_free_blurb",
    "whole_book_core_conflict",
    "source",
    "generation",
    "validation",
}
_CONFLICT_FIELDS = {
    "protagonist",
    "goal",
    "opposition",
    "stakes",
    "escalation",
    "unresolved_choice",
    "evidence",
}
_EVIDENCE_BUCKETS = {"opening", "middle", "late"}
_SOURCE_FIELDS = {
    "run_id",
    "book_check_stage_sha256",
    "source_set_sha256",
    "chapters",
}
_SOURCE_CHAPTER_FIELDS = {"number", "revision_id", "sha256"}
_GENERATION_FIELDS = {
    "conflict_provider",
    "conflict_model",
    "conflict_prompt_version",
    "conflict_response_sha256",
    "writer_provider",
    "writer_model",
    "writer_prompt_version",
    "writer_response_sha256",
    "validator_provider",
    "validator_model",
    "validator_prompt_version",
    "validator_response_sha256",
    "generated_at",
}
_VALIDATION_FIELDS = {
    "policy_version",
    "status",
    "length",
    "checks",
    "reader_pull",
    "claim_evidence",
}
_LENGTH_FIELDS = {"unit", "hook_lead", "spoiler_free_blurb"}
_VALIDATION_CHECK_FIELDS = {
    "whole_book_core_conflict",
    "hook_core_conflict",
    "blurb_core_conflict",
    "protagonist_stakes",
    "spoiler_free",
    "source_supported",
    "not_chapter_one_copy",
}
_SEMANTIC_CHECK_FIELDS = _VALIDATION_CHECK_FIELDS - {"not_chapter_one_copy"}
_READER_PULL_FIELDS = {"status", "checks"}
_READER_PULL_CHECK_FIELDS = {
    "first_glance_clarity",
    "concrete_emotional_stakes",
    "escalating_pressure",
    "protagonist_agency",
    "open_loop",
    "truthful_genre_promise",
}


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize a JSON-compatible value into stable UTF-8 bytes."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _exact_fields(
    value: Any, expected: set[str], name: str
) -> Mapping[str, Any]:
    data = _object(value, name)
    unknown = sorted(set(data) - expected)
    if unknown:
        raise ValueError(f"unknown {name} field: {unknown[0]}")
    missing = sorted(expected - set(data))
    if missing:
        raise ValueError(f"missing {name} field: {missing[0]}")
    return data


def _sequence(value: Any, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{name} must be an array")
    return value


def _trimmed_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonblank string")
    if value != value.strip():
        raise ValueError(f"{name} must be trimmed")
    return value


def _positive_integer(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_integer(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be lowercase 64 hex")
    return value


def _pass_status(value: Any, name: str) -> str:
    if value != "pass":
        raise ValueError(f"{name} must be pass")
    return "pass"


def _true(value: Any, name: str) -> bool:
    if value is not True:
        raise ValueError(f"{name} must be true")
    return True


def _rfc3339(value: Any, name: str) -> str:
    text = _trimmed_text(value, name)
    if not _RFC3339.fullmatch(text):
        raise ValueError(f"{name} must be RFC3339")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ValueError(f"{name} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must be RFC3339")
    return text


@dataclass(frozen=True)
class EvidenceQuote:
    chapter: int
    source_quote: str
    claim: str | None = None

    def __post_init__(self) -> None:
        _positive_integer(self.chapter, "evidence.chapter")
        _trimmed_text(self.source_quote, "evidence.source_quote")
        if self.claim is not None:
            _trimmed_text(self.claim, "evidence.claim")

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "chapter": self.chapter,
            "source_quote": self.source_quote,
        }
        if self.claim is not None:
            value = {"claim": self.claim, **value}
        return value

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], *, claim_required: bool = False
    ) -> "EvidenceQuote":
        expected = {"chapter", "source_quote"}
        if claim_required:
            expected.add("claim")
        data = _exact_fields(value, expected, "evidence quote")
        return cls(
            chapter=_positive_integer(data["chapter"], "evidence.chapter"),
            source_quote=_trimmed_text(data["source_quote"], "evidence.source_quote"),
            claim=(
                _trimmed_text(data["claim"], "evidence.claim")
                if claim_required
                else None
            ),
        )


@dataclass(frozen=True)
class WholeBookCoreConflict:
    protagonist: str
    goal: str
    opposition: str
    stakes: str
    escalation: str
    unresolved_choice: str
    opening: tuple[EvidenceQuote, ...]
    middle: tuple[EvidenceQuote, ...]
    late: tuple[EvidenceQuote, ...]

    def __post_init__(self) -> None:
        for name in (
            "protagonist",
            "goal",
            "opposition",
            "stakes",
            "escalation",
            "unresolved_choice",
        ):
            _trimmed_text(getattr(self, name), f"whole_book_core_conflict.{name}")
        for bucket in ("opening", "middle", "late"):
            quotes = getattr(self, bucket)
            if not isinstance(quotes, tuple) or not quotes:
                raise ValueError(
                    f"whole_book_core_conflict.evidence.{bucket} must be nonempty"
                )
            if not all(isinstance(item, EvidenceQuote) and item.claim is None for item in quotes):
                raise ValueError(
                    f"whole_book_core_conflict.evidence.{bucket} must contain evidence quotes"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protagonist": self.protagonist,
            "goal": self.goal,
            "opposition": self.opposition,
            "stakes": self.stakes,
            "escalation": self.escalation,
            "unresolved_choice": self.unresolved_choice,
            "evidence": {
                "opening": [item.to_dict() for item in self.opening],
                "middle": [item.to_dict() for item in self.middle],
                "late": [item.to_dict() for item in self.late],
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WholeBookCoreConflict":
        data = _exact_fields(value, _CONFLICT_FIELDS, "whole book core conflict")
        evidence = _exact_fields(
            data["evidence"], _EVIDENCE_BUCKETS, "whole book conflict evidence"
        )

        def quotes(bucket: str) -> tuple[EvidenceQuote, ...]:
            return tuple(
                EvidenceQuote.from_dict(item)
                for item in _sequence(
                    evidence[bucket], f"whole_book_core_conflict.evidence.{bucket}"
                )
            )

        return cls(
            protagonist=_trimmed_text(data["protagonist"], "whole_book_core_conflict.protagonist"),
            goal=_trimmed_text(data["goal"], "whole_book_core_conflict.goal"),
            opposition=_trimmed_text(data["opposition"], "whole_book_core_conflict.opposition"),
            stakes=_trimmed_text(data["stakes"], "whole_book_core_conflict.stakes"),
            escalation=_trimmed_text(data["escalation"], "whole_book_core_conflict.escalation"),
            unresolved_choice=_trimmed_text(
                data["unresolved_choice"], "whole_book_core_conflict.unresolved_choice"
            ),
            opening=quotes("opening"),
            middle=quotes("middle"),
            late=quotes("late"),
        )


@dataclass(frozen=True)
class _PublicationChapterRef:
    number: int
    revision_id: str
    sha256: str

    def __post_init__(self) -> None:
        _positive_integer(self.number, "source.chapters.number")
        _trimmed_text(self.revision_id, "source.chapters.revision_id")
        _sha256(self.sha256, "source.chapters.sha256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "revision_id": self.revision_id,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "_PublicationChapterRef":
        data = _exact_fields(value, _SOURCE_CHAPTER_FIELDS, "publication source chapter")
        return cls(
            number=_positive_integer(data["number"], "source.chapters.number"),
            revision_id=_trimmed_text(data["revision_id"], "source.chapters.revision_id"),
            sha256=_sha256(data["sha256"], "source.chapters.sha256"),
        )


@dataclass(frozen=True)
class PublicationSourceRef:
    run_id: str
    book_check_stage_sha256: str
    source_set_sha256: str
    chapters: tuple[_PublicationChapterRef, ...]

    def __post_init__(self) -> None:
        _trimmed_text(self.run_id, "source.run_id")
        _sha256(self.book_check_stage_sha256, "source.book_check_stage_sha256")
        _sha256(self.source_set_sha256, "source.source_set_sha256")
        if not isinstance(self.chapters, tuple) or not self.chapters:
            raise ValueError("source.chapters must be nonempty")
        if not all(isinstance(item, _PublicationChapterRef) for item in self.chapters):
            raise ValueError("source.chapters must contain publication chapter refs")
        numbers = [item.number for item in self.chapters]
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise ValueError("source.chapters must use unique ascending numbers")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "book_check_stage_sha256": self.book_check_stage_sha256,
            "source_set_sha256": self.source_set_sha256,
            "chapters": [item.to_dict() for item in self.chapters],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PublicationSourceRef":
        data = _exact_fields(value, _SOURCE_FIELDS, "publication source")
        return cls(
            run_id=_trimmed_text(data["run_id"], "source.run_id"),
            book_check_stage_sha256=_sha256(
                data["book_check_stage_sha256"], "source.book_check_stage_sha256"
            ),
            source_set_sha256=_sha256(
                data["source_set_sha256"], "source.source_set_sha256"
            ),
            chapters=tuple(
                _PublicationChapterRef.from_dict(item)
                for item in _sequence(data["chapters"], "source.chapters")
            ),
        )


@dataclass(frozen=True)
class PublicationGeneration:
    conflict_provider: str
    conflict_model: str
    conflict_prompt_version: str
    conflict_response_sha256: str
    writer_provider: str
    writer_model: str
    writer_prompt_version: str
    writer_response_sha256: str
    validator_provider: str
    validator_model: str
    validator_prompt_version: str
    validator_response_sha256: str
    generated_at: str

    def __post_init__(self) -> None:
        for name in (
            "conflict_provider",
            "conflict_model",
            "conflict_prompt_version",
            "writer_provider",
            "writer_model",
            "writer_prompt_version",
            "validator_provider",
            "validator_model",
            "validator_prompt_version",
        ):
            _trimmed_text(getattr(self, name), f"generation.{name}")
        for name in (
            "conflict_response_sha256",
            "writer_response_sha256",
            "validator_response_sha256",
        ):
            _sha256(getattr(self, name), f"generation.{name}")
        _rfc3339(self.generated_at, "generation.generated_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_provider": self.conflict_provider,
            "conflict_model": self.conflict_model,
            "conflict_prompt_version": self.conflict_prompt_version,
            "conflict_response_sha256": self.conflict_response_sha256,
            "writer_provider": self.writer_provider,
            "writer_model": self.writer_model,
            "writer_prompt_version": self.writer_prompt_version,
            "writer_response_sha256": self.writer_response_sha256,
            "validator_provider": self.validator_provider,
            "validator_model": self.validator_model,
            "validator_prompt_version": self.validator_prompt_version,
            "validator_response_sha256": self.validator_response_sha256,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PublicationGeneration":
        data = _exact_fields(value, _GENERATION_FIELDS, "publication generation")
        return cls(**dict(data))


@dataclass(frozen=True)
class PublicationValidation:
    policy_version: str
    status: str
    length_unit: str
    hook_lead_length: int
    spoiler_free_blurb_length: int
    whole_book_core_conflict: bool
    hook_core_conflict: bool
    blurb_core_conflict: bool
    protagonist_stakes: bool
    spoiler_free: bool
    source_supported: bool
    not_chapter_one_copy: bool
    reader_pull_status: str
    first_glance_clarity: bool
    concrete_emotional_stakes: bool
    escalating_pressure: bool
    protagonist_agency: bool
    open_loop: bool
    truthful_genre_promise: bool
    claim_evidence: tuple[EvidenceQuote, ...]

    def __post_init__(self) -> None:
        _trimmed_text(self.policy_version, "validation.policy_version")
        _pass_status(self.status, "validation.status")
        if self.length_unit not in {"words", "content_characters"}:
            raise ValueError("validation.length.unit is invalid")
        _nonnegative_integer(self.hook_lead_length, "validation.length.hook_lead")
        _nonnegative_integer(
            self.spoiler_free_blurb_length, "validation.length.spoiler_free_blurb"
        )
        for name in _VALIDATION_CHECK_FIELDS:
            _true(getattr(self, name), f"validation.checks.{name}")
        _pass_status(self.reader_pull_status, "validation.reader_pull.status")
        for name in _READER_PULL_CHECK_FIELDS:
            _true(getattr(self, name), f"validation.reader_pull.checks.{name}")
        if not isinstance(self.claim_evidence, tuple) or not all(
            isinstance(item, EvidenceQuote) and item.claim is not None
            for item in self.claim_evidence
        ):
            raise ValueError("validation.claim_evidence must contain claim evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "status": self.status,
            "length": {
                "unit": self.length_unit,
                "hook_lead": self.hook_lead_length,
                "spoiler_free_blurb": self.spoiler_free_blurb_length,
            },
            "checks": {
                name: getattr(self, name)
                for name in (
                    "whole_book_core_conflict",
                    "hook_core_conflict",
                    "blurb_core_conflict",
                    "protagonist_stakes",
                    "spoiler_free",
                    "source_supported",
                    "not_chapter_one_copy",
                )
            },
            "reader_pull": {
                "status": self.reader_pull_status,
                "checks": {
                    name: getattr(self, name)
                    for name in (
                        "first_glance_clarity",
                        "concrete_emotional_stakes",
                        "escalating_pressure",
                        "protagonist_agency",
                        "open_loop",
                        "truthful_genre_promise",
                    )
                },
            },
            "claim_evidence": [item.to_dict() for item in self.claim_evidence],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PublicationValidation":
        data = _exact_fields(value, _VALIDATION_FIELDS, "publication validation")
        length = _exact_fields(
            data["length"], _LENGTH_FIELDS, "publication validation length"
        )
        checks = _exact_fields(
            data["checks"], _VALIDATION_CHECK_FIELDS, "publication validation checks"
        )
        reader_pull = _exact_fields(
            data["reader_pull"], _READER_PULL_FIELDS, "publication reader pull"
        )
        reader_checks = _exact_fields(
            reader_pull["checks"],
            _READER_PULL_CHECK_FIELDS,
            "publication reader pull checks",
        )
        return cls(
            policy_version=_trimmed_text(data["policy_version"], "validation.policy_version"),
            status=_pass_status(data["status"], "validation.status"),
            length_unit=_trimmed_text(length["unit"], "validation.length.unit"),
            hook_lead_length=_nonnegative_integer(
                length["hook_lead"], "validation.length.hook_lead"
            ),
            spoiler_free_blurb_length=_nonnegative_integer(
                length["spoiler_free_blurb"], "validation.length.spoiler_free_blurb"
            ),
            **{
                name: _true(checks[name], f"validation.checks.{name}")
                for name in _VALIDATION_CHECK_FIELDS
            },
            reader_pull_status=_pass_status(
                reader_pull["status"], "validation.reader_pull.status"
            ),
            **{
                name: _true(
                    reader_checks[name], f"validation.reader_pull.checks.{name}"
                )
                for name in _READER_PULL_CHECK_FIELDS
            },
            claim_evidence=tuple(
                EvidenceQuote.from_dict(item, claim_required=True)
                for item in _sequence(
                    data["claim_evidence"], "validation.claim_evidence"
                )
            ),
        )


@dataclass(frozen=True)
class PublicationCopy:
    schema_version: int
    title: str
    language: str
    reader_heading: str
    hook_lead: str
    spoiler_free_blurb: str
    whole_book_core_conflict: WholeBookCoreConflict
    source: PublicationSourceRef
    generation: PublicationGeneration
    validation: PublicationValidation

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be the integer {SCHEMA_VERSION}")
        nested_types = (
            ("whole_book_core_conflict", WholeBookCoreConflict),
            ("source", PublicationSourceRef),
            ("generation", PublicationGeneration),
            ("validation", PublicationValidation),
        )
        for name, expected_type in nested_types:
            if not isinstance(getattr(self, name), expected_type):
                raise ValueError(f"{name} must be a {expected_type.__name__}")
        for name in (
            "title",
            "language",
            "reader_heading",
            "hook_lead",
            "spoiler_free_blurb",
        ):
            _trimmed_text(getattr(self, name), name)
        expected_unit, hook_length = count_publication_units(self.hook_lead, self.language)
        _, blurb_length = count_publication_units(self.spoiler_free_blurb, self.language)
        hook_bounds, blurb_bounds = _length_bounds(self.language)
        if not hook_bounds[0] <= hook_length <= hook_bounds[1]:
            raise ValueError(
                f"hook_lead_length: expected {hook_bounds[0]}-{hook_bounds[1]} {expected_unit}"
            )
        if not blurb_bounds[0] <= blurb_length <= blurb_bounds[1]:
            raise ValueError(
                "spoiler_free_blurb_length: expected "
                f"{blurb_bounds[0]}-{blurb_bounds[1]} {expected_unit}"
            )
        if self.validation.length_unit != expected_unit:
            raise ValueError("validation.length.unit does not match language")
        if self.validation.hook_lead_length != hook_length:
            raise ValueError("validation.length.hook_lead does not match hook_lead")
        if self.validation.spoiler_free_blurb_length != blurb_length:
            raise ValueError(
                "validation.length.spoiler_free_blurb does not match spoiler_free_blurb"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "language": self.language,
            "reader_heading": self.reader_heading,
            "hook_lead": self.hook_lead,
            "spoiler_free_blurb": self.spoiler_free_blurb,
            "whole_book_core_conflict": self.whole_book_core_conflict.to_dict(),
            "source": self.source.to_dict(),
            "generation": self.generation.to_dict(),
            "validation": self.validation.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PublicationCopy":
        data = _object(value, "publication copy")
        unknown = sorted(set(data) - _TOP_LEVEL_FIELDS)
        if unknown:
            raise ValueError(f"unknown publication copy field: {unknown[0]}")
        missing = sorted(_TOP_LEVEL_FIELDS - set(data))
        if missing:
            raise ValueError(f"missing publication copy field: {missing[0]}")
        schema_version = data["schema_version"]
        if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be the integer {SCHEMA_VERSION}")
        return cls(
            schema_version=schema_version,
            title=_trimmed_text(data["title"], "title"),
            language=_trimmed_text(data["language"], "language"),
            reader_heading=_trimmed_text(data["reader_heading"], "reader_heading"),
            hook_lead=_trimmed_text(data["hook_lead"], "hook_lead"),
            spoiler_free_blurb=_trimmed_text(
                data["spoiler_free_blurb"], "spoiler_free_blurb"
            ),
            whole_book_core_conflict=WholeBookCoreConflict.from_dict(
                data["whole_book_core_conflict"]
            ),
            source=PublicationSourceRef.from_dict(data["source"]),
            generation=PublicationGeneration.from_dict(data["generation"]),
            validation=PublicationValidation.from_dict(data["validation"]),
        )


def parse_publication_copy(raw: bytes | str) -> PublicationCopy:
    """Parse a publication-copy artifact without accepting duplicate keys."""

    if not isinstance(raw, (bytes, str)):
        raise ValueError("publication copy JSON must be bytes or text")
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid publication copy JSON: {exc}") from exc
    return PublicationCopy.from_dict(_object(value, "publication copy"))


def count_publication_units(text: str, language: str) -> tuple[str, int]:
    """Count publication copy using the locale's LanguageLengthPolicy v1 unit."""

    if not isinstance(text, str):
        raise ValueError("publication text must be a string")
    locale = (language or "").casefold()
    if locale.startswith(("zh", "ja", "ko")):
        return "content_characters", sum(
            1
            for char in text
            if not char.isspace() and unicodedata.category(char)[0] != "P"
        )
    token_pattern = _ENGLISH_WORD if locale.startswith("en") else _UNICODE_TOKEN
    return "words", len(token_pattern.findall(text))


def _length_bounds(language: str) -> tuple[tuple[int, int], tuple[int, int]]:
    if (language or "").casefold().startswith(("zh", "ja", "ko")):
        return (16, 36), (240, 360)
    return (8, 20), (120, 180)


def _word_tokens(text: str) -> list[str]:
    return [token.casefold() for token in _ENGLISH_WORD.findall(text)]


def _windows(tokens: Sequence[str], size: int) -> set[tuple[str, ...]]:
    return {
        tuple(tokens[index : index + size])
        for index in range(max(0, len(tokens) - size + 1))
    }


def _validate_plain_copy(reader_heading: str, hook: str, blurb: str) -> None:
    if "\n" in reader_heading or "\r" in reader_heading:
        raise ValueError("plain_text_reader_heading: reader heading must be one line")
    if _PSEUDO_METADATA.search(reader_heading):
        raise ValueError("plain_text_reader_heading: pseudo metadata is forbidden")
    for name, text in (
        ("reader_heading", reader_heading),
        ("hook_lead", hook),
        ("spoiler_free_blurb", blurb),
    ):
        if _HTML.search(text) or _MARKDOWN.search(text):
            code = "plain_text_reader_heading" if name == "reader_heading" else "forbidden_markup"
            raise ValueError(f"{code}: {name} must be plain text")
        if _URL.search(text):
            raise ValueError(f"forbidden_url: {name} contains a URL")
        if _PSEUDO_METADATA.search(text):
            raise ValueError(f"pseudo_metadata: {name} contains metadata")
        if _PROMOTIONAL_CTA.search(text):
            raise ValueError(f"promotional_cta: {name} contains a call to action")


def _validate_generic_hook(hook: str) -> None:
    folded = " ".join(_word_tokens(hook))
    if not folded.startswith(_GENERIC_OPENINGS):
        return
    if set(folded.split()) <= _GENERIC_WORDS:
        raise ValueError("generic_reader_hook: hook contains no story-specific fact")


def _candidate_semantics(
    candidate: Mapping[str, Any]
) -> tuple[dict[str, bool], dict[str, bool], tuple[EvidenceQuote, ...]]:
    checks_value = candidate.get("checks")
    if checks_value is None:
        checks = {name: True for name in _SEMANTIC_CHECK_FIELDS}
    else:
        raw_checks = _object(checks_value, "publication candidate checks")
        allowed = _SEMANTIC_CHECK_FIELDS | {"not_chapter_one_copy"}
        unknown = sorted(set(raw_checks) - allowed)
        missing = sorted(_SEMANTIC_CHECK_FIELDS - set(raw_checks))
        if unknown:
            raise ValueError(f"unknown publication candidate check: {unknown[0]}")
        if missing:
            raise ValueError(f"missing publication candidate check: {missing[0]}")
        checks = {
            name: _true(raw_checks[name], f"semantic check failed: {name}")
            for name in _SEMANTIC_CHECK_FIELDS
        }

    reader_pull_value = candidate.get("reader_pull")
    if reader_pull_value is None:
        reader_checks = {name: True for name in _READER_PULL_CHECK_FIELDS}
    else:
        reader_pull = _exact_fields(
            reader_pull_value, _READER_PULL_FIELDS, "publication candidate reader pull"
        )
        _pass_status(reader_pull["status"], "reader_pull.status")
        raw_reader_checks = _exact_fields(
            reader_pull["checks"],
            _READER_PULL_CHECK_FIELDS,
            "publication candidate reader pull checks",
        )
        reader_checks = {
            name: _true(raw_reader_checks[name], f"reader pull check failed: {name}")
            for name in _READER_PULL_CHECK_FIELDS
        }

    evidence = tuple(
        EvidenceQuote.from_dict(item, claim_required=True)
        for item in _sequence(candidate.get("claim_evidence", ()), "claim_evidence")
    )
    return checks, reader_checks, evidence


def validate_publication_candidate(
    candidate: Mapping[str, Any],
    *,
    source_chapters: Mapping[int, str],
    chapter_one_prefix: str,
    ending_spoilers: Sequence[str],
) -> PublicationValidation:
    """Run deterministic publication-copy gates and return a pass-only record."""

    data = _object(candidate, "publication candidate")
    allowed = {
        "title",
        "language",
        "reader_heading",
        "hook_lead",
        "spoiler_free_blurb",
        "checks",
        "reader_pull",
        "claim_evidence",
    }
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"unknown publication candidate field: {unknown[0]}")
    for required in ("reader_heading", "hook_lead", "spoiler_free_blurb"):
        if required not in data:
            raise ValueError(f"missing publication candidate field: {required}")

    language_value = data.get("language", "en-US")
    language = _trimmed_text(language_value, "language")
    reader_heading = _trimmed_text(data["reader_heading"], "reader_heading")
    hook = _trimmed_text(data["hook_lead"], "hook_lead")
    blurb = _trimmed_text(data["spoiler_free_blurb"], "spoiler_free_blurb")
    if "title" in data:
        _trimmed_text(data["title"], "title")

    _validate_plain_copy(reader_heading, hook, blurb)
    if len(_SENTENCE_END.findall(hook)) > 1 or _SENTENCE_BREAK.search(hook):
        raise ValueError("single_sentence_hook: hook_lead must be one sentence")
    _validate_generic_hook(hook)

    unit, hook_length = count_publication_units(hook, language)
    _, blurb_length = count_publication_units(blurb, language)
    hook_bounds, blurb_bounds = _length_bounds(language)
    if not hook_bounds[0] <= hook_length <= hook_bounds[1]:
        raise ValueError(
            f"hook_lead_length: expected {hook_bounds[0]}-{hook_bounds[1]} {unit}"
        )
    if not blurb_bounds[0] <= blurb_length <= blurb_bounds[1]:
        raise ValueError(
            "spoiler_free_blurb_length: expected "
            f"{blurb_bounds[0]}-{blurb_bounds[1]} {unit}"
        )

    visible_copy = f"{hook}\n{blurb}".casefold()
    for phrase in ending_spoilers:
        spoiler = _trimmed_text(phrase, "ending_spoilers item")
        if spoiler.casefold() in visible_copy:
            raise ValueError(f"ending_spoiler: forbidden phrase {spoiler!r}")

    hook_tokens = _word_tokens(hook)
    blurb_tokens = _word_tokens(blurb)
    if _windows(hook_tokens, 10) & _windows(blurb_tokens, 10):
        raise ValueError("hook_blurb_repetition: repeated ten-word window")

    chapter_tokens = _word_tokens(chapter_one_prefix)[:500]
    chapter_windows = _windows(chapter_tokens, 8)
    if chapter_windows & (_windows(hook_tokens, 8) | _windows(blurb_tokens, 8)):
        raise ValueError("not_chapter_one_copy: repeated eight-word window")

    checks, reader_checks, evidence = _candidate_semantics(data)
    for item in evidence:
        chapter_text = source_chapters.get(item.chapter)
        if not isinstance(chapter_text, str):
            raise ValueError(
                f"source_quote chapter {item.chapter} is absent from source chapters"
            )
        if item.source_quote not in chapter_text:
            raise ValueError(
                f"source_quote is not an exact substring of chapter {item.chapter}"
            )

    return PublicationValidation(
        policy_version=POLICY_VERSION,
        status="pass",
        length_unit=unit,
        hook_lead_length=hook_length,
        spoiler_free_blurb_length=blurb_length,
        **checks,
        not_chapter_one_copy=True,
        reader_pull_status="pass",
        **reader_checks,
        claim_evidence=evidence,
    )


__all__ = [
    "EvidenceQuote",
    "WholeBookCoreConflict",
    "PublicationSourceRef",
    "PublicationGeneration",
    "PublicationValidation",
    "PublicationCopy",
    "parse_publication_copy",
    "count_publication_units",
    "canonical_json_bytes",
    "validate_publication_candidate",
]
