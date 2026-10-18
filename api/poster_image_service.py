"""Image execution for the skeleton-poster pipeline, independent of legacy QA.

Only an explicit pending slot can spend once. Raw image checkpoints survive
lettering failures, and every refinement/retry/layout change creates a version.
"""
from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from typing import Callable, Mapping
import uuid

from PIL import Image

from api import media as media_lib
from api.cover_service import CoverService, CoverServiceError
from core.cover_models import CoverCandidate, CoverSet
from core.cover_poster_typography import compose_poster_cover, prepare_poster_layout
from core.cover_refinement import normalize_refinement_prompt, validate_reference
from core.cover_repair_lineage import record_parent, validate_lineage
from core.cover_sources import _safe_path
from core.cover_store import CoverConflict, CoverStore
from core.cover_typography import CoverTypographyError, canonical_digest
from core.image_binary import aspect_ratio_matches, content_type, dimensions
from core.image_client import CodexImageGenerationClient, GeneratedImage, ImageClientError
from core.usage_tracking import scoped_usage


Progress = Callable[[dict], None] | None
_EXT = {"image/png": ".png", "image/jpeg": ".jpg"}


class PosterProgressError(RuntimeError):
    """Losing a durable task checkpoint stops the remaining paid requests."""


class PosterImageService:
    def __init__(self, service: CoverService):
        # Legacy factories leave CoverService.provider at its compatibility
        # default even for a Codex transport. Attribute this new pipeline to
        # the actual configured client without mutating shared legacy services.
        provider = ("codex" if isinstance(service.image_client, CodexImageGenerationClient)
                    else getattr(getattr(service.image_client, "settings", None), "provider", ""))
        self.service = copy(service) if isinstance(provider, str) and provider.strip() else service
        if self.service is not service:
            self.service.provider = provider.strip()

    @staticmethod
    def _store(project_path):
        project = Path(project_path).resolve()
        _safe_path(project, "outputs/covers")
        _safe_path(project, "outputs/deliverables/covers")
        return project, CoverStore(project)

    @staticmethod
    def _owner(current, project_id):
        if current.project_id != project_id:
            raise CoverConflict("封面不属于当前作品。")

    @staticmethod
    def _candidate(current, candidate_id):
        return CoverService._candidate(current, candidate_id)

    @staticmethod
    def _concept(current, candidate):
        return next(item for item in current.concepts if item.concept_id == candidate.concept_id)

    @staticmethod
    def _recipe(concept, title):
        recipe = deepcopy(concept.scene_plan.get("_poster_recipe") or concept.scene_plan.get("_compiled_recipe") or {})
        if concept.scene_plan.get("_poster_recipe") and recipe.get("image_prompt") != concept.generation_prompt:
            raise CoverConflict("Poster image prompt differs from its saved recipe")
        if recipe.get("title_render_mode", "composed") == "native":
            return {**recipe, "title_render_mode": "native"}
        layout = prepare_poster_layout(recipe.get("title_layout"), expected_title=title)
        return {**recipe, "title_render_mode": "composed", "title_layout": layout}

    @staticmethod
    def _status(current, candidates):
        if current.selected_candidate_id:
            return "selected"
        if any(item.status == "pending" for item in candidates):
            return "generating"
        return CoverService._status(candidates)

    def _save_candidate(self, store, set_id, candidate):
        # Merge only this slot after a provider call. A user can select another
        # finished slot in the meantime without losing that explicit choice.
        with store.mutation_lock():
            current = store.load(set_id)
            candidates = tuple(candidate if item.candidate_id == candidate.candidate_id else item for item in current.candidates)
            return store.save(replace(current, candidates=candidates, status=self._status(current, candidates),
                                      updated_at=self.service._now()), expected_revision=current.revision)

    @staticmethod
    def _progress(progress, phase, current, candidate_id=None):
        if progress:
            try:
                progress({"phase": phase, "cover_set_id": current.cover_set_id, "result_cover_set_id": current.cover_set_id,
                          "current_candidate_id": candidate_id, "result_candidate_id": candidate_id,
                          "current_candidate_index": next((i for i, c in enumerate(current.candidates, 1) if c.candidate_id == candidate_id), None),
                          "total": len(current.candidates), "completed": sum(c.status != "pending" for c in current.candidates),
                          "ready": sum(c.status in {"ready", "selected", "rejected"} for c in current.candidates),
                          "failed": sum(c.status == "failed" for c in current.candidates)})
            except Exception as exc:
                raise PosterProgressError("Cover task progress could not be saved; further image requests stopped") from exc

    def _checkpoint_path(self, project, set_id, candidate_id):
        # set_id is validated by CoverStore.load; hash the opaque candidate id.
        return _safe_path(project, f"outputs/covers/poster-render-checkpoints/{set_id}/{sha256(candidate_id.encode()).hexdigest()}.json")

    def _write_checkpoint(self, project, current, candidate, generated):
        mime, size = content_type(generated.data), dimensions(generated.data)
        if mime not in _EXT or mime != generated.content_type or not aspect_ratio_matches(*size):
            raise CoverServiceError("Generated image is not a complete portrait 2:3 image")
        if len(generated.data) > 40 * 1024 * 1024:
            raise CoverServiceError("Generated image exceeds the cover byte limit")
        with Image.open(BytesIO(generated.data)) as image:
            image.verify()
        digest = sha256(generated.data).hexdigest()
        path = _safe_path(project, f"outputs/covers/artwork/{digest}{_EXT[mime]}")
        self.service._atomic_write(path, generated.data)
        metadata = asdict(generated)
        metadata.pop("data")
        record = {"project_id": current.project_id, "cover_set_id": current.cover_set_id,
                  "candidate_id": candidate.candidate_id, "sha256": digest,
                  "relative_path": path.relative_to(project).as_posix(), "generated": metadata,
                  "provider": self.service.provider}
        CoverStore._write_json(self._checkpoint_path(project, current.cover_set_id, candidate.candidate_id), record)
        return record

    def _load_checkpoint(self, project, current, candidate):
        source, original = current, candidate
        visited = set()
        while True:
            key = (source.cover_set_id, original.candidate_id)
            if key in visited or len(visited) >= 100:
                raise CoverConflict("Saved artwork checkpoint lineage is invalid")
            visited.add(key)
            if source.project_id != current.project_id or original.concept_id != candidate.concept_id:
                raise CoverConflict("Saved artwork checkpoint belongs to another cover")
            path = self._checkpoint_path(project, source.cover_set_id, original.candidate_id)
            if path.is_file():
                break
            # Legacy composition failures can already have paid, intact raw art.
            # Reuse that provenance too; never charge for an image just to retype.
            assets = (original.quality_report or {}).get("assets") or {}
            metadata = assets.get("artwork_generation") or {}
            if assets.get("artwork_sha256") and metadata.get("model"):
                data = self.service._stored_asset(project, current.project_id, assets["artwork_sha256"],
                    assets["artwork_content_type"], assets["artwork_relative_path"])
                width, height = dimensions(data)
                generated = GeneratedImage(data, assets["artwork_content_type"], width, height,
                    metadata.get("request_id", ""), metadata["model"], metadata.get("request_size", ""),
                    metadata.get("observed_model", ""), bool(metadata.get("model_verified", False)),
                    metadata.get("operation", "generate"), metadata.get("reference_image_sha256", ""),
                    metadata.get("reference_transport", ""))
                return generated, {"provider": original.provider or self.service.provider}
            owner = original.safe_request_parameters.get("poster_resume_checkpoint")
            if not owner:
                return None
            source = CoverStore(project).load(owner["cover_set_id"])
            original = self._candidate(source, owner["candidate_id"])
        record = json.loads(path.read_text("utf-8"))
        if (record.get("project_id") != current.project_id or record.get("cover_set_id") != source.cover_set_id
                or record.get("candidate_id") != original.candidate_id):
            raise CoverConflict("Saved artwork checkpoint owner changed")
        data = _safe_path(project, record["relative_path"]).read_bytes()
        if sha256(data).hexdigest() != record["sha256"]:
            raise CoverConflict("Saved artwork checkpoint bytes changed")
        return GeneratedImage(data=data, **record["generated"]), record

    def _validate_generated_model(self, generated):
        from core.image_capabilities import image_models_match
        configured = getattr(getattr(self.service.image_client, "settings", None), "model", "") or generated.model
        if not generated.model or generated.model != configured:
            raise CoverServiceError("Generated image model does not match the configured image route")
        if (generated.observed_model and not image_models_match(configured, generated.observed_model)) or (
                generated.model_verified and not generated.observed_model):
            raise CoverServiceError("Generated image model verification is inconsistent")

    def _persist_output(self, project, current, candidate, concept, generated, *, provider=None):
        """Persist immutable output using its original model, even for local edits.

        A typography-only change cannot inherit a newly configured model name.
        Provider claims have already been checked at the actual request boundary.
        """
        mime = content_type(generated.data)
        width, height = dimensions(generated.data)
        if mime not in _EXT or mime != generated.content_type or not aspect_ratio_matches(width, height):
            raise CoverServiceError("Cover output failed image integrity validation")
        digest = media_lib.digest(generated.data)
        index = next(i for i, item in enumerate(current.candidates, 1) if item.candidate_id == candidate.candidate_id)
        relative = f"outputs/deliverables/covers/pending/{current.cover_set_id}/cover-{index:02d}{_EXT[mime]}"
        self.service._atomic_write(_safe_path(project, relative), generated.data)
        self.service.media_store.put(current.project_id, digest, _EXT[mime], generated.data)
        media = self.service.media_add(project_id=current.project_id, sha=digest, ext=_EXT[mime],
            filename=f"cover-{index:02d}{_EXT[mime]}", content_type=mime, size=len(generated.data),
            width=width, height=height, kind="cover", alt=f"Cover candidate {index}: {concept.visual_strategy}")
        media_id = str(getattr(media, "id", ""))
        if not media_id:
            raise CoverServiceError("Cover media registration returned no id")
        return replace(candidate, status="ready", relative_path=relative, media_id=media_id,
            sha256=digest, width=width, height=height, content_type=mime, provider=provider or self.service.provider,
            model=generated.model, request_id=generated.request_id, generation_prompt=concept.generation_prompt, error="",
            safe_request_parameters={**candidate.safe_request_parameters, "n": 1, "size": generated.request_size,
                "model": generated.model, "observed_model": generated.observed_model,
                "model_verified": generated.model_verified, "output_format": _EXT[mime][1:],
                **self.service._edit_provenance(generated)})

    def _attempt(self, project, store, current, candidate_id, progress):
        candidate = self._candidate(current, candidate_id)
        concept = self._concept(current, candidate)
        if candidate.status in {"ready", "selected", "rejected"}:
            return current
        if candidate.status != "pending":
            raise CoverConflict("失败候选请通过显式重试创建新版本。")
        recipe = self._recipe(concept, current.brief.title)
        checkpoint = self._load_checkpoint(project, current, candidate)
        if checkpoint is None:
            self.service._require_generation_dependencies()
            with store.mutation_lock():
                current = store.load(current.cover_set_id)
                candidate = self._candidate(current, candidate_id)
                if candidate.safe_request_parameters.get("poster_render_state") == "reserved":
                    raise CoverConflict("上次图片请求结果尚不确定；不会自动重复调用，请显式重试。")
                candidate = replace(candidate, safe_request_parameters={**candidate.safe_request_parameters,
                                    "poster_render_state": "reserved"})
                current = self._save_candidate(store, current.cover_set_id, candidate)
        self._progress(progress, "composing" if checkpoint else "generating", current, candidate_id)
        attempt_id = "attempt-" + uuid.uuid4().hex
        assets = {}
        try:
            if checkpoint:
                generated, record = checkpoint
            else:
                refinement = concept.scene_plan.get("_poster_refinement")
                if refinement:
                    reference = refinement["reference_image"]
                    path = validate_reference(project, reference)
                    generated = self.service.image_client.edit(concept.generation_prompt, path,
                        expected_reference_sha256=reference["sha256"])
                    if (generated.operation != "edit" or generated.reference_image_sha256 != reference["sha256"]
                            or media_lib.digest(generated.data) == reference["sha256"]):
                        raise CoverServiceError("图片服务未返回可验证的原图编辑结果。")
                else:
                    generated = self.service.image_client.generate(concept.generation_prompt)
                self._validate_generated_model(generated)
                record = self._write_checkpoint(project, current, candidate, generated)
            if recipe["title_render_mode"] == "composed":
                assets = self.service._persist_artwork(current.project_id, project, generated)
                candidate = replace(candidate, quality_report={"assets": assets, "advisory_status": "not_run"},
                    safe_request_parameters={**candidate.safe_request_parameters, "poster_render_state": "artwork_saved"})
                current = self._save_candidate(store, current.cover_set_id, candidate)
                self._progress(progress, "composing", current, candidate_id)
                composed = compose_poster_cover(generated.data, recipe["title_layout"], expected_title=current.brief.title)
                assets = {**assets, "title_layout_sha256": composed.title_layout_sha256,
                          "final_cover_sha256": composed.final_cover_sha256, "font_sha256": composed.font_sha256,
                          "font_faces": recipe["title_layout"]["fonts"], "title_bounds": [list(x) for x in composed.text_bounds]}
                generated = replace(generated, data=composed.data, content_type="image/png")
            ready = self._persist_output(project, current, candidate, concept, generated, provider=record["provider"])
            ready = replace(ready, quality_report={"assets": assets, "advisory_status": "not_run",
                                                   "selection_policy": "complete_image_direct"},
                safe_request_parameters={**ready.safe_request_parameters, "poster_render_state": "ready"})
            attempt = {**ready.to_dict(), "attempt_id": attempt_id, "operation": generated.operation,
                       "status": "ready", "image_sha256": ready.sha256, "created_at": self.service._now()}
            attempt.pop("attempt_history", None)
            ready = replace(ready, attempt_history=(*candidate.attempt_history, attempt))
        except (ImageClientError, OSError, ValueError) as exc:
            from api.poster_tasks import error_message
            error = (error_message(exc) if isinstance(exc, (ImageClientError, CoverServiceError, CoverTypographyError))
                     else self.service._safe_error(exc))[:500]
            attempt = {"attempt_id": attempt_id, "status": "failed", "operation": "edit" if concept.scene_plan.get("_poster_refinement") else "generate",
                       "error": error, "created_at": self.service._now(), "generation_prompt": concept.generation_prompt}
            ready = replace(candidate, status="failed", error=error, attempt_history=(*candidate.attempt_history, attempt),
                safe_request_parameters={**candidate.safe_request_parameters, "poster_render_state": "failed"})
        current = self._save_candidate(store, current.cover_set_id, ready)
        self._progress(progress, "candidate_saved", current, candidate_id)
        return current

    @scoped_usage("cover", lambda a: a["project_path"])
    def generate(self, project_id, project_path, cover_set_id, candidate_ids=None, progress: Progress = None):
        project, store = self._store(project_path)
        current = store.load(cover_set_id)
        self._owner(current, project_id)
        validate_lineage(store, current)
        ids = list(candidate_ids) if candidate_ids is not None else [c.candidate_id for c in current.candidates if c.status == "pending"]
        if len(ids) != len(set(ids)) or not set(ids) <= {c.candidate_id for c in current.candidates}:
            raise CoverConflict("Unknown or duplicate cover candidate")
        # A local font/line-fit error must not consume four paid image calls.
        # Validate every requested recipe before the first provider request.
        blank = BytesIO()
        Image.new("RGB", (1024, 1536)).save(blank, format="PNG")
        for candidate_id in ids:
            candidate = self._candidate(current, candidate_id)
            if candidate.status in {"ready", "selected", "rejected"}:
                continue
            recipe = self._recipe(self._concept(current, candidate), current.brief.title)
            if recipe["title_render_mode"] == "composed":
                compose_poster_cover(blank.getvalue(), recipe["title_layout"], expected_title=current.brief.title)
        self._progress(progress, "preparing", current)
        for candidate_id in ids:
            validate_lineage(store, current, candidate_id=candidate_id, for_generation=True)
            current = self._attempt(project, store, store.load(cover_set_id), candidate_id, progress)
        self._progress(progress, "complete", current)
        return store.load(cover_set_id)

    def _clone(self, store, parent, candidate_ids, *, operation, concepts=None, replacements=None):
        candidate_ids = list(candidate_ids)
        if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
            raise CoverConflict("Choose one or more unique candidate slots")
        originals = [self._candidate(parent, item) for item in candidate_ids]
        lineage = record_parent(store, parent, candidate_id=candidate_ids[0], operation=operation)
        if operation == "poster_retry":
            lineage.update(parent_candidate_ids=candidate_ids, target_concept_ids=[c.concept_id for c in originals])
        mapping = {}
        for original in originals:
            replacement = (replacements or {}).get(original.candidate_id)
            if replacement is None:
                parameters = {key: value for key, value in original.safe_request_parameters.items()
                              if key not in {"poster_render_state", "poster_resume_checkpoint"}}
                parameters["poster_resume_checkpoint"] = {
                    "cover_set_id": parent.cover_set_id, "candidate_id": original.candidate_id}
                replacement = CoverCandidate(candidate_id="candidate-" + uuid.uuid4().hex, concept_id=original.concept_id,
                    generation_prompt=original.generation_prompt, safe_request_parameters=parameters,
                    attempt_history=original.attempt_history)
            mapping[original.candidate_id] = replacement
        child = store.create(replace(parent, cover_set_id="cover-" + uuid.uuid4().hex, revision=0,
            selected_candidate_id="", status="generating", repair_lineage=lineage,
            concepts=tuple(concepts or parent.concepts), candidates=tuple(mapping.get(c.candidate_id,
                replace(c, status="ready") if c.status == "selected" else c) for c in parent.candidates),
            created_at=self.service._now(), updated_at=self.service._now()))
        return child, mapping, lineage

    @scoped_usage("cover", lambda a: a["project_path"])
    def retry(self, project_id, project_path, parent_set_id, candidate_ids=None, progress: Progress = None):
        project, store = self._store(project_path)
        parent = store.load(parent_set_id)
        self._owner(parent, project_id)
        if candidate_ids is None:
            candidate_ids = [c.candidate_id for c in parent.candidates if c.status in {"failed", "pending"}]
        for candidate_id in candidate_ids:
            if self._candidate(parent, candidate_id).status not in {"failed", "pending"}:
                raise CoverConflict("Only failed or interrupted slots can be retried")
        current, mapping, _ = self._clone(store, parent, candidate_ids, operation="poster_retry")
        self._progress(progress, "preparing", current)
        return self.generate(project_id, project, current.cover_set_id, [x.candidate_id for x in mapping.values()], progress)

    def _reference(self, project, candidate, concept):
        assets = (candidate.quality_report or {}).get("assets") or {}
        if assets.get("artwork_sha256"):
            reference = {"kind": "artwork", "sha256": assets["artwork_sha256"],
                "relative_path": assets["artwork_relative_path"], "content_type": assets["artwork_content_type"]}
        elif candidate.status in {"ready", "selected", "rejected"}:
            recipe = concept.scene_plan.get("_poster_recipe") or concept.scene_plan.get("_compiled_recipe") or {}
            if recipe.get("title_render_mode") == "composed":
                raise CoverConflict("原画已缺失，不能假装使用可独立排版的原图。")
            reference = {"kind": "cover", "sha256": candidate.sha256,
                "relative_path": candidate.relative_path, "content_type": candidate.content_type}
        else:
            raise CoverConflict("请选择已完整生成的封面。")
        data = _safe_path(project, reference["relative_path"]).read_bytes()
        reference["width"], reference["height"] = dimensions(data)
        validate_reference(project, reference)
        return reference, data

    @scoped_usage("cover", lambda a: a["project_path"])
    def refine(self, project_id, project_path, parent_set_id, candidate_id, prompt, keep_composition=True, progress: Progress = None):
        project, store = self._store(project_path)
        parent = store.load(parent_set_id)
        self._owner(parent, project_id)
        original = self._candidate(parent, candidate_id)
        concept = self._concept(parent, original)
        prompt = normalize_refinement_prompt(prompt)
        if not prompt:
            raise ValueError("请填写封面优化要求。")
        if not callable(getattr(self.service.image_client, "edit", None)):
            raise CoverServiceError("当前图片服务不支持原图编辑。")
        reference, _ = self._reference(project, original, concept)
        recipe = self._recipe(concept, parent.brief.title)
        if reference["kind"] == "cover":
            recipe["title_render_mode"] = "native"
        text_policy = ("Keep this artwork free of title lettering; exact book lettering will be composed locally."
                       if reference["kind"] == "artwork" else f"Preserve the exact existing book title: {parent.brief.title!r}.")
        identity = {key: concept.scene_plan[key] for key in ("story", "direction", "metadata") if key in concept.scene_plan}
        brief_data = parent.brief.to_dict()
        identity["core_constraints"] = {key: brief_data[key] for key in (
            "protagonist", "principal_characters", "relationship_map", "relationship_or_power_contrast",
            "lived_environment", "forbidden_elements", "cover_constraints") if key in brief_data}
        edit_prompt = ("Edit the supplied cover reference into one refined image. Preserve recognizable character identities, "
                       "ages and core relationships. The user's art request may change lighting, styling and staging, "
                       "but must not invent different identities or relationships. "
                       + ("Keep the overall composition and subject positions. " if keep_composition else "The composition may be revised. ")
                       + text_policy + "\nBOOK: " + parent.brief.title + "\nCORE STORY: " + parent.brief.core_conflict
                       + "\nPRESERVED DESIGN CONTEXT: " + json.dumps(identity, ensure_ascii=False)
                       + "\nUSER'S VISUAL REFINEMENT REQUEST:\n" + prompt)
        lineage = record_parent(store, parent, candidate_id=candidate_id, operation="image_repair")
        binding = {**(concept.scene_plan.get("_source_binding") or {}), "single_candidate_repair": {
            "parent_sha256": lineage["parent_snapshot_sha256"], "cover_set_id": parent.cover_set_id,
            "candidate_id": candidate_id, "concept_id": original.concept_id}}
        recipe.update(image_prompt=edit_prompt)
        refined = replace(concept, generation_prompt=edit_prompt, scene_plan={**concept.scene_plan,
            "_poster_recipe": recipe, "_source_binding": binding, "_poster_refinement": {
                "prompt": prompt, "keep_composition": bool(keep_composition), "reference_image": reference,
                "reference_image_sha256": reference["sha256"], "edit_prompt_sha256": sha256(edit_prompt.encode()).hexdigest()}})
        replacement = CoverCandidate(candidate_id="candidate-" + uuid.uuid4().hex, concept_id=original.concept_id,
                                     generation_prompt=edit_prompt)
        current, _, _ = self._clone(store, parent, [candidate_id], operation="image_repair",
            concepts=[refined if c.concept_id == original.concept_id else c for c in parent.concepts],
            replacements={candidate_id: replacement})
        self._progress(progress, "preparing", current, replacement.candidate_id)
        return self.generate(project_id, project, current.cover_set_id, [replacement.candidate_id], progress)

    def preview_typography(self, project_id, project_path, parent_set_id, candidate_id, layout):
        project, store = self._store(project_path)
        parent = store.load(parent_set_id)
        self._owner(parent, project_id)
        candidate = self._candidate(parent, candidate_id)
        reference, data = self._reference(project, candidate, self._concept(parent, candidate))
        if reference["kind"] != "artwork":
            raise CoverConflict("此历史封面文字已嵌入原图，暂不支持独立排版。")
        prepared = prepare_poster_layout(layout, expected_title=parent.brief.title)
        return compose_poster_cover(data, prepared, expected_title=parent.brief.title).data

    def typography(self, project_id, project_path, parent_set_id, candidate_id, layout, progress: Progress = None):
        project, store = self._store(project_path)
        parent = store.load(parent_set_id)
        self._owner(parent, project_id)
        original = self._candidate(parent, candidate_id)
        concept = self._concept(parent, original)
        reference, artwork = self._reference(project, original, concept)
        if reference["kind"] != "artwork":
            raise CoverConflict("此历史封面文字已嵌入原图，暂不支持独立排版。")
        prepared = prepare_poster_layout(layout, expected_title=parent.brief.title)
        composed = compose_poster_cover(artwork, prepared, expected_title=parent.brief.title)
        recipe = {**self._recipe(concept, parent.brief.title), "title_layout": prepared, "title_render_mode": "composed"}
        updated_concept = replace(concept, scene_plan={**concept.scene_plan, "_poster_recipe": recipe})
        replacement = CoverCandidate(candidate_id="candidate-" + uuid.uuid4().hex, concept_id=original.concept_id,
            generation_prompt=concept.generation_prompt, attempt_history=original.attempt_history)
        current, _, _ = self._clone(store, parent, [candidate_id], operation="typography",
            concepts=[updated_concept if c.concept_id == original.concept_id else c for c in parent.concepts],
            replacements={candidate_id: replacement})
        self._progress(progress, "composing", current, replacement.candidate_id)
        original_assets = {key: value for key, value in (original.quality_report or {}).get("assets", {}).items()
                           if key not in {"typography_transform", "review_artwork_sha256"}}
        assets = {**original_assets,
            "title_layout_sha256": composed.title_layout_sha256, "font_sha256": composed.font_sha256,
            "font_faces": prepared["fonts"], "final_cover_sha256": composed.final_cover_sha256,
            "title_bounds": [list(x) for x in composed.text_bounds]}
        metadata = assets.get("artwork_generation") or {}
        generated = GeneratedImage(composed.data, "image/png", reference["width"], reference["height"],
            original.request_id or metadata.get("request_id", ""), original.model or metadata.get("model", "unknown"),
            str(original.safe_request_parameters.get("size", "")),
            str(original.safe_request_parameters.get("observed_model", "")),
            bool(original.safe_request_parameters.get("model_verified", False)))
        ready = self._persist_output(project, current, replacement, updated_concept, generated, provider=original.provider)
        ready = replace(ready, quality_report={"assets": assets, "advisory_status": "not_run", "selection_policy": "complete_image_direct"},
            safe_request_parameters={**ready.safe_request_parameters, "operation": "typography_recomposition", "image_calls": 0,
                                     "poster_render_state": "ready"})
        attempt = {**ready.to_dict(), "attempt_id": "composition-" + uuid.uuid4().hex, "operation": "typography_recomposition",
                   "status": "ready", "image_sha256": ready.sha256, "created_at": self.service._now()}
        attempt.pop("attempt_history", None)
        current = self._save_candidate(store, current.cover_set_id, replace(ready, attempt_history=(*original.attempt_history, attempt)))
        self._progress(progress, "complete", current, ready.candidate_id)
        return current
