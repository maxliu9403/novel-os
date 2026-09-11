from __future__ import annotations

import json
import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from compile_book import gather
from compile_epub import render_epub
from core.delivery_package import build_delivery_package
from core.h5_import import digest, epub_index, object_digest
from core.narrative_format import infer_narrative_format, serialization_payload, volume_contract_template
from core.novel_classification import infer_classification
from styles import StyleSheet


def make_book(project: Path, *, chapters=80, series=False):
    classification = infer_classification(genre="Women's Fiction / Revenge", audience="women 50",
                                         chapters=chapters)
    form = infer_narrative_format(
        classification, chapters=chapters, target_words=chapters * 900,
        explicit_length=True, mode="series_installment" if series else "multi_volume",
        volume_count=4,
        **({"series_id": "family-test", "series_title": "Family Test", "series_book_number": 2,
            "planned_books": 4} if series else {}),
    ).with_confirmation("author_selected")
    volumes = volume_contract_template(form)
    for volume in volumes:
        n = volume["volume_number"]
        volume.update(title=f"Volume {n}", central_conflict=f"Conflict {n}",
                      volume_promise=f"Promise {n}", protagonist_shift=f"Shift {n}",
                      payoff=f"Payoff {n}", carryover_hook=f"Consequence {n}" if n < 4 else "")
    serialization = serialization_payload(form, volumes)
    records = [
        {"number": n, "title": f"Chapter {n}", "text": (
            ("## STORY_LEAD: Introduction\n\nA mother discovers the family secret.\n\n" if n == 1 else "")
            + f"# Chapter {n}\n\nTest chapter {n}: Her choice changes the family's future."
        )} for n in range(1, chapters + 1)
    ]
    book = gather(title="H5 Import Test", author="Fixture", genre="Women's Fiction",
                  chapters=records, classification=classification.to_dict(), serialization=serialization)
    deliverables = project / "outputs/deliverables"
    publication = project / "outputs/publication"
    deliverables.mkdir(parents=True)
    publication.mkdir(parents=True)
    (deliverables / "book.epub").write_bytes(render_epub(book, StyleSheet()))
    (publication / "novel-classification.json").write_text(json.dumps(classification.to_dict()))
    (publication / "novel-serialization.json").write_text(json.dumps(serialization))
    return book, classification.to_dict(), serialization


def import_sidecar(result):
    with zipfile.ZipFile(result.archive_path) as archive:
        return json.loads(archive.read("meta/h5-import.json"))


def test_intro_does_not_shift_chapters_volumes_or_free_trial(tmp_path):
    project = tmp_path / "book"
    make_book(project)
    result = build_delivery_package(project)
    data = import_sidecar(result)
    assert data["chapter_count"] == 80
    assert len(data["non_chapter_items"]) == 1
    assert data["non_chapter_items"][0]["kind"] == "introduction"
    assert data["non_chapter_items"][0]["epub"]["item_id"] == "intro"
    first, twenty_first = data["chapters"][0], data["chapters"][20]
    assert first["number"] == 1
    assert first["epub"]["spine_index"] == 1
    assert first["epub"]["item_id"] == "c1"
    assert first["epub"]["href"] == "chap001.xhtml"
    assert twenty_first["volume_id"] == "volume_02"
    assert twenty_first["chapter_in_volume"] == 1
    assert [r["number"] for r in data["chapters"] if r["number"] <= 3] == [1, 2, 3]
    assert twenty_first["number"] > 3  # New volume does not reset the free window.
    with zipfile.ZipFile(result.archive_path) as outer:
        with zipfile.ZipFile(BytesIO(outer.read("book.epub"))) as epub:
            for record in data["chapters"]:
                body = epub.read(record["epub"]["zip_path"])
                assert digest(body) == record["epub"]["xhtml_sha256"]
                assert len(body) == record["epub"]["xhtml_size"]


def test_cover_only_change_keeps_book_body_and_structure_identity(tmp_path):
    project = tmp_path / "book"
    make_book(project)
    cover = project / "outputs/deliverables/covers/selected-cover.png"
    cover.parent.mkdir()
    cover.write_bytes(b"cover-A")
    a = import_sidecar(build_delivery_package(project))
    cover.write_bytes(b"cover-B")
    b = import_sidecar(build_delivery_package(project))
    assert a["book_id"] == b["book_id"]
    assert a["chapters"] == b["chapters"]
    for component in ("content", "epub", "classification", "serialization", "front_matter"):
        assert a["versions"][component + "_sha256"] == b["versions"][component + "_sha256"]
    assert a["versions"]["selected_cover_sha256"] != b["versions"]["selected_cover_sha256"]
    assert a["import_revision_sha256"] != b["import_revision_sha256"]
    assert b == import_sidecar(build_delivery_package(project))


def test_same_structure_is_not_book_identity_and_replica_preserves_identity(tmp_path):
    for name in ("a", "b"):
        make_book(tmp_path / name)
    a = import_sidecar(build_delivery_package(tmp_path / "a"))
    b = import_sidecar(build_delivery_package(tmp_path / "b"))
    assert a["versions"]["serialization_sha256"] == b["versions"]["serialization_sha256"]
    assert a["book_id"] != b["book_id"]
    shutil.copytree(tmp_path / "a", tmp_path / "replica")
    replica = import_sidecar(build_delivery_package(tmp_path / "replica"))
    assert a["book_id"] == replica["book_id"]


def test_series_binding_and_conflicting_epub_metadata(tmp_path):
    project = tmp_path / "book"
    _, classification, serialization = make_book(project, series=True)
    data = import_sidecar(build_delivery_package(project))
    assert data["chapters"][22]["series_id"] == "family-test"
    assert data["chapters"][22]["series_book_number"] == 2
    assert data["chapters"][22]["chapter_in_volume"] == 3
    classification["audience"]["channel"] = "male"
    with pytest.raises(ValueError, match="classification conflicts"):
        epub_index((project / "outputs/deliverables/book.epub").read_bytes(), serialization, classification)


def test_classification_update_changes_metadata_version_not_chapter_content(tmp_path):
    project = tmp_path / "book"
    book, _, _ = make_book(project)
    a = import_sidecar(build_delivery_package(project))
    book.classification = infer_classification(genre="Mystery", audience="male", chapters=80).to_dict()
    (project / "outputs/publication/novel-classification.json").write_text(json.dumps(book.classification))
    (project / "outputs/deliverables/book.epub").write_bytes(render_epub(book, StyleSheet()))
    b = import_sidecar(build_delivery_package(project))
    assert a["book_id"] == b["book_id"]
    assert a["versions"]["content_sha256"] == b["versions"]["content_sha256"]
    assert a["versions"]["classification_sha256"] != b["versions"]["classification_sha256"]
    assert a["versions"]["epub_sha256"] != b["versions"]["epub_sha256"]


def test_invalid_epub_map_and_duplicate_selected_files_stop_export(tmp_path):
    project = tmp_path / "book"
    make_book(project)
    cover = project / "outputs/deliverables/covers"
    cover.mkdir()
    (cover / "selected-cover.jpg").write_bytes(b"A")
    (cover / "selected-cover.png").write_bytes(b"B")
    with pytest.raises(ValueError, match="Multiple selected cover"):
        build_delivery_package(project)


def test_package_revision_includes_cover_and_sidecar_file_bytes(tmp_path):
    project = tmp_path / "book"
    make_book(project)
    result = build_delivery_package(project)
    with zipfile.ZipFile(result.archive_path) as archive:
        manifest = json.loads(archive.read("package-manifest.json"))
        assert manifest["package_revision_sha256"] == object_digest(manifest["files"])
        for entry in manifest["files"]:
            data = archive.read(entry["path"])
            assert entry["sha256"] == digest(data)
            assert entry["size"] == len(data)


@pytest.mark.parametrize("fault", ["missing_xhtml", "wrong_number", "wrong_href", "unmapped_intro"])
def test_epub_map_errors_are_reported_before_import(tmp_path, fault):
    project = tmp_path / "book"
    _, classification, serialization = make_book(project)
    buffer = BytesIO()
    with zipfile.ZipFile(project / "outputs/deliverables/book.epub") as source:
        members = {name: source.read(name) for name in source.namelist()}
    opf = ET.fromstring(members["OEBPS/content.opf"])
    meta = next(m for m in opf.findall("{*}metadata/{*}meta") if m.get("property") == "novel-os:chapter-map")
    value = json.loads(meta.text)
    if fault == "missing_xhtml":
        members.pop("OEBPS/chap021.xhtml")
    elif fault == "wrong_number":
        value["chapters"][0]["number"] = 2
    elif fault == "wrong_href":
        value["chapters"][0]["href"] = "chap002.xhtml"
    else:
        value["non_chapter_items"] = []
    meta.text = json.dumps(value)
    members["OEBPS/content.opf"] = ET.tostring(opf)
    with zipfile.ZipFile(buffer, "w") as target:
        for name, data in members.items():
            target.writestr(name, data)
    with pytest.raises(ValueError):
        epub_index(buffer.getvalue(), serialization, classification)


def test_legacy_epub_without_map_remains_exportable_without_guessing_binding(tmp_path):
    project = tmp_path / "book"
    _, classification, serialization = make_book(project)
    epub_path = project / "outputs/deliverables/book.epub"
    with zipfile.ZipFile(epub_path) as source:
        members = {name: source.read(name) for name in source.namelist()}
    opf = ET.fromstring(members["OEBPS/content.opf"])
    metadata = opf.find("{*}metadata")
    for meta in list(metadata):
        if meta.get("property") == "novel-os:chapter-map":
            metadata.remove(meta)
    members["OEBPS/content.opf"] = ET.tostring(opf)
    with zipfile.ZipFile(epub_path, "w") as target:
        for name, data in members.items():
            target.writestr(name, data)
    data = import_sidecar(build_delivery_package(project))
    assert data["status"] == "legacy_epub"
    assert data["epub"]["sha256"] == digest(epub_path.read_bytes())
    assert data["chapters"] == []
    assert data["versions"]["content_sha256"] is None
    classification["audience"]["channel"] = "male"
    with pytest.raises(ValueError, match="classification conflicts"):
        epub_index(epub_path.read_bytes(), serialization, classification)


def test_old_h5_evidence_conflicting_with_root_json_is_rejected(tmp_path):
    project = tmp_path / "book"
    make_book(project)
    root = project / "outputs/deliverables/h5-publication/old-snapshot"
    (root / "meta").mkdir(parents=True)
    (root / "meta/publication_package.json").write_text(json.dumps({
        "version": 3, "classification": {"primary_genre_id": "wrong"},
    }))
    with pytest.raises(ValueError, match="H5 evidence classification conflicts"):
        build_delivery_package(project, h5_root=root)
