from __future__ import annotations

import signal
import subprocess
from pathlib import Path

import pytest

import llm_client
import image_client
from image_client import CodexImageGenerationClient, build_image_generation_client
from llm_client import LLMClient, LLMError
from studio_settings import CoverSettings


@pytest.fixture
def codex_binary(monkeypatch):
    original = llm_client.shutil.which
    monkeypatch.setattr(
        llm_client.shutil,
        "which",
        lambda name: "/usr/local/bin/codex" if name == "codex" else original(name),
    )


def test_codex_completion_is_ephemeral_and_read_only(monkeypatch, codex_binary) -> None:
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs["input"]
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text("chapter output", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(llm_client.subprocess, "run", run)
    client = LLMClient(provider="codex", model="gpt-5.6-sol")

    result = client.complete("You are the Scribe.", "Write chapter one.")

    assert result == "chapter output"
    assert captured["command"][:2] == ["/usr/local/bin/codex", "exec"]
    assert "--ephemeral" in captured["command"]
    assert captured["command"][captured["command"].index("--sandbox") + 1] == "read-only"
    assert captured["command"][captured["command"].index("--model") + 1] == "gpt-5.6-sol"
    assert "# ROLE\nYou are the Scribe." in captured["input"]


def test_codex_nonzero_exit_is_reported(monkeypatch, codex_binary) -> None:
    monkeypatch.setattr(
        llm_client.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "login required"),
    )

    with pytest.raises(LLMError, match="login required"):
        LLMClient(provider="codex").complete("role", "task")


def test_codex_image_client_uses_same_login_without_api_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(image_client.shutil, "which", lambda name: "/usr/local/bin/codex")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "host-codex-home"))
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs["input"]
        generated_root = Path(kwargs["env"]["CODEX_HOME"]) / "generated_images" / "thread-id"
        generated_root.mkdir(parents=True)
        jpeg = (
            b"\xff\xd8\xff\xc0\x00\x11\x08"
            + (1536).to_bytes(2, "big")
            + (1024).to_bytes(2, "big")
            + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9"
        )
        (generated_root / "cover.jpg").write_bytes(jpeg)
        square = (
            b"\xff\xd8\xff\xc0\x00\x11\x08"
            + (100).to_bytes(2, "big")
            + (100).to_bytes(2, "big")
            + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9"
        )
        (generated_root / "internal-preview.jpg").write_bytes(square)
        return subprocess.CompletedProcess(command, 0, "", "")

    settings = CoverSettings(
        base_url="",
        api_key="",
        provider="codex",
        model="gpt-image-2",
        size="1024x1536",
        quality="high",
        output_format="jpeg",
    )
    client = CodexImageGenerationClient(settings, runner=run)

    generated = client.generate("An original literary cover")

    assert (generated.width, generated.height) == (1024, 1536)
    assert generated.model == "gpt-image-2"
    assert "--enable" in captured["command"]
    assert captured["command"][captured["command"].index("--enable") + 1] == "image_generation"
    assert "An original literary cover" in captured["input"]
    assert isinstance(build_image_generation_client(settings), CodexImageGenerationClient)


def test_codex_image_timeout_terminates_the_whole_process_group(monkeypatch) -> None:
    signals = []

    class FakeProcess:
        pid = 731

        def __init__(self):
            self.waits = 0

        def poll(self):
            return None

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("codex", timeout)
            return -signal.SIGKILL

    monkeypatch.setattr(
        image_client.os,
        "killpg",
        lambda pid, next_signal: signals.append((pid, next_signal)),
    )

    CodexImageGenerationClient._terminate_process_group(FakeProcess())

    assert signals == [(731, signal.SIGTERM), (731, signal.SIGKILL)]
