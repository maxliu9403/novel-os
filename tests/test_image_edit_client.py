"""Reference-bound edits use real image bytes; no real provider calls."""

import base64
import hashlib
import io
import json
import urllib.error
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest
from PIL import Image

from core import codex_image_edit, image_client, usage_tracking
from core.image_capabilities import image_model_capabilities
from core.image_client import CodexImageGenerationClient, ImageClientError, ImageGenerationClient
from core.studio_settings import CoverSettings


def picture(color="navy", format="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (20, 30), color).save(stream, format=format)
    return stream.getvalue()


@pytest.fixture
def reference(tmp_path):
    path = tmp_path / "private-title.png"
    path.write_bytes(picture())
    return path


def settings(**kwargs):
    return CoverSettings(base_url="https://example.invalid/v1", api_key="test-secret", size="1024x1536", **kwargs)


class Response:
    headers = {"x-request-id": "edit-request-123"}

    def __init__(self, data=None, **fields):
        self.data = json.dumps({"data": [{"b64_json": base64.b64encode(data or picture("orange")).decode()}], **fields}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self, _limit):
        return self.data


@pytest.mark.parametrize("model", ["gpt-image-2", "gpt-image-2.5-sunburst", "gpt-image-2.5-flare"])
@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_api_edit_posts_original_bytes_to_edits_and_preserves_model_and_provenance(reference, model, format, mime):
    reference.write_bytes(picture(format=format))
    original = reference.read_bytes()
    calls = []

    def opener(request, **_kwargs):
        calls.append(request)
        assert request.full_url == "https://example.invalid/v1/images/edits"
        assert request.get_header("Authorization") == "Bearer test-secret"
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {request.get_header('Content-type')}\r\n\r\n".encode() + request.data)
        fields = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
        assert set(fields) == {"model", "prompt", "n", "size", "quality", "output_format", "image[]"}
        assert fields["image[]"].get_payload(decode=True) == original
        assert fields["image[]"].get_content_type() == mime
        assert "private-title" not in fields["image[]"].get_filename()
        assert fields["prompt"].get_payload(decode=True).decode() == "只调整光线，保留人物与构图。"
        for key, value in {"model": model, "n": "1", "size": "1024x1536", "quality": "high", "output_format": "jpeg"}.items():
            assert fields[key].get_payload(decode=True).decode() == value
        return Response(model=model)

    result = ImageGenerationClient(settings(model=model), opener=opener).edit(
        "只调整光线，保留人物与构图。", reference, expected_reference_sha256=hashlib.sha256(original).hexdigest())
    assert len(calls) == 1
    assert reference.read_bytes() == original
    assert result.operation == "edit"
    assert result.reference_image_sha256 == hashlib.sha256(original).hexdigest()
    assert result.reference_transport == "images_edits_multipart"
    assert result.request_id == "edit-request-123"
    assert result.model == model and result.observed_model == model and result.model_verified


@pytest.mark.parametrize("failure", [404, 405, 429, 500, "timeout"])
def test_edits_never_retry_or_fall_back_to_generation_even_with_retrying_client(reference, failure):
    calls = []

    def opener(request, **_kwargs):
        calls.append(request.full_url)
        if failure == "timeout":
            raise TimeoutError()
        raise urllib.error.HTTPError(request.full_url, failure, "edit unsupported", {}, None)

    client = ImageGenerationClient(settings(), max_attempts=3, opener=opener,
                                   sleeper=lambda _seconds: pytest.fail("edit must not retry"))
    with pytest.raises(ImageClientError):
        client.edit("Improve lighting", reference)
    assert calls == ["https://example.invalid/v1/images/edits"]


def test_api_edit_records_usage_once_and_keeps_unreported_model_unknown(reference, monkeypatch):
    events = []
    monkeypatch.setattr(usage_tracking, "record_usage", lambda **event: events.append(event))
    usage = {"input_tokens": 50, "output_tokens": 20, "total_tokens": 70}
    result = ImageGenerationClient(settings(), opener=lambda *_a, **_k: Response(usage=usage)).edit("Improve lighting", reference)
    assert result.observed_model == "" and not result.model_verified
    assert [event["status"] for event in events] == ["started", "completed"]
    assert events[-1]["usage"] == usage and events[-1]["image_count"] == 1
    assert events[0]["request_id"] == events[-1]["request_id"]


@pytest.mark.parametrize("case", ["missing", "directory", "symlink", "empty", "oversize", "not_image", "corrupt", "sha_changed", "blank_prompt"])
def test_reference_preflight_fails_before_network_or_usage(reference, tmp_path, monkeypatch, case):
    monkeypatch.setattr(usage_tracking, "record_usage", lambda **_event: pytest.fail("preflight must not record a provider call"))
    path = reference
    prompt = "Change lighting"
    expected = None
    if case == "missing": path = tmp_path / "missing.png"
    elif case == "directory": path = tmp_path
    elif case == "symlink":
        path = tmp_path / "alias.png"
        path.symlink_to(reference)
    elif case == "empty": reference.write_bytes(b"")
    elif case == "oversize": monkeypatch.setattr(image_client, "MAX_IMAGE_BYTES", 10)
    elif case == "not_image": reference.write_bytes(b"private story text")
    elif case == "corrupt": reference.write_bytes(picture()[:40])
    elif case == "sha_changed": expected = "a" * 64
    elif case == "blank_prompt": prompt = " "
    client = ImageGenerationClient(settings(), opener=lambda *_a, **_k: pytest.fail("must not send"))
    with pytest.raises(ImageClientError):
        client.edit(prompt, path, expected_reference_sha256=expected)


def test_unsupported_provider_is_not_treated_as_text_to_image(reference):
    client = ImageGenerationClient(settings(provider="anthropic"), opener=lambda *_a, **_k: pytest.fail("must not send"))
    with pytest.raises(ImageClientError, match="does not support reference-image edits"):
        client.edit("Change lighting", reference)
    assert image_model_capabilities("anthropic")["reference_edit_transport"] is None


def test_api_edit_rejects_model_mismatch_and_unchanged_input(reference):
    client = ImageGenerationClient(settings(), opener=lambda *_a, **_k: Response(model="different-image-model"))
    with pytest.raises(ImageClientError, match="model mismatch"):
        client.edit("Change lighting", reference)
    client = ImageGenerationClient(settings(), opener=lambda *_a, **_k: Response(data=reference.read_bytes()))
    with pytest.raises(ImageClientError, match="unchanged reference"):
        client.edit("Change lighting", reference)


def codex_client(monkeypatch, tmp_path):
    monkeypatch.setattr(image_client.shutil, "which", lambda _name: "/fake/codex")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    return CodexImageGenerationClient(
        settings(provider="codex", model="codex-image-auto"),
        runner=lambda *_args, **_kwargs: pytest.fail("edits must never fall back to CLI generation"))


@pytest.mark.parametrize("large_reference", [False, True])
def test_codex_edit_sends_original_bytes_without_local_path_or_resize(reference, monkeypatch, tmp_path, large_reference):
    if large_reference:
        stream = io.BytesIO()
        Image.new("RGB", (2048, 3072), "navy").save(stream, format="PNG")
        reference.write_bytes(stream.getvalue())
    original = reference.read_bytes()
    calls = []

    def edit_transport(**kwargs):
        calls.append(kwargs)
        root = kwargs["root"]
        assert kwargs["cli"] == "/fake/codex"
        assert kwargs["reference_data"] == original
        assert kwargs["reference_content_type"] == "image/png"
        assert not list(root.rglob("*.png"))
        assert Path(kwargs["environment"]["CODEX_HOME"]).is_relative_to(root)
        assert str(reference) not in kwargs["instruction"]
        assert "num_last_images_to_include=1" in kwargs["instruction"]
        assert "referenced_image_paths=" not in kwargs["instruction"]
        assert "exactly once" in kwargs["instruction"]
        assert "Improve only the lighting" in kwargs["instruction"]
        assert "not a new text-to-image generation" in kwargs["instruction"]
        output = root / "cover.png"
        output.write_bytes(picture("orange"))
        return output

    monkeypatch.setattr(codex_image_edit, "run_image_edit", edit_transport)
    result = codex_client(monkeypatch, tmp_path).edit(
        "Improve only the lighting", reference, expected_reference_sha256=hashlib.sha256(original).hexdigest())
    assert len(calls) == 1 and reference.read_bytes() == original
    assert not calls[0]["root"].exists()
    assert result.operation == "edit" and result.reference_transport == "codex_image_attachment"
    assert result.reference_image_sha256 == hashlib.sha256(original).hexdigest()
    assert result.observed_model == "" and not result.model_verified


@pytest.mark.parametrize("mode", ["no_output", "unrelated_output", "copy_input", "failure"])
def test_codex_edit_never_returns_the_reference_as_a_new_result_or_retries(reference, monkeypatch, tmp_path, mode):
    calls = []

    def edit_transport(**kwargs):
        calls.append(kwargs)
        output = kwargs["root"] / "cover.png"
        if mode == "copy_input":
            output.write_bytes(kwargs["reference_data"])
        elif mode == "unrelated_output":
            (kwargs["root"] / "unrelated.png").write_bytes(picture("orange"))
        elif mode == "failure":
            raise ImageClientError("editing unavailable")
        return output

    monkeypatch.setattr(codex_image_edit, "run_image_edit", edit_transport)
    with pytest.raises(ImageClientError):
        codex_client(monkeypatch, tmp_path).edit("Change lighting", reference)
    assert len(calls) == 1
    assert reference.read_bytes() == picture()


@pytest.mark.parametrize("case", ["missing", "sha_changed"])
def test_codex_invalid_reference_does_not_start_a_provider_or_usage(tmp_path, monkeypatch, reference, case):
    monkeypatch.setattr(codex_image_edit, "run_image_edit", lambda **_kwargs: pytest.fail("must not launch"))
    monkeypatch.setattr(usage_tracking, "record_usage", lambda **_kwargs: pytest.fail("must not record usage"))
    client = codex_client(monkeypatch, tmp_path)
    with pytest.raises(ImageClientError):
        client.edit("Change lighting", tmp_path / "missing.png" if case == "missing" else reference,
                    expected_reference_sha256="a" * 64)


def test_generation_result_remains_backward_compatible():
    generated = image_client.GeneratedImage(picture(), "image/png", 20, 30, "", "gpt-image-2")
    assert generated.operation == "generate"
    assert generated.reference_image_sha256 == generated.reference_transport == ""

