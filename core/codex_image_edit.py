"""One source-bound image edit through Codex app-server's structured input.

The original bytes are a conversation image URL, never a localImage/path. This
avoids the CLI attachment transform and a second sandboxed filesystem read.
No provider retry, interactive approval, or CLI fallback is performed here.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


_MAX_LINE_BYTES = 96 * 1024 * 1024


def _client_api():
    try:
        from . import image_client
    except ImportError:  # CLI's historical top-level core imports.
        import image_client
    return image_client


def _send(process, message, deadline):
    ImageClientError = _client_api().ImageClientError
    data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    try:
        descriptor = process.stdin.fileno()
        os.set_blocking(descriptor, False)
        remaining = memoryview(data)
        while remaining:
            if time.monotonic() >= deadline:
                raise ImageClientError("Codex image edit timed out", retryable=False)
            try:
                written = os.write(descriptor, remaining)
            except BlockingIOError:
                time.sleep(.02)
                continue
            if written <= 0:
                raise OSError("closed input")
            remaining = remaining[written:]
    except (OSError, AttributeError, ValueError):
        raise ImageClientError("Could not send the image edit to Codex app-server") from None


class _Messages:
    def __init__(self, stream):
        self.stream, self.offset, self.pending = stream, 0, bytearray()
        self.scanned = 0

    def read(self):
        ImageClientError = _client_api().ImageClientError
        size = os.fstat(self.stream.fileno()).st_size
        while True:
            while (newline := self.pending.find(b"\n", self.scanned)) >= 0:
                line = bytes(self.pending[:newline])
                del self.pending[:newline + 1]
                self.scanned = 0
                if len(line) > _MAX_LINE_BYTES:
                    raise ImageClientError("Codex image edit protocol message exceeded its limit")
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError):
                    raise ImageClientError("Codex image edit returned an invalid protocol message") from None
                if not isinstance(message, dict):
                    raise ImageClientError("Codex image edit returned an invalid protocol message")
                yield message
            self.scanned = len(self.pending)
            if len(self.pending) > _MAX_LINE_BYTES:
                raise ImageClientError("Codex image edit protocol message exceeded its limit")
            if self.offset >= size:
                break
            block = os.pread(self.stream.fileno(), min(65536, size - self.offset), self.offset)
            if not block:
                break
            self.offset += len(block)
            self.pending.extend(block)


def _usage(message, observation, thread_id, turn_id):
    if message.get("method") != "thread/tokenUsage/updated" or observation is None:
        return
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return
    if params.get("threadId") != thread_id or params.get("turnId") != turn_id:
        return
    usage = params.get("tokenUsage")
    total = usage.get("total") if isinstance(usage, dict) else None
    if not isinstance(total, dict):
        return
    fields = {"inputTokens": "input_tokens", "cachedInputTokens": "cached_input_tokens",
              "outputTokens": "output_tokens"}
    value = {target: total[source] for source, target in fields.items()
             if type(total.get(source)) is int and total[source] >= 0}
    if value:
        # total is one cumulative snapshot for this ephemeral thread. Replacing
        # it avoids charging repeated notifications as additional model turns.
        observation["text_usage"] = value
        observation["text_usage_incomplete_fields"] = sorted(set(fields.values()) - set(value))


def _save_receipt_image(receipt, root):
    """Accept only the bytes returned by the completed image operation."""
    api = _client_api()
    encoded = receipt.get("result")
    if not isinstance(encoded, str) or not encoded:
        raise api.ImageClientError("Codex image edit completed without image result bytes")
    if len(encoded) > 4 * ((api.MAX_IMAGE_BYTES + 2) // 3):
        raise api.ImageClientError("Codex image edit result exceeded its image size limit")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError):
        raise api.ImageClientError("Codex image edit returned invalid image result bytes") from None
    if not data or len(data) > api.MAX_IMAGE_BYTES:
        raise api.ImageClientError("Codex image edit returned invalid image result bytes")
    mime = api.content_type(data)
    if mime not in {"image/png", "image/jpeg"} or not api.aspect_ratio_matches(*api.dimensions(data)):
        raise api.ImageClientError("Codex image edit returned an invalid portrait image result")
    # savedPath may be absent or point at a different file. Persist the exact
    # authoritative receipt bytes instead of discovering an unrelated image.
    try:
        with tempfile.NamedTemporaryFile(prefix="edited-result-", suffix=".png" if mime == "image/png" else ".jpg",
                                         dir=root, delete=False) as output:
            output.write(data)
            return Path(output.name)
    except OSError:
        raise api.ImageClientError("Codex image edit result could not be saved") from None


def run_image_edit(cli, instruction, reference_data, reference_content_type, root,
                   environment, timeout_seconds, observation):
    api = _client_api()
    CodexImageGenerationClient, ImageClientError = api.CodexImageGenerationClient, api.ImageClientError
    if (not isinstance(instruction, str) or not instruction.strip()
            or not isinstance(reference_data, bytes) or not reference_data
            or len(reference_data) > api.MAX_IMAGE_BYTES
            or reference_content_type not in {"image/png", "image/jpeg"}
            or isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds < float("inf")):
        raise ImageClientError("Codex image edit requires a valid image, instruction and timeout")
    root = Path(root).resolve()
    command = [cli, "app-server", "--enable", "image_generation",
               "--disable", "omit_app_server_notification_media",
               "--config", 'model_reasoning_effort="low"']
    deadline = time.monotonic() + timeout_seconds
    with tempfile.TemporaryFile(mode="w+b") as stdout, tempfile.TemporaryFile(mode="w+b") as stderr:
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                cwd=str(root), env=environment, start_new_session=True)
        except OSError:
            raise ImageClientError("Codex image edit app-server could not start") from None
        messages, diagnostics = _Messages(stdout), api._ReferenceReadDiagnostics()
        thread_id = turn_id = None
        waiting = "initialize"
        deferred = []
        completed = False
        image_ids = set()
        receipts = {}
        drained_exit = False

        def notification(message):
            nonlocal completed
            _usage(message, observation, thread_id, turn_id)
            method, params = message.get("method"), message.get("params") or {}
            if not isinstance(params, dict) or params.get("threadId") != thread_id:
                return
            if method == "turn/completed":
                turn = params.get("turn") or {}
                if not isinstance(turn, dict):
                    raise ImageClientError("Codex image edit returned an invalid turn notification")
                if turn.get("id") != turn_id:
                    return
                if turn.get("status") != "completed" or turn.get("error"):
                    raise ImageClientError("Codex image edit turn failed; no edited image was accepted")
                for item in turn.get("items", []):
                    check_item(item, finished=True)
                completed = True
            elif params.get("turnId") == turn_id:
                if method == "error":
                    raise ImageClientError("Codex image edit failed; no automatic retry is permitted")
                if method in {"item/started", "item/completed"}:
                    check_item(params.get("item") or {}, finished=method == "item/completed")

        def check_item(item, *, finished):
            if not isinstance(item, dict) or item.get("type") != "imageGeneration":
                return
            identifier = item.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise ImageClientError("Codex image edit returned an invalid image operation receipt")
            image_ids.add(identifier)
            if len(image_ids) > 1:
                raise ImageClientError("Codex image edit attempted more than the approved single image operation")
            if item.get("failure") or item.get("status") in {"failed", "cancelled", "canceled"}:
                raise ImageClientError("Codex image editing tool failed; no edited image was accepted")
            if finished and item.get("status") == "completed":
                if identifier in receipts and receipts[identifier].get("result") != item.get("result"):
                    raise ImageClientError("Codex image edit returned conflicting image operation receipts")
                receipts[identifier] = item

        try:
            _send(process, {"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "novel_os_cover_edit", "version": "1.0.0"},
                "capabilities": {"experimentalApi": True}}}, deadline)
            while time.monotonic() < deadline:
                diagnostics.check(stderr)
                for message in messages.read():
                    if "method" in message and "id" in message:
                        # Never grant permissions or answer on behalf of the user.
                        _send(process, {"id": message["id"], "error": {"code": -32601,
                            "message": "Interactive requests are not supported by a preapproved image edit"}}, deadline)
                        raise ImageClientError("Codex image edit requested interactive approval; no permission was granted")
                    if "method" in message:
                        if thread_id is not None and turn_id is None:
                            deferred.append(message)
                            if len(deferred) > 256:
                                raise ImageClientError("Codex image edit protocol exceeded pending notification limit")
                        elif turn_id is not None:
                            notification(message)
                        continue
                    response_id = message.get("id")
                    if type(response_id) is not int or response_id not in {1, 2, 3}:
                        continue
                    if message.get("error") is not None:
                        raise ImageClientError(f"Codex image edit {waiting} request was rejected")
                    result = message.get("result")
                    if not isinstance(result, dict):
                        raise ImageClientError("Codex image edit returned an invalid response")
                    if response_id == 1 and waiting == "initialize":
                        _send(process, {"method": "initialized", "params": {}}, deadline)
                        _send(process, {"id": 2, "method": "thread/start", "params": {
                            "ephemeral": True, "cwd": str(root), "sandbox": "workspace-write",
                            "approvalPolicy": "never"}}, deadline)
                        waiting = "thread/start"
                    elif response_id == 2 and waiting == "thread/start":
                        thread = result.get("thread")
                        thread_id = thread.get("id") if isinstance(thread, dict) else None
                        if not isinstance(thread_id, str) or not thread_id:
                            raise ImageClientError("Codex image edit did not create its ephemeral thread")
                        data_url = f"data:{reference_content_type};base64," + base64.b64encode(reference_data).decode("ascii")
                        _send(process, {"id": 3, "method": "turn/start", "params": {
                            "threadId": thread_id, "effort": "low", "input": [
                                {"type": "image", "url": data_url, "detail": "original"},
                                {"type": "text", "text": instruction.strip()}]}}, deadline)
                        waiting = "turn/start"
                    elif response_id == 3 and waiting == "turn/start":
                        turn = result.get("turn")
                        if not isinstance(turn, dict):
                            raise ImageClientError("Codex image edit returned an invalid turn response")
                        turn_id = turn.get("id")
                        if not isinstance(turn_id, str) or not turn_id:
                            raise ImageClientError("Codex image edit did not start its single turn")
                        waiting = "turn/completed"
                        if turn.get("status") in {"completed", "failed", "interrupted"}:
                            notification({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}})
                        for pending in deferred:
                            notification(pending)
                        deferred.clear()
                if completed:
                    diagnostics.check(stderr)
                    if len(receipts) != 1 or set(receipts) != image_ids:
                        raise ImageClientError("Codex image edit completed without its successful image operation receipt")
                    return _save_receipt_image(next(iter(receipts.values())), root)
                if process.poll() is not None:
                    diagnostics.check(stderr)
                    # The final completion/usage lines can arrive between the
                    # read above and poll(). Drain that suffix once before
                    # concluding the app-server exited prematurely.
                    if not drained_exit:
                        drained_exit = True
                        continue
                    raise ImageClientError("Codex image edit app-server exited before turn completion")
                time.sleep(.02)
            raise ImageClientError("Codex image edit timed out", retryable=False)
        finally:
            CodexImageGenerationClient._terminate_process_group(process)
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            # Keep settled cumulative usage even when completion/cleanup races
            # a final usage notification; do not reinterpret outputs as success.
            for message in messages.read():
                if (turn_id is None and waiting == "turn/start" and message.get("id") == 3
                        and isinstance(message.get("result"), dict)):
                    turn = message["result"].get("turn")
                    value = turn.get("id") if isinstance(turn, dict) else None
                    if isinstance(value, str) and value:
                        turn_id = value
                _usage(message, observation, thread_id, turn_id)
            for message in deferred:
                _usage(message, observation, thread_id, turn_id)
            diagnostics.check(stderr)
