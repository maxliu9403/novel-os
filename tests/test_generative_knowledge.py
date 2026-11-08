"""Generative methods reach real requests; old frozen inputs remain byte-stable."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from core import creative_types as ct
from core.creative_execution import make_handoff
from test_creative_types import ref

ROOT = Path(__file__).parents[1]
FILES = [ct.RESOURCE / "tags" / f"{name}.md" for name in
         ("action", "dark-romance", "possessive", "badboy", "age-gap")]
IDS = ["tag.action", "tag.dark_romance", "tag.possessive", "tag.badboy", "tag.age_gap"]


def selection(store):
    return store.snapshot([ref(store.get(i)) for i in ["genre.romance", *IDS]])


def test_five_methods_project_by_stage_and_freeze_into_execution(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    snapshot = selection(store)
    assert snapshot["compiler_version"] == "knowledge-compiler.v4"
    for stage in ct.STAGE_SECTIONS:
        context = ct.compile_snapshot(snapshot, stage)
        assert len(context["documents"]) == 6
        assert len(ct.encoded(context)) < ct.CONTEXT_LIMIT
        assert context["interpretation"]["schema"] == "generative-guidance.v2"
        for doc in context["documents"]:
            source = ct.active_document(store.get(doc["id"]))
            if source["schema"] == "novel-knowledge.v3":
                assert doc["markdown"] == source["body"]
            else:
                assert doc["schema"] == "novel-knowledge.v2"
                assert set(doc["sections"]) == set(ct.GENERATIVE_STAGE_SECTIONS[stage])
            assert "前 10 章强制节拍" not in str(doc)
        assert context["documents"][0]["roles"] == []
        assert all(doc["roles"] for doc in context["documents"][1:])
    handoff = make_handoff(source="作者采用的完整骨架", origin={}, type_snapshot=snapshot)
    for stage, context in handoff["stage_contexts"].items():
        assert context == ct.compile_snapshot(snapshot, stage)
        if stage in {"chapter_plan", "draft", "review"}:
            assert all("发散空间" not in d["sections"] for d in context["documents"] if "sections" in d)


@pytest.mark.parametrize("roles", ["[]", "[relationship, relationship]", "[unknown]", "relationship", "[conflict, relationship, mood, pacing]"])
def test_roles_are_validated_before_enabling(roles):
    # v2 requires at least one role; freeform v3 deliberately permits none.
    original = ROOT / "resources/knowledge-imports/romance-methods-before-20261107/tag.age_gap.md"
    md = original.read_text().replace("[relationship, character]", roles)
    with pytest.raises(ct.CreativeTypeError):
        ct.parse_document(md, enabled=True)


def test_genre_cannot_claim_tag_roles_and_v1_shape_stays_unchanged():
    cases = json.loads((ROOT / "tests/fixtures/knowledge-before-generative-v3.json").read_text())
    legacy = cases["v1"]["snapshot"]["documents"][0]["markdown"]
    assert "roles" not in ct.parse_document(legacy)
    md = (ct.RESOURCE / "genres/romance.md").read_text()
    assert ct.parse_document(md)["roles"] == []
    with pytest.raises(ct.CreativeTypeError, match="Genre 不填写"):
        ct.parse_document(md.replace("roles: []", "roles: [conflict]"))


def test_genre_upgrade_keeps_frozen_inputs_and_public_identity(tmp_path, monkeypatch):
    cases = json.loads((ROOT / "tests/fixtures/knowledge-before-generative-v3.json").read_text())
    legacy = cases["v1"]["snapshot"]["documents"][0]["markdown"]
    initial = ct._initial_records()
    # Reproduce an installed v1 built-in before the new resource ships.
    genre = initial["genre.romance"]
    genre["draft"] = genre["versions"][0]["markdown"] = legacy
    genre["versions"][0]["sha256"] = ct.text_sha(legacy)
    monkeypatch.setattr(ct, "_initial_records", lambda: deepcopy(initial))
    store = ct.CreativeTypeStore(tmp_path)
    old = selection(store)
    before = store.records()
    contexts = {stage: ct.compile_snapshot(old, stage) for stage in ct.STAGE_SECTIONS}
    md = (ct.RESOURCE / "genres/romance.md").read_text()
    result = store.import_documents([md], expected_revisions={"genre.romance": 0})
    assert result["items"][0]["version"] == 2
    assert result["backup"] and (tmp_path / result["backup"]).is_file()
    for identifier, record in before.items():
        after = store.get(identifier)
        if identifier == "genre.romance":
            assert after["versions"][:1] == record["versions"]
        else:
            assert after == record
    new = selection(store)
    assert new["publication_taxonomy"] == old["publication_taxonomy"]
    for stage, context in contexts.items():
        assert ct.compile_snapshot(old, stage) == context
        new_document = ct.compile_snapshot(new, stage)["documents"][0]
        assert new_document["markdown"] == ct.parse_document(md)["body"]
        assert "sections" not in new_document
        assert context["documents"][0]["sections"]


def test_compiler_identity_cannot_be_silently_reinterpreted(tmp_path):
    snapshot = selection(ct.CreativeTypeStore(tmp_path))
    snapshot["compiler_version"] = "knowledge-compiler.v1"
    snapshot["snapshot_sha256"] = ct.digest({k: v for k, v in snapshot.items() if k != "snapshot_sha256"})
    with pytest.raises(ct.CreativeTypeError, match="编译版本"):
        ct.compile_snapshot(snapshot, "draft")


def test_pre_upgrade_frozen_contexts_are_byte_identical():
    # Captured using the actual pre-upgrade implementation, not current output.
    cases = json.loads((ROOT / "tests/fixtures/knowledge-before-generative-v3.json").read_text())
    for case in cases.values():
        for stage, expected in case["context_sha256"].items():
            assert ct.digest(ct.compile_snapshot(case["snapshot"], stage)) == expected


def test_upgrade_retains_original_versions_and_public_taxonomy(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    originals = [(ROOT / "resources/knowledge-imports/romance-five-tags-before-20261106" / f"{i}.md").read_text() for i in IDS[1:]]
    store.import_documents(originals, expected_revisions={i: store.get(i)["revision"] for i in IDS[1:]})
    old_snapshot = selection(store)
    before = deepcopy(store.records())
    result = store.import_documents([p.read_text() for p in FILES], expected_revisions={i: before[i]["revision"] for i in IDS})
    assert len(result["items"]) == 5
    for identifier, record in before.items():
        current = store.get(identifier)
        if identifier not in IDS:
            assert current == record
        else:
            assert current["versions"][:len(record["versions"])] == record["versions"]
    new_snapshot = selection(store)
    assert new_snapshot["publication_taxonomy"] == old_snapshot["publication_taxonomy"]
    assert all(d.get("format") == "tag-prompt.v1" for d in ct.compile_snapshot(old_snapshot, "draft")["documents"][2:])
    assert all("roles" in d for d in ct.compile_snapshot(new_snapshot, "draft")["documents"][1:])


def test_modern_workshop_submits_five_methods_once_in_one_request(tmp_path, monkeypatch):
    from api import workshop
    from api.workshop_design import new_design
    from core.llm_client import LLMClient
    from test_workshop_compact_discussion import ready_session
    from test_workshop_api import model_result
    from test_workshop_contract_repair import stub_model

    session = ready_session()
    snapshot = selection(ct.CreativeTypeStore(tmp_path))
    session["creative_design"] = new_design(session["input"], snapshot)
    calls = stub_model(monkeypatch, lambda *_: json.dumps(model_result()[0]))
    monkeypatch.setattr(LLMClient, "__init__", lambda self, **kw: None)
    workshop._complete(session, workshop._skill_bundle()[0])
    assert len(calls) == 1
    system, user = calls[0]
    context = json.loads(user)["creative_design"]["genre_context"]
    assert context == ct.compile_snapshot(snapshot, "brainstorm")
    assert user.count("generative-guidance.v2") == 1
    assert "前 10 章强制节拍" not in system + user
