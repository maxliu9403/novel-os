"""OpenAI Chat Completions adapter for the supplied gemini-web2api gateway.

Its model IDs encode thinking; token limits are ignored upstream. This profile
does not pretend that a web gateway inherits the official Gemini context limit.
"""
from __future__ import annotations

import base64
import time
from typing import Any
from urllib.parse import urlsplit

PROVIDER = "gemini_openai"
# Match the reviewed gateway's inline cap, INCLUDING our envelope reserve.
# Actual text therefore stays below 128,000 bytes. This web transport's byte
# limit is independent of the model's advertised token window.
INPUT_LIMIT_BYTES = 128_000
ATTACHMENT_LIMIT_BYTES = 156_000
CONTEXT_POLICY = "bounded-attachment-v1"
ENVELOPE_RESERVE_BYTES = 1024
OUTPUT_LIMIT_BYTES = 2_000_000


class GatewayError(ValueError):
    pass


class GatewayInputLimitError(GatewayError):
    """Trusted local metadata, not an upstream response body."""
    def __init__(self, actual_bytes: int, limit_bytes: int):
        self.actual_bytes, self.limit_bytes = actual_bytes, limit_bytes
        super().__init__(
            f"Gemini 网关输入超限：本次 {actual_bytes:,} 字节（含封装预留），"
            f"上限 {limit_bytes:,} 字节。未发送、未截断。"
            "请减少重复上下文或选择支持更大输入的连接；原样重试不会缩小输入。")


def normalize_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise GatewayError("请填写不含 Token 的 HTTP(S) 网关地址，Token 使用独立密钥框")
    if not parsed.path:
        url += "/v1"
    elif not parsed.path.endswith("/v1"):
        raise GatewayError("OpenAI 网关基础地址须以 /v1 结尾，不要填写 /chat/completions 或 /v1beta")
    return url


def is_text_model(model: str) -> bool:
    return model.startswith("gemini-") and not any(
        model == f"gemini-{kind}" or model.startswith(f"gemini-{kind}-")
        for kind in ("image", "music", "video", "canvas")
    )


def validate_model(model: str, effort: str = "") -> None:
    if not is_text_model(model):
        raise GatewayError("请选择模型列表中的 Gemini 文本型号，媒体工具不能用于小说写作")
    if effort:
        raise GatewayError("此网关通过完整模型 ID 选择思考档位；请选择 -thinking 或 -thinking-medium 型号，并清空独立推理档位")


def input_bytes(system: str, user: str) -> int:
    return len(system.encode("utf-8")) + len(user.encode("utf-8")) + ENVELOPE_RESERVE_BYTES


def attachment_capacity(client) -> int:
    """Negotiate only with the configured, authenticated gateway. No model call.

    Old gateways remain inline-only. The companion policy header pins the
    safety contract for the actual request even if runtime settings change.
    Never infer capacity from a model name or an advertised token window.
    """
    from openai import APIError
    try:
        value = client.with_options(timeout=5.0, max_retries=0).get(
            "/context-capabilities", cast_to=dict[str, Any])
    except (APIError, ValueError):
        return INPUT_LIMIT_BYTES
    if not isinstance(value, dict):
        return INPUT_LIMIT_BYTES
    inline, total = value.get("inline_limit_bytes"), value.get("input_limit_bytes")
    if (value.get("schema") != "gw2a.context.v1" or value.get("unit") != "utf8_bytes"
            or value.get("policy") != CONTEXT_POLICY or value.get("text_attachment") is not True
            or type(inline) is not int or type(total) is not int
            or not 0 < inline <= INPUT_LIMIT_BYTES < total <= ATTACHMENT_LIMIT_BYTES):
        return INPUT_LIMIT_BYTES
    return total


def complete(client, *, model, system, user, effort, images=(), check_cancelled,
             diagnostic, observe, set_stream):
    from openai import APIError
    validate_model(model, effort)
    size = input_bytes(system, user)
    limit = INPUT_LIMIT_BYTES
    request_options = {}
    if size > INPUT_LIMIT_BYTES:
        check_cancelled()
        if not images:
            limit = attachment_capacity(client)
            check_cancelled()
        if size > limit:
            raise GatewayInputLimitError(size, limit)
        request_options["extra_headers"] = {"X-GW2A-Context-Policy": CONTEXT_POLICY}
    content = [{"type": "text", "text": user}]
    for data in images:
        mime = ("image/jpeg" if data.startswith(b"\xff\xd8\xff") else
                "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else
                "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else "")
        if not mime:
            raise GatewayError("图片必须为 PNG、JPEG 或 WebP")
        content.append({"type": "image_url", "image_url": {
            "url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}})
    stream = None
    output = []
    output_bytes = 0
    chars = 0
    finish = ""
    reported = ""
    last_signal = 0.0
    content_reported = False
    try:
        check_cancelled()
        stream = client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": system},
                                   {"role": "user", "content": content if images else user}],
            stream=True, stream_options={"include_usage": True},
            **request_options,
        )
        set_stream(stream)
        for chunk in stream:
            check_cancelled()
            if chunk.model:
                reported = chunk.model
            if chunk.usage is not None:
                observe(chunk.usage, provider=PROVIDER, replace=True, observed_model=reported)
            for choice in (chunk.choices or [])[:1]:
                if choice.finish_reason:
                    finish = choice.finish_reason
                text = choice.delta.content or ""
                # reasoning_content is a signal, never manuscript prose.
                reasoning = getattr(choice.delta, "reasoning_content", None)
                if text:
                    output_bytes += len(text.encode("utf-8"))
                    if output_bytes > OUTPUT_LIMIT_BYTES:
                        raise GatewayError("网关输出超过本地安全上限；未保存部分结果，远端可能仍在生成")
                    output.append(text)
                    chars += len(text)
                now = time.monotonic()
                if (text and not content_reported) or (now - last_signal >= 1 and (text or reasoning)):
                    diagnostic("content.progress" if text else "provider.stream_chunk",
                               model_output_chars=chars)
                    content_reported = content_reported or bool(text)
                    last_signal = now
        check_cancelled()
        if finish != "stop" or not "".join(output).strip():
            raise GatewayError(f"网关输出未完整完成（{finish or '连接中断'}）或正文为空；未接受部分结果")
        diagnostic("content.progress", model_output_chars=chars)
        # This gateway echoes the requested/canonical ID, not upstream identity.
        return "".join(output), {"reported_model": reported, "finish_reason": finish,
            "actual_model_verified": False, "input_bytes": size,
            "input_limit_bytes": limit, "context_policy": CONTEXT_POLICY if request_options else "inline",
            "output_limit_enforced_by_provider": False,
            "usage_source": "gateway_estimate"}
    except APIError as error:
        code = getattr(error, "code", None)
        hint = {"context_length_exceeded": "网关拒绝了输入长度",
                "model_downgraded": "上游模型被降级，网关已拒绝此次响应",
                "ip_slot_full": "网关当前并发已满"}.get(code, "请检查 Token、型号权限、网关配额和日志")
        raise GatewayError(f"Gemini 网关请求失败（HTTP {getattr(error, 'status_code', None) or 'stream'}）；{hint}") from None
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        set_stream(None)
