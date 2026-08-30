from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from core import delivery_package
from core.cover_models import CoverBrief, CoverCandidate, CoverConcept, CoverSet
from core.delivery_package import build_delivery_package


SHA = "a" * 64


def _cover_set(selected: bool = False) -> CoverSet:
    brief = CoverBrief.from_dict({
        "schema_version": 1,
        "title": "The Door Is Mine",
        "language": "English",
        "genre": "domestic revenge",
        "target_audience": "women 30-50",
        "market_scope": "English serialized fiction",
        "core_task": "A caregiver claims a home.",
        "core_conflict": "Her family demands her labor.",
        "emotional_promise": "earned independence",
        "protagonist": {"role": "caregiver", "visual_identity": "woman with key", "agency_signal": "closes door"},
        "relationship_or_power_contrast": "one woman against two households",
        "decisive_story_node": "she signs a deed",
        "secondary_task": {"story_function": "protect child", "visual_signal": "backpack"},
        "world_signals": ["fictional commuter district"],
        "title_direction": {"hierarchy": "large", "preferred_zone": "top", "readability": "mobile_thumbnail"},
        "forbidden_elements": ["real cities"],
    }, source_prompt_sha256=SHA)
    concepts = [CoverConcept(
        concept_id=f"concept-{i}", visual_strategy=f"strategy-{i}",
        focal_scene=f"scene-{i}", composition="portrait", palette="red",
        secondary_signal="key", title_treatment="large title",
        generation_prompt=f'Render exact title "The Door Is Mine" once. Strategy {i}',
    ) for i in range(1, 5)]
    base = CoverSet.new("project-one", brief, concepts)
    candidates = tuple(CoverCandidate(
        candidate_id=item.candidate_id,
        concept_id=item.concept_id,
        status="selected" if selected and index == 1 else "ready",
        relative_path=f"outputs/deliverables/covers/pending/cover-{index:02d}.jpg",
        media_id=f"media-{index}", sha256=SHA, width=2048, height=3072,
        content_type="image/jpeg", provider="openai_compatible", model="gpt-image-2",
        request_id=f"req-{index}", generation_prompt=concepts[index - 1].generation_prompt,
        safe_request_parameters={"size": "2048x3072"},
    ) for index, item in enumerate(base.candidates, start=1))
    return CoverSet(
        **{
            **base.to_dict(),
            "brief": brief,
            "concepts": tuple(concepts),
            "candidates": candidates,
            "status": "selected" if selected else "ready",
            "selected_candidate_id": candidates[0].candidate_id if selected else "",
        }
    )


def _payloads(project: Path, selected: bool = False) -> None:
    deliverables = project / "outputs/deliverables"
    (deliverables / "covers/pending").mkdir(parents=True)
    for name in ("book.md", "book.epub", "book.pdf", "book.docx", "book.html"):
        (deliverables / name).write_bytes(f"payload:{name}".encode())
    for index in range(1, 5):
        (deliverables / f"covers/pending/cover-{index:02d}.jpg").write_bytes(
            f"cover:{index}".encode()
        )
    if selected:
        (deliverables / "covers/selected-cover.jpg").write_bytes(b"cover:1")


def test_package_contains_common_exports_pending_covers_and_metadata(tmp_path) -> None:
    project = tmp_path / "project"
    _payloads(project)

    result = build_delivery_package(project, cover_set=_cover_set())

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    paths = [entry["path"] for entry in manifest["files"]]
    assert paths == sorted(paths)
    assert paths[:4] == ["book.docx", "book.epub", "book.md", "book.pdf"]
    assert "book.html" not in paths
    assert "covers/cover-set.json" in paths
    assert len([path for path in paths if path.startswith("covers/pending/")]) == 4
    assert manifest["cover"]["status"] == "ready"
    assert manifest["cover"]["selected_candidate_id"] == ""
    assert all(entry["sha256"] == hashlib.sha256(
        (project / "outputs/deliverables" / entry["path"]).read_bytes()
    ).hexdigest() for entry in manifest["files"])

    with zipfile.ZipFile(result.archive_path) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert "package-manifest.json" in names
        assert "book-package.zip" not in names


def test_selected_cover_is_declared_without_removing_pending_candidates(tmp_path) -> None:
    project = tmp_path / "project"
    _payloads(project, selected=True)

    result = build_delivery_package(project, cover_set=_cover_set(selected=True))
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert manifest["cover"]["status"] == "selected"
    assert manifest["cover"]["selected_candidate_id"]
    assert any(entry["path"] == "covers/selected-cover.jpg" for entry in manifest["files"])
    assert len([entry for entry in manifest["files"] if entry["role"] == "cover_candidate"]) == 4


def test_package_excludes_symlinks_temporary_files_and_previous_archive(tmp_path) -> None:
    project = tmp_path / "project"
    _payloads(project)
    deliverables = project / "outputs/deliverables"
    (deliverables / ".book.tmp").write_text("temp")
    (deliverables / "book-package.zip").write_text("old")
    (deliverables / "outside-link").symlink_to(tmp_path / "outside")

    result = build_delivery_package(project)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in manifest["files"]}

    assert ".book.tmp" not in paths
    assert "book-package.zip" not in paths
    assert "outside-link" not in paths


def test_identical_inputs_produce_identical_archive_bytes(tmp_path) -> None:
    project = tmp_path / "project"
    _payloads(project)
    cover_set = _cover_set()

    first = build_delivery_package(project, cover_set=cover_set).archive_path.read_bytes()
    second = build_delivery_package(project, cover_set=cover_set).archive_path.read_bytes()

    assert second == first


def test_failed_archive_rebuild_preserves_previous_package(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    _payloads(project)
    previous = build_delivery_package(project).archive_path.read_bytes()

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(delivery_package, "_write_archive", fail)
    with pytest.raises(OSError, match="disk full"):
        build_delivery_package(project)

    assert (project / "outputs/deliverables/book-package.zip").read_bytes() == previous
