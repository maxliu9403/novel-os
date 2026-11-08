from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from core import creative_types as ct


def ref(record):
    v = next(v for v in record["versions"] if v["version"] == record["active_version"])
    return ct.TypeRef(id=record["id"], version=v["version"], source_sha256=v["sha256"])


def selected(store):
    return [ref(store.get("genre.romance")), ref(store.get("tag.betrayal"))]


def test_seeds_compile_structured_sections_or_complete_freeform_bodies(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    assert len(store.records()) == 42
    for r in store.records().values():
        ct.parse_document(r["draft"], enabled=True)
    snapshot = store.snapshot(selected(store))
    for stage in ct.STAGE_SECTIONS:
        compiled = ct.compile_snapshot(snapshot, stage)
        assert len(compiled["documents"]) == 2
        for d in compiled["documents"]:
            if d.get("format") == "markdown-guide.v1":
                source = ct.active_document(store.get(d["id"]))
                assert d["markdown"] == source["body"]
                assert "研究依据" not in d["markdown"]
            else:
                assert "核心承诺" in d["sections"]
                assert "研究依据" not in d["sections"]
                assert ("封面表达" in d["sections"]) == (stage == "cover")
    assert not tmp_path.joinpath("knowledge.json").exists()


def test_selection_exactly_one_genre_associations_and_size(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    refs = selected(store)
    for invalid in [[], refs[:1], refs[1:], refs + refs[:1]]:
        with pytest.raises(ct.CreativeTypeError):
            store.snapshot(invalid)
    bad = deepcopy(store.snapshot(refs))
    bad["documents"][1]["markdown"] = bad["documents"][1]["markdown"].replace(
        "genre.romance", "genre.horror"
    )
    bad["documents"][1]["source_sha256"] = ct.text_sha(bad["documents"][1]["markdown"])
    bad["snapshot_sha256"] = ct.digest(
        {k: v for k, v in bad.items() if k != "snapshot_sha256"}
    )
    with pytest.raises(ct.CreativeTypeError, match="不属于"):
        ct.compile_snapshot(bad, "draft")


def test_frozen_snapshot_survives_edit_delete_and_detects_tamper(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    snapshot = store.snapshot(selected(store))
    before = ct.compile_snapshot(snapshot, "draft")
    record = store.get("tag.betrayal")
    changed = store.mutate(
        "activate",
        record["id"],
        revision=0,
        operation_id="activate-1",
        markdown=record["draft"] + "\n新约束\n",
    )
    store.mutate(
        "delete", record["id"], revision=changed["revision"], operation_id="delete-01"
    )
    assert ct.compile_snapshot(snapshot, "draft") == before
    with pytest.raises(ct.CreativeTypeError):
        store.snapshot(selected(ct.CreativeTypeStore(tmp_path / "other")))
    snapshot["documents"][0]["markdown"] += "tamper"
    with pytest.raises(ct.CreativeTypeError):
        ct.compile_snapshot(snapshot, "draft")


def test_draft_activation_revision_idempotency_and_concurrent_writes(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    md = ct.template("genre", "genre.example", "示例")
    draft = store.mutate(
        "create", None, revision=0, operation_id="create-01", markdown=md
    )
    assert draft["status"] == "draft"
    with pytest.raises(ct.CreativeTypeError, match="完成"):
        store.mutate(
            "activate", draft["id"], revision=1, operation_id="activate-1", markdown=md
        )
    complete = md.replace("## ", "## ")
    for heading in ct.SECTIONS:
        complete = complete.replace(
            "## " + heading + "\n", "## " + heading + "\n\n具体承诺和代价。\n"
        )

    def write():
        try:
            return store.mutate(
                "activate",
                draft["id"],
                revision=1,
                operation_id="activate-2",
                markdown=complete,
            )
        except ct.CreativeTypeError as e:
            return e

    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(lambda _: write(), range(2)))
    assert a == b and a["active_version"] == 1
    with pytest.raises(ct.CreativeTypeError, match="同一操作"):
        store.mutate(
            "activate",
            draft["id"],
            revision=1,
            operation_id="activate-2",
            markdown=complete + "\n",
        )
    with pytest.raises(ct.CreativeTypeError, match="已被修改"):
        store.mutate(
            "draft", draft["id"], revision=1, operation_id="save-003", markdown=complete
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.replace("kind: genre", "kind: genre\nkind: tag"),
        lambda s: s.replace("id: genre.romance", "id: tag.romance"),
        lambda s: s + "\n## 核心承诺\n再次定义",
        lambda s: s.replace(
            "aliases:", "evil: !!python/object:builtins.object {}\naliases:"
        ),
    ],
)
def test_reject_malformed_markdown(tmp_path, change):
    # Fixed structured dialect fixture; current author-edited defaults are freeform.
    md = (Path(__file__).parents[1] / "resources/knowledge-imports/romance-all-tags-before-20261106/genre.romance.md").read_text()
    with pytest.raises(ct.CreativeTypeError):
        ct.parse_document(change(md), enabled=True)


def test_fenced_headings_are_not_parsed(tmp_path):
    md = (Path(__file__).parents[1] / "resources/knowledge-imports/romance-all-tags-before-20261106/genre.romance.md").read_text()
    doc = ct.parse_document(md + "\n```md\n## 核心承诺\nexample\n```\n", enabled=True)
    assert "```md" in doc["sections"]["封面表达"]


def test_alias_collisions_are_scoped_by_genre(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    md = store.get("tag.betrayal")["draft"].replace(
        "id: tag.betrayal", "id: tag.duplicate"
    )
    record = store.mutate(
        "create", None, revision=0, operation_id="duplicate-create", markdown=md
    )
    with pytest.raises(ct.CreativeTypeError, match="冲突"):
        store.mutate(
            "activate",
            record["id"],
            revision=1,
            operation_id="duplicate-activate",
            markdown=md,
        )


def default_refs():
    # Content-addressed bundled knowledge; no filesystem mutation.
    records = ct._initial_records()
    return [ref(records["genre.romance"]), ref(records["tag.betrayal"])]
