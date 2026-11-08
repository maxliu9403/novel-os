"""Library content contract and publication identity, not a literary quality score."""

from pathlib import Path
import pytest

from test_creative_types import ref

from core import creative_types as ct


def test_every_tag_projects_user_methods_without_a_fixed_heading_contract(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    tags = [r for r in store.records().values() if r["kind"] == "tag"]
    assert len(tags) == 41
    mechanisms = []
    for record in tags:
        doc = ct.active_document(record)
        assert doc["schema"] in {"novel-knowledge.v2", "novel-knowledge.v3"}
        freeform = doc.get("format") == "markdown-guide.v1"
        core = doc["body"] if freeform else doc["sections"]["核心承诺"]
        if freeform:
            mechanisms.append(core)
        else:
            mechanisms.append(doc["sections"]["故事机制"])
        snapshot = store.snapshot([ref(store.get("genre.romance")), ref(record)])
        for stage in ct.STAGE_SECTIONS:
            projected = ct.compile_snapshot(snapshot, stage)["documents"][1]
            if freeform:
                assert projected["markdown"] == core
                assert "sections" not in projected
            else:
                assert projected["sections"]["核心承诺"] == core
                assert "研究依据" not in projected["sections"]
    assert len(set(mechanisms)) == len(tags)


def test_all_upgraded_public_ids_match_their_archived_originals(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    archive = (
        Path(__file__).parents[1]
        / "resources/knowledge-imports/romance-all-tags-before-20261106"
    )
    for path in archive.glob("*.md"):
        old = ct.parse_document(path.read_text())
        new = ct.active_document(store.get(old["id"]))
        assert old["publication"] == new["publication"]
        assert old["genre_ids"] == new["genre_ids"]


def test_oversized_user_guides_are_rejected_instead_of_truncated(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    tags = sorted(
        (r for r in store.records().values() if r["kind"] == "tag"),
        key=lambda r: len(r["draft"].encode()),
        reverse=True,
    )[:20]
    # Twenty is a count ceiling, not permission to truncate long author documents.
    before = store.records()
    with pytest.raises(ct.CreativeTypeError, match="超过.*未截断"):
        store.snapshot([ref(store.get("genre.romance")), *map(ref, tags)])
    assert store.records() == before
