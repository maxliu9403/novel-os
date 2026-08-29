"""Project-scoped orchestration for durable cover candidate generation."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Callable, Sequence

from api import media as media_lib
from core.cover_models import (
    CoverBrief,
    CoverCandidate,
    CoverConcept,
    CoverSet,
)
from core.cover_store import CoverConflict, CoverStore
from core.delivery_package import build_delivery_package
from core.image_binary import content_type, dimensions
from core.image_client import GeneratedImage, ImageClientError, ImageGenerationClient


_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class CoverServiceError(ValueError):
    pass


class CoverService:
    def __init__(
        self,
        *,
        image_client: ImageGenerationClient | None = None,
        media_store: media_lib.MediaStore | None = None,
        media_add: Callable[..., object] | None = None,
        provider: str = "openai_compatible",
    ) -> None:
        self.image_client = image_client
        self.media_store = media_store
        self.media_add = media_add
        self.provider = provider

    def generate(
        self,
        project_id: str,
        project_path: str | Path,
        brief: CoverBrief,
        concepts: Sequence[CoverConcept],
    ) -> CoverSet:
        project = Path(project_path).resolve()
        self._require_generation_dependencies()
        self._reset_pending_projection(project)
        store = CoverStore(project)
        current = store.create(CoverSet.new(project_id, brief, concepts))
        for candidate in current.candidates:
            current = self._attempt_candidate(
                project_id, project, store, current, candidate.candidate_id
            )
        current = self._finalize(store, current)
        build_delivery_package(project, cover_set=current)
        return current

    def retry_candidate(
        self,
        project_id: str,
        project_path: str | Path,
        cover_set_id: str,
        candidate_id: str,
        *,
        expected_revision: int,
    ) -> CoverSet:
        project = Path(project_path).resolve()
        self._require_generation_dependencies()
        store = CoverStore(project)
        current = store.load(cover_set_id)
        if current.revision != expected_revision:
            raise CoverConflict("Cover set revision changed")
        candidate = self._candidate(current, candidate_id)
        if candidate.status != "failed":
            raise CoverServiceError("Only a failed cover candidate can be retried")
        current = self._attempt_candidate(project_id, project, store, current, candidate_id)
        current = self._finalize(store, current)
        build_delivery_package(project, cover_set=current)
        return current

    def reject_candidate(
        self,
        project_path: str | Path,
        cover_set_id: str,
        candidate_id: str,
        *,
        expected_revision: int,
    ) -> CoverSet:
        store = CoverStore(project_path)
        current = store.load(cover_set_id)
        if current.revision != expected_revision:
            raise CoverConflict("Cover set revision changed")
        candidate = self._candidate(current, candidate_id)
        if candidate.status == "rejected":
            return current
        if candidate.status != "ready":
            raise CoverServiceError("Only a ready cover candidate can be rejected")
        updated_candidate = replace(candidate, status="rejected")
        updated = replace(
            current,
            candidates=self._replace_candidate(current, updated_candidate),
            updated_at=self._now(),
        )
        updated = replace(updated, status=self._status(updated.candidates))
        saved = store.save(updated, expected_revision=expected_revision)
        build_delivery_package(project_path, cover_set=saved)
        return saved

    def select_candidate(
        self,
        project_path: str | Path,
        cover_set_id: str,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_active_revision: int,
        confirm_stale: bool = False,
    ) -> CoverSet:
        store = CoverStore(project_path)
        current = store.load(cover_set_id)
        if current.revision != expected_revision:
            raise CoverConflict("Cover set revision changed")
        if current.selected_candidate_id == candidate_id:
            return current
        if current.status == "stale" and not confirm_stale:
            raise CoverServiceError("The cover set is stale; explicit confirmation is required")
        selected = self._candidate(current, candidate_id)
        if selected.status != "ready":
            raise CoverServiceError("Only a ready cover candidate can be selected")

        candidates = []
        for candidate in current.candidates:
            if candidate.candidate_id == candidate_id:
                candidates.append(replace(candidate, status="selected"))
            elif candidate.status == "selected":
                candidates.append(replace(candidate, status="ready"))
            else:
                candidates.append(candidate)
        updated = replace(
            current,
            candidates=tuple(candidates),
            selected_candidate_id=candidate_id,
            status="selected",
            updated_at=self._now(),
        )
        saved = store.save(updated, expected_revision=expected_revision)
        store.set_active(cover_set_id, expected_revision=expected_active_revision)
        self._project_selected(Path(project_path).resolve(), selected)
        build_delivery_package(project_path, cover_set=saved)
        return saved

    def mark_stale(
        self,
        project_path: str | Path,
        cover_set_id: str,
        source_prompt_sha256: str,
        foundation_sha256: str,
    ) -> CoverSet:
        store = CoverStore(project_path)
        current = store.load(cover_set_id)
        updated = current.with_source_hashes(source_prompt_sha256, foundation_sha256)
        if updated == current:
            return current
        saved = store.save(updated, expected_revision=current.revision)
        build_delivery_package(project_path, cover_set=saved)
        return saved

    def _attempt_candidate(
        self,
        project_id: str,
        project: Path,
        store: CoverStore,
        current: CoverSet,
        candidate_id: str,
    ) -> CoverSet:
        candidate = self._candidate(current, candidate_id)
        concept = next(
            item for item in current.concepts if item.concept_id == candidate.concept_id
        )
        try:
            assert self.image_client is not None
            generated = self.image_client.generate(concept.generation_prompt)
            ready = self._persist_generated(
                project_id, project, current, candidate, concept, generated
            )
        except (ImageClientError, OSError, ValueError) as exc:
            ready = replace(
                candidate,
                status="failed",
                error=self._safe_error(exc),
                generation_prompt=concept.generation_prompt,
            )
        updated = replace(
            current,
            candidates=self._replace_candidate(current, ready),
            status=self._status(self._replace_candidate(current, ready)),
            updated_at=self._now(),
        )
        return store.save(updated, expected_revision=current.revision)

    def _persist_generated(
        self,
        project_id: str,
        project: Path,
        cover_set: CoverSet,
        candidate: CoverCandidate,
        concept: CoverConcept,
        generated: GeneratedImage,
    ) -> CoverCandidate:
        mime = content_type(generated.data)
        width, height = dimensions(generated.data)
        if mime != generated.content_type or (width, height) != (2048, 3072):
            raise CoverServiceError("Generated cover bytes failed 2048x3072 validation")
        extension = _EXTENSIONS.get(mime)
        if extension is None:
            raise CoverServiceError("Generated cover uses an unsupported image type")

        sha = media_lib.digest(generated.data)
        assert self.media_store is not None
        assert self.media_add is not None
        self.media_store.put(project_id, sha, extension, generated.data)
        index = next(
            number for number, item in enumerate(cover_set.candidates, start=1)
            if item.candidate_id == candidate.candidate_id
        )
        relative = Path("outputs/deliverables/covers/pending") / f"cover-{index:02d}{extension}"
        self._atomic_write(project / relative, generated.data)
        media = self.media_add(
            project_id=project_id,
            sha=sha,
            ext=extension,
            filename=f"cover-{index:02d}{extension}",
            content_type=mime,
            size=len(generated.data),
            width=width,
            height=height,
            kind="cover",
            alt=f"Cover candidate {index}: {concept.visual_strategy}",
        )
        media_id = str(getattr(media, "id", "") or "")
        if not media_id:
            raise CoverServiceError("Cover media registration returned no id")
        return replace(
            candidate,
            status="ready",
            relative_path=relative.as_posix(),
            media_id=media_id,
            sha256=sha,
            width=width,
            height=height,
            content_type=mime,
            provider=self.provider,
            model=generated.model,
            request_id=generated.request_id,
            generation_prompt=concept.generation_prompt,
            safe_request_parameters={
                "n": 1,
                "size": "2048x3072",
                "model": generated.model,
                "output_format": extension.lstrip(".").replace("jpg", "jpeg"),
            },
            error="",
        )

    def _finalize(self, store: CoverStore, current: CoverSet) -> CoverSet:
        status = self._status(current.candidates)
        if current.status == status:
            return current
        return store.save(
            replace(current, status=status, updated_at=self._now()),
            expected_revision=current.revision,
        )

    @staticmethod
    def _status(candidates: Sequence[CoverCandidate]) -> str:
        ready = sum(item.status in {"ready", "selected"} for item in candidates)
        pending = sum(item.status == "pending" for item in candidates)
        if ready >= 3:
            return "ready"
        if ready or pending:
            return "partial"
        return "failed"

    @staticmethod
    def _candidate(cover_set: CoverSet, candidate_id: str) -> CoverCandidate:
        for candidate in cover_set.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise CoverServiceError(f"Cover candidate '{candidate_id}' not found")

    @staticmethod
    def _replace_candidate(
        cover_set: CoverSet,
        replacement: CoverCandidate,
    ) -> tuple[CoverCandidate, ...]:
        return tuple(
            replacement if item.candidate_id == replacement.candidate_id else item
            for item in cover_set.candidates
        )

    def _project_selected(self, project: Path, candidate: CoverCandidate) -> None:
        source = project / candidate.relative_path
        self._atomic_write(
            project / f"outputs/deliverables/covers/selected-cover{source.suffix}",
            source.read_bytes(),
        )

    def _require_generation_dependencies(self) -> None:
        if self.image_client is None:
            raise CoverServiceError("Cover image generation client is not configured")
        if self.media_store is None or self.media_add is None:
            raise CoverServiceError("Cover media persistence is not configured")

    @staticmethod
    def _reset_pending_projection(project: Path) -> None:
        pending = project / "outputs" / "deliverables" / "covers" / "pending"
        if pending.is_symlink():
            pending.unlink()
        elif pending.exists():
            shutil.rmtree(pending)
        pending.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, ImageClientError):
            return str(exc)[:500]
        if isinstance(exc, CoverServiceError):
            return str(exc)[:500]
        return f"{type(exc).__name__}: cover candidate persistence failed"

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()
