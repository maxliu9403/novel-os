"""Cancellation uses local stubs only, never a paid or authenticated model call."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import llm_client
from llm_client import LLMCancelledError, LLMClient


def _wait(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail('Local test worker did not reach the expected state')


@pytest.mark.parametrize('provider', ['codex', 'claude_cli'])
def test_cli_cancel_kills_parent_and_stubborn_descendant_and_removes_temp_files(tmp_path, monkeypatch, provider):
    state = tmp_path / 'started.json'
    child_ready = tmp_path / 'child.ready'
    binary = tmp_path / 'stub-cli'
    binary.write_text(f'''#!{sys.executable}
import json, os, signal, sys, time
from pathlib import Path
child = os.fork()
if child == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path({str(child_ready)!r}).write_text('ready')
    time.sleep(30)
    os._exit(0)
while not Path({str(child_ready)!r}).exists():
    time.sleep(0.01)
output = sys.argv[sys.argv.index('--output-last-message') + 1] if '--output-last-message' in sys.argv else None
Path({str(state)!r}).write_text(json.dumps({{'pid': os.getpid(), 'child': child, 'output': output}}))
time.sleep(30)
''')
    binary.chmod(0o700)
    monkeypatch.setattr(llm_client.shutil, 'which', lambda _: str(binary))
    event = threading.Event()
    client = LLMClient(provider=provider, model='local-stub', timeout_seconds=900, cancellation_event=event)
    outcome = []

    def call():
        try:
            outcome.append(client.complete('role', 'task'))
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    process_state = None
    try:
        _wait(lambda: state.exists())
        process_state = json.loads(state.read_text())
        before_cancel = time.monotonic()
        event.set()
        worker.join(2)
        assert not worker.is_alive(), 'Cancellation waited for the original 15-minute timeout'
        assert time.monotonic() - before_cancel < 2
        assert len(outcome) == 1 and isinstance(outcome[0], LLMCancelledError)
        for pid in (process_state['pid'], process_state['child']):
            def no_longer_running():
                # A killed orphan may await init's reap; a zombie no longer runs.
                status = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)],
                    capture_output=True, text=True, timeout=1).stdout.strip()
                return not status or status.startswith('Z')
            _wait(no_longer_running, 1)
        if provider == 'codex':
            assert not Path(process_state['output']).parent.exists()
    finally:
        event.set()
        worker.join(2)
        if process_state:
            for pid in (process_state['pid'], process_state['child']):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_cli_polling_sends_large_prompt_once_and_returns_normally(tmp_path):
    binary = tmp_path / 'echo-cli'
    binary.write_text(f'#!{sys.executable}\nimport sys,time\ntime.sleep(0.2)\nprint(len(sys.stdin.read()))\n')
    binary.chmod(0o700)
    prompt = 'context-' * 30000
    result = LLMClient._run_cancellable_cli([str(binary)], prompt, 10, threading.Event())
    assert result.returncode == 0
    assert int(result.stdout.strip()) == len(prompt)


def test_cancelled_before_start_never_builds_backend(monkeypatch):
    event = threading.Event()
    event.set()
    monkeypatch.setattr(LLMClient, '_build_backend', lambda *_: pytest.fail('Cancelled call built a backend'))
    with pytest.raises(LLMCancelledError):
        LLMClient(provider='codex', cancellation_event=event)


@pytest.mark.parametrize('provider', ['openai', 'anthropic', 'gemini'])
def test_http_cancel_stops_wait_and_discards_late_result_without_retry(monkeypatch, provider):
    entered, release, closed, event = (threading.Event() for _ in range(4))
    calls = []

    def complete(*args):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return 'late reply must not be accepted'

    def close():
        closed.set()
        # Simulate a transport whose close also blocks; the UI must not wait.
        release.wait(5)

    # Keep the real client invariants (including the configured model) while
    # replacing only the external transport with this blocking local stub.
    monkeypatch.setattr(LLMClient, '_build_backend',
                        lambda self, model: (SimpleNamespace(close=close), model))
    client = LLMClient(provider=provider, model='local-stub', cancellation_event=event)
    monkeypatch.setattr(client, '_complete_openai_shape', complete)
    monkeypatch.setattr(client, '_complete_anthropic', complete)
    monkeypatch.setattr(client, '_complete_gemini', complete)
    outcome = []

    def call():
        try:
            outcome.append(client.complete('role', 'context'))
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    try:
        assert entered.wait(1), repr(outcome)
        event.set()
        worker.join(0.5)
        assert not worker.is_alive()
        assert len(outcome) == 1 and isinstance(outcome[0], LLMCancelledError)
        assert closed.wait(0.5)
        release.set()
        with pytest.raises(LLMCancelledError):
            client.complete('role', 'do not retry')
        assert calls == [True]
    finally:
        release.set()
        event.set()
        worker.join(1)


def test_http_stream_cancel_closes_stream_and_client(monkeypatch):
    entered, event, stream_closed, backend_closed = (threading.Event() for _ in range(4))

    class Stream:
        def __iter__(self):
            entered.set()
            stream_closed.wait(5)
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='too late'))])])

        def close(self):
            stream_closed.set()

    client = object.__new__(LLMClient)
    client.provider_name = 'openai'
    client.model, client.max_tokens = 'stub', 128
    client.cancellation_event = event
    client._backend = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: Stream())),
        close=backend_closed.set,
    )
    monkeypatch.delenv('NOVEL_OS_LLM_STREAMING', raising=False)
    outcome = []

    def call():
        try:
            client.complete('role', 'context')
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        event.set()
        worker.join(0.5)
        assert len(outcome) == 1 and isinstance(outcome[0], LLMCancelledError)
        assert stream_closed.wait(0.5) and backend_closed.wait(0.5)
    finally:
        event.set()
        stream_closed.set()
        worker.join(1)


@pytest.mark.parametrize('provider', ['openai', 'openai_compatible', 'anthropic', 'azure'])
def test_cancellable_sdk_clients_disable_native_retries(monkeypatch, provider):
    captured = []

    class Backend:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=Backend, AzureOpenAI=Backend))
    monkeypatch.setitem(sys.modules, 'anthropic', SimpleNamespace(Anthropic=Backend))
    monkeypatch.setenv('NOVEL_OS_SDK_MAX_RETRIES', '7')
    LLMClient(provider=provider, model='stub', api_key='local-key', base_url='https://model.example/v1',
        azure_endpoint='https://azure.example', timeout_seconds=900, cancellation_event=threading.Event())
    assert captured[0]['max_retries'] == 0
    assert captured[0]['timeout'] == 900


def test_default_none_keeps_synchronous_http_path():
    client = object.__new__(LLMClient)
    client.cancellation_event = None
    calling_thread = threading.get_ident()
    assert client._call_http(lambda: threading.get_ident()) == calling_thread
