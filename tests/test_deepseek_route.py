"""DeepSeek is a first-class Studio text provider, not a generic image route."""

from __future__ import annotations

import pytest

from core import provider_settings, studio_settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_OS_SETTINGS_PATH", str(tmp_path / "studio_settings.json"))
    for key in studio_settings._ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_deepseek_text_route_builds_the_openai_compatible_client() -> None:
    from model_router import ModelRouter

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

    client = ModelRouter().client_for("scribe")

    assert client.provider_name == "deepseek"
    assert client.model == "deepseek-reasoner"
    assert client.supports_image_input is False
    assert str(client._backend.base_url).rstrip("/") == "https://api.deepseek.com/v1"
    client._backend.close()
