"""Audience selection changes guidance, never author prose or frozen history."""
from copy import deepcopy

import pytest

from core import creative_types as ct
from core.audience_guidance import (
    AudienceGuidanceError, END, START, audience_metadata,
    parse_audience_guidance, replace_audience_guidance,
)
from core.knowledge_markdown import document_body, prepare_markdown
from test_creative_types import ref
from test_workshop_api import client  # noqa: F401


GUIDES = {"female": "女性读者：尊严与关系选择产生回报。", "male": "男性读者：关系改变行动与担当的代价。"}


def source(kind, name, *, genre_ids=None):
    return prepare_markdown(
        "## 原创方法\r\n作者保留的全部文字。  \r\n",
        identity={"id": f"{kind}.{name}", "kind": kind, "name": name, "genre_ids": genre_ids or []},
    )["markdown"]


def install(store, kind, name, *, channels=None, genre_ids=None):
    original = source(kind, name, genre_ids=genre_ids)
    md = replace_audience_guidance(original, channels or ["female", "male"], GUIDES)
    result = store.import_documents([md], expected_revisions={f"{kind}.{name}": None})
    return store.get(result["items"][0]["id"])


@pytest.mark.parametrize("original", ["无换行结尾", "原始  \n", "原始\r\n\r\n", "\ufeff原始正文。\n\n"])
def test_append_update_keep_original_bytes_and_receipt(original):
    md = replace_audience_guidance(original, ["female"], GUIDES)
    module = parse_audience_guidance(md)
    assert md.encode().startswith(original.encode())
    assert module.original == original
    assert module.public()["original_sha256"] == ct.text_sha(original)
    updated = replace_audience_guidance(md, ["male"], {**GUIDES, "male": "新的男性读者方法。"})
    assert parse_audience_guidance(updated).original == original
    assert parse_audience_guidance(updated).guides["female"] == GUIDES["female"]
    assert updated.count(START) == 1


def test_quoted_fenced_marker_examples_do_not_enable_a_channel():
    example = "说明\n```md\n" + START + "\n" + END + "\n```\n> " + START + "\n"
    assert parse_audience_guidance(example) is None
    actual = replace_audience_guidance(example, ["female"], GUIDES)
    assert parse_audience_guidance(actual).original == example


@pytest.mark.parametrize("mutate", [
    lambda s: s + START + "\n" + END + "\n",
    lambda s: s.replace(END, ""),
    lambda s: s + "频道模块之后的额外文字",
    lambda s: s.replace("schema: audience-guidance.v1", "schema: audience-guidance.v1\nschema: audience-guidance.v1"),
    lambda s: s.replace("- female", "- unknown"),
    lambda s: s.replace("原文", "被改写的原文", 1),
])
def test_invalid_module_never_becomes_selectable(mutate):
    md = mutate(replace_audience_guidance("原文\n", ["female"], GUIDES))
    with pytest.raises(AudienceGuidanceError):
        parse_audience_guidance(md)
    assert audience_metadata(md)["audience_ready"] is False


def test_audience_snapshot_no_genre_coupling_and_selected_guide_only(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    genre = install(store, "genre", "future")
    tag = install(store, "tag", "unbound", genre_ids=["genre.no_longer_exists"])
    frozen = store.snapshot([ref(genre), ref(tag)], audience_channel="male")
    assert frozen["schema"] == "selection-snapshot.v2"
    assert frozen["audience_channel"] == "male"
    for stage in ct.STAGE_SECTIONS:
        context = ct.compile_snapshot(frozen, stage)
        assert context["compiler_version"] == "knowledge-compiler.v5"
        assert context["audience_channel"] == "male"
        for row, item in zip(frozen["documents"], context["documents"]):
            assert item["markdown"] == document_body(row["markdown"])
            assert item["audience_guidance"] == GUIDES["male"]
        assert GUIDES["female"] not in ct.encoded(context).decode()
    before = ct.compile_snapshot(frozen, "draft")
    store.mutate("delete", genre["id"], revision=genre["revision"], operation_id="delete-genre")
    assert ct.compile_snapshot(frozen, "draft") == before
    assert store.get(tag["id"])["status"] == "enabled"


def test_snapshot_rejects_missing_channel_guidance_and_too_many_tags(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    genre = install(store, "genre", "future")
    tags = [install(store, "tag", name, channels=["female"]) for name in ("first", "second", "third")]
    refs = [ref(genre), ref(tags[0])]
    for channel in ("general", "male"):
        with pytest.raises(ct.CreativeTypeError):
            store.snapshot(refs, audience_channel=channel)
    with pytest.raises(ct.CreativeTypeError, match="1–2"):
        store.snapshot([ref(genre), *map(ref, tags)], audience_channel="female")
    naked = source("tag", "unprepared")
    store.import_documents([naked], expected_revisions={"tag.unprepared": None})
    with pytest.raises(ct.CreativeTypeError, match="完整写作指导"):
        store.snapshot([ref(genre), ref(store.get("tag.unprepared"))], audience_channel="female")
    frozen = store.snapshot(refs, audience_channel="female")
    tampered = deepcopy(frozen)
    tampered["audience_channel"] = "male"
    with pytest.raises(ct.CreativeTypeError, match="校验失败"):
        ct.compile_snapshot(tampered, "draft")


def test_global_alias_conflict_is_not_hidden_in_a_different_former_genre(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    first = source("tag", "first", genre_ids=["genre.one"])
    second = source("tag", "second", genre_ids=["genre.two"]).replace("name: second", "name: first")
    store.import_documents([first], expected_revisions={"tag.first": None})
    with pytest.raises(ct.CreativeTypeError, match="冲突"):
        store.import_documents([second], expected_revisions={"tag.second": None})


def test_author_prose_editor_preserves_channel_module_and_rebinds_receipt():
    original = replace_audience_guidance(source("tag", "test"), ["female"], GUIDES)
    edited = prepare_markdown("作者主动修改的正文。\n", base_markdown=original)
    assert document_body(edited["markdown"]) == "作者主动修改的正文。\n"
    parsed = parse_audience_guidance(edited["markdown"])
    assert parsed.guides == GUIDES
    assert parsed.audience_channels == ("female",)
    assert edited["document"]["audience_ready"] is True


def test_api_audience_update_is_revision_checked_idempotent_and_append_only(client):
    identifier = "tag.betrayal"
    original = client.get(f"/api/creative-types/{identifier}").json()
    original_source = parse_audience_guidance(original["draft"])
    original_source = original_source.original if original_source else original["draft"]
    body = {
        "revision": original["revision"], "operation_id": "set-audience-channel",
        "audience_channels": ["female"], "guides": GUIDES,
    }
    response = client.post(f"/api/creative-types/{identifier}/audience", json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["versions"][:-1] == original["versions"]
    assert parse_audience_guidance(saved["draft"]).original == original_source
    assert saved["document"]["audience_guidance"]["guides"] == GUIDES
    assert client.post(f"/api/creative-types/{identifier}/audience", json=body).json() == saved
    stale = client.post(f"/api/creative-types/{identifier}/audience", json={**body, "operation_id": "stale-audience-op"})
    assert stale.status_code == 409
    invalid = client.post(f"/api/creative-types/{identifier}/audience", json={**body, "revision": saved["revision"], "operation_id": "invalid-audience-op", "guides": {"female": ""}})
    assert invalid.status_code == 422
    listed = client.get("/api/creative-types").json()["items"]
    summary = next(item for item in listed if item["id"] == identifier)
    assert summary["audience_channels"] == ["female"]
    assert summary["audience_ready"] is True
