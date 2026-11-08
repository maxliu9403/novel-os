#!/usr/bin/env python3
"""Export the effective workspace library, including user overrides and history.

Run with the project's Python dependencies installed. No model calls or writes
to the live workspace; the output is suitable for source deployment handoff.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.creative_types import CreativeTypeStore
from core.system_prompts import SystemPromptStore


def export_library(data: Path, output: Path, workspace: str = "default") -> dict:
    if not workspace or Path(workspace).name != workspace or workspace in {".", ".."}:
        raise ValueError("Invalid workspace name")
    if output.exists():
        raise ValueError("Output must not exist; existing exports are never overwritten")
    knowledge = CreativeTypeStore(data / "creative_types" / workspace)
    prompt_store = SystemPromptStore(data / "system_prompts" / workspace)
    source_bytes = {p: p.read_bytes() if p.exists() else None for p in (knowledge.path, prompt_store.path)}
    records = knowledge.records()
    prompts = prompt_store.get()
    output.mkdir(parents=True, mode=0o700)
    state = output / "data"
    library_path = state / "creative_types" / workspace / "knowledge.json"
    library_path.parent.mkdir(parents=True)
    # Materialize effective defaults as well, so a future release cannot silently
    # change an item absent from the workspace's override file.
    saved = {**knowledge._load(), "records": records}
    library_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt_path = state / "system_prompts" / workspace / "title-design.json"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text(json.dumps({"revision": prompts["revision"], "versions": prompts["versions"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    for identifier, record in sorted(records.items()):
        if record.get("deleted_at"):
            continue
        active = next((v for v in record["versions"] if v["version"] == record["active_version"]), None)
        relative = None
        if record["status"] == "enabled" and active:
            relative = f"novel-knowledge/{record['kind']}s/{identifier.split('.', 1)[1].replace('_', '-')}.md"
            destination = output / "resources" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(active["markdown"], encoding="utf-8")
        rows.append({"id": identifier, "kind": record["kind"], "status": record["status"],
                     "active_version": record["active_version"], "sha256": active["sha256"] if active else None,
                     "markdown_path": relative, "versions": len(record["versions"])})
    title = output / "resources/system-prompts/title-design.md"
    title.parent.mkdir(parents=True)
    title.write_text(prompts["current"]["markdown"], encoding="utf-8")
    manifest = {"schema": "novel-os.creation-library-export.v1", "workspace": workspace,
                "items": rows, "title_prompt_version": prompts["current"]["version"],
                "files": {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in output.rglob("*") if p.is_file()}}
    if source_bytes != {p: p.read_bytes() if p.exists() else None for p in source_bytes}:
        raise ValueError("Library changed during export; discard this incomplete output and retry")
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", default="default")
    args = parser.parse_args()
    try:
        result = export_library(args.data_dir, args.output, args.workspace)
        print(json.dumps({"items": len(result["items"]), "history_versions": sum(r["versions"] for r in result["items"]),
                          "output": str(args.output)}, ensure_ascii=False))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Library export refused: {exc}\n")


if __name__ == "__main__":
    main()
