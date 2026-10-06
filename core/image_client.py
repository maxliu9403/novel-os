"""OpenAI-compatible image generation client for novel covers."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import signal
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    from .image_binary import aspect_ratio_matches, content_type, dimensions
    from .studio_settings import CoverSettings
except ImportError:  # pragma: no cover - legacy top-level core imports
    from image_binary import aspect_ratio_matches, content_type, dimensions
    from studio_settings import CoverSettings

MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_BYTES = 40 * 1024 * 1024


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
        if not settings.model.strip():
            raise ImageClientError("Cover image model is not configured")
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


class CodexImageGenerationClient:
    """Generate an image through the authenticated Codex image tool."""

    def __init__(
        self,
        settings: CoverSettings,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        cli = shutil.which("codex")
        if not cli:
            raise ImageClientError("Codex CLI is not available in the API runtime")
        if settings.model != "gpt-image-2":
            raise ImageClientError("Codex image generation currently requires gpt-image-2")
        self.settings = settings
        self.cli = cli
        self._run = runner

    def generate(self, prompt: str) -> GeneratedImage:
        if not prompt or not prompt.strip():
            raise ImageClientError("Cover prompt is required")
        with tempfile.TemporaryDirectory(prefix="novel-os-image-") as directory:
            root = Path(directory)
            codex_home = self._isolated_codex_home(root)
            generated_root = codex_home / "generated_images"
            generated_root.mkdir(parents=True, exist_ok=True)
            message_path = root / "result.txt"
            output_name = f"cover.{self.settings.output_format.replace('jpeg', 'jpg')}"
            instruction = (
                "Use the image generation tool exactly once. Generate an original "
                f"portrait book cover from the prompt below using {self.settings.model}. "
                f"Target {self.settings.size}, {self.settings.quality} quality, and "
                f"{self.settings.output_format.upper()} output. Save the final image as "
                f"{output_name} in the current working directory. Do not use shell or "
                "code to synthesize the image. Return only the saved file path.\n\n"
                f"COVER PROMPT\n{prompt.strip()}"
            )
            command = [
                self.cli,
                "exec",
                "--ephemeral",
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
                "--ignore-rules",
                "--enable",
                "image_generation",
                "--config",
                'model_reasoning_effort="low"',
                "--cd",
                str(root),
                "--output-last-message",
                str(message_path),
                "-",
            ]
            environment = {
                **os.environ,
                "CODEX_HOME": str(codex_home),
                "NO_COLOR": "1",
            }
            search_roots = (root, generated_root)
            if self._run is not None:
                try:
                    process = self._run(
                        command,
                        input=instruction,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=self.settings.timeout_seconds,
                        env=environment,
                    )
                except subprocess.TimeoutExpired:
                    raise ImageClientError(
                        "Codex image generation timed out", retryable=True
                    ) from None
                except OSError:
                    raise ImageClientError(
                        "Codex image generation failed", retryable=True
                    ) from None
                if process.returncode != 0:
                    detail = (process.stderr or "").strip().splitlines()
                    suffix = f": {detail[-1][:240]}" if detail else ""
                    raise ImageClientError(f"Codex image generation failed{suffix}")
                path = self._newest_valid_image(search_roots)
            else:
                path = self._wait_for_generated_image(
                    command,
                    instruction,
                    environment,
                    search_roots,
                )
            if path is None:
                raise ImageClientError("Codex returned no saved image")
            data = self._read_generated_image(path, search_roots)
            if data is None:
                raise ImageClientError("Codex returned no saved image")
            if not data or len(data) > MAX_IMAGE_BYTES:
                raise ImageClientError("Codex generated an invalid image size")
            mime = content_type(data)
            if mime not in {"image/png", "image/jpeg"}:
                raise ImageClientError("Codex generated an unsupported image type")
            width, height = dimensions(data)
            if not aspect_ratio_matches(width, height):
                raise ImageClientError(
                    "Generated image must preserve portrait 2:3 aspect ratio; "
                    f"received {width}x{height}"
                )
            return GeneratedImage(
                data=data,
                content_type=mime,
                width=width,
                height=height,
                request_id="",
                model=self.settings.model,
                request_size=self.settings.size,
            )

    @staticmethod
    def _isolated_codex_home(root: Path) -> Path:
        source_home = Path(
            os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
        ).expanduser()
        isolated = root / "codex-home"
        isolated.mkdir()
        for filename in ("auth.json", "config.toml"):
            source = source_home / filename
            if source.is_file():
                (isolated / filename).symlink_to(source)
        return isolated

    @staticmethod
    def _newest_valid_image(roots: tuple[Path, ...]) -> Path | None:
        candidates: list[tuple[int, Path]] = []
        for root in roots:
            try:
                for path in root.rglob("*"):
                    try:
                        if (
                            not path.is_file()
                            or path.suffix.lower() not in {".png", ".jpg", ".jpeg"}
                        ):
                            continue
                        modified_ns = path.stat().st_mtime_ns
                    except OSError:
                        continue
                    candidates.append((modified_ns, path))
            except OSError:
                continue
        if not candidates:
            return None
        for _modified_ns, candidate in sorted(
            candidates,
            key=lambda item: item[0],
            reverse=True,
        ):
            try:
                data = candidate.read_bytes()
            except OSError:
                continue
            if not data or len(data) > MAX_IMAGE_BYTES:
                continue
            if content_type(data) not in {"image/png", "image/jpeg"}:
                continue
            if aspect_ratio_matches(*dimensions(data)):
                return candidate
        return None

    @classmethod
    def _read_generated_image(
        cls,
        candidate: Path,
        roots: tuple[Path, ...],
    ) -> bytes | None:
        for _attempt in range(3):
            try:
                return candidate.read_bytes()
            except OSError:
                candidate = cls._newest_valid_image(roots)
                if candidate is None:
                    return None
        return None

    def _wait_for_generated_image(
        self,
        command: list[str],
        instruction: str,
        environment: dict[str, str],
        roots: tuple[Path, ...],
    ) -> Path:
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout_file, \
                tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr_file:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=True,
                    encoding="utf-8",
                    env=environment,
                    start_new_session=True,
                )
            except OSError:
                raise ImageClientError(
                    "Codex image generation failed", retryable=True
                ) from None
            try:
                try:
                    assert process.stdin is not None
                    process.stdin.write(instruction)
                    process.stdin.close()
                except OSError:
                    raise ImageClientError(
                        "Could not send the image prompt to Codex"
                    ) from None
                deadline = time.monotonic() + self.settings.timeout_seconds
                last_signature: tuple[Path, int, int] | None = None
                while time.monotonic() < deadline:
                    candidate = self._newest_valid_image(roots)
                    if candidate is not None:
                        try:
                            stat_result = candidate.stat()
                        except OSError:
                            stat_result = None
                        if stat_result is not None:
                            signature = (
                                candidate,
                                stat_result.st_size,
                                stat_result.st_mtime_ns,
                            )
                            if signature == last_signature and stat_result.st_size > 0:
                                return candidate
                            last_signature = signature

                    return_code = process.poll()
                    if return_code is not None:
                        candidate = self._newest_valid_image(roots)
                        if candidate is not None:
                            return candidate
                        stderr_file.seek(0)
                        detail = stderr_file.read().strip().splitlines()
                        suffix = f": {detail[-1][:240]}" if detail else ""
                        raise ImageClientError(
                            f"Codex image generation failed (exit {return_code}){suffix}"
                        )
                    time.sleep(0.25)

                raise ImageClientError(
                    "Codex image generation timed out", retryable=True
                )
            finally:
                self._terminate_process_group(process)

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


def build_image_generation_client(settings: CoverSettings):
    """Select the image transport without exposing it to CoverService."""
    if settings.provider == "codex":
        return CodexImageGenerationClient(settings)
    return ImageGenerationClient(settings)
