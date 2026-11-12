"""Native SDK wire-level tests; no network, credentials or model charges."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from google import genai
from google.genai import types

from core import gemini_provider as gemini
from core.llm_client import LLMClient, LLMError


@pytest.fixture
def wire(monkeypatch):
    original = genai.Client
    requests = []
    responses = []
    created = []

    def handler(request):
        requests.append(request)
        assert responses, "Unexpected extra request / automatic retry"
        return responses.pop(0)

    def client(**kwargs):
        options = kwargs['http_options']
        options.httpx_client = httpx.Client(transport=httpx.MockTransport(handler))
        result = original(**kwargs)
        created.append(result)
        return result

    monkeypatch.setattr(genai, 'Client', client)
    yield requests, responses, created
    for client in created:
        client.close()


def chunk(text='', finish=None, *, thought=False, usage=None):
    return {'candidates': [{'content': {'role': 'model', 'parts': [{'text': text, 'thought': thought}]},
                            **({'finishReason': finish} if finish else {})}],
            'modelVersion': 'gemini-4-argon-observed', **({'usageMetadata': usage} if usage else {})}


def sse(*chunks):
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                          content=''.join('data: ' + json.dumps(item) + '\n\n' for item in chunks))


def test_native_system_prompt_stream_reasoning_and_cumulative_usage(wire, monkeypatch):
    requests, responses, _ = wire
    responses.append(sse(chunk('private', thought=True), chunk('Hello '), chunk('world', 'STOP', usage={
        'promptTokenCount': 100, 'candidatesTokenCount': 5, 'thoughtsTokenCount': 9, 'totalTokenCount': 114})))
    observed = []; diagnostics = []
    monkeypatch.setattr('core.llm_client.observe_usage', lambda value, **kwargs: observed.append((value, kwargs)))
    monkeypatch.setenv('GOOGLE_GENAI_USE_VERTEXAI', 'true')
    client = LLMClient(provider='gemini', api_key='test-secret', model='gemini-4-argon',
                       base_url='https://gemini.test/v1beta', reasoning_effort='high',
                       diagnostic_callback=diagnostics.append, timeout_seconds=33)
    assert client.complete('SYSTEM EXACT', 'USER EXACT') == 'Hello world'
    request = requests[0]
    assert request.url.path == '/v1beta/models/gemini-4-argon:streamGenerateContent'
    assert request.headers['x-goog-api-key'] == 'test-secret'
    assert 'key=' not in str(request.url)
    body = json.loads(request.content)
    assert body['systemInstruction']['parts'][0]['text'] == 'SYSTEM EXACT'
    assert body['contents'][0]['parts'][0]['text'] == 'USER EXACT'
    assert body['generationConfig']['thinkingConfig'] == {'include_thoughts': False, 'thinking_level': 'HIGH'}
    assert body['generationConfig']['maxOutputTokens'] == 8192
    assert observed[-1][0].total_token_count == 114
    assert all(options['replace'] for _, options in observed)
    assert any(event['event'] == 'content.progress' for event in diagnostics)
    assert client._active_stream is None


@pytest.mark.parametrize('finish', ['MAX_TOKENS', 'SAFETY', None])
def test_truncated_or_blocked_or_interrupted_output_is_never_success(wire, finish):
    _, responses, _ = wire
    responses.append(sse(chunk('incomplete chapter', finish)))
    client = LLMClient(provider='gemini', api_key='key', model='gemini-3.5-flash')
    with pytest.raises(LLMError, match='未完整结束'):
        client.complete('s', 'u')


@pytest.mark.parametrize('finish', ['MAX_TOKENS', 'SAFETY', None, 'STOP'])
def test_attempt_preserves_all_received_prose_without_reasoning(wire, tmp_path, finish):
    from core.llm_attempt import completion_attempt, recover_completion
    _, responses, _ = wire
    responses.append(sse(chunk('PRIVATE_REASONING', thought=True), chunk('first '), chunk('last', finish)))
    client = LLMClient(provider='gemini', api_key='key', model='gemini-3.5-flash')
    directory = tmp_path / 'attempt'
    with completion_attempt(directory, 'r1'):
        if finish == 'STOP':
            assert client.complete('s', 'u') == 'first last'
        else:
            with pytest.raises(LLMError) as captured:
                client.complete('s', 'u')
            if finish == 'MAX_TOKENS':
                from core.source_request_recovery import classify_failure
                outcome = classify_failure(captured.value)
                assert outcome['outcome'] == 'failed' and outcome['finish_reason'] == 'max_tokens'
    assert (directory / 'partial-response.txt').read_text() == 'first last'
    assert recover_completion(directory, 'r1') == ('first last' if finish == 'STOP' else None)


def test_empty_final_response_and_http_error_are_not_retried(wire):
    requests, responses, _ = wire
    client = LLMClient(provider='gemini', api_key='key', model='gemini-3.5-flash')
    responses.append(sse(chunk('', 'STOP')))
    with pytest.raises(LLMError, match='空正文'):
        client.complete('s', 'u')
    responses.append(httpx.Response(429, json={'error': {'code': 429, 'message': 'PRIVATE key and prompt'}}))
    with pytest.raises(LLMError, match='HTTP 429') as error:
        client.complete('s', 'u')
    assert 'PRIVATE' not in str(error.value)
    assert len(requests) == 2


def test_image_bytes_use_native_parts(wire):
    requests, responses, _ = wire
    responses.append(sse(chunk('A cover', 'STOP')))
    client = LLMClient(provider='gemini', api_key='key', model='gemini-3.5-flash')
    assert client.supports_image_input
    assert client.complete_with_images('s', 'describe', [b'\x89PNG\r\n\x1a\nbytes']) == 'A cover'
    parts = json.loads(requests[0].content)['contents'][0]['parts']
    assert parts[1]['inlineData']['mime_type'] == 'image/png'
    assert parts[0]['text'] == 'describe'


def test_native_catalog_paginates_and_filters_non_text_models(wire):
    requests, responses, _ = wire
    def model(name, actions=['generateContent']):
        return {'name': 'models/' + name, 'displayName': name, 'supportedGenerationMethods': actions,
                'inputTokenLimit': 1000000, 'outputTokenLimit': 65536}
    responses.extend([
        httpx.Response(200, json={'models': [model('gemini-3.5-flash'), model('embedding', ['embedContent'])], 'nextPageToken': 'next'}),
        httpx.Response(200, json={'models': [model('gemini-4-argon'), model('gemini-3-pro-image')]}),
    ])
    result = gemini.discover_models('key', 'https://gemini.test', 12)
    assert set(result) == {'gemini-3.5-flash', 'gemini-4-argon'}
    assert result['gemini-4-argon']['reasoning']['supported_levels'] == ['low', 'medium', 'high']
    assert requests[1].url.params['pageToken'] == 'next'


def test_thinking_options_are_model_specific_and_never_raise_budget_silently():
    assert gemini.thinking_options('gemini-2.5-pro', 'high', 32768) == {'thinking_budget': 24576}
    assert gemini.thinking_options('gemini-4-argon', 'medium', 8192) == {'thinking_level': 'MEDIUM'}
    with pytest.raises(gemini.GeminiError, match='输出上限'):
        gemini.thinking_options('gemini-2.5-pro', 'high', 8192)
    for model, effort in [('gemini-3-pro-preview', 'medium'), ('gemini-4-argon', 'ultra'), ('unknown', 'high')]:
        with pytest.raises(gemini.GeminiError, match='不支持'):
            gemini.thinking_options(model, effort)
    assert gemini.thinking_options('unknown', '') == {}


def test_cancellation_stops_stream_and_closes_it():
    closed = []; published = []; cancel = threading.Event()
    def stream():
        try:
            yield types.GenerateContentResponse.model_validate(chunk('one'))
            cancel.set()
            yield types.GenerateContentResponse.model_validate(chunk('two', 'STOP'))
        finally:
            closed.append(True)
    client = SimpleNamespace(models=SimpleNamespace(generate_content_stream=lambda **kwargs: stream()))
    def check():
        if cancel.is_set():
            raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError):
        gemini.complete(client, model='gemini-4-argon', system='s', user='u', max_tokens=8192,
                        effort='', check_cancelled=check, diagnostic=lambda *args, **kwargs: None,
                        observe=lambda *args, **kwargs: None, set_stream=published.append)
    assert closed == [True]
    assert published[-1] is None


@pytest.mark.parametrize('url', ['https://key:secret@test', 'https://test?key=secret', 'https://test/v1', 'https://test/v1beta/openai'])
def test_native_endpoint_rejects_credential_urls_and_openai_shape(url):
    with pytest.raises(gemini.GeminiError):
        gemini.normalize_base_url(url)


def test_continuation_preserves_prompt_budget_and_counts_usage_once(wire, tmp_path):
    from core.usage_tracking import usage_scope, read_records
    requests, responses, _ = wire
    first = chunk('First ', 'CONTINUATION', usage={
        'promptTokenCount': 10, 'candidatesTokenCount': 3, 'totalTokenCount': 13})
    first['candidates'][0]['continuationToken'] = 'b3BhcXVlLWhhbmRvZmY='
    responses.extend([sse(first), sse(chunk('last', 'STOP', usage={
        'promptTokenCount': 0, 'candidatesTokenCount': 2, 'totalTokenCount': 2}))])
    client = LLMClient(provider='gemini', api_key='key', model='gemini-4-argon')
    with usage_scope(tmp_path, 'manuscript'):
        assert client.complete('exact system', 'exact user') == 'First last'
    assert len(requests) == 2
    bodies = [json.loads(request.content) for request in requests]
    assert bodies[0]['contents'] == bodies[1]['contents']
    assert bodies[0]['systemInstruction'] == bodies[1]['systemInstruction']
    assert gemini.is_continuation(requests[1]), bodies[1]
    records = list(read_records(tmp_path).values())
    assert len(records) == 1
    assert records[0]['status'] == 'completed'
    assert records[0]['usage']['total_tokens'] == 15


def test_metadata_free_trailer_does_not_erase_usage(wire, tmp_path):
    from core.usage_tracking import usage_scope, read_records
    _, responses, _ = wire
    responses.append(sse(chunk('Complete', usage={'promptTokenCount': 7, 'candidatesTokenCount': 3, 'totalTokenCount': 10}), chunk('', 'STOP')))
    client = LLMClient(provider='gemini', api_key='key', model='gemini-4-argon')
    with usage_scope(tmp_path, 'manuscript'):
        assert client.complete('s', 'u') == 'Complete'
    assert next(iter(read_records(tmp_path).values()))['usage']['total_tokens'] == 10
