"""Content-free classification of native diagnostics, never raw error storage.

A code describes an observed diagnostic, not the terminal outcome of a turn.
In particular a model-catalog refresh failure is not a generation failure.
"""
import re

ERROR_CODES = frozenset({
    'authentication', 'rate_limit', 'context_length', 'invalid_request', 'model_unavailable',
    'upstream_timeout', 'transport_error', 'server_error', 'config_error', 'sandbox_error',
    'unclassified_error',
})
COMPONENTS = frozenset({'model_catalog', 'model_transport', 'telemetry', 'configuration', 'sandbox', 'cli'})
LEVELS = frozenset({'error', 'warning'})


def error_code(message):
    """Recognize bounded patterns without returning any caller-provided text."""
    text = message[:4096].lower() if isinstance(message, str) else ''
    for code, patterns in (
        ('context_length', ('context_length_exceeded', 'maximum context length', 'context window exceeded')),
        ('authentication', ('unauthorized', 'authentication', 'invalid_api_key', 'token expired')),
        ('rate_limit', ('rate_limit', 'rate limit', 'too many requests')),
        ('model_unavailable', ('model_not_found', 'model is not supported', 'model not found')),
        ('config_error', ('error loading config', 'invalid config', 'toml parse')),
        ('sandbox_error', ('bwrap:', 'sandbox helper failed')),
        ('upstream_timeout', ('deadline has elapsed', 'timed out', 'timeout', 'deadline exceeded')),
        ('transport_error', ('stream disconnected', 'connection reset', 'connection refused', 'connection closed',
                             'websocket closed', 'error sending request', 'transport error', 'network error',
                             'dns error', 'failed to connect', 'unexpected eof')),
        ('invalid_request', ('invalid_request', 'invalid request', 'unsupported parameter')),
        ('server_error', ('internal_server_error', 'internal server error', 'service unavailable', 'bad gateway')),
    ):
        if any(pattern in text for pattern in patterns):
            return code
    status = re.search(r'(?:http(?:/\d(?:\.\d)?)?\s+|status(?:\s+code)?\s*[:=]?\s*)(401|403|429|5\d\d)\b', text)
    if status:
        return 'authentication' if status[1] in {'401', '403'} else 'rate_limit' if status[1] == '429' else 'server_error'
    return 'unclassified_error'


def stderr_metadata(message):
    # Anchor to a logger prefix rather than interpreting story prose as errors.
    text = message[:4096] if isinstance(message, str) else ''
    match = re.match(r'^(?:\x1b\[[0-9;]*m)*(?:[0-9T:.+Z-]+\s+)?(ERROR|WARN|WARNING)\s+(?:\x1b\[[0-9;]*m)*([^\s]+)', text)
    if not match:
        return None
    target = match[2].lower()
    component = ('model_catalog' if 'models_manager' in target else
                 'model_transport' if target.startswith(('codex_core::client', 'codex_api::', 'codex_client::')) else
                 'telemetry' if 'otel' in target or 'opentelemetry' in target else
                 'configuration' if 'config' in target else
                 'sandbox' if 'sandbox' in target else 'cli')
    return {'diagnostic_code': error_code(text), 'diagnostic_component': component,
            'diagnostic_level': 'error' if match[1] == 'ERROR' else 'warning'}
