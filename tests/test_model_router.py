"""Role-specific model routing without live provider calls."""

import os

import pytest

import model_router
from llm_client import LLMClient as RealLLMClient, LLMError
from model_router import ModelRouter


class RecordingClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.provider = kwargs.get("provider") or "resolved-default"
        self.model = kwargs.get("model") or "resolved-model"
        type(self).instances.append(self)


@pytest.fixture(autouse=True)
def clean_model_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("NOVEL_OS_"):
            monkeypatch.delenv(key, raising=False)
    RecordingClient.instances = []
    monkeypatch.setattr(model_router, "LLMClient", RecordingClient)


def test_role_override_wins_global_model(monkeypatch):
    monkeypatch.setenv("NOVEL_OS_MODEL", "global-model")
    monkeypatch.setenv("NOVEL_OS_WRITER_MODEL", "writer-model")

    client = ModelRouter().client_for("scribe")

    assert client.kwargs["model"] == "writer-model"


def test_judge_openai_compatible_provider_and_base_url_override(monkeypatch):
    monkeypatch.setenv("NOVEL_OS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("NOVEL_OS_MODEL", "global-model")
    monkeypatch.setenv("NOVEL_OS_BASE_URL", "https://global.invalid/v1")
    monkeypatch.setenv("NOVEL_OS_JUDGE_PROVIDER", "openai_compatible")
    monkeypatch.setenv("NOVEL_OS_JUDGE_MODEL", "judge-model")
    monkeypatch.setenv("NOVEL_OS_JUDGE_BASE_URL", "http://127.0.0.1:8080/v1")

    client = ModelRouter().client_for("judge")

    assert client.kwargs == {
        "provider": "openai_compatible",
        "model": "judge-model",
        "base_url": "http://127.0.0.1:8080/v1",
        "max_tokens": None,
    }


def test_role_configuration_falls_back_field_by_field(monkeypatch):
    monkeypatch.setenv("NOVEL_OS_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("NOVEL_OS_MODEL", "global-model")
    monkeypatch.setenv("NOVEL_OS_BASE_URL", "http://localhost:9000/v1")
    monkeypatch.setenv("NOVEL_OS_MAX_TOKENS", "4096")
    monkeypatch.setenv("NOVEL_OS_EDITOR_MODEL", "editor-model")
    monkeypatch.setenv("NOVEL_OS_EDITOR_PROVIDER", "   ")
    monkeypatch.setenv("NOVEL_OS_EDITOR_BASE_URL", "")

    client = ModelRouter().client_for("editor")

    assert client.kwargs == {
        "provider": "openai_compatible",
        "model": "editor-model",
        "base_url": "http://localhost:9000/v1",
        "max_tokens": 4096,
    }


def test_nonblank_role_values_are_forwarded_without_rewriting(monkeypatch):
    monkeypatch.setenv("NOVEL_OS_WRITER_PROVIDER", " openai_compatible ")
    monkeypatch.setenv("NOVEL_OS_WRITER_MODEL", " padded-model ")
    monkeypatch.setenv("NOVEL_OS_WRITER_BASE_URL", " http://localhost:9000/v1 ")

    client = ModelRouter().client_for("writer")

    assert client.kwargs["provider"] == " openai_compatible "
    assert client.kwargs["model"] == " padded-model "
    assert client.kwargs["base_url"] == " http://localhost:9000/v1 "


def test_padded_invalid_provider_preserves_real_client_error(monkeypatch):
    monkeypatch.setattr(model_router, "LLMClient", RealLLMClient)
    monkeypatch.setenv("NOVEL_OS_WRITER_PROVIDER", " openai ")

    with pytest.raises(LLMError, match=r"Unknown provider ' openai '"):
        ModelRouter().client_for("writer")


def test_existing_openai_compatible_globals_are_unchanged_without_role_overrides(
    monkeypatch,
):
    monkeypatch.setenv("NOVEL_OS_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("NOVEL_OS_MODEL", "sub2api-model")
    monkeypatch.setenv("NOVEL_OS_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("NOVEL_OS_MAX_TOKENS", "8192")

    clients = [
        ModelRouter().client_for(role)
        for role in ("scribe", "continuity_guardian", "style_curator")
    ]

    assert all(
        client.kwargs
        == {
            "provider": "openai_compatible",
            "model": "sub2api-model",
            "base_url": "http://127.0.0.1:8080/v1",
            "max_tokens": 8192,
        }
        for client in clients
    )


@pytest.mark.parametrize(
    ("agent_name", "role"),
    [
        ("scribe", "writer"),
        ("continuity_guardian", "guardian"),
        ("style_curator", "style"),
        ("writer", "writer"),
        ("architect", "architect"),
        ("editor", "editor"),
        ("guardian", "guardian"),
        ("style", "style"),
        ("judge", "judge"),
    ],
)
def test_agent_names_normalize_to_supported_roles(agent_name, role):
    assert ModelRouter.normalize_role(agent_name) == role


@pytest.mark.parametrize("role", ["", "   ", "copy-editor", "unknown_agent"])
def test_unknown_or_blank_roles_are_rejected(role):
    with pytest.raises(ValueError, match="role"):
        ModelRouter().client_for(role)


@pytest.mark.parametrize("raw", ["invalid", "1.5"])
def test_invalid_max_tokens_uses_existing_integer_validation(monkeypatch, raw):
    monkeypatch.setenv("NOVEL_OS_WRITER_MAX_TOKENS", raw)

    with pytest.raises(ValueError):
        ModelRouter().client_for("writer")


def test_explicit_zero_is_passed_through_to_existing_client_semantics(monkeypatch):
    monkeypatch.setenv("NOVEL_OS_WRITER_MAX_TOKENS", "0")

    client = ModelRouter().client_for("writer")

    assert client.kwargs["max_tokens"] == 0


def test_router_reads_environment_per_call_and_does_not_mutate_it(monkeypatch):
    monkeypatch.setenv("NOVEL_OS_MODEL", "first")
    router = ModelRouter()

    before_first = dict(os.environ)
    first = router.client_for("writer")
    assert dict(os.environ) == before_first
    monkeypatch.setenv("NOVEL_OS_MODEL", "second")
    before_second = dict(os.environ)
    second = router.client_for("writer")

    assert first is not second
    assert first.kwargs["model"] == "first"
    assert second.kwargs["model"] == "second"
    assert dict(os.environ) == before_second


def test_api_keys_are_never_forwarded_or_exposed(monkeypatch):
    secret = "sk-role-secret"
    monkeypatch.setenv("NOVEL_OS_API_KEY", secret)
    monkeypatch.setenv("NOVEL_OS_WRITER_API_KEY", "must-not-be-read")

    router = ModelRouter()
    client = router.client_for("writer")

    assert "api_key" not in client.kwargs
    assert secret not in repr(router)
    assert "must-not-be-read" not in repr(router)
    assert secret not in repr(client.kwargs)
