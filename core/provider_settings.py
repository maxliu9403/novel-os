"""Versioned provider connections and task routing for Studio.

Connection metadata is stored in ``studio_settings.json`` while API keys live in
a sibling, owner-readable secret file.  Legacy environment-style settings are
projected into the v2 shape until the first v2 mutation, so existing installs do
not need a manual migration step.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from . import studio_settings
except ImportError:  # pragma: no cover - legacy top-level imports
    import studio_settings  # type: ignore


SCHEMA_VERSION = 2
CONFIG_KEY = "model_configuration"
TEXT_CAPABILITY = "text_generation"
IMAGE_CAPABILITY = "image_generation"
TEXT_ROUTE_IDS = (
    "default",
    "architect",
    "writer",
    "editor",
    "guardian",
    "style",
    "judge",
    "cover_director",
)
REASONING_EFFORTS = ("", "low", "medium", "high", "xhigh", "max", "ultra")

PROVIDER_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "codex",
        "label": "Codex",
        "auth_type": "codex_session",
        "default_base_url": "",
        "capabilities": [TEXT_CAPABILITY, IMAGE_CAPABILITY],
        "requires_api_key": False,
        "model_placeholder": "Use Codex default",
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "auth_type": "api_key",
        "default_base_url": "https://api.openai.com/v1",
        "capabilities": [TEXT_CAPABILITY, IMAGE_CAPABILITY],
        "requires_api_key": True,
        "model_placeholder": "gpt-5.4",
    },
    {
        "id": "openai_compatible",
        "label": "OpenAI Compatible",
        "auth_type": "api_key",
        "default_base_url": "",
        "capabilities": [TEXT_CAPABILITY, IMAGE_CAPABILITY],
        "requires_api_key": True,
        "model_placeholder": "Model id",
    },
    {
        "id": "anthropic",
        "label": "Anthropic",
        "auth_type": "api_key",
        "default_base_url": "https://api.anthropic.com/v1",
        "capabilities": [TEXT_CAPABILITY],
        "requires_api_key": True,
        "model_placeholder": "claude-sonnet-4-6",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "auth_type": "api_key",
        "default_base_url": "https://openrouter.ai/api/v1",
        "capabilities": [TEXT_CAPABILITY],
        "requires_api_key": True,
        "model_placeholder": "Provider/model",
    },
    {
        "id": "ollama",
        "label": "Ollama",
        "auth_type": "none",
        "default_base_url": "http://localhost:11434/v1",
        "capabilities": [TEXT_CAPABILITY],
        "requires_api_key": False,
        "model_placeholder": "llama3.2",
    },
)
_TEMPLATES = {item["id"]: item for item in PROVIDER_TEMPLATES}
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LOCK = threading.RLock()


class ProviderSettingsError(ValueError):
    """Raised when a connection or route is invalid."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _secrets_path() -> Path:
    path = studio_settings.settings_path()
    return path.with_name(f"{path.stem}.secrets.json")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_write(path: Path, payload: Mapping[str, Any], *, secret: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600 if secret else 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if secret:
            path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_secrets() -> dict[str, str]:
    raw = _read_json(_secrets_path())
    return {str(key): str(value) for key, value in raw.items() if value}


def _save_secrets(secrets: Mapping[str, str]) -> None:
    _atomic_write(_secrets_path(), secrets, secret=True)


def _secret_ref(connection_id: str) -> str:
    return f"provider:{connection_id}:api_key"


def _legacy_key(values: Mapping[str, Any], provider: str) -> str:
    names = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    return str(
        values.get("NOVEL_OS_API_KEY")
        or values.get(names.get(provider, ""), "")
        or ""
    ).strip()


def _integer(value: Any, default: int) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _reasoning_effort(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in REASONING_EFFORTS:
        choices = ", ".join(item for item in REASONING_EFFORTS if item)
        raise ProviderSettingsError(
            f"Reasoning effort must be blank or one of: {choices}"
        )
    return normalized


def _legacy_configuration(values: Mapping[str, Any]) -> dict[str, Any]:
    provider = str(values.get("NOVEL_OS_LLM_PROVIDER") or "").strip()
    model = str(values.get("NOVEL_OS_MODEL") or "").strip()
    base_url = str(values.get("NOVEL_OS_BASE_URL") or "").strip().rstrip("/")
    key = _legacy_key(values, provider)
    connections: list[dict[str, Any]] = []
    routes = {
        route: {
            "inherits_default": route != "default",
            "reasoning_effort": "",
        }
        for route in TEXT_ROUTE_IDS
    }
    max_tokens = _integer(values.get("NOVEL_OS_MAX_TOKENS"), 8192)

    if provider or model or base_url or key:
        connection_id = "legacy-writing"
        template = _TEMPLATES.get(provider, _TEMPLATES["openai_compatible"])
        connections.append({
            "id": connection_id,
            "name": "Writing connection",
            "provider": provider or "openai_compatible",
            "auth_type": template["auth_type"],
            "base_url": base_url or template["default_base_url"],
            "image_base_url": "",
            "capabilities": list(template["capabilities"]),
            "secret_ref": "legacy:writing" if key else "",
            "created_at": "",
            "updated_at": "",
            "last_tested_at": "",
            "last_test_ok": None,
            "last_test_error": "",
            "discovered_models": [],
        })
        routes["default"] = {
            "connection_id": connection_id,
            "model": model,
            "max_tokens": max_tokens,
            "reasoning_effort": "",
            "inherits_default": False,
        }

    cover_base = str(values.get("NOVEL_OS_COVER_BASE_URL") or "").strip().rstrip("/")
    cover_key = str(values.get("NOVEL_OS_COVER_API_KEY") or "").strip()
    cover_connection = ""
    if cover_base or cover_key:
        cover_connection = "legacy-image"
        connections.append({
            "id": cover_connection,
            "name": "Cover image connection",
            "provider": "openai_compatible",
            "auth_type": "api_key",
            "base_url": cover_base or base_url or "https://api.openai.com/v1",
            "image_base_url": cover_base,
            "capabilities": [IMAGE_CAPABILITY],
            "secret_ref": (
                "legacy:cover" if cover_key
                else (connections[0].get("secret_ref", "") if connections else "")
            ),
            "created_at": "",
            "updated_at": "",
            "last_tested_at": "",
            "last_test_ok": None,
            "last_test_error": "",
            "discovered_models": [],
        })
    elif connections and IMAGE_CAPABILITY in connections[0]["capabilities"]:
        cover_connection = connections[0]["id"]

    director_values = {
        "provider": str(values.get("NOVEL_OS_COVER_DIRECTOR_PROVIDER") or "").strip(),
        "model": str(values.get("NOVEL_OS_COVER_DIRECTOR_MODEL") or "").strip(),
        "base_url": str(values.get("NOVEL_OS_COVER_DIRECTOR_BASE_URL") or "").strip().rstrip("/"),
        "api_key": str(values.get("NOVEL_OS_COVER_DIRECTOR_API_KEY") or "").strip(),
        "reasoning_effort": _reasoning_effort(
            values.get("NOVEL_OS_COVER_DIRECTOR_REASONING_EFFORT")
        ),
    }
    if any(director_values.values()) and connections:
        director_connection = connections[0]["id"]
        if director_values["provider"] or director_values["base_url"] or director_values["api_key"]:
            director_provider = director_values["provider"] or provider or "openai_compatible"
            director_template = _TEMPLATES.get(
                director_provider, _TEMPLATES["openai_compatible"]
            )
            director_connection = "legacy-cover-director"
            connections.append({
                "id": director_connection,
                "name": "Cover director connection",
                "provider": director_provider,
                "auth_type": director_template["auth_type"],
                "base_url": director_values["base_url"] or base_url or director_template["default_base_url"],
                "image_base_url": "",
                "capabilities": [TEXT_CAPABILITY],
                "secret_ref": (
                    "legacy:director" if director_values["api_key"]
                    else connections[0].get("secret_ref", "")
                ),
                "created_at": "",
                "updated_at": "",
                "last_tested_at": "",
                "last_test_ok": None,
                "last_test_error": "",
                "discovered_models": [],
            })
        routes["cover_director"] = {
            "connection_id": director_connection,
            "model": director_values["model"] or model,
            "max_tokens": max_tokens,
            "reasoning_effort": director_values["reasoning_effort"],
            "inherits_default": False,
        }

    raw_cover = {
        "model": values.get("NOVEL_OS_COVER_MODEL") or "gpt-image-2",
        "size": values.get("NOVEL_OS_COVER_SIZE") or "2048x3072",
        "quality": values.get("NOVEL_OS_COVER_QUALITY") or "high",
        "output_format": values.get("NOVEL_OS_COVER_FORMAT") or "jpeg",
        "count": values.get("NOVEL_OS_COVER_COUNT") or 4,
        "timeout_seconds": (
            values.get("NOVEL_OS_COVER_TIMEOUT_SECONDS")
            or studio_settings.DEFAULT_COVER_TIMEOUT_SECONDS
        ),
    }
    try:
        cover_parameters = studio_settings.validate_cover_parameters(raw_cover)
        cover_error = ""
    except ValueError as exc:
        cover_parameters = studio_settings.validate_cover_parameters({})
        cover_error = str(exc)

    return {
        "schema_version": SCHEMA_VERSION,
        "connections": connections,
        "text_routes": routes,
        "image_profiles": {
            "cover": {
                "connection_id": cover_connection,
                **cover_parameters,
                "configuration_error": cover_error,
            }
        },
        "source": "legacy",
    }


def load_configuration() -> dict[str, Any]:
    settings = studio_settings.load_settings()
    raw = settings.get(CONFIG_KEY)
    if isinstance(raw, dict) and raw.get("schema_version") == SCHEMA_VERSION:
        result = deepcopy(raw)
        result["source"] = "v2"
        return result
    values: dict[str, Any] = dict(os.environ)
    values.update(settings)
    return _legacy_configuration(values)


def _persist_configuration(configuration: Mapping[str, Any]) -> None:
    clean = deepcopy(dict(configuration))
    clean.pop("source", None)
    clean["schema_version"] = SCHEMA_VERSION
    secrets = _load_secrets()
    migrated_refs: set[str] = set()
    for connection in clean.get("connections", []):
        reference = str(connection.get("secret_ref") or "")
        if not reference.startswith("legacy:"):
            continue
        secret = _connection_secret(connection)
        if not secret:
            continue
        replacement = _secret_ref(str(connection["id"]))
        secrets[replacement] = secret
        connection["secret_ref"] = replacement
        migrated_refs.add(reference)
    if migrated_refs:
        _save_secrets(secrets)
    patch: dict[str, Any] = {CONFIG_KEY: clean}
    if "legacy:writing" in migrated_refs:
        patch.update({
            "NOVEL_OS_API_KEY": None,
            "OPENAI_API_KEY": None,
            "ANTHROPIC_API_KEY": None,
            "OPENROUTER_API_KEY": None,
            "DEEPSEEK_API_KEY": None,
        })
    if "legacy:cover" in migrated_refs:
        patch["NOVEL_OS_COVER_API_KEY"] = None
    if "legacy:director" in migrated_refs:
        patch["NOVEL_OS_COVER_DIRECTOR_API_KEY"] = None
    studio_settings.save_settings(patch)


def _connection(configuration: Mapping[str, Any], connection_id: str) -> dict[str, Any]:
    for connection in configuration.get("connections", []):
        if connection.get("id") == connection_id:
            return connection
    raise ProviderSettingsError(f"Unknown provider connection '{connection_id}'")


def _connection_secret(connection: Mapping[str, Any]) -> str:
    reference = str(connection.get("secret_ref") or "")
    if reference == "legacy:writing":
        values: dict[str, Any] = dict(os.environ)
        values.update(studio_settings.load_settings())
        return _legacy_key(values, str(connection.get("provider") or ""))
    if reference == "legacy:cover":
        values = dict(os.environ)
        values.update(studio_settings.load_settings())
        return str(values.get("NOVEL_OS_COVER_API_KEY") or "").strip()
    if reference == "legacy:director":
        values = dict(os.environ)
        values.update(studio_settings.load_settings())
        return str(values.get("NOVEL_OS_COVER_DIRECTOR_API_KEY") or "").strip()
    return _load_secrets().get(reference, "")


def _requires_key(provider: str, auth_type: str) -> bool:
    template = _TEMPLATES.get(provider)
    return auth_type == "api_key" and bool(template is None or template["requires_api_key"])


def _connection_status(connection: Mapping[str, Any]) -> tuple[str, str]:
    provider = str(connection.get("provider") or "")
    capabilities = set(connection.get("capabilities") or [])
    if not capabilities:
        return "invalid", "Select at least one capability"
    if provider == "codex":
        if not shutil.which("codex"):
            return "unavailable", "Codex CLI is not available in the API runtime"
        return "ready", ""
    if not str(connection.get("base_url") or "").strip():
        return "invalid", "Base URL is required"
    if _requires_key(provider, str(connection.get("auth_type") or "")) and not _connection_secret(connection):
        return "invalid", "API key is not configured"
    return "ready", ""


def _public_connection(connection: Mapping[str, Any]) -> dict[str, Any]:
    status, error = _connection_status(connection)
    return {
        "id": connection.get("id", ""),
        "name": connection.get("name", ""),
        "provider": connection.get("provider", ""),
        "auth_type": connection.get("auth_type", "api_key"),
        "base_url": connection.get("base_url", ""),
        "image_base_url": connection.get("image_base_url", ""),
        "capabilities": list(connection.get("capabilities") or []),
        "has_api_key": bool(_connection_secret(connection)),
        "status": status,
        "error": error,
        "last_tested_at": connection.get("last_tested_at", ""),
        "last_test_ok": connection.get("last_test_ok"),
        "last_test_error": connection.get("last_test_error", ""),
        "discovered_models": list(connection.get("discovered_models") or []),
    }


def configuration_status() -> dict[str, Any]:
    configuration = load_configuration()
    connections = [_public_connection(item) for item in configuration.get("connections", [])]
    routes = text_routes_status(configuration)
    profile = image_profile_status("cover", configuration)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": configuration.get("source", "v2"),
        "templates": deepcopy(list(PROVIDER_TEMPLATES)),
        "connections": connections,
        "text_routes": routes,
        "image_profiles": {"cover": profile},
    }


def save_connection(payload: Mapping[str, Any], connection_id: str | None = None) -> dict[str, Any]:
    with _LOCK:
        configuration = load_configuration()
        connections = list(configuration.get("connections", []))
        provider = str(payload.get("provider") or "").strip().lower()
        if provider not in _TEMPLATES:
            raise ProviderSettingsError(f"Unknown provider '{provider}'")
        template = _TEMPLATES[provider]
        target_id = connection_id or str(payload.get("id") or "").strip()
        if not target_id:
            stem = re.sub(r"[^a-z0-9]+", "-", provider).strip("-") or "provider"
            target_id = f"{stem}-{uuid.uuid4().hex[:8]}"
        if not _ID_PATTERN.fullmatch(target_id):
            raise ProviderSettingsError("Connection id contains unsupported characters")
        existing = next((item for item in connections if item.get("id") == target_id), None)
        if connection_id and existing is None:
            raise ProviderSettingsError(f"Unknown provider connection '{target_id}'")

        requested_capabilities = payload.get("capabilities")
        capabilities = list(dict.fromkeys(
            template["capabilities"]
            if requested_capabilities is None
            else requested_capabilities
        ))
        unsupported = set(capabilities) - set(template["capabilities"])
        if unsupported or not capabilities:
            raise ProviderSettingsError(
                f"Provider '{provider}' does not support the selected capabilities"
            )
        name = str(payload.get("name") or template["label"]).strip()
        if not name:
            raise ProviderSettingsError("Connection name is required")
        base_url = str(payload.get("base_url") or template["default_base_url"]).strip().rstrip("/")
        image_base_url = str(payload.get("image_base_url") or "").strip().rstrip("/")
        auth_type = str(payload.get("auth_type") or template["auth_type"]).strip()
        if auth_type not in {"api_key", "codex_session", "none"}:
            raise ProviderSettingsError("Unknown authorization type")
        if auth_type != template["auth_type"]:
            raise ProviderSettingsError(
                f"Provider '{provider}' requires authorization type "
                f"'{template['auth_type']}'"
            )

        now = _utc_now()
        connection = {
            "id": target_id,
            "name": name,
            "provider": provider,
            "auth_type": auth_type,
            "base_url": base_url,
            "image_base_url": image_base_url,
            "capabilities": capabilities,
            "secret_ref": (existing or {}).get("secret_ref", ""),
            "created_at": (existing or {}).get("created_at") or now,
            "updated_at": now,
            "last_tested_at": (existing or {}).get("last_tested_at", ""),
            "last_test_ok": (existing or {}).get("last_test_ok"),
            "last_test_error": (existing or {}).get("last_test_error", ""),
            "discovered_models": list((existing or {}).get("discovered_models") or []),
        }

        secret_action = str(payload.get("secret_action") or "keep")
        secrets = _load_secrets()
        old_ref = str(connection.get("secret_ref") or "")
        if secret_action == "replace":
            if auth_type != "api_key":
                raise ProviderSettingsError(
                    f"Provider '{provider}' does not accept an API key"
                )
            api_key = str(payload.get("api_key") or "").strip()
            if not api_key:
                raise ProviderSettingsError("API key is required when replacing a secret")
            reference = _secret_ref(target_id)
            secrets[reference] = api_key
            connection["secret_ref"] = reference
        elif secret_action == "clear":
            if old_ref and not old_ref.startswith("legacy:"):
                secrets.pop(old_ref, None)
            connection["secret_ref"] = ""
        elif secret_action != "keep":
            raise ProviderSettingsError("Unknown secret action")

        if existing is None:
            connections.append(connection)
        else:
            connections[connections.index(existing)] = connection
        configuration["connections"] = connections
        configuration["source"] = "v2"
        if provider != "codex" and not base_url:
            raise ProviderSettingsError("Base URL is required")
        _save_secrets(secrets)
        _persist_configuration(configuration)
        apply_default_route_to_environ(configuration)
        return _public_connection(connection)


def delete_connection(connection_id: str) -> None:
    with _LOCK:
        configuration = load_configuration()
        target = _connection(configuration, connection_id)
        used_by = []
        for route_id, route in configuration.get("text_routes", {}).items():
            if route.get("connection_id") == connection_id:
                used_by.append(route_id)
        for profile_id, profile in configuration.get("image_profiles", {}).items():
            if profile.get("connection_id") == connection_id:
                used_by.append(profile_id)
        if used_by:
            raise ProviderSettingsError(
                f"Connection is still used by: {', '.join(sorted(used_by))}"
            )
        configuration["connections"] = [
            item for item in configuration.get("connections", [])
            if item.get("id") != connection_id
        ]
        secrets = _load_secrets()
        reference = str(target.get("secret_ref") or "")
        if reference and not reference.startswith("legacy:"):
            secrets.pop(reference, None)
        _save_secrets(secrets)
        _persist_configuration(configuration)


def _route_effective(
    route_id: str,
    routes: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any]]:
    route = routes.get(route_id) if isinstance(routes.get(route_id), dict) else {}
    if route_id != "default" and route.get("inherits_default", True):
        default = routes.get("default") if isinstance(routes.get("default"), dict) else {}
        return "default", default
    return route_id, route


def text_routes_status(configuration: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    config = configuration or load_configuration()
    routes = config.get("text_routes", {})
    result = []
    for route_id in TEXT_ROUTE_IDS:
        raw = routes.get(route_id) if isinstance(routes.get(route_id), dict) else {}
        source_id, effective = _route_effective(route_id, routes)
        local_reasoning = _reasoning_effort(raw.get("reasoning_effort"))
        effective_reasoning = local_reasoning or _reasoning_effort(
            effective.get("reasoning_effort")
        )
        connection_id = str(effective.get("connection_id") or "")
        connection_name = ""
        connection_provider = ""
        configured = False
        if connection_id:
            try:
                connection = _connection(config, connection_id)
                connection_name = str(connection.get("name") or "")
                connection_provider = str(connection.get("provider") or "")
                configured = (
                    TEXT_CAPABILITY in set(connection.get("capabilities") or [])
                    and _connection_status(connection)[0] == "ready"
                )
            except ProviderSettingsError:
                configured = False
        if (
            not effective_reasoning
            and route_id == "cover_director"
            and connection_provider == "codex"
        ):
            effective_reasoning = "medium"
        result.append({
            "id": route_id,
            "connection_id": str(raw.get("connection_id") or ""),
            "model": str(raw.get("model") or ""),
            "max_tokens": int(raw.get("max_tokens") or 8192),
            "reasoning_effort": local_reasoning,
            "inherits_default": bool(raw.get("inherits_default", route_id != "default")),
            "effective_connection_id": connection_id,
            "effective_connection_name": connection_name,
            "effective_model": str(effective.get("model") or ""),
            "effective_reasoning_effort": effective_reasoning,
            "effective_source": source_id,
            "configured": configured,
        })
    return result


def save_text_routes(routes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    with _LOCK:
        configuration = load_configuration()
        current = dict(configuration.get("text_routes", {}))
        for item in routes:
            route_id = str(item.get("id") or "").strip()
            if route_id not in TEXT_ROUTE_IDS:
                raise ProviderSettingsError(f"Unknown text route '{route_id}'")
            inherits = bool(item.get("inherits_default", route_id != "default"))
            reasoning_effort = _reasoning_effort(item.get("reasoning_effort"))
            if route_id == "default" and inherits:
                raise ProviderSettingsError("The default route cannot inherit")
            if inherits:
                current[route_id] = {
                    "inherits_default": True,
                    "reasoning_effort": reasoning_effort,
                }
                continue
            connection_id = str(item.get("connection_id") or "").strip()
            connection = _connection(configuration, connection_id)
            if TEXT_CAPABILITY not in set(connection.get("capabilities") or []):
                raise ProviderSettingsError(
                    f"Connection '{connection_id}' does not support text generation"
                )
            model = str(item.get("model") or "").strip()
            if not model and connection.get("provider") != "codex":
                raise ProviderSettingsError(f"Model is required for route '{route_id}'")
            max_tokens = int(item.get("max_tokens") or 8192)
            if max_tokens < 1:
                raise ProviderSettingsError("Max tokens must be positive")
            current[route_id] = {
                "connection_id": connection_id,
                "model": model,
                "max_tokens": max_tokens,
                "reasoning_effort": reasoning_effort,
                "inherits_default": False,
            }
        if not current.get("default", {}).get("connection_id"):
            raise ProviderSettingsError("Configure the default text route")
        configuration["text_routes"] = current
        configuration["source"] = "v2"
        _persist_configuration(configuration)
        apply_default_route_to_environ(configuration)
        return text_routes_status(configuration)


def resolve_text_route(route_id: str) -> dict[str, Any]:
    aliases = {"scribe": "writer", "continuity_guardian": "guardian", "style_curator": "style"}
    normalized = aliases.get(route_id.strip().lower(), route_id.strip().lower())
    if normalized not in TEXT_ROUTE_IDS:
        raise ProviderSettingsError(f"Unknown text route '{route_id}'")
    configuration = load_configuration()
    routes = configuration.get("text_routes", {})
    raw = routes.get(normalized) if isinstance(routes.get(normalized), dict) else {}
    _, route = _route_effective(normalized, routes)
    connection_id = str(route.get("connection_id") or "")
    if not connection_id:
        raise ProviderSettingsError(f"Text route '{normalized}' is not configured")
    connection = _connection(configuration, connection_id)
    if TEXT_CAPABILITY not in set(connection.get("capabilities") or []):
        raise ProviderSettingsError("Selected connection does not support text generation")
    status, error = _connection_status(connection)
    if status != "ready":
        raise ProviderSettingsError(error or "Selected connection is not ready")
    reasoning_effort = _reasoning_effort(raw.get("reasoning_effort")) or _reasoning_effort(
        route.get("reasoning_effort")
    )
    if not reasoning_effort and normalized == "cover_director" and connection.get("provider") == "codex":
        reasoning_effort = "medium"
    return {
        "connection_id": connection_id,
        "provider": connection.get("provider", ""),
        "model": route.get("model", ""),
        "base_url": connection.get("base_url", ""),
        "api_key": _connection_secret(connection),
        "max_tokens": int(route.get("max_tokens") or 8192),
        "reasoning_effort": reasoning_effort,
    }


def apply_default_route_to_environ(configuration: Mapping[str, Any] | None = None) -> None:
    try:
        route = resolve_text_route("default") if configuration is None else _resolved_from(configuration, "default")
    except ProviderSettingsError:
        return
    os.environ["NOVEL_OS_LLM_PROVIDER"] = str(route["provider"])
    if route.get("model"):
        os.environ["NOVEL_OS_MODEL"] = str(route["model"])
    else:
        os.environ.pop("NOVEL_OS_MODEL", None)
    if route.get("base_url"):
        os.environ["NOVEL_OS_BASE_URL"] = str(route["base_url"])
    else:
        os.environ.pop("NOVEL_OS_BASE_URL", None)
    if route.get("api_key"):
        os.environ["NOVEL_OS_API_KEY"] = str(route["api_key"])
    else:
        os.environ.pop("NOVEL_OS_API_KEY", None)
    os.environ["NOVEL_OS_MAX_TOKENS"] = str(route["max_tokens"])


def _resolved_from(configuration: Mapping[str, Any], route_id: str) -> dict[str, Any]:
    routes = configuration.get("text_routes", {})
    raw = routes.get(route_id) if isinstance(routes.get(route_id), dict) else {}
    _, route = _route_effective(route_id, routes)
    connection = _connection(configuration, str(route.get("connection_id") or ""))
    return {
        "provider": connection.get("provider", ""),
        "model": route.get("model", ""),
        "base_url": connection.get("base_url", ""),
        "api_key": _connection_secret(connection),
        "max_tokens": int(route.get("max_tokens") or 8192),
        "reasoning_effort": _reasoning_effort(raw.get("reasoning_effort"))
        or _reasoning_effort(route.get("reasoning_effort")),
    }


def image_profile_status(
    profile_id: str = "cover",
    configuration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    config = configuration or load_configuration()
    profile = dict(config.get("image_profiles", {}).get(profile_id) or {})
    connection_id = str(profile.get("connection_id") or "")
    name = ""
    configured = False
    error = str(profile.get("configuration_error") or "Select an image provider connection")
    if connection_id and not profile.get("configuration_error"):
        try:
            connection = _connection(config, connection_id)
            name = str(connection.get("name") or "")
            if IMAGE_CAPABILITY not in set(connection.get("capabilities") or []):
                error = "Selected connection does not support image generation"
            else:
                status, status_error = _connection_status(connection)
                configured = status == "ready"
                error = status_error
        except ProviderSettingsError as exc:
            error = str(exc)
    return {
        "id": profile_id,
        "connection_id": connection_id,
        "connection_name": name,
        "model": str(profile.get("model") or "gpt-image-2"),
        "size": str(profile.get("size") or "2048x3072"),
        "quality": str(profile.get("quality") or "high"),
        "output_format": str(profile.get("output_format") or "jpeg"),
        "count": int(profile.get("count") or 4),
        "timeout_seconds": float(
            profile.get("timeout_seconds")
            or studio_settings.DEFAULT_COVER_TIMEOUT_SECONDS
        ),
        "configured": configured,
        "error": error,
    }


def save_image_profile(profile_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if profile_id != "cover":
        raise ProviderSettingsError(f"Unknown image profile '{profile_id}'")
    with _LOCK:
        configuration = load_configuration()
        connection_id = str(payload.get("connection_id") or "").strip()
        connection = _connection(configuration, connection_id)
        if IMAGE_CAPABILITY not in set(connection.get("capabilities") or []):
            raise ProviderSettingsError("Selected connection does not support image generation")
        # Cover-specific parameter validation remains in the cover resolver.
        normalized = studio_settings.validate_cover_parameters({
            "model": payload.get("model"),
            "size": payload.get("size"),
            "quality": payload.get("quality"),
            "output_format": payload.get("output_format"),
            "count": payload.get("count"),
            "timeout_seconds": payload.get("timeout_seconds"),
        })
        candidate = {
            "connection_id": connection_id,
            **normalized,
        }
        if connection.get("provider") == "codex" and candidate["model"] != "gpt-image-2":
            raise ProviderSettingsError(
                "Codex image generation currently requires gpt-image-2"
            )
        profiles = dict(configuration.get("image_profiles", {}))
        profiles[profile_id] = candidate
        configuration["image_profiles"] = profiles
        configuration["source"] = "v2"
        _persist_configuration(configuration)
        return image_profile_status(profile_id, configuration)


def resolve_image_profile(profile_id: str = "cover") -> dict[str, Any]:
    configuration = load_configuration()
    profile = dict(configuration.get("image_profiles", {}).get(profile_id) or {})
    connection_id = str(profile.get("connection_id") or "")
    if not connection_id:
        raise ProviderSettingsError("Select an image provider connection")
    connection = _connection(configuration, connection_id)
    if IMAGE_CAPABILITY not in set(connection.get("capabilities") or []):
        raise ProviderSettingsError("Selected connection does not support image generation")
    base_url = str(connection.get("image_base_url") or connection.get("base_url") or "").rstrip("/")
    return {
        **profile,
        "provider": connection.get("provider", "openai_compatible"),
        "base_url": base_url,
        "api_key": _connection_secret(connection),
    }


def _safe_error(error: Exception, secret: str) -> str:
    message = str(error).strip() or type(error).__name__
    if secret:
        message = message.replace(secret, "[redacted]")
    return message[:500]


def _discover_models(connection: Mapping[str, Any], timeout_seconds: float = 12.0) -> list[str]:
    provider = str(connection.get("provider") or "")
    if provider == "codex":
        command = shutil.which("codex")
        if not command:
            raise ProviderSettingsError("Codex CLI is not available in the API runtime")
        proc = subprocess.run(
            [command, "login", "status"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if proc.returncode != 0:
            raise ProviderSettingsError((proc.stderr or proc.stdout or "Codex is not logged in").strip())
        if IMAGE_CAPABILITY in set(connection.get("capabilities") or []):
            features = subprocess.run(
                [command, "features", "list"],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            enabled = any(
                line.split()[:1] == ["image_generation"] and line.split()[-1:] == ["true"]
                for line in (features.stdout or "").splitlines()
            )
            if features.returncode != 0 or not enabled:
                raise ProviderSettingsError(
                    "This Codex runtime does not expose image generation"
                )
        return []

    base_url = str(connection.get("base_url") or "").rstrip("/")
    if not base_url:
        raise ProviderSettingsError("Base URL is required")
    secret = _connection_secret(connection)
    headers = {"Accept": "application/json"}
    if provider == "anthropic":
        headers.update({"x-api-key": secret, "anthropic-version": "2023-06-01"})
    elif secret:
        headers["Authorization"] = f"Bearer {secret}"
    request = urllib.request.Request(f"{base_url}/models", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        raise ProviderSettingsError(f"Provider returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderSettingsError("Provider connection failed or timed out") from None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderSettingsError("Provider returned malformed model data") from None
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return sorted({str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")})


def test_connection(connection_id: str) -> dict[str, Any]:
    with _LOCK:
        configuration = load_configuration()
        connection = _connection(configuration, connection_id)
        status, error = _connection_status(connection)
        secret = _connection_secret(connection)
        ok = status == "ready"
        models: list[str] = []
        if ok:
            try:
                models = _discover_models(connection)
            except Exception as exc:  # return a diagnostic result, not a 500
                ok = False
                error = _safe_error(exc, secret)
        connection["last_tested_at"] = _utc_now()
        connection["last_test_ok"] = ok
        connection["last_test_error"] = "" if ok else error
        if models:
            connection["discovered_models"] = models
        configuration["source"] = "v2"
        _persist_configuration(configuration)
        return {
            "ok": ok,
            "status": "connected" if ok else "failed",
            "message": "Connection verified" if ok else error,
            "models": models,
            "tested_at": connection["last_tested_at"],
        }


__all__ = [
    "IMAGE_CAPABILITY",
    "PROVIDER_TEMPLATES",
    "ProviderSettingsError",
    "SCHEMA_VERSION",
    "TEXT_CAPABILITY",
    "TEXT_ROUTE_IDS",
    "apply_default_route_to_environ",
    "configuration_status",
    "delete_connection",
    "image_profile_status",
    "load_configuration",
    "resolve_image_profile",
    "resolve_text_route",
    "save_connection",
    "save_image_profile",
    "save_text_routes",
    "test_connection",
    "text_routes_status",
]
