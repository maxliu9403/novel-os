"""Capacity discovery uses authenticated metadata, never generation requests."""
import threading

import httpx
import pytest

from core.gemini_gateway import ATTACHMENT_LIMIT_BYTES, CONTEXT_POLICY, INPUT_LIMIT_BYTES
from core.llm_client import LLMCancelledError, LLMClient, LLMInputLimitError


CAPABILITIES = {
    'schema': 'gw2a.context.v1', 'unit': 'utf8_bytes', 'policy': CONTEXT_POLICY,
    'text_attachment': True, 'inline_limit_bytes': INPUT_LIMIT_BYTES,
    'input_limit_bytes': ATTACHMENT_LIMIT_BYTES,
}


@pytest.fixture
def wire(monkeypatch):
    from openai import OpenAI

    requests, replies, clients = [], [], []

    def handler(request):
        requests.append(request)
        assert request.method == 'GET'
        assert request.url.path == '/v1/context-capabilities'
        assert replies, 'Unexpected metadata retry or generation request'
        response = replies.pop(0)
        return response(request) if callable(response) else response

    def builder(self, base_url, api_key):
        backend = OpenAI(base_url=base_url, api_key=api_key, max_retries=0,
                         http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        clients.append(backend)
        return backend

    monkeypatch.setattr(LLMClient, '_build_openai_compatible', builder)
    yield requests, replies
    for backend in clients:
        backend.close()


def client(**options):
    return LLMClient(provider='gemini_openai', model='gemini-4-argon-thinking',
                     base_url='https://gateway.test/v1', api_key='test-capacity-secret', **options)


def test_capacity_uses_authenticated_metadata_and_is_not_cached(wire):
    requests, replies = wire
    replies.extend([httpx.Response(200, json=CAPABILITIES),
                    httpx.Response(404, json={'error': 'old gateway'})])
    llm = client()
    assert llm.text_input_limit_bytes() == ATTACHMENT_LIMIT_BYTES
    assert llm.text_input_limit_bytes() == INPUT_LIMIT_BYTES
    assert len(requests) == 2
    assert all(r.headers['authorization'] == 'Bearer test-capacity-secret' for r in requests)
    assert all(not r.content for r in requests)
    assert all(r.extensions['timeout']['read'] == 5.0 for r in requests)


@pytest.mark.parametrize('payload', [
    None, [], {}, {**CAPABILITIES, 'input_limit_bytes': '156000'},
    {**CAPABILITIES, 'input_limit_bytes': 156001},
    {**CAPABILITIES, 'input_limit_bytes': 128000},
    {**CAPABILITIES, 'inline_limit_bytes': True},
    {**CAPABILITIES, 'policy': 'unknown'},
    {**CAPABILITIES, 'text_attachment': False},
])
def test_malformed_capabilities_keep_inline_limit(wire, payload):
    requests, replies = wire
    replies.append(httpx.Response(200, json=payload))
    assert client().text_input_limit_bytes() == INPUT_LIMIT_BYTES
    assert len(requests) == 1


@pytest.mark.parametrize('status', [401, 404, 500])
def test_unavailable_capability_endpoint_does_not_retry_or_infer_capacity(wire, status):
    requests, replies = wire
    replies.append(httpx.Response(status, json={'error': 'unavailable'}))
    assert client().text_input_limit_bytes() == INPUT_LIMIT_BYTES
    assert len(requests) == 1


def test_cancel_before_discovery_sends_nothing(wire):
    requests, _ = wire
    cancelled = threading.Event()
    llm = client(cancellation_event=cancelled)
    cancelled.set()
    with pytest.raises(LLMCancelledError):
        llm.text_input_limit_bytes()
    assert not requests


def test_cancel_during_discovery_cannot_return_a_usable_limit(wire):
    requests, replies = wire
    cancelled = threading.Event()
    llm = client(cancellation_event=cancelled)

    def response(request):
        cancelled.set()
        return httpx.Response(200, json=CAPABILITIES)

    replies.append(response)
    with pytest.raises(LLMCancelledError):
        llm.text_input_limit_bytes()
    assert len(requests) == 1


@pytest.mark.parametrize('provider', ['openai_compatible', 'openai', 'codex', 'gemini', 'deepseek'])
def test_other_providers_report_undeclared_capacity_without_accessing_backend(provider):
    # No backend construction or credentials are needed to state the absence of
    # a transport-level declaration. None must not be interpreted as infinity.
    llm = object.__new__(LLMClient)
    llm.provider_name = provider
    llm._backend = object()
    assert llm.text_input_limit_bytes() is None


def test_generation_rechecks_capacity_after_successful_preflight(wire):
    requests, replies = wire
    replies.extend([httpx.Response(200, json=CAPABILITIES),
                    httpx.Response(404, json={'error': 'capability withdrawn'})])
    llm = client()
    assert llm.text_input_limit_bytes() == ATTACHMENT_LIMIT_BYTES
    with pytest.raises(LLMInputLimitError):
        llm.complete('system', 'x' * INPUT_LIMIT_BYTES)
    assert len(requests) == 2  # Both GET; no oversized generation was dispatched.
