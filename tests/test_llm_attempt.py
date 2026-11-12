"""Durable completions use local subprocess fixtures, never an authenticated model."""
import json
from pathlib import Path
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import llm_client
from llm_client import LLMCancelledError, LLMClient, LLMError

from llm_attempt import completion_attempt, current_attempt, recover_completion


def local_codex(tmp_path, monkeypatch, body):
    binary = tmp_path / "local-codex"
    binary.write_text(f"#!{sys.executable}\n" + "import sys,json,time\nfrom pathlib import Path\n"
                      + "prompt=sys.stdin.read()\n"
                      + "output=Path(sys.argv[sys.argv.index('--output-last-message')+1])\n" + body)
    binary.chmod(0o700)
    monkeypatch.setattr(llm_client.shutil, "which", lambda _: str(binary))


def test_normal_cli_final_survives_business_crash_without_model_reissue(tmp_path, monkeypatch):
    local_codex(tmp_path, monkeypatch, "output.write_text('{\"routes\": [\"complete\"]}')\n")
    directory = tmp_path / "attempt"
    events = []
    client = LLMClient(provider="codex", model="local-stub", timeout_seconds=5)
    with pytest.raises(RuntimeError, match="business crash"):
        with completion_attempt(directory, "request-1", observer=events.append):
            assert client.complete("complete source", "full instruction") == '{"routes": ["complete"]}'
            raise RuntimeError("business crash before session checkpoint")
    monkeypatch.setattr(client, "complete", lambda *a: pytest.fail("Must not issue another request"))
    assert recover_completion(directory, "request-1") == '{"routes": ["complete"]}'
    assert (directory / "cli" / "last-message.txt").read_text() == '{"routes": ["complete"]}'
    assert events[0]["event"] == "request.start"
    assert events[-1]["event"] == "request.completed"


def test_timeout_preserves_parseable_partial_and_safe_diagnostics(tmp_path, monkeypatch):
    local_codex(tmp_path, monkeypatch, '''
output.write_text('{"routes": ["unfinished"]}')
print(json.dumps({'type':'item.completed','item':{'type':'reasoning','text':'PRIVATE_REASONING'}}),flush=True)
print('HTTP 503 Bearer PRIVATE_TOKEN https://private.example',file=sys.stderr,flush=True)
time.sleep(10)
''')
    directory = tmp_path / "attempt"
    events = []
    with completion_attempt(directory, "request-1", observer=events.append):
        with pytest.raises(LLMError, match="timed out"):
            LLMClient(provider="codex", model="local-stub", timeout_seconds=1).complete("source", "instruction")
    assert (directory / "cli" / "last-message.txt").read_text() == '{"routes": ["unfinished"]}'
    assert recover_completion(directory, "request-1") is None
    assert {"cli.timeout", "request.timeout"} <= {entry["event"] for entry in events}
    public = json.dumps(events)
    assert "PRIVATE_REASONING" not in public and "PRIVATE_TOKEN" not in public
    assert "https://private.example" not in public
    assert "PRIVATE_REASONING" in (directory / "cli" / "stdout.jsonl").read_text()
    assert "PRIVATE_TOKEN" in (directory / "cli" / "stderr.txt").read_text()
    stopped = [entry for entry in events if entry["event"] == "process_stopped"]
    assert stopped and stopped[-1]["local_process_stopped"] is True


def sdk_client(response):
    # Exercise the real generic complete boundary without constructing an SDK
    # or consulting any developer credentials.
    client = object.__new__(LLMClient)
    client.provider_name, client.model = "openai", "local-sdk-stub"
    client.reasoning_effort = "high"
    client.timeout_seconds = 10
    client.max_tokens = 8192
    client._complete_text = lambda system, user: response
    return client


def test_sdk_response_is_durable_before_return_and_observer_failure_is_isolated(tmp_path):
    response = '  {"routes":["full story é"]}\n'
    def broken_observer(_entry):
        raise RuntimeError("UI disconnected")
    with completion_attempt(tmp_path / "a", "sdk-request", observer=broken_observer):
        assert sdk_client(response).complete("all source", "all instruction") == response
        assert recover_completion(tmp_path / "a", "sdk-request") == response
    records = [json.loads(line) for line in (tmp_path / "a" / "diagnostics.jsonl").read_text().splitlines()]
    assert [record["event"] for record in records] == ["request.start", "request.completed"]
    assert records[0]["reasoning_effort"] == "high"


def test_attempt_identity_and_checksums_fail_closed(tmp_path):
    directory = tmp_path / "a"
    with completion_attempt(directory, "r1") as attempt:
        attempt.save_response("complete")
        attempt.save_response("complete")
        with pytest.raises(ValueError, match="different response"):
            attempt.save_response("replacement")
    with pytest.raises(ValueError, match="identity"):
        recover_completion(directory, "r2")
    with pytest.raises(ValueError, match="identity"):
        with completion_attempt(directory, "r2"):
            pass
    (directory / "response.txt").write_text("other complete")
    with pytest.raises(ValueError, match="checksum"):
        recover_completion(directory, "r1")


def test_orphan_response_and_final_json_are_not_completion_proof(tmp_path):
    directory = tmp_path / "a"
    with completion_attempt(directory, "r1") as attempt:
        (directory / "response.txt").write_text('{"valid":"JSON"}')
        (attempt.cli_directory() / "last-message.txt").write_text('{"also":"JSON"}')
    assert recover_completion(directory, "r1") is None


def test_rejects_wrong_request_and_does_not_call_model(tmp_path):
    with completion_attempt(tmp_path / "a", "r1") as attempt:
        attempt.bind_request("source1", "instructions", provider="openai", model="local-sdk-stub")
        client = sdk_client("output")
        client._complete_text = lambda *_: pytest.fail("Changed request must not be sent")
        with pytest.raises(ValueError, match="request changed"):
            client.complete("source2", "instructions")


def test_attempts_are_thread_isolated_and_nested_scope_restores(tmp_path):
    barrier = threading.Barrier(2)
    def work(index):
        assert current_attempt() is None
        with completion_attempt(tmp_path / str(index), str(index)) as attempt:
            barrier.wait(timeout=3)
            assert current_attempt() is attempt
            sdk_client(f"response-{index}").complete("source", f"instruction {index}")
            with completion_attempt(tmp_path / f"nested-{index}", f"nested-{index}"):
                assert current_attempt() is not attempt
            assert current_attempt() is attempt
        assert current_attempt() is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(work, (1, 2)))
    assert recover_completion(tmp_path / "1", "1") == "response-1"
    assert recover_completion(tmp_path / "2", "2") == "response-2"


def test_top_level_and_package_imports_share_context(tmp_path):
    from core.llm_attempt import current_attempt as package_current
    with completion_attempt(tmp_path / "a", "r1") as attempt:
        assert package_current() is attempt


def test_symlink_and_hardlink_artifacts_are_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        with completion_attempt(linked, "r1"):
            pass
    with completion_attempt(target, "r1") as attempt:
        secret = tmp_path / "secret"
        secret.write_text("must not change")
        (target / "response.txt").symlink_to(secret)
        with pytest.raises(OSError):
            attempt.save_response("result")
        assert secret.read_text() == "must not change"
        (target / "response.txt").unlink()
        (target / "response.txt").hardlink_to(secret)
        with pytest.raises(ValueError, match="private regular"):
            attempt.save_response("result")
        assert secret.read_text() == "must not change"


def test_same_attempt_has_one_writer(tmp_path):
    with completion_attempt(tmp_path / "a", "r1"):
        with pytest.raises(BlockingIOError):
            with completion_attempt(tmp_path / "a", "r1"):
                pass


def test_cancel_retains_local_output_but_is_not_completion(tmp_path, monkeypatch):
    local_codex(tmp_path, monkeypatch, '''
output.write_text('partial before cancellation')
print(json.dumps({'type':'turn.started'}),flush=True)
time.sleep(10)
''')
    event = threading.Event()
    def progress(record):
        if record["event"] == "first_cli_event":
            event.set()
    directory = tmp_path / "a"
    with completion_attempt(directory, "r1", observer=progress):
        with pytest.raises(LLMCancelledError):
            LLMClient(provider="codex", timeout_seconds=5, cancellation_event=event).complete("source", "task")
    assert (directory / "cli" / "last-message.txt").read_text() == "partial before cancellation"
    assert recover_completion(directory, "r1") is None
    records = [json.loads(line) for line in (directory / "diagnostics.jsonl").read_text().splitlines()]
    assert records[-1]["event"] == "request.cancelled"


def test_metadata_projection_drops_private_values_even_under_known_keys(tmp_path):
    events = []
    with completion_attempt(tmp_path / "a", "r1", observer=events.append) as attempt:
        attempt.record_diagnostic({"event": "request.failed", "provider": "private.example",
            "endpoint": "https://private.example", "error_type": "PRIVATE_TOKEN", "model": "PRIVATE_TOKEN",
            "error_category": "PRIVATE_TOKEN", "phase": "PRIVATE_TOKEN", "thread_id": "PRIVATE_TOKEN",
            "usage": {"secret": "PRIVATE_TOKEN", "input_tokens": 12}, "stderr": "PRIVATE_TOKEN",
            "message": "PRIVATE_REASONING", "output": "PRIVATE_NOVEL", "http_status": 503})
    public = json.dumps(events)
    assert "PRIVATE" not in public and "private.example" not in public
    assert events[0]["http_status"] == 503 and events[0]["usage"] == {"input_tokens": 12}


def test_codex_nonzero_exit_keeps_evidence_and_never_publishes_complete(tmp_path, monkeypatch):
    local_codex(tmp_path, monkeypatch, "output.write_text('partial')\nprint('local failure',file=sys.stderr)\nsys.exit(2)\n")
    with completion_attempt(tmp_path / "a", "r1"):
        with pytest.raises(LLMError, match="exit 2"):
            LLMClient(provider="codex", timeout_seconds=5).complete("source", "task")
    assert recover_completion(tmp_path / "a", "r1") is None
    assert (tmp_path / "a" / "cli" / "last-message.txt").read_text() == "partial"


@pytest.mark.parametrize("finish", [None, "length", "content_filter"])
def test_sdk_eof_without_complete_stop_preserves_partial_not_completed(tmp_path, monkeypatch, finish):
    monkeypatch.setenv("NOVEL_OS_LLM_STREAMING", "true")
    client = sdk_client("")
    del client._complete_text
    chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"valid":"partial"}'), finish_reason=None)]),
              SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason=finish)])]
    client._backend = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: iter(chunks))))
    directory = tmp_path / "a"
    with completion_attempt(directory, "r1"):
        with pytest.raises(LLMError, match="complete termination"):
            client.complete("source", "instructions")
    assert recover_completion(directory, "r1") is None
    assert (directory / "partial-response.txt").read_text() == '{"valid":"partial"}'


@pytest.mark.parametrize("streaming", [True, False])
def test_sdk_explicit_stop_commits_full_response(tmp_path, monkeypatch, streaming):
    monkeypatch.setenv("NOVEL_OS_LLM_STREAMING", str(streaming).lower())
    client = sdk_client("")
    del client._complete_text
    choice = SimpleNamespace(message=SimpleNamespace(content="full answer"),
                             delta=SimpleNamespace(content="full answer"), finish_reason="stop")
    response = SimpleNamespace(choices=[choice])
    client._backend = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: iter([response]) if streaming else response)))
    with completion_attempt(tmp_path / "a", "r1"):
        assert client.complete("all source", "all instructions") == "full answer"
    assert recover_completion(tmp_path / "a", "r1") == "full answer"


def test_sdk_stream_error_preserves_received_content(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_OS_LLM_STREAMING", "true")
    client = sdk_client("")
    del client._complete_text
    def chunks():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="received part"), finish_reason=None)])
        raise ConnectionError("transport stopped")
    client._backend = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: chunks())))
    with completion_attempt(tmp_path / "a", "r1"):
        with pytest.raises(ConnectionError):
            client.complete("source", "task")
    assert recover_completion(tmp_path / "a", "r1") is None
    assert (tmp_path / "a" / "partial-response.txt").read_text() == "received part"


def test_closed_attempt_reopens_its_identity_and_cannot_write_to_a_reused_descriptor(tmp_path):
    with completion_attempt(tmp_path / "a", "r1") as attempt:
        attempt.bind_request("source", "task", provider="openai", model="stub")
    with completion_attempt(tmp_path / "b", "r2") as other:
        attempt.save_response("late result for first task")
        other.save_response("second task")
    assert recover_completion(tmp_path / "b", "r2") == "second task"
    assert recover_completion(tmp_path / "a", "r1") == "late result for first task"


def test_bound_request_cannot_be_replaced_after_completion(tmp_path):
    with completion_attempt(tmp_path / "a", "r1"):
        sdk_client("output").complete("source", "instructions")
    request = tmp_path / "a" / "request.json"
    request.write_text(json.dumps({"identity": "r1", "request_sha256": "changed"}))
    with pytest.raises(ValueError, match="request checksum"):
        recover_completion(tmp_path / "a", "r1")


def test_reentering_attempt_never_resends_or_replaces_prior_evidence(tmp_path):
    with completion_attempt(tmp_path / "a", "r1"):
        sdk_client("saved").complete("source", "task")
    with completion_attempt(tmp_path / "a", "r1"):
        client = sdk_client("wrong replacement")
        client._complete_text = lambda *_: pytest.fail("Reuse must not resubmit a request")
        with pytest.raises(ValueError, match="already dispatched"):
            client.complete("source", "task")
    assert recover_completion(tmp_path / "a", "r1") == "saved"


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
@pytest.mark.parametrize("finish", ["stop", "length", None])
def test_sdk_late_response_survives_local_stop_without_republishing_progress(tmp_path, monkeypatch, stop, finish):
    monkeypatch.setenv("NOVEL_OS_LLM_STREAMING", "false")
    started, release, cancel = threading.Event(), threading.Event(), threading.Event()
    client = sdk_client("")
    del client._complete_text
    client.cancellation_event = cancel
    if stop == "timeout":
        client.timeout_seconds = 0.1
    def response(**_kwargs):
        started.set()
        assert release.wait(5)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="late received reply"), finish_reason=finish)])
    client._backend = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=response)))
    outcomes, events = [], []
    def caller():
        with completion_attempt(tmp_path / "a", "r1", observer=events.append):
            try:
                client.complete("source", "task")
            except (LLMCancelledError, TimeoutError) as error:
                outcomes.append(error)
    worker = threading.Thread(target=caller)
    worker.start()
    try:
        assert started.wait(3)
        if stop == "cancel":
            cancel.set()
        worker.join(3)
        assert not worker.is_alive() and len(outcomes) == 1
        count = len(events)
        with completion_attempt(tmp_path / "new", "r2"):
            sdk_client("new attempt result").complete("source", "task")
        release.set()
        assert client.wait_for_local_completion(3)
        assert recover_completion(tmp_path / "a", "r1") == ("late received reply" if finish == "stop" else None)
        if finish != "stop":
            assert (tmp_path / "a" / "partial-response.txt").read_text() == "late received reply"
        assert recover_completion(tmp_path / "new", "r2") == "new attempt result"
        assert len(events) == count
    finally:
        release.set()
        cancel.set()
        worker.join(3)


def test_late_partial_survives_scope_close_without_becoming_complete(tmp_path):
    with completion_attempt(tmp_path / "a", "r1") as attempt:
        attempt.bind_request("source", "task", provider="openai", model="stub")
    attempt.save_partial_response("last received public text")
    assert (tmp_path / "a" / "partial-response.txt").read_text() == "last received public text"
    assert recover_completion(tmp_path / "a", "r1") is None


def test_late_write_rejects_replaced_binding_and_different_completed_result(tmp_path):
    with completion_attempt(tmp_path / "a", "r1") as attempt:
        attempt.bind_request("source", "task", provider="openai", model="stub")
        attempt.save_response("original complete")
    with pytest.raises(ValueError, match="different response"):
        attempt.save_response("other response")
    request = tmp_path / "a" / "request.json"
    request.write_text(json.dumps({"identity": "r1", "request_sha256": "changed"}))
    with pytest.raises(ValueError, match="original dispatched"):
        attempt.save_partial_response("late partial")


def test_stream_cancel_preserves_already_received_last_chunk_without_accepting_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_OS_LLM_STREAMING", "true")
    client = sdk_client("")
    del client._complete_text
    client.cancellation_event = threading.Event()
    def chunks():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="first "), finish_reason=None)])
        client.cancellation_event.set()
        yield SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content="last", reasoning_content="PRIVATE_REASONING"), finish_reason="stop")])
    client._backend = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: chunks())))
    with completion_attempt(tmp_path / "a", "r1"):
        with pytest.raises(LLMCancelledError):
            client.complete("source", "task")
    assert client.wait_for_local_completion(2)
    assert (tmp_path / "a" / "partial-response.txt").read_text() == "first last"
    assert recover_completion(tmp_path / "a", "r1") is None
