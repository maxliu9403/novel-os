"""Coarse story delivery stages, retaining the full author context at every step.

This is a business checkpoint, not a resumed remote inference. Only complete,
validated stage artifacts are saved. Final publication remains WorkshopService's
responsibility and still requires the existing complete skeleton validators.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
import weakref

from .workshop_checkpoints import SCHEMA_VERSION, STAGES, make_fingerprint

MODES = frozenset({"short_novel", "standalone_long", "multi_volume", "series_installment"})
LABELS = {"story_design": "全局故事设计", "chapter_plan": "完整章节规划", "assembly": "骨架交付整合"}
_LOCKS = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()


class DeliveryError(ValueError):
    """Safe stage validation error without raw model output."""


def author_context(session: dict) -> dict:
    return {"initial_brief": session["input"], "conversation": session["messages"],
            "previous_pending_questions": session.get("questions", []),
            "current_complete_skeleton": session.get("prompt_md", "")}


def checkpoint_fingerprint(session: dict, skill_version: str) -> str:
    return make_fingerprint({**author_context(session),
        "context_start_revision": session.get("context_start_revision", 0),
        "pending_input_fields": session.get("pending_input_fields", []),
        "input_needs_revision": bool(session.get("input_needs_revision")),
    }, skill_version)


@contextmanager
def serialized_inference(session: dict, *, now=time.monotonic):
    """Wait for a cancelled native process to drain before reusing a discussion.

    Never hold the session state lock while waiting or while calling a model.
    Distinct discussions remain independent. The enclosing turn deadline applies
    after this gate too; acquiring the gate does not purchase a fresh budget.
    """
    path = session.get("_inference_lock_path")
    if path is None:
        yield
        return
    from core.llm_client import LLMCancelledError
    path = Path(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(str(path))
        if lock is None:
            lock = threading.Lock()
            _LOCKS[str(path)] = lock
    cancel = session.get("_cancellation_event")

    def check():
        if cancel is not None and cancel.is_set():
            raise LLMCancelledError("Workshop inference was paused")
        if now() >= session["_turn_deadline"]:
            raise TimeoutError("Workshop turn timed out waiting for the previous process to stop")

    acquired = False
    try:
        while not acquired:
            check()
            acquired = lock.acquire(timeout=0.05)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as stream:
            while True:
                check()
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if cancel is not None:
                        cancel.wait(0.05)
                    else:
                        time.sleep(0.05)
            try:
                check()
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
    finally:
        if acquired:
            lock.release()


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def parse_stage(text: str, stage: str, mode: str, chapter_count: int | None = None) -> dict:
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise DeliveryError("阶段结果不是完整 JSON，请保留故事内容并修复格式。") from None
    if (not isinstance(data, dict) or data.get("narrative_mode") != mode
            or type(data.get("total_chapters")) is not int or data["total_chapters"] < 1):
        raise DeliveryError("阶段结果缺少有效的组织形式或全书章节数。")
    if chapter_count is not None and data["total_chapters"] != chapter_count:
        raise DeliveryError("章节规划必须保持已保存故事设计的章节数。")
    if stage == "story_design":
        content = data.get("story_design")
        if not isinstance(content, dict) or not all(_text(content.get(key)) for key in
                ("premise", "characters", "causal_chain", "ending", "decisions")):
            raise DeliveryError("全局故事设计必须完整包含故事承诺、人物、因果主线、结局与作者决定。")
    elif stage == "chapter_plan":
        chapters = data.get("chapters")
        if (not isinstance(chapters, list) or len(chapters) != data["total_chapters"]
                or any(not isinstance(chapter, dict) or type(chapter.get("number")) is not int
                       or chapter["number"] != index or not _text(chapter.get("title"))
                       or not _text(chapter.get("plan")) for index, chapter in enumerate(chapters, 1))):
            raise DeliveryError("章节规划必须逐章完整覆盖全书，不能缺章、重复、乱序或保留空内容。")
    else:
        raise DeliveryError("未知的骨架交付阶段。")
    return data


def _markdown(stage: str, data: dict) -> str:
    if stage == "story_design":
        fields = (("premise", "故事承诺"), ("characters", "人物与弧光"),
                  ("causal_chain", "全书因果主线"), ("ending", "高潮与结局"), ("decisions", "作者决定与创作假设"))
        return f"## 全局故事设计\n\n章节数：{data['total_chapters']}\n\n" + "\n\n".join(
            f"### {label}\n\n{data['story_design'][key]}" for key, label in fields)
    if stage == "chapter_plan":
        return "## 完整章节规划\n\n" + "\n\n".join(
            f"### Chapter {chapter['number']} · {chapter['title']}\n\n{chapter['plan']}" for chapter in data["chapters"])
    return data["prompt_md"]


class DeliveryPlan:
    """Validate, persist and reuse stages through one service-owned save seam."""

    def __init__(self, session: dict, mode: str):
        if mode not in MODES:
            raise DeliveryError("骨架交付缺少有效的组织形式。")
        self.session = session
        self.mode = mode
        self.record = deepcopy(session.get("_delivery_record")) or {
            "schema_version": SCHEMA_VERSION,
            "run_id": session.get("delivery_run_id") or uuid.uuid4().hex,
            "session_id": session["id"],
            "source_revision": session.get("delivery_source_revision", session["revision"]),
            "input_sha256": checkpoint_fingerprint(session, session["skill_version"]),
            "skill_version": session["skill_version"], "narrative_mode": mode,
            "active_stage": None, "completed": {},
        }
        # Revalidate semantic shape as well as storage integrity before reuse.
        for stage in STAGES[:2]:
            if stage in self.record["completed"]:
                parse_stage(json.dumps(self.record["completed"][stage]["content"], ensure_ascii=False),
                            stage, mode, self.chapter_count if stage == "chapter_plan" else None)

    @property
    def chapter_count(self) -> int | None:
        story = self.record["completed"].get("story_design")
        return story["content"]["total_chapters"] if story else None

    @property
    def stage(self) -> str:
        return next((stage for stage in STAGES if stage not in self.record["completed"]), "assembly")

    def event(self, action: str, stage: str | None = None) -> dict:
        stage = stage or self.stage
        return {"event": f"delivery.stage.{action}", "delivery_stage": stage,
                "stage_index": STAGES.index(stage) + 1, "completed_stages": len(self.record["completed"])}

    def begin(self) -> None:
        if len(self.record["completed"]) < len(STAGES):
            self.record["active_stage"] = self.stage
            self._save()

    def _save(self):
        callback = self.session.get("_save_delivery_checkpoint")
        if callback is not None:
            callback(deepcopy(self.record))

    def finish(self, stage: str, data: dict):
        if stage in self.record["completed"]:
            return
        if stage != self.stage:
            raise DeliveryError("不能跳过未完成的骨架交付阶段。")
        self.record["completed"][stage] = {"content": deepcopy(data), "content_md": _markdown(stage, data),
                                            "saved_at": datetime.now(timezone.utc).isoformat()}
        self.record["active_stage"] = STAGES[len(self.record["completed"])] if len(self.record["completed"]) < 3 else None
        self._save()

    def context(self, base: dict) -> dict:
        return {**base, "delivery_stage": self.stage, "delivery_mode": self.mode,
                "saved_story_stages": {stage: item["content"] for stage, item in self.record["completed"].items()
                                       if stage != "assembly"}}

    def rules(self) -> str:
        common = """
CURRENT REQUEST PROFILE: STAGED SKELETON DELIVERY.
Read the FULL author brief and conversation. They are not shortened. Preserve explicit
facts, hard constraints and accepted decisions. Reuse saved_story_stages as the design
baseline; do not reopen settled alternatives or silently change their chapter count,
mode, protagonist choices, major causal turns or ending. Complete ONLY delivery_stage.
These are public story artifacts and concise design decisions, never private reasoning.
Do not invoke tools, read files, ask for another stage approval, or emit progress instead
of the complete artifact. All stages continue in this authorized turn if budget remains.
Publication contract serialization is deferred to assembly. The finished skeleton must
contain the whole design, not a summary that drops chapter events or established facts.
"""
        if self.stage == "story_design":
            return common + """
STAGE: story_design. Design the complete global story before mapping every chapter.
Return ONLY JSON:
{"narrative_mode":"the supplied delivery_mode","total_chapters":21,
 "story_design":{"premise":"story promise, stakes, setting and whole-book goal",
 "characters":"identities, motivations, relationships, distinctive behaviors and emotional arcs",
 "causal_chain":"ordered major turns: motivated choices, opposition, costs, reversals, escalation and payoffs",
 "ending":"earned climax, resolution of the main problem and every major arc",
 "decisions":"accepted author choices and explicitly labeled delegated creative assumptions"}}
The example chapter count is illustrative only: derive the actual count from the author
context. Do not replace unspecified choices with questions after a delegated delivery.
"""
        if self.stage == "chapter_plan":
            return common + """
STAGE: chapter_plan. Build the COMPLETE chapter map for saved story_design, preserving
its total_chapters and narrative_mode. Cover every chapter, numbered 1 through N, once
and in order. Each plan contains POV, concrete events, motivated choices, causal advance,
emotional change, setup/payoff references and ending hook; link to the global causal chain.
Check whole-book continuity, subplot coverage and the earned ending across the full map.
Return ONLY JSON:
{"narrative_mode":"the saved mode","total_chapters":21,
 "chapters":[{"number":1,"title":"actual title","plan":"complete chapter design"}]}
The one-entry example is schema only; return ALL chapters, without placeholders or ellipses.
"""
        return common + """
STAGE: assembly. Assemble and check the complete 17-section skeleton using all saved
story_design and chapter_plan content plus the full author context and export references.
Keep every planned chapter in the same order and preserve its story events, consequences,
POV and payoffs. Check global consistency against the saved story design before returning.
Supply the exact engine contracts, with the saved mode and chapter count. Do not turn this
stage into another whole-book brainstorm. Return the standard four public response fields
with a complete prompt_md and ready_for_confirmation true. No requests_delivery fields.
"""

    def validate_assembly(self, data: dict, actual_mode: str | None):
        prompt = data.get("prompt_md")
        match = re.search(r"^Chapters:\s*([1-9][0-9]*)\s*$", prompt or "", re.MULTILINE)
        if (not prompt or not data.get("ready_for_confirmation") or data.get("questions")
                or not match or int(match.group(1)) != self.chapter_count or actual_mode != self.mode):
            raise DeliveryError("交付整合必须返回完整可确认骨架，并保持已保存设计的组织形式与章节数。")
