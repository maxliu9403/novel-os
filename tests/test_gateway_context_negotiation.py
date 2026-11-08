"""Cross-version wire contract for the local gateway's bounded file transport."""
import json

import httpx
import pytest

from core.llm_client import LLMInputLimitError
from test_gemini_gateway import wire, client, chunk, stream


def capabilities(**changes):
    return httpx.Response(200, json={
        'schema': 'gw2a.context.v1', 'unit': 'utf8_bytes',
        'inline_limit_bytes': 128000, 'input_limit_bytes': 156000,
        'text_attachment': True, 'policy': 'bounded-attachment-v1', **changes,
    })


def test_actual_failure_size_uses_negotiated_transport_without_truncation(wire):
    sent, responses = wire
    responses.extend([capabilities(), stream(chunk('fixed', 'stop'))])
    llm = client()
    text = 'x' * 129470  # + system byte + 1024 reserve = actual failure 130495
    assert llm.complete('s', text) == 'fixed'
    assert len(sent) == 2
    assert sent[0].method == 'GET' and sent[0].url.path == '/v1/context-capabilities'
    assert sent[0].headers['authorization'] == 'Bearer secret-test'
    assert sent[1].headers['x-gw2a-context-policy'] == 'bounded-attachment-v1'
    assert json.loads(sent[1].content)['messages'][1]['content'] == text
    assert llm.last_response_metadata['input_limit_bytes'] == 156000
    assert llm.last_response_metadata['context_policy'] == 'bounded-attachment-v1'


@pytest.mark.parametrize('response', [
    httpx.Response(404, json={'error': 'unsupported'}),
    httpx.Response(503, json={'error': 'unavailable'}),
    capabilities(text_attachment=False, input_limit_bytes=128000),
    capabilities(schema='unknown'), capabilities(input_limit_bytes=10000000),
    capabilities(input_limit_bytes='156000'), capabilities(inline_limit_bytes=0),
])
def test_old_unavailable_or_invalid_capability_never_sends_large_prompt(wire, response):
    sent, responses = wire
    responses.append(response)
    with pytest.raises(LLMInputLimitError):
        client().complete('s', 'x' * 129470)
    assert len(sent) == 1 and sent[0].method == 'GET'


def test_negotiated_utf8_boundary_and_no_oversized_completion(wire):
    sent, responses = wire
    responses.extend([capabilities(), stream(chunk('ok', 'stop'))])
    text = '中' * 51657 + 'xxxx'  # 154975 payload + 1025 envelope = 156000
    assert client().complete('s', text) == 'ok'
    assert json.loads(sent[-1].content)['messages'][1]['content'] == text
    responses.append(capabilities())
    with pytest.raises(LLMInputLimitError) as error:
        client().complete('s', text + 'x')
    assert error.value.limit_bytes == 156000
    assert len(sent) == 3 and sent[-1].method == 'GET'


def test_large_multimodal_request_is_not_claimed_as_text_capacity(wire):
    sent, _ = wire
    with pytest.raises(LLMInputLimitError):
        client().complete_with_images('s', 'x' * 129470, [b'\x89PNG\r\n\x1a\nfixture'])
    assert sent == []


def test_cancellation_after_capability_lookup_stops_before_model(wire):
    from threading import Event
    from core.llm_client import LLMCancelledError
    from core import gemini_gateway
    sent, responses = wire
    responses.append(capabilities())
    cancel = Event()
    llm = client(cancellation_event=cancel)
    # Exercise cancellation at the real negotiation boundary.
    original = llm._check_cancelled
    checks = 0
    def check():
        nonlocal checks
        if sent:
            cancel.set()
        checks += 1
        original()
    with pytest.raises(LLMCancelledError):
        gemini_gateway.complete(llm._backend, model=llm.model, system='s', user='x'*129470,
            effort='', check_cancelled=check, diagnostic=lambda *a, **kw: None,
            observe=lambda *a, **kw: None, set_stream=lambda x: None)
    assert checks >= 2 and len(sent) == 1
