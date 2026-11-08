"""Author Markdown survives import, model projection and public export boundaries."""

import json
from pathlib import Path

import pytest

from core import creative_types as ct
from core.audience_guidance import replace_audience_guidance
from core.creative_execution import make_handoff
from core.knowledge_import import read_import
from test_creative_types import ref, selected

PACK = Path(__file__).parents[1] / "resources/knowledge-imports/romance-35-tags"
FILES = read_import(PACK)


def import_all(store, markdowns=None):
    markdowns = markdowns or [md for _, md in FILES]
    records = store.records()
    ids = [ct.parse_document(md)["id"] for md in markdowns]
    return store.import_documents(markdowns, expected_revisions={
        identifier: records[identifier]["revision"] if identifier in records else None
        for identifier in ids
    })


@pytest.mark.parametrize("path,markdown", FILES, ids=[p.stem for p, _ in FILES])
def test_every_author_tag_reaches_every_model_stage_verbatim(path, markdown, tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    result = import_all(store, [markdown])
    identifier = result["items"][0]["id"]
    record = store.get(identifier)
    assert record["draft"].encode("utf-8") == path.read_bytes()
    doc = ct.parse_document(markdown, enabled=True)
    assert doc["format"] == "tag-prompt.v1"
    assert doc["genre_ids"] == ["genre.romance"]
    assert doc["appendices"] == []
    assert len(doc["sections"]) >= 12
    snapshot = store.snapshot([ref(store.get("genre.romance")), ref(record)])
    for stage in ct.STAGE_SECTIONS:
        compiled = ct.compile_snapshot(snapshot, stage)
        item = compiled["documents"][1]
        assert item["markdown"].encode("utf-8") == path.read_bytes()
        assert item["source_sha256"] == ct.text_sha(markdown)
        assert "sections" not in item  # Do not duplicate raw + parsed text in prompts.
        assert compiled["projection_manifest"][1]["excluded_sections"] == []
    public = snapshot["publication_taxonomy"]["tags"][0]
    assert public["labels"]["zh-CN"] == doc["name"]
    assert public["labels"]["en-US"] == doc["aliases"][0]
    assert "强制节拍" not in json.dumps(public, ensure_ascii=False)


def test_batch_preserves_unselected_records_snapshots_and_backup(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    before = store.records()
    old_snapshot = store.snapshot(selected(store))
    old_context = ct.compile_snapshot(old_snapshot, "draft")
    result = import_all(store)
    assert len(FILES) == 35
    imported_ids = {ct.parse_document(md)["id"] for _, md in FILES}
    assert set(store.records()) == set(before) | imported_ids
    assert sum(r["action"] == "replaced" for r in result["items"]) == len(set(before) & imported_ids)
    assert sum(r["action"] == "created" for r in result["items"]) == len(imported_ids - set(before))
    assert store.get("tag.second_chance") == before["tag.second_chance"]
    assert store.get("genre.romance") == before["genre.romance"]
    for identifier, original in before.items():
        assert store.get(identifier)["versions"][0] == original["versions"][0]
    assert ct.compile_snapshot(old_snapshot, "draft") == old_context
    backup = json.loads((tmp_path / result["backup"]).read_bytes())
    assert backup["records"] == before
    first_bytes = store.path.read_bytes()
    again = import_all(store)
    assert {r["action"] for r in again["items"]} == {"unchanged"}
    assert again["backup"] is None
    assert store.path.read_bytes() == first_bytes


@pytest.mark.parametrize("failure", ["invalid", "duplicate", "collision", "malformed_association", "concurrent"])
def test_invalid_batch_never_partially_overwrites_library(tmp_path, failure):
    store = ct.CreativeTypeStore(tmp_path)
    md = next(md for p, md in FILES if p.stem == "Betrayal")
    import_all(store, [md])
    documents = [md + "\n\nAuthor's edit.\n", FILES[0][1]]
    if failure == "invalid":
        documents[1] = "not a tag document"
    elif failure == "duplicate":
        documents[1] = md
    elif failure == "collision":
        documents[1] = md.replace("# 背叛 Betrayal", "# 背叛 Duplicate")
    elif failure == "malformed_association":
        documents[1] = documents[1].replace("关联 Genre：Romance", "关联 Genre：浪漫")
    before = store.path.read_bytes()
    with pytest.raises(ct.CreativeTypeError):
        if failure == "concurrent":
            store.import_documents(documents, expected_revisions={"tag.betrayal": 0, "tag.adventurous": None})
        else:
            import_all(store, documents)
    assert store.path.read_bytes() == before


def test_legacy_association_metadata_does_not_constrain_explicit_channel_selection(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    markdown = FILES[0][1].replace("关联 Genre：Romance", "关联 Genre：Missing")
    identifier = ct.parse_document(markdown)["id"]
    import_all(store, [markdown])
    original = store.get(identifier)
    assert original["draft"] == markdown
    assert ct.active_document(original)["genre_ids"] == ["genre.missing"]
    assert "genre.missing" not in store.records()
    guided = replace_audience_guidance(markdown, ["female"], {
        "female": "让人物选择与代价改变关系，兑现该标签承诺的阅读回报。",
    })
    import_all(store, [guided])
    record = store.get(identifier)
    assert record["versions"][:-1] == original["versions"]
    refs = [ref(store.get("genre.romance")), ref(record)]
    snapshot = store.snapshot(refs, audience_channel="female")
    assert snapshot["schema"] == "selection-snapshot.v2"
    assert snapshot["genre_id"] == "genre.romance"
    assert snapshot["tag_ids"] == [identifier]
    # Historical snapshot rules remain frozen; only the explicit v2 contract
    # removes association membership as a selection constraint.
    with pytest.raises(ct.CreativeTypeError, match="不属于当前 Genre"):
        store.snapshot(refs)


def test_raw_format_preserves_crlf_future_sections_and_fenced_headings(tmp_path):
    md = FILES[0][1] + "\n## 新增写作要求\n保留完整内容。\n```md\n## 核心承诺\n只是示例\n```\n  \n"
    md = md.replace("\n", "\r\n")
    store = ct.CreativeTypeStore(tmp_path)
    import_all(store, [md])
    doc = ct.parse_document(md, enabled=True)
    assert "只是示例" in doc["sections"]["新增写作要求"]
    snapshot = store.snapshot([ref(store.get("genre.romance")), ref(store.get(doc["id"]))])
    handoff = make_handoff(source="Approved story", origin={}, type_snapshot=snapshot)
    for context in handoff["stage_contexts"].values():
        assert context["documents"][1]["markdown"] == md
    assert ct.compile_snapshot(snapshot, "cover")["documents"][1]["markdown"] == md


def test_raw_format_rejects_index_bad_metadata_and_missing_required_sections():
    md = FILES[0][1]
    invalid = [
        (PACK / "README.md").read_text(),
        md.replace("关联 Genre：Romance", "来源：Romance"),
        md.replace("关联 Genre：Romance", "关联 Genre：浪漫"),
        md.replace("## 核心承诺", "## 未命名"),
        md + "\n## 核心承诺\n重复\n",
    ]
    for value in invalid:
        with pytest.raises(ct.CreativeTypeError):
            ct.parse_document(value, enabled=True)


def test_largest_four_tags_fit_budget_and_oversized_selection_is_not_truncated(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    import_all(store)
    ordered = sorted(FILES, key=lambda pair: len(pair[1].encode()), reverse=True)
    refs = [ref(store.get(ct.parse_document(md)["id"])) for _, md in ordered]
    snapshot = store.snapshot([ref(store.get("genre.romance")), *refs[:4]])
    for stage in ct.STAGE_SECTIONS:
        assert len(ct.encoded(ct.compile_snapshot(snapshot, stage))) < ct.CONTEXT_LIMIT
    with pytest.raises(ct.CreativeTypeError, match="未截断"):
        store.snapshot([ref(store.get("genre.romance")), *refs[:20]])
