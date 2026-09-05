"""Project-scoped orchestration for durable cover candidate generation."""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
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
from core.cover_models_v2 import (
    CoverBriefV2,
    CoverGenerationAttempt,
    CoverQualityReport,
    CoverScenePlan,
    CoreConflictVisualContract,
    QualityFinding,
)
from core.cover_handoff import refresh_cover_concept_prompt
from core.cover_store import CoverConflict, CoverStore
from core.delivery_package import build_delivery_package
from core.image_binary import aspect_ratio_matches, content_type, dimensions
from core.image_client import (
    CodexImageGenerationClient,
    GeneratedImage,
    ImageClientError,
    ImageGenerationClient,
)
from core.cover_quality import (
    UnavailableCoverVisualEvaluator,
    ThumbnailProjectionError,
    evaluate_binary_cover,
    human_review_report,
    project_cover_thumbnail,
    report_from_binary_findings,
)


_GENERATION_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg"}
_STORED_EXTENSIONS = {**_GENERATION_EXTENSIONS, "image/webp": ".webp"}
_AUTO_SEMANTIC_REPAIR_CODES = frozenset({
    "core_conflict_missing",
    "causal_relationship_missing",
    "protagonist_action_missing",
})


class CoverServiceError(ValueError):
    pass


class CoverService:
    def __init__(
        self,
        *,
        image_client: ImageGenerationClient | CodexImageGenerationClient | None = None,
        media_store: media_lib.MediaStore | None = None,
        media_add: Callable[..., object] | None = None,
        provider: str = "openai_compatible",
        visual_evaluator=None,
        thumbnail_projector: Callable[..., bytes] = project_cover_thumbnail,
    ) -> None:
        self.image_client = image_client
        self.media_store = media_store
        self.media_add = media_add
        self.provider = provider
        self.visual_evaluator = visual_evaluator or UnavailableCoverVisualEvaluator()
        self.thumbnail_projector = thumbnail_projector

    def generate(
        self,
        project_id: str,
        project_path: str | Path,
        brief: CoverBrief | CoverBriefV2,
        concepts: Sequence[CoverConcept],
        *,
        compiler_version: str = "",
    ) -> CoverSet:
        project = Path(project_path).resolve()
        self._require_generation_dependencies()
        self._reset_pending_projection(project)
        store = CoverStore(project)
        current = store.create(
            CoverSet.new(project_id, brief, concepts, compiler_version=compiler_version)
        )
        for candidate in current.candidates:
            current = self._attempt_candidate(
                project_id, project, store, current, candidate.candidate_id
            )
            reviewed = self._candidate(current, candidate.candidate_id)
            repair_codes = tuple(
                code for code in (
                    (reviewed.quality_report or {}).get("repair_codes") or ()
                )
                if code in _AUTO_SEMANTIC_REPAIR_CODES
            )
            if reviewed.status == "ready" and repair_codes:
                repaired = self._repair_concept(
                    current.brief,
                    next(
                        item for item in current.concepts
                        if item.concept_id == reviewed.concept_id
                    ),
                    reviewed,
                    repair_codes,
                )
                current = replace(
                    current,
                    concepts=tuple(
                        repaired if item.concept_id == repaired.concept_id else item
                        for item in current.concepts
                    ),
                )
                current = self._attempt_candidate(
                    project_id,
                    project,
                    store,
                    current,
                    candidate.candidate_id,
                    repair_codes=repair_codes,
                    prompt_revision=reviewed.prompt_revision + 1,
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
        repair_codes: Sequence[str] = (),
    ) -> CoverSet:
        project = Path(project_path).resolve()
        self._require_generation_dependencies()
        store = CoverStore(project)
        current = store.load(cover_set_id)
        if current.revision != expected_revision:
            raise CoverConflict("Cover set revision changed")
        candidate = self._candidate(current, candidate_id)
        if candidate.status != "failed" and not (
            candidate.status == "ready" and repair_codes
        ):
            raise CoverServiceError(
                "Retry requires a failed candidate or a ready candidate with reported repair codes"
            )
        refreshed_concepts = tuple(
            self._repair_concept(current.brief, concept, candidate, repair_codes)
            if concept.concept_id == candidate.concept_id else concept
            for concept in current.concepts
        )
        current = replace(current, concepts=refreshed_concepts)
        current = self._attempt_candidate(
            project_id,
            project,
            store,
            current,
            candidate_id,
            repair_codes=repair_codes,
            prompt_revision=candidate.prompt_revision + 1,
        )
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
        active = store.active()
        if current.selected_candidate_id == candidate_id:
            selected = self._candidate(current, candidate_id)
            if active.cover_set_id != cover_set_id:
                store.set_active(
                    cover_set_id, expected_revision=expected_active_revision
                )
            self._project_selected(Path(project_path).resolve(), current, selected)
            build_delivery_package(project_path, cover_set=current)
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
        try:
            store.set_active(cover_set_id, expected_revision=expected_active_revision)
        except CoverConflict:
            try:
                store.save(current, expected_revision=saved.revision)
            except CoverConflict as rollback_error:
                raise CoverConflict(
                    "Active cover selection conflicted and candidate rollback failed"
                ) from rollback_error
            raise
        self._project_selected(Path(project_path).resolve(), saved, selected)
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
        *,
        repair_codes: Sequence[str] = (),
        prompt_revision: int | None = None,
    ) -> CoverSet:
        candidate = self._candidate(current, candidate_id)
        concept = next(
            item for item in current.concepts if item.concept_id == candidate.concept_id
        )
        prompt_revision = prompt_revision or candidate.prompt_revision
        try:
            assert self.image_client is not None
            generated = self.image_client.generate(concept.generation_prompt)
            ready = self._persist_generated(
                project_id, project, current, candidate, concept, generated
            )
            report = self._quality_report(
                current.brief,
                concept,
                generated.data,
                ready.sha256,
                ready.content_type,
            )
            ready = replace(ready, quality_report=report.to_dict())
            attempt = CoverGenerationAttempt(
                attempt_id=f"attempt-{uuid.uuid4().hex}",
                prompt_revision=prompt_revision,
                status="ready",
                generation_prompt=concept.generation_prompt,
                repair_codes=tuple(repair_codes),
                image_sha256=ready.sha256,
                request_id=ready.request_id,
                model=ready.model,
                relative_path=ready.relative_path,
                media_id=ready.media_id,
                width=ready.width,
                height=ready.height,
                content_type=ready.content_type,
                safe_request_parameters=dict(ready.safe_request_parameters),
                quality_report=report,
                created_at=self._now(),
            )
        except (ImageClientError, OSError, ValueError) as exc:
            error = self._safe_error(exc)
            ready = candidate if candidate.status == "ready" else replace(
                candidate,
                status="failed",
                error=error,
                generation_prompt=concept.generation_prompt,
                prompt_revision=prompt_revision,
            )
            attempt = CoverGenerationAttempt(
                attempt_id=f"attempt-{uuid.uuid4().hex}",
                prompt_revision=prompt_revision,
                status="failed",
                generation_prompt=concept.generation_prompt,
                repair_codes=tuple(repair_codes),
                error=error,
                created_at=self._now(),
            )
        ready = replace(
            ready,
            attempt_history=(*candidate.attempt_history, attempt.to_dict()),
            prompt_revision=prompt_revision if attempt.status == "ready" else ready.prompt_revision,
        )
        updated = replace(
            current,
            candidates=self._replace_candidate(current, ready),
            status=self._status(self._replace_candidate(current, ready)),
            updated_at=self._now(),
        )
        return store.save(updated, expected_revision=current.revision)

    def _repair_concept(
        self,
        brief: CoverBrief | CoverBriefV2,
        concept: CoverConcept,
        candidate: CoverCandidate,
        repair_codes: Sequence[str],
    ) -> CoverConcept:
        from core.cover_design import BookVisualIdentity, VisualEvidenceLedger

        scene_payload = dict(concept.scene_plan)
        identity_raw = scene_payload.get("_visual_identity")
        ledger_raw = scene_payload.get("_evidence_ledger")
        conflict_raw = scene_payload.get("_core_conflict_visual_contract")
        visual_identity = (
            BookVisualIdentity.from_dict(identity_raw) if isinstance(identity_raw, dict) else None
        )
        evidence_ledger = (
            VisualEvidenceLedger.from_dict(ledger_raw) if isinstance(ledger_raw, dict) else None
        )
        conflict_contract = (
            CoreConflictVisualContract.from_dict(conflict_raw)
            if isinstance(conflict_raw, dict) else None
        )
        if not repair_codes:
            if isinstance(brief, CoverBriefV2) and scene_payload:
                from core.cover_prompt_compiler import scene_to_cover_concept

                scene = CoverScenePlan.from_dict(scene_payload)
                return scene_to_cover_concept(
                    brief, scene,
                    visual_identity=visual_identity,
                    evidence_ledger=evidence_ledger,
                    conflict_contract=conflict_contract,
                )
            return replace(
                concept,
                generation_prompt=refresh_cover_concept_prompt(brief, concept),
                scene_plan=scene_payload,
            )
        report = candidate.quality_report or {}
        reported = set(str(item) for item in report.get("repair_codes") or ())
        requested = tuple(dict.fromkeys(str(item).strip() for item in repair_codes if str(item).strip()))
        if not requested or not set(requested).issubset(reported):
            raise CoverServiceError("Retry repair codes must be reported by the candidate quality report")
        if not isinstance(brief, CoverBriefV2) or not concept.scene_plan:
            raise CoverServiceError("Repair-code retry requires a versioned cover direction")
        scene = CoverScenePlan.from_dict(scene_payload)
        from core.cover_prompt_compiler import compile_cover_prompt, compile_repair_prompt

        baseline = compile_cover_prompt(
            brief, scene,
            visual_identity=visual_identity,
            evidence_ledger=evidence_ledger,
            conflict_contract=conflict_contract,
        )
        prior = replace(baseline, revision=max(1, candidate.prompt_revision))
        compiled = compile_repair_prompt(
            brief, scene, prior, requested,
            visual_identity=visual_identity,
            evidence_ledger=evidence_ledger,
            conflict_contract=conflict_contract,
        )
        return replace(concept, generation_prompt=compiled.text, scene_plan=scene_payload)

    def _quality_report(
        self,
        brief: CoverBrief | CoverBriefV2,
        concept: CoverConcept,
        image: bytes,
        image_sha256: str,
        expected_content_type: str,
    ) -> CoverQualityReport:
        binary = evaluate_binary_cover(
            image,
            expected_sha256=image_sha256,
            expected_content_type=expected_content_type,
        )
        if binary:
            return report_from_binary_findings(binary)
        if not isinstance(brief, CoverBriefV2) or not concept.scene_plan:
            return human_review_report(
                "Structured scene plan is unavailable for this historical candidate"
            )
        if not self.visual_evaluator.available:
            return self.visual_evaluator.evaluate(
                image=b"",
                thumbnail=b"",
                brief=brief,
                scene=CoverScenePlan.from_dict(concept.scene_plan),
            )
        try:
            scene = CoverScenePlan.from_dict(concept.scene_plan)
            thumbnail = self.thumbnail_projector(
                image,
                width=brief.commercial_visual_goal.thumbnail_reference_width,
                height=brief.commercial_visual_goal.thumbnail_reference_height,
            )
            report = self.visual_evaluator.evaluate(
                image=image,
                thumbnail=thumbnail,
                brief=brief,
                scene=scene,
            )
        except ThumbnailProjectionError as exc:
            return report_from_binary_findings((QualityFinding(
                "thumbnail_projection_failure",
                "blocker",
                "Cover could not be decoded for mobile-thumbnail review",
                str(exc),
            ),))
        except Exception as exc:
            return human_review_report(str(exc))
        blockers = list(report.blockers)
        repair_codes = list(report.repair_codes)
        if scene.causal_visibility:
            semantic_failures = (
                (
                    "core_conflict_missing",
                    report.core_conflict_fidelity,
                    True,
                ),
                (
                    "causal_relationship_missing",
                    report.causal_relationship_clarity,
                    scene.causal_visibility == "direct",
                ),
                (
                    "protagonist_action_missing",
                    report.protagonist_agency,
                    scene.protagonist_action_visible,
                ),
            )
            for code, score, applies in semantic_failures:
                if applies and score is not None and score < 70:
                    if code not in blockers:
                        blockers.append(code)
                    if code not in repair_codes:
                        repair_codes.append(code)
        if tuple(blockers) != report.blockers or tuple(repair_codes) != report.repair_codes:
            report = replace(
                report,
                status="blocked" if blockers else report.status,
                blockers=tuple(blockers),
                repair_codes=tuple(repair_codes),
            )
        blockers = tuple(report.blockers)
        if blockers and report.status != "blocked":
            report = replace(report, status="blocked")
        required_scores = (
            report.canon_fidelity, report.required_cast_coverage,
            report.age_and_environment_fidelity, report.render_fidelity,
            report.anatomy_and_physics,
        )
        if scene.causal_visibility:
            required_scores = (*required_scores,
                report.core_conflict_fidelity,
                report.causal_relationship_clarity,
                report.protagonist_agency,
            )
        if not blockers and all(score is not None and score >= 80 for score in required_scores):
            report = replace(report, status="recommended_for_human_review")
        elif not blockers:
            report = replace(report, status="human_review_required")
        return report

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
        expected_model = str(
            getattr(getattr(self.image_client, "settings", None), "model", "")
            or "gpt-image-2"
        )
        if generated.model != expected_model or generated.model != "gpt-image-2":
            raise CoverServiceError("Generated cover model must be gpt-image-2")
        if mime != generated.content_type or not aspect_ratio_matches(width, height):
            raise CoverServiceError(
                "Generated cover bytes failed portrait 2:3 aspect-ratio validation"
            )
        extension = _GENERATION_EXTENSIONS.get(mime)
        if extension is None:
            raise CoverServiceError("Generated cover uses an unsupported image type")

        requested_size = generated.request_size
        if not requested_size and self.image_client is not None:
            requested_size = str(getattr(getattr(self.image_client, "settings", None), "size", "") or "")
        requested_size = requested_size or "2048x3072"

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
                "size": requested_size,
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

    def _project_selected(
        self,
        project: Path,
        cover_set: CoverSet,
        candidate: CoverCandidate,
    ) -> None:
        extension = _STORED_EXTENSIONS.get(candidate.content_type)
        if extension is None:
            raise CoverServiceError("Selected cover uses an unsupported image type")

        data = None
        if self.media_store is not None:
            data = self.media_store.read(
                cover_set.project_id, candidate.sha256, extension
            )
        if data is None:
            source = project / candidate.relative_path
            if source.is_file() and not source.is_symlink():
                data = source.read_bytes()
        if data is None:
            raise CoverServiceError("Selected cover media is missing")
        if (
            media_lib.digest(data) != candidate.sha256
            or content_type(data) != candidate.content_type
            or not aspect_ratio_matches(*dimensions(data))
        ):
            raise CoverServiceError(
                "Selected cover media failed portrait 2:3 provenance validation"
            )

        target = project / f"outputs/deliverables/covers/selected-cover{extension}"
        self._atomic_write(target, data)
        for old_target in target.parent.glob("selected-cover.*"):
            if old_target != target and (old_target.is_file() or old_target.is_symlink()):
                old_target.unlink()

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
