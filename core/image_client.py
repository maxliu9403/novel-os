"""OpenAI-compatible image generation client for novel covers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
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
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

try:
    from .image_binary import aspect_ratio_matches, content_type, dimensions
    from .image_capabilities import image_edit_transport, image_models_match, validate_image_model
    from .studio_settings import CoverSettings
except ImportError:  # pragma: no cover - legacy top-level core imports
    from image_binary import aspect_ratio_matches, content_type, dimensions
    from image_capabilities import image_edit_transport, image_models_match, validate_image_model
    from studio_settings import CoverSettings

MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_BYTES = 40 * 1024 * 1024


@contextmanager
def _image_usage(settings):
    """One image attempt, plus reported Codex host-text usage if available."""
    try:
        from core.usage_tracking import record_usage
    except ModuleNotFoundError as exc:
        if exc.name != "core":
            raise
        from usage_tracking import record_usage
    attempt_id = uuid.uuid4().hex
    observation = {"usage": None, "observed_model": None, "image_count": None,
                   "text_usage": None, "text_usage_incomplete_fields": []}
    status = "failed"
    record_usage(provider=settings.provider, model=settings.model, request_id=attempt_id,
                 kind="image", status="started")
    try:
        yield observation
        status = "completed"
    finally:
        record_usage(provider=settings.provider, model=settings.model, request_id=attempt_id,
                     kind="image", status=status, usage=observation["usage"],
                     image_count=observation["image_count"], observed_model=observation["observed_model"])
        if observation["text_usage"] is not None:
            # These are the agent's text tokens, never the image model's tokens.
            record_usage(provider="codex", model="unknown", request_id=attempt_id + ":host-text",
                         kind="text", usage=observation["text_usage"],
                         usage_incomplete_fields=observation["text_usage_incomplete_fields"])


def _capture_codex_usage(output, observation):
    if observation is None or not isinstance(output, str):
        return
    try:
        from .llm_diagnostics import CLIDiagnostics
    except ImportError:  # Standalone imports used by the CLI entry points.
        from llm_diagnostics import CLIDiagnostics
    # Reuse the CLI observer's turn boundaries and cumulative-snapshot rules.
    # Each invocation receives the complete output, so polling cannot add the
    # same turn twice. Missing components retain explicit incompleteness.
    observer = CLIDiagnostics(lambda _event: None)
    observer.observe(output, None)
    report = observer.finish("cli.completed")
    if report["usage"]:
        observation["text_usage"] = report["usage"]
        observation["text_usage_incomplete_fields"] = report["usage_incomplete_fields"]


def _check_reference_sandbox_error(stderr: str) -> None:
    # A tool can fail while the hosting Codex process exits successfully or
    # continues reasoning. Match this specific runtime diagnostic, not generic
    # sandbox warnings, and never expose the private reference path.
    for line in stderr.casefold().splitlines():
        if "unable to read referenced image" in line and (
            "fs sandbox helper failed" in line or "bwrap:" in line
        ):
            raise ImageClientError(
                "Codex reference image sandbox read failed: 运行环境无法读取精修参考图，"
                "本次未保存新图片。请更新图片附件通道后重新提交；不会自动重试。"
            )


class _ReferenceReadDiagnostics:
    """Inspect stderr incrementally without moving the child's shared offset."""

    def __init__(self):
        self.offset = 0
        self.overlap = ""

    def check(self, stream):
        size = os.fstat(stream.fileno()).st_size
        while self.offset < size:
            chunk = os.pread(stream.fileno(), min(16384, size - self.offset), self.offset)
            if not chunk:
                break
            self.offset += len(chunk)
            text = self.overlap + chunk.decode("utf-8", errors="replace")
            _check_reference_sandbox_error(text)
            self.overlap = text[-4096:]


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
    # `model` remains the requested route for existing consumers. These fields
    # distinguish an actual provider claim from an unverified configured name.
    observed_model: str = ""
    model_verified: bool = False
    operation: str = "generate"
    reference_image_sha256: str = ""
    reference_transport: str = ""


@dataclass(frozen=True)
class _ImageReference:
    data: bytes
    content_type: str
    sha256: str


def _read_image_reference(image_path: str | Path, expected_sha256: str | None = None) -> _ImageReference:
    """Freeze validated image bytes before any provider request or usage charge."""
    from PIL import Image, UnidentifiedImageError

    try:
        path = Path(image_path)
        if path.is_symlink() or not path.is_file():
            raise ImageClientError("Reference image must be an existing regular file")
        with path.open("rb") as source:
            data = source.read(MAX_IMAGE_BYTES + 1)
    except (OSError, TypeError, ValueError):
        raise ImageClientError("Reference image could not be read") from None
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageClientError("Reference image is empty or exceeds the allowed size")
    mime = content_type(data)
    if mime not in {"image/png", "image/jpeg"}:
        raise ImageClientError("Reference image must be PNG or JPEG")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ImageClientError("Reference image is corrupt or unsafe to decode") from None
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ImageClientError("Reference image changed after the edit was approved")
    return _ImageReference(data, mime, digest)


def _edited_image(image: GeneratedImage, reference: _ImageReference, transport: str) -> GeneratedImage:
    if image.data == reference.data:
        raise ImageClientError("Image provider returned the unchanged reference image; no edited result was saved")
    return replace(image, operation="edit", reference_image_sha256=reference.sha256, reference_transport=transport)


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
        try:
            validate_image_model(settings.model, settings.provider)
        except ValueError as exc:
            raise ImageClientError(str(exc)) from None
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
                with _image_usage(self.settings) as observation:
                    return self._send(request, observation=observation)
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

    def edit(self, prompt: str, image_path: str | Path, *, expected_reference_sha256: str | None = None) -> GeneratedImage:
        """Send one reference-bound edit; failures never retry or fall back to generate."""
        if not prompt or not prompt.strip():
            raise ImageClientError("Cover edit prompt is required")
        transport = image_edit_transport(self.settings.provider)
        if transport != "images_edits_multipart":
            raise ImageClientError("This image provider does not support reference-image edits")
        reference = _read_image_reference(image_path, expected_reference_sha256)
        request = self._edit_request(prompt.strip(), reference)
        with _image_usage(self.settings) as observation:
            image = self._send(request, observation=observation)
            return _edited_image(image, reference, transport)

    def _edit_request(self, prompt: str, reference: _ImageReference) -> urllib.request.Request:
        # OpenAI Images edits accepts actual file bytes in multipart image[].
        # Compatible endpoints must implement this protocol; an unsupported edit
        # returns the provider error instead of silently invoking generations.
        boundary = "novel-os-edit-" + uuid.uuid4().hex
        fields = {"model": self.settings.model, "prompt": prompt, "n": "1",
                  "size": self.settings.size, "quality": self.settings.quality,
                  "output_format": self.settings.output_format}
        parts = []
        for name, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
        filename = "reference.png" if reference.content_type == "image/png" else "reference.jpg"
        parts.extend([
            f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="{filename}"\r\nContent-Type: {reference.content_type}\r\n\r\n'.encode("ascii"),
            reference.data, f"\r\n--{boundary}--\r\n".encode("ascii"),
        ])
        return urllib.request.Request(
            f"{self.settings.base_url.rstrip('/')}/images/edits", data=b"".join(parts),
            headers={"Authorization": f"Bearer {self.settings.api_key}",
                     "Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
            method="POST",
        )

    def _send(self, request: urllib.request.Request, *, observation=None) -> GeneratedImage:
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
        model_claims = [payload.get("model")] if isinstance(payload, dict) else []
        if isinstance(items, list):
            model_claims.extend(item.get("model") for item in items if isinstance(item, dict))
        observed_models = {value.strip() for value in model_claims if isinstance(value, str) and value.strip()}
        mismatched = any(not image_models_match(self.settings.model, model) for model in observed_models)
        observed_model = (
            sorted(observed_models)[-1]
            if observed_models and (len(observed_models) == 1 or not mismatched) else ""
        )
        if observation is not None:
            observation["usage"] = payload.get("usage") if isinstance(payload, dict) else None
            observation["observed_model"] = observed_model or None
            observation["image_count"] = len(items) if isinstance(items, list) else None
        if mismatched:
            raise ImageClientError("Image provider model mismatch: response does not match the requested image model")
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
            observed_model=observed_model,
            model_verified=bool(observed_model),
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
        try:
            validate_image_model(settings.model, "codex")
        except ValueError as exc:
            raise ImageClientError(str(exc)) from None
        self.settings = settings
        self.cli = cli
        self._run = runner

    def generate(self, prompt: str) -> GeneratedImage:
        if not prompt or not prompt.strip():
            raise ImageClientError("Cover prompt is required")
        with _image_usage(self.settings) as observation:
            return self._generate(prompt, observation)

    def edit(self, prompt: str, image_path: str | Path, *, expected_reference_sha256: str | None = None) -> GeneratedImage:
        if not prompt or not prompt.strip():
            raise ImageClientError("Cover edit prompt is required")
        reference = _read_image_reference(image_path, expected_reference_sha256)
        try:
            from .codex_image_edit import run_image_edit
        except ImportError:  # Standalone imports used by the CLI entry points.
            from codex_image_edit import run_image_edit

        with _image_usage(self.settings) as observation:
            with tempfile.TemporaryDirectory(prefix="novel-os-image-edit-") as directory:
                root = Path(directory)
                codex_home = self._isolated_codex_home(root)
                generated_root = codex_home / "generated_images"
                generated_root.mkdir(parents=True, exist_ok=True)
                instruction = (
                    "Use the built-in image generation tool exactly once, with its automatically selected image model. "
                    "Edit the attached existing portrait book cover using the changes below. "
                    "This is an image-to-image edit, not a new text-to-image generation. "
                    "The sole image attached to the first user message is the original reference. "
                    "Pass num_last_images_to_include=1 to the image tool to edit that conversation image. "
                    "Do not provide referenced_image_paths, open local image files or call view_image: "
                    "the original is already attached and visible in this conversation. "
                    "Do not omit that attached reference or substitute a description of it. "
                    "Preserve the existing composition, characters, typography and other details except "
                    "where the edit prompt explicitly requests a change. Never copy the original as the result. "
                    "If reference-image editing is unavailable, stop and report the error; do not generate a replacement. "
                    f"Target {self.settings.size}, {self.settings.quality} quality, and "
                    f"{self.settings.output_format.upper()} output. The image tool saves the edited result; "
                    "return its saved file path. Do not use shell or code to synthesize the image.\n\n"
                    f"COVER EDIT PROMPT\n{prompt.strip()}"
                )
                path = run_image_edit(
                    cli=self.cli, instruction=instruction,
                    reference_data=reference.data, reference_content_type=reference.content_type,
                    root=root, environment={**os.environ, "CODEX_HOME": str(codex_home), "NO_COLOR": "1"},
                    timeout_seconds=self.settings.timeout_seconds, observation=observation,
                )
                image = self._generated_result(path, (root, generated_root), observation, discover_fallback=False)
                return _edited_image(image, reference, "codex_image_attachment")

    def _generate(self, prompt, observation) -> GeneratedImage:
        with tempfile.TemporaryDirectory(prefix="novel-os-image-") as directory:
            root = Path(directory)
            codex_home = self._isolated_codex_home(root)
            generated_root = codex_home / "generated_images"
            generated_root.mkdir(parents=True, exist_ok=True)
            message_path = root / "result.txt"
            output_name = f"cover.{self.settings.output_format.replace('jpeg', 'jpg')}"
            instruction = (
                "Use the built-in image generation tool exactly once, with its automatically selected image model. "
                "Generate an original portrait book cover from the prompt below. "
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
                "--json",
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
                _capture_codex_usage(process.stdout, observation)
                _check_reference_sandbox_error(process.stderr or "")
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
                    observation=observation,
                )
            return self._generated_result(path, search_roots, observation)

    def _generated_result(self, path, search_roots, observation, *, discover_fallback=True) -> GeneratedImage:
        if path is None:
            raise ImageClientError("Codex returned no saved image")
        if discover_fallback:
            data = self._read_generated_image(path, search_roots)
        else:
            # Edits are bound to the exact successful tool receipt. A missing
            # receipt file must never select another file from the directory.
            try:
                data = path.read_bytes()
            except OSError:
                data = None
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
        observation["image_count"] = 1
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
        *, observation=None,
    ) -> Path:
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout_file, \
                tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr_file:
            diagnostics = _ReferenceReadDiagnostics()
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
                    # pread leaves the child process's shared file offset alone.
                    # Seeking this TemporaryFile during execution could overwrite
                    # stderr still being written by Codex.
                    diagnostics.check(stderr_file)
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
                        diagnostics.check(stderr_file)
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
                stdout_file.seek(0)
                _capture_codex_usage(stdout_file.read(), observation)
                # Catch diagnostics written between our last poll and process
                # exit, including a successful exit after a failed tool call.
                diagnostics.check(stderr_file)

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
    # CoverService reserves one image call per attempt, including failed calls.
    # Retrying an ambiguous timeout here could silently spend unapproved images.
    return ImageGenerationClient(settings, max_attempts=1)
