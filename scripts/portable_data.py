#!/usr/bin/env python3
"""Verified, private checkpoint transfer. Standard library only; never starts jobs.

The source must stay unchanged for the complete snapshot window. SQLite uses
the backup API (including committed WAL data), not a raw database-file copy.
On a busy source the export fails rather than publishing a mixed checkpoint.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile


SCHEMA = "novel-os.portable-data.v1"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hash_stream(stream)


def hash_stream(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def fingerprint(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError(f"Unsupported entry (links are not copied): {path}")
        result[path.relative_to(root).as_posix()] = (
            info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino
        )
    return result


def sqlite_file(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(16) == b"SQLite format 3\x00"


def backup_database(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError(f"SQLite integrity check failed: {source.name}")


def copy_tree(source: Path, target: Path) -> None:
    # APFS clone copies save gigabytes without extending the live-source window.
    # Other platforms use regular copies; both paths require the same stability check.
    if sys.platform == "darwin":
        result = subprocess.run(["cp", "-cR", str(source) + "/.", str(target)],
                                capture_output=True)
        if result.returncode == 0:
            return
        shutil.rmtree(target)
        target.mkdir()
    shutil.copytree(source, target, dirs_exist_ok=True)


def snapshot(source: Path, archive: Path) -> dict:
    source, archive = source.resolve(), archive.absolute()
    if not source.is_dir() or archive.exists() or archive.is_symlink():
        raise ValueError("Source must be a directory; archive must not exist")
    if source == archive.parent.resolve() or source in archive.parent.resolve().parents:
        raise ValueError("Archive must be outside its source tree")
    archive.parent.mkdir(parents=True, exist_ok=True)
    before = fingerprint(source)
    files = [name for name, row in before.items() if stat.S_ISREG(row[0])]
    databases = {name for name in files if sqlite_file(source / name)}
    sidecars = {name + suffix for name in databases for suffix in ("-wal", "-shm", "-journal")}
    with tempfile.TemporaryDirectory(prefix=".transfer-", dir=archive.parent) as temporary:
        stage = Path(temporary)
        content = stage / "data"
        content.mkdir()
        copy_tree(source, content)
        fingerprint(content)  # Refuse a link introduced during the copy, too.
        for name in sidecars:
            (content / name).unlink(missing_ok=True)
        for name in databases:
            (content / name).unlink()
            backup_database(source / name, content / name)
        if before != fingerprint(source):
            raise ValueError("Source changed during snapshot; no archive published. Retry at a quiet checkpoint.")
        captured_at = datetime.now(timezone.utc).isoformat()
        entries = {}
        for name, row in before.items():
            target = content / name
            if stat.S_ISDIR(row[0]):
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            if name in sidecars:
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.chmod(0o700 if row[0] & 0o111 else 0o600)
            entries[name] = {"size": target.stat().st_size, "sha256": sha256(target),
                             "executable": bool(row[0] & 0o111)}
        manifest = {"schema": SCHEMA, "created_at": captured_at,
                    "files": entries, "sqlite_backups": sorted(databases),
                    "directories": [n for n, r in before.items() if stat.S_ISDIR(r[0])],
                    "scope": "saved checkpoints only; in-flight model requests are not transferable"}
        packed = stage / "payload.tar.gz"
        with tarfile.open(packed, "w:gz", compresslevel=1) as tar:
            body = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mode = len(body), 0o600
            tar.addfile(info, io.BytesIO(body))
            for name in entries:
                tar.add(content / name, arcname="data/" + name, recursive=False)
        packed.chmod(0o600)
        # Exclusive publication: never replace an existing successful export.
        os.link(packed, archive)
    return manifest


def safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name and path.as_posix() == name


def inspect_archive(tar: tarfile.TarFile) -> tuple[dict, dict]:
    members = tar.getmembers()
    by_name = {m.name: m for m in members}
    if len(by_name) != len(members) or any(not m.isfile() for m in members):
        raise ValueError("Duplicate entries or non-regular archive members")
    header = by_name.get("manifest.json")
    if not header or header.size > 64 * 1024 * 1024:
        raise ValueError("Missing or oversized manifest")
    manifest = json.load(tar.extractfile(header))
    if manifest.get("schema") != SCHEMA or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported transfer manifest")
    names = list(manifest["files"]) + manifest.get("directories", [])
    if not all(isinstance(n, str) and safe_name(n) for n in names):
        raise ValueError("Unsafe transfer path")
    if set(by_name) != {"manifest.json", *("data/" + n for n in manifest["files"])}:
        raise ValueError("Archive and manifest membership differ")
    for name, row in manifest["files"].items():
        member = by_name["data/" + name]
        if member.size != row["size"] or hash_stream(tar.extractfile(member)) != row["sha256"]:
            raise ValueError(f"Checksum mismatch: {name}")
    return manifest, by_name


def verify(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        return inspect_archive(tar)[0]


def restore(archive: Path, destination: Path) -> dict:
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination must not exist; existing installations are never overwritten")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        manifest, members = inspect_archive(tar)  # Verify everything before writing payloads.
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=destination.parent) as temporary:
            stage = Path(temporary) / "data"
            stage.mkdir(mode=0o700)
            for name in manifest.get("directories", []):
                (stage / name).mkdir(parents=True, exist_ok=True, mode=0o700)
            for name, row in manifest["files"].items():
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with target.open("xb") as stream:
                    shutil.copyfileobj(tar.extractfile(members["data/" + name]), stream)
                target.chmod(0o700 if row.get("executable") else 0o600)
                if sha256(target) != row["sha256"]:
                    raise ValueError(f"Restore checksum mismatch: {name}")
            # Reserve destination exclusively, then install the verified staged tree.
            destination.mkdir(mode=0o700)
            try:
                os.replace(stage, destination)
            except BaseException:
                destination.rmdir()
                raise
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("snapshot")
    export.add_argument("source", type=Path)
    export.add_argument("archive", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("archive", type=Path)
    unpack = commands.add_parser("restore")
    unpack.add_argument("archive", type=Path)
    unpack.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            result = snapshot(args.source, args.archive)
        elif args.command == "verify":
            result = verify(args.archive)
        else:
            result = restore(args.archive, args.destination)
        print(json.dumps({"command": args.command, "files": len(result["files"]),
                          "bytes": sum(r["size"] for r in result["files"].values()),
                          "created_at": result["created_at"]}, ensure_ascii=False))
    except (ValueError, OSError, tarfile.TarError, sqlite3.Error, KeyError, TypeError) as exc:
        parser.exit(1, f"Transfer refused: {exc}\n")


if __name__ == "__main__":
    main()
