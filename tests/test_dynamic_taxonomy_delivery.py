"""Public Genre/Tags survive every container; private guidance never ships."""

import json
from copy import deepcopy
from zipfile import ZipFile

import pytest
from compile_book import gather
from h5_fixtures import compiled_book
from test_creative_types import selected

from core.creative_types import CreativeTypeStore
from core.delivery_package import build_delivery_package
from core.h5_acceptance import inspect_package
from core.h5_import import epub_index
from core.narrative_format import infer_narrative_format, serialization_payload
from core.novel_classification import NovelClassification
from core.publication_taxonomy import classification


def make_dynamic_book(root):
    store = CreativeTypeStore(root / "knowledge")
    taxonomy = store.snapshot(selected(store))["publication_taxonomy"]
    selected_classification = classification(taxonomy, 2)
    value = selected_classification.to_dict()
    serialization = serialization_payload(
        infer_narrative_format(
            selected_classification, chapters=2, target_words=2000, explicit_length=True
        ),
        None,
        allow_legacy_defaults=True,
    )
    book = gather(
        title="A Different Choice",
        author="Fixture Author",
        genre="Romance",
        chapters=[
            {
                "number": 1,
                "title": "The promise",
                "text": "She found his letter and chose to ask him herself.",
            },
            {
                "number": 2,
                "title": "The choice",
                "text": "His answer changed what a promise meant to them.",
            },
        ],
        classification=value,
        serialization=serialization,
    )
    return book, value, serialization


def test_dynamic_zip_epub_sidecar_and_package_share_exact_public_taxonomy(tmp_path):
    project = tmp_path / "project"
    book, value, serialization = make_dynamic_book(tmp_path)
    compiled_book(project, book=book)
    private = project / "outputs/private"
    private.mkdir()
    (private / "source-context.json").write_text("DO_NOT_PUBLISH_SOURCE_TEXT")
    (private / "knowledge.md").write_text("DO_NOT_PUBLISH_PRIVATE_GUIDANCE")
    result = build_delivery_package(project)
    report = inspect_package(result.archive_path)
    assert report["engine_contract_status"] == "passed"
    assert report["external_import_status"] == "pending"
    assert report["required_h5_capabilities"] == [
        "novel-classification.v2",
        "novel-taxonomy.v1",
    ]
    with ZipFile(result.archive_path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    sidecars = [
        json.loads(raw)
        for name, raw in files.items()
        if name.endswith("novel-classification.json")
    ]
    assert sidecars == [value]
    epub = next(raw for name, raw in files.items() if name.endswith(".epub"))
    epub_index(epub, serialization, value)
    from test_cover_epub import NS, epub_parts

    _, _, opf = epub_parts(epub)
    embedded = opf.find(
        'opf:metadata/opf:meta[@property="novel-os:classification"]', NS
    )
    assert json.loads(embedded.text) == value
    package = next(
        json.loads(raw)
        for name, raw in files.items()
        if name.endswith("h5-import.json")
    )
    assert json.loads(files[package["classification"]["path"]]) == value
    assert all(b"DO_NOT_PUBLISH_" not in raw for raw in files.values())
    assert not any("/private/" in name or "knowledge" in name for name in files)


def test_dynamic_classification_rejects_identity_and_alias_tampering(tmp_path):
    _, value, _ = make_dynamic_book(tmp_path)
    for mutate in (
        lambda c: c.update(primary_genre_id="genre.forged"),
        lambda c: c["taxonomy"]["tags"].append(deepcopy(c["taxonomy"]["tags"][0])),
        lambda c: c["taxonomy"]["genre"].update(private_prompt="LEAK"),
        lambda c: c.update(classification_id="0" * 64),
    ):
        invalid = deepcopy(value)
        mutate(invalid)
        with pytest.raises(ValueError):
            NovelClassification.from_dict(invalid)
