"""Immutable, owner-scoped image references shared by source import and cover work.

Never accepts a client filesystem path. MIME, dimensions and hash derive from the
fully decoded bytes. References are private inputs, not publication deliverables.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image

MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 40_000_000
MODES = {"visual_inspiration", "adaptation_reference"}


def atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_json(path: Path, value):
    atomic_bytes(
        path,
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode(),
    )


def image_info(data: bytes) -> dict:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("参考图需为 12 MiB 以内的 PNG/JPEG。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as im:
                if im.format not in ("PNG", "JPEG") or getattr(im, "n_frames", 1) != 1:
                    raise ValueError("参考图仅支持单帧 PNG/JPEG。")
                width, height = im.size
                if (
                    min(width, height) < 64
                    or max(width, height) > 16000
                    or width * height > MAX_PIXELS
                ):
                    raise ValueError(
                        "参考图尺寸需至少 64 像素、单边不超过 16000 像素且不超过 4000 万像素。"
                    )
                mime = "image/png" if im.format == "PNG" else "image/jpeg"
                im.verify()
            with Image.open(BytesIO(data)) as im:
                im.load()
    except (
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("无法完整解码参考图片。") from exc
    sha = hashlib.sha256(data).hexdigest()
    return {
        "asset_id": "reference-" + sha,
        "sha256": sha,
        "content_type": mime,
        "width": width,
        "height": height,
        "size_bytes": len(data),
    }


class ReferenceAssets:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, identifier: str, suffix: str) -> Path:
        if not re.fullmatch(r"reference-[0-9a-f]{64}", identifier):
            raise ValueError("参考图 ID 无效。")
        path = self.root / (identifier + suffix)
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("参考图路径异常。")
        return path

    def put(self, data: bytes, *, origin: str = "upload") -> dict:
        if origin not in ("upload", "source_book"):
            raise ValueError("参考图来源无效。")
        info = {**image_info(data), "origin": origin}
        path = self._path(info["asset_id"], ".image")
        metadata = self._path(info["asset_id"], ".json")
        if metadata.exists():
            return self.get(info["asset_id"])
        atomic_bytes(path, data)
        atomic_json(metadata, info)
        return info

    def get(self, identifier: str) -> dict:
        try:
            info = json.loads(self._path(identifier, ".json").read_text("utf-8"))
            path = self._path(identifier, ".image")
            if path.stat().st_size > MAX_BYTES:
                raise ValueError("参考图超出上限。")
            actual = image_info(path.read_bytes())
            if (
                any(info.get(k) != v for k, v in actual.items())
                or info["asset_id"] != identifier
            ):
                raise ValueError("参考图片校验失败，不能继续调用模型。")
            return info
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            raise ValueError("参考图不存在或记录损坏。") from None

    def path(self, identifier: str) -> Path:
        self.get(identifier)
        return self._path(identifier, ".image")

    def read(self, identifier: str) -> bytes:
        return self.path(identifier).read_bytes()

    def selection(self) -> dict | None:
        path = self.root / "selection.json"
        if path.is_symlink():
            raise ValueError("参考图选择路径异常。")
        if not path.exists():
            return None
        value = json.loads(path.read_text("utf-8"))
        if value is None:
            return None
        if value.get("mode") not in MODES:
            raise ValueError("参考图模式无效。")
        return {**value, "asset": self.get(value["asset_id"])}

    def select(
        self, identifier: str | None, mode: str = "visual_inspiration"
    ) -> dict | None:
        if mode not in MODES:
            raise ValueError("参考图模式无效。")
        self._path(
            "reference-" + "0" * 64, ".json"
        )  # Validate owned root even when clearing.
        if identifier:
            self.get(identifier)
        value = {"asset_id": identifier, "mode": mode} if identifier else None
        atomic_json(self.root / "selection.json", value)
        return self.selection()
