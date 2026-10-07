"""Persistent brainstorming sessions; model work is queued, never run in HTTP handlers."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import time
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .jobs import runner
from . import db, tenancy
from .project_operations import project_operations, ProjectMutationBlocked
from .routes import get_service
from .services import ProjectService

LOG = logging.getLogger("uvicorn.error.workshop")
REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills" / "novel-brainstorm-workshop"
FORMATS = ("markdown", "html", "docx", "epub", "pdf")
SCHEMA = "workshop.v2"
router = APIRouter(prefix="/api/workshop", tags=["workshop"])


class ModelResponseError(ValueError):
    """Safe, authored format error suitable for displaying without provider payloads."""


class SessionInput(BaseModel):
    title: str = ""
    author: str = ""
    genres: list[str] = Field(default_factory=list)
    premise: str = ""
    chapters: int | None = Field(default=None, ge=1)
    words_per_chapter: int | None = Field(default=None, ge=1)
    language: str = ""
    mode: Literal["", "short_novel", "standalone_long", "multi_volume", "series_installment"] | None = None
    audience: str = ""
    market: str = ""
    constraints: str = ""
    method_mode: Literal["off", "advisory"] = "advisory"


class RevisionInput(BaseModel):
    revision: int = Field(ge=0)


class TurnInput(RevisionInput):
    message: str = Field(min_length=1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".workshop-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _slug(title: str) -> str:
    return re.sub(r"[^\w-]+", "-", title.lower(), flags=re.UNICODE).strip("-_")[:90] or "novel"


def _timeout() -> float:
    from core.provider_settings import workshop_timeout_seconds
    return workshop_timeout_seconds()


def _skill_bundle(phase: str = "design") -> tuple[str, str]:
    files = ["SKILL.md", "references/interaction.md", "references/character-and-emotion.md",
             "references/hooks-and-payoffs.md", "references/prompt-contract.md",
             "references/narrative-format.md", "references/novel-classification.md",
             "references/launch-command.md", "assets/novel-skeleton-template.md"]
    parts = {name: f"\n--- {name} ---\n{(SKILL / name).read_text(encoding='utf-8')}" for name in files}
    version = hashlib.sha256("\n".join(parts.values()).encode()).hexdigest()[:16]
    # Any turn may receive a complete brief in free-form prose. Keep all output
    # contracts available even when the optional form fields are empty; the
    # phase only selects supplementary craft/launcher material, not capability.
    if phase == "intake":
        selected = [name for name in files if name not in {
            "references/character-and-emotion.md", "references/hooks-and-payoffs.md",
            "references/launch-command.md",
        }]
    elif phase == "revision":
        selected = [name for name in files if name != "references/launch-command.md"]
    else:
        selected = files
    return "\n".join(parts[name] for name in selected), version


def _complete(session: dict, skill: str) -> tuple[dict, dict]:
    from core import provider_settings
    from core.llm_client import LLMClient

    route = provider_settings.resolve_text_route("workshop")
    # Accepting a turn freezes its timeout. Settings changed during inference apply next turn.
    timeout = float(session.get("turn_timeout_seconds") or route.get("timeout_seconds") or _timeout())
    session["_model_log"] = {"provider": route["provider"], "model": route["model"], "timeout_seconds": timeout}
    client = LLMClient(provider=route["provider"], model=route["model"] or None,
                       base_url=route["base_url"] or None, api_key=route["api_key"] or None,
                       max_tokens=route["max_tokens"], timeout_seconds=timeout,
                       reasoning_effort=route.get("reasoning_effort") or None)
    system = skill + """

WEB WORKSHOP TRANSPORT (follow the Skill's creative rules within this interface):
You are brainstorming, not executing files, scripts, commands or tools. Treat the author's
initial premise as author_brief, preserve their actual names and decisions. Questions may be
answered in unlimited subsequent messages; do not pretend missing answers are confirmed.
Read the full conversation and current skeleton below. Do not repeat answered questions.
previous_pending_questions preserves the most recent unanswered questions, including older
sessions whose assistant messages did not store question arrays. Resolve them using the latest
user answers; do not repeat answered questions or invent missing older question wording.
The questions field is a required JSON artifact, not a request for interactive tool input.
Even if a generic text-generation preamble says not to ask questions, record missing essential
author decisions here instead of inventing confirmation. This is the task's output contract.
Return ONLY one JSON object with these exact keys:
{"reply":"### 本轮建议\\n\\nChinese Markdown describing actual changes or questions",
 "questions":["only essential unresolved questions; empty when ready"],
 "prompt_md":"complete current Markdown skeleton if changed, or null to retain the previous version",
 "ready_for_confirmation":false}
The reply string MUST be reader-facing Markdown, not an unstructured wall of text:
use concise ### section headings, blank lines between paragraphs, and bullet or numbered
lists for options, changes, and decisions. Give each proposed direction its own list item
or subsection; use bold for the key difference and a table only when comparison benefits.
Keep simple replies brief; omit empty sections. Questions may use inline Markdown.
Do not wrap reply in a ```markdown fence, emit raw HTML, or expose the transport JSON to
the reader. Keep the complete reasoning summary and all requested details; do not shorten
the stored reply to a UI preview. Include decisions and rationale, not private chain of thought.
Never return a patch, ellipsis, partial outline, command-only response, or generic template
as prompt_md. On the first turn, ask missing essentials and keep prompt_md null if a complete
skeleton cannot yet be designed. When ready, fill every template section with actual story
content, preserve the template's numbered ## 1. through ## 17. sections and use
### Chapter N headings for every chapter inside section 12. Use valid existing
classification and narrative-format JSON, and set
ready_for_confirmation true. The UI's prepare button confirms the whole displayed design;
its narrative_format may be pending_confirmation until then. Keep word targets advisory.
For series, design the series overview and CURRENT installment only, with explicit series
metadata; tell the writer each other installment needs its own session and prompt.
Do not output a launch command yourself: the server generates it from this exact skeleton.
Do not claim parser/design/prose validation was executed or creation started. The transport
JSON and current draft are not an additional mandatory user confirmation round.
"""
    user = json.dumps({"initial_brief": session["input"], "conversation": session["messages"],
                       "previous_pending_questions": session.get("questions", []),
                       "current_complete_skeleton": session["prompt_md"]}, ensure_ascii=False)
    LOG.info("workshop.model.start session=%s route=workshop provider=%s model=%s timeout=%s revision=%s",
             session["id"], route["provider"], route["model"], timeout, session["revision"])
    for attempt in range(3):
        text = client.complete(system, user).strip()
        try:
            data = _parse_response(text, session["prompt_md"])
            break
        except (json.JSONDecodeError, ModelResponseError):
            if attempt == 2:
                raise
            LOG.warning("workshop.model.format_retry session=%s attempt=%s", session["id"], attempt+1)
            user += "\nThe last response did not satisfy the JSON contract. Return a valid complete object from the full brief and conversation above; preserve the previous complete skeleton unless supplying a complete revision."
    return data, {"provider": route["provider"], "model": route["model"], "timeout_seconds": timeout}


def _parse_response(text: str, previous_prompt: str) -> dict:
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    data = json.loads(text)
    if (not isinstance(data, dict) or not isinstance(data.get("reply"), str)
            or not data["reply"].strip() or not isinstance(data.get("questions"), list)
            or any(not isinstance(q, str) for q in data["questions"])
            or not isinstance(data.get("ready_for_confirmation"), bool)
            or (data.get("prompt_md") is not None and not isinstance(data["prompt_md"], str))):
        raise ModelResponseError("模型未返回完整的讨论响应格式，请重试；上一版骨架已保留。")
    if data.get("prompt_md") is not None and not data["prompt_md"].strip():
        raise ModelResponseError("模型返回空骨架；上一版骨架已保留。")
    if data["ready_for_confirmation"] and not (data.get("prompt_md") or previous_prompt):
        raise ModelResponseError("模型没有返回小说骨架，不能标记为可启动。")
    # Models sometimes fence their entire Markdown answer as code. Unwrap only
    # that outer presentation fence; preserve internal code blocks and full text.
    reply = data["reply"].strip()
    opening = re.match(r"(`{3,})(?:md|markdown)[ \t]*\n", reply, re.IGNORECASE)
    if opening:
        # The first matching closing fence must end the whole reply. Otherwise
        # it is a real example followed by prose/code, not an outer wrapper.
        closing = re.search(r"^" + re.escape(opening.group(1)) + r"[ \t]*$",
                            reply[opening.end():], re.MULTILINE)
        if closing and not reply[opening.end()+closing.end():].strip():
            reply = reply[opening.end():opening.end()+closing.start()].strip()
    if not reply:
        raise ModelResponseError("模型未返回讨论内容，请重试；上一版骨架已保留。")
    if not re.search(r"^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>\s)", reply, re.MULTILINE):
        reply = "### 本轮说明\n\n" + reply
    data["reply"] = reply
    return data


def _preserved_context(has_prompt: bool) -> str:
    return ("完整对话和上一版完整骨架已保留。" if has_prompt
            else "基础提示与完整对话已保留，本轮尚未生成完整骨架。")


def _failure(exc: Exception, *, timeout_seconds: float, has_prompt: bool) -> str:
    retained = _preserved_context(has_prompt)
    if isinstance(exc, (TimeoutError,)) or any(word in str(exc).lower() for word in ("timeout", "timed out")):
        return f"模型响应超时（本轮上限 {timeout_seconds:g} 秒）。{retained}可以携带完整上下文重试。"
    if isinstance(exc, json.JSONDecodeError):
        return f"模型响应不完整或不是有效 JSON。{retained}可以重试。"
    if isinstance(exc, ModelResponseError):
        detail = str(exc).replace("上一版完整骨架已保留", "").replace("上一版骨架已保留", "").strip("；，。 ")
        return f"{detail[:400]}。{retained}"
    # Provider exceptions can contain authentication headers or request bodies.
    return f"模型调用失败（{type(exc).__name__}），请检查头脑风暴模型配置。{retained}"


def _handoff():
    spec = importlib.util.spec_from_file_location("novel_workshop_handoff", SKILL / "scripts/handoff.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _confirm_format(text: str) -> str:
    pattern = r"\[NARRATIVE_FORMAT_JSON\]\s*(.*?)\s*\[/NARRATIVE_FORMAT_JSON\]"
    matches = list(re.finditer(pattern, text, flags=re.DOTALL))
    if len(matches) != 1:
        raise ValueError("骨架需要一个完整的 NARRATIVE_FORMAT_JSON 结构配置。")
    match = matches[0]
    body = match.group(1).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        body = fenced.group(1)
    from core.narrative_format import NarrativeFormat
    contract = NarrativeFormat.from_dict(json.loads(body))
    source = contract.selection_source
    if source not in {"author_selected", "recommended_then_confirmed"}:
        source = "recommended_then_confirmed"
    data = contract.with_confirmation(source).to_dict()
    return text[:match.start()] + "[NARRATIVE_FORMAT_JSON]\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n[/NARRATIVE_FORMAT_JSON]" + text[match.end():]


def _validate_candidate(path: Path) -> None:
    """Reject incomplete transport artifacts, without evaluating prose or rigid word counts."""
    text = path.read_text(encoding="utf-8")
    headings = list(re.finditer(r"^##\s+(\d+)\.\s+[^\n]+", text, flags=re.MULTILINE))
    if [int(match.group(1)) for match in headings] != list(range(1, 18)):
        raise ModelResponseError("模型骨架缺少完整的 1–17 节，请补全；上一版完整骨架已保留。")
    if re.search(r"\{\{[^}]+\}\}|\b(?:TODO|TBD)\b|后续略|待补充", text):
        raise ModelResponseError("模型骨架仍含未填写的模板占位内容；上一版完整骨架已保留。")
    if text.count("```") % 2:
        raise ModelResponseError("模型骨架代码块未闭合，可能被截断；上一版完整骨架已保留。")
    for i, match in enumerate(headings):
        end = headings[i+1].start() if i+1 < len(headings) else len(text)
        if not text[match.end():end].strip():
            raise ModelResponseError(f"模型骨架第 {i+1} 节为空；上一版完整骨架已保留。")
    chapters = re.search(r"^Chapters:\s*([1-9][0-9]*)\s*$", text, flags=re.MULTILINE)
    section = text[headings[11].end():headings[12].start()]
    found = [int(number) for number in re.findall(r"^###\s+Chapter\s+([0-9]+)\b", section, flags=re.MULTILINE | re.IGNORECASE)]
    if not chapters or found != list(range(1, int(chapters.group(1))+1)):
        raise ModelResponseError("模型骨架的分章细纲缺章、重复或顺序不一致；上一版完整骨架已保留。")
    try:
        confirmed = _confirm_format(text)
        # Validate a temporary confirmed projection only. The candidate still needs the UI confirmation.
        with tempfile.TemporaryDirectory(prefix="workshop-check-") as folder:
            check_path = Path(folder) / "prompt.md"
            check_path.write_text(confirmed, encoding="utf-8")
            _handoff().validate(check_path, REPO)
        from core.cover_handoff import parse_cover_handoff
        parse_cover_handoff(text)
    except (ValueError, KeyError, TypeError) as exc:
        raise ModelResponseError(f"模型骨架的结构合同尚不完整：{str(exc)[:240]}；上一版骨架已保留。") from None


class WorkshopService:
    def __init__(self, projects: ProjectService):
        self.projects = projects
        self.root = projects.root.resolve().parent / "workshop_v2"
        if projects.workspace is not None and projects.workspace.id != tenancy.DEFAULT_WORKSPACE_ID:
            self.root = self.root / projects.workspace.id

    def _path(self, session_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise HTTPException(404, "找不到头脑风暴会话。")
        path = self.root / session_id
        if path.resolve().parent != self.root.resolve():
            raise HTTPException(404, "找不到头脑风暴会话。")
        return path

    @contextmanager
    def _locked(self, session_id: str):
        path = self._path(session_id)
        if not (path / "session.json").is_file():
            raise HTTPException(404, "找不到头脑风暴会话。")
        with (path / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads((path / "session.json").read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("schema") != SCHEMA:
                    raise HTTPException(409, "此会话版本不兼容，请创建新会话。")
                if data.get("id") != session_id:
                    raise HTTPException(409, "会话记录与其标识不一致，无法恢复。")
                yield data
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _save(self, data: dict):
        data["updated_at"] = _now()
        _atomic(self._path(data["id"]) / "session.json", json.dumps(data, ensure_ascii=False, indent=2))

    def _log(self, data: dict, event: str, message: str, **fields):
        entry = {"time": _now(), "event": event, "message": message, **fields}
        data["logs"].append(entry)
        LOG.info("workshop.%s session=%s revision=%s %s", event, data["id"], data["revision"], json.dumps(entry, ensure_ascii=False))

    def create(self, body: SessionInput) -> dict:
        _, version = _skill_bundle()
        session_id = uuid.uuid4().hex
        self._path(session_id).mkdir(parents=True)
        data = {"schema": SCHEMA, "id": session_id, "revision": 0, "input": body.model_dump(),
                "messages": [], "prompt_md": "", "questions": [], "status": "draft",
                "last_error": None, "job_id": None, "skill_version": version, "prepared": None,
                "project_id": None, "logs": [], "created_at": _now(), "updated_at": _now(),
                "ready_for_confirmation": False}
        self._save(data)
        return data

    def get(self, session_id: str) -> dict:
        with self._locked(session_id) as data:
            if data["status"] == "running" and not runner.get(data.get("job_id") or ""):
                data["status"] = "error"
                data["last_error"] = "服务重启中断了本轮讨论。" + _preserved_context(bool(data.get("prompt_md"))) + "请重试。"
                self._log(data, "interrupted", data["last_error"])
                self._save(data)
            return data

    def list_sessions(self, *, q: str = "", limit: int = 50, offset: int = 0) -> dict:
        """List only this workspace's recoverable sessions; UUID remains the identity."""
        if not self.root.is_dir():
            return {"sessions": [], "total": 0}
        query = q.strip().casefold()
        rows: list[tuple[float, dict]] = []
        for path in self.root.iterdir():
            if (not re.fullmatch(r"[0-9a-f]{32}", path.name) or path.is_symlink()
                    or not path.is_dir() or (path / "session.json").is_symlink()
                    or (path / ".lock").is_symlink()):
                continue
            try:
                # Reconcile a model job interrupted by a process restart, just as resume does.
                data = self.get(path.name)
                if (not isinstance(data.get("input"), dict)
                        or not isinstance(data.get("messages"), list)
                        or not isinstance(data.get("prompt_md"), str)
                        or not isinstance(data.get("updated_at"), str)
                        or type(data.get("revision")) is not int
                        or data["revision"] < 0
                        or data.get("status") not in {"draft", "running", "ready", "error", "launched"}
                        or (data.get("project_id") is not None and not isinstance(data["project_id"], str))):
                    continue
                header = re.split(r"^##\s", data["prompt_md"], maxsplit=1, flags=re.MULTILINE)[0]
                title_match = re.search(
                    r"^\s*(?:[-*]\s*)?(?:\*\*)?Title(?:\*\*)?\s*:[ \t]*(\S[^\n]*?)\s*$",
                    header, flags=re.MULTILINE | re.IGNORECASE,
                )
                input_title = data["input"].get("title") or ""
                premise = data["input"].get("premise") or ""
                if not isinstance(input_title, str) or not isinstance(premise, str):
                    continue
                title = (title_match.group(1).strip() if title_match else input_title.strip()) or "未命名作品"
                if query and query not in title.casefold() and not data["id"].startswith(query):
                    continue
                updated = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                preview = " ".join(premise.split())
                rows.append((updated.timestamp(), {
                    "id": data["id"], "title": title, "status": data["status"],
                    "revision": data["revision"], "updated_at": data["updated_at"],
                    "project_id": data.get("project_id"), "has_prompt": bool(data["prompt_md"].strip()),
                    "message_count": len(data["messages"]),
                    "premise_preview": preview[:160] + ("…" if len(preview) > 160 else ""),
                }))
            except (HTTPException, OSError, ValueError, TypeError, KeyError, AttributeError):
                # Legacy, corrupt, missing, and inaccessible records cannot break the library.
                # Never return their contents or error details to a different session's list.
                continue
        rows.sort(key=lambda row: (row[0], row[1]["id"]), reverse=True)
        return {"sessions": [row for _, row in rows[offset:offset + limit]], "total": len(rows)}

    @staticmethod
    def _available(data: dict, revision: int):
        if revision != data["revision"]:
            raise HTTPException(409, "骨架版本已变化，请刷新当前会话后重试。")
        if data["status"] == "running":
            raise HTTPException(409, "本轮讨论仍在进行，请等待完成。")
        if data["status"] == "launched":
            raise HTTPException(409, "此骨架已启动创作，请新建会话设计另一本作品。")

    def turn(self, session_id: str, body: TurnInput) -> dict:
        if not body.message.strip():
            raise HTTPException(422, "请输入想法或修改要求。")
        with self._locked(session_id) as data:
            self._available(data, body.revision)
            complete_input = all(data["input"].get(key) for key in
                                 ("chapters", "words_per_chapter", "language", "mode", "audience", "market"))
            phase = "revision" if data["prompt_md"] else "design" if data["messages"] or complete_input else "intake"
            skill, version = _skill_bundle(phase)
            timeout = _timeout()
            data["revision"] += 1
            data["messages"].append({"role": "user", "content": body.message.strip()})
            data.update(status="running", last_error=None, prepared=None, skill_version=version,
                        turn_timeout_seconds=timeout)
            self._log(data, "turn_started", "模型正在按小说骨架 Skill 处理本轮意见。", timeout_seconds=timeout)
            # Worker takes the same file lock, so it cannot race the job id save.
            job_id = runner.submit("workshop.turn", lambda: self._run_turn(session_id, skill),
                                   {"session_id": session_id}, unique_key=f"workshop:{self._path(session_id)}")
            data["job_id"] = job_id
            self._save(data)
            return runner.get(job_id)

    def _run_turn(self, session_id: str, skill: str):
        started = time.monotonic()
        with self._locked(session_id) as data:
            snapshot = dict(data)
        try:
            result, route = _complete(snapshot, skill)
            if result.get("prompt_md") is not None:
                candidate = self._path(session_id) / "candidates" / f'{snapshot["revision"]}.md'
                _atomic(candidate, result["prompt_md"])
                _validate_candidate(candidate)
            with self._locked(session_id) as data:
                data["revision"] += 1
                if result.get("prompt_md") is not None:
                    data["prompt_md"] = result["prompt_md"]
                    _atomic(self._path(session_id) / "versions" / f'{data["revision"]}.md', data["prompt_md"])
                data["messages"].append({"role": "assistant", "content": result["reply"],
                                         "questions": list(result["questions"])})
                data.update(questions=result["questions"], ready_for_confirmation=result["ready_for_confirmation"],
                            status="ready" if result["ready_for_confirmation"] else "draft", last_error=None)
                self._log(data, "turn_completed", "本轮讨论已完成。", elapsed_seconds=round(time.monotonic()-started, 2), **route)
                self._save(data)
        except Exception as exc:
            message = _failure(exc, timeout_seconds=float(snapshot.get("turn_timeout_seconds") or 900),
                               has_prompt=bool(snapshot.get("prompt_md")))
            with self._locked(session_id) as data:
                data.update(status="error", last_error=message)
                self._log(data, "turn_failed", message, error_type=type(exc).__name__,
                          elapsed_seconds=round(time.monotonic()-started, 2), **snapshot.get("_model_log", {}))
                self._save(data)
            raise RuntimeError(message) from None

    def prepare(self, session_id: str, body: RevisionInput) -> dict:
        with self._locked(session_id) as data:
            self._available(data, body.revision)
            if not data["prompt_md"] or not data["ready_for_confirmation"] or data["questions"]:
                raise HTTPException(422, "请先完成必要信息和小说骨架，再确认并生成启动命令。")
            confirmed = _confirm_format(data["prompt_md"])
            path = self._path(session_id) / "prepared.md"
            _atomic(path, confirmed)
            helper = _handoff()
            try:
                validated = helper.validate(path, REPO)
                name = _slug(validated["title"]) + "-" + session_id[:8]
                command, _ = helper.command(path, REPO, name, method_mode=data["input"].get("method_mode", "advisory"))
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(422, f"骨架格式尚需修正：{exc}") from exc
            # Keep shell quoting from the verified helper, replacing only final launcher line.
            filename = name + ".md"
            command = command.rsplit("\n", 1)[0] + "\n./deploy.sh novel " + shlex.quote("./prompt/" + filename)
            validated.pop("prompt", None)
            data["prompt_md"] = confirmed
            data["prepared"] = {"revision": data["revision"], "command": command, "project_name": name,
                                "prompt_filename": filename, "validation": validated, "output_formats": list(FORMATS),
                                "method_mode": data["input"].get("method_mode", "advisory"),
                                "prompt_sha256": hashlib.sha256(confirmed.encode()).hexdigest()}
            self._log(data, "prepared", "已确认当前骨架并生成全格式启动命令；尚未启动创作。")
            self._save(data)
            return data

    def launch(self, session_id: str, body: RevisionInput) -> dict:
        with self._locked(session_id) as data:
            if data["status"] == "launched" and body.revision == data["revision"]:
                return {"project_id": data["project_id"], "job_id": data["job_id"]}
            self._available(data, body.revision)
            prepared = data["prepared"]
            if not prepared or prepared["revision"] != body.revision:
                raise HTTPException(409, "请先确认当前骨架并生成启动命令。")
            if hashlib.sha256(data["prompt_md"].encode()).hexdigest() != prepared["prompt_sha256"]:
                raise HTTPException(409, "骨架已变化，请重新确认。")
            project_id = prepared["project_name"]
            project_path = self.projects.base_dir.resolve() / project_id
            validated = prepared["validation"]
            from core.pipeline_models import RunSpec
            from core.pipeline_runner import PipelineRunner
            from core.prompt_intake import ingest_prompt
            from core.narrative_methods import MethodPolicy
            from core.narrative_methods.store import MethodStore
            job_id = None
            created = False
            try:
                with project_operations.mutation(project_path):
                    # Allocate a new path atomically; never reuse another project's files.
                    project_path.mkdir(parents=True, exist_ok=False)
                    created = True
                    prompt_path = project_path / "outputs" / "input" / "prompt.md"
                    _atomic(prompt_path, data["prompt_md"])
                    # Make the project immediately readable without generating an author/model call.
                    ingest_prompt(project_path, prompt_path,
                                  {"title": validated["title"], "genre": validated["genre"]},
                                  generate_author=False)
                    db.project_claim(project_id, self.projects.workspace.id if self.projects.workspace
                                     else tenancy.DEFAULT_WORKSPACE_ID)
                    policy = MethodPolicy(mode=data["input"].get("method_mode", "advisory"))
                    MethodStore(project_path).set_policy(policy, "")
                    spec = RunSpec(project_path=str(project_path), prompt_path=str(prompt_path),
                                   title=validated["title"], genre=validated["genre"],
                                   num_chapters=validated["chapters"], target_words=validated["target_words"],
                                   edit_mode="line", approval_policy="auto", quality_policy="evidence_v1",
                                   max_retries=5, max_quality_repairs=2, output_formats=FORMATS,
                                   method_policy=policy.to_dict())
                    job_id = runner.submit("novel.run", lambda: PipelineRunner().run(spec),
                                           {"project_id": project_id, "project_path": str(project_path)},
                                           unique_key=f"workshop-launch:{self._path(session_id)}",
                                           result_mapper=lambda result: {"run_id": result.run_id, "run_status": result.status})
            except (FileExistsError, ProjectMutationBlocked) as exc:
                raise HTTPException(409, "项目路径已存在或正在删除，请勿重复启动。") from exc
            except Exception as exc:
                # Only our just-created, never-submitted project can be cleaned up.
                if created and job_id is None:
                    shutil.rmtree(project_path)
                    db.project_data_delete(project_id)
                self._log(data, "launch_failed", "启动提交失败，可以重试。", error_type=type(exc).__name__)
                data["last_error"] = "启动提交失败，可以重试。"
                self._save(data)
                raise HTTPException(503, data["last_error"]) from exc
            data.update(status="launched", project_id=project_id, job_id=job_id)
            self._log(data, "launched", "已启动现有小说创作流水线。", project_id=project_id, job_id=job_id)
            self._save(data)
            return {"project_id": project_id, "job_id": job_id}


def get_workshop_service(projects: ProjectService = Depends(get_service)) -> WorkshopService:
    return WorkshopService(projects)


@router.post("/sessions", status_code=201)
def create_session(body: SessionInput, service: WorkshopService = Depends(get_workshop_service)):
    return service.create(body)


@router.get("/sessions")
def list_sessions(q: str = Query(default="", max_length=250),
                  limit: int = Query(default=50, ge=1, le=100),
                  offset: int = Query(default=0, ge=0),
                  service: WorkshopService = Depends(get_workshop_service)):
    return service.list_sessions(q=q, limit=limit, offset=offset)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, service: WorkshopService = Depends(get_workshop_service)):
    return service.get(session_id)


@router.post("/sessions/{session_id}/turns", status_code=202)
def add_turn(session_id: str, body: TurnInput, service: WorkshopService = Depends(get_workshop_service)):
    return service.turn(session_id, body)


@router.post("/sessions/{session_id}/prepare")
def prepare_session(session_id: str, body: RevisionInput, service: WorkshopService = Depends(get_workshop_service)):
    try:
        return service.prepare(session_id, body)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"骨架格式尚需修正：{exc}") from exc


@router.post("/sessions/{session_id}/launch", status_code=202)
def launch_session(session_id: str, body: RevisionInput, service: WorkshopService = Depends(get_workshop_service)):
    return service.launch(session_id, body)


@router.get("/sessions/{session_id}/prompt")
def download_prompt(session_id: str, service: WorkshopService = Depends(get_workshop_service)):
    data = service.get(session_id)
    if not data["prompt_md"]:
        raise HTTPException(404, "当前尚未生成骨架。")
    from urllib.parse import quote
    name = (data.get("prepared") or {}).get("prompt_filename") or (_slug(data["input"]["title"]) + ".md")
    return Response(data["prompt_md"], media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)})
