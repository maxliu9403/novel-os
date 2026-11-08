"""Private source-book bundles with deterministic inventory and complete text reads.

Only uploaded bytes are accepted. Source labels never become enabled Genre/Tags,
secondary summaries never become canon, and images live in the shared asset store.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import unicodedata
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from .creative_types import digest
from .reference_assets import ReferenceAssets, atomic_bytes, atomic_json

MAX_UPLOAD = 128 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_TEXT = 4 * 1024 * 1024
MAX_FILES = 5000


def safe_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or len(value) > 1000
    ):
        raise ValueError("资料路径无效。")
    name = PurePosixPath(value)
    if (
        name.is_absolute()
        or any(p in ("..", ".") for p in value.split("/"))
        or ":" in value
        or "//" in value
    ):
        raise ValueError("资料路径必须是包内相对路径。")
    return str(name)


def unpack_zip(stream) -> dict[str, bytes]:
    files, expanded, names = {}, 0, set()
    try:
        with ZipFile(stream) as archive:
            if len(archive.infolist()) > MAX_FILES:
                raise ValueError("资料包条目过多。")
            for entry in archive.infolist():
                name = safe_name(entry.filename.rstrip("/"))
                key = unicodedata.normalize("NFC", name).casefold()
                if key in names:
                    raise ValueError("资料包含重复路径。")
                names.add(key)
                mode = (entry.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode) or (
                    stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                ):
                    raise ValueError("资料包不接受符号链接或特殊文件。")
                if entry.is_dir():
                    continue
                if entry.flag_bits & 1:
                    raise ValueError("不支持加密 ZIP。")
                limit = (
                    12 * 1024 * 1024
                    if name.lower().endswith((".png", ".jpg", ".jpeg"))
                    else MAX_TEXT
                )
                if entry.file_size > limit or expanded + entry.file_size > MAX_EXPANDED:
                    raise ValueError("资料包超过解压大小限制。")
                with archive.open(entry) as f:
                    content = f.read(limit + 1)
                if len(content) > limit or len(content) != entry.file_size:
                    raise ValueError("资料条目大小异常。")
                expanded += len(content)
                files[name] = content
    except BadZipFile as exc:
        raise ValueError("不是完整有效的 ZIP 文件。") from exc
    return files


def _text(data: bytes, name: str) -> str:
    if len(data) > MAX_TEXT:
        raise ValueError(f"{name} 超过单文件 4 MiB 上限。")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError(f"{name} 必须使用 UTF-8。") from None
    if "\x00" in text:
        raise ValueError(f"{name} 含有非法空字符。")
    return text


def inspect_bundle(uploaded: Mapping[str, bytes]) -> tuple[dict, dict[str, bytes]]:
    if (
        not uploaded
        or len(uploaded) > MAX_FILES
        or sum(map(len, uploaded.values())) > MAX_EXPANDED
    ):
        raise ValueError("资料包为空或超过容量限制。")
    files, seen = {}, set()
    for raw, data in uploaded.items():
        name = safe_name(raw)
        key = unicodedata.normalize("NFC", name).casefold()
        if key in seen:
            raise ValueError("资料包含重复路径。")
        seen.add(key)
        files[name] = data
    if any(
        "/".join(name.split("/")[:i]) in files
        for name in files
        for i in range(1, len(name.split("/")))
    ):
        raise ValueError("文件和目录路径冲突。")
    roots = [
        n[: -len("metadata.json")]
        for n in files
        if n == "metadata.json" or n.endswith("/metadata.json")
    ]
    if len(roots) != 1:
        raise ValueError("每次请导入一本小说，且包含唯一 metadata.json。")
    prefix = roots[0]
    if any(not name.startswith(prefix) for name in files):
        raise ValueError("资料必须位于同一书籍目录中。")
    files = {name[len(prefix) :]: data for name, data in files.items()}
    for required in ("metadata.json", "synopsis.md", "chapters.json"):
        if required not in files:
            raise ValueError(f"缺少 {required}。")
    try:
        metadata = json.loads(_text(files["metadata.json"], "metadata.json"))
        chapters = json.loads(_text(files["chapters.json"], "chapters.json"))
    except json.JSONDecodeError:
        raise ValueError("metadata.json 或 chapters.json 格式无效。") from None
    if not isinstance(metadata, dict) or not isinstance(chapters, list) or not chapters:
        raise ValueError("章节清单必须是非空数组。")
    title = metadata.get("novel_name") or metadata.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 500:
        raise ValueError("metadata 缺少合法书名。")
    paths, indices, units = set(), set(), []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise ValueError("章节清单条目无效。")
        path = safe_name(chapter.get("path"))
        index = chapter.get("index")
        if (
            type(index) is not int
            or index < 1
            or index in indices
            or path in paths
            or not path.startswith("chapters/")
            or not path.endswith(".md")
        ):
            raise ValueError("章节序号、路径重复或无效。")
        if path not in files:
            raise ValueError(f"章节文件缺失：{path}")
        if not _text(files[path], path).strip():
            raise ValueError(f"章节为空：{path}")
        paths.add(path)
        indices.add(index)
        units.append(
            {
                "path": path,
                "index": index,
                "title": str(chapter.get("title") or path),
                "scope": "listed_chapter",
            }
        )
    if [u["index"] for u in units] != sorted(indices):
        raise ValueError("chapters.json 需按 index 升序列出章节。")
    if isinstance(metadata.get("chapters"), list):
        if [
            (c.get("index"), c.get("path"))
            for c in metadata["chapters"]
            if isinstance(c, dict)
        ] != [(u["index"], u["path"]) for u in units]:
            raise ValueError("metadata 与 chapters.json 的章节清单不一致。")
    extras = sorted(
        n
        for n in files
        if n.startswith("chapters/") and n.endswith(".md") and n not in paths
    )
    for path in extras:
        _text(files[path], path)
        units.append(
            {
                "path": path,
                "index": None,
                "title": PurePosixPath(path).stem,
                "scope": "unlisted_supplement_not_ordered_chapter",
            }
        )
    synopsis = _text(files["synopsis.md"], "synopsis.md")
    if not synopsis.strip():
        raise ValueError("官方 Synopsis 不能为空。")
    cover = metadata.get("cover")
    if cover is not None and not isinstance(cover, str):
        raise ValueError("cover 必须是包内图片路径。")
    if cover:
        cover = safe_name(cover)
        if cover not in files:
            raise ValueError("metadata 指定的封面文件缺失。")
    else:
        cover = next(
            (p for p in ("cover.jpg", "cover.jpeg", "cover.png") if p in files), None
        )
    for name, data in files.items():
        limit = (
            12 * 1024 * 1024
            if name.lower().endswith((".png", ".jpg", ".jpeg"))
            else MAX_TEXT
        )
        if len(data) > limit:
            raise ValueError(f"{name} 超出单文件容量限制。")
        if name.endswith((".md", ".json", ".txt")):
            _text(data, name)
    inventory = [
        {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]
    sha = digest(inventory)
    return {
        "schema": "source-book.v1",
        "id": "source-" + sha,
        "source_sha256": sha,
        "title": title,
        "book_id": str(metadata.get("book_id") or ""),
        "metadata": metadata,
        "synopsis": synopsis,
        "units": units,
        "inventory": inventory,
        "cover_path": cover,
        "warnings": ["存在未列入清单的章节目录文件，作为未排序附加资料完整阅读。"]
        if extras
        else [],
        "scope": "provided_free_chapters_only",
        "chapter_count": len(chapters),
        "supplement_count": len(extras),
    }, files


class SourceLibrary:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, identifier: str) -> Path:
        if not re.fullmatch(r"source-[0-9a-f]{64}", identifier):
            raise ValueError("来源资料 ID 无效。")
        path = self.root / identifier
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("来源资料路径异常。")
        return path

    def import_files(self, uploaded: Mapping[str, bytes]) -> dict:
        manifest, files = inspect_bundle(uploaded)
        root = self.path(manifest["id"])
        if (root / "manifest.json").exists():
            return self.get(manifest["id"])
        if manifest["cover_path"]:
            manifest["cover_asset"] = ReferenceAssets(root / "images").put(
                files[manifest["cover_path"]], origin="source_book"
            )
        for name, data in files.items():
            target = root / "original" / name
            if any(p.is_symlink() for p in (target, *target.parents)):
                raise ValueError("来源文件路径异常。")
            atomic_bytes(target, data)
        # Manifest is the commit marker. Partial uploads never appear in listings.
        manifest["manifest_sha256"] = digest(manifest)
        atomic_json(root / "manifest.json", manifest)
        return manifest

    def get(self, identifier: str) -> dict:
        try:
            value = json.loads(
                (self.path(identifier) / "manifest.json").read_text("utf-8")
            )
        except (FileNotFoundError, json.JSONDecodeError):
            raise ValueError("找不到来源资料。") from None
        if (
            value.get("id") != identifier
            or value.get("source_sha256") != digest(value.get("inventory"))
            or identifier != "source-" + value.get("source_sha256", "")
            or value.get("manifest_sha256")
            != digest({k: v for k, v in value.items() if k != "manifest_sha256"})
        ):
            raise ValueError("来源资料身份校验失败。")
        return value

    def read_file(self, manifest: dict, name: str) -> bytes:
        item = next((i for i in manifest["inventory"] if i["path"] == name), None)
        if item is None:
            raise ValueError("文件不属于来源资料。")
        path = self.path(manifest["id"]) / "original" / safe_name(name)
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("来源文件路径异常。")
        if path.stat().st_size != item["bytes"]:
            raise ValueError("来源文件大小已变化。")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("来源文件哈希已变化。")
        return data

    def texts(self, identifier: str) -> list[dict]:
        manifest = self.get(identifier)
        result = [
            {
                "path": "synopsis.md",
                "scope": "official_synopsis",
                "text": _text(self.read_file(manifest, "synopsis.md"), "synopsis.md"),
            }
        ]
        for unit in manifest["units"]:
            result.append(
                {
                    **unit,
                    "text": _text(self.read_file(manifest, unit["path"]), unit["path"]),
                }
            )
        return result

    def list(self) -> list[dict]:
        return [
            self.get(p.parent.name)
            for p in sorted(self.root.glob("source-*/manifest.json"))
        ]
