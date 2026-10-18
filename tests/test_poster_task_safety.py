"""Provider diagnostics must remain useful without publishing credentials."""
import json

import pytest

from api.poster_tasks import error_message


@pytest.mark.parametrize("message,secrets", [
    ('{"access_token":"private-jwt","api_key":"non-sk-secret"}', ["private-jwt", "non-sk-secret"]),
    ("{'client_secret': 'contains spaces and punctuation', 'refresh-token': 'refresh-private'}",
     ["contains spaces and punctuation", "refresh-private"]),
    (json.dumps({"api_key": 'escaped "quote" and slash\\credential'}), ["quote", "credential"]),
    ("Authorization: Basic dXNlcjpwYXNz", ["dXNlcjpwYXNz"]),
    ("authorization = Bearer jwt-private-token", ["jwt-private-token"]),
    ('headers={"Authorization": "Bearer private-jwt", "x-api-key": "proxy-private"}',
     ["private-jwt", "proxy-private"]),
    ("provider failed Bearer upstream-private", ["upstream-private"]),
    ("api_key=proxy-private; access_token=token-private", ["proxy-private", "token-private"]),
    ("password='multi word private'; token=opaque-private", ["multi word private", "opaque-private"]),
    ("Cookie: session=private-session; model=gpt-fixture", ["private-session"]),
    ("request https://provider.invalid/responses?key=query-private&model=gpt-fixture",
     ["query-private"]),
    ("request https://provider.invalid/responses?access%5Ftoken=encoded-private&token=other-private",
     ["encoded-private", "other-private"]),
    ("request https://provider.invalid/object?X-Amz-Credential=cloud-private&X-Amz-Signature=signed-private",
     ["cloud-private", "signed-private"]),
    ("request https://username:user-password@provider.invalid/responses?model=gpt-fixture",
     ["username", "user-password"]),
    ("upstream returned sk-provider-opaque-secret", ["sk-provider-opaque-secret"]),
])
def test_quoted_headers_query_parameters_and_non_openai_keys_are_redacted(message, secrets):
    rendered = error_message(ValueError(message))
    assert "redacted" in rendered
    for secret in secrets:
        assert secret not in rendered


def test_budget_diagnostics_remain_readable_and_only_known_integer_fields_are_added():
    failure = ValueError("campaign request exceeds its byte budget; stage=create_campaign; input_tokens=2048")
    failure.details = {"actual_bytes": 422108, "limit_bytes": 384000, "reserved_tokens": 12000,
                       "input_tokens": True, "api_key": "private-secret", "raw": "request-private"}
    rendered = error_message(failure)
    assert "stage=create_campaign" in rendered
    assert "input_tokens=2048" in rendered
    assert "actual_bytes=422108" in rendered and "limit_bytes=384000" in rendered
    assert "reserved_tokens=12000" in rendered
    assert "private" not in rendered and "True" not in rendered


def test_non_mapping_details_and_oversized_provider_errors_are_safe():
    failure = ValueError('x' * 3900 + ' {"api_key":"private-tail"}')
    failure.details = ["unexpected provider details"]
    rendered = error_message(failure)
    assert len(rendered) <= 4000
    assert "private-tail" not in rendered


def test_nonsecret_url_routing_and_model_context_remain_available():
    result = error_message(ValueError("503 https://provider.invalid/v1/responses?model=gpt-fixture&key=private"))
    assert "503" in result and "provider.invalid/v1/responses" in result
    assert "model=gpt-fixture" in result and "private" not in result
