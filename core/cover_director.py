"""Structured Art Director boundary for cover scene plans."""

from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from typing import Any, Callable, Mapping, Sequence

from .cover_story_policy import STORY_POLICY_VERSION, story_cast_policy, uses_story_policy
from .cover_design import VisualEvidenceLedger, evidence_ledger_from_brief
from .cover_models_v2 import ArtDirectionSet, CoverBriefV2
from .cover_novelty import plan_fingerprint
from .cover_profiles import portfolio_blueprint
from .cover_validator import ValidationFinding, validate_direction
from .cover_render_policy import RENDER_POLICY, PHOTOGRAPHIC_RENDER_CONTRACT, HUMAN_PERFORMANCE_CONTRACT


class CoverDirectionError(ValueError):
    """A direction response cannot safely reach image generation."""


class CoverArtDirector:
    _MAX_SEMANTIC_REPAIR_ATTEMPTS = 2

    def __init__(
        self,
        *,
        complete: Callable[[str, str], str] | None = None,
        model: str = "",
        profile_version: str = "cover-profiles.v9",
        fixture: Mapping[str, Any] | None = None,
    ) -> None:
        self._complete = complete
        self.model = model.strip()
        self.profile_version = profile_version
        self._fixture = dict(fixture) if fixture is not None else None

    @classmethod
    def from_fixture(cls, payload: Mapping[str, Any]) -> "CoverArtDirector":
        return cls(
            model=str(payload.get("director_model") or "fixture-director"),
            profile_version=str(payload.get("profile_version") or "cover-profiles.v2"),
            fixture=payload,
        )

    def plan(
        self,
        brief: CoverBriefV2,
        *,
        count: int,
        evidence_ledger: VisualEvidenceLedger | None = None,
        recent_fingerprints: Sequence[Mapping[str, Any]] = (),
    ) -> ArtDirectionSet:
        if not 3 <= count <= 5:
            raise CoverDirectionError("Cover direction count must be between 3 and 5")
        ledger = evidence_ledger or evidence_ledger_from_brief(brief)
        if self._fixture is not None:
            return self._direction_from_payload(
                dict(self._fixture), brief=brief, count=count,
                evidence_ledger=evidence_ledger,
            )
        if self._complete is not None:
            user_prompt = self._user_prompt(
                brief, count, profile_version=self.profile_version,
                evidence_ledger=ledger, recent_fingerprints=recent_fingerprints,
            )
            last_direction: ArtDirectionSet | None = None
            for attempt in range(self._MAX_SEMANTIC_REPAIR_ATTEMPTS + 1):
                raw = self._complete_response(user_prompt)
                try:
                    payload = self._decode_response(raw)
                    direction = self._direction_from_payload(
                        payload, brief=brief, count=count, evidence_ledger=ledger,
                    )
                except CoverDirectionError as exc:
                    if attempt >= self._MAX_SEMANTIC_REPAIR_ATTEMPTS:
                        raise
                    user_prompt = self._schema_repair_prompt(
                        brief=brief,
                        count=count,
                        previous_response=raw,
                        error=str(exc),
                        profile_version=self.profile_version,
                        evidence_ledger=ledger,
                        recent_fingerprints=recent_fingerprints,
                    )
                    continue
                findings = validate_direction(
                    brief, direction, recent_fingerprints=recent_fingerprints,
                )
                if not findings:
                    novelty_report = tuple(
                        plan_fingerprint(plan) for plan in direction.plans
                    )
                    return replace(direction, novelty_report=novelty_report)
                last_direction = direction
                if attempt < self._MAX_SEMANTIC_REPAIR_ATTEMPTS:
                    user_prompt = self._repair_prompt(
                        brief=brief,
                        count=count,
                        previous_payload=payload,
                        findings=findings,
                        profile_version=self.profile_version,
                        evidence_ledger=ledger,
                        recent_fingerprints=recent_fingerprints,
                    )
            assert last_direction is not None
            # Preserve the existing API contract: the route reports semantic
            # validation as a 400 and does not persist the rejected direction.
            return last_direction
        raise CoverDirectionError("cover director is not configured")

    @staticmethod
    def _schema_repair_prompt(
        *,
        brief: CoverBriefV2,
        count: int,
        previous_response: str,
        error: str,
        profile_version: str,
        evidence_ledger: VisualEvidenceLedger,
        recent_fingerprints: Sequence[Mapping[str, Any]],
    ) -> str:
        return json.dumps({
            "task": (
                "Repair the previous response so it is one valid JSON object matching the exact response "
                "contract. Preserve valid creative decisions, add or correct only structurally invalid fields, "
                "and return no Markdown or commentary."
            ),
            "schema_error": error,
            "count": count,
            "brief": brief.to_dict(),
            "visual_evidence_ledger": evidence_ledger.prompt_payload(),
            "recent_cover_fingerprints_to_avoid": list(recent_fingerprints)[:24],
            "response_contract": CoverArtDirector._response_contract(
                brief, count, profile_version=profile_version,
            ),
            "previous_response": previous_response,
        }, ensure_ascii=False, sort_keys=True)

    def _complete_response(self, user_prompt: str) -> str:
        assert self._complete is not None
        try:
            return self._complete(self._system_prompt(), user_prompt)
        except Exception as exc:  # provider failures must stop before image generation
            raise CoverDirectionError(f"cover director failed: {exc}") from exc

    @staticmethod
    def _decode_response(raw: str) -> Mapping[str, Any]:
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise CoverDirectionError("cover director returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise CoverDirectionError("cover director response must be a JSON object")
        return payload

    def _direction_from_payload(
        self,
        source: Mapping[str, Any],
        *,
        brief: CoverBriefV2,
        count: int,
        evidence_ledger: VisualEvidenceLedger | None = None,
    ) -> ArtDirectionSet:
        payload = dict(source)
        payload["director_model"] = str(payload.get("director_model") or self.model or "fixture-director")
        payload["profile_version"] = (
            str(payload.get("profile_version") or self.profile_version)
            if self._fixture is not None
            else self.profile_version
        )
        plans = payload.get("plans")
        if not isinstance(plans, list):
            raise CoverDirectionError("cover director response field 'plans' must be a list")
        if len(plans) != count:
            raise CoverDirectionError(
                f"cover director plan count mismatch: requested {count}, received {len(plans)}"
            )
        if self._fixture is None and self.profile_version.casefold().startswith("cover-profiles.v3"):
            hydrated_plans: list[Any] = []
            for raw_plan, treatment in zip(plans, portfolio_blueprint(count), strict=True):
                if not isinstance(raw_plan, Mapping):
                    hydrated_plans.append(raw_plan)
                    continue
                plan = dict(raw_plan)
                # These ids are application-owned portfolio policy, not creative
                # facts. Hydrating them avoids wasting a model repair on a typo
                # while the validator still checks hook order, scene beats, and
                # story-specific location selection.
                plan.update({
                    "portfolio_slot": treatment.portfolio_slot,
                    "composition_family": treatment.composition_family,
                    "scene_family": treatment.scene_family,
                    "art_style": treatment.art_style,
                    "emotion_register": treatment.emotion_register,
                    "typography_style": treatment.typography_style,
                })
                hydrated_plans.append(plan)
            payload["plans"] = hydrated_plans
        if uses_story_policy(payload["profile_version"]):
            payload["plans"] = [
                {**plan, "story_policy_version": STORY_POLICY_VERSION} if isinstance(plan, Mapping) else plan
                for plan in payload["plans"]
            ]
        if evidence_ledger is not None:
            # Evidence is application-owned provenance. The director may cite
            # it but may never rewrite or invent it.
            payload["evidence_ledger"] = evidence_ledger.to_dict()
        try:
            return ArtDirectionSet.from_dict(payload, brief_sha256=brief.source_prompt_sha256)
        except (TypeError, ValueError) as exc:
            raise CoverDirectionError(f"cover director schema validation failed: {exc}") from exc

    @staticmethod
    def _repair_prompt(
        *,
        brief: CoverBriefV2,
        count: int,
        previous_payload: Mapping[str, Any],
        findings: tuple[ValidationFinding, ...],
        profile_version: str = "cover-profiles.v9",
        evidence_ledger: VisualEvidenceLedger | None = None,
        recent_fingerprints: Sequence[Mapping[str, Any]] = (),
    ) -> str:
        response_contract = CoverArtDirector._response_contract(
            brief, count, profile_version=profile_version,
        )
        finding_specific_repairs: list[dict[str, Any]] = []
        missing_conflict_concepts = [
            item.evidence
            for item in findings
            if item.code == "missing_conflict_evidence" and item.evidence
        ]
        if missing_conflict_concepts:
            raw_conflict_contract = previous_payload.get(
                "core_conflict_visual_contract"
            )
            raw_conflict_refs = (
                raw_conflict_contract.get("evidence_refs", ())
                if isinstance(raw_conflict_contract, Mapping)
                else ()
            )
            allowed_ref_candidates = list(dict.fromkeys(
                response_contract.get("base_evidence_refs", ())
            ))
            if evidence_ledger is not None:
                allowed_ref_candidates.extend(
                    reference
                    for reference in evidence_ledger.allowed_refs
                    if reference not in allowed_ref_candidates
                )
            allowed_refs = set(allowed_ref_candidates)
            shared_refs = [
                reference
                for reference in raw_conflict_refs
                if isinstance(reference, str) and reference in allowed_refs
            ]
            replace_invalid_contract_refs = not shared_refs
            if replace_invalid_contract_refs:
                shared_refs = allowed_ref_candidates
            finding_specific_repairs.append({
                "code": "missing_conflict_evidence",
                "concept_ids": missing_conflict_concepts,
                "allowed_shared_evidence_refs": shared_refs,
                "replace_invalid_core_conflict_evidence_refs": replace_invalid_contract_refs,
                "instruction": (
                    "For each named concept, add at least one allowed_shared_evidence_refs "
                    "value to story_evidence_refs while preserving its other valid evidence. "
                    "When replace_invalid_core_conflict_evidence_refs is true, first replace "
                    "the contract's invalid evidence_refs with source-supported values from "
                    "allowed_shared_evidence_refs. This repair is scoped to "
                    "missing_conflict_evidence; other listed validation findings may require "
                    "adding or replacing further exact evidence references."
                ),
            })
        return json.dumps({
            "task": (
                "Repair the previous cover direction JSON. Correct only the listed validation "
                "findings, retain distinct concepts and approved story facts, and return the complete "
                "JSON object with the exact response contract. Do not add commentary."
            ),
            "count": count,
            "brief": brief.to_dict(),
            "visual_evidence_ledger": (
                evidence_ledger.prompt_payload() if evidence_ledger is not None else None
            ),
            "recent_cover_fingerprints_to_avoid": list(recent_fingerprints)[:24],
            "response_contract": response_contract,
            "previous_response": dict(previous_payload),
            "validation_findings": [
                {
                    "code": item.code,
                    "message": item.message,
                    "concept_or_evidence": item.evidence,
                }
                for item in findings
            ],
            "finding_specific_repairs": finding_specific_repairs,
            "repair_rules": (
                [
                    "Use concept_or_evidence to repair the named concept rather than rewriting unrelated plans.",
                    "For portfolio_similarity, change the story instant, focal strategy, composition topology, lighting and typography together while retaining live-action photography; a crop or palette change is insufficient.",
                    "For non_photographic_medium, re-stage the same story facts as a physically plausible photographic scene; replace painted figures, relief stages and illustration instructions throughout the plan, not just art_style.",
                    "For historical_similarity, replace the repeated visual grammar while preserving this book's evidence anchors.",
                    "For missing_visual_identity, derive one coherent book-specific design language from at least two evidence sources.",
                    "For missing_design_reasoning, complete the evidence, design, typography, novelty, and visual-signature fields.",
                    "For missing_human_anchor or missing_reader_anchor, keep the current concept but stage an approved focal protagonist under the versioned cast policy as a clear, emotionally active person inside it.",
                    "Object, environment, absence, and typography may lead the idea, while an approved focal protagonist remains visibly present and meaningful.",
                    "For conflict findings, preserve the distinct design hypothesis while making the approved pressure source, protagonist consequence, and protagonist action legible at thumbnail size.",
                    "Follow the versioned portfolio_rules in response_contract. Preserve different visual hypotheses; do not repair every plan into a crowded ensemble or the same foreground/background tableau.",
                    "Preserve plan count, approved character ids, valid exact evidence references, and spoiler boundaries. Change evidence references only when required by validation_findings; finding_specific_repairs supplies exact candidates for missing_conflict_evidence.",
                ]
                if profile_version.casefold().startswith(("cover-profiles.v4", "cover-profiles.v5", "cover-profiles.v6", "cover-profiles.v7", "cover-profiles.v8", "cover-profiles.v9"))
                else [
                    "Use concept_or_evidence to repair the named concept rather than rewriting unrelated plans.",
                    "For group_blocking, state foreground and background explicitly across blocking and depth_plan.",
                    "For portfolio treatment findings, copy every required treatment id from the matching portfolio_blueprint slot.",
                    "Preserve concept_id, plan count, allowed character ids, and evidence references.",
                ]
            ),
        }, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are the lead book-cover designer for a world-class publishing studio, not a template filler. "
            "First derive a visual identity that could belong only to this novel from the supplied source-bound "
            "evidence. Next derive one core conflict visual contract: who the reader identifies with, what source "
            "of pressure causes the wound, what relationship or status is at stake, and what visible action or "
            "consequence makes the protagonist's response active. Then privately explore at least eight design "
            "hypotheses and return the strongest, most "
            "structurally different portfolio. Choose character-led, relationship-led, object-led, environment-led, "
            "absence-led or typography-led photographic campaign language according to the "
            "story rather than a fixed hierarchy. Every direction must show an approved focal protagonist under the versioned cast policy "
            "as a clear, emotionally active human subject whose face, posture, and story action read at thumbnail "
            "size. Every plan must communicate the same source-bound cause-and-consequence story, but through a "
            "different visual language. A dominant close-up is optional; foreground/background causality is optional. "
            "Composition, photographic treatment, palette, emotional register, and lettering must grow from the novel's own motifs. "
            "Treat title typography as authored visual storytelling, not a generic text overlay. "
            "Return exactly one JSON object that "
            "matches the supplied response_contract, without Markdown fences or prose. Use only approved "
            "story facts and character ids. Never infer ethnicity, age, class, nationality, "
            "new characters, unsupported spoilers, or named artist identities. "
            + PHOTOGRAPHIC_RENDER_CONTRACT + " " + HUMAN_PERFORMANCE_CONTRACT
        )

    @staticmethod
    def _user_prompt(
        brief: CoverBriefV2,
        count: int,
        *,
        profile_version: str = "cover-profiles.v9",
        evidence_ledger: VisualEvidenceLedger | None = None,
        recent_fingerprints: Sequence[Mapping[str, Any]] = (),
    ) -> str:
        return json.dumps({
            "task": (
                f"Create one coherent {count}-direction cover portfolio whose visual identity belongs only to "
                "this novel. Use the complete evidence ledger to discover concrete gestures, spaces, objects, "
                "rituals, emotional reversals, and title semantics. The returned plans are different design "
                "hypotheses, not variations: changing only crop, pose, palette, prop, location, or camera angle "
                "is a repeated concept. Do not default to a large hurt protagonist in front of a smaller causal "
                "relationship. Follow the versioned focal-character policy for every plan, then select the "
                "strongest focal strategy independently for each plan. Do not confuse an emotional aftermath with "
                "the core conflict: each thumbnail must preserve a readable cause, consequence, and protagonist "
                "decision. Follow response_contract.plans.portfolio_rules and story_cast_policy for causal coverage. Choose source-supported story moments to meet the relationship coverage, "
                "and never invent encounters. Treat lighting, palette, negative space, performance and typography "
                "as one finished film campaign design rather than a literal documentary record. "
                "Write concise executable visual fields (usually one or two sentences), not repeated story essays. "
                "Keep detailed justifications in rationale fields; never bury lighting or colors there. "
                "Express the story through actor behavior rather than a display of plot symbols. Refusal, "
                "shock and resolve are intentions, not mandatory gestures: invent their physical expression "
                "from this scene instead of repeating raised palms, finger-pointing or clenched fists. "
                "Emotional intensity need not mean a larger grimace. Props support the event, not an inventory display."
            ),
            "count": count,
            "brief": brief.to_dict(),
            "render_policy": RENDER_POLICY,
            "visual_evidence_ledger": (
                evidence_ledger or evidence_ledger_from_brief(brief)
            ).prompt_payload(),
            "recent_cover_fingerprints_to_avoid": list(recent_fingerprints)[:24],
            "response_contract": CoverArtDirector._response_contract(
                brief, count, profile_version=profile_version,
            ),
        }, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _response_contract(
        brief: CoverBriefV2,
        count: int,
        *,
        profile_version: str = "cover-profiles.v9",
    ) -> dict[str, Any]:
        if profile_version.casefold().startswith(("cover-profiles.v4", "cover-profiles.v5", "cover-profiles.v6", "cover-profiles.v7", "cover-profiles.v8", "cover-profiles.v9")):
            return CoverArtDirector._adaptive_response_contract(brief, count, profile_version)
        treatments = portfolio_blueprint(count)
        hook_types = [item.hook_type for item in treatments]
        character_ids = [item.character_id for item in brief.principal_characters]
        required_character_ids = [item.character_id for item in brief.required_characters]
        optional_conflict_character_ids = [
            item.character_id for item in brief.principal_characters if not item.must_appear
        ]
        evidence_refs = [
            *(f"character:{item.character_id}" for item in brief.principal_characters),
            *(f"node:{item.node_id}" for item in brief.decisive_story_nodes),
            *(f"signal:{item.signal_id}" for item in brief.secondary_signals),
        ]
        location_families = list(brief.lived_environment.primary_spaces)
        return {
            "top_level_required_fields": [
                "schema_version", "director_model", "profile_version", "plans",
                "visual_assumptions",
            ],
            "fixed_values": {
                "schema_version": 1,
                "director_model": "configured model id or cover-art-director",
                "profile_version": profile_version,
            },
            "allowed_character_ids": character_ids,
            "required_character_ids": required_character_ids,
            "optional_conflict_character_ids": optional_conflict_character_ids,
            "allowed_evidence_refs": evidence_refs,
            "allowed_location_families": location_families,
            "plans": {
                "exact_count": count,
                "required_hook_types": hook_types,
                "one_unique_hook_type_per_plan": True,
                "one_unique_visual_strategy_per_plan": True,
                "cast_must_include_every_required_character_id": True,
                "portfolio_blueprint": [
                    {
                        "hook_type": item.hook_type,
                        "portfolio_slot": item.portfolio_slot,
                        "display_name": item.display_name,
                        "composition_family": item.composition_family,
                        "composition_direction": item.composition_direction,
                        "scene_family": item.scene_family,
                        "scene_direction": item.scene_direction,
                        "art_style": item.art_style,
                        "art_direction": item.art_direction,
                        "emotion_register": item.emotion_register,
                        "emotion_direction": item.emotion_direction,
                        "typography_style": item.typography_style,
                        "typography_direction": (
                            f"{item.typography.letterform_voice}; {item.typography.hierarchy}; "
                            f"{item.typography.expressive_detail}"
                        ),
                        "camera_direction": item.camera_direction,
                    }
                    for item in treatments
                ],
                "portfolio_rules": [
                    (
                        "When approved optional conflict characters and evidence can expose the source of the "
                        "emotion, at least one plan uses a causal conflict tableau: foreground emotional "
                        "consequence plus background causal relationship action in one continuous scene."
                    ),
                    (
                        "Return exactly one plan for each portfolio_blueprint entry, in blueprint order. Copy its "
                        "portfolio_slot, composition_family, scene_family, art_style, emotion_register, and "
                        "typography_style ids exactly into that plan; copy location_family from "
                        "allowed_location_families."
                    ),
                    (
                        "Each plan stages a different evidence-grounded narrative beat. The intimate portrait, "
                        "ensemble conflict, evidence discovery, irreversible threshold, and optional public-pressure "
                        "slot may not replay one location-action tableau with new poses or crops."
                    ),
                    (
                        "Vary who witnesses, chooses, discovers, leaves, refuses, or reaches; changing only palette, "
                        "pose, crop, prop, lens, or camera angle does not create a distinct emotional story."
                    ),
                    (
                        "Use every allowed_location_families entry once before repeating one. When four or more "
                        "approved locations exist, the four default plans use four different locations."
                    ),
                ],
                "required_fields": [
                    "concept_id", "visual_strategy", "story_evidence_refs", "cast",
                    "focal_character_id", "moment_before", "frozen_action", "moment_after",
                    "gaze_graph", "blocking", "environment_anchors", "primary_prop",
                    "shot_scale", "camera_height", "lens", "depth_plan",
                    "motivated_lighting", "color_script", "title_safe_zone", "visual_hook",
                    "portfolio_slot", "composition_family", "scene_family", "location_family", "art_style",
                    "emotion_register", "typography_style",
                ],
                "visual_hook_required_fields": [
                    "hook_type", "first_glance_subject", "open_question", "identity_anchor",
                    "genre_signal", "reader_promise", "target_emotion", "misleading_risk",
                    "expected_thumbnail_read",
                ],
                "field_rules": {
                    "portfolio_slot": "exact id copied from the matching portfolio_blueprint entry",
                    "composition_family": "exact id copied from the matching portfolio_blueprint entry",
                    "scene_family": "exact id copied from the matching portfolio_blueprint entry",
                    "location_family": "one exact string copied from allowed_location_families",
                    "art_style": "exact id copied from the matching portfolio_blueprint entry",
                    "emotion_register": "exact id copied from the matching portfolio_blueprint entry",
                    "typography_style": "exact id copied from the matching portfolio_blueprint entry",
                    "story_evidence_refs": "non-empty array using allowed_evidence_refs only",
                    "cast": (
                        "non-empty array using allowed_character_ids only; include optional conflict characters "
                        "only when they make the approved cause of the emotion visible"
                    ),
                    "focal_character_id": "one id present in cast",
                    "frozen_action": (
                        "one emotionally legible action or decision with visible consequence, never a static mood pose"
                    ),
                    "gaze_graph": (
                        "non-empty array of visible gaze or attention relationships; for a layered conflict "
                        "tableau, at least one attention line must cross depth planes and connect cause to consequence"
                    ),
                    "blocking": (
                        "character-led theatrical poster hierarchy; principal people are clear, "
                        "unobstructed, and larger than environmental detail; foreground carries the emotional "
                        "cost while a secondary plane reveals its cause, not a flat group pose; for every plan "
                        "with four or more cast members, use the literal words foreground and background in "
                        "blocking or depth_plan"
                    ),
                    "environment_anchors": "non-empty array grounded in brief.lived_environment",
                    "shot_scale": (
                        "prefer a medium close-up, medium shot, or readable ensemble scale that keeps "
                        "every required principal face legible"
                    ),
                    "depth_plan": (
                        "required principal faces, eyes, hands, and the primary story action stay "
                        "sharp enough to read at mobile thumbnail size; causal background action may be secondary "
                        "but remains recognizable"
                    ),
                    "motivated_lighting": (
                        "physically plausible scene light that carries the core story atmosphere "
                        "without hiding faces or replacing conflict with a generic color wash"
                    ),
                    "title_safe_zone": (
                        "clear negative space for a horizontally centered title lockup; never overlap "
                        "a principal face, hand, or primary prop"
                    ),
                    "misleading_risk": "brief factual risk statement, not a numeric score",
                },
            },
            "visual_assumptions": {
                "type": "array",
                "item_required_fields": [
                    "field", "proposed_value", "reason", "status", "critical",
                ],
                "allowed_statuses": ["pending_confirmation", "approved"],
                "rule": "use an empty array when no new visual assumptions are needed",
            },
        }
    @staticmethod
    def _adaptive_response_contract(
        brief: CoverBriefV2,
        count: int,
        profile_version: str,
    ) -> dict[str, Any]:
        character_ids = [item.character_id for item in brief.principal_characters]
        reader_anchor_id = brief.reader_anchor_character.character_id
        evidence_refs = [
            *(f"character:{item.character_id}" for item in brief.principal_characters),
            *(f"node:{item.node_id}" for item in brief.decisive_story_nodes),
            *(f"signal:{item.signal_id}" for item in brief.secondary_signals),
        ]
        conflict_profile = profile_version.casefold().startswith(("cover-profiles.v5", "cover-profiles.v6", "cover-profiles.v7", "cover-profiles.v8", "cover-profiles.v9"))
        explicitly_named_conflict_ids = [
            item.character_id
            for item in brief.principal_characters
            if item.character_id != reader_anchor_id
            and item.name.strip()
            and re.search(
                rf"(?<!\w){re.escape(item.name.casefold())}(?!\w)",
                brief.core_conflict.casefold(),
            )
        ]
        contract: dict[str, Any] = {
            "top_level_required_fields": [
                "schema_version", "director_model", "profile_version", "visual_identity",
                "plans", "visual_assumptions",
            ],
            "fixed_values": {
                "schema_version": 1,
                "director_model": "configured model id or cover-art-director",
                "profile_version": profile_version,
            },
            "visual_identity_required_fields": [
                "design_thesis", "dominant_emotional_contradiction", "story_signatures",
                "visual_grammar", "material_language", "palette_logic", "lighting_logic",
                "spatial_logic", "typography_voice", "cast_policy", "cliche_blacklist",
                "uniqueness_anchors", "spoiler_boundary",
            ],
            "visual_identity_field_types": {
                "story_signatures": "array of at least two concrete strings",
                "visual_grammar": "non-empty string or array of non-empty strings",
                "material_language": "non-empty string or array of non-empty strings",
                "cliche_blacklist": "non-empty array of strings",
                "uniqueness_anchors": "array of at least two concrete strings",
                "spoiler_boundary": "non-empty string or array of non-empty strings",
                "all_other_fields": "non-empty string",
            },
            "visual_identity_rules": [
                "Base every identity choice on the supplied brief and evidence ledger, never on a universal cover recipe.",
                "story_signatures and uniqueness_anchors each contain at least two concrete book-specific details.",
                "visual_grammar describes a flexible family resemblance across the portfolio, not one repeated composition.",
                "cliche_blacklist names tempting genre shortcuts that would make this book resemble unrelated books.",
            ],
            "allowed_character_ids": character_ids,
            "reader_anchor_character_id": reader_anchor_id,
            "base_evidence_refs": evidence_refs,
            "evidence_ref_rule": (
                "Use base_evidence_refs or exact evidence:<evidence_id> values copied from visual_evidence_ledger."
            ),
            "allowed_location_families": list(brief.lived_environment.primary_spaces),
            "plans": {
                "exact_count": count,
                "required_fields": [
                    "concept_id", "visual_strategy", "focal_strategy", "story_evidence_refs",
                    "cast", "focal_character_id", "moment_before", "frozen_action", "moment_after",
                    "gaze_graph", "blocking", "environment_anchors", "primary_prop", "shot_scale",
                    "camera_height", "lens", "depth_plan", "motivated_lighting", "color_script",
                    "title_safe_zone", "visual_hook", "portfolio_slot", "composition_family",
                    "scene_family", "location_family", "art_style", "emotion_register",
                    "typography_style", "design_rationale", "evidence_summary",
                    "typography_rationale", "novelty_rationale", "visual_signature",
                ],
                "visual_hook_required_fields": [
                    "hook_type", "first_glance_subject", "open_question", "identity_anchor",
                    "genre_signal", "reader_promise", "target_emotion", "misleading_risk",
                    "expected_thumbnail_read",
                ],
                "field_types": {
                    "story_evidence_refs": "non-empty array of strings",
                    "cast": "non-empty array of strings",
                    "gaze_graph": "non-empty array of strings",
                    "environment_anchors": "non-empty array of strings",
                    "visual_hook": "object",
                    "all_other_required_fields": "non-empty string",
                },
                "portfolio_rules": [
                    "Invent descriptive ids for visual fields; do not copy a fixed portfolio blueprint.",
                    "Each plan must differ structurally across focal strategy, composition topology, scene source, photographic treatment, emotional register, and typography logic; all remain live-action photography.",
                    "Every plan includes the reader-anchor protagonist as a clear human subject with readable face, posture, emotion, and story action at mobile size.",
                    "Object-led, environment-led, absence-led, and typography-led concepts remain valid by integrating the protagonist into their distinct visual grammar rather than deleting the person.",
                    "Every plan stages one concrete story action grounded in character evidence plus a node, signal, or source-ledger reference; pure decorative symbolism is insufficient.",
                    "When more than one approved principal character exists, at least one plan stages a relationship scene with two or more named characters.",
                    "A repeated location is valid when it is a signature space, but the visual hypothesis and story moment must still differ.",
                    "At least two source types from the evidence ledger should influence each plan when available.",
                    "Late-spoiler evidence may guide recurring motifs and factual identity but must not expose the resolution.",
                ],
                "field_rules": {
                    "focal_strategy": "character-led, relationship-led, object-led, environment-led, absence-led, typography-led, graphic-metaphor, or a story-specific invented strategy",
                    "story_evidence_refs": "non-empty exact references copied from base_evidence_refs or visual_evidence_ledger",
                    "cast": "one or more allowed_character_ids; every plan must include reader_anchor_character_id and only other people essential to this hypothesis",
                    "focal_character_id": "exactly reader_anchor_character_id",
                    "gaze_graph": "non-empty visible attention, gesture, or reading-path logic for the reader-anchor protagonist and any other people",
                    "blocking": "describe the chosen hierarchy without mandatory foreground/background or mandatory large faces",
                    "location_family": "an approved physical location or evidence-supported practical tabletop set, with the real photographed protagonist present; not an abstract or illustrated world",
                    "art_style": "an explicitly photographic, live-action treatment justified by this novel; no illustration, oil painting, relief, CGI or 3D; never name a living artist",
                    "typography_style": "a book-specific lettering system tied to title semantics",
                    "visual_signature": "one concise fingerprint describing what makes this plan recognizable as this book",
                    "novelty_rationale": "state how this differs from the other plans and supplied recent fingerprints",
                },
            },
            "visual_assumptions": {
                "type": "array",
                "item_required_fields": [
                    "field", "proposed_value", "reason", "status", "critical",
                ],
                "allowed_statuses": ["pending_confirmation", "approved"],
                "rule": "use an empty array when no new visual assumptions are needed",
            },
        }
        if not conflict_profile:
            return contract

        contract["top_level_required_fields"].insert(5, "core_conflict_visual_contract")
        contract["core_conflict_visual_contract"] = {
            "required_fields": [
                "protagonist_character_id", "conflict_kind", "pressure_source",
                "pressure_character_ids", "relationship_stakes", "visible_cause",
                "decisive_consequence", "required_visual_signals", "evidence_refs",
                "spoiler_boundary",
            ],
            "field_types": {
                "pressure_character_ids": "array of strings; may be empty",
                "required_visual_signals": "array of at least two strings",
                "evidence_refs": "non-empty array of strings",
                "all_other_required_fields": "non-empty string",
            },
            "rules": [
                "Derive the contract from the approved core conflict and evidence ledger before designing plans.",
                f"protagonist_character_id must be {reader_anchor_id}.",
                "pressure_character_ids contains only approved named characters who visibly embody the cause; it may be empty for non-human pressure.",
                "Do not soften a confirmed betrayal, exclusion, concealed choice, abuse of power, or competing family structure into generic distance.",
                "Do not strengthen ambiguity into romance, infidelity, violence, or another unsupported fact.",
                "required_visual_signals contains at least two concrete, drawable cause-and-consequence signals.",
                "evidence_refs uses exact approved evidence references and spoiler_boundary protects the resolution.",
                "Every plan's story_evidence_refs must include at least one exact value also present in this evidence_refs array.",
            ],
            "characters_explicitly_named_in_core_conflict": explicitly_named_conflict_ids,
            "explicit_character_rule": "Every listed character must appear in pressure_character_ids.",
        }
        plan_contract = contract["plans"]
        plan_contract["required_fields"].extend([
            "causal_visibility", "conflict_delivery", "conflict_read", "cause_signal",
            "consequence_signal", "conflict_character_ids", "protagonist_action_visible",
        ])
        plan_contract["field_types"].update({
            "conflict_character_ids": "array of strings; may be empty",
            "protagonist_action_visible": "boolean",
        })
        flexible = profile_version.casefold().startswith(("cover-profiles.v7", "cover-profiles.v8", "cover-profiles.v9"))
        if flexible:
            plan_contract["portfolio_rules"].extend([
                "Every plan communicates the core conflict through specific source-bound cause and protagonist consequence; sadness alone is insufficient.",
                "At least one plan shows the complete approved causal relationship directly; no fixed percentage of ensemble scenes or fixed cast count for the remaining plans.",
                "Other plans may show direct pressure or indirect, visibly legible evidence of it. Select cast for the story moment; never add people merely to satisfy a quota.",
                "At least one plan shows a concrete active protagonist decision. In every other plan the protagonist still visibly reacts to the specific conflict, not a decorative mood pose.",
                "Every conflict_delivery is distinct; design different narrative moments and spatial hierarchies, not alternate crops of one scene.",
            ])
            plan_contract["field_rules"].update({
                "motivated_lighting": "concise executable key/fill/practical light directions, subject separation and exposure; motivated cinematic polish without automatic grit or flat ambient exposure",
                "color_script": "specific color relationships and focal accents; natural skin, intentional contrast; no mandatory sepia or muted brown palette",
                "title_safe_zone": "one continuous title lockup; English reads left-to-right then top-to-bottom in exact title order, never split into competing clause columns",
                "typography_style": "a book-specific lettering system with coherent hierarchy and exact reading order, readable at thumbnail size",
            })
        else:
            plan_contract["portfolio_rules"].extend([
                "Every plan communicates the core conflict contract at thumbnail size; atmosphere or sadness alone is insufficient.",
                f"At least {math.ceil(count * 0.75)} of {count} plans use causal_visibility=direct and visibly cast approved pressure characters when the contract names them.",
                f"At least {math.ceil(count * 0.5)} of {count} plans show the complete pressure-character relationship when the contract names people.",
                f"At least {math.ceil(count * 0.75)} of {count} plans set protagonist_action_visible=true and depict a concrete active response.",
                "At most one plan may use causal_visibility=indirect; it must still show specific source-bound evidence of the cause and a readable protagonist consequence.",
                "Use a different conflict_delivery in every plan, such as direct event, spatial opposition, reflection, embedded typography, environmental trace, or another story-specific solution; these are examples, not a fixed template.",
            ])
        if profile_version.casefold().startswith(("cover-profiles.v8", "cover-profiles.v9")):
            plan_contract["field_rules"].update({
                "frozen_action": "one physically possible captured beat, one primary action per person; before/after are context, not extra simultaneous actions; describe the actor's intention and motivated response rather than a symbolic pose",
                "gaze_graph": "each visible person has one visible target of attention at this instant; distinguish listening, avoidance, concern or divided loyalty through supported eyelines, not everybody staring at camera or exchanging identical glares",
                "blocking": "natural weight distribution, balance, shoulder/hip orientation, relaxed joints and believable hand/prop contact; preserve story hierarchy without mandatory foreground/background or arranging every person and object for frontal display",
                "depth_plan": "physically coherent focus falloff; focal face and essential causal gesture remain legible while secondary detail resolves naturally, not identical pore-level sharpness on every face, hand, fabric and prop",
                "art_style": "live-action photographic campaign still with unselfconscious actor behavior; premium composition without beauty-ad skin, exaggerated wrinkles, showroom perfection or synthetic rim outlines",
            })
        plan_contract["field_rules"].update({
            "story_evidence_refs": "non-empty exact references copied from base_evidence_refs or visual_evidence_ledger; every plan must include at least one exact reference also present in core_conflict_visual_contract.evidence_refs",
            "causal_visibility": "exactly direct or indirect; direct means the source of pressure itself is visually readable, not merely inferred from sadness",
            "conflict_delivery": "one concise, plan-specific description of how cause and consequence share the visual reading path; unique across the portfolio",
            "conflict_read": "the one-sentence story understood from the thumbnail, naming cause and consequence without revealing the ending",
            "cause_signal": "specific visible person, relationship action, institution, environmental force, or evidence object causing the protagonist's emotion",
            "consequence_signal": "specific visible emotional or practical consequence carried by the protagonist",
            "conflict_character_ids": "approved pressure_character_ids actually visible in this plan; each id must also appear in cast",
            "protagonist_action_visible": "boolean; true only when the protagonist performs a concrete story-supported response rather than posing with a mood",
        })
        if uses_story_policy(profile_version):
            policy = story_cast_policy(brief, count)
            contract["story_cast_policy"] = policy
            contract["core_conflict_visual_contract"].update({
                "characters_explicitly_named_in_core_conflict": [],
                "source_approved_pressure_character_ids": policy["pressure_character_ids"],
                "explicit_character_rule": "Include all source_approved_pressure_character_ids in the contract. Additional pressure people require explicit story evidence; co-leads and allies are not pressure merely because they are mentioned. Each plan selects only the causal actors belonging to its scene.",
            })
            # Replace older single-anchor and one-ensemble guidance, rather than
            # leaving conflicting instructions for the director to reconcile.
            plan_contract["portfolio_rules"] = [
                rule for rule in plan_contract["portfolio_rules"]
                if not any(fragment in rule for fragment in (
                    "Every plan includes the reader-anchor", "Object-led, environment-led",
                    "When more than one approved", "complete approved causal relationship",
                    "Other plans may show direct pressure",
                ))
            ] + [
                "Every plan has a visible focal person selected from story_cast_policy.focal_character_ids; co-leads may share the scene or carry different directions.",
                f"At least {policy['minimum_relationship_plans']} plans visibly stage two or more central_character_ids interacting or reacting in one source-supported event. At most one solo direction for relationship-central stories. Tiny portraits on a phone, anonymous silhouettes and decorative background figures do not satisfy this rule.",
                f"Use at least {policy['minimum_distinct_cast_sets']} distinct central cast subsets among relationship scenes. Choose which relationship exposes each different causal layer; do not crowd every character into every cover.",
                "When require_colead_scene is true, include a scene of two co-leads acting or reacting together. When require_direct_pressure_scene is true, include a direct encounter between an approved focal lead and a primary_pressure_character_id.",
                "The picture must answer: who is connected, who chooses or excludes whom, and who bears the consequence. The title, legible document text, private backstory and generic sadness cannot carry this information instead of visible behavior.",
                "Use distinct story moments and relationship geometry (alignment, exclusion, refusal, discovery, shared action) only as the evidence supports. Do not invent infidelity, violence, kinship, an encounter or a resolution to improve visual drama.",
                "All people in cast must be substantial participants in the scene with distinguishable gestures and eyelines at thumbnail size, not hidden behind title text or reduced to background decoration.",
            ]
            plan_contract["field_rules"].update({
                "cast": "source-approved central cast subset for this scene, including its focal protagonist; satisfy story_cast_policy across the portfolio",
                "focal_character_id": "one of story_cast_policy.focal_character_ids, also present in cast",
                "conflict_read": "one sentence naming the visible relationship, action/exclusion, and consequence a reader can infer from the image without reading the title or small text; no invisible financial/legal explanation",
            })
        return contract
