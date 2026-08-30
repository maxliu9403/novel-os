"""OpenAI-compatible image generation client for novel covers."""

from __future__ import annotations

import base64
import binascii
import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

try:
    from .image_binary import aspect_ratio_matches, content_type, dimensions
    from .studio_settings import CoverSettings
except ImportError:  # pragma: no cover - legacy top-level core imports
    from image_binary import aspect_ratio_matches, content_type, dimensions
    from studio_settings import CoverSettings

MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_BYTES = 40 * 1024 * 1024
REQUIRED_IMAGE_MODEL = "gpt-image-2"


class ImageClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    content_type: str
    width: int
    height: int
    request_id: str
    model: str
    request_size: str = ""


class ImageGenerationClient:
    def __init__(
        self,
        settings: CoverSettings,
        *,
        max_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
        opener: Callable[..., object] = urllib.request.urlopen,
    ) -> None:
        if not settings.api_key:
            raise ImageClientError("Cover API key is not configured")
        if settings.model != REQUIRED_IMAGE_MODEL:
            raise ImageClientError(f"Cover image model must be {REQUIRED_IMAGE_MODEL}")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.settings = settings
        self.max_attempts = max_attempts
        self._sleep = sleeper
        self._open = opener

    def generate(self, prompt: str) -> GeneratedImage:
        if not prompt or not prompt.strip():
            raise ImageClientError("Cover prompt is required")
        request = self._request(prompt.strip())
        last_error: ImageClientError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._send(request)
            except ImageClientError as exc:
                last_error = exc
                if not exc.retryable or attempt == self.max_attempts:
                    raise
                self._sleep(float(2 ** (attempt - 1)))
        assert last_error is not None
        raise last_error

    def _request(self, prompt: str) -> urllib.request.Request:
        body = json.dumps({
            "model": self.settings.model,
            "prompt": prompt,
            "n": 1,
            "size": self.settings.size,
            "quality": self.settings.quality,
            "output_format": self.settings.output_format,
        }, ensure_ascii=False).encode("utf-8")
        return urllib.request.Request(
            f"{self.settings.base_url.rstrip('/')}/images/generations",
            data=body,
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

    def _send(self, request: urllib.request.Request) -> GeneratedImage:
        try:
            response = self._open(request, timeout=self.settings.timeout_seconds)
            with response:  # type: ignore[attr-defined]
                raw = response.read(MAX_RESPONSE_BYTES + 1)  # type: ignore[attr-defined]
                headers = response.headers  # type: ignore[attr-defined]
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            detail = self._provider_error_detail(exc)
            message = f"Image provider returned HTTP {exc.code}"
            if detail:
                message = f"{message}: {detail}"
            raise ImageClientError(
                message,
                retryable=retryable,
                status_code=exc.code,
            ) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise ImageClientError("Image provider request timed out", retryable=True) from None
        except OSError:
            raise ImageClientError("Image provider request failed", retryable=True) from None

        if len(raw) > MAX_RESPONSE_BYTES:
            raise ImageClientError("Image provider response exceeds the allowed size")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ImageClientError("Image provider returned malformed JSON") from None

        items = payload.get("data") if isinstance(payload, dict) else None
        encoded = items[0].get("b64_json") if isinstance(items, list) and items else None
        if not isinstance(encoded, str) or not encoded:
            raise ImageClientError("Image provider returned no image data")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ImageClientError("Image provider returned invalid base64 image data") from None
        if not data:
            raise ImageClientError("Image provider returned empty image data")
        if len(data) > MAX_IMAGE_BYTES:
            raise ImageClientError("Generated image exceeds the allowed size")

        mime = content_type(data)
        if mime not in {"image/png", "image/jpeg"}:
            raise ImageClientError("Image provider returned an unsupported image type")
        width, height = dimensions(data)
        if not aspect_ratio_matches(width, height):
            raise ImageClientError(
                "Generated image must preserve portrait 2:3 aspect ratio; "
                f"received {width}x{height}"
            )
        request_id = str(headers.get("x-request-id") or headers.get("request-id") or "")
        return GeneratedImage(
            data=data,
            content_type=mime,
            width=width,
            height=height,
            request_id=request_id,
            model=self.settings.model,
            request_size=self.settings.size,
        )

    def _provider_error_detail(self, error: urllib.error.HTTPError) -> str:
        try:
            raw = error.read(8192)
        except OSError:
            return ""
        if not raw:
            return ""

        detail = ""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = raw.decode("utf-8", "replace").strip()
        else:
            if isinstance(payload, dict):
                provider_error = payload.get("error")
                if isinstance(provider_error, dict):
                    detail = str(
                        provider_error.get("message")
                        or provider_error.get("detail")
                        or provider_error.get("code")
                        or ""
                    ).strip()
                elif isinstance(provider_error, str):
                    detail = provider_error.strip()
                else:
                    detail = str(payload.get("message") or payload.get("detail") or "").strip()
        if self.settings.api_key:
            detail = detail.replace(self.settings.api_key, "[redacted]")
        detail = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", detail)
        return detail[:300]
