"""Process-wide advisory serialization for one project's promotion authority."""

from __future__ import annotations

import errno
import os
import stat
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
        self._descriptor: Optional[int] = None

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

    def _create_safe_layout(self) -> None:
        self._check_directory(self.project_root)
        try:
            self.project_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProjectLockError(f"cannot create project root: {exc}") from exc
        current = self.project_root
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
            mode = self.path.lstat().st_mode
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
            descriptor = os.open(self.path, flags, 0o600)
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
