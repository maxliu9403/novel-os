from types import SimpleNamespace
import json
import subprocess

import pytest


@pytest.mark.parametrize("kind", ["read", "connect", "openai"])
def test_sdk_timeout_types_keep_safe_local_reason(kind):
    import httpx
    from core.source_request_recovery import classify_failure
    request = httpx.Request("POST", "https://private.example/api?secret=token")
    if kind == "openai":
        from openai import APITimeoutError
        error = APITimeoutError(request=request)
    else:
        error = (httpx.ReadTimeout if kind == "read" else httpx.ConnectTimeout)("PRIVATE_TOKEN", request=request)
    evidence = classify_failure(error)
    assert evidence["local_outcome"] == "timeout"
    assert evidence["outcome"] == "unknown" and not evidence["retryable"]
    assert "PRIVATE_TOKEN" not in str(evidence) and "private.example" not in str(evidence)

from core.source_request_recovery import classify_failure


def test_wrapped_process_timeout_keeps_remote_unknown_and_safe_local_reason():
    from core.llm_client import LLMError
    original = subprocess.TimeoutExpired(['model', '--token=private-secret'], 900,
                                         output='private stdout', stderr='private stderr')
    try:
        raise original
    except subprocess.TimeoutExpired:
        try:
            raise LLMError('provider secret and private prompt') from None
        except LLMError as wrapped:
            result = classify_failure(wrapped)
    assert result['outcome'] == 'unknown'
    assert result['retryable'] is False
    assert result['local_outcome'] == 'timeout'
    assert result['timeout_scope'] == 'local_process'
    assert result['timeout_seconds'] == 900
    assert '900' in result['message']
    assert '未知' in result['message'] or '未确认' in result['message']
    serialized = json.dumps(result, ensure_ascii=False)
    assert 'private' not in serialized and 'secret' not in serialized


def test_generic_timeout_does_not_invent_process_timeout_duration():
    result = classify_failure(TimeoutError('private request body'))
    assert result['outcome'] == 'unknown' and not result['retryable']
    assert result['local_outcome'] == 'timeout'
    assert result['timeout_scope'] == 'request'
    assert 'timeout_seconds' not in result
    assert 'private' not in result['message']


def test_cancellation_is_local_without_claiming_remote_failure():
    from core.llm_client import LLMCancelledError
    result = classify_failure(LLMCancelledError('private cancellation text'))
    assert result['outcome'] == 'unknown' and not result['retryable']
    assert result['local_outcome'] == 'cancelled'
    assert 'private' not in result['message']


@pytest.mark.parametrize('finish_reason', ['length', 'max_tokens', 'content_filter', 'tool_calls', 'tool_use'])
def test_explicit_incomplete_termination_is_failed_but_not_automatically_retried(finish_reason):
    from core.llm_client import LLMError, LLMIncompleteResponseError
    try:
        raise LLMIncompleteResponseError(finish_reason)
    except LLMIncompleteResponseError as cause:
        try:
            raise LLMError('private model response and credentials') from cause
        except LLMError as wrapped:
            result = classify_failure(wrapped)
    assert result['outcome'] == 'failed' and result['retryable'] is False
    assert result['local_outcome'] == 'incomplete'
    assert result['finish_reason'] == finish_reason
    assert '不完整' in result['message']
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('finish_reason', [None, 'unknown', 'private provider string'])
def test_incomplete_eof_without_terminal_reason_remains_unknown(finish_reason):
    from core.llm_client import LLMIncompleteResponseError
    result = classify_failure(LLMIncompleteResponseError(finish_reason))
    assert result['outcome'] == 'unknown' and result['retryable'] is False
    assert result['local_outcome'] == 'incomplete'
    assert '不完整' in result['message'] and '未确认' in result['message']
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('timeout', [None, 'private', float('inf'), float('nan'), -1, True, 10**1000])
def test_invalid_timeout_duration_is_not_persisted(timeout):
    result = classify_failure(subprocess.TimeoutExpired('private command', timeout))
    assert result['local_outcome'] == 'timeout'
    assert 'timeout_seconds' not in result
    assert 'private' not in json.dumps(result)


def test_failure_message_supports_legacy_and_new_receipts_without_echoing_error_text():
    from core.source_request_recovery import failure_message
    legacy = {'error_type': 'LLMError', 'outcome': 'unknown', 'retryable': False,
              'reason': 'no_verified_terminal_response', 'message': 'private stale message'}
    assert '未确认' in failure_message(legacy)
    assert 'private' not in failure_message(legacy)
    new = classify_failure(subprocess.TimeoutExpired('private command', 900))
    assert failure_message(new) == new['message']
    returned = classify_failure(type('HTTPError', (RuntimeError,), {'status_code': 502})('secret'))
    assert '502' in failure_message(returned)
    assert 'secret' not in failure_message(returned)


@pytest.mark.parametrize('status,retryable', [(401, False), (403, False), (429, True), (502, True)])
def test_http_status_survives_adapter_wrapping_without_error_text_matching(status, retryable):
    original = RuntimeError('provider content is not classified or persisted')
    original.status_code = status
    try:
        raise original
    except RuntimeError:
        try:
            raise ValueError('wrapped without cause') from None
        except ValueError as wrapped:
            result = classify_failure(wrapped)
    assert result == {'outcome': 'failed', 'retryable': retryable, 'http_status': status,
                      'reason': 'returned_http_error', 'error_type': 'ValueError'}


def test_plain_prose_cannot_manufacture_failure_evidence():
    assert classify_failure(RuntimeError('HTTP 502 surely failed'))['outcome'] == 'unknown'
    assert classify_failure(TimeoutError('read timeout'))['outcome'] == 'unknown'
    assert classify_failure(ConnectionRefusedError())['outcome'] == 'not_sent'


def test_native_and_response_status_are_supported():
    error = RuntimeError()
    error.code = 429
    assert classify_failure(error)['retryable']
    del error.code
    error.response = SimpleNamespace(status_code=401)
    assert classify_failure(error)['http_status'] == 401
