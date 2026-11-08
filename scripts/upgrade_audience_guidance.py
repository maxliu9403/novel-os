#!/usr/bin/env python3
"""Plan or explicitly apply append-only audience guidance to an existing library.

No model calls, service restarts or automatic startup migration. Each eligible
record is updated through the store's revision-checked transaction. User-modified
originals and unpublished drafts are reported as conflicts and left intact;
unaffected records may still be updated. A partial/conflicted run exits nonzero.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.audience_guidance import (  # noqa: E402
    AudienceGuidanceError, parse_audience_guidance, replace_audience_guidance,
)
from core.creative_types import (  # noqa: E402
    RESOURCE, CreativeTypeError, CreativeTypeStore, digest, parse_document, text_sha,
)


def _seeds(resources: Path) -> dict:
    result = {}
    for path in sorted(resources.glob("*/*.md")):
        markdown = path.read_bytes().decode("utf-8")
        document = parse_document(markdown, enabled=True)
        module = parse_audience_guidance(markdown)
        if module is None:
            raise ValueError(f"Seed has no audience module: {path.name}")
        if document["id"] in result:
            raise ValueError(f"Duplicate seed ID: {document['id']}")
        result[document["id"]] = {"module": module, "sha256": text_sha(markdown)}
    if not result:
        raise ValueError("No audience seed documents found")
    return result


def _candidate(record: dict, seed: dict | None) -> tuple[dict, dict | None]:
    row = {
        "id": record["id"], "revision": record["revision"],
        "active_version": record["active_version"],
        "status": "skipped", "reason": "",
    }
    if record.get("deleted_at"):
        return {**row, "reason": "deleted"}, None
    if record["status"] != "enabled":
        return {**row, "reason": "not_enabled"}, None
    active = next((v for v in record["versions"] if v["version"] == record["active_version"]), None)
    if active is None:
        return {**row, "status": "conflict", "reason": "missing_active_version"}, None
    row["active_sha256"] = active["sha256"]
    try:
        existing = parse_audience_guidance(active["markdown"])
    except AudienceGuidanceError as exc:
        return {**row, "status": "conflict", "reason": "invalid_existing_module", "detail": str(exc)}, None
    if existing is not None:
        return {**row, "reason": "already_guided", "original_sha256": text_sha(existing.original)}, None
    if seed is None:
        return {**row, "reason": "no_matching_seed"}, None
    if record["draft"] != active["markdown"]:
        return {
            **row, "status": "conflict", "reason": "unpublished_draft",
            "draft_sha256": text_sha(record["draft"]),
        }, None
    module = seed["module"]
    row.update(seed_sha256=seed["sha256"], seed_original_sha256=text_sha(module.original))
    if module.original != active["markdown"]:
        return {**row, "status": "conflict", "reason": "original_changed"}, None
    audience = {
        "audience_channels": list(module.audience_channels),
        "guides": module.guides, "activate": True,
    }
    updated = replace_audience_guidance(active["markdown"], audience["audience_channels"], audience["guides"])
    row.update(
        status="planned", reason="append_guidance", target_sha256=text_sha(updated),
        original_sha256=text_sha(module.original), audience_channels=audience["audience_channels"],
    )
    return row, audience


def upgrade_library(store: CreativeTypeStore, *, resources: Path = RESOURCE, apply: bool = False) -> dict:
    seeds = _seeds(resources)
    records = store.records()
    rows = []
    for identifier, record in sorted(records.items()):
        row, audience = _candidate(record, seeds.get(identifier))
        if apply and audience is not None:
            operation = "audience-upgrade-" + digest([
                identifier, record["revision"], row["active_sha256"], row["target_sha256"],
            ])[:40]
            try:
                saved = store.mutate(
                    "audience", identifier, revision=record["revision"],
                    operation_id=operation, audience=audience,
                )
                row.update(
                    status="updated", new_revision=saved["revision"],
                    new_active_version=saved["active_version"],
                    saved_sha256=text_sha(saved["draft"]), operation_id=operation,
                )
            except (CreativeTypeError, OSError) as exc:
                row.update(status="conflict", reason="mutation_rejected", detail=str(exc))
        rows.append(row)
    counts = Counter(row["status"] for row in rows)
    return {
        "schema": "novel-os.audience-upgrade.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "apply" if apply else "plan",
        "complete": not counts["conflict"],
        "counts": {status: counts[status] for status in ("planned", "updated", "skipped", "conflict")},
        "items": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--workspace", default="default")
    parser.add_argument("--resources", type=Path, default=RESOURCE)
    parser.add_argument("--apply", action="store_true", help="Explicitly activate new versions for eligible records")
    args = parser.parse_args()
    try:
        if not args.workspace or Path(args.workspace).name != args.workspace or args.workspace in {".", ".."}:
            raise ValueError("Invalid workspace name")
        store = CreativeTypeStore(args.data_dir / "creative_types" / args.workspace)
        report = upgrade_library(store, resources=args.resources, apply=args.apply)
    except (ValueError, OSError) as exc:
        report = {"schema": "novel-os.audience-upgrade.v1", "complete": False, "error": str(exc)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["complete"] else 2)


if __name__ == "__main__":
    main()
