"""Append-only source design storage, separate from every historical study file."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from .creative_types import digest
from .reference_assets import atomic_json
from .source_routes import SourceRouteConflict


def now() -> str:
    return datetime.now(UTC).isoformat()


class SourceDesignStore:
    def __init__(self, source_root: Path):
        self.root = Path(source_root) / "story-design"

    def path(self, *parts: str) -> Path:
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) or p in {".", ".."} for p in parts):
            raise ValueError("设计文件身份无效。")
        path = self.root.joinpath(*parts)
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("设计文件路径异常。")
        return path

    def read(self, *parts: str, default=None):
        path = self.path(*parts)
        if not path.exists():
            return default
        return json.loads(path.read_text("utf-8"))

    def write(self, value, *parts: str):
        atomic_json(self.path(*parts), value)

    def immutable(self, value: dict, *parts: str):
        existing = self.read(*parts)
        if existing is not None and existing != value:
            raise SourceRouteConflict("不可变设计回执已存在且内容不同。")
        if existing is None:
            self.write(value, *parts)
        return value

    def put_artifact(self, kind: str, body: dict) -> dict:
        value = {**body, "sha256": digest(body)}
        return self.immutable(value, kind, value["sha256"] + ".json")

    def get_artifact(self, kind: str, sha: str) -> dict:
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise ValueError("设计版本身份无效。")
        value = self.read(kind, sha + ".json")
        if not isinstance(value, dict) or value.get("sha256") != sha or digest({k: v for k, v in value.items() if k != "sha256"}) != sha:
            raise ValueError("设计版本不存在或完整性校验失败。")
        return value

    def artifacts(self, kind: str) -> list[dict]:
        path = self.path(kind)
        if not path.exists():
            return []
        return sorted((self.get_artifact(kind, p.stem) for p in path.glob("*.json")),
                      key=lambda item: item.get("created_at", ""))

    def get_route_set(self, sha: str) -> dict:
        return self.get_artifact("route-sets", sha)

    def get_understanding(self, sha: str) -> dict:
        return self.get_artifact("understandings", sha)

    def state(self, run_id: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("设计运行 ID 无效。")
        value = self.read("runs", run_id, "state.json")
        if value is None:
            raise ValueError("找不到设计运行。")
        return value

    def save_state(self, state: dict):
        self.write(state, "runs", state["run_id"], "state.json")

