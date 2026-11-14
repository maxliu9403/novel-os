"""Published profile receipts remain usable through every read-only consumer."""

import json
from dataclasses import replace
from hashlib import sha256
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.project_delivery_summary import project_delivery_summary
from core.cover_delivery import DeliveryUnavailable, delivery_status, read_current_package
from core.delivery_package import build_delivery_package
from core.delivery_validation import current_package_binding_sha, validated_package_sha
from core.h5_contract import ContractError, PROFILE
from core.h5_import import object_digest
from core.publication_taxonomy import export_target
from tests.h5_fixtures import compiled_book
from tests.test_dynamic_taxonomy_delivery import make_dynamic_book
from tests.test_project_task_summary import _project


def _dynamic_project(tmp_path, channel=None):
    project = _project(tmp_path / "projects", "book", author="Fixture Author")
    book, classification, _ = make_dynamic_book(tmp_path, channel)
    compiled_book(project, book=book)
    return project, classification


def _client(tmp_path):
    return TestClient(create_app(
        projects_root=tmp_path / "projects",
        db_url=f"sqlite:///{tmp_path / 'test.db'}",
        media_root=tmp_path / "media",
    ))


@pytest.mark.parametrize("channel", [None, "male", "female"])
def test_dynamic_profile_build_is_current_in_every_public_consumer(tmp_path, channel):
    project, classification = _dynamic_project(tmp_path, channel)
    result = build_delivery_package(project)
    expected = result.archive_path.read_bytes()
    receipt = json.loads((project / "outputs/publication/export-validation.json").read_text())
    assert {key: receipt[key] for key in export_target(classification)} == export_target(classification)
    assert current_package_binding_sha(project) == result.archive_sha256
    assert validated_package_sha(project) == result.archive_sha256
    assert delivery_status(project)["status"] == "ready"
    assert project_delivery_summary(project).status == "ready"
    assert read_current_package(project) == expected

    client = _client(tmp_path)
    listed = client.get("/api/projects")
    detailed = client.get("/api/projects/book")
    assert listed.status_code == detailed.status_code == 200
    assert listed.json()[0]["delivery"]["status"] == "ready"
    assert detailed.json()["delivery"]["status"] == "ready"
    before = {path: (path.stat().st_mtime_ns, sha256(path.read_bytes()).hexdigest())
              for path in project.rglob("*") if path.is_file()}
    download = client.get("/api/projects/book/deliverables/package")
    assert download.status_code == 200, download.text
    assert download.content == expected
    assert {path: (path.stat().st_mtime_ns, sha256(path.read_bytes()).hexdigest())
            for path in project.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("change", [
    {"target_profile": "novel-h5-export.unknown.v99"},
    {"target_profile": PROFILE.id},
    {"validator_revision": "novel-os:dynamic-taxonomy.v99"},
    {"validator_revision": PROFILE.validator_revision},
    {"catalog_sha256": "0" * 64},
    {"catalog_sha256": PROFILE.catalog_sha256},
    {"required_h5_capabilities": None},
    {"required_h5_capabilities": []},
    {"required_h5_capabilities": ["novel-classification.v2"]},
    {"required_h5_capabilities": ["novel-classification.v2", "novel-taxonomy.v1", "unknown.v1"]},
], ids=["unknown-profile", "static-profile", "unknown-validator", "static-validator",
        "wrong-catalog", "static-catalog", "missing-capabilities", "empty-capabilities",
        "partial-capabilities", "extra-capabilities"])
def test_dynamic_receipt_target_tampering_cannot_authorize_download(tmp_path, change):
    project, _ = _dynamic_project(tmp_path)
    result = build_delivery_package(project)
    receipt_path = project / "outputs/publication/export-validation.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update(change)
    if change.get("required_h5_capabilities", "present") is None:
        receipt.pop("required_h5_capabilities")
    receipt_path.write_text(json.dumps(receipt))

    for validate in (current_package_binding_sha, validated_package_sha):
        with pytest.raises(ContractError, match="delivery_validation_version"):
            validate(project)
    assert delivery_status(project)["status"] == "stale"
    assert project_delivery_summary(project).status != "ready"
    with pytest.raises(DeliveryUnavailable):
        read_current_package(project)
    response = _client(tmp_path).get("/api/projects/book/deliverables/package")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "delivery_not_current"
    assert sha256(result.archive_path.read_bytes()).hexdigest() == result.archive_sha256


def test_dynamic_receipt_cannot_be_relabelled_as_a_static_receipt(tmp_path):
    project, _ = _dynamic_project(tmp_path)
    build_delivery_package(project)
    receipt_path = project / "outputs/publication/export-validation.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update(export_target(None))
    receipt.pop("required_h5_capabilities")
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ContractError, match="delivery_validation_version"):
        validated_package_sha(project)


def test_independent_zip_inspection_rejects_forged_static_projections(tmp_path):
    project, _ = _dynamic_project(tmp_path)
    result = build_delivery_package(project)
    static_project = tmp_path / "static"
    compiled_book(static_project)
    static_result = build_delivery_package(static_project)
    receipt_path = project / "outputs/publication/export-validation.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update(export_target(None))
    receipt.pop("required_h5_capabilities")
    manifest = "outputs/deliverables/package-manifest.json"
    raw = static_result.manifest_path.read_bytes()
    (project / manifest).write_bytes(raw)
    receipt["sources"][manifest] = sha256(raw).hexdigest()
    receipt["source_snapshot_sha256"] = object_digest(receipt["sources"])
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(ContractError, match="delivery_validation_version"):
        validated_package_sha(project)
    assert sha256(result.archive_path.read_bytes()).hexdigest() == result.archive_sha256


def test_dynamic_source_change_invalidates_warmed_validation_caches(tmp_path):
    project, _ = _dynamic_project(tmp_path)
    build_delivery_package(project)
    assert delivery_status(project)["status"] == "ready"
    assert project_delivery_summary(project).status == "ready"
    path = project / "outputs/deliverables/meta/novel-classification.json"
    path.write_bytes(path.read_bytes() + b"\n")

    for validate in (current_package_binding_sha, validated_package_sha):
        with pytest.raises(ContractError, match="delivery_source_changed"):
            validate(project)
    assert delivery_status(project)["status"] == "stale"
    assert project_delivery_summary(project).status == "stale"


def test_dynamic_library_stays_lightweight_and_download_validation_is_cached(tmp_path, monkeypatch):
    from core import delivery_validation

    project, _ = _dynamic_project(tmp_path)
    result = build_delivery_package(project)
    inspections = []
    inspect = delivery_validation.inspect_package_bytes

    def counted(raw):
        inspections.append(sha256(raw).hexdigest())
        return inspect(raw)

    monkeypatch.setattr(delivery_validation, "inspect_package_bytes", counted)
    client = _client(tmp_path)
    assert project_delivery_summary(project).status == "ready"
    assert client.get("/api/projects").json()[0]["delivery"]["status"] == "ready"
    assert client.get("/api/projects/book").json()["delivery"]["status"] == "ready"
    assert inspections == []
    for _ in range(2):
        response = client.get("/api/projects/book/deliverables/package")
        assert response.status_code == 200, response.text
        assert sha256(response.content).hexdigest() == result.archive_sha256
    assert inspections == [result.archive_sha256]


@pytest.mark.parametrize("with_classification", [False, True])
def test_static_and_unclassified_receipts_still_download(tmp_path, with_classification):
    project = _project(tmp_path / "projects", "book", author="Fixture")
    if with_classification:
        from tests.test_h5_import import make_book
        make_book(project, chapters=4)
    else:
        compiled_book(project)
    result = build_delivery_package(project)
    receipt = json.loads((project / "outputs/publication/export-validation.json").read_text())
    assert receipt["target_profile"] == PROFILE.id
    assert current_package_binding_sha(project) == result.archive_sha256
    assert validated_package_sha(project) == result.archive_sha256
    assert delivery_status(project)["status"] == "ready"
    assert project_delivery_summary(project).status == "ready"
    response = _client(tmp_path).get("/api/projects/book/deliverables/package")
    assert response.status_code == 200, response.text
    assert response.content == result.archive_path.read_bytes()


def test_selected_cover_local_rebuild_keeps_dynamic_profile_downloadable(tmp_path, monkeypatch):
    from api.cover_service import CoverService
    from core.cover_store import CoverStore
    from tests.test_cover_epub import embedded_cover
    from tests.test_delivery_package import _cover_set, _image

    def unexpected_generation(*_args, **_kwargs):
        pytest.fail("Local delivery rebuild must not use image generation")

    monkeypatch.setattr(CoverService, "_require_generation_dependencies", unexpected_generation)
    project, classification = _dynamic_project(tmp_path, "female")
    cover = _cover_set(selected=True)
    cover = replace(cover, project_id="book", compiler_version="skeleton-poster.v1",
                    brief=replace(cover.brief, title="A Different Choice"))
    for index, candidate in enumerate(cover.candidates, 1):
        path = project / candidate.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_image(index))
    store = CoverStore(project)
    saved = store.create(cover)
    store.set_active(saved.cover_set_id, expected_revision=0)
    selection_before = (project / "outputs/covers/index.json").read_bytes()
    cover_before = (project / f"outputs/covers/sets/{saved.cover_set_id}.json").read_bytes()
    client = _client(tmp_path)
    url = "/api/projects/book/deliverables/package"

    for _ in range(2):
        rebuilt = client.post(url + "/rebuild")
        assert rebuilt.status_code == 200, rebuilt.text
        assert rebuilt.json()["delivery"]["status"] == "ready"
        download = client.get(url)
        assert download.status_code == 200, download.text
        assert delivery_status(project)["status"] == "ready"
        assert project_delivery_summary(project).status == "ready"
        receipt = json.loads((project / "outputs/publication/export-validation.json").read_text())
        assert {key: receipt[key] for key in export_target(classification)} == export_target(classification)
        archive_path = project / "outputs/deliverables/book-package.zip"
        assert download.content == archive_path.read_bytes()
        with ZipFile(archive_path) as archive:
            assert archive.read("covers/selected-cover.jpg") == _image(1)
            assert embedded_cover(archive.read("book.epub"))[0] == _image(1)
            assert json.loads(archive.read("meta/novel-classification.json")) == classification
        assert (project / "outputs/covers/index.json").read_bytes() == selection_before
        assert (project / f"outputs/covers/sets/{saved.cover_set_id}.json").read_bytes() == cover_before
