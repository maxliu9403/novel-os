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
        if not isinstance(plans, list) or len(plans) != count:
            raise CoverDirectionError(f"cover director must return exactly {count} plans")
        try:
            return ArtDirectionSet.from_dict(payload, brief_sha256=brief.source_prompt_sha256)
        except (TypeError, ValueError) as exc:
            raise CoverDirectionError(f"cover director schema validation failed: {exc}") from exc

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a structured cover art director. Return JSON only. Use only approved "
            "story facts and character ids. Never infer ethnicity, age, class, nationality, "
            "new characters, unsupported spoilers, or artist styles."
        )

    @staticmethod
    def _user_prompt(brief: CoverBriefV2, count: int) -> str:
        return json.dumps({
            "task": "Create a direction set of continuous cinematic scene plans.",
            "count": count,
            "brief": brief.to_dict(),
            "required_hook_types": [
                "emotional_identification", "relationship_tension", "evidence_reveal", "irreversible_moment",
            ],
        }, ensure_ascii=False, sort_keys=True)
