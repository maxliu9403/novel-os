"""CLI input delivery regressions using only bounded, local subprocesses."""

import hashlib
import io
import json
import subprocess
import sys
import threading
import time

import pytest

import llm_client
from llm_client import LLMCancelledError, LLMClient


UNICODE_PROMPT = "角色：林舟\n线索—海岸🌊\r\n\"quoted\"\\路径\t" * 12000


def fingerprint(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


@pytest.fixture
def input_files(monkeypatch):
    """Retain references so closing resources can be checked after every exit."""
    opened = []
    temporary_file = llm_client.tempfile.TemporaryFile

    def track(*args, **kwargs):
        stream = temporary_file(*args, **kwargs)
        opened.append(stream)
        return stream

    monkeypatch.setattr(llm_client.tempfile, "TemporaryFile", track)
    yield opened
    for stream in opened:
        stream.close()


@pytest.fixture
def no_background_workers():
    existing = set(threading.enumerate())
    yield
    assert set(threading.enumerate()) <= existing, "CLI left a background I/O worker running"


class Polls:
    def __init__(self, cancel=None):
        self.count = 0
        self.cancel = cancel

    def heartbeat(self):
        self.count += 1
        if self.cancel is not None:
            self.cancel.set()


def test_delayed_reader_receives_exact_large_unicode_prompt(
    input_files, no_background_workers,
):
    """The child must survive several polls before reading more than pipe capacity."""
    polls = Polls()
    command = [sys.executable, "-c", """
import hashlib, json, sys, time
time.sleep(0.45)
data = sys.stdin.buffer.read()
print(json.dumps({'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}))
"""]
    result = LLMClient._run_cancellable_cli(
        command, UNICODE_PROMPT, 3, threading.Event(), diagnostics=polls,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == fingerprint(UNICODE_PROMPT.encode("utf-8"))
    assert polls.count >= 3
    assert input_files and all(stream.closed for stream in input_files)


def test_polling_does_not_rely_on_resuming_unfinished_communicate_input(
    monkeypatch, input_files,
):
    """Model Python 3.11's stdin retry behavior even on newer test interpreters.

    On 3.11, communicate(None) after a timeout does not register an unfinished
    stdin pipe for writing. A small process double preserves that behavior;
    the real delayed-reader test above checks actual bytes and subprocess I/O.
    """
    class Python311Process:
        def __init__(self, command, *, stdin, **kwargs):
            self.command = command
            self.pid = 123456789
            self.returncode = None
            self.stdout, self.stderr = io.StringIO(), io.StringIO()
            self.stdin = io.StringIO() if stdin == subprocess.PIPE else None
            self.data = None if self.stdin is not None else stdin.read()
            self.calls = 0

        def communicate(self, input=None, timeout=None):
            self.calls += 1
            if self.stdin is not None:
                # The first call accepts a pipe-sized prefix, then times out.
                # Later calls without input cannot advance that transfer.
                if self.calls == 1:
                    assert input is not None and len(input.encode("utf-8")) > 65536
                else:
                    assert input is None
                time.sleep(min(timeout, 0.005))
                raise subprocess.TimeoutExpired(self.command, timeout)
            if self.calls == 1:
                raise subprocess.TimeoutExpired(self.command, timeout)
            self.returncode = 0
            return json.dumps(fingerprint(self.data)), ""

        def wait(self, timeout=None):
            self.returncode = 0
            return self.returncode

    monkeypatch.setattr(llm_client.subprocess, "Popen", Python311Process)
    monkeypatch.setattr(llm_client.os, "killpg", lambda *args: None)

    result = LLMClient._run_cancellable_cli(
        ["local-contract-stub"], UNICODE_PROMPT, 3, threading.Event(),
    )
    assert json.loads(result.stdout) == fingerprint(UNICODE_PROMPT.encode("utf-8"))
    assert input_files and all(stream.closed for stream in input_files)


@pytest.mark.parametrize("outcome", ["success", "nonzero", "timeout", "cancel", "launch_error"])
def test_input_is_closed_on_every_process_exit(
    outcome, tmp_path, input_files, no_background_workers,
):
    event = threading.Event()
    body = "import sys; sys.stdin.buffer.read(); print('done')"
    if outcome == "nonzero":
        body += "; sys.exit(7)"
    elif outcome in {"timeout", "cancel"}:
        body = "import time; time.sleep(10)"
    command = [sys.executable, "-c", body]
    if outcome == "launch_error":
        command = [str(tmp_path / "missing-cli")]
    timeout = 0.2 if outcome == "timeout" else 3
    diagnostics = Polls(cancel=event) if outcome == "cancel" else None
    expected_error = {
        "timeout": subprocess.TimeoutExpired,
        "cancel": LLMCancelledError,
        "launch_error": OSError,
    }.get(outcome)

    if expected_error is not None:
        with pytest.raises(expected_error):
            LLMClient._run_cancellable_cli(
                command, UNICODE_PROMPT, timeout, event, diagnostics=diagnostics,
            )
    else:
        result = LLMClient._run_cancellable_cli(command, UNICODE_PROMPT, timeout, event)
        assert result.returncode == (7 if outcome == "nonzero" else 0)
        assert result.stdout == "done\n"

    assert input_files and all(stream.closed for stream in input_files)


@pytest.mark.parametrize("preparation", ["cancel", "write_error"])
def test_preparation_interrupt_closes_input_and_never_launches_process(
    preparation, monkeypatch, input_files, no_background_workers,
):
    event = threading.Event()
    tracked_file = llm_client.tempfile.TemporaryFile

    class InterruptedInput:
        def __init__(self, *args, **kwargs):
            self.stream = tracked_file(*args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, data):
            if preparation == "write_error":
                raise OSError("input storage unavailable")
            written = self.stream.write(data)
            event.set()
            return written

        def seek(self, offset):
            return self.stream.seek(offset)

    def unexpected_launch(*args, **kwargs):
        pytest.fail("A process was launched after input preparation was interrupted")

    monkeypatch.setattr(llm_client.tempfile, "TemporaryFile", InterruptedInput)
    monkeypatch.setattr(llm_client.subprocess, "Popen", unexpected_launch)
    expected_error = LLMCancelledError if preparation == "cancel" else OSError
    with pytest.raises(expected_error):
        LLMClient._run_cancellable_cli(["never-start"], UNICODE_PROMPT, 3, event)

    assert input_files and all(stream.closed for stream in input_files)
