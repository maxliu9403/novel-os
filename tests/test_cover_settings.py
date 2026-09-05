from __future__ import annotations

import pytest

from core import studio_settings


def test_cover_settings_default_to_image2_and_exact_2k() -> None:
    settings = studio_settings.resolve_cover_settings({})

    assert settings.model == "gpt-image-2"
    assert settings.size == "2048x3072"
    assert settings.quality == "high"
    assert settings.output_format == "jpeg"
    assert settings.count == 4
    assert settings.timeout_seconds == 180.0


def test_cover_settings_reuse_writing_endpoint_and_key() -> None:
    settings = studio_settings.resolve_cover_settings({
        "NOVEL_OS_BASE_URL": "https://sub2api.example/v1/",
        "NOVEL_OS_API_KEY": "shared-secret",
    })

    assert settings.base_url == "https://sub2api.example/v1"
    assert settings.api_key == "shared-secret"
    assert settings.inherits_base_url is True
    assert settings.inherits_api_key is True


@pytest.mark.parametrize("output_format", ["jpeg", "png"])
def test_cover_settings_allow_jpeg_and_png(output_format: str) -> None:
    settings = studio_settings.resolve_cover_settings({
        "NOVEL_OS_COVER_FORMAT": output_format,
    })

    assert settings.output_format == output_format


def test_cover_settings_allow_independent_overrides() -> None:
    settings = studio_settings.resolve_cover_settings({
        "NOVEL_OS_BASE_URL": "https://writing.example/v1",
        "NOVEL_OS_API_KEY": "writing-secret",
        "NOVEL_OS_COVER_BASE_URL": "https://images.example/v1/",
        "NOVEL_OS_COVER_API_KEY": "cover-secret",
        "NOVEL_OS_COVER_COUNT": "5",
        "NOVEL_OS_COVER_TIMEOUT_SECONDS": "240",
    })

    assert settings.base_url == "https://images.example/v1"
    assert settings.api_key == "cover-secret"
    assert settings.count == 5
    assert settings.timeout_seconds == 240.0
    assert settings.inherits_base_url is False
    assert settings.inherits_api_key is False


def test_legacy_webp_setting_migrates_to_jpeg_without_losing_endpoint() -> None:
    settings = studio_settings.resolve_cover_settings({
        "NOVEL_OS_COVER_BASE_URL": "https://images.example/v1/",
        "NOVEL_OS_COVER_API_KEY": "cover-secret",
        "NOVEL_OS_COVER_FORMAT": "webp",
    })

    assert settings.output_format == "jpeg"
    assert settings.base_url == "https://images.example/v1"
    assert settings.api_key == "cover-secret"


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("NOVEL_OS_COVER_COUNT", "2", "between 3 and 5"),
        ("NOVEL_OS_COVER_SIZE", "1024x1024", "2:3"),
        ("NOVEL_OS_COVER_QUALITY", "ultra", "quality"),
        ("NOVEL_OS_COVER_FORMAT", "gif", "format"),
        ("NOVEL_OS_COVER_TIMEOUT_SECONDS", "0", "timeout"),
    ],
)
def test_cover_settings_reject_invalid_generation_parameters(
    key: str,
    value: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        studio_settings.resolve_cover_settings({key: value})


def test_cover_status_redacts_key() -> None:
    status = studio_settings.cover_status({
        "NOVEL_OS_COVER_API_KEY": "cover-secret",
        "NOVEL_OS_COVER_BASE_URL": "https://images.example/v1",
    })

    assert status["configured"] is True
    assert status["has_api_key"] is True
    assert status["model"] == "gpt-image-2"
    assert "cover-secret" not in repr(status)


def test_cover_settings_allow_provider_native_portrait_resolution() -> None:
    settings = studio_settings.resolve_cover_settings({
        "NOVEL_OS_COVER_SIZE": "1024x1536",
    })

    assert settings.size == "1024x1536"


def test_cover_settings_allow_configured_image_model() -> None:
    settings = studio_settings.resolve_cover_settings({
        "NOVEL_OS_COVER_MODEL": "publisher/image-v3",
    })

    assert settings.model == "publisher/image-v3"


def test_cover_director_settings_fall_back_to_writing_model_not_image_model() -> None:
    settings = studio_settings.resolve_cover_director_settings({
        "NOVEL_OS_LLM_PROVIDER": "openai_compatible",
        "NOVEL_OS_MODEL": "story-planner-v2",
        "NOVEL_OS_BASE_URL": "https://text.example/v1/",
        "NOVEL_OS_API_KEY": "writing-secret",
        "NOVEL_OS_COVER_MODEL": "gpt-image-2",
    })

    assert settings.provider == "openai_compatible"
    assert settings.model == "story-planner-v2"
    assert settings.base_url == "https://text.example/v1"
    assert settings.api_key == "writing-secret"
    assert settings.timeout_seconds == 600.0
    assert settings.inherits_writing is True


def test_cover_director_settings_allow_independent_provider_and_validate_timeout() -> None:
    settings = studio_settings.resolve_cover_director_settings({
        "NOVEL_OS_LLM_PROVIDER": "anthropic",
        "NOVEL_OS_MODEL": "writing-model",
        "NOVEL_OS_COVER_DIRECTOR_PROVIDER": "openai_compatible",
        "NOVEL_OS_COVER_DIRECTOR_MODEL": "director-model",
        "NOVEL_OS_COVER_DIRECTOR_BASE_URL": "https://director.example/v1/",
        "NOVEL_OS_COVER_DIRECTOR_API_KEY": "director-secret",
        "NOVEL_OS_COVER_DIRECTOR_TIMEOUT_SECONDS": "240",
    })

    assert settings.provider == "openai_compatible"
    assert settings.model == "director-model"
    assert settings.base_url == "https://director.example/v1"
    assert settings.api_key == "director-secret"
    assert settings.timeout_seconds == 240.0
    assert settings.inherits_writing is False

    with pytest.raises(ValueError, match="Director timeout"):
        studio_settings.resolve_cover_director_settings({
            "NOVEL_OS_COVER_DIRECTOR_TIMEOUT_SECONDS": "0",
        })


def test_v2_cover_director_honors_the_persisted_timeout(
    tmp_path, monkeypatch,
) -> None:
    from core import provider_settings

    monkeypatch.setenv(
        "NOVEL_OS_SETTINGS_PATH", str(tmp_path / "studio_settings.json")
    )
    connection = provider_settings.save_connection({
        "name": "Director models",
        "provider": "openai_compatible",
        "auth_type": "api_key",
        "base_url": "https://models.example/v1",
        "image_base_url": "",
        "capabilities": ["text_generation"],
        "secret_action": "replace",
        "api_key": "provider-secret",
    })
    provider_settings.save_text_routes([{
        "id": "default",
        "connection_id": connection["id"],
        "model": "director-model",
        "max_tokens": 8192,
        "inherits_default": False,
    }])
    studio_settings.save_settings({
        "NOVEL_OS_COVER_DIRECTOR_TIMEOUT_SECONDS": 420,
    })

    settings = studio_settings.resolve_cover_director_settings()

    assert settings.timeout_seconds == 420.0


def test_cover_director_status_redacts_independent_key() -> None:
    status = studio_settings.cover_director_status({
        "NOVEL_OS_COVER_DIRECTOR_PROVIDER": "openai_compatible",
        "NOVEL_OS_COVER_DIRECTOR_MODEL": "director-model",
        "NOVEL_OS_COVER_DIRECTOR_BASE_URL": "https://director.example/v1",
        "NOVEL_OS_COVER_DIRECTOR_API_KEY": "director-secret",
    })

    assert status["has_api_key"] is True
    assert status["model"] == "director-model"
    assert "director-secret" not in repr(status)
