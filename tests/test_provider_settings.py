from __future__ import annotations

import json
import os
import stat
import threading
import time

import pytest

from core import provider_settings, studio_settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_OS_SETTINGS_PATH", str(tmp_path / "studio_settings.json"))
    for key in studio_settings._ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield tmp_path


def _connection(**overrides):
    payload = {
        "name": "Primary models",
        "provider": "openai_compatible",
        "auth_type": "api_key",
        "base_url": "https://models.example/v1/",
        "image_base_url": "",
        "capabilities": ["text_generation", "image_generation"],
        "secret_action": "replace",
        "api_key": "provider-secret",
    }
    payload.update(overrides)
    return provider_settings.save_connection(payload)


def test_connection_secret_is_stored_separately_and_redacted(isolated_settings) -> None:
    connection = _connection()

    settings_text = studio_settings.settings_path().read_text(encoding="utf-8")
    secrets_path = isolated_settings / "studio_settings.secrets.json"
    secrets_text = secrets_path.read_text(encoding="utf-8")

    assert connection["has_api_key"] is True
    assert "provider-secret" not in settings_text
    assert "provider-secret" not in repr(connection)
    assert "provider-secret" in secrets_text
    assert stat.S_IMODE(secrets_path.stat().st_mode) == 0o600


def test_saved_key_can_be_explicitly_cleared() -> None:
    connection = _connection()

    updated = provider_settings.save_connection({
        "name": connection["name"],
        "provider": connection["provider"],
        "auth_type": connection["auth_type"],
        "base_url": connection["base_url"],
        "capabilities": connection["capabilities"],
        "secret_action": "clear",
    }, connection["id"])

    assert updated["has_api_key"] is False
    assert updated["status"] == "invalid"
    assert "API key" in updated["error"]


def test_one_connection_drives_text_and_image_routes() -> None:
    connection = _connection()

    routes = provider_settings.save_text_routes([
        {
            "id": "default",
            "connection_id": connection["id"],
            "model": "story-model",
            "max_tokens": 12000,
            "inherits_default": False,
        }
    ])
    profile = provider_settings.save_image_profile("cover", {
        "connection_id": connection["id"],
        "model": "gpt-image-2",
        "size": "1024x1536",
        "quality": "high",
        "output_format": "jpeg",
        "count": 4,
        "timeout_seconds": 240,
    })

    writer = provider_settings.resolve_text_route("writer")
    image = provider_settings.resolve_image_profile("cover")

    assert next(item for item in routes if item["id"] == "writer")["effective_source"] == "default"
    assert writer["model"] == "story-model"
    assert writer["api_key"] == "provider-secret"
    assert image["connection_id"] == connection["id"]
    assert image["api_key"] == "provider-secret"
    assert profile["configured"] is True


def test_image_profile_allows_a_provider_model_id() -> None:
    connection = _connection()

    profile = provider_settings.save_image_profile("cover", {
        "connection_id": connection["id"],
        "model": "publisher/image-v3",
        "size": "2048x3072",
        "quality": "medium",
        "output_format": "png",
        "count": 3,
        "timeout_seconds": 180,
    })

    assert profile["model"] == "publisher/image-v3"


def test_codex_login_can_back_text_and_image_profiles(monkeypatch) -> None:
    monkeypatch.setattr(provider_settings.shutil, "which", lambda name: "/usr/local/bin/codex")
    connection = provider_settings.save_connection({
        "name": "Codex subscription",
        "provider": "codex",
        "auth_type": "codex_session",
        "capabilities": ["text_generation", "image_generation"],
    })
    provider_settings.save_text_routes([{
        "id": "default",
        "connection_id": connection["id"],
        "model": "",
        "inherits_default": False,
    }])
    provider_settings.save_image_profile("cover", {
        "connection_id": connection["id"],
        "model": "gpt-image-2",
        "size": "2048x3072",
        "quality": "high",
        "output_format": "jpeg",
        "count": 4,
        "timeout_seconds": 180,
    })

    text = provider_settings.resolve_text_route("writer")
    image = studio_settings.resolve_cover_settings()

    assert text["provider"] == "codex"
    assert text["api_key"] == ""
    assert image.provider == "codex"
    assert image.api_key == ""
    assert studio_settings.cover_status()["configured"] is True


def test_codex_default_route_clears_stale_http_environment(monkeypatch) -> None:
    monkeypatch.setattr(provider_settings.shutil, "which", lambda name: "/usr/local/bin/codex")
    monkeypatch.setenv("NOVEL_OS_MODEL", "stale-model")
    monkeypatch.setenv("NOVEL_OS_BASE_URL", "https://stale.example/v1")
    monkeypatch.setenv("NOVEL_OS_API_KEY", "stale-secret")
    connection = provider_settings.save_connection({
        "name": "Codex subscription",
        "provider": "codex",
        "capabilities": ["text_generation", "image_generation"],
    })

    provider_settings.save_text_routes([{
        "id": "default",
        "connection_id": connection["id"],
        "model": "",
        "inherits_default": False,
    }])

    assert os.environ["NOVEL_OS_LLM_PROVIDER"] == "codex"
    assert "NOVEL_OS_MODEL" not in os.environ
    assert "NOVEL_OS_BASE_URL" not in os.environ
    assert "NOVEL_OS_API_KEY" not in os.environ


def test_provider_rejects_capabilities_outside_its_template() -> None:
    with pytest.raises(provider_settings.ProviderSettingsError, match="capabilities"):
        _connection(
            provider="anthropic",
            capabilities=["text_generation", "image_generation"],
        )


def test_provider_rejects_authorization_type_override() -> None:
    with pytest.raises(provider_settings.ProviderSettingsError, match="authorization type"):
        _connection(
            provider="openai",
            auth_type="none",
            capabilities=["text_generation"],
        )


def test_non_key_provider_rejects_a_secret(monkeypatch) -> None:
    monkeypatch.setattr(provider_settings.shutil, "which", lambda name: "/usr/local/bin/codex")

    with pytest.raises(provider_settings.ProviderSettingsError, match="does not accept"):
        provider_settings.save_connection({
            "name": "Codex subscription",
            "provider": "codex",
            "capabilities": ["text_generation"],
            "secret_action": "replace",
            "api_key": "must-not-be-stored",
        })


def test_text_route_rejects_connection_without_required_key() -> None:
    connection = provider_settings.save_connection({
        "name": "Incomplete gateway",
        "provider": "openai_compatible",
        "base_url": "https://models.example/v1",
        "capabilities": ["text_generation"],
    })
    provider_settings.save_text_routes([{
        "id": "default",
        "connection_id": connection["id"],
        "model": "story-model",
        "inherits_default": False,
    }])

    with pytest.raises(provider_settings.ProviderSettingsError, match="API key"):
        provider_settings.resolve_text_route("writer")


def test_image_profile_rejects_zero_count() -> None:
    connection = _connection()

    with pytest.raises(ValueError, match="between 3 and 5"):
        provider_settings.save_image_profile("cover", {
            "connection_id": connection["id"],
            "model": "publisher/image-v3",
            "size": "2048x3072",
            "quality": "medium",
            "output_format": "png",
            "count": 0,
            "timeout_seconds": 180,
        })


def test_connection_in_use_cannot_be_deleted() -> None:
    connection = _connection()
    provider_settings.save_text_routes([
        {
            "id": "default",
            "connection_id": connection["id"],
            "model": "story-model",
            "inherits_default": False,
        }
    ])

    with pytest.raises(provider_settings.ProviderSettingsError, match="still used"):
        provider_settings.delete_connection(connection["id"])


def test_model_router_uses_v2_role_override(monkeypatch) -> None:
    from core import model_router

    captured = {}

    class RecordingClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(model_router, "LLMClient", RecordingClient)
    connection = _connection()
    provider_settings.save_text_routes([
        {
            "id": "default",
            "connection_id": connection["id"],
            "model": "default-story",
            "inherits_default": False,
        },
        {
            "id": "editor",
            "connection_id": connection["id"],
            "model": "editor-specialist",
            "max_tokens": 4096,
            "inherits_default": False,
        },
    ])

    model_router.ModelRouter().client_for("editor")

    assert captured == {
        "provider": "openai_compatible",
        "model": "editor-specialist",
        "base_url": "https://models.example/v1",
        "api_key": "provider-secret",
        "max_tokens": 4096,
    }


def test_legacy_settings_project_into_shared_connection() -> None:
    studio_settings.save_settings({
        "NOVEL_OS_LLM_PROVIDER": "openai_compatible",
        "NOVEL_OS_MODEL": "legacy-story",
        "NOVEL_OS_BASE_URL": "https://legacy.example/v1",
        "NOVEL_OS_API_KEY": "legacy-secret",
        "NOVEL_OS_COVER_MODEL": "gpt-image-2",
    })

    configuration = provider_settings.configuration_status()

    assert configuration["source"] == "legacy"
    assert len(configuration["connections"]) == 1
    assert configuration["image_profiles"]["cover"]["connection_id"] == "legacy-writing"
    assert "legacy-secret" not in json.dumps(configuration)


def test_first_v2_mutation_moves_legacy_secret_out_of_metadata() -> None:
    studio_settings.save_settings({
        "NOVEL_OS_LLM_PROVIDER": "openai_compatible",
        "NOVEL_OS_MODEL": "legacy-story",
        "NOVEL_OS_BASE_URL": "https://legacy.example/v1",
        "NOVEL_OS_API_KEY": "legacy-secret",
    })

    provider_settings.save_connection({
        "name": "Migrated gateway",
        "provider": "openai_compatible",
        "auth_type": "api_key",
        "base_url": "https://legacy.example/v1",
        "capabilities": ["text_generation", "image_generation"],
        "secret_action": "keep",
    }, "legacy-writing")

    metadata = studio_settings.settings_path().read_text(encoding="utf-8")
    secrets = studio_settings.settings_path().with_name(
        "studio_settings.secrets.json"
    ).read_text(encoding="utf-8")
    assert "legacy-secret" not in metadata
    assert "legacy-secret" in secrets
    assert provider_settings.resolve_text_route("default")["api_key"] == "legacy-secret"


def test_legacy_cover_director_override_becomes_an_explicit_route() -> None:
    studio_settings.save_settings({
        "NOVEL_OS_LLM_PROVIDER": "openai_compatible",
        "NOVEL_OS_MODEL": "story-model",
        "NOVEL_OS_BASE_URL": "https://writing.example/v1",
        "NOVEL_OS_API_KEY": "writing-secret",
        "NOVEL_OS_COVER_DIRECTOR_MODEL": "director-model",
    })

    configuration = provider_settings.configuration_status()
    director = next(
        route for route in configuration["text_routes"]
        if route["id"] == "cover_director"
    )

    assert director["inherits_default"] is False
    assert director["effective_model"] == "director-model"
    assert director["effective_connection_id"] == "legacy-writing"


def test_legacy_image_endpoint_can_reuse_writing_secret() -> None:
    studio_settings.save_settings({
        "NOVEL_OS_LLM_PROVIDER": "openai_compatible",
        "NOVEL_OS_MODEL": "story-model",
        "NOVEL_OS_BASE_URL": "https://writing.example/v1",
        "NOVEL_OS_API_KEY": "shared-secret",
        "NOVEL_OS_COVER_BASE_URL": "https://images.example/v1",
    })

    image = provider_settings.resolve_image_profile("cover")

    assert image["base_url"] == "https://images.example/v1"
    assert image["api_key"] == "shared-secret"


def test_v2_api_configures_shared_text_and_image_connection(tmp_path) -> None:
    from fastapi.testclient import TestClient
    from api.main import create_app

    client = TestClient(create_app(
        projects_root=tmp_path / "projects",
        db_url=f"sqlite:///{tmp_path / 'api.db'}",
        media_root=tmp_path / "media",
    ))
    created = client.post("/api/studio/providers", json={
        "name": "Shared gateway",
        "provider": "openai_compatible",
        "auth_type": "api_key",
        "base_url": "https://gateway.example/v1",
        "capabilities": ["text_generation", "image_generation"],
        "secret_action": "replace",
        "api_key": "api-route-secret",
    })

    assert created.status_code == 201
    connection_id = created.json()["id"]
    assert created.json()["has_api_key"] is True
    assert "api-route-secret" not in created.text

    routes = client.put("/api/studio/model-routes", json={"routes": [{
        "id": "default",
        "connection_id": connection_id,
        "model": "story-model",
        "max_tokens": 8192,
        "inherits_default": False,
    }]})
    image = client.put("/api/studio/image-profiles/cover", json={
        "connection_id": connection_id,
        "model": "gpt-image-2",
        "size": "2048x3072",
        "quality": "high",
        "output_format": "jpeg",
        "count": 4,
        "timeout_seconds": 180,
    })
    status = client.get("/api/studio/models")

    assert routes.status_code == 200
    assert image.status_code == 200
    assert status.status_code == 200
    assert status.json()["image_profiles"]["cover"]["configured"] is True
    assert "api-route-secret" not in status.text


def _wait_for_job(client, job_id: str) -> dict:
    for _ in range(100):
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] != "running":
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_text_route_response_test_returns_model_reply(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient
    from api.main import create_app
    from core import llm_client

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def complete(self, system: str, prompt: str) -> str:
            captured["prompt"] = prompt
            return "The model connection is working."

    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    connection = _connection()
    provider_settings.save_text_routes([{
        "id": "default",
        "connection_id": connection["id"],
        "model": "story-model",
        "inherits_default": False,
    }])
    client = TestClient(create_app(
        projects_root=tmp_path / "projects",
        db_url=f"sqlite:///{tmp_path / 'text-test.db'}",
        media_root=tmp_path / "media",
    ))

    started = client.post(
        "/api/studio/model-routes/default/test",
        json={"prompt": "Confirm the connection."},
    )
    completed = _wait_for_job(client, started.json()["job_id"])

    assert started.status_code == 202
    assert completed["status"] == "done"
    assert completed["meta"]["reply"] == "The model connection is working."
    assert captured["provider"] == "openai_compatible"
    assert captured["reasoning_effort"] == "low"


def test_image_profile_test_returns_immediately_and_deduplicates(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi.testclient import TestClient
    from api.main import create_app
    from core import image_client

    release = threading.Event()
    calls = []

    class FakeImageClient:
        def generate(self, prompt: str):
            calls.append(prompt)
            assert release.wait(timeout=2)
            return image_client.GeneratedImage(
                data=b"image-bytes",
                content_type="image/png",
                width=1024,
                height=1536,
                request_id="image-test-request",
                model="gpt-image-2",
            )

    monkeypatch.setattr(
        image_client,
        "build_image_generation_client",
        lambda settings: FakeImageClient(),
    )
    connection = _connection()
    provider_settings.save_image_profile("cover", {
        "connection_id": connection["id"],
        "model": "gpt-image-2",
        "size": "1024x1536",
        "quality": "high",
        "output_format": "png",
        "count": 4,
        "timeout_seconds": 180,
    })
    client = TestClient(create_app(
        projects_root=tmp_path / "projects",
        db_url=f"sqlite:///{tmp_path / 'image-test.db'}",
        media_root=tmp_path / "media",
    ))

    first = client.post("/api/studio/image-profiles/cover/test")
    second = client.post("/api/studio/image-profiles/cover/test")

    assert first.status_code == 202
    assert first.json()["status"] == "running"
    assert second.json()["job_id"] == first.json()["job_id"]
    release.set()
    completed = _wait_for_job(client, first.json()["job_id"])
    assert completed["status"] == "done"
    assert completed["meta"]["data_url"].startswith("data:image/png;base64,")
    assert len(calls) == 1
