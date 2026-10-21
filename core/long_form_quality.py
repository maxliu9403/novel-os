"""Deterministic plan and progress gates for long-form novels."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Package imports in tests; top-level imports in the pipeline.
    from .artifacts import ArtifactError, ArtifactStore
    from .contracts import ChapterContract
    from .narrative_format import (
        NarrativeFormat,
        obligations_for_chapter,
        validate_volume_contracts,
    )
    from .state_manager import StoryState
except ImportError:  # pragma: no cover
    from artifacts import ArtifactError, ArtifactStore
    from contracts import ChapterContract
    from narrative_format import (
        NarrativeFormat,
        obligations_for_chapter,
        validate_volume_contracts,
    )
    from state_manager import StoryState


SCHEMA_VERSION = "long-form-quality.v1"


@dataclass(frozen=True)
class LongFormFinding:
    severity: str
    code: str
    message: str
    volume_id: str = ""
    chapter: int | None = None

    def __post_init__(self) -> None:
        if self.severity not in {"blocker", "warning"}:
            raise ValueError("long-form finding severity is invalid")
        if not self.code or not self.message:
            raise ValueError("long-form finding must have code and message")

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "volume_id": self.volume_id,
            "chapter": self.chapter,
        }


@dataclass(frozen=True)
class LongFormReport:
    audit_type: str
    status: str
    format_id: str
    as_of_chapter: int | None = None
    volume_id: str = ""
    checks: Mapping[str, Any] = field(default_factory=dict)
    findings: tuple[LongFormFinding, ...] = ()

    def __post_init__(self) -> None:
        if self.audit_type not in {"plan", "interval", "volume_end", "whole_book", "inapplicable"}:
            raise ValueError("long-form audit_type is invalid")
        if self.status not in {"passed", "blocked", "inapplicable"}:
            raise ValueError("long-form report status is invalid")

    def _identity(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "audit_type": self.audit_type,
            "status": self.status,
            "format_id": self.format_id,
            "as_of_chapter": self.as_of_chapter,
            "volume_id": self.volume_id,
            "checks": dict(self.checks),
            "findings": [item.to_dict() for item in self.findings],
        }

    @property
    def report_id(self) -> str:
        encoded = json.dumps(
            self._identity(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return f"long-form-report:{hashlib.sha256(encoded).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {"report_id": self.report_id, **self._identity()}

    @property
    def blockers(self) -> tuple[LongFormFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "blocker")


def _status(findings: Sequence[LongFormFinding]) -> str:
    return "blocked" if any(item.severity == "blocker" for item in findings) else "passed"


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", str(value or "").casefold())


def evaluate_foundation_plan(foundation: Mapping[str, Any]) -> LongFormReport:
    format_contract = NarrativeFormat.from_dict(foundation["narrative_format"])
    if not format_contract.is_long_form:
        return LongFormReport(
            audit_type="inapplicable",
            status="inapplicable",
            format_id=format_contract.format_id,
            checks={"reason": "short_novel"},
        )
    findings: list[LongFormFinding] = []
    try:
        volumes = validate_volume_contracts(
            format_contract, foundation.get("volume_contracts")
        )
    except (KeyError, TypeError, ValueError) as exc:
        return LongFormReport(
            audit_type="plan",
            status="blocked",
            format_id=format_contract.format_id,
            checks={"volume_count": len(format_contract.volumes)},
            findings=(LongFormFinding(
                "blocker", "invalid_volume_contracts", str(exc)
            ),),
        )

    chapters = foundation.get("chapters")
    if not isinstance(chapters, list):
        findings.append(LongFormFinding(
            "blocker", "missing_chapter_map", "Foundation chapters are missing."
        ))
        chapters = []
    for raw in chapters:
        if not isinstance(raw, Mapping):
            findings.append(LongFormFinding(
                "blocker", "invalid_chapter_map", "A foundation chapter is not an object."
            ))
            continue
        number = int(raw.get("number") or 0)
        span = format_contract.volume_for_chapter(number)
        expected = {
            "volume_id": span.volume_id,
            "volume_number": span.volume_number,
            "chapter_in_volume": number - span.chapter_start + 1,
        }
        if any(raw.get(name) != value for name, value in expected.items()):
            findings.append(LongFormFinding(
                "blocker",
                "chapter_volume_binding",
                f"Chapter {number} is not bound to {span.volume_id}.",
                volume_id=span.volume_id,
                chapter=number,
            ))

    suitability = format_contract.long_form_suitability
    if suitability.requested_fit in {"supported_with_expansion", "high_extension_risk"}:
        findings.append(LongFormFinding(
            "warning",
            "extension_capacity",
            "The customer-confirmed long format needs the distinct volume engines recorded in this plan.",
        ))
    return LongFormReport(
        audit_type="plan",
        status=_status(findings),
        format_id=format_contract.format_id,
        checks={
            "chapter_count": len(chapters),
            "volume_count": len(volumes),
            "distinct_volume_engines": True,
            "complete_chapter_binding": not any(
                item.code == "chapter_volume_binding" for item in findings
            ),
        },
        findings=tuple(findings),
    )


def audit_due_chapters(format_contract: NarrativeFormat) -> tuple[int, ...]:
    if not format_contract.is_long_form:
        return ()
    due = set(range(5, format_contract.total_chapters + 1, 5))
    due.update(volume.chapter_end for volume in format_contract.volumes)
    due.add(format_contract.total_chapters)
    return tuple(sorted(due))


def _contract(artifacts: ArtifactStore, chapter: int) -> ChapterContract | None:
    head = artifacts.get_head(chapter, "chapter_contract")
    if head is None:
        return None
    return ChapterContract.from_dict(json.loads(artifacts.read_text(head.revision_id)))


def _repetition_findings(
    contracts: Sequence[ChapterContract], volume_id: str
) -> list[LongFormFinding]:
    findings: list[LongFormFinding] = []
    for field, code in (
        ("irreversible_change", "repeated_irreversible_change"),
        ("active_choice", "repeated_active_choice"),
        ("local_payoff", "repeated_local_payoff"),
    ):
        seen: dict[str, list[int]] = {}
        for contract in contracts:
            seen.setdefault(_normalized(getattr(contract, field)), []).append(contract.chapter)
        for chapters in seen.values():
            if len(chapters) >= 2:
                findings.append(LongFormFinding(
                    "blocker",
                    code,
                    f"Chapters {', '.join(map(str, chapters))} repeat the same {field.replace('_', ' ')}.",
                    volume_id=volume_id,
                    chapter=chapters[-1],
                ))
    return findings


def evaluate_progress(project: Path | str, as_of_chapter: int) -> LongFormReport:
    project = Path(project)
    state = StoryState(str(project))
    format_contract = NarrativeFormat.from_dict(state.metadata["narrative_format"])
    if not format_contract.is_long_form:
        return LongFormReport(
            audit_type="inapplicable",
            status="inapplicable",
            format_id=format_contract.format_id,
            as_of_chapter=as_of_chapter,
            checks={"reason": "short_novel"},
        )
    volume_contracts = validate_volume_contracts(
        format_contract, state.story_bible.get("volume_contracts")
    )
    span = format_contract.volume_for_chapter(as_of_chapter)
    volume = next(item for item in volume_contracts if item["volume_id"] == span.volume_id)
    findings: list[LongFormFinding] = []
    artifacts = ArtifactStore(project)
    contracts: list[ChapterContract] = []
    for chapter in range(span.chapter_start, as_of_chapter + 1):
        try:
            value = _contract(artifacts, chapter)
        except (ArtifactError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            findings.append(LongFormFinding(
                "blocker", "invalid_chapter_contract",
                f"Chapter {chapter} contract is invalid: {exc}",
                volume_id=span.volume_id, chapter=chapter,
            ))
            continue
        if value is None:
            findings.append(LongFormFinding(
                "blocker", "missing_chapter_contract",
                f"Chapter {chapter} has no quality contract.",
                volume_id=span.volume_id, chapter=chapter,
            ))
        else:
            contracts.append(value)

    # Compare only the latest five chapters. Deliberate motifs can recur over
    # a long volume; local duplication is the stronger filler signal.
    findings.extend(_repetition_findings(contracts[-5:], span.volume_id))

    contract_by_chapter = {item.chapter: item for item in contracts}
    due_obligations = [
        item
        for chapter in range(span.chapter_start, as_of_chapter + 1)
        for item in obligations_for_chapter(volume_contracts, chapter)
    ]
    for obligation in due_obligations:
        due = int(obligation["due_chapter"])
        contract = contract_by_chapter.get(due)
        if contract is None or obligation["event_id"] not in contract.world_event_ids:
            findings.append(LongFormFinding(
                "blocker",
                "missing_volume_milestone",
                f"Chapter {due} must record `{obligation['event_id']}` in world_event_ids.",
                volume_id=span.volume_id,
                chapter=due,
            ))

    volume_end = as_of_chapter == span.chapter_end
    whole_book = as_of_chapter == format_contract.total_chapters
    if volume_end:
        for chapter in range(span.chapter_start, span.chapter_end + 1):
            final_path = project / "outputs/manuscript" / f"chapter_{chapter:03d}_final.md"
            if not final_path.is_file() or not final_path.read_text(encoding="utf-8").strip():
                findings.append(LongFormFinding(
                    "blocker", "missing_volume_final",
                    f"Volume close is missing approved Final chapter {chapter}.",
                    volume_id=span.volume_id, chapter=chapter,
                ))

    audit_type = "whole_book" if whole_book else "volume_end" if volume_end else "interval"
    return LongFormReport(
        audit_type=audit_type,
        status=_status(findings),
        format_id=format_contract.format_id,
        as_of_chapter=as_of_chapter,
        volume_id=span.volume_id,
        checks={
            "contracts_checked": len(contracts),
            "milestones_due": len(due_obligations),
            "volume_end": volume_end,
            "whole_book": whole_book,
            "planned_payoff": volume["payoff"],
        },
        findings=tuple(findings),
    )


def report_path(project: Path | str, *, as_of_chapter: int | None = None) -> Path:
    root = Path(project) / "outputs/quality/long-form"
    return root / (
        "plan-report.json"
        if as_of_chapter is None
        else f"chapter_{as_of_chapter:03d}_audit.json"
    )


def write_report(
    project: Path | str,
    report: LongFormReport,
    *,
    as_of_chapter: int | None = None,
) -> Path:
    path = report_path(project, as_of_chapter=as_of_chapter)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


__all__ = [
    "LongFormFinding",
    "LongFormReport",
    "audit_due_chapters",
    "evaluate_foundation_plan",
    "evaluate_progress",
    "report_path",
    "write_report",
]
