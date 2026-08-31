"""Deterministic chapter design and commercial quality contracts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from commercial_story import (
    BELONGING_ANCHORS,
    CommercialStoryContract,
    READER_JOBS,
    RESOURCE_DIMENSIONS,
    SATISFACTION_TYPES,
)
from contracts import ChapterContract
from artifacts import ArtifactStore


@dataclass(frozen=True)
class CommercialFinding:
    code: str
    severity: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclass(frozen=True)
class ChapterDesignReport:
    status: str
    chapter: int
    story_contract_id: str
    chapter_contract_id: str
    prior_contract_ids: tuple[str, ...]
    findings: tuple[CommercialFinding, ...]
    schema_version: int = 1

    @property
    def blockers(self) -> tuple[CommercialFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "blocked")

    @property
    def warnings(self) -> tuple[CommercialFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "warning")

    @property
    def report_id(self) -> str:
        encoded = json.dumps(
            self._payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "commercial-chapter-design:" + hashlib.sha256(encoded).hexdigest()

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "chapter": self.chapter,
            "story_contract_id": self.story_contract_id,
            "chapter_contract_id": self.chapter_contract_id,
            "prior_contract_ids": list(self.prior_contract_ids),
            "findings": [item.to_dict() for item in self.findings],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "report_id": self.report_id}


def _finding(code: str, message: str, severity: str = "blocked") -> CommercialFinding:
    return CommercialFinding(code=code, severity=severity, message=message)


def _trailing_count(prior: Sequence[ChapterContract], value: str, field: str) -> int:
    count = 0
    for contract in reversed(prior):
        if getattr(contract, field) != value:
            break
        count += 1
    return count


def _free_trial_findings(
    contract: ChapterContract, story: CommercialStoryContract
) -> list[CommercialFinding]:
    chapter = contract.chapter
    arc = story.free_trial_arc
    if chapter > arc.chapter_count:
        return []
    findings: list[CommercialFinding] = []
    jobs = set(contract.reader_jobs)
    if chapter == 1 and "recognition" not in jobs:
        findings.append(_finding("free_trial_recognition_missing", "Chapter 1 must declare recognition."))
    if 1 < chapter < arc.chapter_count and not jobs.intersection({"anger", "pity", "regret"}):
        findings.append(
            _finding(
                "free_trial_intermediate_emotion_missing",
                "An intermediate free-trial chapter must declare anger, pity, or regret.",
            )
        )
    if chapter == arc.chapter_count and not jobs.intersection({"agency", "hope"}):
        findings.append(
            _finding(
                "free_trial_closing_agency_missing",
                "The final free-trial chapter must declare agency or hope.",
            )
        )
    return findings


def validate_chapter_design(
    contract: ChapterContract,
    prior_contracts: Sequence[ChapterContract],
    story_contract: CommercialStoryContract,
) -> ChapterDesignReport:
    if not isinstance(contract, ChapterContract):
        raise TypeError("contract must be ChapterContract")
    if not isinstance(story_contract, CommercialStoryContract):
        raise TypeError("story_contract must be CommercialStoryContract")
    if contract.schema_version != 2:
        raise ValueError("commercial chapter design requires schema-v2 chapter contract")
    prior = tuple(prior_contracts)
    findings: list[CommercialFinding] = []
    premise = story_contract.premise_engine
    allowed_anchors = set(premise.belonging_anchors)
    if any(anchor not in allowed_anchors for anchor in contract.belonging_anchors):
        findings.append(_finding("unapproved_belonging_anchor", "Chapter anchor is outside the approved story anchors."))
    if contract.belonging_anchors and "belonging" not in contract.reader_jobs:
        findings.append(_finding("anchor_without_belonging_job", "A declared chapter anchor requires the belonging reader job."))
    if "belonging" in contract.reader_jobs and not contract.belonging_anchors:
        findings.append(_finding("belonging_job_without_anchor", "The belonging reader job requires at least one chapter anchor."))

    known_resources = {
        resource_id
        for previous in prior
        for resource_id in previous.seeded_resource_ids
    }
    known_resources.update(contract.seeded_resource_ids)
    if any(resource_id not in known_resources for resource_id in contract.used_resource_ids):
        findings.append(_finding("unseeded_resource", "Every used resource must be seeded in this or an earlier chapter."))
    if not contract.protagonist_causes_turn:
        findings.append(_finding("protagonist_does_not_cause_turn", "The protagonist must cause the chapter's major turn."))

    if prior and _trailing_count(prior, contract.hook_type, "hook_type") + 1 > story_contract.quality_budgets.identical_hook_type_max + 1:
        findings.append(_finding("identical_hook_budget", "The same hook type repeats beyond the configured consecutive budget."))
    if contract.humiliation_scene and _trailing_count(prior, True, "humiliation_scene") + 1 > story_contract.quality_budgets.consecutive_humiliation_scenes_max:
        findings.append(_finding("humiliation_budget", "Consecutive humiliation scenes exceed the configured budget."))

    findings.extend(_free_trial_findings(contract, story_contract))
    status = "blocked" if any(item.severity == "blocked" for item in findings) else "warning" if findings else "pass"
    return ChapterDesignReport(
        status=status,
        chapter=contract.chapter,
        story_contract_id=story_contract.contract_id,
        chapter_contract_id=contract.contract_id,
        prior_contract_ids=tuple(item.contract_id for item in prior),
        findings=tuple(findings),
    )


def write_design_report(project: Path, report: ChapterDesignReport) -> Path:
    path = project / "outputs/quality/commercial" / f"chapter_{report.chapter:03d}_design.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f"{path.name}.tmp-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def chapter_design_input_hashes(project: Path, chapter: int) -> dict[str, str]:
    """Return immutable contract-head bindings used by the design gate."""
    artifacts = ArtifactStore(project)
    story_head = artifacts.get_head(0, "story_contract")
    current_head = artifacts.get_head(chapter, "chapter_contract")
    if story_head is None or current_head is None:
        raise ValueError("chapter design requires story and chapter contract heads")
    prior_ids: list[str] = []
    for number in range(1, chapter):
        head = artifacts.get_head(number, "chapter_contract")
        if head is None:
            raise ValueError(f"chapter design is missing prior contract head {number}")
        prior_ids.append(head.revision_id)
    encoded = json.dumps(prior_ids, separators=(",", ":")).encode("utf-8")
    return {
        "story_contract_revision_id": story_head.revision_id,
        "chapter_contract_revision_id": current_head.revision_id,
        "prior_contract_revisions_sha256": hashlib.sha256(encoded).hexdigest(),
    }


__all__ = [
    "ChapterDesignReport",
    "CommercialFinding",
    "chapter_design_input_hashes",
    "validate_chapter_design",
    "write_design_report",
]
