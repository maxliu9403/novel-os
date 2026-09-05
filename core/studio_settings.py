"""Studio-level LLM settings (local product). Persists next to the DB / projects root."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    from .image_binary import aspect_ratio_matches
except ImportError:  # pragma: no cover - legacy top-level core imports
    from image_binary import aspect_ratio_matches

# Keys we are willing to set from the Studio UI into the process env.
_ENV_KEYS = (
    "NOVEL_OS_LLM_PROVIDER",
    "NOVEL_OS_MODEL",
    "NOVEL_OS_API_KEY",
    "NOVEL_OS_BASE_URL",
    "NOVEL_OS_MAX_TOKENS",
    "NOVEL_OS_COVER_BASE_URL",
    "NOVEL_OS_COVER_API_KEY",
    "NOVEL_OS_COVER_MODEL",
    "NOVEL_OS_COVER_SIZE",
    "NOVEL_OS_COVER_QUALITY",
    "NOVEL_OS_COVER_FORMAT",
    "NOVEL_OS_COVER_COUNT",
    "NOVEL_OS_COVER_TIMEOUT_SECONDS",
    "NOVEL_OS_COVER_DIRECTOR_PROVIDER",
    "NOVEL_OS_COVER_DIRECTOR_MODEL",
    "NOVEL_OS_COVER_DIRECTOR_BASE_URL",
    "NOVEL_OS_COVER_DIRECTOR_API_KEY",
    "NOVEL_OS_COVER_DIRECTOR_TIMEOUT_SECONDS",
    "NOVEL_OS_COVER_DIRECTOR_REASONING_EFFORT",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
)

_COVER_SIZE = "2048x3072"
_COVER_MODEL = "gpt-image-2"
DEFAULT_COVER_TIMEOUT_SECONDS = 300.0
_COVER_DIRECTOR_TIMEOUT_SECONDS = 600.0
_COVER_QUALITIES = {"low", "medium", "high", "auto"}
_COVER_FORMATS = {"png", "jpeg"}
_COVER_SIZE_PATTERN = re.compile(r"^(\d+)x(\d+)$", re.IGNORECASE)


def _validate_cover_size(size: str) -> str:
    match = _COVER_SIZE_PATTERN.fullmatch(size.strip())
    if match is None:
        raise ValueError("Cover size must be widthxheight with a portrait 2:3 ratio")
    width, height = (int(value) for value in match.groups())
    if not aspect_ratio_matches(width, height):
        raise ValueError("Cover size must preserve portrait 2:3 aspect ratio")
    return f"{width}x{height}"


@dataclass(frozen=True)
class CoverSettings:
    base_url: str
    api_key: str
    provider: str = "openai_compatible"
    model: str = "gpt-image-2"
    size: str = _COVER_SIZE
    quality: str = "high"
    output_format: str = "jpeg"
    count: int = 4
    timeout_seconds: float = DEFAULT_COVER_TIMEOUT_SECONDS
    inherits_base_url: bool = False
    inherits_api_key: bool = False


@dataclass(frozen=True)
class CoverDirectorSettings:
    provider: str
    model: str
    base_url: str
    api_key: str
    timeout_seconds: float = _COVER_DIRECTOR_TIMEOUT_SECONDS
    reasoning_effort: str = ""
    inherits_writing: bool = True


_PROVIDER_KEY_NAMES = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def resolve_cover_director_settings(
    source: Mapping[str, Any] | None = None,
) -> CoverDirectorSettings:
    """Resolve planning settings independently from the final image model."""
    if source is None:
        try:
            from .provider_settings import load_configuration, resolve_text_route
        except ImportError:  # pragma: no cover - legacy top-level imports
            from provider_settings import load_configuration, resolve_text_route
        configuration = load_configuration()
        if configuration.get("source") == "v2":
            route = resolve_text_route("cover_director")
            director_route = configuration.get("text_routes", {}).get(
                "cover_director", {}
            )
            values: dict[str, Any] = dict(os.environ)
            values.update(load_settings())
            return CoverDirectorSettings(
                provider=str(route["provider"]),
                model=str(route["model"]),
                base_url=str(route["base_url"]).rstrip("/"),
                api_key=str(route["api_key"]),
                timeout_seconds=_cover_director_timeout(values),
                reasoning_effort=str(route.get("reasoning_effort") or ""),
                inherits_writing=bool(director_route.get("inherits_default", True)),
            )
    if source is None:
        values: dict[str, Any] = dict(os.environ)
        values.update(load_settings())
    else:
        values = dict(source)

    writing_provider = str(values.get("NOVEL_OS_LLM_PROVIDER") or "").strip()
    provider = str(
        values.get("NOVEL_OS_COVER_DIRECTOR_PROVIDER") or writing_provider
    ).strip()
    model = str(
        values.get("NOVEL_OS_COVER_DIRECTOR_MODEL")
        or values.get("NOVEL_OS_MODEL")
        or ""
    ).strip()
    base_url = str(
        values.get("NOVEL_OS_COVER_DIRECTOR_BASE_URL")
        or values.get("NOVEL_OS_BASE_URL")
        or ""
    ).strip().rstrip("/")
    native_key_name = _PROVIDER_KEY_NAMES.get(provider or writing_provider, "")
    api_key = str(
        values.get("NOVEL_OS_COVER_DIRECTOR_API_KEY")
        or values.get("NOVEL_OS_API_KEY")
        or (values.get(native_key_name) if native_key_name else "")
        or ""
    ).strip()
    timeout = _cover_director_timeout(values)
    reasoning_effort = _cover_director_reasoning_effort(values, provider)
    independent = any(
        str(values.get(key) or "").strip()
        for key in (
            "NOVEL_OS_COVER_DIRECTOR_PROVIDER",
            "NOVEL_OS_COVER_DIRECTOR_MODEL",
            "NOVEL_OS_COVER_DIRECTOR_BASE_URL",
            "NOVEL_OS_COVER_DIRECTOR_API_KEY",
        )
    )
    return CoverDirectorSettings(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout,
        reasoning_effort=reasoning_effort,
        inherits_writing=not independent,
    )


def _cover_director_timeout(values: Mapping[str, Any]) -> float:
    raw = values.get("NOVEL_OS_COVER_DIRECTOR_TIMEOUT_SECONDS")
    try:
        timeout = float(
            _COVER_DIRECTOR_TIMEOUT_SECONDS if raw in (None, "") else raw
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Cover Director timeout must be a positive number") from exc
    if timeout <= 0:
        raise ValueError("Cover Director timeout must be a positive number")
    return timeout


def _cover_director_reasoning_effort(
    values: Mapping[str, Any], provider: str,
) -> str:
    effort = str(
        values.get("NOVEL_OS_COVER_DIRECTOR_REASONING_EFFORT") or ""
    ).strip().lower()
    if not effort and provider.casefold() == "codex":
        return "medium"
    if effort not in {"", "low", "medium", "high", "xhigh", "max", "ultra"}:
        raise ValueError("Cover Director reasoning effort is invalid")
    return effort


def cover_director_status(source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return Director routing without exposing either inherited or dedicated keys."""
    settings = resolve_cover_director_settings(source)
    return {
        "provider": settings.provider,
        "model": settings.model,
        "base_url": settings.base_url,
        "has_api_key": bool(settings.api_key),
        "timeout_seconds": settings.timeout_seconds,
        "reasoning_effort": settings.reasoning_effort,
        "inherits_writing": settings.inherits_writing,
    }


def resolve_cover_settings(source: Mapping[str, Any] | None = None) -> CoverSettings:
    """Resolve image settings without mutating writing-model configuration."""
    if source is None:
        try:
            from .provider_settings import load_configuration, resolve_image_profile
        except ImportError:  # pragma: no cover - legacy top-level imports
            from provider_settings import load_configuration, resolve_image_profile
        configuration = load_configuration()
        if configuration.get("source") == "v2":
            profile = resolve_image_profile("cover")
            normalized = validate_cover_parameters(profile)
            return CoverSettings(
                base_url=str(profile.get("base_url") or "").rstrip("/"),
                api_key=str(profile.get("api_key") or ""),
                provider=str(profile.get("provider") or "openai_compatible"),
                model=normalized["model"],
                size=normalized["size"],
                quality=normalized["quality"],
                output_format=normalized["output_format"],
                count=normalized["count"],
                timeout_seconds=normalized["timeout_seconds"],
            )
    if source is None:
        values: dict[str, Any] = dict(os.environ)
        values.update(load_settings())
    else:
        values = dict(source)

    cover_base_url = str(values.get("NOVEL_OS_COVER_BASE_URL") or "").strip()
    writing_base_url = str(values.get("NOVEL_OS_BASE_URL") or "").strip()
    cover_api_key = str(values.get("NOVEL_OS_COVER_API_KEY") or "").strip()
    writing_api_key = str(
        values.get("NOVEL_OS_API_KEY") or values.get("OPENAI_API_KEY") or ""
    ).strip()

    normalized = validate_cover_parameters({
        "model": values.get("NOVEL_OS_COVER_MODEL") or _COVER_MODEL,
        "size": values.get("NOVEL_OS_COVER_SIZE") or _COVER_SIZE,
        "quality": values.get("NOVEL_OS_COVER_QUALITY") or "high",
        "output_format": values.get("NOVEL_OS_COVER_FORMAT") or "jpeg",
        "count": values.get("NOVEL_OS_COVER_COUNT") or 4,
        "timeout_seconds": (
            values.get("NOVEL_OS_COVER_TIMEOUT_SECONDS")
            or DEFAULT_COVER_TIMEOUT_SECONDS
        ),
    })

    return CoverSettings(
        base_url=(cover_base_url or writing_base_url or "https://api.openai.com/v1").rstrip("/"),
        api_key=cover_api_key or writing_api_key,
        model=normalized["model"],
        size=normalized["size"],
        quality=normalized["quality"],
        output_format=normalized["output_format"],
        count=normalized["count"],
        timeout_seconds=normalized["timeout_seconds"],
        inherits_base_url=not bool(cover_base_url) and bool(writing_base_url),
        inherits_api_key=not bool(cover_api_key) and bool(writing_api_key),
    )


def validate_cover_parameters(source: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize cover-only generation parameters."""
    size = _validate_cover_size(str(source.get("size") or _COVER_SIZE))

    quality = str(source.get("quality") or "high").strip().lower()
    if quality not in _COVER_QUALITIES:
        raise ValueError(
            f"Unknown cover quality '{quality}'; choose {', '.join(sorted(_COVER_QUALITIES))}"
        )

    output_format = str(source.get("output_format") or "jpeg").strip().lower()
    if output_format == "webp":
        output_format = "jpeg"
    if output_format not in _COVER_FORMATS:
        raise ValueError(
            f"Unknown cover format '{output_format}'; choose {', '.join(sorted(_COVER_FORMATS))}"
        )

    try:
        raw_count = source.get("count")
        count = int(4 if raw_count in (None, "") else raw_count)
    except (TypeError, ValueError) as exc:
        raise ValueError("Cover count must be an integer between 3 and 5") from exc
    if count < 3 or count > 5:
        raise ValueError("Cover count must be between 3 and 5")

    try:
        raw_timeout = source.get("timeout_seconds")
        timeout = float(
            DEFAULT_COVER_TIMEOUT_SECONDS
            if raw_timeout in (None, "")
            else raw_timeout
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Cover timeout must be a positive number") from exc
    if timeout <= 0:
        raise ValueError("Cover timeout must be a positive number")

    model = str(source.get("model") or _COVER_MODEL).strip()
    if not model:
        raise ValueError("Cover model is required")
    return {
        "model": model,
        "size": size,
        "quality": quality,
        "output_format": output_format,
        "count": count,
        "timeout_seconds": timeout,
    }


def cover_status(source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the safe, UI-facing cover configuration projection."""
    try:
        settings = resolve_cover_settings(source)
        director = cover_director_status(source)
    except ValueError as exc:
        return {
            "configured": False,
            "has_api_key": False,
            "base_url": "",
            "model": "gpt-image-2",
            "size": _COVER_SIZE,
            "quality": "high",
            "output_format": "jpeg",
            "count": 4,
            "timeout_seconds": DEFAULT_COVER_TIMEOUT_SECONDS,
            "inherits_base_url": False,
            "inherits_api_key": False,
            "director_provider": "",
            "director_model": "",
            "director_base_url": "",
            "director_has_api_key": False,
            "director_timeout_seconds": _COVER_DIRECTOR_TIMEOUT_SECONDS,
            "director_reasoning_effort": "",
            "director_inherits_writing": True,
            "error": str(exc),
        }
    has_key = bool(settings.api_key)
    configured = (
        settings.provider == "codex"
        or (has_key and bool(settings.base_url))
    )
    return {
        "configured": configured,
        "has_api_key": has_key,
        "base_url": settings.base_url,
        "model": settings.model,
        "size": settings.size,
        "quality": settings.quality,
        "output_format": settings.output_format,
        "count": settings.count,
        "timeout_seconds": settings.timeout_seconds,
        "inherits_base_url": settings.inherits_base_url,
        "inherits_api_key": settings.inherits_api_key,
        "director_provider": director["provider"],
        "director_model": director["model"],
        "director_base_url": director["base_url"],
        "director_has_api_key": director["has_api_key"],
        "director_timeout_seconds": director["timeout_seconds"],
        "director_reasoning_effort": director["reasoning_effort"],
        "director_inherits_writing": director["inherits_writing"],
        "error": None if configured else "Add an image provider API key or reuse a shared connection.",
    }

PRESETS: dict[str, dict[str, str]] = {
    "quality": {
        "label": "Quality",
        "hint": "Best prose and planning (Claude / strong cloud).",
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "mature_capable": "false",
    },
    "fast": {
        "label": "Fast",
        "hint": "Cheaper / quicker drafts.",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "mature_capable": "false",
    },
    "local": {
        "label": "Local",
        "hint": "Ollama / LM Studio private, mature-capable if your model is.",
        "provider": "ollama",
        "model": "llama3.2",
        "mature_capable": "true",
    },
    "mature": {
        "label": "Mature-capable",
        "hint": "OpenRouter / uncensored routes. Not a hosted NSFW model.",
        "provider": "openrouter",
        "model": "deepseek/deepseek-chat",
        "mature_capable": "true",
    },
}


def settings_path() -> Path:
    root = Path(os.environ.get("NOVEL_OS_SETTINGS_PATH", "./studio_settings.json"))
    return root


def load_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def apply_to_environ(data: dict[str, Any]) -> None:
    """Push saved studio settings into process env (does not wipe unset keys)."""
    for key in _ENV_KEYS:
        val = data.get(key)
        if val is None or val == "":
            continue
        os.environ[key] = str(val)
    preset = data.get("preset")
    if preset and preset in PRESETS and not data.get("NOVEL_OS_LLM_PROVIDER"):
        p = PRESETS[preset]
        os.environ.setdefault("NOVEL_OS_LLM_PROVIDER", p["provider"])
        os.environ.setdefault("NOVEL_OS_MODEL", p["model"])


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    for k, v in patch.items():
        if v is None:
            current.pop(k, None)
        else:
            current[k] = v
    # Apply preset defaults when switching preset without overriding explicit model
    preset = current.get("preset")
    if preset in PRESETS:
        p = PRESETS[preset]
        if patch.get("preset") and not patch.get("NOVEL_OS_LLM_PROVIDER"):
            current["NOVEL_OS_LLM_PROVIDER"] = p["provider"]
        if patch.get("preset") and not patch.get("NOVEL_OS_MODEL"):
            current["NOVEL_OS_MODEL"] = p["model"]
        current["mature_capable"] = p["mature_capable"] == "true"
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    apply_to_environ(current)
    return current


def llm_status() -> dict[str, Any]:
    """Resolve current provider without raising (for Studio UI)."""
    from core.llm_client import LLMClient, LLMError

    saved = load_settings()
    apply_to_environ(saved)
    try:
        from .provider_settings import apply_default_route_to_environ
    except ImportError:  # pragma: no cover - legacy top-level imports
        from provider_settings import apply_default_route_to_environ
    apply_default_route_to_environ()
    configured = True
    error: Optional[str] = None
    provider = os.environ.get("NOVEL_OS_LLM_PROVIDER") or ""
    model = os.environ.get("NOVEL_OS_MODEL") or ""
    try:
        client = LLMClient()
        provider = client.provider
        model = client.model
    except LLMError as e:
        configured = False
        error = str(e)
    except Exception as e:  # pragma: no cover defensive
        configured = False
        error = f"{type(e).__name__}: {e}"

    return {
        "configured": configured,
        "provider": provider,
        "model": model,
        "preset": saved.get("preset"),
        "mature_capable": bool(saved.get("mature_capable", False)),
        "error": error,
        "presets": [
            {
                "id": k,
                "label": v["label"],
                "hint": v["hint"],
                "provider": v["provider"],
                "model": v["model"],
                "mature_capable": v["mature_capable"] == "true",
            }
            for k, v in PRESETS.items()
        ],
        "onboarding_completed": bool(saved.get("onboarding_completed", False)),
    }
