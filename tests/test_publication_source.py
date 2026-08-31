import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import publication_source as source_module
from artifacts import ArtifactStore
from pipeline_models import ManifestStore, RunManifest, RunSpec, StageResult
from publication_source import (
    PublicationSourceError,
    PublicationSourceSet,
    SourceChapter,
    build_evidence_ledger,
    build_publication_source_set,
    group_source_chapters,
    publication_source_input_hash,
    validate_conflict_evidence,
)


_FINISHED_AT = "2026-08-31T00:00:00+00:00"


def _legacy_receipt_id(run_id: str, stage: StageResult) -> str:
    identity = json.dumps(
        {
            "run_id": run_id,
            "chapter": stage.chapter,
            "revision_id": stage.revision_id,
            "artifact_hashes": stage.artifact_hashes,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "legacy-receipt-" + hashlib.sha256(identity).hexdigest()


def _bound_project(
    tmp_path: Path,
    *,
    quality_policy: str = "evidence_v1",
    texts: tuple[str, ...] = (
        "# One\n\nApproved chapter one.",
        "# Two\n\nApproved chapter two.",
        "# Three\n\nApproved chapter three.",
        "# Four\n\nApproved chapter four.",
    ),
):
    project = tmp_path / "project"
    run_id = "run-001"
    artifact_store = ArtifactStore(project)
    manifest = RunManifest(
        run_id=run_id,
        spec=RunSpec(
            project_path=str(project),
            num_chapters=len(texts),
            quality_policy=quality_policy,
        ),
    )
    receipts = {}

    for number, text in enumerate(texts, start=1):
        revision = artifact_store.put_text(
            chapter=number,
            kind="final",
            text=text,
            source="test",
        )
        artifact_store.set_head(
            number,
            "final",
            revision.revision_id,
            expected_revision_id=None,
        )
        relative = f"outputs/manuscript/chapter_{number:03d}_final.md"
        receipt_id = "promotion-receipt-" + f"{number:064x}"
        stage = StageResult(
            phase="chapter.promote",
            chapter=number,
            status="done",
            artifact_paths=[relative],
            artifact_hashes={relative: revision.sha256},
            revision_id=revision.revision_id,
            promotion_receipt_id=receipt_id,
            finished_at=_FINISHED_AT,
        )
        if quality_policy == "legacy":
            stage.promotion_receipt_id = _legacy_receipt_id(run_id, stage)
        else:
            receipts[f"pipeline-{run_id}-chapter-{number}"] = SimpleNamespace(
                receipt_id=receipt_id,
                idempotency_key=f"pipeline-{run_id}-chapter-{number}",
                chapter=number,
                new_artifact_revision_id=revision.revision_id,
                new_artifact_sha256=revision.sha256,
                committed_at=_FINISHED_AT,
            )
        manifest.record(stage)

    ManifestStore(project / "outputs/runs" / run_id / "run.json").save(manifest)
    return project, manifest, receipts


def _install_receipts(monkeypatch, receipts):
    calls = []

    class FakePromotionService:
        def __init__(self, project):
            self.project = project

        def load_receipt(self, key, *, check_current_tail=True):
            calls.append((key, check_current_tail))
            return receipts.get(key)

    monkeypatch.setattr(source_module, "PromotionService", FakePromotionService)
    return calls


def _source_set(texts: list[str]) -> PublicationSourceSet:
    chapters = tuple(
        SourceChapter(
            number=number,
            title=f"Chapter {number}",
            revision_id=hashlib.sha256(f"revision-{number}".encode()).hexdigest(),
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            text=text,
            promotion_receipt_id="legacy-receipt-" + f"{number:064x}",
            finalized_at=_FINISHED_AT,
        )
        for number, text in enumerate(texts, start=1)
    )
    provisional = PublicationSourceSet(
        run_id="run-source",
        chapters=chapters,
        source_set_sha256="0" * 64,
    )
    return replace(
        provisional,
        source_set_sha256=publication_source_input_hash(provisional),
    )


def test_source_set_reads_final_head_bytes_and_receipt_not_mutable_projection(
    tmp_path,
    monkeypatch,
):
    project, manifest, receipts = _bound_project(tmp_path)
    calls = _install_receipts(monkeypatch, receipts)
    projection = project / "outputs/manuscript/chapter_001_final.md"
    projection.parent.mkdir(parents=True, exist_ok=True)
    projection.write_text("forged", encoding="utf-8")

    source = build_publication_source_set(
        project,
        manifest.run_id,
        4,
        "evidence_v1",
    )

    assert source.chapters[0].text == "# One\n\nApproved chapter one."
    assert source.chapters[0].title == "One"
    assert source.chapters[0].revision_id == ArtifactStore(project).get_head(
        1, "final"
    ).revision_id
    assert calls == [
        (f"pipeline-{manifest.run_id}-chapter-{number}", False)
        for number in range(1, 5)
    ]


def test_source_set_blocks_missing_receipt_or_final_head_drift(tmp_path, monkeypatch):
    project, manifest, receipts = _bound_project(tmp_path)
    _install_receipts(monkeypatch, receipts)
    receipts.pop(f"pipeline-{manifest.run_id}-chapter-1")

    with pytest.raises(PublicationSourceError, match="promotion receipt"):
        build_publication_source_set(project, manifest.run_id, 4, "evidence_v1")

    _, _, receipts = _bound_project(tmp_path / "drift")
    project = tmp_path / "drift/project"
    _install_receipts(monkeypatch, receipts)
    store = ArtifactStore(project)
    current = store.get_head(1, "final")
    drifted = store.put_text(
        chapter=1,
        kind="final",
        text="A divergent final.",
        source="test-drift",
        parent_revision_id=current.revision_id,
    )
    store.set_head(
        1,
        "final",
        drifted.revision_id,
        expected_revision_id=current.revision_id,
    )

    with pytest.raises(PublicationSourceError, match="promotion.*diverg"):
        build_publication_source_set(project, manifest.run_id, 4, "evidence_v1")


def test_legacy_source_requires_completed_deterministic_stage_binding(tmp_path):
    project, manifest, _ = _bound_project(tmp_path, quality_policy="legacy")

    source = build_publication_source_set(project, manifest.run_id, 4, "legacy")

    assert source.chapters[0].promotion_receipt_id.startswith("legacy-receipt-")
    assert source.chapters[0].finalized_at == _FINISHED_AT

    stored = ManifestStore(
        project / "outputs/runs" / manifest.run_id / "run.json"
    ).load()
    stored.get("chapter.promote", 2).promotion_receipt_id = (
        "legacy-receipt-" + "f" * 64
    )
    ManifestStore(project / "outputs/runs" / manifest.run_id / "run.json").save(stored)
    with pytest.raises(PublicationSourceError, match="legacy promotion receipt"):
        build_publication_source_set(project, manifest.run_id, 4, "legacy")


def test_source_fingerprint_is_ordered_stable_and_content_bound(tmp_path, monkeypatch):
    project, manifest, receipts = _bound_project(tmp_path)
    _install_receipts(monkeypatch, receipts)

    first = build_publication_source_set(project, manifest.run_id, 4, "evidence_v1")
    second = build_publication_source_set(project, manifest.run_id, 4, "evidence_v1")
    changed_chapter = replace(first.chapters[1], sha256="f" * 64)
    changed = replace(
        first,
        chapters=(first.chapters[0], changed_chapter, *first.chapters[2:]),
    )
    expected_identity = [
        {
            "chapter": item.number,
            "revision_id": item.revision_id,
            "sha256": item.sha256,
        }
        for item in first.chapters
    ]
    expected_sha256 = hashlib.sha256(
        json.dumps(
            expected_identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    assert first.source_set_sha256 == expected_sha256
    assert first.source_set_sha256 == publication_source_input_hash(first)
    assert publication_source_input_hash(first) == publication_source_input_hash(
        second
    )
    assert publication_source_input_hash(changed) != first.source_set_sha256


def test_grouping_preserves_all_text_and_only_splits_at_paragraph_boundaries():
    source = _source_set(["A" * 90_000 + "\n\n" + "B" * 40_000, "Chapter two."])

    groups = group_source_chapters(source, max_codepoints=120_000)
    flattened = [chapter for group in groups for chapter in group]

    assert [chapter.number for chapter in flattened] == [1, 1, 2]
    assert "".join(
        item.text for item in flattened if item.number == 1
    ) == source.chapters[0].text
    assert flattened[0].text.endswith("\n\n")
    assert all(
        sum(len(item.text) for item in group) <= 120_000 for group in groups
    )

    ledger_records = [
        json.loads(line)
        for group in groups
        for line in build_evidence_ledger(group).splitlines()
    ]
    assert [record["source_quote"] for record in ledger_records] == [
        item.text for item in flattened
    ]
    assert [record["chapter"] for record in ledger_records] == [1, 1, 2]


def test_grouping_rejects_a_paragraph_that_cannot_fit():
    source = _source_set(["A" * 120_001])

    with pytest.raises(PublicationSourceError, match="paragraph boundary"):
        group_source_chapters(source, max_codepoints=120_000)


def test_grouping_preserves_crlf_paragraph_boundaries_exactly():
    original = "A" * 12 + "\r\n \r\n" + "B" * 12
    source = _source_set([original])

    groups = group_source_chapters(source, max_codepoints=18)
    segments = [item for group in groups for item in group]

    assert len(segments) == 2
    assert segments[0].text.endswith("\r\n \r\n")
    assert "".join(item.text for item in segments) == original


def test_conflict_evidence_requires_exact_quotes_from_each_proportional_bucket():
    source = _source_set(
        [
            "Opening pressure remains.",
            "Early middle response.",
            "The opposition escalates.",
            "The cost becomes personal.",
            "Late pressure remains unresolved.",
        ]
    )
    evidence = {
        "opening": [
            {"chapter": 1, "source_quote": "Opening pressure remains."}
        ],
        "middle": [
            {"chapter": 3, "source_quote": "The opposition escalates."}
        ],
        "late": [
            {"chapter": 5, "source_quote": "Late pressure remains unresolved."}
        ],
    }

    validate_conflict_evidence(source, evidence)

    wrong_bucket = {**evidence, "late": [evidence["middle"][0]]}
    with pytest.raises(PublicationSourceError, match="late.*bucket"):
        validate_conflict_evidence(source, wrong_bucket)

    wrong_quote = {
        **evidence,
        "middle": [{"chapter": 3, "source_quote": "Not in the Final chapter."}],
    }
    with pytest.raises(PublicationSourceError, match="exact source quote"):
        validate_conflict_evidence(source, wrong_quote)

    six_chapters = _source_set([f"Chapter {number}." for number in range(1, 7)])
    midpoint_evidence = {
        "opening": [{"chapter": 1, "source_quote": "Chapter 1."}],
        "middle": [
            {"chapter": 2, "source_quote": "Chapter 2."},
        ],
        "late": [
            {"chapter": 5, "source_quote": "Chapter 5."},
            {"chapter": 6, "source_quote": "Chapter 6."},
        ],
    }
    validate_conflict_evidence(six_chapters, midpoint_evidence)
