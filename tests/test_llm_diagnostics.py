"""Codex diagnostics report process evidence without retaining model content."""

import json

import pytest

from core import llm_diagnostics
from core.llm_diagnostics import CLIDiagnostics


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(llm_diagnostics.time, "monotonic", lambda: now[0])
    return now


def _line(payload):
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode()


def test_no_output_timeout_reports_elapsed_and_idle_time(clock):
    records = []
    observer = CLIDiagnostics(records.append)
    observer.start(123)
    clock[0] += 29
    observer.heartbeat()
    assert len(records) == 1
    clock[0] += 1
    observer.heartbeat()
    clock[0] += 20
    terminal = observer.finish("cli.timeout", timeout_seconds=50)

    assert [record["event"] for record in records] == [
        "process_started", "heartbeat", "cli.timeout",
    ]
    assert terminal["elapsed_seconds"] == terminal["idle_seconds"] == 50
    assert terminal["stdout_bytes"] == terminal["stderr_bytes"] == 0
    assert terminal["first_output_seconds"] is None
    assert terminal["last_output_seconds"] is None
    assert terminal["first_cli_event_seconds"] is None


def test_cumulative_fragmented_json_observes_each_event_once(clock):
    records = []
    observer = CLIDiagnostics(records.append)
    observer.start(123)
    first = _line({"type": "thread.started", "thread_id": "0199b420-29bc-73e1-9695-f890efd770cf"})
    second = _line({"type": "turn.started"})
    clock[0] += 2
    observer.observe(first[:10], None)
    assert records[-1]["event"] == "first_output"
    assert records[-1]["first_cli_event_seconds"] is None
    clock[0] += 3
    observer.observe(first, None)
    observer.observe(first, None)
    observer.observe(None, None)
    observer.observe(first[:8], None)  # A stale snapshot cannot rewind the offset.
    clock[0] += 4
    observer.observe(first + second, b"")
    clock[0] += 1
    terminal = observer.finish("cli.completed", exit_code=0)

    assert [r["event"] for r in records].count("first_output") == 1
    assert [r["event"] for r in records].count("first_cli_event") == 1
    assert [r["event"] for r in records].count("cli_event") == 1
    assert terminal["thread_id"] == "0199b420-29bc-73e1-9695-f890efd770cf"
    assert terminal["stdout_bytes"] == len(first + second)
    assert terminal["last_cli_event"] == "turn.started"
    assert terminal["first_output_seconds"] == 2
    assert terminal["first_cli_event_seconds"] == 5
    assert terminal["last_output_seconds"] == 9
    assert terminal["idle_seconds"] == 1


def test_content_reasoning_secrets_commands_and_raw_errors_never_emitted(clock):
    records = []
    observer = CLIDiagnostics(records.append)
    secret = "PRIVATE_STORY_AUTHORIZATION_BEARER_sk-123"
    stdout = b"".join([
        _line({"type": "thread.started", "thread_id": secret}),
        _line({"type": "item.started", "item": {
            "type": "reasoning", "text": secret, "content": secret,
        }}),
        _line({"type": "item.completed", "item": {
            "type": "command_execution", "command": secret, "aggregated_output": secret,
        }}),
        _line({"type": "item.completed", "item": {"type": secret, "text": secret}}),
        _line({"type": "error", "message": f"HTTP 503 {secret}"}),
        _line({"type": secret, "message": secret}),
        _line({"type": "turn.completed", "usage": {
            "input_tokens": 12, "cached_input_tokens": 3, "output_tokens": 9,
            "secret": secret,
        }}),
    ])
    observer.observe(stdout, f"Authorization: Bearer {secret}\n".encode())
    terminal = observer.finish(
        "cli.failed", exception=secret, stderr=secret, content=secret,
        exit_code=1, final_output_exists=True, final_output_bytes=10,
    )

    serialized = json.dumps(records)
    assert secret not in serialized
    assert "Authorization" not in serialized
    assert terminal["thread_id"] is None
    assert terminal["usage"] == {"input_tokens": 12, "cached_input_tokens": 3, "output_tokens": 9}
    assert terminal["http_status"] == 503
    assert terminal["error_category"] == "upstream"
    assert terminal["exit_code"] == 1
    assert terminal["final_output_exists"] is True
    assert terminal["final_output_bytes"] == 10


def test_model_prose_is_never_classified_as_transport_error(clock):
    records = []
    observer = CLIDiagnostics(records.append)
    observer.observe(_line({"type": "item.completed", "item": {
        "type": "agent_message",
        "text": "HTTP 429 Reconnecting... 1/5 network error request timeout",
    }}), None)
    terminal = observer.finish("cli.completed")

    assert terminal["retry_count"] == 0
    assert terminal["http_status"] is None
    assert terminal["error_category"] is None
    assert terminal["last_item_type"] == "agent_message"


@pytest.mark.parametrize("status, category", [
    (401, "authentication"), (403, "authentication"), (429, "rate_limit"),
    (408, "timeout"), (504, "timeout"), (502, "upstream"), (400, "http_client_error"),
])
def test_structured_status_categories(status, category, clock):
    observer = CLIDiagnostics(lambda record: None)
    observer.observe(_line({"type": "turn.failed", "error": {
        "status_code": status, "message": "request failed with sensitive payload",
    }}), None)
    terminal = observer.finish("cli.failed")
    assert terminal["http_status"] == status
    assert terminal["error_category"] == category


def test_retry_errors_are_classified_and_cross_stream_duplicates_are_coalesced(clock):
    records = []
    observer = CLIDiagnostics(records.append)
    first = _line({"type": "error", "message": "Reconnecting... 1/5 (stream disconnected before completion)"})
    stderr = b"Reconnecting... 1/5 (transport error)\n"
    observer.observe(first, stderr)
    observer.observe(first, stderr)
    second = _line({"type": "error", "message": "Reconnecting... 2/5 (unexpected status 429 Too Many Requests)"})
    observer.observe(first + second, stderr)
    terminal = observer.finish("cli.failed")

    assert terminal["retry_count"] == 2
    assert terminal["http_status"] == 429
    assert terminal["error_category"] == "rate_limit"
    # The first error is the first CLI event, with its retry metadata attached.
    assert next(r for r in records if r["event"] == "first_cli_event")["retry_count"] == 1
    assert [record["event"] for record in records].count("retry") == 1


def test_mirrored_retry_batches_and_missing_earlier_attempts_do_not_overcount(clock):
    observer = CLIDiagnostics(lambda record: None)
    stdout = b"".join(_line({
        "type": "error", "message": f"Reconnecting... {attempt}/5 (network error)",
    }) for attempt in (2, 3))
    stderr = b"Reconnecting... 2/5 (network error)\nReconnecting... 3/5 (network error)\n"
    observer.observe(stdout, stderr)
    assert observer.finish("cli.failed")["retry_count"] == 3


def test_input_transfer_and_cli_phases_use_separate_clocks(clock):
    records = []
    observer = CLIDiagnostics(records.append)
    observer.start(123)
    assert records[-1]["phase"] == "input_transfer"
    clock[0] += 2
    observer.input_sent()
    observer.input_sent()
    assert records[-1]["event"] == "stdin_sent"
    assert records[-1]["phase"] == "awaiting_cli_event"
    assert records[-1]["input_sent_seconds"] == 2
    assert records[-1]["first_output_seconds"] is None
    assert len([r for r in records if r["event"] == "stdin_sent"]) == 1
    clock[0] += 3
    first = _line({"type": "thread.started"})
    observer.observe(first, None)
    assert records[-1]["phase"] == "cli_active"
    assert records[-1]["first_cli_event_seconds"] == 5
    observer.observe(first + _line({"type": "turn.completed"}), None)
    terminal = observer.finish("cli.completed")
    assert terminal["phase"] == "turn_completed"
    observer.input_sent()
    assert len([r for r in records if r["event"] == "stdin_sent"]) == 1


def test_heartbeat_does_not_turn_each_chunk_into_an_event(clock):
    records = []
    observer = CLIDiagnostics(records.append)
    data = b""
    for _ in range(20):
        clock[0] += 1
        data += _line({"type": "item.updated", "item": {"type": "reasoning", "text": "private"}})
        observer.observe(data, None)
        observer.heartbeat()
    assert [r["event"] for r in records] == ["first_output", "first_cli_event"]
    clock[0] += 11
    observer.heartbeat()
    assert records[-1]["event"] == "heartbeat"
    assert records[-1]["idle_seconds"] == 11


def test_oversized_lines_use_bounded_buffers_and_preserve_following_json(clock):
    records = []
    observer = CLIDiagnostics(records.append)
    giant = b'{"type":"item.completed","item":{"text":"' + b"s" * (llm_diagnostics._MAX_MODEL_EVENT_BYTES + 1)
    stderr = b"s" * 1_000_000
    observer.observe(giant, stderr)
    assert len(observer._stdout_lines.buffer) <= llm_diagnostics._MAX_MODEL_EVENT_BYTES
    assert len(observer._stderr_lines.buffer) <= llm_diagnostics._MAX_LINE_BYTES
    stdout = giant + b'"}}\n' + _line({"type": "turn.completed", "usage": {"output_tokens": 999}})
    observer.observe(stdout, stderr + b"\nHTTP 503\n")
    terminal = observer.finish("cli.failed")

    assert terminal["discarded_stdout_lines"] == 1
    assert terminal["discarded_stderr_lines"] == 1
    assert terminal["last_cli_event"] == "turn.completed"
    assert terminal["usage"] == {"output_tokens": 999}
    assert terminal["http_status"] == 503
    assert len(json.dumps(records)) < 10_000


def test_utf8_fragment_and_final_string_snapshot_share_byte_offset(clock):
    records = []
    observer = CLIDiagnostics(records.append)
    payload = _line({"type": "item.completed", "item": {"type": "agent_message", "text": "秘密"}})
    split = payload.index("秘".encode()) + 1
    observer.observe(payload[:split], None)
    observer.observe(payload.decode(), None)
    terminal = observer.finish("cli.completed")
    assert terminal["stdout_bytes"] == len(payload)
    assert terminal["last_item_type"] == "agent_message"
    assert [r["event"] for r in records].count("first_cli_event") == 1


def test_unterminated_json_and_stderr_are_flushed_on_finish(clock):
    observer = CLIDiagnostics(lambda record: None)
    observer.observe(b'{"type":"turn.failed"}', b"HTTP/1.1 502 Bad Gateway")
    terminal = observer.finish("cli.failed")
    assert terminal["last_cli_event"] == "turn.failed"
    assert terminal["http_status"] == 502
    assert terminal["error_category"] == "upstream"


def test_callback_failures_cannot_break_observation_or_finish(clock):
    def broken_callback(record):
        raise RuntimeError("private error that must not escape")

    observer = CLIDiagnostics(broken_callback)
    observer.start(123)
    observer.observe(_line({"type": "turn.started"}), b"HTTP 503\n")
    clock[0] += 35
    observer.heartbeat()
    terminal = observer.finish("cli.failed", exit_code=1)
    assert terminal["http_status"] == 503
    assert terminal["exit_code"] == 1


def test_malformed_and_untrusted_metadata_is_ignored(clock):
    records = []
    observer = CLIDiagnostics(records.append)
    observer.observe(b"not JSON\n[]\n" + _line({"type": {"bad": "value"}}) + _line({
        "type": "turn.completed", "usage": {
            "input_tokens": True, "cached_input_tokens": -10, "output_tokens": "private",
        },
    }), None)
    terminal = observer.finish(
        "private event", exit_code="private", final_output_exists="private",
        timeout_seconds=float("inf"), output_bytes=True, error_category={"secret": "private"},
    )
    assert terminal["event"] == "finished"
    assert terminal["usage"] == {}
    assert "exit_code" not in terminal
    assert "final_output_exists" not in terminal
    assert "timeout_seconds" not in terminal
    assert "output_bytes" not in terminal
    assert "private" not in json.dumps(records)


def test_callback_mutation_and_repeated_finish_do_not_change_observer_state(clock):
    def mutate(record):
        record["usage"]["input_tokens"] = "private"
        record["event"] = "private"

    observer = CLIDiagnostics(mutate)
    observer.observe(_line({"type": "turn.completed", "usage": {"input_tokens": 5}}), None)
    terminal = observer.finish("cli.completed")
    observer.observe(_line({"type": "turn.failed"}), None)
    observer.heartbeat()
    assert terminal["usage"] == {"input_tokens": 5}
    assert observer.finish("cli.failed") == terminal


def test_visible_output_counts_snapshots_without_reasoning_or_duplicate_items(clock):
    records = []
    observer = CLIDiagnostics(records.append)
    stdout = b""
    for event in (
        {"type": "item.started", "item": {"id": "r", "type": "reasoning", "text": "PRIVATE_REASONING"}},
        {"type": "item.updated", "item": {"id": "a", "type": "agent_message", "text": "秘密"}},
        {"type": "item.updated", "item": {"id": "a", "type": "agent_message", "text": "秘密文本"}},
        {"type": "item.completed", "item": {"id": "a", "type": "agent_message", "text": "秘密文本"}},
        {"type": "item.completed", "item": {"id": "b", "type": "agent_message", "text": "another"}},
    ):
        clock[0] += 1
        stdout += _line(event)
        observer.observe(stdout, None)
    observer.observe(stdout, None)
    terminal = observer.finish("cli.completed")
    assert terminal["model_output_chars"] == 11
    assert terminal["first_model_output_seconds"] == 2
    assert terminal["last_model_output_seconds"] == 5
    assert [r["model_output_chars"] for r in records if r["event"] == "cli.model_output"] == [2, 4, 11]
    assert "秘密" not in json.dumps(records, ensure_ascii=False)
    assert "PRIVATE_REASONING" not in json.dumps(records)


@pytest.mark.parametrize("fragmented", [False, True])
def test_large_final_agent_message_has_counts_without_retaining_its_content(clock, fragmented):
    records = []
    observer = CLIDiagnostics(records.append)
    text = "私密骨架内容" * 10000
    event = _line({"type": "item.completed", "item": {
        "id": "large-final", "type": "agent_message", "text": text,
    }})
    assert llm_diagnostics._MAX_LINE_BYTES < len(event) < llm_diagnostics._MAX_MODEL_EVENT_BYTES
    if fragmented:
        for offset in range(10000, len(event), 10000):
            observer.observe(event[:offset], None)
            assert len(observer._stdout_lines.buffer) <= llm_diagnostics._MAX_MODEL_EVENT_BYTES
    clock[0] += 5
    observer.observe(event, None)
    observer.observe(event, None)
    terminal = observer.finish("cli.completed")
    assert terminal["model_output_chars"] == len(text)
    assert terminal["discarded_stdout_lines"] == 0
    assert terminal["first_model_output_seconds"] == 5
    assert terminal["last_item_type"] == "agent_message"
    assert [r["event"] for r in records].count("cli.model_output") == 1
    assert "私密骨架内容" not in json.dumps(records, ensure_ascii=False)
    assert not observer._stdout_lines.buffer
