"""Private sidecars only. Atomic checkpoints, verified immutable reports, CAS policy."""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .models import MethodPolicy, canonical, digest


class MethodConflict(ValueError):
    pass


class MethodStore:
    def __init__(self, project: Path):
        # ProjectService already enforces tenancy. Resolve only system aliases;
        # reject pre-existing symlinks under the actual project tree.
        self.project = Path(os.path.abspath(project))
        self.root = self.project / "outputs/quality/methods"

    def path(self, relative: str) -> Path:
        parts = Path(relative).parts
        if Path(relative).is_absolute() or ".." in parts:
            raise ValueError("invalid method path")
        current = self.project
        if current.is_symlink() or not current.is_dir():
            raise ValueError("method project is missing or unsafe")
        for part in ("outputs", "quality", "methods", *parts):
            current /= part
            if current.is_symlink():
                raise ValueError("method paths must not contain symlinks")
        return current

    @staticmethod
    def key(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
            raise ValueError("invalid method identifier")
        return value

    @contextmanager
    def locked(self, relative: str = ".lock"):
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def read(self, relative: str) -> dict | None:
        path = self.path(relative)
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > 8_000_000:
            raise ValueError("invalid method record")
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(wrapper, dict) or set(wrapper) != {"data", "sha256"} or digest(wrapper["data"]) != wrapper["sha256"]:
            raise ValueError("method record checksum mismatch")
        return wrapper

    def write(self, relative: str, data: dict, *, immutable=True) -> dict:
        wrapper = {"data": data, "sha256": digest(data)}
        path = self.path(relative)
        if path.exists() and immutable:
            previous = self.read(relative)
            if previous["sha256"] != wrapper["sha256"]:
                raise MethodConflict("immutable method record differs")
            return previous
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".method-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(canonical(wrapper) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return wrapper

    def policy(self) -> dict:
        found = self.read("policy.json")
        if found:
            MethodPolicy.from_dict(found["data"]["policy"])
            return found
        return {"data": {"project_schema_family": "narrative_methods", "version": 1,
                         "policy": MethodPolicy().to_dict()}, "sha256": ""}

    def set_policy(self, policy: MethodPolicy, expected_revision: str) -> dict:
        with self.locked():
            previous = self.policy()
            if previous["sha256"] != expected_revision:
                raise MethodConflict("method policy changed; refresh before saving")
            return self.write("policy.json", {"project_schema_family": "narrative_methods", "version": 1,
                                              "policy": policy.to_dict()}, immutable=False)
