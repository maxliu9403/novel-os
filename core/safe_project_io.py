"""Race-safe, read-only file access confined to one project directory."""
from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence


class ProjectPathError(OSError):
    """A project-relative source is unsafe or has an unsupported file type."""


@dataclass(frozen=True)
class ProjectFileSnapshot:
    """Bytes and metadata observed through the same validated descriptor."""

    data: bytes
    modified_ns: int


def _components(relative: Sequence[str]) -> tuple[str, ...]:
    parts = tuple(relative)
    if not parts:
        raise ValueError("project-relative path must not be empty")
    for part in parts:
        if (
            not isinstance(part, str)
            or not part
            or part in {".", ".."}
            or "/" in part
            or "\\" in part
            or "\x00" in part
        ):
            raise ValueError("project-relative path contains an unsafe component")
    return parts


def _flags(*, directory: bool) -> int:
    flags = os.O_RDONLY
    if directory:
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if not hasattr(os, "O_NOFOLLOW"):  # pragma: no cover - POSIX target guarantee
        raise RuntimeError("safe project reads require O_NOFOLLOW")
    return flags | os.O_NOFOLLOW


@contextmanager
def open_project_file(
    project: Path,
    relative: Sequence[str],
    *,
    missing_ok: bool = False,
) -> Iterator[int | None]:
    """Open a regular file beneath ``project`` without following any symlink.

    Every directory component is opened relative to the preceding directory
    descriptor. Replacing a checked path after this function starts therefore
    cannot redirect the eventual read outside the opened project tree.
    """
    parts = _components(relative)
    descriptors: list[int] = []
    try:
        try:
            current = os.open(os.fspath(project), _flags(directory=True))
        except OSError as exc:
            raise ProjectPathError("Project directory cannot be opened safely.") from exc
        descriptors.append(current)
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise ProjectPathError("Project path is not a directory.")

        for component in parts[:-1]:
            try:
                next_directory = os.open(
                    component,
                    _flags(directory=True),
                    dir_fd=current,
                )
            except FileNotFoundError:
                if missing_ok:
                    yield None
                    return
                raise ProjectPathError("Project source directory is missing.") from None
            except OSError as exc:
                raise ProjectPathError(
                    "Project source directory cannot be opened safely."
                ) from exc
            descriptors.append(next_directory)
            if not stat.S_ISDIR(os.fstat(next_directory).st_mode):
                raise ProjectPathError("Project source parent is not a directory.")
            current = next_directory

        try:
            descriptor = os.open(
                parts[-1],
                _flags(directory=False),
                dir_fd=current,
            )
        except FileNotFoundError:
            if missing_ok:
                yield None
                return
            raise ProjectPathError("Project source file is missing.") from None
        except OSError as exc:
            raise ProjectPathError("Project source file cannot be opened safely.") from exc
        descriptors.append(descriptor)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ProjectPathError("Project source must be a regular file.")
        yield descriptor
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def read_project_bytes(
    project: Path,
    relative: Sequence[str],
    *,
    missing_ok: bool = False,
    max_bytes: int | None = None,
) -> bytes | None:
    with open_project_file(project, relative, missing_ok=missing_ok) as descriptor:
        if descriptor is None:
            return None
        size = os.fstat(descriptor).st_size
        if max_bytes is not None and size > max_bytes:
            raise ProjectPathError("Project source exceeds its size limit.")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk_size = 1024 * 1024
            if max_bytes is not None:
                chunk_size = min(chunk_size, max_bytes - total + 1)
                if chunk_size <= 0:
                    raise ProjectPathError("Project source exceeds its size limit.")
            try:
                chunk = os.read(descriptor, chunk_size)
            except OSError as exc:
                raise ProjectPathError("Project source cannot be read.") from exc
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ProjectPathError("Project source exceeds its size limit.")
        return b"".join(chunks)


def read_project_text(
    project: Path,
    relative: Sequence[str],
    *,
    missing_ok: bool = False,
    max_bytes: int | None = None,
) -> str | None:
    data = read_project_bytes(
        project,
        relative,
        missing_ok=missing_ok,
        max_bytes=max_bytes,
    )
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectPathError("Project source is not valid UTF-8.") from exc


def read_project_snapshot(
    project: Path,
    relative: Sequence[str],
    *,
    missing_ok: bool = False,
) -> ProjectFileSnapshot | None:
    """Read a stable snapshot; changing files get at most three local reads.

    Each attempt uses the shared safe opener. No path-based metadata lookup
    can redirect a read, and bytes from an in-progress write are not cached.
    """
    for _attempt in range(3):
        with open_project_file(project, relative, missing_ok=missing_ok) as descriptor:
            if descriptor is None:
                return None
            before = os.fstat(descriptor)
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            after = os.fstat(descriptor)
            data = b"".join(chunks)
            stamp_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            stamp_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            if stamp_before == stamp_after and len(data) == after.st_size:
                return ProjectFileSnapshot(data=data, modified_ns=after.st_mtime_ns)
    raise ProjectPathError("Project source changed repeatedly while being read.")


def project_file_stamp(
    project: Path,
    relative: Sequence[str],
) -> tuple[int, int, int, int, int] | None:
    """Return an fd-derived cache stamp, or ``None`` when safely absent."""
    with open_project_file(project, relative, missing_ok=True) as descriptor:
        if descriptor is None:
            return None
        value = os.fstat(descriptor)
        return (
            value.st_mode,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
