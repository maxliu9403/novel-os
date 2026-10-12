"""Offline checks against installed SDK transports, without provider requests."""
from core.usage_tracking import read_records, usage_scope


def test_gemini_sdk_retry_records_each_real_http_attempt(tmp_path, monkeypatch):
    import httpx
    from google import genai
    from google.genai import types
    from core.llm_client import LLMClient

    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, json={'error': {
                'code': 429, 'message': 'retry', 'status': 'RESOURCE_EXHAUSTED',
            }})
        return httpx.Response(200, json={
            'candidates': [{'content': {'parts': [{'text': 'ok'}], 'role': 'model'}, 'finishReason': 'STOP'}],
            'modelVersion': 'gemini-fixture',
            'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 3, 'totalTokenCount': 13},
        })

    backend = genai.Client(api_key='test-not-real', http_options=types.HttpOptions(
        httpx_client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_options=types.HttpRetryOptions(attempts=2, initial_delay=.001, max_delay=.001, jitter=0),
    ))
    monkeypatch.setattr(LLMClient, '_build_backend', lambda *args: (backend, 'gemini-fixture'))
    try:
        client = LLMClient(provider='gemini', model='gemini-fixture')
        with usage_scope(tmp_path, 'manuscript'):
            assert client.complete('', 'fixture') == 'ok'
    finally:
        backend.close()
    rows = list(read_records(tmp_path).values())
    assert len(requests) == len(rows) == 2
    assert {row['status'] for row in rows} == {'failed', 'completed'}
    assert sum(row['usage'].get('total_tokens', 0) for row in rows) == 13
    assert next(row for row in rows if row['status'] == 'failed')['usage'] == {}
