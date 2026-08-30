"""Structured Art Director boundary for cover scene plans."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from .cover_models_v2 import ArtDirectionSet, CoverBriefV2


class CoverDirectionError(ValueError):
    """A direction response cannot safely reach image generation."""


class CoverArtDirector:
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
            payload: Any = dict(self._fixture)
        elif self._complete is not None:
            try:
                raw = self._complete(self._system_prompt(), self._user_prompt(brief, count))
            except Exception as exc:  # provider failures must stop before image generation
                raise CoverDirectionError(f"cover director failed: {exc}") from exc
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError) as exc:
                raise CoverDirectionError("cover director returned invalid JSON") from exc
        else:
            raise CoverDirectionError("cover director is not configured")
        if not isinstance(payload, Mapping):
            raise CoverDirectionError("cover director response must be a JSON object")
        payload = dict(payload)
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
    def _system_prompt() -> str:
        return (
            "You are a structured cover art director. Return exactly one JSON object that "
            "matches the supplied response_contract, without Markdown fences or prose. Use only approved "
            "story facts and character ids. Never infer ethnicity, age, class, nationality, "
            "new characters, unsupported spoilers, or artist styles."
        )

    @staticmethod
    def _user_prompt(brief: CoverBriefV2, count: int) -> str:
        return json.dumps({
            "task": "Create a direction set of continuous cinematic scene plans.",
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
            "allowed_evidence_refs": evidence_refs,
            "plans": {
                "exact_count": count,
                "required_hook_types": hook_types,
                "one_unique_hook_type_per_plan": True,
                "one_unique_visual_strategy_per_plan": True,
                "cast_must_include_every_required_character_id": True,
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
                    "cast": "non-empty array using allowed_character_ids only",
                    "focal_character_id": "one id present in cast",
                    "gaze_graph": "non-empty array of visible gaze or attention relationships",
                    "environment_anchors": "non-empty array grounded in brief.lived_environment",
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
