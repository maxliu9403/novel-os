"""Codex reference-read failures reproduced without a real CLI or model call.

The stderr fragment is the observed reference-reader sandbox failure, not an
image model rejection. Popen and clocks are local doubles, so these tests do not
loosen any container security settings or consume image calls. No unobserved tool
event schema is assumed.
"""
import io
import json
import subprocess

import pytest

from core import image_client, usage_tracking
from core.image_client import CodexImageGenerationClient, ImageClientError
from tests.test_image_edit_client import picture, reference, settings  # noqa: F401


REFERENCE_FAILURE = (
    "unable to read referenced image /private/PRIVATE_STORY/reference.png: "
    "fs sandbox helper failed: bwrap: No permissions to create a new namespace"
)


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(image_client.shutil, "which", lambda _name: "/fake/codex")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "unused-codex-home"))
    return CodexImageGenerationClient(settings(provider="codex", model="codex-image-auto",
                                              timeout_seconds=1))


class _Process:
    pid = 177

    def __init__(self, return_code):
        self.stdin = io.StringIO()
        self.return_code = return_code
        self.poll_count = 0

    def poll(self):
        self.poll_count += 1
        return self.return_code


def _process_double(monkeypatch, client, *, stdout="", stderr="", return_code=0, output=None):
    launched, cleaned = [], []
    process = _Process(return_code)

    def launch(command, **kwargs):
        launched.append(command)
        kwargs["stdout"].write(stdout)
        kwargs["stdout"].flush()
        kwargs["stderr"].write(stderr)
        kwargs["stderr"].flush()
        if output is not None:
            from pathlib import Path
            (Path(command[command.index("--cd") + 1]) / "cover.png").write_bytes(output)
        return process

    # A deterministic fake timeout avoids sleeping or launching a background
    # process if the regression causes the structured error to be ignored.
    tick = iter(i / 10 for i in range(1000))
    monkeypatch.setattr(image_client.subprocess, "Popen", launch)
    monkeypatch.setattr(image_client.time, "monotonic", lambda: next(tick))
    monkeypatch.setattr(image_client.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(client, "_terminate_process_group", lambda active: cleaned.append(active))
    return process, launched, cleaned


@pytest.mark.parametrize("helper", ["fs sandbox helper failed", "bwrap: No permissions to create a new namespace"])
@pytest.mark.parametrize("return_code", [0, None])
def test_reference_sandbox_failure_is_terminal_even_if_codex_does_not_exit_nonzero(
        monkeypatch, tmp_path, reference, helper, return_code):
    client = _client(monkeypatch, tmp_path)
    stdout = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 15, "output_tokens": 4}}) + "\n"
    stderr = "unable to read referenced image /private/PRIVATE_STORY/reference.png: " + helper + "\n"
    process, launched, cleaned = _process_double(monkeypatch, client, stdout=stdout,
        stderr=stderr, return_code=return_code)
    events = []
    monkeypatch.setattr(usage_tracking, "record_usage", lambda **event: events.append(event))
    original = reference.read_bytes()

    with pytest.raises(ImageClientError) as caught:
        client.generate("Improve only the lighting")

    assert "reference" in str(caught.value).casefold()
    assert "sandbox" in str(caught.value).casefold()
    assert "PRIVATE_STORY" not in str(caught.value)
    assert not caught.value.retryable
    assert len(launched) == 1 and process.poll_count <= 1
    assert cleaned == [process] and reference.read_bytes() == original
    image_events = [event for event in events if event["kind"] == "image"]
    assert [event["status"] for event in image_events] == ["started", "failed"]
    assert image_events[-1]["image_count"] is None
    assert [event["usage"] for event in events if event["kind"] == "text"] == [
        {"input_tokens": 15, "output_tokens": 4}]


def test_reference_sandbox_error_is_not_hidden_by_unrelated_final_stderr_line(monkeypatch, tmp_path, reference):
    client = _client(monkeypatch, tmp_path)
    process, launched, cleaned = _process_double(monkeypatch, client,
        stderr=REFERENCE_FAILURE + "\nobserver shutdown completed\n", return_code=1)
    with pytest.raises(ImageClientError) as caught:
        client.generate("Improve only the lighting")
    assert "reference" in str(caught.value).casefold() and "sandbox" in str(caught.value).casefold()
    assert "PRIVATE_STORY" not in str(caught.value)
    assert not caught.value.retryable and len(launched) == 1 and cleaned == [process]


def test_unrelated_image_description_does_not_turn_success_into_sandbox_failure(monkeypatch, tmp_path, reference):
    client = _client(monkeypatch, tmp_path)
    message = {"type": "item.completed", "item": {"type": "agent_message", "text":
        "The edited cover preserves the playground sandbox and its reference lighting."}}
    process, launched, cleaned = _process_double(monkeypatch, client,
        stdout=json.dumps(message) + "\n", output=picture("orange"))
    result = client.generate("Improve only the lighting")
    assert result.operation == "generate" and result.data == picture("orange")
    assert len(launched) == 1 and cleaned == [process]


@pytest.mark.parametrize("stderr", [
    "bwrap: No permissions to create a new namespace\n",
    "fs sandbox helper failed\n",
    "unable to read referenced image: unknown image format\n",
])
def test_one_pattern_alone_does_not_classify_a_reference_sandbox_failure(monkeypatch, tmp_path, reference, stderr):
    client = _client(monkeypatch, tmp_path)
    process, launched, cleaned = _process_double(monkeypatch, client, stderr=stderr, return_code=0)
    with pytest.raises(ImageClientError) as caught:
        client.generate("Improve lighting")
    assert str(caught.value).startswith("Codex image generation failed (exit 0)")
    assert len(launched) == 1 and cleaned == [process]


@pytest.mark.parametrize("return_code", [0, 1])
def test_completed_runner_preserves_same_terminal_reference_error_without_retry(
        monkeypatch, tmp_path, reference, return_code):
    client = _client(monkeypatch, tmp_path)
    launched = []

    def runner(command, **_kwargs):
        launched.append(command)
        return subprocess.CompletedProcess(command, return_code, "",
            REFERENCE_FAILURE + "\nobserver shutdown completed\n")

    client._run = runner
    with pytest.raises(ImageClientError) as caught:
        client.generate("Improve only the lighting")
    assert "reference" in str(caught.value).casefold() and "sandbox" in str(caught.value).casefold()
    assert "PRIVATE_STORY" not in str(caught.value)
    assert not caught.value.retryable and len(launched) == 1


@pytest.mark.parametrize("timing", ["during_exit", "before_tail_rollover"])
def test_reference_failure_survives_exit_race_and_long_followup_logs(monkeypatch, tmp_path, reference, timing):
    client = _client(monkeypatch, tmp_path)
    process, launched, cleaned = _process_double(monkeypatch, client,
        stderr=(REFERENCE_FAILURE + "\n" + "noise\n" * 4000) if timing == "before_tail_rollover" else "")
    original_launch = image_client.subprocess.Popen

    def launch(command, **kwargs):
        active = original_launch(command, **kwargs)
        if timing == "during_exit":
            def poll():
                kwargs["stderr"].write(REFERENCE_FAILURE + "\n")
                kwargs["stderr"].flush()
                return 1
            active.poll = poll
        return active

    monkeypatch.setattr(image_client.subprocess, "Popen", launch)
    with pytest.raises(ImageClientError) as caught:
        client.generate("Improve only the lighting")
    assert "reference" in str(caught.value).casefold() and "sandbox" in str(caught.value).casefold()
    assert "PRIVATE_STORY" not in str(caught.value)
    assert not caught.value.retryable and len(launched) == 1 and cleaned == [process]
