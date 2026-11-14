"""Malformed frozen classification metadata cannot escape public error handling."""

import json
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.project_delivery_summary import project_delivery_summary
from core.cover_delivery import DeliveryUnavailable, delivery_status, read_current_package
from core.delivery_package import build_delivery_package
from core.h5_import import object_digest
from tests.h5_fixtures import compiled_book
from tests.test_dynamic_taxonomy_delivery import make_dynamic_book
from tests.test_project_task_summary import _project


@pytest.mark.parametrize("field,value", [
    ("confidence", []),
    ("filter_type_ids", 1),
    ("confidence", 10 ** 400),
], ids=["nonnumeric-confidence", "noniterable-filter-ids", "overflowing-confidence"])
def test_malformed_bound_classification_returns_stale_and_download_conflict(tmp_path, field, value):
    project = _project(tmp_path / "projects", "book", author="Fixture Author")
    book, _, _ = make_dynamic_book(tmp_path)
    compiled_book(project, book=book)
    result = build_delivery_package(project)
    assert delivery_status(project)["status"] == "ready"
    assert project_delivery_summary(project).status == "ready"

    relative = "outputs/deliverables/meta/novel-classification.json"
    path = project / relative
    classification = json.loads(path.read_text())
    classification[field] = value
    path.write_text(json.dumps(classification))
    # A self-consistent local receipt still cannot authorize malformed metadata.
    receipt_path = project / "outputs/publication/export-validation.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["sources"][relative] = sha256(path.read_bytes()).hexdigest()
    receipt["source_snapshot_sha256"] = object_digest(receipt["sources"])
    receipt_path.write_text(json.dumps(receipt))

    status = delivery_status(project)
    assert status["status"] == "stale"
    assert "classification_contract" in status["error"]
    assert project_delivery_summary(project).status == "stale"
    with pytest.raises(DeliveryUnavailable, match="classification_contract"):
        read_current_package(project)

    client = TestClient(create_app(
        projects_root=tmp_path / "projects",
        db_url=f"sqlite:///{tmp_path / 'test.db'}",
        media_root=tmp_path / "media",
    ))
    listed = client.get("/api/projects")
    detailed = client.get("/api/projects/book")
    assert listed.status_code == detailed.status_code == 200
    assert listed.json()[0]["delivery"]["status"] == "stale"
    assert detailed.json()["delivery"]["status"] == "stale"
    download = client.get("/api/projects/book/deliverables/package")
    assert download.status_code == 409, download.text
    assert download.json()["detail"]["code"] == "delivery_not_current"
    assert sha256(result.archive_path.read_bytes()).hexdigest() == result.archive_sha256
