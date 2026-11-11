from types import SimpleNamespace

import pytest

from core.source_request_recovery import classify_failure


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
