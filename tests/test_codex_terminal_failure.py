"""Terminal transport evidence must not be confused with CLI diagnostic chatter."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

import llm_client
from llm_client import LLMClient, LLMError
from core.codex_terminal_failure import MAX_EVENT_CHARS, MAX_STDOUT_BYTES, terminal_http_failure


FAILURE_MESSAGE = (
    'unexpected status 402 Payment Required: {"detail":{"code":"deactivated_workspace"}}, '
    'url: https://private.example/request?token=URL_SECRET, request id: REQUEST_SECRET'
)


def events(*items):
    return "\n".join(json.dumps(item) for item in items) + "\n"


def failed(message=FAILURE_MESSAGE):
    return {"type": "turn.failed", "error": {"message": message}}


def test_adapter_preserves_terminal_provider_rejection_as_typed_failure(monkeypatch):
    monkeypatch.setattr(llm_client.shutil, "which", lambda _: "/stub/codex")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(
            command, 1, events({"type": "turn.started"}, failed()),
            "catalog fetch failed HTTP 503 STDERR_SECRET",
        )

    monkeypatch.setattr(llm_client.subprocess, "run", run)
    with pytest.raises(LLMError) as caught:
        LLMClient(provider="codex").complete("role", "task")
    assert getattr(caught.value, "status_code", None) == 402
    assert getattr(caught.value, "provider_code", None) == "deactivated_workspace"
    assert "--json" in commands[0]
    assert len(commands) == 1
    assert "SECRET" not in str(caught.value)


def test_only_terminal_error_is_used_after_transient_failures():
    stdout = events(
        {"type": "thread.started", "thread_id": "PRIVATE_THREAD"},
        {"type": "turn.started"},
        {"type": "error", "message": "HTTP 503 transient"},
        {"type": "item.completed", "item": {"type": "error", "message": "HTTP 429"}},
        failed(),
    )
    result = terminal_http_failure(stdout.encode(), exit_code=1)
    assert result.status_code == 402
    assert result.provider_code == "deactivated_workspace"
    assert "PRIVATE" not in repr(result) and "SECRET" not in repr(result)


@pytest.mark.parametrize("stdout", [
    "", FAILURE_MESSAGE,
    events({"type": "error", "message": FAILURE_MESSAGE}),
    events({"type": "item.completed", "item": {"type": "error", "message": FAILURE_MESSAGE}}),
    events({"type": "item.completed", "item": {"type": "agent_message", "text": events(failed())}}),
    events({"type": "item.completed", "item": {"type": "reasoning", "text": FAILURE_MESSAGE}}),
    events({"type": "turn.failed", "error": FAILURE_MESSAGE}),
    events(failed("Model wrote: " + FAILURE_MESSAGE)),
    events(failed("catalog refresh HTTP 402")),
    events(failed("unexpected status 200 OK: {}")),
    events(failed("unexpected status 302 Found: {}")),
    events({"type": "turn.completed"}, failed()),
    events(failed(), {"type": "turn.completed"}),
    events(failed(), {"type": "error", "message": "new activity"}),
    events(failed(), failed()),
    events({"type": "turn.started"}, {"type": "turn.started"}, failed()),
    events(failed()) + "unparseable trailing output",
    "unparseable prefix\n" + events(failed()),
    events([{"type": "turn.failed"}]),
    events({"type": "turn.failed", "error": {"message": 402}}),
    b"\xff" + events(failed()).encode(),
    "\ud800" + events(failed()),
])
def test_nonterminal_unstructured_or_conflicting_evidence_stays_unknown(stdout):
    assert terminal_http_failure(stdout, exit_code=1) is None


@pytest.mark.parametrize("exit_code", [0, -9, -15, None, True, "1"])
def test_unsuccessful_local_completion_is_required(exit_code):
    assert terminal_http_failure(events(failed()), exit_code=exit_code) is None


def test_complete_response_proof_has_priority():
    assert terminal_http_failure(events(failed()), exit_code=1, complete_response=True) is None


def test_duplicate_event_keys_cannot_hide_completion():
    stdout = '{"type":"turn.completed",' + json.dumps(failed())[1:]
    assert terminal_http_failure(stdout, exit_code=1) is None


@pytest.mark.parametrize("body,expected", [
    ('{"detail":{"code":"deactivated_workspace"}}', "deactivated_workspace"),
    ('{"detail":{"code":"PRIVATE_PROVIDER_SECRET"}}', ""),
    ('{"detail":{"code":["deactivated_workspace"]}}', ""),
    ('{"detail":"deactivated_workspace"}', ""),
    ('{"detail":{"code":"deactivated_workspace","code":"PRIVATE_SECRET"}}', ""),
    ('{"other":{"code":"deactivated_workspace"}}', ""),
    ('not JSON PRIVATE_SECRET', ""),
])
def test_provider_body_only_contributes_allowlisted_code(body, expected):
    result = terminal_http_failure(events(failed(f"unexpected status 402 Payment Required: {body}, url: SECRET")), exit_code=1)
    assert result.status_code == 402
    assert result.provider_code == expected
    assert "SECRET" not in repr(result)


def test_oversized_evidence_is_not_tail_truncated():
    assert terminal_http_failure(" " * MAX_STDOUT_BYTES + events(failed()), exit_code=1) is None
    stdout = events({"type": "item.updated", "text": "x" * MAX_EVENT_CHARS}, failed())
    assert terminal_http_failure(stdout, exit_code=1) is None
    assert terminal_http_failure("\n".join("中" * 200000 for _ in range(20)), exit_code=1) is None


@pytest.mark.parametrize("status", [400, 401, 403, 408, 429, 500, 502, 503, 599])
def test_http_status_is_retained_without_guessing_reason(status):
    result = terminal_http_failure(events(failed(f"unexpected status {status}: upstream unavailable")), exit_code=1)
    assert result.status_code == status
    assert result.provider_code == ""


@pytest.mark.parametrize("exit_code,final_output", [(0, "complete story"), (1, "possibly complete story"), (-9, "")])
def test_adapter_does_not_override_complete_response_or_local_kill(monkeypatch, exit_code, final_output):
    monkeypatch.setattr(llm_client.shutil, "which", lambda _: "/stub/codex")

    def run(command, **kwargs):
        if final_output:
            Path(command[command.index("--output-last-message") + 1]).write_text(final_output)
        return subprocess.CompletedProcess(command, exit_code, events(failed()), "")

    monkeypatch.setattr(llm_client.subprocess, "run", run)
    client = LLMClient(provider="codex")
    if exit_code == 0:
        assert client.complete("role", "task") == final_output
    else:
        with pytest.raises(LLMError) as caught:
            client.complete("role", "task")
        assert not hasattr(caught.value, "status_code")


def test_timeout_with_observed_error_remains_unknown(monkeypatch):
    monkeypatch.setattr(llm_client.shutil, "which", lambda _: "/stub/codex")

    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 2, output=events(failed()))

    monkeypatch.setattr(llm_client.subprocess, "run", run)
    with pytest.raises(LLMError, match="timed out") as caught:
        LLMClient(provider="codex").complete("role", "task")
    assert not hasattr(caught.value, "status_code")
    assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)


def test_http_exception_sanitizes_caller_supplied_code():
    error = llm_client.LLMHTTPError(402, provider_code="https://SECRET")
    assert error.status_code == 402 and error.provider_code == ""
    assert "SECRET" not in str(error)


def test_real_local_cli_terminal_failure_keeps_safe_diagnostics(tmp_path, monkeypatch):
    binary = tmp_path / "local-cli"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "assert '--json' in sys.argv\n"
        "sys.stdin.read()\n"
        f"print({events({'type': 'turn.started'}, failed())!r}, end='', flush=True)\n"
        "print('PRIVATE_STDERR_SECRET', file=sys.stderr, flush=True)\n"
        "sys.exit(1)\n"
    )
    binary.chmod(0o700)
    monkeypatch.setattr(llm_client.shutil, "which", lambda _: str(binary))
    diagnostics = []
    client = LLMClient(provider="codex", timeout_seconds=5, diagnostic_callback=diagnostics.append)
    with pytest.raises(llm_client.LLMHTTPError) as caught:
        client.complete("PRIVATE_ROLE_SECRET", "PRIVATE_TASK_SECRET")
    assert caught.value.status_code == 402
    assert caught.value.provider_code == "deactivated_workspace"
    assert any(row["event"] == "cli.failed" and row["exit_code"] == 1 for row in diagnostics)
    assert "SECRET" not in json.dumps(diagnostics)
