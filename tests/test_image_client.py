from __future__ import annotations

import base64
import io
import json
import subprocess
import threading
import urllib.error
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import core.image_client as image_client
from core.image_client import (
    CodexImageGenerationClient,
    ImageClientError,
    ImageGenerationClient,
)
from core.studio_settings import CoverSettings


def test_codex_image_host_usage_sums_explicit_turns_without_recounting_snapshots():
    from core.image_client import _capture_codex_usage

    events = [
        {'type': 'turn.started'},
        {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'cached_input_tokens': 2, 'output_tokens': 3}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'cached_input_tokens': 2, 'output_tokens': 3}},
        {'type': 'turn.started'},
        {'type': 'turn.completed', 'usage': {'input_tokens': 20, 'output_tokens': 7}},
    ]
    output = '\n'.join(json.dumps(row) for row in events) + '\n'
    observation = {'text_usage': None}
    _capture_codex_usage(output, observation)
    _capture_codex_usage(output, observation)
    assert observation['text_usage'] == {'input_tokens': 30, 'cached_input_tokens': 2, 'output_tokens': 10}
    assert 'cached_input_tokens' in observation['text_usage_incomplete_fields']


def test_image_client_standalone_import_and_usage_without_repository_on_sys_path(tmp_path):
    import os
    import sys
    from pathlib import Path

    core_path = str(Path(__file__).resolve().parents[1] / 'core')
    script = '''
import sys
sys.path.insert(0, sys.argv[1])
from image_client import ImageGenerationClient, _image_usage, _capture_codex_usage
from studio_settings import CoverSettings
settings = CoverSettings(base_url='https://unused.invalid', api_key='test', model='gpt-image-2.5-flare')
ImageGenerationClient(settings)
with _image_usage(settings) as observation:
    _capture_codex_usage('{"type":"turn.completed","usage":{"input_tokens":2,"output_tokens":3}}', observation)
    assert observation['text_usage']['input_tokens'] == 2
'''
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, '-I', '-c', script, core_path],
                            cwd=tmp_path, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _jpeg(width: int = 2048, height: int = 3072) -> bytes:
    # Minimal SOF0 header; image clients only need type and dimensions here.
    return (
        b"\xff\xd8\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9"
    )


def _png(width: int = 2048, height: int = 3072) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _settings(base_url: str, api_key: str = "cover-secret") -> CoverSettings:
    return CoverSettings(
        base_url=base_url,
        api_key=api_key,
        model="gpt-image-2",
        size="2048x3072",
        quality="high",
        output_format="jpeg",
        count=4,
        timeout_seconds=2,
    )


class _Server:
    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                owner.requests.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(length)),
                })
                status, body = owner.responses.pop(0)
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("x-request-id", "req-cover-123")
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> "_Server":
        self.thread.start()
        return self

    def __exit__(self, *_args) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=2)
        self.httpd.server_close()


def _success(data: bytes | None = None) -> dict:
    encoded = base64.b64encode(data or _jpeg()).decode("ascii")
    return {"created": 1, "data": [{"b64_json": encoded}]}


def test_generate_posts_exact_image2_contract_and_decodes_jpeg() -> None:
    with _Server([(200, _success())]) as server:
        result = ImageGenerationClient(_settings(server.url)).generate("COVER PROMPT")

    assert server.requests == [{
        "path": "/v1/images/generations",
        "authorization": "Bearer cover-secret",
        "body": {
            "model": "gpt-image-2",
            "prompt": "COVER PROMPT",
            "n": 1,
            "size": "2048x3072",
            "quality": "high",
            "output_format": "jpeg",
        },
    }]
    assert result.data == _jpeg()
    assert result.content_type == "image/jpeg"
    assert (result.width, result.height) == (2048, 3072)
    assert result.request_id == "req-cover-123"
    assert result.model == "gpt-image-2"
    assert result.request_size == "2048x3072"


def test_client_accepts_configured_image_model() -> None:
    client = ImageGenerationClient(
        replace(_settings("https://images.example/v1"), model="publisher/image-v3")
    )

    assert client.settings.model == "publisher/image-v3"


def test_codex_ambiguous_image25_model_reports_exact_ids_without_running_provider(monkeypatch) -> None:
    monkeypatch.setattr(image_client.shutil, "which", lambda _name: "/fake/codex")
    with pytest.raises(ImageClientError, match="exact.*gpt-image-2.5-sunburst.*gpt-image-2.5-flare"):
        CodexImageGenerationClient(
            replace(_settings("", api_key=""), provider="codex", model="gpt-image-2.5"),
        )


@pytest.mark.parametrize("model", ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"])
def test_codex_exact_image25_reports_transport_capability_without_launch(monkeypatch, model):
    monkeypatch.setattr(image_client.shutil, "which", lambda _name: "/fake/codex")
    with pytest.raises(ImageClientError, match="cannot select.*image model.*API"):
        CodexImageGenerationClient(replace(_settings(""), provider="codex", model=model))


@pytest.mark.parametrize("model", ["codex-image-auto", "gpt-image-2"])
def test_codex_auto_and_legacy_profiles_use_builtin_tool_without_claiming_an_image_model(monkeypatch, tmp_path, model):
    from pathlib import Path
    monkeypatch.setattr(image_client.shutil, "which", lambda _: "/fake/codex")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    calls = []
    def runner(command, **kwargs):
        calls.append(kwargs["input"])
        assert "gpt-image-2" not in kwargs["input"]
        assert "codex-image-auto" not in kwargs["input"]
        assert "built-in image generation tool" in kwargs["input"]
        Path(command[command.index("--cd") + 1], "cover.jpg").write_bytes(_jpeg())
        return subprocess.CompletedProcess(command, 0, "", "")
    result = CodexImageGenerationClient(replace(_settings(""), provider="codex", model=model), runner=runner).generate("A source-bound cover")
    assert len(calls) == 1
    assert result.model == model  # Configured transport selector, not verified model identity.
    assert result.observed_model == "" and not result.model_verified


def test_api_does_not_send_codex_transport_selector_as_a_model_id():
    with pytest.raises(ImageClientError, match="Codex"):
        ImageGenerationClient(replace(_settings("https://unused.invalid"), model="codex-image-auto"),
                              opener=lambda *args, **kwargs: pytest.fail("must not send"))


def _memory_response(payload):
    response = io.BytesIO(json.dumps(payload).encode())
    response.headers = {"x-request-id": "req-model-check"}
    return response


@pytest.mark.parametrize("model", ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"])
def test_image25_posts_exact_model_and_records_only_reported_identity(model):
    sent = []
    def opened(request, **kwargs):
        sent.append(json.loads(request.data))
        return _memory_response({**_success(), "model": model})
    generated = ImageGenerationClient(replace(_settings("https://api.openai.com/v1"), model=model),
                                      opener=opened).generate("one cover")
    assert sent[0]["model"] == model
    assert generated.model == generated.observed_model == model
    assert generated.model_verified is True


def test_image25_response_from_different_model_is_not_mislabeled():
    calls = []
    def opened(*args, **kwargs):
        calls.append(1)
        return _memory_response({**_success(), "model": "gpt-image-2"})
    client = ImageGenerationClient(replace(_settings("https://api.openai.com/v1"), model="gpt-image-2.5-sunburst"),
                                   opener=opened)
    with pytest.raises(ImageClientError, match="model mismatch"):
        client.generate("one cover")
    assert calls == [1]


def test_missing_response_model_remains_unverified():
    generated = ImageGenerationClient(_settings("https://api.openai.com/v1"),
        opener=lambda *args, **kwargs: _memory_response(_success())).generate("one cover")
    assert generated.model == "gpt-image-2"
    assert generated.observed_model == ""
    assert generated.model_verified is False


def test_generate_posts_png_when_configured() -> None:
    with _Server([(200, _success(_png()))]) as server:
        result = ImageGenerationClient(
            replace(_settings(server.url), output_format="png")
        ).generate("PNG COVER PROMPT")

    assert server.requests[0]["body"]["output_format"] == "png"
    assert result.data == _png()
    assert result.content_type == "image/png"


def test_generate_rejects_webp_response() -> None:
    webp_header = b"RIFF\x04\x00\x00\x00WEBP"
    with _Server([(200, _success(webp_header))]) as server:
        with pytest.raises(ImageClientError, match="unsupported image type"):
            ImageGenerationClient(_settings(server.url)).generate("PROMPT")


def test_generate_retries_rate_limit_then_succeeds() -> None:
    with _Server([(429, {"error": {"message": "busy"}}), (200, _success())]) as server:
        result = ImageGenerationClient(
            _settings(server.url),
            max_attempts=2,
            sleeper=lambda _seconds: None,
        ).generate("PROMPT")

    assert result.width == 2048
    assert len(server.requests) == 2


@pytest.mark.parametrize("failure", [429, 503, "timeout"])
def test_production_factory_spends_one_request_per_candidate_on_retryable_failure(failure) -> None:
    client = image_client.build_image_generation_client(_settings("https://unused.invalid/v1"))
    requests = []
    sleeps = []

    def failing_open(request, *, timeout):
        requests.append(json.loads(request.data))
        if failure == "timeout":
            raise TimeoutError("Fake response timeout after the request was sent")
        raise urllib.error.HTTPError(request.full_url, failure, "Fake provider error", {}, None)

    client._open = failing_open
    client._sleep = sleeps.append
    for index in range(3):
        with pytest.raises(ImageClientError) as raised:
            client.generate(f"Approved candidate {index + 1}")
        assert raised.value.retryable is True

    assert [request["prompt"] for request in requests] == [
        "Approved candidate 1", "Approved candidate 2", "Approved candidate 3",
    ]
    assert all(request["n"] == 1 for request in requests)
    assert sleeps == []


@pytest.mark.parametrize("failure", ["timeout", "exit"])
def test_production_codex_factory_does_not_restart_a_failed_image_call(monkeypatch, tmp_path, failure) -> None:
    monkeypatch.setattr(image_client.shutil, "which", lambda _name: "/fake/codex")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    client = image_client.build_image_generation_client(
        replace(_settings("", api_key=""), provider="codex"),
    )
    calls = []

    def failed_run(command, **kwargs):
        calls.append(kwargs["input"])
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 1, "", "Fake process failure")

    client._run = failed_run
    with pytest.raises(ImageClientError, match="timed out|failed"):
        client.generate("One approved candidate")
    assert len(calls) == 1
    assert "image generation tool exactly once" in calls[0]


def test_generate_does_not_retry_authentication_or_leak_secret() -> None:
    with _Server([(401, {"error": {"message": "bad cover-secret"}})]) as server:
        with pytest.raises(ImageClientError) as raised:
            ImageGenerationClient(
                _settings(server.url),
                max_attempts=3,
                sleeper=lambda _seconds: None,
            ).generate("PROMPT")

    assert raised.value.status_code == 401
    assert raised.value.retryable is False
    assert len(server.requests) == 1
    assert "cover-secret" not in str(raised.value)


def test_generate_accepts_upstream_portrait_resolution() -> None:
    with _Server([(200, _success(_jpeg(1024, 1536)))]) as server:
        result = ImageGenerationClient(_settings(server.url)).generate("PROMPT")

    assert (result.width, result.height) == (1024, 1536)


def test_generate_accepts_minor_portrait_raster_rounding() -> None:
    with _Server([(200, _success(_jpeg(1023, 1537)))]) as server:
        result = ImageGenerationClient(_settings(server.url)).generate("PROMPT")

    assert (result.width, result.height) == (1023, 1537)


def test_generate_surfaces_provider_permission_detail_without_api_key() -> None:
    with _Server([(
        403,
        {"error": {"type": "permission_error", "message": "Image generation is not enabled for this group"}},
    )]) as server:
        with pytest.raises(ImageClientError, match="not enabled for this group") as raised:
            ImageGenerationClient(_settings(server.url)).generate("PROMPT")

    assert raised.value.status_code == 403
    assert raised.value.retryable is False
    assert "cover-secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"data": [{"b64_json": "not-base64"}]}, "base64"),
        (_success(_jpeg(1024, 1024)), "2:3"),
        ({"data": []}, "image data"),
    ],
)
def test_generate_rejects_malformed_or_wrong_ratio_images(body: dict, message: str) -> None:
    with _Server([(200, body)]) as server:
        with pytest.raises(ImageClientError, match=message):
            ImageGenerationClient(_settings(server.url)).generate("PROMPT")


def test_codex_image_discovery_skips_candidate_moved_before_stat() -> None:
    class Candidate:
        suffix = ".jpg"

        def __init__(self, *, mtime: int, data: bytes, moved: bool = False) -> None:
            self.mtime = mtime
            self.data = data
            self.moved = moved

        def is_file(self) -> bool:
            return True

        def stat(self):
            if self.moved:
                raise FileNotFoundError("candidate was moved")
            return SimpleNamespace(st_mtime_ns=self.mtime)

        def read_bytes(self) -> bytes:
            return self.data

    moved = Candidate(mtime=2, data=_jpeg(), moved=True)
    stable = Candidate(mtime=1, data=_jpeg())

    class Root:
        def rglob(self, _pattern: str):
            return iter((moved, stable))

    assert CodexImageGenerationClient._newest_valid_image((Root(),)) is stable


def test_codex_image_generation_rediscovers_candidate_moved_before_read(
    monkeypatch,
) -> None:
    monkeypatch.setattr(image_client.shutil, "which", lambda _name: "/usr/bin/codex")

    class MovedCandidate:
        def read_bytes(self) -> bytes:
            raise FileNotFoundError("candidate was moved")

    class StableCandidate:
        def read_bytes(self) -> bytes:
            return _jpeg()

    candidates = iter((MovedCandidate(), StableCandidate()))
    monkeypatch.setattr(
        CodexImageGenerationClient,
        "_newest_valid_image",
        staticmethod(lambda _roots: next(candidates)),
    )
    client = CodexImageGenerationClient(
        _settings("", api_key=""),
        runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, "", "",
        ),
    )

    generated = client.generate("A portrait cover")

    assert generated.data == _jpeg()
    assert (generated.width, generated.height) == (2048, 3072)


def test_codex_image_wait_cleans_up_process_when_discovery_raises(
    monkeypatch,
) -> None:
    monkeypatch.setattr(image_client.shutil, "which", lambda _name: "/usr/bin/codex")

    class Process:
        pid = 17
        stdin = io.StringIO()

    process = Process()
    monkeypatch.setattr(image_client.subprocess, "Popen", lambda *_args, **_kwargs: process)
    client = CodexImageGenerationClient(_settings("", api_key=""))
    monkeypatch.setattr(
        client,
        "_newest_valid_image",
        lambda _roots: (_ for _ in ()).throw(FileNotFoundError("scan race")),
    )
    cleaned: list[object] = []
    monkeypatch.setattr(
        client,
        "_terminate_process_group",
        lambda active: cleaned.append(active),
    )

    with pytest.raises(FileNotFoundError, match="scan race"):
        client._wait_for_generated_image([], "prompt", {}, ())

    assert cleaned == [process]


def test_http_usage_is_recorded_once_per_real_attempt_including_retries(monkeypatch):
    from core import usage_tracking
    events = []
    monkeypatch.setattr(usage_tracking, 'record_usage', lambda **event: events.append(event))
    replies = iter([429, 200])
    usage = {'input_tokens': 25, 'output_tokens': 500, 'input_tokens_details': {'cached_tokens': 5}}
    def opened(request, **kwargs):
        if next(replies) == 429:
            raise urllib.error.HTTPError(request.full_url, 429, 'busy', {}, None)
        return _memory_response({**_success(), 'model': 'gpt-image-2', 'usage': usage})
    ImageGenerationClient(_settings('https://unused.invalid'), max_attempts=2,
                          opener=opened, sleeper=lambda _: None).generate('private prompt')
    final = [e for e in events if e['status'] != 'started']
    assert len(final) == 2 and len({e['request_id'] for e in final}) == 2
    assert [e['status'] for e in final] == ['failed', 'completed']
    assert final[0]['usage'] is None and final[0]['image_count'] is None
    assert final[1]['usage'] == usage and final[1]['image_count'] == 1
    assert final[1]['observed_model'] == 'gpt-image-2'
    assert all(e['kind'] == 'image' for e in events)
    assert 'private prompt' not in repr(events) and 'cover-secret' not in repr(events)


@pytest.mark.parametrize('multiturn', [False, True])
def test_codex_host_text_usage_is_separate_from_unknown_image_usage(monkeypatch, tmp_path, multiturn):
    from pathlib import Path
    from core import usage_tracking
    events = []
    monkeypatch.setattr(usage_tracking, 'record_usage', lambda **event: events.append(event))
    monkeypatch.setattr(image_client.shutil, 'which', lambda _: '/fake/codex')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    usage = {'input_tokens': 80, 'cached_input_tokens': 20, 'output_tokens': 10}
    def runner(command, **kwargs):
        assert '--json' in command
        Path(command[command.index('--cd') + 1], 'cover.jpg').write_bytes(_jpeg())
        rows = [{'type': 'turn.started'}, {'type': 'turn.completed', 'usage': usage}]
        if multiturn:
            rows += [{'type': 'turn.started'}, {'type': 'turn.completed',
                     'usage': {'input_tokens': 5, 'output_tokens': 2}}]
        return subprocess.CompletedProcess(command, 0, '\n'.join(map(json.dumps, rows)), '')
    generated = CodexImageGenerationClient(replace(_settings(''), provider='codex'), runner=runner).generate('prompt')
    assert generated.model == 'gpt-image-2' and not generated.model_verified and generated.observed_model == ''
    images = [e for e in events if e['kind'] == 'image' and e['status'] != 'started']
    texts = [e for e in events if e['kind'] == 'text']
    assert len(images) == len(texts) == 1
    assert images[0]['image_count'] == 1 and images[0]['usage'] is None and images[0]['observed_model'] is None
    expected_usage = {'input_tokens': 85, 'cached_input_tokens': 20, 'output_tokens': 12} if multiturn else usage
    assert texts[0]['model'] == 'unknown' and texts[0]['usage'] == expected_usage
    assert ('cached_input_tokens' in texts[0]['usage_incomplete_fields']) is multiturn
    assert texts[0]['request_id'] == images[0]['request_id'] + ':host-text'


def test_api_ambiguous_image25_never_substitutes_or_sends_request():
    with pytest.raises(ImageClientError, match='exact image model'):
        ImageGenerationClient(replace(_settings('https://unused.invalid'), model='gpt-image-2.5'),
                              opener=lambda *args, **kwargs: pytest.fail('must not send'))


def test_api_accepts_documented_snapshot_of_requested_alias_without_rewriting_request():
    requested, observed = 'gpt-image-2.5-flare', 'gpt-image-2.5-flare-2026-09-08'
    captured = []
    def opened(request, **kwargs):
        captured.append(json.loads(request.data)['model'])
        return _memory_response({**_success(), 'model': observed})
    result = ImageGenerationClient(replace(_settings('https://unused.invalid'), model=requested),
                                   opener=opened).generate('prompt')
    assert captured == [requested]
    assert result.model == requested and result.observed_model == observed and result.model_verified


def test_api_checks_per_image_model_and_does_not_pick_one_conflicting_identity(monkeypatch):
    from core import usage_tracking
    events = []
    monkeypatch.setattr(usage_tracking, 'record_usage', lambda **event: events.append(event))
    payload = _success()
    payload['model'] = 'gpt-image-2.5-sunburst'
    payload['data'][0]['model'] = 'gpt-image-2'
    with pytest.raises(ImageClientError, match='model mismatch'):
        ImageGenerationClient(replace(_settings('https://unused.invalid'), model='gpt-image-2.5-sunburst'),
            opener=lambda *args, **kwargs: _memory_response(payload)).generate('prompt')
    final = [event for event in events if event['status'] == 'failed']
    assert len(final) == 1 and final[0]['observed_model'] is None and final[0]['image_count'] == 1
