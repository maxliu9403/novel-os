"""Immutable source results, including receipts saved before version browsing."""

import json
import re

from .creative_types import digest
from .reference_assets import atomic_json


class SourceVersions:
    def __init__(self, library, source_id):
        self.library = library
        self.source_id = source_id
        self.root = library.path(source_id) / "study"

    def validate(self, value):
        if (
            not isinstance(value, dict)
            or value.get("source_id") != self.source_id
            or value.get("source_sha256")
            != self.library.get(self.source_id)["source_sha256"]
            or value.get("analysis_sha256")
            != digest({k: v for k, v in value.items() if k != "analysis_sha256"})
        ):
            raise ValueError("来源分析版本校验失败。")
        return value

    def save(self, receipt):
        self.validate(receipt)
        path = self.root / "results" / f"{receipt['analysis_sha256']}.json"
        if path.exists():
            existing = self.validate(json.loads(path.read_text("utf-8")))
            if existing != receipt:
                raise ValueError("来源分析版本文件与内容身份不一致。")
        else:
            atomic_json(path, receipt)

    def all(self):
        paths = [
            self.root / "result.json",
            *self.root.glob("results/*.json"),
            *self.root.glob("history/*/result.json"),
        ]
        values = {}
        for path in paths:
            if (
                not path.is_file()
                or path.is_symlink()
                or path.resolve() != self.root.resolve() / path.relative_to(self.root)
            ):
                continue
            try:
                value = self.validate(json.loads(path.read_text("utf-8")))
            except (ValueError, OSError, KeyError):
                continue
            sha = value["analysis_sha256"]
            stamp = value.get("created_at") or path.stat().st_mtime
            values.setdefault(sha, (value, stamp))
        return values

    def get(self, sha):
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError("来源分析版本无效。")
        saved = self.all().get(sha)
        if not saved:
            raise ValueError("找不到此来源分析版本。")
        return saved[0]

    def summaries(self):
        from datetime import UTC, datetime

        rows = []
        for sha, (value, stamp) in self.all().items():
            created = (
                stamp
                if isinstance(stamp, str)
                else datetime.fromtimestamp(stamp, UTC).isoformat()
            )
            rows.append(
                {
                    "analysis_sha256": sha,
                    "created_at": created,
                    "has_plan": bool(value.get("continuation_plan")),
                }
            )
        return sorted(
            rows,
            key=lambda row: (row["created_at"], row["analysis_sha256"]),
            reverse=True,
        )
