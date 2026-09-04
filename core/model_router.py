"""Role-specific LLM configuration with global Novel OS fallbacks."""

from __future__ import annotations

import os
from typing import Final

from llm_client import LLMClient


class ModelRouter:
    """Resolve one client configuration from role and global environment fields.

    The router intentionally does not cache. ``NovelOrchestrator`` owns client
    lifetime, while each direct ``client_for`` call observes the current
    environment. API keys remain entirely under ``LLMClient`` ownership.
    """

    _ROLE_ALIASES: Final = {
        "scribe": "writer",
        "continuity_guardian": "guardian",
        "style_curator": "style",
    }
    _ROLES: Final = frozenset(
        {"writer", "architect", "editor", "guardian", "style", "judge"}
    )
    _GLOBAL_ENV: Final = {
        "MODEL": "NOVEL_OS_MODEL",
        "PROVIDER": "NOVEL_OS_LLM_PROVIDER",
        "BASE_URL": "NOVEL_OS_BASE_URL",
        "MAX_TOKENS": "NOVEL_OS_MAX_TOKENS",
    }

    @classmethod
    def normalize_role(cls, role: str) -> str:
        if not isinstance(role, str) or not role.strip():
            raise ValueError("role must be a supported nonblank agent or role name")
        normalized = role.strip().lower()
        normalized = cls._ROLE_ALIASES.get(normalized, normalized)
        if normalized not in cls._ROLES:
            raise ValueError(f"unknown model role '{role}'")
        return normalized

    @staticmethod
    def _nonblank_env(name: str) -> str | None:
        value = os.environ.get(name)
        if value is None or not value.strip():
            return None
        return value

    @classmethod
    def _field_for(cls, role: str, suffix: str) -> str | None:
        return cls._nonblank_env(
            f"NOVEL_OS_{role.upper()}_{suffix}"
        ) or cls._nonblank_env(cls._GLOBAL_ENV[suffix])

    def client_for(self, role: str) -> LLMClient:
        normalized = self.normalize_role(role)
        try:
            try:
                from .provider_settings import (
                    ProviderSettingsError,
                    load_configuration,
                    resolve_text_route,
                )
            except ImportError:  # pragma: no cover - legacy top-level imports
                from provider_settings import (  # type: ignore
                    ProviderSettingsError,
                    load_configuration,
                    resolve_text_route,
                )
            configuration = load_configuration()
            if configuration.get("source") == "v2":
                route = resolve_text_route(normalized)
                return LLMClient(
                    provider=route["provider"] or None,
                    model=route["model"] or None,
                    base_url=route["base_url"] or None,
                    api_key=route["api_key"] or None,
                    max_tokens=route["max_tokens"],
                )
        except ProviderSettingsError:
            if configuration.get("source") == "v2":
                raise
        raw_max_tokens = self._field_for(normalized, "MAX_TOKENS")
        max_tokens = int(raw_max_tokens) if raw_max_tokens is not None else None
        return LLMClient(
            provider=self._field_for(normalized, "PROVIDER"),
            model=self._field_for(normalized, "MODEL"),
            base_url=self._field_for(normalized, "BASE_URL"),
            max_tokens=max_tokens,
        )

    def __repr__(self) -> str:
        return "ModelRouter()"


__all__ = ["ModelRouter"]
