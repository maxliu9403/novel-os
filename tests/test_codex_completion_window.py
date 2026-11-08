"""A reply still arriving at the deadline may finish once, within a hard cap."""
import subprocess
import sys
import threading
import time

import pytest
from core.llm_client import LLMClient, LLMCancelledError


class TextObserver:
    config_args = []
    metrics_enabled = False
    def __init__(self, *, text=True, cancel=None):
        self.last_public_text_at = None
        self.started = time.monotonic()
        self.text = text
        self.cancel = cancel
    def poll(self):
        if self.text:
            self.last_public_text_at = time.monotonic()
        if self.cancel and time.monotonic() - self.started > .65:
            self.cancel.set()


class Diagnostic:
    def __init__(self): self.events = []
    def completion_window(self, seconds): self.events.append(seconds)


def test_public_text_can_finish_after_original_deadline_without_another_request():
    observer, diagnostic = TextObserver(), Diagnostic()
    result = LLMClient._run_cancellable_cli(
        [sys.executable, '-c', 'import time; time.sleep(.8); print("complete")'], '', .5,
        threading.Event(), upstream_observer=observer, diagnostics=diagnostic,
        completion_grace_seconds=1)
    assert result.stdout.strip() == 'complete'
    assert diagnostic.events == [1]


@pytest.mark.parametrize('text', [False, True])
def test_no_content_gets_no_extension_and_continuous_text_cannot_extend_forever(text):
    started = time.monotonic()
    diagnostic = Diagnostic()
    with pytest.raises(subprocess.TimeoutExpired):
        LLMClient._run_cancellable_cli(
            [sys.executable, '-c', 'import time; time.sleep(30)'], '', .5, threading.Event(),
            upstream_observer=TextObserver(text=text), diagnostics=diagnostic, completion_grace_seconds=.4)
    assert time.monotonic() - started < 2
    assert diagnostic.events == ([.4] if text else [])


def test_completion_window_remains_cancellable():
    cancel = threading.Event()
    with pytest.raises(LLMCancelledError):
        LLMClient._run_cancellable_cli(
            [sys.executable, '-c', 'import time; time.sleep(30)'], '', .5, cancel,
            upstream_observer=TextObserver(cancel=cancel), completion_grace_seconds=1)


def test_stale_text_does_not_open_a_window():
    class Stale(TextObserver):
        def poll(self): self.last_public_text_at = time.monotonic() - 60
    diagnostic = Diagnostic()
    with pytest.raises(subprocess.TimeoutExpired):
        LLMClient._run_cancellable_cli([sys.executable, '-c', 'import time;time.sleep(30)'], '', .2,
            threading.Event(), diagnostics=diagnostic, upstream_observer=Stale(), completion_grace_seconds=1)
    assert diagnostic.events == []


def test_stalled_text_ends_an_open_window():
    class Stalled(TextObserver):
        def poll(self):
            self.last_public_text_at = time.monotonic() - (60 if time.monotonic()-self.started > .7 else 0)
    diagnostic = Diagnostic()
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        LLMClient._run_cancellable_cli([sys.executable, '-c', 'import time;time.sleep(30)'], '', .5,
            threading.Event(), diagnostics=diagnostic, upstream_observer=Stalled(), completion_grace_seconds=180)
    assert diagnostic.events == [180]
    assert time.monotonic()-start < 2


def test_cached_client_cannot_extend_a_pipeline_runtime_budget(tmp_path, monkeypatch):
    from core.pipeline_runtime import PipelineRuntime
    monkeypatch.setattr(LLMClient, '_build_backend', lambda self, _: ('fake', 'fake'))
    monkeypatch.setattr(LLMClient, '_complete_text', lambda *args: 'complete')
    events = []
    client = LLMClient(provider='codex', completion_grace_seconds=180, diagnostic_callback=events.append)
    with PipelineRuntime(tmp_path, 'run'):
        assert client.complete('system', 'user') == 'complete'
    assert next(e for e in events if e['event']=='request.start')['completion_grace_limit'] == 0
