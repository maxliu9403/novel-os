"""Image-input routing stays independent from text and image generation."""

from __future__ import annotations

import pytest

from core import provider_settings, studio_settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_OS_SETTINGS_PATH", str(tmp_path / "studio_settings.json"))
    for key in studio_settings._ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _deepseek_text_route() -> dict:
    connection = provider_settings.save_connection({
        "name": "DeepSeek",
        "provider": "deepseek",
        "auth_type": "api_key",
        "base_url": "https://api.deepseek.com/v1",
        "capabilities": ["text_generation"],
        "secret_action": "replace",
        "api_key": "deepseek-secret",
    })
    provider_settings.save_text_routes([{
        "id": "default",
        "connection_id": connection["id"],
        "model": "deepseek-reasoner",
        "max_tokens": 8192,
        "inherits_default": False,
    }])
    return connection


def _vision_connection() -> dict:
    return provider_settings.save_connection({
        "name": "Vision gateway",
        "provider": "openai_compatible",
        "auth_type": "api_key",
        "base_url": "https://vision.example/v1",
        "capabilities": ["text_generation", "image_input"],
        "secret_action": "replace",
        "api_key": "vision-secret",
    })


def test_cover_evaluator_never_falls_back_to_deepseek_text_route() -> None:
    from api.routes import _cover_visual_evaluator

    _deepseek_text_route()

    evaluator = _cover_visual_evaluator()

    assert evaluator.available is False
    assert "image input" in evaluator.reason.lower()


def test_cover_evaluator_uses_the_explicit_vision_route() -> None:
    from api.routes import _cover_visual_evaluator

    _deepseek_text_route()
    vision = _vision_connection()
    provider_settings.save_vision_route("cover_review", {
        "connection_id": vision["id"], "model": "vision-model", "max_tokens": 4096,
    })

    evaluator = _cover_visual_evaluator()

    assert evaluator.available is True
    assert evaluator.provider == "openai_compatible"
    assert evaluator.model == "vision-model"


def test_poster_advisory_uses_the_vision_route_not_the_text_planner() -> None:
    from api.poster_studio import get_vision_reviewer

    _deepseek_text_route()
    with pytest.raises(provider_settings.ProviderSettingsError, match="image input"):
        get_vision_reviewer()

    vision = _vision_connection()
    provider_settings.save_vision_route("cover_review", {
        "connection_id": vision["id"], "model": "vision-model", "max_tokens": 4096,
    })
    client = get_vision_reviewer()
    assert client.provider == "openai_compatible"
    assert client.model == "vision-model"
