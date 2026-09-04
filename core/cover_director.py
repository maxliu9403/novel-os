"""Structured Art Director boundary for cover scene plans."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from .cover_models_v2 import ArtDirectionSet, CoverBriefV2
from .cover_validator import ValidationFinding, validate_direction


class CoverDirectionError(ValueError):
    """A direction response cannot safely reach image generation."""


class CoverArtDirector:
    _MAX_SEMANTIC_REPAIR_ATTEMPTS = 2

    def __init__(
        self,
        *,
        complete: Callable[[str, str], str] | None = None,
        model: str = "",
        profile_version: str = "cover-profiles.v2",
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

    def plan(self, brief: CoverBriefV2, *, count: int) -> ArtDirectionSet:
        if not 3 <= count <= 5:
            raise CoverDirectionError("Cover direction count must be between 3 and 5")
        if self._fixture is not None:
            return self._direction_from_payload(dict(self._fixture), brief=brief, count=count)
        if self._complete is not None:
            user_prompt = self._user_prompt(brief, count)
            last_direction: ArtDirectionSet | None = None
            for attempt in range(self._MAX_SEMANTIC_REPAIR_ATTEMPTS + 1):
                raw = self._complete_response(user_prompt)
                payload = self._decode_response(raw)
                direction = self._direction_from_payload(payload, brief=brief, count=count)
                findings = validate_direction(brief, direction)
                if not findings:
                    return direction
                last_direction = direction
                if attempt < self._MAX_SEMANTIC_REPAIR_ATTEMPTS:
                    user_prompt = self._repair_prompt(
                        brief=brief,
                        count=count,
                        previous_payload=payload,
                        findings=findings,
                    )
            assert last_direction is not None
            # Preserve the existing API contract: the route reports semantic
            # validation as a 400 and does not persist the rejected direction.
            return last_direction
        raise CoverDirectionError("cover director is not configured")

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
    ) -> ArtDirectionSet:
        payload = dict(source)
        payload["director_model"] = str(payload.get("director_model") or self.model or "fixture-director")
        payload["profile_version"] = str(payload.get("profile_version") or self.profile_version)
        plans = payload.get("plans")
        if not isinstance(plans, list):
            raise CoverDirectionError("cover director response field 'plans' must be a list")
        if len(plans) != count:
            raise CoverDirectionError(
                f"cover director plan count mismatch: requested {count}, received {len(plans)}"
            )
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
    ) -> str:
        return json.dumps({
            "task": (
                "Repair the previous cover direction JSON. Correct only the listed validation "
                "findings, retain distinct concepts and approved story facts, and return the complete "
                "JSON object with the exact response contract. Do not add commentary."
            ),
            "count": count,
            "brief": brief.to_dict(),
            "response_contract": CoverArtDirector._response_contract(brief, count),
            "previous_response": dict(previous_payload),
            "validation_findings": [
                {
                    "code": item.code,
                    "message": item.message,
                    "concept_or_evidence": item.evidence,
                }
                for item in findings
            ],
            "repair_rules": [
                "Use concept_or_evidence to repair the named concept rather than rewriting unrelated plans.",
                "For group_blocking, state foreground and background explicitly across blocking and depth_plan.",
                "Preserve concept_id, plan count, allowed character ids, and evidence references.",
            ],
        }, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a structured theatrical key-art director. Design every concept as a "
            "live-action film campaign poster built from a believable publicity still, with clear "
            "principal characters and the core story atmosphere visible in one decisive moment. Treat "
            "conflict as emotional geography: stage the person carrying the emotional consequence as the "
            "foreground anchor and, when approved facts support it, reveal the causing relationship action "
            "in a secondary depth plane. Connect cause and consequence through gaze, body direction, distance, "
            "and interrupted action rather than generic facial sadness or a flat group pose. "
            "Return exactly one JSON object that "
            "matches the supplied response_contract, without Markdown fences or prose. Use only approved "
            "story facts and character ids. Never infer ethnicity, age, class, nationality, "
            "new characters, unsupported spoilers, or artist styles."
        )

    @staticmethod
    def _user_prompt(brief: CoverBriefV2, count: int) -> str:
        return json.dumps({
            "task": (
                "Create distinct continuous cinematic scene plans for prestige theatrical poster "
                "key art; prioritize clear principal people, centered title-safe space, and the "
                "story's core emotional conflict. Make the source of the emotion visually legible in the "
                "same moment whenever approved cast and evidence allow it."
            ),
            "count": count,
            "brief": brief.to_dict(),
            "response_contract": CoverArtDirector._response_contract(brief, count),
        }, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _response_contract(brief: CoverBriefV2, count: int) -> dict[str, Any]:
        hook_types = [
            "emotional_identification",
            "relationship_tension",
            "evidence_reveal",
            "irreversible_moment",
            "environmental_pressure",
        ][:count]
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
        return {
            "top_level_required_fields": [
                "schema_version", "director_model", "profile_version", "plans",
                "visual_assumptions",
            ],
            "fixed_values": {
                "schema_version": 1,
                "director_model": "configured model id or cover-art-director",
                "profile_version": "cover-profiles.v2",
            },
            "allowed_character_ids": character_ids,
            "required_character_ids": required_character_ids,
            "optional_conflict_character_ids": optional_conflict_character_ids,
            "allowed_evidence_refs": evidence_refs,
            "plans": {
                "exact_count": count,
                "required_hook_types": hook_types,
                "one_unique_hook_type_per_plan": True,
                "one_unique_visual_strategy_per_plan": True,
                "cast_must_include_every_required_character_id": True,
                "portfolio_rules": [
                    (
                        "When approved optional conflict characters and evidence can expose the source of the "
                        "emotion, at least one plan uses a causal conflict tableau: foreground emotional "
                        "consequence plus background causal relationship action in one continuous scene."
                    ),
                    (
                        "Across the set, vary who witnesses, chooses, leaves, refuses, or reaches; changing only "
                        "palette, pose, crop, or prop does not create a distinct emotional story."
                    ),
                ],
                "required_fields": [
                    "concept_id", "visual_strategy", "story_evidence_refs", "cast",
                    "focal_character_id", "moment_before", "frozen_action", "moment_after",
                    "gaze_graph", "blocking", "environment_anchors", "primary_prop",
                    "shot_scale", "camera_height", "lens", "depth_plan",
                    "motivated_lighting", "color_script", "title_safe_zone", "visual_hook",
                ],
                "visual_hook_required_fields": [
                    "hook_type", "first_glance_subject", "open_question", "identity_anchor",
                    "genre_signal", "reader_promise", "target_emotion", "misleading_risk",
                    "expected_thumbnail_read",
                ],
                "field_rules": {
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
