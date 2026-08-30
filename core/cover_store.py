"""Atomic, revision-checked persistence for cover candidate sets."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

try:
    from .cover_models import CoverCandidate, CoverSet
    from .cover_models_v2 import ArtDirectionSet
except ImportError:  # pragma: no cover - legacy top-level core imports
    from cover_models import CoverCandidate, CoverSet
    from cover_models_v2 import ArtDirectionSet


_ID = re.compile(r"^cover-[0-9a-f]{32}$")
_DIRECTION_ID = re.compile(r"^direction-[0-9a-f]{32}$")
_PROVENANCE_FIELDS = (
    "relative_path", "media_id", "sha256", "width", "height", "content_type",
    "provider", "model", "request_id", "generation_prompt", "safe_request_parameters",
)


class CoverConflict(ValueError):
    pass


@dataclass(frozen=True)
class ActiveCoverPointer:
    cover_set_id: str = ""
    revision: int = 0


class CoverStore:
    def __init__(self, project_path: str | Path) -> None:
        self.project_path = Path(project_path).resolve()
        self.root = self.project_path / "outputs" / "covers"
        self.sets_dir = self.root / "sets"
        self.directions_dir = self.root / "directions"
        self.index_path = self.root / "index.json"

    def create(self, cover_set: CoverSet) -> CoverSet:
        path = self._set_path(cover_set.cover_set_id)
        if path.exists():
            raise CoverConflict(f"Cover set '{cover_set.cover_set_id}' already exists")
        created = replace(cover_set, revision=1)
        self._write_json(path, created.to_dict())
        return created

    def load(self, cover_set_id: str) -> CoverSet:
        path = self._set_path(cover_set_id)
        if not path.is_file():
            raise FileNotFoundError(f"Cover set '{cover_set_id}' not found")
        return CoverSet.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> list[CoverSet]:
        if not self.sets_dir.is_dir():
            return []
        values = [
            CoverSet.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in self.sets_dir.glob("cover-*.json")
            if path.is_file() and not path.is_symlink()
        ]
        return sorted(values, key=lambda item: (item.created_at, item.cover_set_id))

    def create_direction(self, direction: ArtDirectionSet) -> ArtDirectionSet:
        direction_id = direction.direction_id or f"direction-{uuid.uuid4().hex}"
        if not _DIRECTION_ID.fullmatch(direction_id):
            raise ValueError("Invalid cover direction id")
        created = replace(direction, direction_id=direction_id, direction_sha256="")
        created = replace(created, direction_sha256=created.content_hash())
        path = self._direction_path(direction_id)
        if path.exists():
            raise CoverConflict(f"Cover direction '{direction_id}' already exists")
        self._write_json(path, created.to_dict())
        return created

    def load_direction(self, direction_id: str) -> ArtDirectionSet:
        path = self._direction_path(direction_id)
        if not path.is_file():
            raise FileNotFoundError(f"Cover direction '{direction_id}' not found")
        direction = ArtDirectionSet.from_dict(json.loads(path.read_text(encoding="utf-8")))
        if direction.direction_sha256 and direction.direction_sha256 != direction.content_hash():
            raise CoverConflict("Cover direction content hash is invalid")
        return direction

    def list_directions(self) -> list[ArtDirectionSet]:
        if not self.directions_dir.is_dir():
            return []
        values = [
            self.load_direction(path.stem)
            for path in self.directions_dir.glob("direction-*.json")
            if path.is_file() and not path.is_symlink()
        ]
        return sorted(values, key=lambda item: item.direction_id)

    def approve_direction(
        self,
        direction_id: str,
        *,
        expected_brief_sha256: str,
        approved_direction_sha256: str,
    ) -> ArtDirectionSet:
        current = self.load_direction(direction_id)
        if current.status == "stale":
            raise CoverConflict("Cover direction is stale")
        if current.brief_sha256 != expected_brief_sha256:
            raise CoverConflict("Cover direction brief hash changed")
        if current.direction_sha256 != approved_direction_sha256:
            raise CoverConflict("Cover direction approval hash mismatch")
        approved = replace(current, status="approved")
        self._write_json(self._direction_path(direction_id), approved.to_dict())
        return approved

    def mark_direction_stale(self, direction_id: str, brief_sha256: str) -> ArtDirectionSet:
        current = self.load_direction(direction_id)
        if current.brief_sha256 == brief_sha256 or current.status == "stale":
            return current
        stale = replace(current, status="stale")
        # The original content hash remains the immutable identity of the direction.
        self._write_json(self._direction_path(direction_id), stale.to_dict())
        return stale

    def save(self, cover_set: CoverSet, *, expected_revision: int) -> CoverSet:
        current = self.load(cover_set.cover_set_id)
        if current.revision != expected_revision:
            raise CoverConflict(
                f"Cover set revision changed: expected {expected_revision}, found {current.revision}"
            )
        self._validate_ready_provenance(current, cover_set)
        saved = replace(cover_set, revision=current.revision + 1)
        self._write_json(self._set_path(saved.cover_set_id), saved.to_dict())
        return saved

    def active(self) -> ActiveCoverPointer:
        if not self.index_path.is_file():
            return ActiveCoverPointer()
        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        return ActiveCoverPointer(
            cover_set_id=str(data.get("cover_set_id") or ""),
            revision=int(data.get("revision") or 0),
        )

    def set_active(self, cover_set_id: str, *, expected_revision: int) -> ActiveCoverPointer:
        self.load(cover_set_id)
        current = self.active()
        if current.revision != expected_revision:
            raise CoverConflict(
                "active cover revision changed: "
                f"expected {expected_revision}, found {current.revision}"
            )
        pointer = ActiveCoverPointer(cover_set_id, current.revision + 1)
        self._write_json(
            self.index_path,
            {"cover_set_id": pointer.cover_set_id, "revision": pointer.revision},
        )
        return pointer

    def _set_path(self, cover_set_id: str) -> Path:
        if not _ID.fullmatch(cover_set_id or ""):
            raise ValueError("Invalid cover set id")
        return self.sets_dir / f"{cover_set_id}.json"

    def _direction_path(self, direction_id: str) -> Path:
        if not _DIRECTION_ID.fullmatch(direction_id or ""):
            raise ValueError("Invalid cover direction id")
        return self.directions_dir / f"{direction_id}.json"

    @staticmethod
    def _validate_ready_provenance(current: CoverSet, proposed: CoverSet) -> None:
        proposed_by_id = {item.candidate_id: item for item in proposed.candidates}
        for old in current.candidates:
            if old.status not in {"ready", "selected", "rejected"}:
                continue
            new = proposed_by_id.get(old.candidate_id)
            if new is None:
                raise CoverConflict("Ready candidate provenance is immutable")
            if any(getattr(old, name) != getattr(new, name) for name in _PROVENANCE_FIELDS):
                raise CoverConflict("Ready candidate provenance is immutable")

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
