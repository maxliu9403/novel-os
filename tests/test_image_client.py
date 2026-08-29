from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.image_client import ImageClientError, ImageGenerationClient
from core.studio_settings import CoverSettings


def _webp(width: int = 2048, height: int = 3072) -> bytes:
    payload = (
        b"VP8X"
        + (10).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )
    return b"RIFF" + (len(payload) + 4).to_bytes(4, "little") + b"WEBP" + payload


def _settings(base_url: str, api_key: str = "cover-secret") -> CoverSettings:
    return CoverSettings(
        base_url=base_url,
        api_key=api_key,
        model="gpt-image-2",
        size="2048x3072",
        quality="high",
        output_format="webp",
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
    encoded = base64.b64encode(data or _webp()).decode("ascii")
    return {"created": 1, "data": [{"b64_json": encoded}]}


def test_generate_posts_exact_image2_contract_and_decodes_webp() -> None:
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
            "output_format": "webp",
        },
    }]
    assert result.data == _webp()
    assert result.content_type == "image/webp"
    assert (result.width, result.height) == (2048, 3072)
    assert result.request_id == "req-cover-123"
    assert result.model == "gpt-image-2"


def test_generate_retries_rate_limit_then_succeeds() -> None:
    with _Server([(429, {"error": {"message": "busy"}}), (200, _success())]) as server:
        result = ImageGenerationClient(
            _settings(server.url),
            max_attempts=2,
            sleeper=lambda _seconds: None,
        ).generate("PROMPT")

    assert result.width == 2048
    assert len(server.requests) == 2


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


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"data": [{"b64_json": "not-base64"}]}, "base64"),
        (_success(_webp(1024, 1536)), "2048x3072"),
        ({"data": []}, "image data"),
    ],
)
def test_generate_rejects_malformed_or_wrong_size_images(body: dict, message: str) -> None:
    with _Server([(200, body)]) as server:
        with pytest.raises(ImageClientError, match=message):
            ImageGenerationClient(_settings(server.url)).generate("PROMPT")

