"""Offline app-server protocol doubles; no model, network or real subprocess."""
import base64
import io
import json
from pathlib import Path
import tempfile

import pytest
from PIL import Image

from core import codex_image_edit as edit
from core.image_client import CodexImageGenerationClient, ImageClientError
from tests.test_image_edit_client import picture


class Server:
    pid = 422

    def __init__(self, command, kwargs, root, mode="success"):
        self.command, self.kwargs, self.root, self.mode = command, kwargs, root, mode
        self.stdin = tempfile.TemporaryFile(mode="w+b")
        self.offset = 0
        self.sent = []
        self.cleaned = False
        self.turn_count = 0

    def emit(self, message):
        self.kwargs["stdout"].write((json.dumps(message) + "\n").encode())
        self.kwargs["stdout"].flush()

    def read_input(self):
        import os
        data = os.pread(self.stdin.fileno(), 64 * 1024 * 1024, self.offset)
        self.offset += len(data)
        rows = [json.loads(line) for line in data.splitlines()]
        self.sent.extend(rows)
        return rows

    def usage(self, inputs=13, thread="thread-test", turn="turn-test"):
        self.emit({"method": "thread/tokenUsage/updated", "params": {
            "threadId": thread, "turnId": turn, "tokenUsage": {
                "total": {"inputTokens": inputs, "cachedInputTokens": 3, "outputTokens": 7},
                "last": {"inputTokens": 1, "outputTokens": 1}}}})

    def poll(self):
        for message in self.read_input():
            method = message.get("method")
            if method == "initialize":
                self.emit({"id": 1, "result": {"userAgent": "fixture"}})
            elif method == "thread/start":
                if self.mode == "rpc_error":
                    self.emit({"id": 2, "error": {"code": -1, "message": "PRIVATE_PROVIDER_SECRET"}})
                elif self.mode == "approval":
                    self.emit({"id": "request-approval", "method": "item/commandExecution/requestApproval",
                               "params": {"command": "unsafe"}})
                elif self.mode == "invalid_thread":
                    self.emit({"id": 2, "result": {"thread": "not an object"}})
                else:
                    self.emit({"id": 2, "result": {"thread": {"id": "thread-test"}}})
            elif method == "turn/start":
                self.turn_count += 1
                self.emit({"id": 3, "result": {"turn": {"id": "turn-test", "status": "inProgress", "items": []}}})
                self.usage()
                self.usage()  # cumulative, never another charge
                self.usage(999, thread="other-thread")
                self.usage(999, turn="other-turn")
                if self.mode == "timeout":
                    continue
                if self.mode == "stderr":
                    self.kwargs["stderr"].write(b"unable to read referenced image /PRIVATE_SOURCE/ref.png: fs sandbox helper failed\n")
                    self.kwargs["stderr"].flush()
                    continue
                if self.mode == "malformed":
                    self.kwargs["stdout"].write(b"not-json\n")
                    self.kwargs["stdout"].flush()
                    continue
                if self.mode == "notification_error":
                    self.emit({"method": "error", "params": {"threadId": "thread-test", "turnId": "turn-test",
                        "willRetry": True, "error": {"message": "PRIVATE_PROVIDER_SECRET"}}})
                    continue
                item = {"type": "imageGeneration", "id": "image-1", "status": "completed",
                        "result": base64.b64encode(picture("orange")).decode("ascii"),
                        "failure": None}
                if self.mode == "tool_error":
                    item.update(status="failed", failure={"message": "PRIVATE_SOURCE"})
                if self.mode == "no_image":
                    item["result"] = ""
                if self.mode == "invalid_image":
                    item["result"] = "not-base64!!"
                if self.mode == "incomplete_image":
                    item["status"] = "in_progress"
                if self.mode != "no_receipt":
                    self.emit({"method": "item/completed", "params": {"threadId": "thread-test",
                        "turnId": "turn-test", "item": item}})
                if self.mode == "two_images":
                    self.emit({"method": "item/started", "params": {"threadId": "thread-test",
                        "turnId": "turn-test", "item": {**item, "id": "image-2", "status": "inProgress"}}})
                if self.mode not in {"no_image", "receipt_only"}:
                    image = self.root / "codex-home/generated_images/edit.png"
                    image.parent.mkdir(parents=True, exist_ok=True)
                    image.write_bytes(picture("green"))  # Unrelated file must never replace receipt bytes.
                status = self.mode if self.mode in {"failed", "interrupted"} else "completed"
                self.emit({"method": "turn/completed", "params": {"threadId": "thread-test",
                    "turn": {"id": "turn-test", "status": status,
                             "items": [] if self.mode == "no_receipt" else [item], "error": None}}})
        return 0 if self.mode == "exit_with_completion" and self.turn_count else None

    def cleanup(self):
        self.cleaned = True
        self.read_input()
        if self.mode == "late_usage":
            self.usage(17)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    root = tmp_path / "isolated-output"
    root.mkdir()
    servers = []
    setting = {"mode": "success"}

    def popen(command, **kwargs):
        server = Server(command, kwargs, root, setting["mode"])
        servers.append(server)
        return server

    ticks = iter(i / 100 for i in range(100000))
    monkeypatch.setattr(edit.subprocess, "Popen", popen)
    monkeypatch.setattr(edit.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(edit.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(CodexImageGenerationClient, "_terminate_process_group", lambda server: server.cleanup())
    observation = {"text_usage": None, "text_usage_incomplete_fields": []}
    params = {"cli": "/fake/codex", "instruction": "Edit this conversation image exactly once.",
        "reference_data": picture(), "reference_content_type": "image/png", "root": root,
        "environment": {"CODEX_HOME": str(root / "codex-home")}, "timeout_seconds": 2,
        "observation": observation}
    return params, setting, servers


def test_single_turn_structured_image_preserves_original_bytes_without_local_path(harness):
    params, _, servers = harness
    raw = io.BytesIO()
    Image.new("RGB", (2048, 3072), "navy").save(raw, "PNG")
    params["reference_data"] = raw.getvalue()
    result = edit.run_image_edit(**params)
    server = servers[0]
    assert result.read_bytes() == picture("orange")
    assert len(servers) == 1 and server.turn_count == 1 and server.cleaned and server.stdin.closed
    assert server.command == ["/fake/codex", "app-server", "--enable", "image_generation",
                              "--disable", "omit_app_server_notification_media",
                              "--config", 'model_reasoning_effort="low"']
    assert server.kwargs["start_new_session"] and server.kwargs["cwd"] == str(params["root"])
    methods = [message["method"] for message in server.sent]
    assert methods == ["initialize", "initialized", "thread/start", "turn/start"]
    thread = server.sent[2]["params"]
    assert thread == {"ephemeral": True, "cwd": str(params["root"]), "sandbox": "workspace-write", "approvalPolicy": "never"}
    turn = server.sent[3]["params"]
    assert turn["effort"] == "low" and turn["threadId"] == "thread-test"
    image, text = turn["input"]
    assert image["type"] == "image" and image["detail"] == "original"
    assert base64.b64decode(image["url"].split(",", 1)[1]) == params["reference_data"]
    assert Image.open(io.BytesIO(base64.b64decode(image["url"].split(",", 1)[1]))).size == (2048, 3072)
    assert text == {"type": "text", "text": params["instruction"]}
    assert "localImage" not in json.dumps(turn) and "<image" not in json.dumps(turn)
    assert params["observation"]["text_usage"] == {"input_tokens": 13, "cached_input_tokens": 3, "output_tokens": 7}


@pytest.mark.parametrize("mode,fragment", [
    ("rpc_error", "request was rejected"), ("approval", "no permission was granted"),
    ("timeout", "timed out"), ("stderr", "reference image sandbox"),
    ("malformed", "invalid protocol"), ("notification_error", "automatic retry"),
    ("tool_error", "tool failed"), ("two_images", "single image operation"),
    ("failed", "turn failed"), ("interrupted", "turn failed"), ("no_image", "without image result bytes"),
    ("no_receipt", "successful image operation receipt"),
    ("incomplete_image", "successful image operation receipt"),
    ("invalid_image", "invalid image result bytes"),
    ("invalid_thread", "ephemeral thread"),
])
def test_failures_stop_without_retry_fallback_or_raw_provider_details(harness, mode, fragment):
    params, setting, servers = harness
    setting["mode"] = mode
    with pytest.raises(ImageClientError, match=fragment) as caught:
        edit.run_image_edit(**params)
    assert "PRIVATE_" not in str(caught.value) and not caught.value.retryable
    assert len(servers) == 1 and servers[0].turn_count <= 1
    assert servers[0].cleaned and servers[0].stdin.closed
    if mode == "approval":
        refusal = servers[0].sent[-1]
        assert refusal["id"] == "request-approval" and refusal["error"]["code"] == -32601
        assert "result" not in refusal
    elif mode not in {"rpc_error", "invalid_thread"}:
        assert params["observation"]["text_usage"]["input_tokens"] == 13


def test_final_usage_during_cleanup_replaces_snapshot_once(harness):
    params, setting, _ = harness
    setting["mode"] = "late_usage"
    edit.run_image_edit(**params)
    assert params["observation"]["text_usage"] == {"input_tokens": 17, "cached_input_tokens": 3, "output_tokens": 7}


def test_receipt_bytes_are_sufficient_without_saved_path_or_other_file(harness):
    params, setting, servers = harness
    setting["mode"] = "receipt_only"
    result = edit.run_image_edit(**params)
    assert result.read_bytes() == picture("orange")
    assert result.parent == params["root"]
    assert servers[0].turn_count == 1 and servers[0].cleaned


def test_standalone_core_import_rejects_invalid_input_without_launching_provider():
    import subprocess
    import sys

    core_path = str(Path(__file__).resolve().parents[1] / "core")
    script = f"""
import sys
sys.path.insert(0, {core_path!r})
import codex_image_edit
import image_client
try:
    codex_image_edit.run_image_edit('unused', '', b'', 'image/png', '.', {{}}, 1, {{}})
except image_client.ImageClientError:
    print('standalone-ok')
else:
    raise AssertionError('invalid input launched a provider')
"""
    result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "standalone-ok"


def test_completion_written_between_stdout_read_and_process_exit_is_drained(harness):
    params, setting, servers = harness
    setting["mode"] = "exit_with_completion"
    result = edit.run_image_edit(**params)
    assert result.read_bytes() == picture("orange")
    assert servers[0].cleaned and servers[0].turn_count == 1


def test_messages_handle_partial_utf8_large_lines_and_leftovers_after_consumer_stops():
    with tempfile.TemporaryFile(mode="w+b") as stream:
        reader = edit._Messages(stream)
        first = {"type": "test", "text": "原图" * 50000}
        payload = (json.dumps(first, ensure_ascii=False) + "\n").encode()
        stream.write(payload[:11]); stream.flush()
        assert list(reader.read()) == []
        stream.write(payload[11:] + b'{"next":1}\n'); stream.flush()
        values = reader.read()
        assert next(values) == first
        values.close()  # Failure handling consumes the unread complete suffix.
        assert list(reader.read()) == [{"next": 1}]


def test_partial_token_usage_does_not_invent_missing_fields():
    observation = {}
    edit._usage({"method": "thread/tokenUsage/updated", "params": {
        "threadId": "thread", "turnId": "turn", "tokenUsage": {"total": {
            "inputTokens": 9, "cachedInputTokens": True, "outputTokens": -3}}}},
        observation, "thread", "turn")
    assert observation["text_usage"] == {"input_tokens": 9}
    assert observation["text_usage_incomplete_fields"] == ["cached_input_tokens", "output_tokens"]


@pytest.mark.parametrize("field,value", [
    ("instruction", " "), ("instruction", None), ("reference_data", b""),
    ("reference_data", "base64"), ("reference_content_type", "image/webp"),
    ("timeout_seconds", 0), ("timeout_seconds", True), ("timeout_seconds", float("nan")),
])
def test_invalid_input_never_launches_app_server(harness, field, value):
    params, _, servers = harness
    params[field] = value
    with pytest.raises(ImageClientError, match="requires a valid"):
        edit.run_image_edit(**params)
    assert servers == []
