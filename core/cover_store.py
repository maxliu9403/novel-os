"""Atomic, revision-checked persistence for cover candidate sets."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
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
        self.design_dir = self.root / "design"
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

    def create_direction(
        self,
        direction: ArtDirectionSet,
        *,
        brief: dict | None = None,
    ) -> ArtDirectionSet:
        direction_id = direction.direction_id or f"direction-{uuid.uuid4().hex}"
        if not _DIRECTION_ID.fullmatch(direction_id):
            raise ValueError("Invalid cover direction id")
        created = replace(
            direction,
            direction_id=direction_id,
            direction_sha256="",
            status="awaiting_approval",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        created = replace(created, direction_sha256=created.content_hash())
        path = self._direction_path(direction_id)
        if path.exists():
            raise CoverConflict(f"Cover direction '{direction_id}' already exists")
        self._write_json(path, created.to_dict())
        if brief is not None:
            self._write_json(self._direction_brief_path(direction_id), dict(brief))
        if created.evidence_ledger is not None:
            self._write_json(
                self.design_dir / f"{direction_id}.evidence.json",
                created.evidence_ledger.to_dict(),
            )
            self._write_json(
                self.design_dir / "visual-evidence-ledger.json",
                created.evidence_ledger.to_dict(),
            )
        if created.visual_identity is not None:
            self._write_json(
                self.design_dir / f"{direction_id}.identity.json",
                created.visual_identity.to_dict(),
            )
            self._write_json(
                self.design_dir / "book-visual-identity.json",
                created.visual_identity.to_dict(),
            )
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
            if path.is_file() and not path.is_symlink() and not path.name.endswith(".brief.json")
        ]
        return sorted(values, key=lambda item: (item.created_at, item.direction_id))

    def load_direction_brief(self, direction_id: str) -> dict:
        path = self._direction_brief_path(direction_id)
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, dict) else {}

    def require_latest_direction(self, direction_id: str) -> ArtDirectionSet:
        current = self.load_direction(direction_id)
        directions = self.list_directions()
        if not directions or directions[-1].direction_id != direction_id:
            raise CoverConflict("Only the latest cover direction can be approved or generated")
        return current

    def append_attempt(self, candidate_id: str, attempt) -> CoverSet:
        """Append an immutable attempt to the one cover set owning a candidate."""
        matches: list[CoverSet] = []
        for cover_set in self.list():
            if any(item.candidate_id == candidate_id for item in cover_set.candidates):
                matches.append(cover_set)
        if not matches:
            raise FileNotFoundError(f"Cover candidate '{candidate_id}' not found")
        if len(matches) > 1:
            raise CoverConflict(f"Cover candidate '{candidate_id}' is ambiguous")
        current = matches[0]
        candidate = next(item for item in current.candidates if item.candidate_id == candidate_id)
        payload = attempt.to_dict() if hasattr(attempt, "to_dict") else dict(attempt)
        updated_candidate = replace(
            candidate,
            attempt_history=(*candidate.attempt_history, payload),
        )
        updated = replace(
            current,
            candidates=tuple(
                updated_candidate if item.candidate_id == candidate_id else item
                for item in current.candidates
            ),
        )
        return self.save(updated, expected_revision=current.revision)

    def approve_direction(
        self,
        direction_id: str,
        *,
        expected_brief_sha256: str,
        approved_direction_sha256: str,
    ) -> ArtDirectionSet:
        current = self.require_latest_direction(direction_id)
        if current.status == "stale":
            raise CoverConflict("Cover direction is stale")
        if current.brief_sha256 != expected_brief_sha256:
            raise CoverConflict("Cover direction brief hash changed")
        if current.direction_sha256 != approved_direction_sha256:
            raise CoverConflict("Cover direction approval hash mismatch")
        approved = replace(current, status="approved")
        self._write_json(self._direction_path(direction_id), approved.to_dict())
        return approved

    def mark_direction_stale(
        self,
        direction_id: str,
        brief_sha256: str = "",
        *,
        force: bool = False,
    ) -> ArtDirectionSet:
        """Invalidate a direction after either brief or deeper source evidence changes.

        ``brief_sha256`` preserves the historical story-facts check. ``force`` is
        used by the evidence-ledger seam when final prose, publication copy, or
        another source outside the brief changed without changing that digest.
        """
        current = self.load_direction(direction_id)
        if current.status == "stale":
            return current
        if not force and current.brief_sha256 == brief_sha256:
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

    def _direction_brief_path(self, direction_id: str) -> Path:
        self._direction_path(direction_id)
        return self.directions_dir / f"{direction_id}.brief.json"

    @staticmethod
    def _validate_ready_provenance(current: CoverSet, proposed: CoverSet) -> None:
        proposed_by_id = {item.candidate_id: item for item in proposed.candidates}
        for old in current.candidates:
            if old.status not in {"ready", "selected", "rejected"}:
                continue
            new = proposed_by_id.get(old.candidate_id)
            if new is None:
                raise CoverConflict("Ready candidate provenance is immutable")
            old_history = tuple(old.attempt_history)
            new_history = tuple(new.attempt_history)
            if len(new_history) < len(old_history) or new_history[:len(old_history)] != old_history:
                raise CoverConflict("Cover generation attempt history is append-only")
            if any(getattr(old, name) != getattr(new, name) for name in _PROVENANCE_FIELDS):
                if len(new_history) <= len(old_history) or new_history[:len(old_history)] != old_history:
                    raise CoverConflict("Ready candidate provenance is immutable")
                latest = dict(new_history[-1])
                if (
                    latest.get("status") != "ready"
                    or latest.get("generation_prompt") != new.generation_prompt
                    or latest.get("image_sha256") != new.sha256
                    or latest.get("request_id") != new.request_id
                    or latest.get("model") != new.model
                    or latest.get("relative_path") != new.relative_path
                    or latest.get("media_id") != new.media_id
                    or int(latest.get("width") or 0) != new.width
                    or int(latest.get("height") or 0) != new.height
                    or latest.get("content_type") != new.content_type
                ):
                    raise CoverConflict("Ready candidate retry provenance is incomplete")

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
