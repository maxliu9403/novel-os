"""Real local collector/fake CLI coverage; no authenticated model calls."""
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from core import codex_telemetry
from core.codex_telemetry import CodexTelemetry, project_batch, project_record
import llm_client
from llm_client import LLMClient, LLMError, LLMCancelledError


def record(event="codex.sse_event", **attrs):
    attrs = {"event.name": event, **attrs}
    return {"attributes": [{"key": key, "value": {
        "boolValue" if type(value) is bool else "intValue" if type(value) is int else "stringValue": value,
    }} for key, value in attrs.items()]}


def batch(*records):
    return {"resourceLogs": [{"resource": {"attributes": [{
        "key": "user.email", "value": {"stringValue": "ACCOUNT_SECRET"},
    }]}, "scopeLogs": [{"logRecords": list(records)}]}]}


def endpoint(collector):
    config = collector.config_args[-1]
    return config.split('endpoint="', 1)[1].split('"', 1)[0]


def post(url, payload):
    request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=2) as response:
        return response.status


def test_metadata_projection_is_strict_and_excludes_credentials_content_and_reasoning():
    safe = project_record(record(**{
        "event.kind": "response.completed", "event.timestamp": "2026-10-08T07:25:29.679Z",
        "input_token_count": "53007", "output_token_count": "23", "reasoning_token_count": "1500",
        "duration_ms": "100.5", "ttft_ms": "4695", "attempt": "2",
        "http.response.status_code": "200", "success": "true",
        "reasoning_effort": "ultra", "reasoning_summary": "auto",
        "endpoint": "https://TOKEN_SECRET@private.example/v1/models?api_key=KEY_SECRET",
        "prompt": "PROMPT_SECRET", "response": "OUTPUT_SECRET", "reasoning": "REASONING_SECRET",
        "error": "ERROR_SECRET", "user.email": "ACCOUNT_SECRET", "conversation.id": "ID_SECRET",
        "model": "MODEL_SECRET",
    }))
    assert safe == {
        "event": "upstream.event", "upstream_event": "codex.sse_event",
        "event_kind": "response.completed", "source_timestamp": "2026-10-08T07:25:29.679000+00:00",
        "input_token_count": 53007, "output_token_count": 23, "reasoning_token_count": 1500,
        "duration_ms": 100.5, "ttft_ms": 4695, "upstream_attempt": 2,
        "http_status": 200, "success": True, "reasoning_effort": "ultra", "reasoning_summary": "auto",
        "endpoint_category": "models",
    }
    assert "SECRET" not in json.dumps(safe)
    assert list(project_batch(batch(record("codex.user_prompt", prompt="SECRET")))) == []


def test_malformed_metadata_never_escapes_and_work_is_bounded():
    safe = project_record(record(**{
        "event.kind": "SECRET", "reasoning_effort": "SECRET", "reasoning_summary": "SECRET",
        "output_token_count": True, "duration_ms": str(10**100), "ttft_ms": "nan",
        "success": "SECRET", "event.timestamp": "SECRET", "http.response.status_code": -1,
    }))
    assert safe == {"event": "upstream.event", "upstream_event": "codex.sse_event"}
    assert project_record({"attributes": {}}) is None
    assert project_record({"attributes": [None]}) is None
    assert len(list(project_batch(batch(*[record() for _ in range(1000)])))) == 256


def test_metrics_exporter_is_independent_and_projects_content_free_activity():
    from test_codex_metric_activity import payload
    events = []
    collector = CodexTelemetry(events.append).start()
    try:
        config = next(arg for arg in collector.config_args if arg.startswith('otel.metrics_exporter='))
        metrics_url = config.split('endpoint="', 1)[1].split('"', 1)[0]
        assert post(metrics_url, payload(count=3)) == 200
        assert post(metrics_url, payload(count=3)) == 200
        collector.poll()
        rows = [row for row in events if row['event'] == 'upstream.activity']
        assert len(rows) == 1 and rows[0]['metric_count'] == 3
        assert rows[0]['activity_kind'] == 'text' and rows[0]['observed_at']
        assert 'PRIVATE' not in json.dumps(events)
        assert not any('usage' in row or 'output_token_count' in row for row in rows)
    finally:
        collector.close()


def test_ttft_is_safe_metadata_and_never_body_activity():
    projected = project_record(record('codex.turn_ttft', duration_ms='120', prompt='PRIVATE'))
    assert projected == {'event': 'upstream.event', 'upstream_event': 'codex.turn_ttft', 'duration_ms': 120}


def test_metrics_disabled_preserves_logs_and_does_not_expose_metrics_route():
    events = []
    collector = CodexTelemetry(events.append, enable_metrics=False).start()
    try:
        assert not any(arg.startswith('otel.metrics_exporter=') for arg in collector.config_args)
        assert post(endpoint(collector), batch(record(**{'event.kind': 'response.completed'}))) == 200
        collector.poll()
        assert any(event['event'] == 'upstream.event' for event in events)
        with pytest.raises(HTTPError) as caught:
            post(endpoint(collector).removesuffix('logs') + 'metrics', {})
        assert caught.value.code == 404
        assert not any(event['event'] == 'upstream.activity' for event in events)
    finally:
        collector.close()


def test_collector_receives_filters_and_stops_without_logging_raw_payload():
    events = []
    collector = CodexTelemetry(events.append).start()
    url = endpoint(collector)
    try:
        assert post(url, batch(record(**{"event.kind": "response.output_text.delta", "text": "SECRET"}))) == 200
        collector.poll()
        assert any(e.get("event_kind") == "response.output_text.delta" for e in events)
        assert "SECRET" not in json.dumps(events)
        assert "ACCOUNT_SECRET" not in json.dumps(events)
        request = Request(url, data=b"{}", headers={"Content-Length": str(2 * 1024 * 1024)})
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=2)
        assert error.value.code == 413
    finally:
        collector.close()
    assert not collector._thread.is_alive()
    assert events[-1]["event"] == "upstream.observer_stopped"
    with pytest.raises(URLError):
        post(url, {})


def test_close_interrupts_incomplete_http_request_and_callback_errors_are_nonfatal():
    def broken(_):
        raise OSError("PRIVATE_ERROR")

    collector = CodexTelemetry(broken).start()
    connection = socket.create_connection(("127.0.0.1", collector._server.server_port))
    try:
        # An incomplete HTTP header must not leave a lingering receiver thread.
        connection.sendall(b"POST / HTTP/1.1\r\nX-Slow: ")
        time.sleep(.03)
        started = time.monotonic()
        collector.close()
        assert time.monotonic() - started < 1
        assert not collector._thread.is_alive()
    finally:
        connection.close()
        collector.close()


FAKE_CLI = '''import json,os,re,sys,time,urllib.request
from pathlib import Path
prompt = sys.stdin.read()
assert '--json' in sys.argv
assert 'otel.log_user_prompt=false' in sys.argv
assert os.environ['OTEL_METRIC_EXPORT_INTERVAL'] == '5000'
assert os.environ['NOVEL_TEST_INHERITED_ENV'] == 'preserved'
assert not any('max_tokens' in arg or 'max_output_tokens' in arg for arg in sys.argv)
config = next(arg for arg in sys.argv if arg.startswith('otel.exporter='))
url = re.search(r'endpoint="([^"]+)"', config).group(1)
attrs = {'event.name':'codex.sse_event','event.kind':'response.completed',
         'event.timestamp':'2026-10-08T07:25:29.679Z',
         'output_token_count':'0','reasoning_token_count':'0','prompt':'PROMPT_SECRET',
         'user.email':'ACCOUNT_SECRET'}
payload = {'resourceLogs':[{'scopeLogs':[{'logRecords':[{'attributes':[
    {'key':k,'value':{'stringValue':v}} for k,v in attrs.items()]}]}]}]}
with urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(payload).encode()),timeout=2) as response:
    assert response.status == 200
print(json.dumps({'type':'turn.started'}),flush=True)
output = Path(sys.argv[sys.argv.index('--output-last-message')+1])
'''


def stub(tmp_path, monkeypatch, body):
    path = tmp_path / "fake-codex"
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)
    monkeypatch.setattr(llm_client.shutil, "which", lambda _: str(path))


@pytest.mark.parametrize("outcome", ["success", "timeout", "cancel", "error"])
def test_fake_cli_telemetry_reaches_callback_and_cleans_up_for_every_outcome(tmp_path, monkeypatch, outcome):
    monkeypatch.setenv('OTEL_METRIC_EXPORT_INTERVAL', '90000')
    monkeypatch.setenv('NOVEL_TEST_INHERITED_ENV', 'preserved')
    body = FAKE_CLI
    if outcome == "success":
        body += "output.write_text('FINAL_SECRET')\n"
    elif outcome == "error":
        body += "sys.exit(2)\n"
    else:
        body += "time.sleep(30)\n"
    stub(tmp_path, monkeypatch, body)
    events = []
    cancel = threading.Event()

    def emit(event):
        events.append(event)
        if outcome == "cancel" and event["event"] == "upstream.event":
            cancel.set()

    # Give the fresh Python child time to import urllib and deliver its local
    # telemetry before testing timeout cleanup. The timeout branch then sleeps
    # for 30 seconds, so the same 3-second limit still forces a real timeout.
    client = LLMClient(provider="codex", model="fake", max_tokens=8192,
                       timeout_seconds=3,
                       cancellation_event=cancel, diagnostic_callback=emit)
    if outcome == "success":
        assert client.complete("SYSTEM_SECRET", "AUTHOR_SECRET") == "FINAL_SECRET"
    else:
        with pytest.raises(LLMCancelledError if outcome == "cancel" else LLMError):
            client.complete("SYSTEM_SECRET", "AUTHOR_SECRET")
    assert events[0]["configured_max_tokens"] == 8192
    assert events[0]["effective_max_tokens"] is None
    assert events[0]["max_tokens_supported"] is False
    assert events[0]["timeout_scope"] == "local_process"
    assert any(e.get("event_kind") == "response.completed" and e["output_token_count"] == 0 for e in events)
    names = [e["event"] for e in events]
    assert names.index("upstream.observer_stopped") < len(names) - 2
    assert not any(t.name == "codex-otel-local" and t.is_alive() for t in threading.enumerate())
    assert "SECRET" not in json.dumps(events)
    assert os.environ['OTEL_METRIC_EXPORT_INTERVAL'] == '90000'


def test_observer_bind_failure_is_nonfatal(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("PRIVATE_ADDRESS")

    monkeypatch.setattr(codex_telemetry.HTTPServer, "server_bind", fail)
    events = []
    collector = CodexTelemetry(events.append).start()
    assert collector.config_args == []
    assert events[-1]["event"] == "upstream.observer_unavailable"
    collector.close()
    assert "PRIVATE_ADDRESS" not in json.dumps(events)


def test_failed_observer_keeps_child_environment_and_model_completion(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("PRIVATE_ADDRESS")

    # llm_client's standalone import uses the same source under its CLI name.
    import codex_telemetry as cli_telemetry
    monkeypatch.setattr(cli_telemetry.HTTPServer, "server_bind", fail)
    monkeypatch.setenv("OTEL_METRIC_EXPORT_INTERVAL", "81000")
    stub(tmp_path, monkeypatch, '''import os,sys
from pathlib import Path
assert not any('otel.' in arg for arg in sys.argv)
assert os.environ['OTEL_METRIC_EXPORT_INTERVAL'] == '81000'
sys.stdin.read()
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('observer unavailable is nonfatal')
''')
    events = []
    result = LLMClient(provider="codex", timeout_seconds=2, diagnostic_callback=events.append).complete("role", "task")
    assert result == "observer unavailable is nonfatal"
    assert any(event["event"] == "upstream.observer_unavailable" for event in events)
    assert "PRIVATE_ADDRESS" not in json.dumps(events)


def test_calls_without_diagnostic_sink_do_not_configure_exporter(tmp_path, monkeypatch):
    monkeypatch.setenv('OTEL_METRIC_EXPORT_INTERVAL', '70000')
    stub(tmp_path, monkeypatch, '''import os,sys
from pathlib import Path
assert not any('otel' in arg for arg in sys.argv)
assert os.environ['OTEL_METRIC_EXPORT_INTERVAL'] == '70000'
sys.stdin.read()
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('plain')
''')
    assert LLMClient(provider="codex", timeout_seconds=2).complete("role", "task") == "plain"


@pytest.mark.parametrize('with_images', [False, True])
def test_usage_only_codex_keeps_logs_accounting_and_inherited_interval_without_new_metrics(tmp_path, monkeypatch, with_images):
    from core.usage_tracking import read_records, usage_scope
    monkeypatch.setenv('OTEL_METRIC_EXPORT_INTERVAL', '73000')
    body = '''import json,os,re,sys,urllib.request
from pathlib import Path
assert '--json' in sys.argv
assert 'otel.log_user_prompt=false' in sys.argv
assert not any('otel.metrics_exporter' in arg for arg in sys.argv)
assert os.environ['OTEL_METRIC_EXPORT_INTERVAL'] == '73000'
assert 'read-only' in sys.argv
if '--image' in sys.argv:
    assert Path(sys.argv[sys.argv.index('--image')+1]).read_bytes() == b'\\x89PNG\\r\\nPRIVATE_IMAGE'
url = re.search(r'endpoint="([^"]+)"', next(arg for arg in sys.argv if arg.startswith('otel.exporter='))).group(1)
payload = {'resourceLogs':[{'scopeLogs':[{'logRecords':[{'attributes':[
    {'key':'event.name','value':{'stringValue':'codex.sse_event'}},
    {'key':'event.kind','value':{'stringValue':'response.completed'}},
    {'key':'output_token_count','value':{'intValue':'9999'}}]}]}]}]}
with urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(payload).encode()),timeout=2) as response:
    assert response.status == 200
sys.stdin.read()
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':7}}),flush=True)
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('result')
'''
    stub(tmp_path, monkeypatch, body)
    client = LLMClient(provider='codex', timeout_seconds=3)
    with usage_scope(tmp_path / 'owner', 'cover'):
        result = client.complete_with_images('system', 'review', [b'\x89PNG\r\nPRIVATE_IMAGE']) if with_images else client.complete('system', 'plan')
    assert result == 'result'
    rows = list(read_records(tmp_path / 'owner').values())
    assert len(rows) == 1 and rows[0]['status'] == 'completed'
    assert rows[0]['usage']['input_tokens'] == 20 and rows[0]['usage']['output_tokens'] == 7
    assert os.environ['OTEL_METRIC_EXPORT_INTERVAL'] == '73000'
    assert not any(t.name == 'codex-otel-local' and t.is_alive() for t in threading.enumerate())
