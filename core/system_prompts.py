"""Versioned workspace instructions for built-in capabilities, independent of taxonomy."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

PROMPT_ID = "title-design"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "resources/system-prompts/title-design.md"
MAX_BYTES = 12_000


class SystemPromptError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _version(markdown: str, number: int, created_at: str | None) -> dict:
    return {"version": number, "markdown": markdown, "created_at": created_at,
            "sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest()}


class SystemPromptStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / f"{PROMPT_ID}.json"

    def _read(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"revision": 0, "versions": [_version(DEFAULT_PATH.read_text(encoding="utf-8"), 1, None)]}

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / f"{PROMPT_ID}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _write(self, record: dict):
        fd, name = tempfile.mkstemp(prefix=".prompt-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def get(self) -> dict:
        record = self._read()
        return {"id": PROMPT_ID, "revision": record["revision"],
                "current": record["versions"][-1], "versions": record["versions"],
                "default_markdown": DEFAULT_PATH.read_text(encoding="utf-8"), "max_bytes": MAX_BYTES}

    def snapshot(self) -> dict:
        # Copies bytes as well as identity: editing the library cannot change an in-flight call.
        # Freeze the first used default as well; a later application upgrade must not
        # silently replace a workspace's version 1 under the same version number.
        with self._locked():
            record = self._read()
            if not self.path.exists():
                self._write(record)
            return {"id": PROMPT_ID, **record["versions"][-1]}

    def save(self, markdown: str, revision: int, operation_id: str) -> dict:
        if not markdown.strip():
            raise SystemPromptError("请填写书名设计提示词。")
        if len(markdown.encode("utf-8")) > MAX_BYTES:
            raise SystemPromptError(f"提示词不能超过 {MAX_BYTES:,} 字节，请精简重复说明。")
        with self._locked():
            record = self._read()
            # A lost response may be retried safely, without activating another version.
            for item in record["versions"]:
                if item.get("operation_id") == operation_id:
                    if item["markdown"] != markdown or item["base_revision"] != revision:
                        raise SystemPromptError("保存请求标识已被使用，请刷新后重试。", 409)
                    return self.get()
            if record["revision"] != revision:
                raise SystemPromptError("提示词已在其他页面更新，请重新加载后合并修改。", 409)
            if record["versions"][-1]["markdown"] == markdown:
                return self.get()
            record["revision"] += 1
            record["versions"].append({
                **_version(markdown, record["versions"][-1]["version"] + 1, datetime.now(timezone.utc).isoformat()),
                "operation_id": operation_id, "base_revision": revision,
            })
            self._write(record)
            return self.get()
