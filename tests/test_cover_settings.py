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
        ("NOVEL_OS_COVER_MODEL", "dall-e-3", "gpt-image-2"),
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
