"""Process-wide advisory serialization for one project's promotion authority.

Security boundary: managed paths are checked for symlinks before transaction
I/O, and every Novel OS writer must cooperate through this lock. The project
tree must not be concurrently renamed or replaced by a non-cooperating process
running as the same OS user while the lock is held; such a process already has
equivalent authority over the user's project files. Pre-existing untrusted
symlinks remain fail-closed.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path
from types import TracebackType
from typing import Optional, Type

try:
    import fcntl
except ImportError as exc:  # pragma: no cover - Novel OS targets POSIX
    raise RuntimeError("project promotion locks require POSIX fcntl") from exc


class ProjectLockError(Exception):
    """The promotion lock path is unsafe or the lock cannot be acquired."""


class ProjectLock:
    """Blocking exclusive ``flock`` held at ``outputs/state/.promotion.lock``."""

    def __init__(self, project_root: os.PathLike[str] | str) -> None:
        # abspath normalizes dot segments without following the project symlink.
        self.project_root = Path(os.path.abspath(os.fspath(project_root)))
        self.state_dir = self.project_root / "outputs" / "state"
        self.path = self.state_dir / ".promotion.lock"
        self._checked_project_root = self._normalize_system_alias(self.project_root)
        self._checked_state_dir = self._checked_project_root / "outputs" / "state"
        self._checked_path = self._checked_state_dir / ".promotion.lock"
        self._descriptor: Optional[int] = None

    @staticmethod
    def _normalize_system_alias(path: Path) -> Path:
        if sys.platform != "darwin":
            return path
        for alias_text, target_text in (
            ("/var", "/private/var"),
            ("/tmp", "/private/tmp"),
        ):
            alias = Path(alias_text)
            target = Path(target_text)
            try:
                relative = path.relative_to(alias)
                mode = alias.lstat().st_mode
            except (FileNotFoundError, ValueError):
                continue
            if stat.S_ISLNK(mode) and Path(os.path.realpath(alias)) == target:
                return target / relative
        return path

    @staticmethod
    def _check_directory(path: Path) -> None:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode):
            raise ProjectLockError(f"promotion lock parent must not be a symlink: {path}")
        if not stat.S_ISDIR(mode):
            raise ProjectLockError(f"promotion lock parent must be a directory: {path}")

    @staticmethod
    def _check_existing_prefixes(path: Path) -> None:
        current = Path(path.anchor)
        for component in path.parts[1:]:
            current /= component
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                break
            if stat.S_ISLNK(mode):
                raise ProjectLockError(
                    f"promotion lock parent must not contain a symlink: {current}"
                )
            if not stat.S_ISDIR(mode):
                raise ProjectLockError(
                    f"promotion lock parent must be a directory: {current}"
                )

    def _create_safe_layout(self) -> None:
        self._check_existing_prefixes(self._checked_project_root)
        self._check_directory(self._checked_project_root)
        try:
            self._checked_project_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProjectLockError(f"cannot create project root: {exc}") from exc
        current = self._checked_project_root
        for component in ("outputs", "state"):
            self._check_directory(current)
            current = current / component
            try:
                current.mkdir(exist_ok=True)
            except FileExistsError:
                pass
            except OSError as exc:
                raise ProjectLockError(f"cannot create promotion lock parent: {exc}") from exc
            self._check_directory(current)

    def __enter__(self) -> "ProjectLock":
        if self._descriptor is not None:
            raise ProjectLockError("project lock is already held by this context")
        self._create_safe_layout()
        try:
            mode = self._checked_path.lstat().st_mode
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(mode):
                raise ProjectLockError(
                    f"promotion lock file must not be a symlink: {self.path}"
                )
            if not stat.S_ISREG(mode):
                raise ProjectLockError("promotion lock path must be a regular file")

        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._checked_path, flags, 0o600)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ProjectLockError(
                    f"promotion lock path must not be a symlink: {self.path}"
                ) from exc
            raise ProjectLockError(f"cannot open promotion lock: {exc}") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ProjectLockError("promotion lock descriptor is not a regular file")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        self._descriptor = descriptor
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> bool:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is None:
            return False
        cleanup_error: Optional[OSError] = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError as caught:
            cleanup_error = caught
        try:
            os.close(descriptor)
        except OSError as caught:
            if cleanup_error is None:
                cleanup_error = caught
        # Never replace the transaction's primary exception with cleanup noise.
        if exc_type is None and cleanup_error is not None:
            raise ProjectLockError(f"cannot release promotion lock: {cleanup_error}")
        return False


__all__ = ["ProjectLock", "ProjectLockError"]
