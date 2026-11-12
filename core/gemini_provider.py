"""Native Gemini Developer API transport and model capabilities.

No OpenAI protocol translation, prompt rewriting or automatic failure retry.
The SDK owns pagination/stream decoding; the application owns output acceptance.
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"


class GeminiError(ValueError):
    def __init__(self, message, *, finish_reason=None):
        super().__init__(message)
        self.finish_reason = finish_reason


def is_continuation(request) -> bool:
    """A native continuation extends one generation with cumulative SDK usage."""
    try:
        payload = json.loads(request.content)
        return bool(payload.get("continuationToken") or payload.get("continuation_token"))
    except (ValueError, AttributeError, TypeError):
        return False


def normalize_base_url(value: str) -> str:
    url = (value or DEFAULT_BASE_URL).strip().rstrip("/")
    parts = urlsplit(url)
    if (parts.scheme not in {"https", "http"} or not parts.netloc
            or parts.username or parts.password or parts.query or parts.fragment):
        raise GeminiError("Gemini API 地址必须是无密钥、查询参数的 HTTP(S) 基础地址")
    # The native SDK appends the API version, including for a compatible gateway.
    if url.endswith("/v1beta"):
        url = url[:-7]
    if url.endswith(("/v1", "/openai")):
        raise GeminiError("请选择 Gemini 原生 API 根地址，不是 /v1 或 /openai 兼容接口")
    return url


def reasoning_levels(model: str) -> list[str]:
    name = model.removeprefix("models/").lower()
    if name.startswith("gemini-3-pro"):
        return ["low", "high"]
    if name.startswith(("gemini-2.5-", "gemini-3", "gemini-4")):
        return ["low", "medium", "high"]
    return []  # Unknown model: use its default, not invented capabilities.


def thinking_options(model: str, effort: str, max_tokens: int | None = None) -> dict:
    if not effort:
        return {}
    if effort not in reasoning_levels(model):
        raise GeminiError(f"Gemini 模型 {model} 不支持推理档位 {effort}，请使用模型默认或受支持的档位")
    if model.removeprefix("models/").startswith("gemini-2.5-"):
        # Same budgets as Google's documented reasoning-effort mapping.
        budget = {"low": 1024, "medium": 8192, "high": 24576}[effort]
        if max_tokens is not None and max_tokens <= budget:
            raise GeminiError(f"Gemini 2.5 的 {effort} 推理预算为 {budget} tokens；输出上限须更大并为正文留出空间，或使用模型默认")
        return {"thinking_budget": budget}
    return {"thinking_level": effort.upper()}


def create_client(api_key: str, base_url: str = "", timeout_seconds: float | None = None):
    from google import genai
    from google.genai import types
    return genai.Client(
        vertexai=False, api_key=api_key,
        http_options=types.HttpOptions(
            base_url=normalize_base_url(base_url), api_version="v1beta",
            timeout=int((timeout_seconds or 300) * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def discover_models(api_key: str, base_url: str, timeout_seconds: float) -> dict[str, dict]:
    client = create_client(api_key, base_url, timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
    details = {}
    try:
        for index, item in enumerate(client.models.list(config={"page_size": 100})):
            if time.monotonic() >= deadline:
                raise TimeoutError("Gemini model discovery timed out")
            if index >= 2000:
                raise GeminiError("Gemini model catalog exceeded the model limit")
            name = str(item.name or "").removeprefix("models/")
            actions = item.supported_actions or []
            if "generateContent" not in actions or not name:
                continue
            if any(part in name.lower() for part in ("image", "tts", "audio", "live")):
                continue
            details[name] = {
                "id": name, "name": item.display_name or name,
                "context_window": item.input_token_limit,
                "max_output_tokens": item.output_token_limit,
                "reasoning": {"supported_levels": reasoning_levels(name)},
            }
        return details
    finally:
        client.close()


def complete(client, *, model: str, system: str, user: str, max_tokens: int,
             effort: str, images: tuple[bytes, ...] = (), check_cancelled,
             diagnostic, observe, set_stream, preserve_received=None) -> str:
    from google.genai import types
    from google.genai.errors import APIError
    thinking = thinking_options(model, effort, max_tokens)
    parts = [types.Part.from_text(text=user)]
    for data in images:
        if data.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            raise GeminiError("Gemini 图片输入仅支持有效 PNG、JPEG 或 WebP")
        parts.append(types.Part.from_bytes(data=data, mime_type=mime))
    config = types.GenerateContentConfig(
        system_instruction=system, max_output_tokens=max_tokens,
        thinking_config=types.ThinkingConfig(include_thoughts=False, **thinking) if thinking else None,
        # Continuations are part of the same bounded generation, not retries.
        automatic_continuation=True,
    )
    stream = None
    output = []
    received = [] if preserve_received is not None else None
    chars = 0
    finish = ""
    last_signal = 0.0
    try:
        check_cancelled()
        stream = client.models.generate_content_stream(
            model=model, contents=[types.Content(role="user", parts=parts)], config=config)
        set_stream(stream)
        for chunk in stream:
            if received is not None:
                # Keep every received public text part before cancellation,
                # safety or completion checks; thoughts are never manuscript.
                received.extend(part.text for candidate in (chunk.candidates or [])[:1]
                                for part in (candidate.content.parts if candidate.content else []) or []
                                if part.text and not part.thought)
            check_cancelled()
            if chunk.usage_metadata is not None:
                observe(chunk.usage_metadata, provider="gemini", replace=True,
                        observed_model=chunk.model_version)
            feedback = chunk.prompt_feedback
            if feedback and feedback.block_reason and str(getattr(feedback.block_reason, "value", feedback.block_reason)) != "BLOCK_REASON_UNSPECIFIED":
                raise GeminiError("Gemini 拒绝了此请求（prompt blocked）；未保存为完整输出")
            for candidate in (chunk.candidates or [])[:1]:
                if candidate.finish_reason:
                    finish = getattr(candidate.finish_reason, "value", str(candidate.finish_reason))
                for part in (candidate.content.parts if candidate.content else []) or []:
                    if part.text and not part.thought:
                        output.append(part.text)
                        chars += len(part.text)
            now = time.monotonic()
            if now - last_signal >= 1:
                diagnostic("content.progress" if chars else "provider.stream_chunk", model_output_chars=chars)
                last_signal = now
        check_cancelled()
        if finish != "STOP":
            raise GeminiError(f"Gemini 输出未完整结束（{finish or 'stream interrupted'}）；请检查输出上限或服务状态，未接受部分结果",
                              finish_reason=finish or "unknown")
        text = "".join(output)
        if not text.strip():
            raise GeminiError("Gemini 返回空正文；未接受为完成结果")
        diagnostic("content.progress", model_output_chars=chars)
        return text
    except APIError as error:
        # SDK messages may include request details. Persist only the status.
        raise GeminiError(f"Gemini API 请求失败（HTTP {error.code}）；请检查模型权限、配额与 API 地址") from None
    finally:
        try:
            if preserve_received is not None:
                preserve_received("".join(received))
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            set_stream(None)
