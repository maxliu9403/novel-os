"""Streaming behavior for OpenAI-compatible LLM calls."""

from types import SimpleNamespace

import llm_client
from llm_client import LLMClient


class _Completions:
    def __init__(self, chunks):
        self.chunks = chunks
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return iter(self.chunks)


def _chunk(content=None):
    delta = SimpleNamespace(content=content)
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice])


def _streaming_client(chunks):
    client = object.__new__(LLMClient)
    client.provider_name = "openai_compatible"
    client.model = "test-model"
    client.max_tokens = 128
    completions = _Completions(chunks)
    client._backend = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return client, completions


def test_openai_shape_streams_and_aggregates_content(monkeypatch, capsys):
    monkeypatch.delenv("NOVEL_OS_LLM_STREAMING", raising=False)
    client, completions = _streaming_client(
        [_chunk(None), _chunk("ST"), _chunk("REAM"), _chunk("_OK")]
    )

    result = client._complete_openai_shape("system", "user")

    assert result == "STREAM_OK"
    assert completions.kwargs["stream"] is True
    assert "Response stream started" in capsys.readouterr().out


def test_openai_compatible_client_disables_hidden_retries_and_extends_timeout(
    monkeypatch,
):
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(
        __import__("sys").modules,
        "openai",
        SimpleNamespace(OpenAI=FakeOpenAI),
    )
    monkeypatch.delenv("NOVEL_OS_LLM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("NOVEL_OS_SDK_MAX_RETRIES", raising=False)

    LLMClient(
        provider="openai_compatible",
        model="test-model",
        base_url="http://127.0.0.1:8080/v1",
        api_key="test-key",
    )

    assert captured["timeout"] == 1800.0
    assert captured["max_retries"] == 0
