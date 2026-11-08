"""Read-only presentation of a book's frozen creative inputs.

No library lookup, manuscript scan, model call or persistence. Selected proposal
cards are historical intent; the final adopted skeleton is the design authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.creative_execution import digest, validate_handoff
from core.creative_types import compile_snapshot, parse_document
from core.project_identity import ProjectIdentityError, load_project_instance_id_unlocked
from core.safe_project_io import read_project_bytes


class CreativeBasisError(ValueError):
    """The saved inputs cannot be presented as verified provenance."""


AUTHORITY_NOTE = "最终采用骨架优先。以下为历史已选设计，后续自由文本修订可能已调整它们，不代表当前确定剧情。"
EVIDENCE_NOTE = "已规划／正文体现待确认。当前没有绑定定稿版本与逐字引文的标签体现记录。"
MAX_BYTES = 8 * 1024 * 1024


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def creative_basis(project: Path, project_id: str, manifest: dict | None) -> dict:
    """Project only the selected run, never fall back to an older design run."""
    empty = {"schema": "project-creative-basis.v1", "project_id": project_id,
             "recorded": False, "run_id": None, "audience_channel": None,
             "source_sha256": None, "handoff_sha256": None, "lock_sha256": None,
             "selection_sha256": None, "binding": None, "documents": [],
             "historical_designs": [], "prose_evidence": [],
             "authority_note": AUTHORITY_NOTE, "evidence_note": EVIDENCE_NOTE}
    if manifest is None:
        return empty
    try:
        if manifest.get("spec", {}).get("creative_handoff") is None:
            if manifest.get("creative_snapshot_started") or manifest.get("creative_lock_sha256"):
                raise ValueError("Run lost its bound creative handoff")
            return empty
        return _project(project, project_id, manifest, empty)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, ProjectIdentityError) as exc:
        raise CreativeBasisError("创作依据完整性校验失败；请检查冻结设计与采用骨架，未展示未经验证的记录。") from exc


def _project(project: Path, project_id: str, manifest: dict, base: dict) -> dict:
    handoff = validate_handoff(manifest["spec"]["creative_handoff"])
    run_id = manifest["run_id"]
    source = read_project_bytes(project, ("outputs", "input", "prompt.md"), max_bytes=MAX_BYTES)
    if hashlib.sha256(source).hexdigest() != handoff["source_sha256"]:
        raise ValueError("Adopted source changed")
    expected_lock = manifest.get("creative_lock_sha256") or ""
    lock_raw = read_project_bytes(project, ("outputs", "runs", run_id, "creative-design", "lock.json"),
                                  missing_ok=True, max_bytes=MAX_BYTES)
    if lock_raw is not None:
        lock = json.loads(lock_raw)
        if (not expected_lock or digest(lock) != expected_lock or lock.get("run_id") != run_id
                or lock.get("schema") != "creative-execution.v1" or lock.get("handoff") != handoff
                or lock.get("project_instance_id") != load_project_instance_id_unlocked(project)):
            raise ValueError("Execution lock does not match this run")
    elif expected_lock or manifest.get("creative_snapshot_started"):
        raise ValueError("Execution lock missing")
    snapshot = handoff.get("type_snapshot")
    documents = []
    if snapshot is not None:
        # Reuse the declared snapshot dialect's integrity and membership checks.
        compile_snapshot(snapshot, "story_design")
        for row in snapshot["documents"]:
            doc = parse_document(row["markdown"])
            documents.append({"id": row["id"], "kind": doc["kind"], "name": doc["name"],
                              "version": row["version"], "source_sha256": row["source_sha256"]})
    names = {doc["id"]: doc["name"] for doc in documents if doc["kind"] == "tag"}
    designs = []
    for item in handoff["adopted_proposals"]:
        proposal, decision = item["proposal"], item["author_decision"]
        contributions = []
        for contribution in proposal.get("tag_contributions", []):
            tag_id = contribution["tag_id"]
            if tag_id not in names or not isinstance(contribution.get("contribution"), str):
                raise ValueError("Unbound Tag contribution")
            contributions.append({"tag_id": tag_id, "tag_name": names[tag_id],
                                  "contribution": contribution["contribution"]})
        designs.append({"id": _text(proposal.get("id")), "title": _text(proposal.get("title")),
                        "summary": _text(proposal.get("story")) or _text(proposal.get("summary")), "status": decision["status"],
                        "author_note": _text(decision.get("note")), "tag_contributions": contributions,
                        **{key: _text(proposal.get(key)) for key in ("choice", "cost", "payoff")}})
    return {**base, "recorded": True, "project_id": project_id, "run_id": run_id,
            "audience_channel": snapshot.get("audience_channel") if snapshot else None,
            "source_sha256": handoff["source_sha256"], "handoff_sha256": handoff["handoff_sha256"],
            "lock_sha256": expected_lock or None,
            "selection_sha256": snapshot["snapshot_sha256"] if snapshot else None,
            "binding": "execution_lock" if lock_raw is not None else "frozen_handoff",
            "documents": documents, "historical_designs": designs}


def creative_basis_markdown(report: dict) -> str:
    """Independent private export; never part of the public H5 package."""
    rows = ["# 创作依据", "", report["authority_note"], "", report["evidence_note"], ""]
    if not report["recorded"]:
        return "\n".join([*rows, "本作品未记录冻结创作依据。", ""])
    rows += [f"- 作品 ID：{report['project_id']}", f"- 创作任务：{report['run_id']}",
             f"- 读者频道：{ {'female': '女频', 'male': '男频'}.get(report['audience_channel'], '未记录') }",
             f"- 采用骨架 SHA-256：{report['source_sha256']}",
             f"- 创作交接 SHA-256：{report['handoff_sha256']}",
             f"- 执行锁 SHA-256：{report['lock_sha256'] or '尚未建立'}", "", "## 冻结 Genre 与 Tags", ""]
    for doc in report["documents"]:
        rows += [f"- {doc['name']}（{doc['kind']} / {doc['id']} / v{doc['version']}）",
                 f"  SHA-256：{doc['source_sha256']}"]
    if not report["documents"]:
        rows.append("未记录冻结 Genre 与 Tags。")
    rows += ["", "## 历史已选设计", ""]
    for index, design in enumerate(report["historical_designs"], 1):
        rows += [f"### {index}. {design['title'] or '未命名方案'}", "", design["summary"], ""]
        for key, label in (("choice", "人物选择"), ("cost", "代价"), ("payoff", "看点与回报"), ("author_note", "作者当时的修改")):
            if design[key]:
                rows += [f"**{label}**", "", design[key], ""]
        for contribution in design["tag_contributions"]:
            rows += [f"**{contribution['tag_name']} 的规划作用**", "", contribution["contribution"], ""]
    if not report["historical_designs"]:
        rows += ["未记录历史已选方案卡；请以采用骨架为准。", ""]
    return "\n".join(rows)
