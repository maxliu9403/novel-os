from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from core import delivery_package
from core.cover_models import CoverBrief, CoverCandidate, CoverConcept, CoverSet
from core.delivery_package import build_delivery_package
from core.h5_import import object_digest
from core.novel_classification import canonical_json_bytes, infer_classification
from core.narrative_format import infer_narrative_format, serialization_payload
from compile_book import gather
from compile_epub import render_epub
from styles import StyleSheet


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
        media_id=f"media-{index}", sha256=hashlib.sha256(f"cover:{index}".encode()).hexdigest(), width=2048, height=3072,
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
    (deliverables / "book.epub").write_bytes(render_epub(gather(
        title="The Door Is Mine", author="", genre="", chapters=[
            {"number": i, "title": f"Chapter {i}", "text": f"Chapter content {i}."}
            for i in range(1, 5)
        ],
    ), StyleSheet()))
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
    assert manifest["schema_version"] == 4
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


def test_manifest_includes_publication_copy_and_only_the_current_h5_root(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    _payloads(project)
    publication_copy = project / "outputs/publication/publication-copy.json"
    publication_copy.parent.mkdir(parents=True)
    publication_copy.write_text('{"schema_version":1}\n', encoding="utf-8")
    classification = project / "outputs/publication/novel-classification.json"
    classification_value = infer_classification(
        genre="Women's Fiction / Family / Revenge",
        audience="female",
        chapters=4,
    )
    classification.write_bytes(canonical_json_bytes(classification_value))
    format_contract = infer_narrative_format(
        classification_value,
        chapters=4,
        target_words=4000,
        explicit_length=True,
    )
    serialization = project / "outputs/publication/novel-serialization.json"
    serialization.write_text(
        json.dumps(
            serialization_payload(
                format_contract, None, allow_legacy_defaults=True
            ),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    h5_base = project / "outputs/deliverables/h5-publication"
    current = h5_base / "pkg-run-current-aaaaaaaaaaaa"
    stale = h5_base / "pkg-run-stale-bbbbbbbbbbbb"
    for root, marker in ((current, "current"), (stale, "stale")):
        (root / "meta").mkdir(parents=True)
        (root / "chapters").mkdir()
        (root / "meta/publication_package.json").write_text(
            f'{{"package":"{marker}"}}\n', encoding="utf-8"
        )
        (root / "chapters/01.md").write_text(marker, encoding="utf-8")

    result = build_delivery_package(
        project,
        publication_copy_path=publication_copy,
        classification_path=classification,
        serialization_path=serialization,
        h5_root=current,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    roles = {entry["path"]: entry["role"] for entry in manifest["files"]}
    current_prefix = f"h5-publication/{current.name}/"
    assert roles["meta/publication-copy.json"] == "publication_copy"
    assert roles["meta/novel-classification.json"] == "novel_classification"
    assert roles["meta/novel-serialization.json"] == "novel_serialization"
    assert manifest["classification"]["classification_id"].startswith(
        "classification:"
    )
    assert manifest["serialization"]["serialization_id"].startswith(
        "serialization:"
    )
    assert all(
        role == "h5_publication_object"
        for path, role in roles.items()
        if path.startswith(current_prefix)
    )
    assert any(path.startswith(current_prefix) for path in roles)
    assert not any(stale.name in path for path in roles)
    with zipfile.ZipFile(result.archive_path) as archive:
        names = archive.namelist()
        archived_classification = json.loads(
            archive.read("meta/novel-classification.json")
        )
    assert "meta/publication-copy.json" in names
    assert "meta/novel-classification.json" in names
    assert "meta/novel-serialization.json" in names
    assert not any(stale.name in name for name in names)
    assert archived_classification == classification_value.to_dict()

    # Cover generation/selection rebuilds the archive without receiving the
    # publication paths explicitly. The current canonical metadata and H5
    # projection must survive that cover-only rebuild.
    rebuilt = build_delivery_package(project, cover_set=_cover_set())
    rebuilt_manifest = json.loads(rebuilt.manifest_path.read_text(encoding="utf-8"))
    rebuilt_paths = {entry["path"] for entry in rebuilt_manifest["files"]}
    assert "meta/publication-copy.json" in rebuilt_paths
    assert "meta/novel-classification.json" in rebuilt_paths
    assert "meta/novel-serialization.json" in rebuilt_paths
    assert any(path.startswith(current_prefix) for path in rebuilt_paths)
    with zipfile.ZipFile(rebuilt.archive_path) as archive:
        assert json.loads(
            archive.read("meta/novel-classification.json")
        ) == classification_value.to_dict()


def test_omitted_current_inputs_do_not_repackage_stale_projections(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    _payloads(project)
    stale_copy = project / "outputs/deliverables/meta/publication-copy.json"
    stale_copy.parent.mkdir(parents=True)
    stale_copy.write_text("stale\n", encoding="utf-8")
    stale_classification = (
        project / "outputs/deliverables/meta/novel-classification.json"
    )
    stale_classification.write_text("stale\n", encoding="utf-8")
    stale_serialization = (
        project / "outputs/deliverables/meta/novel-serialization.json"
    )
    stale_serialization.write_text("stale\n", encoding="utf-8")
    stale_h5 = project / "outputs/deliverables/h5-publication/pkg-old-aaaaaaaaaaaa"
    (stale_h5 / "meta").mkdir(parents=True)
    (stale_h5 / "meta/publication_package.json").write_text(
        "{}\n", encoding="utf-8"
    )

    result = build_delivery_package(project)

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in manifest["files"]}
    assert "meta/publication-copy.json" not in paths
    assert "meta/novel-classification.json" not in paths
    assert "meta/novel-serialization.json" not in paths
    assert not any(path.startswith("h5-publication/") for path in paths)


@pytest.mark.parametrize("guessed_type", [None, "application/x-wrong-system-type"])
def test_known_delivery_media_types_are_independent_of_system_mapping(
    tmp_path: Path, monkeypatch, guessed_type: str | None,
) -> None:
    project = tmp_path / "project"
    _payloads(project, selected=True)
    deliverables = project / "outputs/deliverables"
    (deliverables / "extras").mkdir()
    for name in ("preview.png", "preview.jpeg", "preview.webp"):
        (deliverables / "extras" / name).write_bytes(b"mime metadata fixture")
    cover_set = _cover_set(selected=True)
    expected = {
        ".epub": "application/epub+zip",
        ".md": "text/markdown",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }

    # Compare deterministic ZIP output against a system with correct MIME data.
    monkeypatch.setattr(delivery_package.mimetypes, "guess_type", lambda name: (
        expected.get(Path(name).suffix.lower()), None,
    ))
    baseline = build_delivery_package(project, cover_set=cover_set).archive_path.read_bytes()
    monkeypatch.setattr(delivery_package.mimetypes, "guess_type", lambda _: (guessed_type, None))
    result = build_delivery_package(project, cover_set=cover_set)

    with zipfile.ZipFile(result.archive_path) as archive:
        manifest = json.loads(archive.read("package-manifest.json"))
        entries = {item["path"]: item for item in manifest["files"]}
        assert entries["book.epub"]["media_type"] == "application/epub+zip"
        for entry in entries.values():
            assert entry["media_type"] == expected[Path(entry["path"]).suffix.lower()]
            data = archive.read(entry["path"])
            assert entry["size"] == len(data)
            assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert manifest["package_revision_sha256"] == object_digest(manifest["files"])
        sidecar = json.loads(archive.read("meta/h5-import.json"))
        assert sidecar["cover"]["selected"] == entries["covers/selected-cover.jpg"]
        assert sidecar["cover"]["metadata"] == entries["covers/cover-set.json"]
        assert sidecar["import_revision_sha256"] == object_digest({
            "book_id": sidecar["book_id"], "versions": sidecar["versions"],
        })
    assert result.archive_path.read_bytes() == baseline


@pytest.mark.parametrize("name,expected", [
    ("book.EPUB", "application/epub+zip"),
    ("cover.JPG", "image/jpeg"),
    ("metadata.JSON", "application/json"),
])
def test_known_media_type_ignores_suffix_case(tmp_path, monkeypatch, name, expected):
    source = tmp_path / name
    source.write_bytes(b"mime metadata fixture")
    monkeypatch.setattr(delivery_package.mimetypes, "guess_type", lambda _: (None, None))
    assert delivery_package._manifest_entry(tmp_path, source, None)["media_type"] == expected


@pytest.mark.parametrize("guessed,expected", [
    ("application/x-custom-attachment", "application/x-custom-attachment"),
    (None, "application/octet-stream"),
])
def test_unknown_attachment_keeps_mime_fallback(tmp_path, monkeypatch, guessed, expected):
    source = tmp_path / "attachment.custom"
    source.write_bytes(b"custom attachment")
    monkeypatch.setattr(delivery_package.mimetypes, "guess_type", lambda _: (guessed, None))
    assert delivery_package._manifest_entry(tmp_path, source, None)["media_type"] == expected


def test_mime_only_repackage_preserves_payloads_and_import_identity(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _payloads(project, selected=True)
    real_entry = delivery_package._manifest_entry

    def legacy_entry(root, path, cover_set):
        entry = real_entry(root, path, cover_set)
        if path.suffix.lower() == ".epub":
            entry["media_type"] = "application/octet-stream"
        return entry

    # Simulate the deployed legacy packager, not a newly invented book identity.
    with monkeypatch.context() as legacy:
        legacy.setattr(delivery_package, "_manifest_entry", legacy_entry)
        result = build_delivery_package(project, cover_set=_cover_set(selected=True))
    with zipfile.ZipFile(result.archive_path) as archive:
        old_members = {name: archive.read(name) for name in archive.namelist()}
    old_manifest = json.loads(old_members["package-manifest.json"])
    old_sidecar = json.loads(old_members["meta/h5-import.json"])

    monkeypatch.setattr(delivery_package.mimetypes, "guess_type", lambda _: (None, None))
    rebuilt = build_delivery_package(project)  # Same path used by cover-only rebuilds.
    with zipfile.ZipFile(rebuilt.archive_path) as archive:
        new_members = {name: archive.read(name) for name in archive.namelist()}
    new_manifest = json.loads(new_members["package-manifest.json"])
    new_sidecar = json.loads(new_members["meta/h5-import.json"])

    assert next(item for item in new_manifest["files"] if item["path"] == "book.epub")["media_type"] == "application/epub+zip"
    assert set(old_members) == set(new_members)
    assert [name for name in old_members if old_members[name] != new_members[name]] == ["package-manifest.json"]
    assert new_manifest["book_id"] == old_manifest["book_id"]
    assert new_sidecar == old_sidecar  # Includes chapter IDs, all versions and import revision.
    assert new_manifest["package_revision_sha256"] != old_manifest["package_revision_sha256"]
    assert new_manifest["package_revision_sha256"] == object_digest(new_manifest["files"])
    rebuilt_bytes = rebuilt.archive_path.read_bytes()
    assert build_delivery_package(project).archive_path.read_bytes() == rebuilt_bytes
