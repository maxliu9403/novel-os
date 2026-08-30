from __future__ import annotations

import base64
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.image_client import ImageClientError, ImageGenerationClient
from core.studio_settings import CoverSettings


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
