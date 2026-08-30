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
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
)

_COVER_SIZE = "2048x3072"
_COVER_MODEL = "gpt-image-2"
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
    model: str = "gpt-image-2"
    size: str = _COVER_SIZE
    quality: str = "high"
    output_format: str = "jpeg"
    count: int = 4
    timeout_seconds: float = 180.0
    inherits_base_url: bool = False
    inherits_api_key: bool = False


def resolve_cover_settings(source: Mapping[str, Any] | None = None) -> CoverSettings:
    """Resolve image settings without mutating writing-model configuration."""
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

    size = _validate_cover_size(str(values.get("NOVEL_OS_COVER_SIZE") or _COVER_SIZE))

    quality = str(values.get("NOVEL_OS_COVER_QUALITY") or "high").strip().lower()
    if quality not in _COVER_QUALITIES:
        raise ValueError(
            f"Unknown cover quality '{quality}'; choose {', '.join(sorted(_COVER_QUALITIES))}"
        )

    output_format = str(values.get("NOVEL_OS_COVER_FORMAT") or "jpeg").strip().lower()
    if output_format == "webp":
        output_format = "jpeg"
    if output_format not in _COVER_FORMATS:
        raise ValueError(
            f"Unknown cover format '{output_format}'; choose {', '.join(sorted(_COVER_FORMATS))}"
        )

    try:
        count = int(values.get("NOVEL_OS_COVER_COUNT") or 4)
    except (TypeError, ValueError) as exc:
        raise ValueError("Cover count must be an integer between 3 and 5") from exc
    if count < 3 or count > 5:
        raise ValueError("Cover count must be between 3 and 5")

    try:
        timeout = float(values.get("NOVEL_OS_COVER_TIMEOUT_SECONDS") or 180)
    except (TypeError, ValueError) as exc:
        raise ValueError("Cover timeout must be a positive number") from exc
    if timeout <= 0:
        raise ValueError("Cover timeout must be a positive number")

    model = str(values.get("NOVEL_OS_COVER_MODEL") or _COVER_MODEL).strip()
    if model != _COVER_MODEL:
        raise ValueError(f"Cover model must be {_COVER_MODEL}")

    return CoverSettings(
        base_url=(cover_base_url or writing_base_url or "https://api.openai.com/v1").rstrip("/"),
        api_key=cover_api_key or writing_api_key,
        model=model,
        size=size,
        quality=quality,
        output_format=output_format,
        count=count,
        timeout_seconds=timeout,
        inherits_base_url=not bool(cover_base_url) and bool(writing_base_url),
        inherits_api_key=not bool(cover_api_key) and bool(writing_api_key),
    )


def cover_status(source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the safe, UI-facing cover configuration projection."""
    try:
        settings = resolve_cover_settings(source)
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
            "timeout_seconds": 180.0,
            "inherits_base_url": False,
            "inherits_api_key": False,
            "error": str(exc),
        }
    has_key = bool(settings.api_key)
    return {
        "configured": has_key and bool(settings.base_url),
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
        "error": None if has_key else "Add a cover API key or configure the shared Sub2API key.",
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
