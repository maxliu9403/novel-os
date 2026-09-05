from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from core.publication_copy import canonical_json_bytes
from core.publication_source import PublicationSourceSet, SourceChapter


@dataclass(frozen=True)
class _CopyFixture:
    hook_lead: str = (
        "Claire must protect her unborn child before Ethan's betrayal destroys home."
    )
    spoiler_free_blurb: str = (
        "Claire has built an ordinary life around a marriage she believed was steady, "
        "but late pregnancy turns every small absence into a warning she can no longer "
        "dismiss. Ethan keeps choosing excuses over honesty while bills, family pressure, "
        "and the approaching birth leave her with less room to pretend. Speaking up could "
        "shatter the home she hoped to give her child; staying silent lets his betrayal "
        "define what that home becomes. As each confrontation exposes another broken "
        "promise, Claire begins recording what happened, protecting what money remains, "
        "and deciding which relatives deserve her trust. She wants her baby to inherit "
        "security rather than fear, yet every practical step toward independence makes "
        "Ethan more determined to control the story. Before the birth forces a choice, "
        "Claire must decide whether preserving the appearance of a family is worth losing "
        "the future she can still build."
    )


def _source_set(count: int = 4) -> PublicationSourceSet:
    names = ("One", "Two", "Three", "Four", "Five")
    chapters = []
    for number in range(1, count + 1):
        text = f"# {names[number - 1]}\n\nBody {number}."
        sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chapters.append(SourceChapter(
            number=number,
            title=names[number - 1],
            revision_id=sha256,
            sha256=sha256,
            text=text,
            promotion_receipt_id=f"promotion-receipt-{sha256}",
            finalized_at="2026-08-31T00:00:00Z",
        ))
    source_sha = hashlib.sha256(canonical_json_bytes([
        {
            "chapter": chapter.number,
            "revision_id": chapter.revision_id,
            "sha256": chapter.sha256,
        }
        for chapter in chapters
    ])).hexdigest()
    return PublicationSourceSet(
        run_id="run-123",
        chapters=tuple(chapters),
        source_set_sha256=source_sha,
    )


def test_h5_projector_matches_v1_bytes_counts_receipts_and_snapshot(tmp_path: Path):
    from core.h5_publication import project_h5_publication

    source = _source_set()
    result = project_h5_publication(
        tmp_path,
        "run-123",
        source,
        _CopyFixture(),
        "2026-08-31T00:00:00Z",
        "2026-08-31T00:01:00Z",
        "The Empty Chair Beside Her",
        ["The Chair Beside Her"],
        ["Family Drama", "Women's Fiction"],
    )

    assert result is not None
    package_raw = result.publication_package_path.read_bytes()
    finalization_raw = result.finalization_path.read_bytes()
    package = json.loads(package_raw)
    finalization = json.loads(finalization_raw)
    delivery = json.loads(result.delivery_path.read_bytes())
    expected_merged = b"\n\n".join(
        chapter.text.encode("utf-8") for chapter in source.chapters
    ) + b"\n"

    assert package["version"] == 1
    assert package["platform"] == "novel-os"
    assert package["hook_lead"] == _CopyFixture().hook_lead
    assert package["spoiler_free_blurb"] == _CopyFixture().spoiler_free_blurb
    assert package["counting_policy"] == "utf8-runes"
    assert package["total_runes"] == sum(len(item.text) for item in source.chapters)
    assert package["final_checks"] == {
        "body_hashes": True,
        "chapter_order": True,
        "delivery": True,
    }
    assert package["chapters"][0]["body_sha256"] == hashlib.sha256(
        (result.root / "chapters/01.md").read_bytes()
    ).hexdigest()
    assert finalization["receipts"] == [
        {
            "chapter_number": chapter.number,
            "body_sha256": chapter.sha256,
            "verdict": "accepted",
        }
        for chapter in source.chapters
    ]
    assert (result.root / "正文.md").read_bytes() == expected_merged
    assert finalization["merged_manuscript_sha256"] == hashlib.sha256(
        expected_merged
    ).hexdigest()
    assert delivery["snapshot_sha256"] == hashlib.sha256(
        package_raw + finalization_raw
    ).hexdigest()
    assert delivery["delivered_at"] == "2026-08-31T00:01:00Z"
    assert result.snapshot_sha256 == delivery["snapshot_sha256"]
    assert result.package_id == f"pkg-run-123-{result.snapshot_sha256[:12]}"


def test_h5_projector_returns_none_for_books_shorter_than_four_chapters(
    tmp_path: Path,
):
    from core.h5_publication import project_h5_publication

    result = project_h5_publication(
        tmp_path,
        "run-123",
        _source_set(3),
        _CopyFixture(),
        "2026-08-31T00:00:00Z",
        "2026-08-31T00:01:00Z",
        "Short Book",
        [],
        [],
    )

    assert result is None
    assert not (tmp_path / "outputs/deliverables/h5-publication").exists()


def test_h5_projector_reuses_only_a_byte_identical_immutable_root(tmp_path: Path):
    from core.h5_publication import H5PublicationError, project_h5_publication

    arguments = (
        tmp_path,
        "run-123",
        _source_set(),
        _CopyFixture(),
        "2026-08-31T00:00:00Z",
        "2026-08-31T00:01:00Z",
        "The Empty Chair Beside Her",
        [],
        [],
    )
    first = project_h5_publication(*arguments)
    second = project_h5_publication(*arguments)

    assert first == second
    linked = tmp_path / "linked-chapter.md"
    os.link(first.root / "chapters/01.md", linked)
    with pytest.raises(H5PublicationError, match="single-link"):
        project_h5_publication(*arguments)
    linked.unlink()
    (first.root / "chapters/01.md").write_bytes(b"tampered")
    with pytest.raises(H5PublicationError, match="byte-identical"):
        project_h5_publication(*arguments)
    assert first.root.exists()
    assert (first.root / "chapters/01.md").read_bytes() == b"tampered"


def test_h5_projector_rejects_noncanonical_catalog_metadata(tmp_path: Path):
    from core.h5_publication import H5PublicationError, project_h5_publication

    with pytest.raises(H5PublicationError, match="duplicate tag"):
        project_h5_publication(
            tmp_path,
            "run-123",
            _source_set(),
            _CopyFixture(),
            "2026-08-31T00:00:00Z",
            "2026-08-31T00:01:00Z",
            "The Empty Chair Beside Her",
            [],
            ["Drama", "Drama"],
        )


def test_h5_validator_binds_finalization_and_delivery_stage_times(tmp_path: Path):
    from core.h5_publication import (
        H5PublicationError,
        delivery_snapshot,
        project_h5_publication,
        validate_h5_publication_root,
    )

    result = project_h5_publication(
        tmp_path,
        "run-123",
        _source_set(),
        _CopyFixture(),
        "2026-08-31T00:00:00Z",
        "2026-08-31T00:01:00Z",
        "The Empty Chair Beside Her",
        [],
        [],
    )
    delivery = json.loads(result.delivery_path.read_bytes())
    delivery["delivered_at"] = "2026-08-31T02:00:00Z"
    result.delivery_path.write_text(
        json.dumps(delivery, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(H5PublicationError, match="delivery timestamp"):
        validate_h5_publication_root(
            result.root,
            expected_finalized_at="2026-08-31T00:00:00Z",
            expected_delivered_at="2026-08-31T00:01:00Z",
        )

    finalization = json.loads(result.finalization_path.read_bytes())
    finalization["finalized_at"] = "2026-08-31T02:00:00Z"
    result.finalization_path.write_text(
        json.dumps(finalization, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    package_raw = result.publication_package_path.read_bytes()
    finalization_raw = result.finalization_path.read_bytes()
    delivery["snapshot_sha256"] = delivery_snapshot(package_raw, finalization_raw)
    delivery["delivered_at"] = "2026-08-31T00:01:00Z"
    result.delivery_path.write_text(
        json.dumps(delivery, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(H5PublicationError, match="finalization timestamp"):
        validate_h5_publication_root(
            result.root,
            expected_finalized_at="2026-08-31T00:00:00Z",
            expected_delivered_at="2026-08-31T00:01:00Z",
        )


def test_h5_publish_race_rechecks_an_existing_byte_identical_root(
    tmp_path: Path,
    monkeypatch,
):
    from core import h5_publication

    def competing_publish(source: Path, target: Path) -> bool:
        shutil.copytree(source, target)
        return False

    monkeypatch.setattr(
        h5_publication, "_rename_directory_noreplace", competing_publish
    )
    result = h5_publication.project_h5_publication(
        tmp_path,
        "run-123",
        _source_set(),
        _CopyFixture(),
        "2026-08-31T00:00:00Z",
        "2026-08-31T00:01:00Z",
        "The Empty Chair Beside Her",
        [],
        [],
    )

    assert result.root.is_dir()
    assert h5_publication.validate_h5_publication_root(result.root)
