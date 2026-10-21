"""Shared approval/integrity gate for skeleton-driven Web and CLI covers.

The approved recipe is an artifact, not a request to run today's compiler again.
All source/analysis reads are through the owner-scoped snapshot repository.
"""
from __future__ import annotations

from core.usage_tracking import scoped_usage, cover_owner


import fcntl
import hashlib
import inspect
import json
import re
from contextlib import contextmanager
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

from .cover_models_v2 import CoverBriefV2
from .cover_store import CoverConflict, CoverStore


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _normalize_targeted_repair(repair, ledger):
    """Remove only a verified, reconstructible application-owned evidence copy.

    Persisted directions carry the complete ledger, unlike raw model drafts.
    Passing that ledger as patchable previous_response duplicates the complete
    frozen evidence already supplied to every director/reviewer request. Normalize
    before constructing the patch contract so the model and local merge use the
    same base hash; never alter an already serialized request or a saved direction.
    """
    if not isinstance(repair, Mapping) or not isinstance(repair.get("base"), Mapping):
        raise CoverConflict("Single-candidate repair requires a complete direction base")
    normalized = deepcopy(dict(repair))
    base = normalized["base"] = dict(normalized["base"])
    if "evidence_ledger" in base:
        try:
            matches = digest(base["evidence_ledger"]) == digest(ledger.to_dict())
        except (TypeError, ValueError) as exc:
            raise CoverConflict("Single-candidate repair evidence ledger is invalid") from exc
        if not matches:
            raise CoverConflict("Single-candidate repair evidence ledger differs from frozen analysis")
        base.pop("evidence_ledger")
    if "refinement_prompt" in normalized:
        prompt = normalized["refinement_prompt"]
        reference = normalized.get("reference_image")
        if (not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 2000
                or not isinstance(reference, Mapping)
                or not isinstance(reference.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]) is None):
            raise CoverConflict("Single-candidate refinement requires a valid request and reference image hash")
        normalized["refinement_prompt"] = prompt.strip()
    return normalized


class CoverWorkflow:
    def __init__(self, context, store: CoverStore):
        self.context = context
        self.store = store

    @scoped_usage("cover", lambda a: cover_owner(a["self"].context))
    def plan(self, analysis_id: str, director, *, count: int = 4,
             freshness_policy: str = "require_current_head", progress_sink=None, targeted_repair=None):
        from .cover_analysis import load_analysis, derive_analysis_context
        from .cover_analysis_reviews import assert_analysis_usable
        from .cover_constraint_sources import constraint_source_evidence
        from .cover_direction_diagnostics import CoverDirectionDiagnostics, DirectionDiagnosticError
        from .cover_sources import load_snapshot
        from .cover_prompt_compiler import compile_direction_recipes
        from .cover_validator import validate_direction

        if freshness_policy not in {"require_current_head", "pinned_snapshot"}:
            raise ValueError("Invalid source freshness policy")
        analysis = load_analysis(self.context, analysis_id)
        payload = analysis.to_dict()
        assert_analysis_usable(self.context, analysis_id, payload)
        if payload.get("coverage_status") != "complete":
            raise CoverConflict("全文分析尚未完成，不能创建封面方向。")
        snapshot = load_snapshot(self.context, payload["snapshot_id"])
        brief, ledger = derive_analysis_context(self.context, analysis)
        if targeted_repair is not None:
            targeted_repair = _normalize_targeted_repair(targeted_repair, ledger)
        if brief.blocking_pending_critical_assumptions():
            raise CoverConflict("骨架仍有关键故事事实待确认，请先修订来源并重新分析。视觉外貌等封面细节可由模型提出临时方案。")
        # Optional capability keeps existing fixture/third-party directors
        # compatible while the production director receives verified source bytes.
        parameters = inspect.signature(director.plan).parameters
        source_options = ({"source_snapshot": snapshot}
                          if "source_snapshot" in parameters else {})
        binding = {
            "schema_version": 1, "owner_scope": self.context.owner_scope,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "source_content_sha256": snapshot.source_content_sha256,
            "source_bundle_sha256": snapshot.source_bundle_sha256,
            "analysis_id": analysis_id, "analysis_sha256": digest(payload),
            "brief_sha256": digest(brief.to_dict()),
            "evidence_sha256": digest(ledger.to_dict()),
            "freshness_policy": freshness_policy,
        }
        if targeted_repair is not None:
            source_options["targeted_repair"] = targeted_repair
            if (getattr(director, "profile_version", "") == "cover-profiles.v11"
                    and "prior_reviewed_directions" in parameters):
                source_options["prior_reviewed_directions"] = self._repair_review_sources(targeted_repair)
        diagnostic_sink = None
        if "diagnostic_sink" in parameters:
            diagnostic_sink = CoverDirectionDiagnostics(
                self.store.root, owner_scope=self.context.owner_scope,
                analysis_id=analysis_id, snapshot_id=snapshot.snapshot_id,
                snapshot_sha256=snapshot.snapshot_sha256,
                source_content_sha256=snapshot.source_content_sha256,
                source_bundle_sha256=snapshot.source_bundle_sha256,
                analysis_sha256=binding["analysis_sha256"],
                model=str(getattr(director, "model", "") or ""), count=count,
            )
            def record_progress(event):
                # A visible checkpoint must be backed by a durable private
                # diagnostic first. Never forward source text or model replies.
                diagnostic_sink(event)
                if progress_sink is not None:
                    progress_sink({"phase": event["stage"], "attempt": event["attempt"],
                                   "diagnostic_attempt_id": diagnostic_sink.attempt_id})

            source_options["diagnostic_sink"] = record_progress
        try:
            direction = director.plan(brief, count=count, evidence_ledger=ledger,
                                      story_analysis=payload, **source_options)
            if targeted_repair is not None:
                from .cover_director import CoverArtDirector
                CoverArtDirector._validate_targeted_repair(targeted_repair, direction.to_dict())
            if direction.source_binding and "campaign_evidence_review" in direction.source_binding:
                binding["campaign_evidence_review"] = deepcopy(
                    direction.source_binding["campaign_evidence_review"])
            if targeted_repair is not None:
                # Repair lineage is immutable and keeps this direction from authorizing a new batch.
                binding["single_candidate_repair"] = {key: targeted_repair[key] for key in
                    ("cover_set_id", "candidate_id", "concept_id", "parent_sha256", "quality_sha256")}
                if "refinement_prompt" in targeted_repair:
                    binding["single_candidate_repair"].update(
                        refinement_prompt_sha256=digest(targeted_repair["refinement_prompt"]),
                        reference_image_sha256=targeted_repair["reference_image"]["sha256"])
            direction = replace(direction, schema_version=2, source_binding=binding)
            findings = validate_direction(
                brief, direction, story_analysis=payload,
                verified_constraint_source_refs={
                    item["reference"]
                    for item in constraint_source_evidence(snapshot, brief.cover_constraints)
                },
            )
            if direction.profile_version == "cover-profiles.v11":
                # Application-owned warnings are persisted as commercial advice;
                # they cannot skip independent facts review or buy another draft.
                from .cover_direction_feedback import commercial_validation_advisories
                receipt = binding.get("campaign_evidence_review")
                if isinstance(receipt, dict):
                    advice = receipt.setdefault("advisories", [])
                    for item in commercial_validation_advisories(direction, findings):
                        if item not in advice:
                            advice.append(item)
                    direction = replace(direction, source_binding=binding)
                findings = tuple(f for f in findings if f.severity != "warning")
            if findings:
                raise ValueError("Cover direction validation failed: " + "; ".join(
                    f"{item.code}: {item.evidence}" for item in findings))
            direction = replace(direction, compiled_recipes=compile_direction_recipes(brief, direction))
            return self.store.create_direction(direction, brief=brief.to_dict())
        except DirectionDiagnosticError:
            # A failed sink is terminal; retrying it must not hide the original
            # storage error or let the director issue another paid call.
            raise
        except Exception as exc:
            if diagnostic_sink is not None:
                diagnostic_sink.record_failure(str(exc))
            raise

    def _repair_review_sources(self, repair):
        """Resolve retained-slot authority from this owner, never a model's base.

        A mixed repair batch can carry candidates produced by several different
        approved directions. Each original binding is independently validated;
        the reviewer later checks exact plan, shared context and receipt hashes.
        Missing legacy receipts select explicit full-facts review, not trust.
        """
        parent = self.store.load(repair["cover_set_id"])
        if digest(parent.to_dict()) != repair["parent_sha256"]:
            raise CoverConflict("Single-candidate review parent changed before planning")
        base_plans = repair["base"]["plans"]
        actual_plans = [{k: deepcopy(v) for k, v in concept.scene_plan.items()
                         if not k.startswith("_")} for concept in parent.concepts]
        if actual_plans != base_plans:
            raise CoverConflict("Single-candidate review base differs from the retained portfolio")
        directions = {}
        for concept in parent.concepts:
            if concept.concept_id == repair["concept_id"]:
                continue
            binding = concept.scene_plan.get("_source_binding") or {}
            if not binding.get("direction_id"):
                continue
            candidate = next((c for c in parent.candidates if c.concept_id == concept.concept_id), None)
            if candidate is None:
                raise CoverConflict("Retained review source has no candidate")
            self.validate_candidate(parent.cover_set_id, candidate.candidate_id)
            direction = self.store.load_direction(binding["direction_id"])
            directions[direction.direction_id] = direction
        return tuple(directions.values())

    def validate(self, direction_id: str, *, approved_hash: str | None = None,
                 require_approval: bool = False, require_latest: bool = True):
        from .cover_analysis import load_analysis, derive_analysis_context
        from .cover_analysis_reviews import assert_analysis_usable
        from .cover_sources import load_snapshot, check_source_head

        direction = (self.store.require_latest_direction(direction_id) if require_latest
                     else self.store.load_direction(direction_id))
        binding = direction.source_binding
        if not binding or direction.schema_version != 2:
            raise CoverConflict("Direction is not bound to a complete skeleton analysis")
        if binding.get("owner_scope") != self.context.owner_scope:
            raise CoverConflict("Cover source belongs to another owner")
        snapshot = load_snapshot(self.context, binding["snapshot_id"])
        analysis = load_analysis(self.context, binding["analysis_id"])
        payload = analysis.to_dict()
        assert_analysis_usable(self.context, analysis.analysis_id, payload)
        brief, ledger = derive_analysis_context(self.context, analysis)
        checks = {
            "snapshot_sha256": snapshot.snapshot_sha256,
            "source_content_sha256": snapshot.source_content_sha256,
            "source_bundle_sha256": snapshot.source_bundle_sha256,
            "analysis_sha256": digest(payload),
            "brief_sha256": digest(brief.to_dict()),
            "evidence_sha256": digest(ledger.to_dict()),
        }
        if payload.get("snapshot_id") != snapshot.snapshot_id or payload.get("coverage_status") != "complete":
            raise CoverConflict("Cover analysis is incomplete or bound to another snapshot")
        if any(binding.get(key) != value for key, value in checks.items()):
            raise CoverConflict("Cover source/analysis binding failed integrity verification")
        if "campaign_evidence_review" in binding:
            from .cover_campaign_evidence import approved_evidence_claim_paths
            _, receipt_findings = approved_evidence_claim_paths(direction, payload)
            if receipt_findings:
                raise CoverConflict("Cover evidence review verification failed: " + "; ".join(
                    f"{item.code}: {item.message}" for item in receipt_findings))
        stored = self.store.load_direction_brief(direction_id)
        if digest(stored) != binding["brief_sha256"]:
            raise CoverConflict("Stored cover brief has changed")
        if direction.brief_sha256 != snapshot.source_content_sha256:
            raise CoverConflict("Cover direction has a different source identity")
        if not direction.evidence_ledger or digest(direction.evidence_ledger.to_dict()) != binding["evidence_sha256"]:
            raise CoverConflict("Stored cover evidence has changed")
        if direction.status in {"stale", "rejected"}:
            raise CoverConflict("请创建并批准新方向；旧批准不能恢复使用。")
        if binding.get("freshness_policy") == "require_current_head":
            try:
                check_source_head(self.context, snapshot)
            except (ValueError, FileNotFoundError) as exc:
                self.store.mark_direction_stale(direction_id, force=True)
                raise CoverConflict(f"骨架已变化，请重新分析或创建固定旧稿的新方向：{exc}") from exc
        elif binding.get("freshness_policy") != "pinned_snapshot":
            raise CoverConflict("Unknown cover freshness policy")
        recipes = direction.compiled_recipes or {}
        if set(recipes) != {plan.concept_id for plan in direction.plans}:
            raise CoverConflict("Approved image recipes are missing")
        for recipe in recipes.values():
            value = dict(recipe)
            expected = value.pop("recipe_sha256", "")
            if expected != digest(value) or not value.get("image_prompt"):
                raise CoverConflict("Approved image recipe failed integrity verification")
        if require_approval:
            approval = self.approval(direction_id)
            if (direction.status != "approved" or not approval.get("source_confirmed")
                    or approval.get("revoked") or approval.get("direction_sha256") != direction.direction_sha256
                    or approval.get("recipes_sha256") != digest(recipes)
                    or approval.get("binding_sha256") != digest(binding)):
                raise CoverConflict("需要批准此骨架快照、方向和生成预算。")
            if approved_hash is not None and approved_hash != direction.direction_sha256:
                raise CoverConflict("Cover direction approval hash mismatch")
        return direction, brief

    @contextmanager
    def _lock(self):
        self.store.root.mkdir(parents=True, exist_ok=True)
        with (self.store.root / ".workflow.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _approval_path(self, direction_id):
        # Validate the ID through the store before constructing a companion path.
        self.store.load_direction(direction_id)
        return self.store.root / "approvals" / f"{direction_id}.json"

    def approval(self, direction_id):
        path = self._approval_path(direction_id)
        return json.loads(path.read_text("utf-8")) if path.is_file() else {}

    def approve(self, direction_id: str, *, expected_brief_sha256: str,
                approved_direction_sha256: str, confirm_source: bool,
                extra_image_attempts: int = 0, single_candidate: str = ""):
        if not confirm_source:
            raise CoverConflict("审核时请同时确认本次采用的骨架与封面事实。")
        if extra_image_attempts not in {0, 1}:
            raise ValueError("Extra image repair allowance must be 0 or 1 per candidate")
        with self._lock():
            direction, _ = self.validate(direction_id)
            repair = (direction.source_binding or {}).get("single_candidate_repair")
            if repair and (single_candidate != repair["concept_id"] or extra_image_attempts != 0):
                raise CoverConflict("此方向仅允许批准指定候选的 1 次修复出图。")
            if single_candidate and not repair:
                raise CoverConflict("普通方向不能转为单张修复批准。")
            old = self.approval(direction_id)
            if old:
                if (old.get("direction_sha256") != approved_direction_sha256 or
                        old.get("extra_image_attempts") != extra_image_attempts or old.get("single_candidate", "") != single_candidate or old.get("revoked")):
                    raise CoverConflict("Existing approval cannot be rebound or its budget replenished")
                return self.store.approve_direction(direction_id,
                    expected_brief_sha256=expected_brief_sha256,
                    approved_direction_sha256=approved_direction_sha256)
            approved = self.store.approve_direction(direction_id,
                expected_brief_sha256=expected_brief_sha256,
                approved_direction_sha256=approved_direction_sha256)
            self.store._write_json(self._approval_path(direction_id), {
                "schema_version": 1, "direction_sha256": approved.direction_sha256,
                "binding_sha256": digest(approved.source_binding),
                "recipes_sha256": digest(approved.compiled_recipes),
                "source_confirmed": True, "extra_image_attempts": extra_image_attempts,
                "initial_image_calls": 1 if single_candidate else len(approved.plans), "batch_reserved": False,
                "single_candidate": single_candidate,
                "approved_at": datetime.now(timezone.utc).isoformat(),
            })
            return approved

    def generation_arguments(self, direction_id: str, approved_hash: str, *, reserve: bool = True,
                             single_candidate: str = "", generation_batch_id: str = ""):
        """Reserve one explicit batch without replenishing an older batch's calls.

        Identified callers supply a fresh durable batch ID and own active-job
        exclusion and request replay. Web tasks use their persisted task ID.
        Re-entering a consumed batch is never authority for another model call.
        Unidentified legacy calls and single-candidate approvals stay one-shot.
        """
        from .cover_models import CoverConcept
        if not isinstance(generation_batch_id, str) or (generation_batch_id and re.fullmatch(
                r"(?:generation-task|cli-batch)-[0-9a-f]{32}", generation_batch_id) is None):
            raise ValueError("Invalid cover generation batch id")
        if generation_batch_id and single_candidate:
            raise CoverConflict("单张修复批准不能用于新的整批生成。")
        with self._lock():
            direction, brief = self.validate(direction_id, approved_hash=approved_hash,
                                              require_approval=True)
            approval = self.approval(direction_id)
            if approval.get("single_candidate", "") != single_candidate:
                raise CoverConflict("单张修复批准不能用于整批生成或其它候选。")
            reservations = approval.get("batch_reservations", {})
            if not isinstance(reservations, dict):
                raise CoverConflict("Cover generation batch reservations are invalid")
            if generation_batch_id:
                if generation_batch_id in reservations:
                    raise CoverConflict("本批出图额度已使用；请查看该批结果，需要再生成时请新建一批。")
            elif approval.get("batch_reserved"):
                raise CoverConflict("本批出图额度已使用；请显式创建新的生成批次。")
            concepts = []
            for plan in direction.plans:
                # Approval freezes executable bytes. Re-entering
                # scene_to_cover_concept would run today's compiler and could
                # alter or reject a previously approved image recipe.
                scene_payload = plan.to_dict()
                if generation_batch_id:
                    scene_payload["_generation_batch_id"] = generation_batch_id
                if direction.visual_identity is not None:
                    scene_payload["_visual_identity"] = direction.visual_identity.to_dict()
                if direction.evidence_ledger is not None:
                    scene_payload["_evidence_ledger"] = direction.evidence_ledger.to_dict()
                if direction.core_conflict_visual_contract is not None:
                    scene_payload["_core_conflict_visual_contract"] = direction.core_conflict_visual_contract.to_dict()
                concepts.append(CoverConcept(
                    concept_id=plan.concept_id, visual_strategy=plan.visual_strategy,
                    focal_scene=plan.frozen_action, composition=plan.blocking,
                    palette=plan.color_script, secondary_signal=plan.primary_prop,
                    title_treatment=plan.title_safe_zone,
                    generation_prompt=direction.compiled_recipes[plan.concept_id]["image_prompt"],
                    scene_plan=scene_payload,
                ))
            if reserve:
                approval["batch_reserved"] = True
                if generation_batch_id:
                    approval["batch_reservations"] = {**reservations, generation_batch_id: {
                        "reserved_at": datetime.now(timezone.utc).isoformat(),
                        "direction_sha256": direction.direction_sha256,
                        "initial_image_calls": len(concepts),
                        "extra_image_attempts": approval["extra_image_attempts"],
                    }}
                self.store._write_json(self._approval_path(direction_id), approval)
            return brief, concepts, {
                "compiler_version": next(iter(direction.compiled_recipes.values()))["compiler_version"],
                "compiled_recipes": direction.compiled_recipes,
                "source_binding": {**direction.source_binding, "direction_id": direction.direction_id,
                                   "direction_sha256": direction.direction_sha256},
                "extra_image_attempts": approval["extra_image_attempts"],
            }

    def validate_candidate(self, cover_set_id: str, candidate_id: str, *,
                           for_generation: bool = False) -> None:
        """Selection may retain older approved work; new calls obey latest/budget gates.

        A UI preference to accept a stale-looking image cannot skip immutable
        source, analysis, recipe, or approval checks for a bound candidate.
        """
        cover_set = self.store.load(cover_set_id)
        from .cover_repair_lineage import validate_lineage
        validate_lineage(self.store, cover_set, candidate_id=candidate_id, for_generation=for_generation)
        candidate = next((x for x in cover_set.candidates if x.candidate_id == candidate_id), None)
        if candidate is None:
            raise CoverConflict("Candidate does not belong to this cover set")
        concept = next((x for x in cover_set.concepts if x.concept_id == candidate.concept_id), None)
        if concept is None:
            raise CoverConflict("Candidate has no matching cover concept")
        scene = concept.scene_plan or {}
        from .cover_typography_amendments import validate_candidate_amendment
        amendment = validate_candidate_amendment(self.store.root, cover_set, candidate, concept)
        if for_generation and amendment is not None:
            raise CoverConflict("Typography amendment approval does not authorize image generation")
        if scene.get("_cover_import"):
            if for_generation:
                raise CoverConflict("Imported cover history has no image-generation allowance")
            from .cover_transfer import validate_import_selection
            validate_import_selection(self.context, self.store, cover_set, candidate)
            return
        binding = scene.get("_source_binding")
        if not binding:
            if scene.get("campaign") or scene.get("_compiled_recipe"):
                raise CoverConflict("Campaign candidate is missing its immutable source binding")
            return
        if not isinstance(binding, dict) or not binding.get("direction_id") or not binding.get("direction_sha256"):
            raise CoverConflict("Candidate source binding is incomplete")
        direction, _ = self.validate(binding["direction_id"], approved_hash=binding["direction_sha256"],
                                     require_approval=True, require_latest=for_generation)
        expected_binding = {**direction.source_binding, "direction_id": direction.direction_id,
                            "direction_sha256": direction.direction_sha256}
        if digest(binding) != digest(expected_binding):
            raise CoverConflict("Candidate source binding does not match its approved direction")
        recipe = (direction.compiled_recipes or {}).get(concept.concept_id)
        if not recipe or digest(scene.get("_compiled_recipe")) != digest(recipe):
            raise CoverConflict("Candidate recipe does not match its approved direction")
        if "_generation_batch_id" in scene:
            batch_id = scene["_generation_batch_id"]
            approval = self.approval(direction.direction_id)
            reservations = approval.get("batch_reservations")
            reservation = (reservations.get(batch_id) if isinstance(batch_id, str)
                           and isinstance(reservations, dict) else None)
            if (not isinstance(reservation, dict)
                    or reservation.get("direction_sha256") != direction.direction_sha256
                    or type(reservation.get("initial_image_calls")) is not int
                    or reservation["initial_image_calls"] != len(direction.plans)
                    or type(reservation.get("extra_image_attempts")) is not int
                    or reservation["extra_image_attempts"] != approval.get("extra_image_attempts")):
                raise CoverConflict("Candidate generation batch does not match its recorded allowance")
        if for_generation:
            authorization = scene.get("_generation_authorization") or {}
            granted = self.approval(direction.direction_id).get("extra_image_attempts")
            stored = authorization.get("extra_image_attempts")
            if type(stored) is not int or stored != granted:
                raise CoverConflict("Candidate repair allowance does not match its original approval")
