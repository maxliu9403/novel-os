"""Project-scoped orchestration for durable cover candidate generation."""

from __future__ import annotations

from core.usage_tracking import scoped_usage, cover_owner


import os
import fcntl
import tempfile
import uuid
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
from core.delivery_package import build_delivery_package, delivery_lock
from core.image_binary import aspect_ratio_matches, content_type, dimensions
from core.image_client import (
    CodexImageGenerationClient,
    GeneratedImage,
    ImageClientError,
    ImageGenerationClient,
)
from core.cover_quality import (
    UnavailableCoverVisualEvaluator,
    automatic_repair_improves,
    ThumbnailProjectionError,
    evaluate_binary_cover,
    human_review_report,
    project_cover_thumbnail,
    report_from_binary_findings,
    classify_cover_repair,
)
from core.cover_typography import canonical_digest, compose_cover, prepare_title_layout, CoverTypographyError
from core.cover_visual_similarity import compare_cover_images
from core import cover_typography_amendments as typography_amendments
from core.cover_refinement import normalize_refinement_prompt, reference_for_candidate, validate_reference, edit_prompt


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

    @staticmethod
    def _is_bound(concept: CoverConcept) -> bool:
        return bool(concept.scene_plan.get("_compiled_recipe") or concept.scene_plan.get("campaign"))

    @classmethod
    def _bind_generation(cls, concepts, brief, recipes, extra_attempts, source_binding):
        if recipes is not None and set(recipes) != {item.concept_id for item in concepts}:
            raise CoverServiceError("Approved recipes must match every concept exactly")
        bound = []
        for concept in concepts:
            scene = dict(concept.scene_plan)
            recipe = dict(recipes[concept.concept_id]) if recipes is not None else None
            if (scene.get("campaign") or source_binding) and recipe is None:
                raise CoverServiceError("New cover directions require approved compiled recipes")
            if recipe is not None:
                cls._validate_recipe(recipe)
                if recipe.get("title_render_mode") == "composed":
                    layout = recipe.get("title_layout") or {}
                    if not layout.get("font_sha256"):
                        raise CoverServiceError("Composed recipes must pin an available font before approval")
                    prepare_title_layout(layout, expected_title=brief.title)
                scene["_compiled_recipe"] = recipe
                scene["_source_binding"] = dict(source_binding or {})
                concept = replace(concept, generation_prompt=recipe["image_prompt"])
            if recipe is not None or extra_attempts:
                scene["_generation_authorization"] = {"extra_image_attempts": extra_attempts}
            bound.append(replace(concept, scene_plan=scene))
        return tuple(bound)

    @staticmethod
    def _validate_recipe(recipe: Mapping[str, Any]) -> None:
        digest = recipe.get("recipe_sha256")
        payload = {key: value for key, value in recipe.items() if key != "recipe_sha256"}
        if not digest or digest != canonical_digest(payload):
            raise CoverServiceError("Compiled recipe hash mismatch")
        if not isinstance(recipe.get("image_prompt"), str) or not recipe["image_prompt"].strip():
            raise CoverServiceError("Compiled recipe requires an image prompt")
        if recipe.get("title_render_mode") not in {"native", "composed"}:
            raise CoverServiceError("Compiled recipe requires a known title render mode")

    @staticmethod
    def _internal_root(project: Path, cover_root: str | Path | None) -> Path:
        root = Path(cover_root).resolve() if cover_root is not None else project / "outputs/covers"
        if not root.is_relative_to(project):
            raise CoverServiceError("Cover assets must remain inside their owner directory")
        return root

    @scoped_usage("cover", lambda a: a["project_path"])
    def generate(
        self,
        project_id: str,
        project_path: str | Path,
        brief: CoverBrief | CoverBriefV2,
        concepts: Sequence[CoverConcept],
        *,
        compiler_version: str = "",
        compiled_recipes: Mapping[str, Mapping[str, Any]] | None = None,
        extra_image_attempts: int = 0,
        source_binding: Mapping[str, Any] | None = None,
        cover_root: str | Path | None = None,
    ) -> CoverSet:
        project = Path(project_path).resolve()
        if isinstance(extra_image_attempts, bool) or not isinstance(extra_image_attempts, int) or extra_image_attempts < 0:
            raise CoverServiceError("Additional image budget must be a nonnegative integer")
        concepts = self._bind_generation(concepts, brief, compiled_recipes, extra_image_attempts, source_binding)
        self._require_generation_dependencies()
        store = CoverStore(project, **({"cover_root": cover_root} if cover_root is not None else {}))
        current = store.create(
            CoverSet.new(project_id, brief, concepts, compiler_version=compiler_version)
        )
        self._generation_progress("preparing", current)
        for candidate in current.candidates:
            current = self._attempt_candidate(
                project_id, project, store, current, candidate.candidate_id, cover_root=cover_root,
            )
            reviewed = self._candidate(current, candidate.candidate_id)
            repair_codes = tuple(
                code for code in (
                    (reviewed.quality_report or {}).get("repair_codes") or ()
                )
                if code in _AUTO_SEMANTIC_REPAIR_CODES
            )
            concept = next(item for item in current.concepts if item.concept_id == reviewed.concept_id)
            # Legacy semantic rerenders survive only under an explicit extra budget.
            # New bound plans return design failures to the director for a new approval.
            if reviewed.status == "ready" and repair_codes and extra_image_attempts > 0 and not self._is_bound(concept):
                original_concept = next(
                    item for item in current.concepts if item.concept_id == reviewed.concept_id
                )
                repaired = self._repair_concept(
                    current.brief,
                    original_concept,
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
                    automatic_repair_original=original_concept,
                    cover_root=cover_root,
                )
            self._generation_progress("preparing", current)
        self._generation_progress("comparing", current)
        current = self._compare_portfolio(project_id, project, store, current, cover_root=cover_root)
        self._generation_progress("saving", current)
        current = self._finalize(store, current)
        if cover_root is None:
            self._rebuild_delivery(project, current)
        return current

    @scoped_usage("cover", lambda a: a["project_path"])
    def repair_one(self, project_id, project_path, parent, candidate_id, *,
                   brief, concepts, compiler_version, compiled_recipes, source_binding,
                   extra_image_attempts=0, cover_root=None, checkpoint=None, refinement=None):
        """Create a sibling batch, rendering one approved slot and retaining its siblings.

        Parent media, approval, selection and reports are never rewritten. Retained
        slots keep their original concepts/recipes; only the repaired slot binds to
        the new single-candidate approval.
        """
        project = Path(project_path).resolve()
        self._require_generation_dependencies()
        store = CoverStore(project, cover_root=cover_root)
        original = self._candidate(parent, candidate_id)
        target = original.concept_id
        planned = next(item for item in concepts if item.concept_id == target)
        bound = self._bind_generation((planned,), brief, {target: compiled_recipes[target]},
                                      extra_image_attempts, source_binding)[0]
        if refinement is not None:
            if extra_image_attempts or not callable(getattr(self.image_client, "edit", None)):
                raise CoverServiceError("精修需要支持原图编辑的图片服务，且仅批准一次图片调用。")
            prompt = normalize_refinement_prompt(refinement.get("prompt"))
            reference = refinement.get("reference_image")
            parent_concept = next(item for item in parent.concepts if item.concept_id == target)
            if not prompt or reference != reference_for_candidate(project, original, parent_concept):
                raise CoverConflict("精修参考图与原候选不一致，请重新创建方案。")
            metadata = {"schema_version": 1, "prompt": prompt, "reference_image": deepcopy(reference),
                        "edit_prompt_sha256": media_lib.digest(edit_prompt(bound.generation_prompt).encode())}
            bound = replace(bound, scene_plan={**bound.scene_plan, "_image_refinement": metadata})
            self._refinement_reference(project, bound)
        replacement = CoverCandidate(candidate_id=f"candidate-{uuid.uuid4().hex}", concept_id=target,
            generation_prompt=bound.generation_prompt, created_at=self._now())
        from core.cover_repair_lineage import record_parent
        lineage = record_parent(store, parent, candidate_id=candidate_id, operation="image_repair")
        current = store.create(replace(parent, cover_set_id=f"cover-{uuid.uuid4().hex}", revision=0,
            repair_lineage=lineage,
            compiler_version=parent.compiler_version if parent.compiler_version == compiler_version else "mixed",
            concepts=tuple(bound if item.concept_id == target else item for item in parent.concepts),
            candidates=tuple(replacement if item.candidate_id == candidate_id else
                             replace(item, status="ready") if item.status == "selected" else item
                             for item in parent.candidates),
            selected_candidate_id="", status="generating", created_at=self._now(), updated_at=self._now()))
        if checkpoint:
            checkpoint({"phase": "generating", "cover_set_id": current.cover_set_id,
                        "result_candidate_id": replacement.candidate_id})
        current = self._attempt_candidate(project_id, project, store, current,
            replacement.candidate_id, cover_root=cover_root)
        current = self._compare_portfolio(project_id, project, store, current,
            cover_root=cover_root, only_candidate_id=replacement.candidate_id)
        current = self._finalize(store, current)
        return current

    def _refinement_reference(self, project, concept):
        metadata = concept.scene_plan.get("_image_refinement")
        binding = (concept.scene_plan.get("_source_binding") or {}).get("single_candidate_repair") or {}
        if metadata is None:
            if binding.get("reference_image_sha256") or binding.get("refinement_prompt_sha256"):
                raise CoverConflict("精修缺少已批准的原图记录，禁止降级为文字生图。")
            return None
        from core.cover_workflow import digest
        if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
            raise CoverConflict("精修来源记录无效。")
        prompt = normalize_refinement_prompt(metadata.get("prompt"))
        reference = metadata.get("reference_image") or {}
        if (not prompt or digest(prompt) != binding.get("refinement_prompt_sha256")
                or reference.get("sha256") != binding.get("reference_image_sha256")
                or media_lib.digest(edit_prompt(concept.generation_prompt).encode()) != metadata.get("edit_prompt_sha256")):
            raise CoverConflict("精修要求或原图与批准的方案不一致。")
        if not callable(getattr(self.image_client, "edit", None)):
            raise CoverServiceError("当前图片服务不支持原图编辑。")
        return validate_reference(project, reference)

    @scoped_usage("cover", lambda a: a["project_path"])
    def retry_candidate(
        self,
        project_id: str,
        project_path: str | Path,
        cover_set_id: str,
        candidate_id: str,
        *,
        expected_revision: int,
        repair_codes: Sequence[str] = (),
        cover_root: str | Path | None = None,
    ) -> CoverSet:
        project = Path(project_path).resolve()
        self._require_generation_dependencies()
        store = CoverStore(project, **({"cover_root": cover_root} if cover_root is not None else {}))
        current = store.load(cover_set_id)
        if current.revision != expected_revision:
            raise CoverConflict("Cover set revision changed")
        candidate = self._candidate(current, candidate_id)
        concept = next(item for item in current.concepts if item.concept_id == candidate.concept_id)
        if typography_amendments.validate_candidate_amendment(store.root, current, candidate, concept) is not None:
            raise CoverConflict("Typography amendment does not authorize image generation")
        if self._is_bound(concept):
            decision = (candidate.quality_report or {}).get("repair_decision") or {}
            if decision.get("kind") in {"design", "evaluation", "typography"}:
                raise CoverServiceError(f"This {decision['kind']} failure must use {decision.get('action')}; image retry is not authorized")
            if set(repair_codes) & {"core_conflict_missing", "causal_relationship_missing", "protagonist_action_missing", "reader_promise_mismatch"}:
                raise CoverServiceError("Story changes require approval of a new direction")
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
            cover_root=cover_root,
        )
        current = self._compare_portfolio(project_id, project, store, current, cover_root=cover_root)
        current = self._finalize(store, current)
        if cover_root is None:
            self._rebuild_delivery(project, current)
        return current

    def reject_candidate(
        self,
        project_path: str | Path,
        cover_set_id: str,
        candidate_id: str,
        *,
        expected_revision: int,
        cover_root: str | Path | None = None,
    ) -> CoverSet:
        store = CoverStore(project_path, **({"cover_root": cover_root} if cover_root is not None else {}))
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
        if cover_root is None:
            self._rebuild_delivery(project_path, saved)
        return saved

    @scoped_usage("cover", lambda a: a["project_path"])
    def reevaluate_candidate(
        self, project_id: str, project_path: str | Path, cover_set_id: str, candidate_id: str,
        *, expected_revision: int, cover_root: str | Path | None = None, preserve_siblings: bool = False,
    ) -> CoverSet:
        """Review immutable existing assets without invoking the image provider."""
        project = Path(project_path).resolve()
        store = CoverStore(project, **({"cover_root": cover_root} if cover_root is not None else {}))
        current = store.load(cover_set_id)
        if current.revision != expected_revision:
            raise CoverConflict("Cover set revision changed")
        if current.project_id != project_id:
            raise CoverServiceError("Cover set does not belong to this media owner")
        candidate = self._candidate(current, candidate_id)
        concept = next(item for item in current.concepts if item.concept_id == candidate.concept_id)
        amendment = typography_amendments.validate_candidate_amendment(store.root, current, candidate, concept)
        if amendment is None and ((candidate.quality_report or {}).get("repair_decision") or {}).get("kind") == "typography":
            return self.recompose_candidate(project_id, project, cover_set_id, candidate_id,
                expected_revision=expected_revision, cover_root=cover_root, preserve_siblings=preserve_siblings)
        if candidate.status not in {"ready", "selected", "rejected"}:
            raise CoverServiceError("Only an existing rendered cover can be reevaluated")
        original = candidate.quality_report or {}
        image = self._stored_asset(project, project_id, candidate.sha256, candidate.content_type, candidate.relative_path)
        assets = original.get("assets") or {}
        artwork = None
        if assets.get("artwork_sha256"):
            artwork = self._stored_asset(project, project_id, assets["artwork_sha256"], assets["artwork_content_type"], assets["artwork_relative_path"])
        if amendment is not None:
            _, artwork, review_scene = typography_amendments.amendment_inputs(store.root, amendment, concept)
            concept = replace(concept, scene_plan=review_scene)
        retained = ["assets", "recipe_sha256", "source_binding", "portfolio_comparison", "typography_amendment"]
        if amendment is None:
            retained.append("human_acceptance")
        supplemental = {name: original[name] for name in retained if name in original}
        report = self._quality_report(current.brief, concept, image, candidate.sha256, candidate.content_type,
            artwork=artwork, supplemental=supplemental,
            checkpoint=lambda evaluation: self._persist_blind_checkpoint(project, current, candidate,
                {**evaluation, "typography_amendment": original["typography_amendment"],
                 "typography_transform": amendment["transform"]} if amendment is not None else evaluation,
                image_sha256=candidate.sha256, artwork_sha256=media_lib.digest(artwork) if artwork is not None else "",
                cover_root=cover_root))
        report = self._portfolio_quality(report, supplemental.get("portfolio_comparison") or {})
        if self._is_bound(concept):
            supplemental["repair_decision"] = classify_cover_repair(report,
                evaluation_status=supplemental.get("evaluation", {}).get("evaluation_status", "unavailable"), composed=artwork is not None)
            prior_id = original.get("evaluation", {}).get("evaluation_id")
            if prior_id:
                supplemental.setdefault("evaluation", {})["previous_evaluation_id"] = prior_id
            self._persist_evaluation(project, current, candidate, report, supplemental, cover_root=cover_root)
        updated_candidate = replace(candidate, quality_report={**report.to_dict(), **supplemental})
        saved = store.save(replace(current, candidates=self._replace_candidate(current, updated_candidate),
            updated_at=self._now()), expected_revision=expected_revision)
        if cover_root is None:
            self._rebuild_delivery(project, saved)
        return saved

    @scoped_usage("cover", lambda a: a["project_path"])
    def recompose_candidate(
        self, project_id: str, project_path: str | Path, cover_set_id: str, candidate_id: str,
        *, expected_revision: int, cover_root: str | Path | None = None, preserve_siblings: bool = False,
    ) -> CoverSet:
        """Reapply only the approved local title layout to retained original artwork."""
        project = Path(project_path).resolve()
        store = CoverStore(project, **({"cover_root": cover_root} if cover_root is not None else {}))
        current = store.load(cover_set_id)
        if current.revision != expected_revision:
            raise CoverConflict("Cover set revision changed")
        if current.project_id != project_id:
            raise CoverServiceError("Cover set does not belong to this media owner")
        candidate = self._candidate(current, candidate_id)
        if candidate.status not in {"failed", "ready", "selected", "rejected"}:
            raise CoverServiceError("Only a retained artwork can be recomposed")
        concept = next(item for item in current.concepts if item.concept_id == candidate.concept_id)
        if typography_amendments.validate_candidate_amendment(store.root, current, candidate, concept) is not None:
            # Never silently restore the old recipe's title after an amendment.
            return self.reevaluate_candidate(project_id, project, cover_set_id, candidate_id,
                expected_revision=expected_revision, cover_root=cover_root)
        recipe = concept.scene_plan.get("_compiled_recipe") or {}
        self._validate_recipe(recipe)
        if recipe.get("title_render_mode") != "composed" or recipe.get("image_prompt") != concept.generation_prompt:
            raise CoverServiceError("Recomposition requires the unchanged approved composed recipe")
        original = candidate.quality_report or {}
        assets = dict(original.get("assets") or {})
        if not assets.get("artwork_sha256"):
            raise CoverServiceError("Original artwork is unavailable; local recomposition cannot proceed")
        artwork = self._stored_asset(project, project_id, assets["artwork_sha256"],
            assets["artwork_content_type"], assets["artwork_relative_path"])
        try:
            composed = compose_cover(artwork, recipe["title_layout"], expected_title=current.brief.title,
                output_format="PNG" if assets["artwork_content_type"] == "image/png" else "JPEG")
        except CoverTypographyError as exc:
            raise CoverServiceError("Approved title layout still cannot be composed. Modify the layout and approve a new direction; the original artwork is retained and no image call was made. " + str(exc)) from exc
        assets.update({"title_layout_sha256": composed.title_layout_sha256,
            "final_cover_sha256": composed.final_cover_sha256, "font_sha256": composed.font_sha256,
            "title_bounds": [list(bounds) for bounds in composed.text_bounds]})
        updated = candidate
        if candidate.sha256 != composed.final_cover_sha256:
            if candidate.status == "selected":
                raise CoverServiceError("Local recomposition would change the selected cover. Create and approve a new candidate before replacing its published image.")
            if self.media_store is None or self.media_add is None:
                raise CoverServiceError("Media storage is required for local recomposition")
            provenance = assets.get("artwork_generation") or {}
            model = str(provenance.get("model") or candidate.model)
            if not model:
                raise CoverServiceError("Original artwork generation provenance is unavailable")
            width, height = dimensions(composed.data)
            generated = GeneratedImage(data=composed.data, content_type=content_type(composed.data), width=width, height=height,
                request_id=str(provenance.get("request_id") or candidate.request_id), model=model,
                request_size=str(provenance.get("request_size") or candidate.safe_request_parameters.get("size") or ""),
                observed_model=str(provenance.get("observed_model") or ""),
                model_verified=provenance.get("model_verified") is True)
            updated = self._persist_generated(project_id, project, current, candidate, concept, generated,
                project_pending=candidate.status != "selected", cover_root=cover_root)
            if candidate.status in {"selected", "rejected"}:
                updated = replace(updated, status=candidate.status)
        supplemental = {name: original[name] for name in ("source_binding", "human_acceptance", "portfolio_comparison") if name in original}
        supplemental.update({"assets": assets, "recipe_sha256": recipe["recipe_sha256"]})
        report = self._quality_report(current.brief, concept, composed.data, composed.final_cover_sha256,
            content_type(composed.data), artwork=artwork, supplemental=supplemental,
            checkpoint=lambda evaluation: self._persist_blind_checkpoint(project, current, candidate, evaluation,
                image_sha256=composed.final_cover_sha256, artwork_sha256=media_lib.digest(artwork),
                cover_root=cover_root))
        report = self._portfolio_quality(report, supplemental.get("portfolio_comparison") or {})
        supplemental["repair_decision"] = classify_cover_repair(report,
            evaluation_status=supplemental.get("evaluation", {}).get("evaluation_status", "unavailable"), composed=True)
        prior_id = (original.get("evaluation") or {}).get("evaluation_id")
        if prior_id:
            supplemental.setdefault("evaluation", {})["previous_evaluation_id"] = prior_id
        supplemental["local_recomposition"] = {"completed_at": self._now(), "image_provider_called": False,
            "artwork_sha256": assets["artwork_sha256"], "recipe_sha256": recipe["recipe_sha256"]}
        self._persist_evaluation(project, current, candidate, report, supplemental, cover_root=cover_root)
        updated = replace(updated, quality_report={**report.to_dict(), **supplemental})
        if updated.sha256 != candidate.sha256:
            attempt = CoverGenerationAttempt(attempt_id=f"composition-{uuid.uuid4().hex}", prompt_revision=candidate.prompt_revision,
                status="ready", generation_prompt=concept.generation_prompt, image_sha256=updated.sha256,
                request_id=updated.request_id, model=updated.model, relative_path=updated.relative_path,
                media_id=updated.media_id, width=updated.width, height=updated.height, content_type=updated.content_type,
                safe_request_parameters=dict(updated.safe_request_parameters), quality_report=report, created_at=self._now()).to_dict()
            attempt.update({"operation": "typography_recomposition", "quality_report": updated.quality_report})
            updated = replace(updated, attempt_history=(*candidate.attempt_history, attempt))
        saved = store.save(replace(current, candidates=self._replace_candidate(current, updated), updated_at=self._now()), expected_revision=expected_revision)
        if updated.sha256 != candidate.sha256:
            saved = self._compare_portfolio(project_id, project, store, saved, cover_root=cover_root,
                only_candidate_id=candidate_id if preserve_siblings else None)
        saved = self._finalize(store, saved) if current.status != "selected" else saved
        if cover_root is None:
            self._rebuild_delivery(project, saved)
        return saved

    def propose_typography_amendment(
        self, project_id, project_path, cover_set_id, candidate_id, *, expected_revision,
        proposal, cover_root=None,
    ):
        project = Path(project_path).resolve()
        typography_amendments.validate_owner_root(project, cover_root or project / "outputs" / "covers")
        store = CoverStore(project, cover_root=cover_root)
        with store.mutation_lock():
            current, candidate, concept = self._typography_candidate(store, project_id, cover_set_id, candidate_id,
                expected_revision=expected_revision)
            assets = (candidate.quality_report or {}).get("assets") or {}
            if not assets.get("artwork_sha256"):
                raise CoverServiceError("Original artwork is unavailable for typography amendment")
            artwork = self._stored_asset(project, project_id, assets["artwork_sha256"],
                assets["artwork_content_type"], assets["artwork_relative_path"])
            record = typography_amendments.propose_amendment(store.root, current, candidate, concept, artwork, dict(proposal))
            return typography_amendments.amendment_summary(store.root, project, record)

    def approve_typography_amendment(
        self, project_id, project_path, cover_set_id, candidate_id, amendment_id, *,
        expected_amendment_sha256, confirm_layout, cover_root=None,
    ):
        project = Path(project_path).resolve()
        typography_amendments.validate_owner_root(project, cover_root or project / "outputs" / "covers")
        store = CoverStore(project, cover_root=cover_root)
        with store.mutation_lock():
            current, candidate, concept = self._typography_candidate(store, project_id, cover_set_id, candidate_id)
            record = typography_amendments.load_amendment(store.root, amendment_id, expected_sha256=expected_amendment_sha256)
            receipt = typography_amendments.approve_amendment(store.root, record, current, candidate, concept,
                confirm_layout=confirm_layout)
            return {**typography_amendments.amendment_summary(store.root, project, record, approved=True),
                    "approval": receipt}

    @staticmethod
    def _typography_candidate(store, project_id, cover_set_id, candidate_id, *, expected_revision=None):
        current = store.load(cover_set_id)
        if current.project_id != project_id:
            raise CoverConflict("Typography candidate does not belong to this media owner")
        if expected_revision is not None and current.revision != expected_revision:
            raise CoverConflict("Cover set revision changed")
        candidate = CoverService._candidate(current, candidate_id)
        concept = next(item for item in current.concepts if item.concept_id == candidate.concept_id)
        return current, candidate, concept

    @scoped_usage("cover", lambda a: a["project_path"])
    def apply_typography_amendment(
        self, project_id, project_path, cover_set_id, candidate_id, amendment_id, *,
        expected_revision, expected_amendment_sha256, source_guard: Callable[[], None], cover_root=None,
    ):
        """Apply exact approved preview bytes; evaluation is outside publication locks."""
        project = Path(project_path).resolve()
        typography_amendments.validate_owner_root(project, cover_root or project / "outputs" / "covers")
        store = CoverStore(project, cover_root=cover_root)
        source_guard()
        current, candidate, concept = self._typography_candidate(store, project_id, cover_set_id, candidate_id,
            expected_revision=expected_revision)
        record = typography_amendments.load_amendment(store.root, amendment_id, expected_sha256=expected_amendment_sha256)
        typography_amendments.validate_parent(record, current, candidate, concept)
        receipt = typography_amendments.approval_receipt(store.root, record)
        original = candidate.quality_report or {}
        assets = dict(original.get("assets") or {})
        artwork = self._stored_asset(project, project_id, assets["artwork_sha256"],
            assets["artwork_content_type"], assets["artwork_relative_path"])
        if media_lib.digest(artwork) != record["source_artwork_sha256"]:
            raise CoverConflict("Original typography artwork changed")
        image, review_artwork, review_scene = typography_amendments.amendment_inputs(store.root, record, concept)
        marker = {"amendment_id": amendment_id, "amendment_sha256": record["amendment_sha256"],
                  "approval_sha256": receipt["approval_sha256"]}
        assets.update({"title_layout_sha256": record["title_layout_sha256"],
            "final_cover_sha256": record["preview_sha256"], "font_sha256": record["font_sha256"],
            "title_bounds": record["title_bounds"], "review_artwork_sha256": record["review_artwork_sha256"],
            "typography_transform": record["transform"]})
        supplemental = {"assets": assets, "recipe_sha256": record["binding"]["recipe_sha256"],
            "source_binding": record["binding"]["source_binding"], "typography_amendment": marker}
        if "portfolio_comparison" in original:
            supplemental["portfolio_comparison"] = deepcopy(original["portfolio_comparison"])
        # New evaluation cannot inherit a prior human quality acceptance.
        review_concept = replace(concept, scene_plan=review_scene)
        report = self._quality_report(current.brief, review_concept, image, record["preview_sha256"], "image/png",
            artwork=review_artwork, supplemental=supplemental,
            checkpoint=lambda evaluation: self._persist_blind_checkpoint(project, current, candidate,
                {**evaluation, "typography_amendment": marker, "typography_transform": record["transform"]},
                image_sha256=record["preview_sha256"], artwork_sha256=record["review_artwork_sha256"], cover_root=cover_root))
        report = self._portfolio_quality(report, supplemental.get("portfolio_comparison") or {})
        supplemental["repair_decision"] = classify_cover_repair(report,
            evaluation_status=supplemental.get("evaluation", {}).get("evaluation_status", "unavailable"), composed=True)
        supplemental["local_recomposition"] = {"completed_at": self._now(), "image_provider_called": False,
            "artwork_sha256": record["source_artwork_sha256"], "recipe_sha256": record["binding"]["recipe_sha256"]}
        self._persist_evaluation(project, current, candidate, report, supplemental, cover_root=cover_root)
        with delivery_lock(project) if cover_root is None else nullcontext():
            with store.mutation_lock():
                source_guard()
                latest, parent, latest_concept = self._typography_candidate(store, project_id, cover_set_id, candidate_id,
                    expected_revision=expected_revision)
                record = typography_amendments.load_amendment(store.root, amendment_id, expected_sha256=expected_amendment_sha256)
                typography_amendments.validate_parent(record, latest, parent, latest_concept)
                typography_amendments.approval_receipt(store.root, record)
                # Register immutable media only; no shared projection is overwritten before CAS.
                if self.media_store is None or self.media_add is None:
                    raise CoverServiceError("Media storage is required for typography amendment")
                width, height = dimensions(image)
                generated = GeneratedImage(data=image, content_type="image/png", width=width, height=height,
                    request_id=candidate.request_id, model=candidate.model,
                    request_size=str(candidate.safe_request_parameters.get("size") or ""),
                    observed_model=str(candidate.safe_request_parameters.get("observed_model") or ""),
                    model_verified=candidate.safe_request_parameters.get("model_verified") is True)
                updated = self._persist_generated(project_id, project, current, candidate, concept, generated,
                    project_pending=False, cover_root=cover_root)
                pending = (store.root / "pending").relative_to(project) if cover_root is not None else Path("outputs/deliverables/covers/pending")
                relative = pending / current.cover_set_id / f"{amendment_id}.png"
                updated = replace(updated, relative_path=relative.as_posix(),
                                  quality_report={**report.to_dict(), **supplemental})
                attempt = CoverGenerationAttempt(attempt_id=f"composition-{uuid.uuid4().hex}", prompt_revision=candidate.prompt_revision,
                    status="ready", generation_prompt=concept.generation_prompt, image_sha256=updated.sha256,
                    request_id=updated.request_id, model=updated.model, relative_path=updated.relative_path,
                    media_id=updated.media_id, width=width, height=height, content_type="image/png",
                    safe_request_parameters=dict(updated.safe_request_parameters), quality_report=report, created_at=self._now()).to_dict()
                attempt.update(operation="typography_recomposition", typography_amendment=marker,
                               quality_report=updated.quality_report)
                updated = replace(updated, attempt_history=(*candidate.attempt_history, attempt))
                target = project / relative
                if target.is_symlink() or not target.resolve().is_relative_to(project):
                    raise CoverConflict("Typography projection must stay in its owner")
                typography_amendments.record_application(store.root, record, updated)
                self._atomic_write(target, image)
                saved = store.save(replace(latest, candidates=self._replace_candidate(latest, updated),
                    status=self._status(self._replace_candidate(latest, updated)) if not latest.selected_candidate_id else latest.status,
                    updated_at=self._now()), expected_revision=expected_revision)
                if cover_root is None:
                    self._rebuild_delivery(project, saved)
        return saved

    def _stored_asset(self, project: Path, owner: str, sha: str, mime: str, relative: str) -> bytes:
        extension = _STORED_EXTENSIONS.get(mime)
        if not extension:
            raise CoverServiceError("Unsupported stored image format")
        data = self.media_store.read(owner, sha, extension) if self.media_store is not None else None
        if data is None:
            path = project / relative
            if not path.resolve().is_relative_to(project) or path.is_symlink() or not path.is_file():
                raise CoverServiceError("Stored cover image is unavailable")
            data = path.read_bytes()
        if media_lib.digest(data) != sha or content_type(data) != mime:
            raise CoverServiceError("Stored cover image failed provenance validation")
        return data

    def select_candidate(
        self,
        project_path: str | Path,
        cover_set_id: str,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_active_revision: int,
        confirm_stale: bool = False,
        cover_root: str | Path | None = None,
        quality_acceptance_reason: str = "",
    ) -> CoverSet:
        store = CoverStore(project_path, cover_root=cover_root)
        with delivery_lock(project_path) if cover_root is None else nullcontext():
            with store.mutation_lock():
                return self._select_candidate(
                project_path, cover_set_id, candidate_id,
                expected_revision=expected_revision,
                expected_active_revision=expected_active_revision,
                confirm_stale=confirm_stale, cover_root=cover_root,
                quality_acceptance_reason=quality_acceptance_reason,
            )

    def _select_candidate(
        self,
        project_path: str | Path,
        cover_set_id: str,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_active_revision: int,
        confirm_stale: bool,
        cover_root: str | Path | None,
        quality_acceptance_reason: str,
    ) -> CoverSet:
        store = CoverStore(project_path, **({"cover_root": cover_root} if cover_root is not None else {}))
        current = store.load(cover_set_id)
        if current.revision != expected_revision:
            raise CoverConflict("Cover set revision changed")
        from core.cover_repair_lineage import validate_lineage, resolve_candidate_origin
        validate_lineage(store, current, candidate_id=candidate_id)
        selected = self._candidate(current, candidate_id)
        concept = next(item for item in current.concepts if item.concept_id == selected.concept_id)
        origin_set, origin_candidate, origin_concept = resolve_candidate_origin(store, current, candidate_id)
        typography_amendments.validate_candidate_amendment(store.root, origin_set, origin_candidate, origin_concept)
        active = store.active()
        if current.selected_candidate_id == candidate_id:
            selected = self._candidate(current, candidate_id)
            if active.cover_set_id != cover_set_id:
                store.set_active(
                    cover_set_id, expected_revision=expected_active_revision
                )
            if cover_root is None:
                self._rebuild_selected_delivery(project_path, current)
            return current
        if current.status == "stale" and not confirm_stale:
            raise CoverServiceError("The cover set is stale; explicit confirmation is required")
        selected = self._candidate(current, candidate_id)
        if selected.status not in {"ready", "rejected"}:
            raise CoverServiceError("Only a completed cover candidate can be selected")
        if current.compiler_version.startswith("skeleton-poster") or any(
                item.scene_plan.get("_poster_recipe") for item in current.concepts):
            # A package failure must not invalidate a real user's selection, but
            # a missing/corrupt candidate is not a completed selectable image.
            image = self._stored_asset(Path(project_path).resolve(), current.project_id,
                selected.sha256, selected.content_type, selected.relative_path)
            if not aspect_ratio_matches(*dimensions(image)):
                raise CoverServiceError("Selected cover media failed portrait 2:3 provenance validation")
        concept = next(item for item in current.concepts if item.concept_id == selected.concept_id)
        # Quality review informs the user's choice; it never requires a written
        # justification. Keep optional notes from older API/CLI clients without
        # inventing an acceptance reason or changing the model's assessment.
        reason = quality_acceptance_reason.strip()
        if reason and self._is_bound(concept):
            if len(reason) > 2000:
                raise CoverServiceError("Optional cover selection note must not exceed 2000 characters")
            selected = replace(selected, quality_report={**(selected.quality_report or {}),
                "human_acceptance": {"reason": reason, "accepted_at": self._now(),
                    "evaluation_id": (selected.quality_report or {}).get("evaluation", {}).get("evaluation_id", "")}})

        candidates = []
        for candidate in current.candidates:
            if candidate.candidate_id == candidate_id:
                candidates.append(replace(selected, status="selected"))
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
        if cover_root is None:
            self._rebuild_selected_delivery(project_path, saved)
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
        self._rebuild_delivery(project_path, saved)
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
        automatic_repair_original: CoverConcept | None = None,
        cover_root: str | Path | None = None,
    ) -> CoverSet:
        candidate = self._candidate(current, candidate_id)
        concept = next(
            item for item in current.concepts if item.concept_id == candidate.concept_id
        )
        recipe = concept.scene_plan.get("_compiled_recipe")
        if recipe:
            self._validate_recipe(recipe)
            if concept.generation_prompt != recipe["image_prompt"]:
                raise CoverServiceError("Candidate prompt differs from its approved recipe")
        reference_path = self._refinement_reference(project, concept)
        refinement = concept.scene_plan.get("_image_refinement")
        # Publish the exact batch before reserving or starting a paid call.
        # A lost durable checkpoint must stop work, not be swallowed as a
        # recoverable image/evaluation failure and continue with another image.
        self._generation_progress("generating", current, candidate_id=candidate_id)
        authorization = concept.scene_plan.get("_generation_authorization")
        if authorization is not None:
            image_attempts = sum(item.get("operation") != "typography_recomposition" for item in candidate.attempt_history)
            consumed = max(int(authorization.get("image_attempts_reserved") or 0), image_attempts)
            maximum = 1 + int(authorization.get("extra_image_attempts") or 0)
            if consumed >= maximum:
                raise CoverServiceError("Approved additional image-attempt budget is exhausted")
            # Reserve before calling the provider, so crashes and failed calls consume
            # budget too. Compare-and-swap prevents two retries spending one allowance.
            scene_payload = dict(concept.scene_plan)
            scene_payload["_generation_authorization"] = {**authorization, "image_attempts_reserved": consumed + 1}
            concept = replace(concept, scene_plan=scene_payload)
            lock_path = store.root / ".image-budget.lock"
            with lock_path.open("a+b") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    current = store.save(replace(current, concepts=tuple(
                        concept if item.concept_id == concept.concept_id else item for item in current.concepts
                    )), expected_revision=current.revision)
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        prompt_revision = max(
            prompt_revision or candidate.prompt_revision,
            max((int(item.get("prompt_revision") or 0) for item in candidate.attempt_history), default=0) + 1,
        )
        supplemental: dict[str, Any] = {}
        artwork: bytes | None = None
        try:
            assert self.image_client is not None
            if reference_path is not None:
                generated = self.image_client.edit(edit_prompt(concept.generation_prompt), reference_path,
                    expected_reference_sha256=refinement["reference_image"]["sha256"])
                if (generated.operation != "edit" or generated.reference_image_sha256 != refinement["reference_image"]["sha256"]
                        or media_lib.digest(generated.data) == generated.reference_image_sha256):
                    raise CoverServiceError("图片服务未返回可验证的原图编辑结果。")
                supplemental["image_refinement"] = {**refinement, "reference_transport": generated.reference_transport}
            else:
                generated = self.image_client.generate(concept.generation_prompt)
            if recipe and recipe.get("title_render_mode") == "composed":
                artwork = generated.data
                assets = self._persist_artwork(project_id, project, generated, cover_root=cover_root)
                supplemental["assets"] = assets
                self._generation_progress("composing", current, candidate_id=candidate_id)
                composed = compose_cover(artwork, recipe["title_layout"], expected_title=current.brief.title,
                    output_format="PNG" if generated.content_type == "image/png" else "JPEG")
                generated = replace(generated, data=composed.data)
                assets.update({"title_layout_sha256": composed.title_layout_sha256,
                    "final_cover_sha256": composed.final_cover_sha256, "font_sha256": composed.font_sha256,
                    "title_bounds": [list(bounds) for bounds in composed.text_bounds]})
            ready = self._persist_generated(
                project_id, project, current, candidate, concept, generated,
                project_pending=automatic_repair_original is None,
                cover_root=cover_root,
            )
            self._generation_progress("reviewing", current, candidate_id=candidate_id)
            report = self._quality_report(
                current.brief,
                concept,
                generated.data,
                ready.sha256,
                ready.content_type,
                artwork=artwork,
                supplemental=supplemental,
                checkpoint=lambda evaluation: self._persist_blind_checkpoint(project, current, candidate, evaluation,
                    image_sha256=ready.sha256, artwork_sha256=media_lib.digest(artwork) if artwork is not None else "",
                    cover_root=cover_root),
            )
            if self._is_bound(concept):
                evaluation = supplemental.get("evaluation") or {}
                supplemental["repair_decision"] = classify_cover_repair(report,
                    evaluation_status=str(evaluation.get("evaluation_status") or "unavailable"), composed=artwork is not None)
                supplemental["recipe_sha256"] = recipe.get("recipe_sha256") if recipe else ""
                supplemental["source_binding"] = dict(concept.scene_plan.get("_source_binding") or {})
                self._persist_evaluation(project, current, candidate, report, supplemental, cover_root=cover_root)
            ready = replace(ready, quality_report={**report.to_dict(), **supplemental})
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
            if isinstance(exc, CoverTypographyError):
                report = report_from_binary_findings((QualityFinding("title_layout_failure", "blocker", str(exc), "approved title layout"),))
                attempt = replace(attempt, quality_report=report)
                supplemental["evaluation"] = {"evaluation_schema_version": "cover-evaluation.v1",
                    "evaluation_status": "not_run", "blindness_level": "unavailable", "uncertainty": [str(exc)]}
                supplemental["repair_decision"] = classify_cover_repair(report, composed=True)
                self._persist_evaluation(project, current, candidate, report, supplemental, cover_root=cover_root)
                ready = replace(ready, quality_report={**report.to_dict(), **supplemental})
        if automatic_repair_original is not None:
            prior_report = CoverQualityReport.from_dict(candidate.quality_report or {})
            promote = (
                attempt.status == "ready"
                and attempt.quality_report is not None
                and automatic_repair_improves(prior_report, attempt.quality_report)
            )
            if promote:
                # Publish the pending projection only after checking both story
                # and visual craft. Immutable media/attempt provenance is kept either way.
                try:
                    with delivery_lock(project) if cover_root is None else nullcontext():
                        self._atomic_write(project / ready.relative_path, generated.data)
                except OSError:
                    promote = False
            if not promote:
                review = replace(prior_report, findings=(*prior_report.findings, QualityFinding(
                    "repair_not_promoted", "warning",
                    "Automatic repair retained in history; original remains active for human review",
                    f"{attempt.attempt_id}: repair/projection failed, lacked craft evidence, or introduced a regression",
                )))
                ready = replace(candidate, quality_report={**(candidate.quality_report or {}), **review.to_dict()})
                current = replace(current, concepts=tuple(
                    replace(automatic_repair_original, scene_plan={**automatic_repair_original.scene_plan,
                        "_generation_authorization": concept.scene_plan.get("_generation_authorization", {})})
                    if item.concept_id == concept.concept_id else item
                    for item in current.concepts
                ))
        else:
            promote = attempt.status == "ready"
        attempt_payload = attempt.to_dict()
        if supplemental:
            attempt_payload["quality_report"] = {**(attempt_payload.get("quality_report") or {}), **supplemental}
        ready = replace(
            ready,
            attempt_history=(*candidate.attempt_history, attempt_payload),
            prompt_revision=prompt_revision if promote else ready.prompt_revision,
        )
        updated = replace(
            current,
            candidates=self._replace_candidate(current, ready),
            status=self._status(self._replace_candidate(current, ready)),
            updated_at=self._now(),
        )
        return store.save(updated, expected_revision=current.revision)

    @staticmethod
    def _generation_progress(phase: str, current: CoverSet, *, candidate_id: str | None = None) -> None:
        """Report saved batch state to this request's task, never shared service state."""
        from .cover_generation_tasks import current_progress_sink, GenerationProgressError

        sink = current_progress_sink()
        if sink is None:
            return  # Direct CLI/service calls and candidate retries keep their existing contract.
        index = next((i for i, item in enumerate(current.candidates, 1)
                      if item.candidate_id == candidate_id), None)
        try:
            sink({"phase": phase, "cover_set_id": current.cover_set_id,
                  "current_candidate_id": candidate_id, "current_candidate_index": index,
                  "total": len(current.candidates),
                  "completed": sum(item.status != "pending" for item in current.candidates),
                  "failed": sum(item.status == "failed" for item in current.candidates),
                  "ready": sum(item.status in {"ready", "selected"} for item in current.candidates)})
        except Exception as exc:
            # RuntimeError deliberately escapes the candidate ValueError/OSError
            # fallback. Never spend on the next provider after losing task state.
            raise GenerationProgressError("Cover generation progress checkpoint failed") from exc

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
            if self._is_bound(concept):
                return concept
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
        if self._is_bound(concept):
            # No image-edit endpoint is configured: the fallback is an honest
            # rerender of the exact approved recipe, not a silently changed plan.
            return concept
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
        *,
        artwork: bytes | None = None,
        supplemental: dict[str, Any] | None = None,
        checkpoint: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
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
        new_review = self._is_bound(concept)
        if new_review and supplemental is not None:
            supplemental["evaluation"] = {
                "evaluation_schema_version": "cover-evaluation.v1", "evaluation_status": "unavailable",
                "blindness_level": "unavailable", "missing_dimensions": list(CoverQualityReport._DIMENSIONS),
                "uncertainty": ["Compatible independent evaluator is not configured"],
            }
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
            if new_review:
                review = self.visual_evaluator.review(image=image, thumbnail=thumbnail, artwork=artwork,
                    brief=brief, scene=scene, **({"checkpoint": checkpoint} if checkpoint is not None else {}))
                report = review.report
                if supplemental is not None:
                    supplemental["evaluation"] = review.evaluation
            else:
                report = self.visual_evaluator.evaluate(image=image, thumbnail=thumbnail, brief=brief, scene=scene)
        except ThumbnailProjectionError as exc:
            if new_review and supplemental is not None:
                supplemental["evaluation"].update(evaluation_status="projection_failed", uncertainty=[str(exc)])
            return report_from_binary_findings((QualityFinding(
                "thumbnail_projection_failure",
                "blocker",
                "Cover could not be decoded for mobile-thumbnail review",
                str(exc),
            ),))
        except Exception as exc:
            if new_review and supplemental is not None:
                supplemental["evaluation"].update(evaluation_status="unavailable", uncertainty=[f"Evaluation failed: {type(exc).__name__}"])
            return human_review_report(str(exc))
        blockers = list(report.blockers)
        repair_codes = list(report.repair_codes)
        commercial_review = bool((scene.campaign or {}).get("expression_mode") in {"literal_scene", "editorial"} and supplemental and
            supplemental.get("evaluation", {}).get("review_policy_version") == "cover-readability.v2")
        for finding in report.findings:
            title_failure = not commercial_review and finding.severity != "info" and finding.code in {
                "title_failure", "title_reading_order_ambiguous", "title_reading_order_reversed",
                "title_text_mismatch",
            }
            if finding.severity == "blocker" or title_failure:
                code = "title_failure" if title_failure else finding.code
                if code not in blockers:
                    blockers.append(code)
                if title_failure and code not in repair_codes:
                    repair_codes.append(code)
        if scene.causal_visibility and not commercial_review:
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
            report.anatomy_and_physics, report.cinematic_storytelling, report.genre_emotion,
            report.thumbnail_clarity, report.hook_promise_alignment, report.title_legibility_advisory,
        )
        if scene.causal_visibility:
            required_scores = (*required_scores,
                report.core_conflict_fidelity,
                report.causal_relationship_clarity,
                report.protagonist_agency,
            )
        independent_pass = (not new_review or (supplemental is not None
            and supplemental.get("evaluation", {}).get("evaluation_status") == "complete"
            and supplemental.get("evaluation", {}).get("blindness_level") in {"artwork", "partial"}))
        if not blockers and independent_pass and all(score is not None and score >= 80 for score in required_scores):
            report = replace(report, status="recommended_for_human_review")
        elif not blockers:
            report = replace(report, status="human_review_required")
        return report

    def _persist_artwork(self, project_id, project, generated, *, cover_root=None):
        mime = content_type(generated.data)
        width, height = dimensions(generated.data)
        extension = _GENERATION_EXTENSIONS.get(mime)
        if not extension or mime != generated.content_type or not aspect_ratio_matches(width, height):
            raise CoverServiceError("Artwork bytes failed cover validation")
        sha = media_lib.digest(generated.data)
        path = self._internal_root(project, cover_root) / "artwork" / f"{sha}{extension}"
        self._atomic_write(path, generated.data)
        assert self.media_store is not None and self.media_add is not None
        self.media_store.put(project_id, sha, extension, generated.data)
        media = self.media_add(project_id=project_id, sha=sha, ext=extension, filename=f"artwork-{sha[:12]}{extension}",
            content_type=mime, size=len(generated.data), width=width, height=height, kind="cover", alt="Cover artwork before title composition")
        return {"artwork_sha256": sha, "artwork_relative_path": path.relative_to(project).as_posix(),
                "artwork_media_id": str(getattr(media, "id", "")), "artwork_content_type": mime,
                "artwork_generation": {"model": generated.model, "request_id": generated.request_id,
                    "request_size": generated.request_size, "observed_model": generated.observed_model,
                    "model_verified": generated.model_verified, **self._edit_provenance(generated)}}

    @staticmethod
    def _edit_provenance(generated):
        return ({"operation": "edit", "reference_image_sha256": generated.reference_image_sha256,
                 "reference_transport": generated.reference_transport} if generated.operation == "edit" else {})

    def _evaluation_root(self, project, cover_root):
        from core.cover_sources import _safe_path
        relative = (self._internal_root(project, cover_root) / "evaluations").relative_to(project)
        # Validate from the owner boundary before resolving any intermediate
        # directory, so neither A nor its final report can follow a symlink out.
        return _safe_path(project, relative.as_posix())

    def _persist_blind_checkpoint(self, project, cover_set, candidate, evaluation, *,
                                  image_sha256, artwork_sha256, cover_root=None):
        """Persist immutable A evidence before the independent B call begins."""
        import json
        concept = next(item for item in cover_set.concepts if item.concept_id == candidate.concept_id)
        evaluation_id = f"evaluation-{uuid.uuid4().hex}"
        root = self._evaluation_root(project, cover_root)
        root.mkdir(parents=True, exist_ok=True)
        directory = root / evaluation_id
        directory.mkdir(exist_ok=False)  # Never overwrite a prior A, even on ID collision.
        path = directory / "blind-read.json"
        payload = {"schema_version": "cover-blind-checkpoint.v1", "evaluation_id": evaluation_id,
            "cover_set_id": cover_set.cover_set_id, "candidate_id": candidate.candidate_id,
            "concept_id": candidate.concept_id, "project_id": cover_set.project_id,
            "recipe_sha256": (concept.scene_plan.get("_compiled_recipe") or {}).get("recipe_sha256", ""),
            "source_binding": dict(concept.scene_plan.get("_source_binding") or {}),
            "image_sha256": image_sha256, "artwork_sha256": artwork_sha256,
            "created_at": self._now(), "blind_evaluation": dict(evaluation)}
        data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        self._atomic_write(path, data)
        return {"evaluation_id": evaluation_id, "relative_path": path.relative_to(project).as_posix(),
                "sha256": media_lib.digest(data), "blind_read_sha256": evaluation["blind_read_sha256"]}

    def _persist_evaluation(self, project, cover_set, candidate, report, supplemental, *, cover_root=None):
        import json
        evaluation = supplemental.setdefault("evaluation", {})
        root = self._evaluation_root(project, cover_root)
        checkpoint = evaluation.get("blind_checkpoint")
        if checkpoint:
            from core.cover_sources import _safe_path
            evaluation_id = str(checkpoint.get("evaluation_id") or "")
            directory = _safe_path(root, evaluation_id)
            checkpoint_path = _safe_path(directory, "blind-read.json")
            data = checkpoint_path.read_bytes()
            original = json.loads(data)
            if (checkpoint.get("relative_path") != checkpoint_path.relative_to(project).as_posix()
                    or media_lib.digest(data) != checkpoint.get("sha256")
                    or original.get("evaluation_id") != evaluation_id
                    or original.get("cover_set_id") != cover_set.cover_set_id
                    or original.get("candidate_id") != candidate.candidate_id
                    or original.get("concept_id") != candidate.concept_id
                    or original.get("blind_evaluation", {}).get("blind_read") != evaluation.get("blind_read")
                    or original.get("blind_evaluation", {}).get("blind_read_sha256") != evaluation.get("blind_read_sha256")):
                raise CoverServiceError("Blind read checkpoint binding failed integrity verification")
        else:
            evaluation_id = f"evaluation-{uuid.uuid4().hex}"
            directory = root / evaluation_id
        relative = directory / "report.json"
        evaluation["evaluation_id"] = evaluation_id
        evaluation["relative_path"] = relative.relative_to(project).as_posix()
        payload = {"cover_set_id": cover_set.cover_set_id, "candidate_id": candidate.candidate_id,
                   "quality_report": {**report.to_dict(), **supplemental}}
        self._atomic_write(relative, (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())

    def _persist_generated(
        self,
        project_id: str,
        project: Path,
        cover_set: CoverSet,
        candidate: CoverCandidate,
        concept: CoverConcept,
        generated: GeneratedImage,
        *, project_pending: bool = True, cover_root: str | Path | None = None,
    ) -> CoverCandidate:
        mime = content_type(generated.data)
        width, height = dimensions(generated.data)
        expected_model = str(
            getattr(getattr(self.image_client, "settings", None), "model", "")
            or candidate.model
            or "gpt-image-2"
        )
        from core.image_capabilities import image_models_match
        if generated.model != expected_model:
            raise CoverServiceError(f"Generated cover model does not match the configured image model {expected_model}")
        if (generated.observed_model and not image_models_match(expected_model, generated.observed_model)) or (
            generated.model_verified and not generated.observed_model
        ):
            raise CoverServiceError("Generated cover model verification does not match the requested image model")
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
        pending = (self._internal_root(project, cover_root) / "pending").relative_to(project) if cover_root is not None else Path("outputs/deliverables/covers/pending")
        relative = pending / cover_set.cover_set_id / f"cover-{index:02d}{extension}"
        if project_pending:
            target = project / relative
            if not target.resolve().is_relative_to(project):
                raise CoverServiceError("Cover candidate projection must stay within the project")
            with delivery_lock(project) if cover_root is None else nullcontext():
                self._atomic_write(target, generated.data)
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
                **candidate.safe_request_parameters,
                "n": 1,
                "size": requested_size,
                "model": generated.model,
                "observed_model": generated.observed_model,
                "model_verified": generated.model_verified,
                "output_format": extension.lstrip(".").replace("jpg", "jpeg"),
                **self._edit_provenance(generated),
                **({"edit_prompt_sha256": concept.scene_plan["_image_refinement"]["edit_prompt_sha256"]}
                   if generated.operation == "edit" else {}),
            },
            error="",
        )

    @staticmethod
    def _portfolio_quality(report: CoverQualityReport, comparison: Mapping[str, Any]) -> CoverQualityReport:
        if "duplicate_artwork" in report.blockers:
            blockers = tuple(code for code in report.blockers if code != "duplicate_artwork")
            report = replace(report, blockers=blockers,
                status="blocked" if blockers else "human_review_required")
        matches = comparison.get("matches") or ()
        if any(item.get("kind") == "exact" for item in matches):
            return replace(report, status="blocked", blockers=tuple(dict.fromkeys((*report.blockers, "duplicate_artwork"))))
        if (matches or comparison.get("status") == "unavailable") and report.status == "recommended_for_human_review":
            return replace(report, status="human_review_required")
        return report

    def _compare_portfolio(self, project_id, project, store, current, *, cover_root=None, only_candidate_id=None):
        """Local duplicate advisory on retained artwork, without model or image calls."""
        eligible = [candidate for candidate in current.candidates
            if candidate.status in {"ready", "selected", "rejected"}
            and self._is_bound(next(item for item in current.concepts if item.concept_id == candidate.concept_id))]
        if len(eligible) < 2:
            return current
        images, basis, warnings = {}, {}, []
        try:
            for candidate in eligible:
                assets = (candidate.quality_report or {}).get("assets") or {}
                artwork = bool(assets.get("artwork_sha256"))
                basis[candidate.candidate_id] = "artwork" if artwork else "final_cover"
                images[candidate.candidate_id] = self._stored_asset(project, project_id,
                    assets["artwork_sha256"] if artwork else candidate.sha256,
                    assets["artwork_content_type"] if artwork else candidate.content_type,
                    assets["artwork_relative_path"] if artwork else candidate.relative_path)
            warnings = compare_cover_images(images)
            status, error = "complete", ""
        except (OSError, ValueError) as exc:
            status, error = "unavailable", self._safe_error(exc)
        replacements = {}
        for candidate in eligible:
            if only_candidate_id is not None and candidate.candidate_id != only_candidate_id:
                continue
            matches = [item for item in warnings if candidate.candidate_id in item["pair"]]
            original = candidate.quality_report or {}
            comparison = {"status": status, "basis": basis.get(candidate.candidate_id, "unknown"),
                "matches": matches, "compared_candidates": [item.candidate_id for item in eligible],
                "limitation": "Local pixel similarity only; different images may still repeat the same story composition."}
            if error:
                comparison["error"] = error
            report = self._portfolio_quality(CoverQualityReport.from_dict(original), comparison)
            supplemental = {name: value for name, value in original.items() if name not in report.to_dict()}
            supplemental["portfolio_comparison"] = comparison
            supplemental["evaluation"] = dict(supplemental.get("evaluation") or {})
            self._persist_evaluation(project, current, candidate, report, supplemental, cover_root=cover_root)
            replacements[candidate.candidate_id] = replace(candidate, quality_report={**report.to_dict(), **supplemental})
        return store.save(replace(current, candidates=tuple(replacements.get(item.candidate_id, item) for item in current.candidates)), expected_revision=current.revision)

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

    def _rebuild_delivery(self, project_path: str | Path, cover_set: CoverSet) -> None:
        with delivery_lock(project_path):
            from core.cover_delivery import delivery_selection, tracks_delivery, record_delivery
            selected = delivery_selection(project_path, cover_set)
            tracked = tracks_delivery(selected)
            if tracked:
                record_delivery(project_path, selected, "pending")
            try:
                self._rebuild_delivery_locked(project_path, cover_set)
            except Exception as exc:
                if tracked:
                    record_delivery(project_path, selected, "failed", error=exc)
                raise

    def _rebuild_selected_delivery(self, project_path: str | Path, cover_set: CoverSet) -> None:
        from core.cover_delivery import tracks_delivery
        try:
            self._rebuild_delivery(project_path, cover_set)
        except Exception:
            # Human selection is already committed. A failed local export is a
            # separate durable status; it must not turn selection into a false
            # failure or expose the old ZIP as the newly selected cover.
            if not tracks_delivery(cover_set):
                raise

    def _rebuild_delivery_locked(self, project_path: str | Path, cover_set: CoverSet) -> None:
        project = Path(project_path).resolve()
        store = CoverStore(project)
        active = store.active()
        delivery_set = store.load(active.cover_set_id) if active.cover_set_id else cover_set
        # Legacy sets share pending/cover-XX paths. Restore their own bytes from
        # content-addressed media before exporting, without rewriting provenance.
        pending = project / "outputs/deliverables/covers/pending"
        for candidate in delivery_set.candidates:
            if not candidate.relative_path or not candidate.sha256:
                continue
            target = project / candidate.relative_path
            if not target.resolve().is_relative_to(pending) or target.is_symlink():
                continue
            try:
                data = self._stored_asset(project, delivery_set.project_id,
                    candidate.sha256, candidate.content_type, candidate.relative_path)
            except CoverServiceError:
                # An unavailable historical candidate is omitted by the package
                # builder; unrelated bytes must never be attributed to it.
                continue
            if not target.is_file() or media_lib.digest(target.read_bytes()) != candidate.sha256:
                self._atomic_write(target, data)
        if delivery_set.selected_candidate_id:
            self._project_selected(project, delivery_set,
                self._candidate(delivery_set, delivery_set.selected_candidate_id))
            if delivery_set.compiler_version.startswith("skeleton-poster") or any(
                    concept.scene_plan.get("_poster_recipe") for concept in delivery_set.concepts):
                from core.cover_epub import sync_selected_epub
                chosen = self._candidate(delivery_set, delivery_set.selected_candidate_id)
                sync_selected_epub(project, self._stored_asset(project, delivery_set.project_id,
                    chosen.sha256, chosen.content_type, chosen.relative_path),
                    title=delivery_set.brief.title, expected_sha256=chosen.sha256)
        build_delivery_package(project, cover_set=delivery_set)

    def _require_generation_dependencies(self) -> None:
        if self.image_client is None:
            raise CoverServiceError("Cover image generation client is not configured")
        if self.media_store is None or self.media_add is None:
            raise CoverServiceError("Cover media persistence is not configured")

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, ImageClientError):
            return str(exc)[:500]
        if isinstance(exc, (CoverServiceError, CoverTypographyError)):
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
