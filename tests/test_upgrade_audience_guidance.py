"""Existing author material is upgraded only by an explicit, checked append."""
import json
from pathlib import Path
import subprocess
import sys

from core import creative_types as ct
from core.audience_guidance import START, parse_audience_guidance, replace_audience_guidance
from core.knowledge_markdown import prepare_markdown
from scripts.upgrade_audience_guidance import upgrade_library

GUIDES = {"female": "把人物尊严和关系变化落在有代价的选择中。", "male": "把行动、关系和能力的变化连接到人物要承担的代价。"}


def legacy_record(store, resources, name="upgrade_sample"):
    identity = {"id": "tag." + name, "kind": "tag", "name": name}
    markdown = prepare_markdown("原始方法：保持作者逐字内容。  \r\n", identity=identity)["markdown"]
    draft = store.mutate("create", None, revision=0, operation_id="create-" + name, markdown=markdown)
    active = store.mutate("activate", draft["id"], revision=draft["revision"], operation_id="activate-" + name, markdown=markdown)
    folder = resources / "tags"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name + ".md")).write_bytes(replace_audience_guidance(markdown, ["female", "male"], GUIDES).encode())
    return active


def entry(report, identifier):
    return next(row for row in report["items"] if row["id"] == identifier)


def test_dry_run_is_read_only_apply_appends_and_repeat_is_noop(tmp_path):
    store = ct.CreativeTypeStore(tmp_path / "library")
    resources = tmp_path / "seeds"
    before = legacy_record(store, resources)
    disk = store.path.read_bytes()
    report = upgrade_library(store, resources=resources)
    planned = entry(report, before["id"])
    assert report["mode"] == "plan" and report["complete"]
    assert planned["status"] == "planned"
    assert planned["original_sha256"] == ct.text_sha(before["draft"])
    assert store.path.read_bytes() == disk

    applied = upgrade_library(store, resources=resources, apply=True)
    receipt = entry(applied, before["id"])
    after = store.get(before["id"])
    assert receipt["status"] == "updated"
    assert receipt["target_sha256"] == receipt["saved_sha256"] == ct.text_sha(after["draft"])
    assert after["versions"][:-1] == before["versions"]
    assert parse_audience_guidance(after["draft"]).original == before["draft"]
    assert after["revision"] == before["revision"] + 1
    disk = store.path.read_bytes()
    repeated = upgrade_library(store, resources=resources, apply=True)
    assert entry(repeated, before["id"])["reason"] == "already_guided"
    assert store.path.read_bytes() == disk


def test_conflicts_keep_user_originals_and_unpublished_drafts(tmp_path):
    store = ct.CreativeTypeStore(tmp_path / "library")
    resources = tmp_path / "seeds"
    draft = legacy_record(store, resources, "draft_changed")
    changed = legacy_record(store, resources, "original_changed")
    unknown = legacy_record(store, resources, "unknown_seed")
    store.mutate("draft", draft["id"], revision=draft["revision"], operation_id="user-draft-edit", markdown=draft["draft"] + "待发布的想法\n")
    store.mutate("activate", changed["id"], revision=changed["revision"], operation_id="user-active-edit", markdown=changed["draft"] + "作者发布的新方法\n")
    (resources / "tags/unknown_seed.md").unlink()
    disk = store.path.read_bytes()
    report = upgrade_library(store, resources=resources, apply=True)
    assert report["complete"] is False
    assert report["counts"]["conflict"] == 2
    assert entry(report, draft["id"])["reason"] == "unpublished_draft"
    assert entry(report, changed["id"])["reason"] == "original_changed"
    assert entry(report, unknown["id"])["reason"] == "no_matching_seed"
    assert store.path.read_bytes() == disk


def test_does_not_resurrect_or_enable_or_replace_existing_guidance(tmp_path):
    store = ct.CreativeTypeStore(tmp_path / "library")
    resources = tmp_path / "seeds"
    deleted = legacy_record(store, resources, "deleted")
    disabled = legacy_record(store, resources, "disabled")
    customized = legacy_record(store, resources, "customized")
    for action, record in (("delete", deleted), ("disable", disabled)):
        store.mutate(action, record["id"], revision=record["revision"], operation_id=action + "-record")
    store.mutate("audience", customized["id"], revision=customized["revision"], operation_id="author-channel-edit", audience={"audience_channels": ["female"], "guides": {"female": "作者已写好的频道方法。"}})
    disk = store.path.read_bytes()
    report = upgrade_library(store, resources=resources, apply=True)
    assert entry(report, deleted["id"])["reason"] == "deleted"
    assert entry(report, disabled["id"])["reason"] == "not_enabled"
    assert entry(report, customized["id"])["reason"] == "already_guided"
    assert store.path.read_bytes() == disk


def test_concurrent_edit_rejects_only_affected_transaction_and_reports_partial_result(tmp_path, monkeypatch):
    store = ct.CreativeTypeStore(tmp_path / "library")
    resources = tmp_path / "seeds"
    first = legacy_record(store, resources, "race")
    other = legacy_record(store, resources, "unaffected")
    mutate = store.mutate

    def concurrent(action, identifier, **kwargs):
        if action == "audience" and identifier == first["id"]:
            mutate("draft", identifier, revision=first["revision"], operation_id="racing-author-edit", markdown=first["draft"] + "并发保存的正文\n")
        return mutate(action, identifier, **kwargs)

    monkeypatch.setattr(store, "mutate", concurrent)
    report = upgrade_library(store, resources=resources, apply=True)
    assert report["complete"] is False
    assert entry(report, first["id"])["reason"] == "mutation_rejected"
    assert entry(report, other["id"])["status"] == "updated"
    assert store.get(first["id"])["draft"].endswith("并发保存的正文\n")
    assert parse_audience_guidance(store.get(first["id"])["draft"]) is None


def test_malformed_existing_module_is_visible_conflict_not_replaced(tmp_path):
    store = ct.CreativeTypeStore(tmp_path / "library")
    resources = tmp_path / "seeds"
    record = legacy_record(store, resources)
    store.mutate("activate", record["id"], revision=record["revision"], operation_id="broken-channel-module", markdown=record["draft"] + "\n" + START + "\n")
    disk = store.path.read_bytes()
    report = upgrade_library(store, resources=resources, apply=True)
    assert entry(report, record["id"])["reason"] == "invalid_existing_module"
    assert report["complete"] is False
    assert store.path.read_bytes() == disk


def test_cli_defaults_to_plan_and_returns_machine_readable_conflicts(tmp_path):
    data, resources = tmp_path / "data", tmp_path / "seeds"
    store = ct.CreativeTypeStore(data / "creative_types/default")
    record = legacy_record(store, resources)
    script = Path(__file__).parents[1] / "scripts/upgrade_audience_guidance.py"
    command = [sys.executable, str(script), "--data-dir", str(data), "--resources", str(resources)]
    disk = store.path.read_bytes()
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["mode"] == "plan"
    assert store.path.read_bytes() == disk
    store.mutate("draft", record["id"], revision=record["revision"], operation_id="cli-conflict-edit", markdown=record["draft"] + "新草稿\n")
    result = subprocess.run(command + ["--apply"], capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["counts"]["conflict"] == 1
