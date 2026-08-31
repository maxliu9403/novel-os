"""Trusted Final-chapter sources for reader-facing publication copy.

This module deliberately ignores mutable manuscript projections. A publication
source is valid only when an immutable Final head is bound to the promotion
record for the requested run.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from artifacts import ArtifactError, ArtifactStore
from pipeline_models import ManifestStore, RunManifest, StageResult
from promotion import PromotionService
from publication_copy import canonical_json_bytes


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_ID_RE = re.compile(
    r"^(?:promotion|legacy)-receipt-[0-9a-f]{64}$"
)
_CHAPTER_COMMENT_RE = re.compile(
    r"<!--[\s\S]*?^\s*CHAPTER:\s*(?P<number>\d+)\s*[-\u2013\u2014:]\s*"
    r"(?P<title>.+?)\s*$[\s\S]*?-->",
    re.IGNORECASE | re.MULTILINE,
)
_MARKDOWN_TITLE_RE = re.compile(r"^#(?!#)\s+(?P<title>\S.*)\s*$", re.MULTILINE)
_PARAGRAPH_BOUNDARY_RE = re.compile(r"\r?\n[ \t]*\r?\n")
_EVIDENCE_BUCKETS = ("opening", "middle", "late")


class PublicationSourceError(RuntimeError):
    """A trusted publication source could not be established."""


@dataclass(frozen=True)
class SourceChapter:
    number: int
    title: str
    revision_id: str
    sha256: str
    text: str
    promotion_receipt_id: str
    finalized_at: str
    segment_sha256: str | None = None
    segment_ordinal: int | None = None

    def __post_init__(self) -> None:
        if type(self.number) is not int or self.number < 1:
            raise ValueError("source chapter number must be a positive integer")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("source chapter title must be a nonblank string")
        if self.title != self.title.strip():
            raise ValueError("source chapter title must be trimmed")
        if not isinstance(self.revision_id, str) or not _SHA256_RE.fullmatch(
            self.revision_id
        ):
            raise ValueError("source chapter revision_id must be lowercase SHA-256")
        if not isinstance(self.sha256, str) or not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("source chapter sha256 must be lowercase SHA-256")
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("source chapter text must be a nonempty string")
        is_segment = self.segment_sha256 is not None or self.segment_ordinal is not None
        if is_segment:
            if (
                not isinstance(self.segment_sha256, str)
                or not _SHA256_RE.fullmatch(self.segment_sha256)
            ):
                raise ValueError("source chapter segment_sha256 must be SHA-256")
            if type(self.segment_ordinal) is not int or self.segment_ordinal < 1:
                raise ValueError(
                    "source chapter segment_ordinal must be a positive integer"
                )
            if self.segment_sha256 != _text_sha256(self.text):
                raise ValueError(
                    "source chapter segment_sha256 does not match its UTF-8 text"
                )
        else:
            if not self.text.strip():
                raise ValueError("whole source chapter text must be nonblank")
            if self.sha256 != _text_sha256(self.text):
                raise ValueError(
                    "source chapter sha256 does not match its UTF-8 text"
                )
        if (
            not isinstance(self.promotion_receipt_id, str)
            or not _RECEIPT_ID_RE.fullmatch(self.promotion_receipt_id)
        ):
            raise ValueError("source chapter promotion_receipt_id is invalid")
        if not isinstance(self.finalized_at, str) or not self.finalized_at.strip():
            raise ValueError("source chapter finalized_at must be nonblank")
        if self.finalized_at != self.finalized_at.strip():
            raise ValueError("source chapter finalized_at must be trimmed")


@dataclass(frozen=True)
class PublicationSourceSet:
    run_id: str
    chapters: tuple[SourceChapter, ...]
    source_set_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("publication source run_id must be nonblank")
        if self.run_id != self.run_id.strip():
            raise ValueError("publication source run_id must be trimmed")
        if not isinstance(self.chapters, tuple) or not self.chapters:
            raise ValueError("publication source chapters must be a nonempty tuple")
        if not all(isinstance(chapter, SourceChapter) for chapter in self.chapters):
            raise ValueError(
                "publication source chapters must contain SourceChapter values"
            )
        for chapter in self.chapters:
            SourceChapter.__post_init__(chapter)
            if chapter.segment_sha256 is not None:
                raise ValueError(
                    "publication source sets must contain whole Final chapters"
                )
        numbers = [chapter.number for chapter in self.chapters]
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise ValueError(
                "publication source chapters must have unique ascending numbers"
            )
        if (
            not isinstance(self.source_set_sha256, str)
            or not _SHA256_RE.fullmatch(self.source_set_sha256)
        ):
            raise ValueError("publication source source_set_sha256 must be SHA-256")
        expected_sha256 = _source_identity_hash(self.chapters)
        if self.source_set_sha256 != expected_sha256:
            raise ValueError(
                "publication source source_set_sha256 does not match its chapters"
            )


def build_publication_source_set(
    project: Path,
    run_id: str,
    chapter_count: int,
    quality_policy: str,
) -> PublicationSourceSet:
    """Build the ordered source set from immutable Final authority.

    Evidence runs use their durable PromotionService receipt. Legacy runs have
    no durable promotion ledger, so their completed StageResult plus the
    deterministic legacy receipt identity is the binding authority.
    """

    project = Path(project)
    if not isinstance(run_id, str) or not run_id.strip() or run_id != run_id.strip():
        raise PublicationSourceError("run_id must be a trimmed nonblank string")
    if type(chapter_count) is not int or chapter_count < 1:
        raise PublicationSourceError("chapter_count must be a positive integer")
    if quality_policy not in {"evidence_v1", "legacy"}:
        raise PublicationSourceError(
            f"unsupported publication source quality policy: {quality_policy!r}"
        )

    manifest = _load_run_manifest(project, run_id)
    _validate_manifest_scope(manifest, run_id, chapter_count, quality_policy)
    artifact_store = ArtifactStore(project)
    promotion_service = (
        PromotionService(project) if quality_policy == "evidence_v1" else None
    )

    chapters: list[SourceChapter] = []
    for number in range(1, chapter_count + 1):
        stage = _promotion_stage(manifest, number)
        try:
            head = artifact_store.get_head(number, "final")
            if head is None:
                raise PublicationSourceError(
                    f"chapter {number} Final head is missing"
                )
            revision = artifact_store.get_revision(head.revision_id)
            text = artifact_store.read_text(revision.revision_id)
        except PublicationSourceError:
            raise
        except (ArtifactError, KeyError, OSError, UnicodeError, ValueError) as exc:
            raise PublicationSourceError(
                f"chapter {number} Final artifact failed integrity validation: {exc}"
            ) from exc

        actual_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if (
            revision.chapter != number
            or revision.kind != "final"
            or revision.revision_id != head.revision_id
            or revision.sha256 != actual_sha256
        ):
            raise PublicationSourceError(
                f"chapter {number} Final revision hash is divergent"
            )
        if not text.strip():
            raise PublicationSourceError(f"chapter {number} Final artifact is empty")

        _validate_stage_artifact_binding(
            stage,
            number,
            revision.revision_id,
            revision.sha256,
        )
        if quality_policy == "evidence_v1":
            receipt_id, finalized_at = _evidence_binding(
                promotion_service,
                run_id,
                number,
                stage,
                revision.revision_id,
                revision.sha256,
            )
        else:
            receipt_id, finalized_at = _legacy_binding(
                run_id,
                number,
                stage,
            )

        try:
            source_chapter = SourceChapter(
                number=number,
                title=_chapter_title(text, number),
                revision_id=revision.revision_id,
                sha256=revision.sha256,
                text=text,
                promotion_receipt_id=receipt_id,
                finalized_at=finalized_at,
            )
        except (TypeError, ValueError) as exc:
            raise PublicationSourceError(
                f"chapter {number} publication source binding is invalid: {exc}"
            ) from exc
        chapters.append(source_chapter)

    source_chapters = tuple(chapters)
    try:
        return PublicationSourceSet(
            run_id=run_id,
            chapters=source_chapters,
            source_set_sha256=_source_identity_hash(source_chapters),
        )
    except (TypeError, ValueError) as exc:
        raise PublicationSourceError(
            f"publication source set binding is invalid: {exc}"
        ) from exc


def publication_source_input_hash(source_set: PublicationSourceSet) -> str:
    """Hash the canonical ordered revision identity of a source set."""

    _require_source_set_integrity(source_set)
    return _source_identity_hash(source_set.chapters)


def group_source_chapters(
    source_set: PublicationSourceSet,
    max_codepoints: int = 120_000,
) -> tuple[tuple[SourceChapter, ...], ...]:
    """Partition every source code point into bounded, consecutive groups.

    A chapter larger than the budget is segmented only immediately after a
    blank-line paragraph boundary. The separator stays in the preceding
    segment so concatenating the segments reproduces the exact Final text.
    """

    if type(max_codepoints) is not int or max_codepoints < 1:
        raise PublicationSourceError(
            "max_codepoints must be a positive integer"
        )
    _require_source_set_integrity(source_set)

    segments: list[SourceChapter] = []
    for chapter in source_set.chapters:
        segments.extend(_split_chapter(chapter, max_codepoints))

    groups: list[tuple[SourceChapter, ...]] = []
    current: list[SourceChapter] = []
    current_size = 0
    for segment in segments:
        segment_size = len(segment.text)
        if current and current_size + segment_size > max_codepoints:
            groups.append(tuple(current))
            current = []
            current_size = 0
        current.append(segment)
        current_size += segment_size
    if current:
        groups.append(tuple(current))
    return tuple(groups)


def build_evidence_ledger(group: Sequence[SourceChapter]) -> str:
    """Serialize a source group as deterministic JSON Lines with exact text."""

    if (
        isinstance(group, (str, bytes))
        or not isinstance(group, Sequence)
        or not group
    ):
        raise PublicationSourceError(
            "evidence ledger group must be a nonempty sequence"
        )
    records: list[str] = []
    for item in group:
        if not isinstance(item, SourceChapter):
            raise PublicationSourceError(
                "evidence ledger entries must be SourceChapter values"
            )
        _require_source_chapter_integrity(item)
        segment_sha256 = item.segment_sha256 or item.sha256
        segment_ordinal = item.segment_ordinal or 1
        record = {
            "chapter": item.number,
            "title": item.title,
            "revision_id": item.revision_id,
            "source_sha256": item.sha256,
            "promotion_receipt_id": item.promotion_receipt_id,
            "finalized_at": item.finalized_at,
            "segment": segment_ordinal,
            "segment_sha256": segment_sha256,
            "source_quote": item.text,
        }
        records.append(canonical_json_bytes(record).decode("utf-8"))
    return "\n".join(records)


def validate_conflict_evidence(
    source_set: PublicationSourceSet,
    evidence: Mapping[str, Sequence[Any]],
) -> None:
    """Require exact Final quotes in opening, middle, and late buckets."""

    _require_source_set_integrity(source_set)
    if not isinstance(evidence, Mapping) or set(evidence) != set(_EVIDENCE_BUCKETS):
        raise PublicationSourceError(
            "conflict evidence must contain exactly opening, middle, and late"
        )

    by_number = {chapter.number: chapter for chapter in source_set.chapters}
    chapter_count = max(by_number)
    if set(by_number) != set(range(1, chapter_count + 1)):
        raise PublicationSourceError(
            "conflict evidence requires a contiguous source chapter sequence"
        )

    for bucket in _EVIDENCE_BUCKETS:
        entries = evidence[bucket]
        if (
            isinstance(entries, (str, bytes))
            or not isinstance(entries, Sequence)
            or not entries
        ):
            raise PublicationSourceError(
                f"conflict evidence {bucket} bucket has no exact source quote"
            )
        for entry in entries:
            chapter_number = _evidence_field(entry, "chapter")
            quote = _evidence_field(entry, "source_quote")
            if type(chapter_number) is not int or chapter_number not in by_number:
                raise PublicationSourceError(
                    f"{bucket} evidence names an unknown source chapter"
                )
            if not isinstance(quote, str) or not quote or quote != quote.strip():
                raise PublicationSourceError(
                    f"{bucket} evidence must contain a trimmed exact source quote"
                )
            if not _chapter_matches_bucket(
                chapter_number,
                bucket=bucket,
                chapter_count=chapter_count,
            ):
                raise PublicationSourceError(
                    f"{bucket} evidence chapter {chapter_number} is outside the "
                    f"{bucket} bucket"
                )
            if quote not in by_number[chapter_number].text:
                raise PublicationSourceError(
                    f"{bucket} evidence is not an exact source quote from Final "
                    f"chapter {chapter_number}"
                )


def _load_run_manifest(project: Path, run_id: str) -> RunManifest:
    path = project / "outputs" / "runs" / run_id / "run.json"
    try:
        return ManifestStore(path).load()
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PublicationSourceError(
            f"run {run_id} manifest is missing or invalid: {exc}"
        ) from exc


def _validate_manifest_scope(
    manifest: RunManifest,
    run_id: str,
    chapter_count: int,
    quality_policy: str,
) -> None:
    if manifest.run_id != run_id:
        raise PublicationSourceError("run manifest id does not match requested run")
    if manifest.spec.quality_policy != quality_policy:
        raise PublicationSourceError(
            "run manifest quality policy does not match publication source policy"
        )
    if manifest.spec.num_chapters != chapter_count:
        raise PublicationSourceError(
            "run manifest chapter count does not match publication source request"
        )


def _promotion_stage(manifest: RunManifest, number: int) -> StageResult:
    stage = manifest.get("chapter.promote", number)
    if stage is None:
        raise PublicationSourceError(
            f"chapter {number} has no run-bound promotion stage"
        )
    if (
        stage.status != "done"
        or stage.phase != "chapter.promote"
        or stage.chapter != number
    ):
        raise PublicationSourceError(
            f"chapter {number} promotion stage is not completed"
        )
    return stage


def _validate_stage_artifact_binding(
    stage: StageResult,
    number: int,
    revision_id: str,
    sha256: str,
) -> None:
    relative = f"outputs/manuscript/chapter_{number:03d}_final.md"
    if stage.revision_id != revision_id:
        raise PublicationSourceError(
            f"chapter {number} promotion stage revision is divergent from Final head"
        )
    if stage.artifact_hashes.get(relative) != sha256:
        raise PublicationSourceError(
            f"chapter {number} promotion stage SHA is divergent from Final head"
        )


def _evidence_binding(
    service: PromotionService,
    run_id: str,
    number: int,
    stage: StageResult,
    revision_id: str,
    sha256: str,
) -> tuple[str, str]:
    key = f"pipeline-{run_id}-chapter-{number}"
    try:
        receipt = service.load_receipt(key, check_current_tail=False)
    except Exception as exc:  # noqa: BLE001 - normalize durable receipt failures
        raise PublicationSourceError(
            f"chapter {number} promotion receipt is invalid: {exc}"
        ) from exc
    if receipt is None:
        raise PublicationSourceError(f"chapter {number} promotion receipt is missing")
    if (
        receipt.idempotency_key != key
        or receipt.chapter != number
        or receipt.receipt_id != stage.promotion_receipt_id
        or receipt.new_artifact_revision_id != revision_id
        or receipt.new_artifact_sha256 != sha256
        or getattr(receipt, "status", "committed") != "committed"
    ):
        raise PublicationSourceError(
            f"chapter {number} promotion receipt is divergent from Final head or run"
        )
    finalized_at = getattr(receipt, "committed_at", "")
    if not isinstance(finalized_at, str) or not finalized_at.strip():
        raise PublicationSourceError(
            f"chapter {number} promotion receipt has no finalization timestamp"
        )
    return receipt.receipt_id, finalized_at


def _legacy_binding(
    run_id: str,
    number: int,
    stage: StageResult,
) -> tuple[str, str]:
    expected_receipt_id = _legacy_receipt_id(run_id, stage)
    if stage.promotion_receipt_id != expected_receipt_id:
        raise PublicationSourceError(
            f"chapter {number} legacy promotion receipt is divergent"
        )
    if not stage.finished_at.strip():
        raise PublicationSourceError(
            f"chapter {number} legacy promotion has no finalization timestamp"
        )
    return expected_receipt_id, stage.finished_at


def _legacy_receipt_id(run_id: str, stage: StageResult) -> str:
    identity = {
        "run_id": run_id,
        "chapter": stage.chapter,
        "revision_id": stage.revision_id,
        "artifact_hashes": stage.artifact_hashes,
    }
    return "legacy-receipt-" + hashlib.sha256(
        canonical_json_bytes(identity)
    ).hexdigest()


def _chapter_title(text: str, number: int) -> str:
    comment = _CHAPTER_COMMENT_RE.search(text)
    if comment is not None and int(comment.group("number")) == number:
        return comment.group("title").strip()
    heading = _MARKDOWN_TITLE_RE.search(text)
    if heading is not None:
        return heading.group("title").strip()
    return f"Chapter {number}"


def _split_chapter(
    chapter: SourceChapter,
    max_codepoints: int,
) -> list[SourceChapter]:
    if len(chapter.text) <= max_codepoints:
        return [chapter]

    segment_texts: list[str] = []
    remaining = chapter.text
    while len(remaining) > max_codepoints:
        boundary_end = 0
        for match in _PARAGRAPH_BOUNDARY_RE.finditer(
            remaining, 0, max_codepoints
        ):
            if remaining[: match.end()].strip():
                boundary_end = match.end()
        if boundary_end == 0:
            if remaining[:max_codepoints].isspace():
                boundary_end = max_codepoints
            else:
                raise PublicationSourceError(
                    f"chapter {chapter.number} contains a paragraph larger than "
                    f"max_codepoints; no paragraph boundary can preserve it"
                )
        segment_texts.append(remaining[:boundary_end])
        remaining = remaining[boundary_end:]
    if remaining:
        segment_texts.append(remaining)

    segments: list[SourceChapter] = []
    for ordinal, segment_text in enumerate(segment_texts, start=1):
        try:
            segments.append(
                replace(
                    chapter,
                    text=segment_text,
                    segment_sha256=_text_sha256(segment_text),
                    segment_ordinal=ordinal,
                )
            )
        except (TypeError, ValueError) as exc:
            raise PublicationSourceError(
                f"chapter {chapter.number} segment {ordinal} is invalid: {exc}"
            ) from exc
    return segments


def _evidence_field(entry: Any, name: str) -> Any:
    if isinstance(entry, Mapping):
        if set(entry) != {"chapter", "source_quote"}:
            raise PublicationSourceError(
                "conflict evidence entries require chapter and source_quote"
            )
        return entry[name]
    if not hasattr(entry, "chapter") or not hasattr(entry, "source_quote"):
        raise PublicationSourceError(
            "conflict evidence entries require chapter and source_quote"
        )
    return getattr(entry, name)


def _chapter_matches_bucket(
    chapter_number: int,
    *,
    bucket: str,
    chapter_count: int,
) -> bool:
    if chapter_count == 1:
        return chapter_number == 1
    if chapter_count == 2:
        allowed = {
            "opening": {1},
            "middle": {1, 2},
            "late": {2},
        }
        return chapter_number in allowed[bucket]

    # Place each chapter by its midpoint in the book. This gives the exact
    # 1 / 2-3 / 4 partition for four chapters and applies the same
    # proportional boundaries to other lengths.
    proportion = (chapter_number - 0.5) / chapter_count
    if proportion < 0.25:
        expected_bucket = "opening"
    elif proportion >= 0.75:
        expected_bucket = "late"
    else:
        expected_bucket = "middle"
    return bucket == expected_bucket


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_identity_hash(chapters: Sequence[SourceChapter]) -> str:
    identity = [
        {
            "chapter": item.number,
            "revision_id": item.revision_id,
            "sha256": item.sha256,
        }
        for item in sorted(chapters, key=lambda item: item.number)
    ]
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


def _require_source_chapter_integrity(chapter: SourceChapter) -> None:
    if not isinstance(chapter, SourceChapter):
        raise PublicationSourceError("publication source chapter type is invalid")
    try:
        SourceChapter.__post_init__(chapter)
    except ValueError as exc:
        raise PublicationSourceError(
            f"invalid publication source chapter: {exc}"
        ) from exc


def _require_source_set_integrity(source_set: PublicationSourceSet) -> None:
    if not isinstance(source_set, PublicationSourceSet):
        raise PublicationSourceError(
            "source_set must be a PublicationSourceSet"
        )
    try:
        PublicationSourceSet.__post_init__(source_set)
    except (AttributeError, TypeError, ValueError) as exc:
        raise PublicationSourceError(
            f"invalid publication source set: {exc}"
        ) from exc
