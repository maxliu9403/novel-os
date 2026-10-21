"""Title suggestions and deterministic metadata updates for a confirmed outline."""
from __future__ import annotations

import json
import re
import time
import unicodedata

from .workshop_diagnostics import diagnostic_error_category, make_diagnostic_callback


def clean_title(value: str) -> str:
    if not isinstance(value, str) or any(unicodedata.category(c) in {"Cc", "Zl", "Zp"} for c in value):
        raise ValueError("书名必须是单行文字，不能包含控制字符。")
    value = value.strip()
    if not value or len(value) > 500:
        raise ValueError("请输入 1–500 个字符的书名。")
    return value


def rename_prompt(prompt: str, title: str) -> str:
    title = clean_title(title)
    boundary = re.search(r"^##\s", prompt, re.MULTILINE)
    end = boundary.start() if boundary else len(prompt)
    header, body = prompt[:end], prompt[end:]
    pattern = re.compile(r"^[ \t]*(?:[-*]\s*)?(?:\*\*)?Title(?:\*\*)?\s*:[ \t]*(.+)$", re.MULTILINE | re.IGNORECASE)
    matches = list(pattern.finditer(header))
    if len(matches) != 1:
        raise ValueError("骨架顶部必须包含唯一的 Title 字段。")
    old_title = matches[0].group(1).strip()
    header = pattern.sub(lambda _: f"Title: {title}", header, count=1)
    # A generic '# Novel skeleton' heading is not the book title.
    header = re.sub(r"^#\s+(.+)$", lambda m: f"# {title}" if m.group(1).strip() == old_title else m.group(0),
                    header, count=1, flags=re.MULTILINE)
    updated = header + body
    pattern = re.compile(r"(COVER_HANDOFF_BEGIN\s*)(.*?)(\s*COVER_HANDOFF_END)", re.DOTALL)
    blocks = list(pattern.finditer(updated))
    if len(blocks) != 1:
        raise ValueError("骨架必须包含唯一的封面交接块。")
    raw = blocks[0].group(2).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    cover = json.loads(raw)
    if not isinstance(cover, dict):
        raise ValueError("封面交接格式无效。")
    cover["title"] = title
    updated = pattern.sub(lambda m: m.group(1) + json.dumps(cover, ensure_ascii=False, indent=2) + m.group(3), updated, count=1)
    from core.cover_handoff import parse_cover_handoff
    parse_cover_handoff(updated)
    return updated


def complete_titles(session: dict, rules: str) -> tuple[list[dict], dict]:
    from core import provider_settings
    from core.llm_client import LLMClient

    route = provider_settings.resolve_text_route("workshop")
    timeout = float(session["turn_timeout_seconds"])
    deadline = time.monotonic() + timeout
    system = rules + """
Return ONLY JSON: {"candidates":[{"title":"Candidate title","reason":"简短中文推荐理由"}]}.
Return five distinct candidates (3–8 allowed). This is a naming task, not a story rewrite.
Use the confirmed skeleton, original brief and all naming requests. The latest author
requirements take precedence. Title language follows the manuscript unless the author
requests otherwise. Give varied, story-specific options and accurate emotional promises.
Each request's current_title is the author's selected/edited draft for instructions such
as 'shorten this title'; it is context, not a final approved title.
Do not claim a title is legally unique or that popularity was researched. Never pick the
final title for the author. Do not output commands, tools, private reasoning or new plot.
"""
    context = {"confirmed_skeleton": session["prompt_md"], "author_brief": session["input"],
               "naming_history": session["naming"]["requests"]}
    user = json.dumps(context, ensure_ascii=False)
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Title request exceeded its configured deadline")
        diagnostic = make_diagnostic_callback(session, attempt=attempt+1, operation="titles",
                                              route={**route, "timeout_seconds": remaining})
        if diagnostic is not None:
            diagnostic({"event": "attempt.start", "timeout_seconds": remaining})
        try:
            client = LLMClient(provider=route["provider"], model=route["model"] or None,
                               base_url=route["base_url"] or None, api_key=route["api_key"] or None,
                               max_tokens=route["max_tokens"], timeout_seconds=remaining,
                               reasoning_effort=route.get("reasoning_effort") or None,
                               **({"cancellation_event": session["_cancellation_event"]} if session.get("_cancellation_event") is not None else {}),
                               **({"diagnostic_callback": diagnostic} if diagnostic is not None else {}))
            text = client.complete(system, user).strip()
        except Exception as exc:
            if diagnostic is not None:
                category = diagnostic_error_category(exc)
                diagnostic({"event": f"attempt.{category}" if category in {"timeout", "cancelled"} else "attempt.failed",
                            "error_category": category})
            raise
        validation_phase = "titles_parse"
        try:
            raw = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE))
            validation_phase = "titles_validation"
            candidates = raw["candidates"]
            if not isinstance(candidates, list) or not 3 <= len(candidates) <= 8:
                raise ValueError("模型需要返回 3–8 个不同的候选书名。")
            result, seen = [], set()
            for item in candidates:
                title = clean_title(item["title"])
                reason = item["reason"]
                if not isinstance(reason, str) or not reason.strip() or title.casefold() in seen:
                    raise ValueError("候选书名重复或缺少推荐理由。")
                seen.add(title.casefold())
                result.append({"title": title, "reason": reason.strip()})
            if diagnostic is not None:
                diagnostic({"event": "attempt.completed"})
            return result, {"provider": route["provider"], "model": route["model"], "timeout_seconds": timeout}
        except (ValueError, KeyError, TypeError) as exc:
            if diagnostic is not None:
                category = "json_decode" if isinstance(exc, json.JSONDecodeError) else "titles_schema"
                diagnostic({"event": "validation.failure", "phase": validation_phase, "error_category": category})
                diagnostic({"event": "attempt.failed", "phase": validation_phase, "error_category": category})
            if attempt:
                raise ValueError("书名建议格式不完整，请重试；已有骨架与命名要求已保留。") from None
            if diagnostic is not None:
                diagnostic({"event": "format_retry", "retry_count": attempt+1, "error_category": category})
            user += "\nReturn the required valid candidates JSON with distinct single-line titles and reasons."
    raise AssertionError("unreachable")
