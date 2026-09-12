"""Role-specific LLM configuration with global Novel OS fallbacks."""

from __future__ import annotations

import os
from urllib.parse import urlsplit
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

    def client_for(self, role: str, *, timeout_seconds: float | None = None) -> LLMClient:
        normalized = self.normalize_role(role)
        options = {"timeout_seconds": timeout_seconds} if timeout_seconds is not None else {}
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
                client = LLMClient(
                    provider=route["provider"] or None,
                    model=route["model"] or None,
                    base_url=route["base_url"] or None,
                    api_key=route["api_key"] or None,
                    max_tokens=route["max_tokens"],
                    reasoning_effort=route["reasoning_effort"] or None,
                    **options,
                )
                client.configuration_id = route.get("connection_id", "")
                return client
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
            **options,
        )

    def __repr__(self) -> str:
        return "ModelRouter()"

    @staticmethod
    def public_snapshot(client):
        # P1 keeps an identical private implementation in its frozen runtime.
        # Editing that file solely to deduplicate would invalidate existing run
        # implementation hashes. Unify only with explicit schema compatibility.
        configured = str(getattr(client, "snapshot_configured_base_url", None)
                         if getattr(client, "snapshot_configured_base_url", None) is not None
                         else getattr(client, "_explicit_base_url", "") or "")
        effective = str(getattr(getattr(client, "_backend", None), "base_url", "") or
                        getattr(client, "_explicit_base_url", "") or "")
        azure_endpoint = str(getattr(client, "azure_endpoint", "") or "")
        for endpoint in (configured, effective, azure_endpoint):
            parts = urlsplit(endpoint)
            if parts.username or parts.password or parts.query or parts.fragment:
                raise ValueError("review endpoint must not embed credentials or query parameters")
        return {"provider": str(getattr(client, "provider_name", "") or getattr(client, "provider", "")),
                "model": str(getattr(client, "model", "")),
                "reasoning_effort": str(getattr(client, "reasoning_effort", "") or ""),
                "max_tokens": getattr(client, "max_tokens", None),
                "timeout_seconds": getattr(client, "timeout_seconds", None),
                "base_url": effective, "configured_base_url": configured,
                "azure_endpoint": azure_endpoint,
                "azure_api_version": str(getattr(client, "azure_api_version", "") or ""),
                "connection_id": str(getattr(client, "configuration_id", "") or "")}

    @staticmethod
    def client_from_snapshot(snapshot: dict) -> LLMClient:
        """Restore public routing values; credentials stay in the provider store."""
        key = None
        if snapshot.get("connection_id"):
            try:
                from .provider_settings import resolve_connection_credential
            except ImportError:
                from provider_settings import resolve_connection_credential
            key = resolve_connection_credential(
                snapshot["connection_id"], snapshot["provider"], snapshot["configured_base_url"],
            )
        azure_options = ({"azure_endpoint": snapshot["azure_endpoint"],
                          "azure_api_version": snapshot["azure_api_version"]}
                         if snapshot["provider"] == "azure" else {})
        client = LLMClient(
            provider=snapshot["provider"], model=snapshot["model"],
            base_url=snapshot.get("base_url") or None, api_key=key or None,
            max_tokens=snapshot.get("max_tokens"),
            reasoning_effort=snapshot.get("reasoning_effort") or None,
            timeout_seconds=snapshot.get("timeout_seconds"),
            **azure_options,
        )
        client.configuration_id = snapshot.get("connection_id", "")
        # Keep the connection's configured value distinct from its SDK-effective URL.
        client.snapshot_configured_base_url = snapshot.get("configured_base_url", "")
        return client


__all__ = ["ModelRouter"]
