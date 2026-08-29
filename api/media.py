"""Media storage content-addressed image blobs.

Backs Codex portraits, the research moodboard, and inline manuscript images
(PLAN.md P0.3). Dev writes to the local filesystem; production swaps in an
S3-compatible store behind the same `MediaStore` interface.

Two properties matter:

* **Content-addressed.** A blob's path is the SHA-256 of its bytes, so
  re-uploading the same image de-duplicates for free and served URLs are
  immutably cacheable.
* **The user's filename never touches the path.** It is metadata only. That
  removes path traversal as a category of bug rather than filtering for it.

Blobs are namespaced per project so a tenant boundary can be drawn around a
directory later (P0.5).
"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from core.image_binary import dimensions as image_dimensions

# SVG is deliberately absent: it can carry script and would execute if served
# inline. If vector art is needed later it must be sanitised first.
ALLOWED_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}

MAX_BYTES = 10 * 1024 * 1024  # 10 MiB

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


class MediaError(ValueError):
    """Upload rejected. Carries an HTTP status for the route layer."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_filename(name: str) -> str:
    """Keep a readable label for the UI. Never used to build a path."""
    base = Path(name or "").name
    cleaned = _SAFE_NAME.sub("_", base).strip() or "image"
    return cleaned[:120]


def validate(data: bytes, content_type: str) -> str:
    """Check size and type. Returns the extension for the stored blob."""
    ext = ALLOWED_TYPES.get((content_type or "").split(";")[0].strip().lower())
    if ext is None:
        raise MediaError(
            f"Unsupported image type {content_type!r}. "
            f"Allowed: {', '.join(sorted(ALLOWED_TYPES))}",
            status=415,
        )
    if not data:
        raise MediaError("Empty upload.")
    if len(data) > MAX_BYTES:
        raise MediaError(f"Image exceeds the {MAX_BYTES // (1024 * 1024)} MiB limit.", status=413)
    return ext


# --------------------------------------------------------------------- dimensions

def dimensions(data: bytes) -> tuple[int, int]:
    """Best-effort intrinsic size, parsed from the file header.

    Used so the editor can reserve space and avoid layout shift. Reading a few
    header bytes keeps Pillow out of the dependency list; an unrecognised or
    truncated header simply yields (0, 0) and the caller lays out fluidly.
    """
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    return image_dimensions(data)


# ------------------------------------------------------------------------ stores

class MediaStore(ABC):
    """Blob persistence. Metadata lives in the database, bytes live here."""

    @abstractmethod
    def put(self, project_id: str, sha: str, ext: str, data: bytes) -> None: ...

    @abstractmethod
    def read(self, project_id: str, sha: str, ext: str) -> Optional[bytes]: ...

    @abstractmethod
    def delete(self, project_id: str, sha: str, ext: str) -> bool: ...


class LocalMediaStore(MediaStore):
    """Filesystem store: <root>/<project>/<sha[:2]>/<sha><ext>."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, project_id: str, sha: str, ext: str) -> Path:
        # Both components are validated: project ids are slugs and sha is hex,
        # so neither can escape the root.
        if not re.fullmatch(r"[A-Za-z0-9._-]+", project_id or ""):
            raise MediaError("Invalid project id.", status=404)
        if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
            raise MediaError("Invalid media digest.", status=404)
        return self.root / project_id / sha[:2] / f"{sha}{ext}"

    def put(self, project_id: str, sha: str, ext: str, data: bytes) -> None:
        path = self._path(project_id, sha, ext)
        if path.exists():
            return  # content-addressed: identical bytes, nothing to rewrite
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: write beside the target, then rename into place.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def read(self, project_id: str, sha: str, ext: str) -> Optional[bytes]:
        path = self._path(project_id, sha, ext)
        return path.read_bytes() if path.exists() else None

    def delete(self, project_id: str, sha: str, ext: str) -> bool:
        path = self._path(project_id, sha, ext)
        if not path.exists():
            return False
        path.unlink()
        return True
