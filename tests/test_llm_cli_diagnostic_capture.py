"""Exercise real local subprocess I/O; never call an authenticated model."""
import json
from pathlib import Path
import sys
import threading
import time

import pytest

import llm_client
from llm_client import LLMCancelledError, LLMClient, LLMError


def stub_cli(tmp_path, monkeypatch, body):
    binary = tmp_path / "diagnostic-cli"
    binary.write_text(f"#!{sys.executable}\n" + body)
    binary.chmod(0o700)
    monkeypatch.setattr(llm_client.shutil, "which", lambda _: str(binary))
    return binary


PRELUDE = '''import json,sys,time
from pathlib import Path
assert '--json' in sys.argv
prompt = sys.stdin.read()
output = Path(sys.argv[sys.argv.index('--output-last-message') + 1])
print(json.dumps({'type':'thread.started','thread_id':'12345678-1234-1234-1234-123456789abc'}),flush=True)
print(json.dumps({'type':'turn.started'}),flush=True)
print(json.dumps({'type':'item.completed','item':{'type':'reasoning','text':'PRIVATE_REASONING_SECRET'}}),flush=True)
'''


def test_cli_timeout_retains_progress_and_result_metadata_without_content(tmp_path, monkeypatch):
    stub_cli(tmp_path, monkeypatch, PRELUDE + '''
print('Reconnecting... 1/5 HTTP 503 Bearer AUTH_SECRET', file=sys.stderr, flush=True)
output.write_text('PARTIAL_OUTPUT_SECRET')
time.sleep(10)
''')
    events = []
    client = LLMClient(provider="codex", model="local-stub", reasoning_effort="ultra",
                       timeout_seconds=2, cancellation_event=threading.Event(),
                       diagnostic_callback=events.append)
    with pytest.raises(LLMError, match="timed out"):
        client.complete("SYSTEM_SECRET", "AUTHOR_SECRET")

    names = [entry["event"] for entry in events]
    assert names[0] == "request.start"
    assert "process_started" in names and "first_output" in names
    assert "first_cli_event" in names
    assert names[-2:] == ["cli.timeout", "request.timeout"]
    timeout = events[-2]
    assert timeout["stdout_bytes"] > 0 and timeout["stderr_bytes"] > 0
    assert timeout["first_output_seconds"] is not None
    assert timeout["last_output_seconds"] is not None
    assert timeout["final_output_exists"] is True
    assert timeout["final_output_bytes"] == len("PARTIAL_OUTPUT_SECRET")
    assert timeout["retry_count"] >= 1
    assert timeout["http_status"] == 503
    assert len({e["request_id"] for e in events}) == 1
    assert all(e["time"] for e in events)
    logged = json.dumps(events)
    for secret in ["SYSTEM_SECRET", "AUTHOR_SECRET", "PRIVATE_REASONING_SECRET", "AUTH_SECRET", "PARTIAL_OUTPUT_SECRET"]:
        assert secret not in logged


def test_cli_success_returns_exact_final_message_and_emits_terminal_metadata(tmp_path, monkeypatch):
    stub_cli(tmp_path, monkeypatch, PRELUDE + '''
time.sleep(.15)
assert prompt.count('AUTHOR_SECRET') == 1
output.write_text('FINAL_ARTIFACT_SECRET')
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':120,'output_tokens':40}}),flush=True)
''')
    events = []
    client = LLMClient(provider="codex", timeout_seconds=2, diagnostic_callback=events.append)
    assert client.complete("system" * 30000, "AUTHOR_SECRET") == "FINAL_ARTIFACT_SECRET"
    assert events[-2]["event"] == "cli.completed"
    assert events[-2]["exit_code"] == 0
    assert events[-1]["event"] == "request.completed"
    assert events[0]["system_chars"] == 180000
    assert "FINAL_ARTIFACT_SECRET" not in json.dumps(events)


def test_cli_cancel_keeps_last_activity_and_stops_quickly(tmp_path, monkeypatch):
    stub_cli(tmp_path, monkeypatch, PRELUDE + "time.sleep(10)\n")
    events, outcomes = [], []
    cancel, observed = threading.Event(), threading.Event()

    def log(entry):
        events.append(entry)
        if entry["event"] == "first_cli_event":
            observed.set()

    client = LLMClient(provider="codex", timeout_seconds=30, cancellation_event=cancel,
                       diagnostic_callback=log)

    def run():
        try:
            client.complete("system", "author")
        except BaseException as exc:
            outcomes.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert observed.wait(2)
        started = time.monotonic()
        cancel.set()
        worker.join(2)
        assert not worker.is_alive()
        assert time.monotonic() - started < 2
        assert isinstance(outcomes[0], LLMCancelledError)
        assert [e["event"] for e in events][-2:] == ["cli.cancelled", "request.cancelled"]
        assert events[-2]["stdout_bytes"] > 0
    finally:
        cancel.set()
        worker.join(2)


def test_broken_log_sink_does_not_change_model_result(tmp_path, monkeypatch):
    stub_cli(tmp_path, monkeypatch, PRELUDE + "output.write_text('result')\n")

    def broken(_):
        raise OSError("disk unavailable")

    client = LLMClient(provider="codex", timeout_seconds=2, diagnostic_callback=broken)
    assert client.complete("role", "task") == "result"


def test_no_output_timeout_and_nonzero_exit_are_distinct(tmp_path, monkeypatch):
    events = []
    stub_cli(tmp_path, monkeypatch, "import sys,time\nsys.stdin.read()\ntime.sleep(10)\n")
    with pytest.raises(LLMError, match="timed out"):
        LLMClient(provider="codex", timeout_seconds=.3, diagnostic_callback=events.append).complete("role", "task")
    assert events[-2]["event"] == "cli.timeout"
    assert events[-2]["stdout_bytes"] == 0
    assert events[-2]["first_output_seconds"] is None

    events.clear()
    stub_cli(tmp_path, monkeypatch, "import sys\nsys.stdin.read()\nprint('HTTP 401 Unauthorized AUTH_SECRET',file=sys.stderr,flush=True)\nsys.exit(3)\n")
    with pytest.raises(LLMError):
        LLMClient(provider="codex", timeout_seconds=2, diagnostic_callback=events.append).complete("role", "task")
    assert events[-2]["event"] == "cli.failed"
    assert events[-2]["exit_code"] == 3
    assert events[-2]["http_status"] == 401
    assert events[-1]["event"] == "request.failed"
    assert "AUTH_SECRET" not in json.dumps(events)


def test_large_skeleton_cli_event_counts_visible_reply_without_logging_body(tmp_path, monkeypatch):
    stub_cli(tmp_path, monkeypatch, PRELUDE + '''
text = '私密章节' * 11000
output.write_text(text)
print(json.dumps({'type':'item.completed','item':{'id':'large-final','type':'agent_message','text':text}},ensure_ascii=False),flush=True)
print(json.dumps({'type':'turn.completed','usage':{'output_tokens':21000}}),flush=True)
''')
    events = []
    client = LLMClient(provider="codex", timeout_seconds=2, diagnostic_callback=events.append)
    result = client.complete("role", "task")
    completed = next(entry for entry in events if entry["event"] == "cli.completed")
    assert len(result) == 44000
    assert completed["model_output_chars"] == len(result)
    assert completed["discarded_stdout_lines"] == 0
    assert completed["first_model_output_seconds"] is not None
    assert completed["final_output_bytes"] == len(result.encode())
    assert "私密章节" not in json.dumps(events, ensure_ascii=False)
