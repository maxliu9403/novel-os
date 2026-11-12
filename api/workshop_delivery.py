"""Story delivery stages and bounded assembly parts with the full author context.

This is a business checkpoint, not a resumed remote inference. Only complete,
validated artifacts are saved; targeted part revisions preserve their originals.
Final publication remains WorkshopService's responsibility and still requires
the existing complete skeleton validators.
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

from core.narrative_format import MODES
from .workshop_checkpoints import SCHEMA_VERSION, STAGES, make_fingerprint
from .workshop_assembly import (ASSEMBLY_PARTS, ASSEMBLY_PART_LABELS, ASSEMBLY_PROTOCOL_VERSION,
                                parse_part, render_part)

LABELS = {"story_design": "全局故事设计", "chapter_plan": "完整章节规划", "assembly": "骨架交付整合"}
_LOCKS = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()


class DeliveryError(ValueError):
    """Safe stage validation error without raw model output."""


class IncompleteStageResponse(DeliveryError):
    """Incomplete transport output, not a rejection of the story design."""


class NarrativeFormatResponseError(DeliveryError):
    def __init__(self, detail: str, candidate: dict):
        super().__init__(detail)
        self.candidate = deepcopy(candidate)


class StageEnvelopeResponseError(DeliveryError):
    """A complete candidate with invalid stage metadata, retained for narrow repair."""

    def __init__(self, candidate: dict, issues: list[dict]):
        self.candidate = deepcopy(candidate)
        self.issues = deepcopy(issues)
        details = []
        for issue in issues:
            field, code = issue['field'], issue['code']
            if code == 'enum':
                details.append(f"{field} 必须为以下值之一：{', '.join(issue['expected'])}")
            elif code == 'const':
                details.append(f"{field} 必须保持已确定的值 {issue['expected']}")
            else:
                details.append(f"{field} 必须为正整数，不能使用字符串、布尔值或小数")
        # Raw model values are private evidence, never part of the public error.
        super().__init__('阶段结果字段无效：' + '；'.join(details) + '。完整故事内容已保留。')


def stage_envelope_contract(mode: str | None = None, chapter_count: int | None = None) -> dict:
    """One schema for first design, saved stages, chapter windows and repairs."""
    if mode is not None and mode not in MODES:
        raise DeliveryError('骨架交付缺少有效的组织形式。')
    if chapter_count is not None and (type(chapter_count) is not int or chapter_count < 1):
        raise DeliveryError('骨架交付缺少有效的已确定章节数。')
    return {'schema': 'workshop-stage-envelope.v1', 'required': ['narrative_mode', 'total_chapters'],
            'fields': {
                'narrative_mode': {'type': 'string', 'enum': sorted(MODES),
                                   **({'const': mode} if mode is not None else {})},
                'total_chapters': {'type': 'integer', 'minimum': 1,
                                   **({'const': chapter_count} if chapter_count is not None else {})}}}


def stage_envelope_rules(mode: str | None = None, chapter_count: int | None = None) -> str:
    contract = json.dumps(stage_envelope_contract(mode, chapter_count), ensure_ascii=False, separators=(',', ':'))
    return '\nSTAGE METADATA CONTRACT: ' + contract + '''
Use canonical enum values exactly; do not invent aliases or localized values.
short_novel is a short complete novel; standalone_long is a standalone long novel;
multi_volume is one story explicitly organized into volumes; series_installment is
the current installment of a series. Chapter count alone does not select the form.
Respect any const bound to a saved design or confirmed allocation. Otherwise choose
from the FULL author context and latest explicit choices; compatible author choices
outrank model suggestions, and newly saved settings outrank conflicting older inputs.
If form is genuinely unspecified, explain the recommended form in decisions without
adding a separate approval round. Keep the confirmed total_chapters exactly, not a
quoted number. These metadata rules do not authorize changing any story content.
'''


def _stage_envelope_issues(data: dict, mode: str | None, chapter_count: int | None) -> list[dict]:
    contract = stage_envelope_contract(mode, chapter_count)['fields']
    issues = []
    for field, rules in contract.items():
        value = data.get(field)
        if field == 'narrative_mode' and (not isinstance(value, str) or value not in rules['enum']):
            code, expected = 'enum', rules['enum']
        elif field == 'total_chapters' and (type(value) is not int or value < 1):
            code, expected = 'positive_integer', {'type': 'integer', 'minimum': 1}
        elif 'const' in rules and value != rules['const']:
            code, expected = 'const', rules['const']
        else:
            continue
        issues.append({'field': field, 'code': code, 'expected': expected, 'actual': deepcopy(value)})
    return issues


def repair_stage_envelope(candidate: dict, patch: dict, *, mode: str | None = None,
                          chapter_count: int | None = None) -> dict:
    """Apply only the invalid metadata fields; the complete story stays frozen."""
    if not isinstance(candidate, dict):
        raise DeliveryError('阶段结果必须是完整 JSON 对象。')
    issues = _stage_envelope_issues(candidate, mode, chapter_count)
    fields = {issue['field'] for issue in issues}
    if not fields or not isinstance(patch, dict) or set(patch) != fields:
        raise DeliveryError('阶段字段修复超出范围：仅可返回全部待修复字段，不得重写故事或有效字段。')
    result = {**deepcopy(candidate), **deepcopy(patch)}
    remaining = _stage_envelope_issues(result, mode, chapter_count)
    if remaining:
        raise StageEnvelopeResponseError(result, remaining)
    return result


def author_context(session: dict) -> dict:
    from core.creation_mode import source_policy
    from .workshop_design import design_context
    from .workshop_capacity import context as capacity_context
    from core.creative_baseline import context as development_context
    return {"initial_brief": session["input"], "conversation": session["messages"],
            "previous_pending_questions": session.get("questions", []),
            "current_complete_skeleton": session.get("prompt_md", ""), **design_context(session), **capacity_context(session), **development_context(session),
            **({'publication_selection_policy': 'initial_brief.publication_taxonomy is author-selected public metadata. The frozen Genre/Tag knowledge supplies writing guidance. Never add or change selected IDs; propose changes to the author. Server code binds exactly this taxonomy into the skeleton, EPUB and package.'}
               if session['input'].get('publication_taxonomy') else {}),
            **({'source_reference': session['source_context'], 'source_reference_policy': source_policy(session['source_context'])}
               if session.get('source_context') else {})}


def checkpoint_fingerprint(session: dict, skill_version: str) -> str:
    return make_fingerprint({**author_context({**session, "turn_intent": "deliver"}),
        "context_start_revision": session.get("context_start_revision", 0),
        "pending_input_fields": session.get("pending_input_fields", []),
        "input_needs_revision": bool(session.get("input_needs_revision")),
        **({"design_needs_revision": bool(session.get("design_needs_revision"))} if session.get("creative_design") else {}),
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


def parse_stage(text: str, stage: str, mode: str | None, chapter_count: int | None = None, *, story_first: bool = False,
                chapter_range: tuple[int, int] | None = None) -> dict:
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise IncompleteStageResponse("阶段结果不是完整 JSON，请保留故事内容并修复格式。") from None
    if not isinstance(data, dict):
        raise DeliveryError("阶段结果必须是完整 JSON 对象。")
    issues = _stage_envelope_issues(data, mode, chapter_count)
    if issues:
        raise StageEnvelopeResponseError(data, issues)
    if stage == "story_design":
        content = data.get("story_design")
        if not isinstance(content, dict) or not all(_text(content.get(key)) for key in
                ("premise", "characters", "causal_chain", "ending", "decisions")):
            raise DeliveryError("全局故事设计必须完整包含故事承诺、人物、因果主线、结局与作者决定。")
    elif stage == "chapter_plan":
        if chapter_range and set(data) != {"narrative_mode", "total_chapters", "chapters"}:
            raise DeliveryError("章卡批次的额外故事资料须完整写入对应章卡，批次顶层只接受组织形式、总章数和章卡列表。")
        chapters = data.get("chapters")
        first, last = chapter_range or (1, data["total_chapters"])
        if (not 1 <= first <= last <= data["total_chapters"] or not isinstance(chapters, list) or len(chapters) != last - first + 1
                or any(not isinstance(chapter, dict) or type(chapter.get("number")) is not int
                       or chapter["number"] != index or not _text(chapter.get("title"))
                       or not (chapter.get("core_plot") if story_first else _text(chapter.get("plan"))) for index, chapter in enumerate(chapters, first))):
            raise DeliveryError("章节规划必须逐章完整覆盖全书，不能缺章、重复、乱序或保留空内容。")
    else:
        raise DeliveryError("未知的骨架交付阶段。")
    if story_first:
        from core.story_design import validate_stage, NarrativeFormatValidationError, normalize_format_envelope
        try:
            data = validate_stage(data, stage, chapter_range=chapter_range)
        except NarrativeFormatValidationError as exc:
            raise NarrativeFormatResponseError(str(exc), normalize_format_envelope(data)) from None
        except ValueError as exc:
            raise DeliveryError(str(exc)) from None
    return data


def _markdown(stage: str, data: dict) -> str:
    if stage == "story_design":
        fields = (("premise", "故事承诺"), ("characters", "人物与弧光"),
                  ("causal_chain", "全书因果主线"), ("ending", "高潮与结局"), ("decisions", "作者决定与创作假设"))
        return f"## 全局故事设计\n\n章节数：{data['total_chapters']}\n\n" + "\n\n".join(
            f"### {label}\n\n{data['story_design'][key]}" for key, label in fields)
    if stage == "chapter_plan":
        if all("core_plot" in chapter for chapter in data["chapters"]):
            from core.story_design import ChapterCard
            return "\n\n".join(ChapterCard.model_validate({k: v for k, v in c.items() if k not in {"target_words", "capacity_scene_ids"}}).markdown() for c in data["chapters"])
        return "## 完整章节规划\n\n" + "\n\n".join(
            f"### Chapter {chapter['number']} · {chapter['title']}\n\n{chapter['plan']}" for chapter in data["chapters"])
    return data["prompt_md"]


class DeliveryPlan:
    """Validate, persist and reuse artifacts through one service-owned save seam."""

    def __init__(self, session: dict, mode: str | None):
        if mode is not None and mode not in MODES:
            raise DeliveryError("骨架交付缺少有效的组织形式。")
        from core.story_design import enabled
        self.story_first = enabled(session)
        self.session = session
        self.mode = mode
        self.record = deepcopy(session.get("_delivery_record")) or {
            "schema_version": SCHEMA_VERSION,
            "run_id": session.get("delivery_run_id") or uuid.uuid4().hex,
            "session_id": session["id"],
            "source_revision": session.get("delivery_source_revision", session["revision"]),
            "input_sha256": checkpoint_fingerprint(session, session["skill_version"]),
            **({"prior_candidate": session["_prior_candidate"]} if "_prior_candidate" in session else {}),
            "skill_version": session["skill_version"], "narrative_mode": mode,
            "active_stage": None, "completed": {},
        }
        # Revalidate semantic shape as well as storage integrity before reuse.
        for stage in STAGES[:2]:
            if stage in self.record["completed"]:
                parse_stage(json.dumps(self.record["completed"][stage]["content"], ensure_ascii=False),
                            stage, mode, self.expected_chapter_count, story_first=self.story_first)
        if "chapter_windows" in self.record and "chapter_plan" not in self.record["completed"]:
            from .workshop_chapter_windows import prefix
            cards = prefix(self.record)
            data = {"narrative_mode": self.mode, "total_chapters": self.chapter_count, "chapters": cards}
            parse_stage(json.dumps(data, ensure_ascii=False), "chapter_plan", self.mode, self.chapter_count,
                        chapter_range=(1, len(cards)), story_first=self.story_first)
        for part, data in self.saved_parts.items():
            parse_part(json.dumps(data, ensure_ascii=False), part, mode, self.chapter_count)

    @property
    def chapter_count(self) -> int | None:
        story = self.record["completed"].get("story_design")
        return story["content"]["total_chapters"] if story else None

    @property
    def expected_chapter_count(self) -> int | None:
        """A confirmed allocation binds the very first stage, before a checkpoint."""
        from .workshop_capacity import enabled
        capacity = self.session.get('capacity') or {}
        if enabled(self.session) and capacity.get('status') == 'confirmed':
            allocation = capacity.get('confirmed') or {}
            chapters = (allocation.get('option') or {}).get('chapters')
            if not isinstance(chapters, list) or not chapters:
                raise DeliveryError('已确认篇幅缺少完整的章节分配；原记录仍保留。')
            return len(chapters)
        return self.chapter_count

    def chapter_window(self, max_tokens: int | None, max_width: int | None = None) -> tuple[int, int] | None:
        from .workshop_chapter_windows import prefix, window_width
        if self.stage != "chapter_plan":
            return None
        size = window_width(self.chapter_count, max_tokens)
        if max_width is not None:
            size = min(size, max_width)
        if size >= self.chapter_count and "chapter_windows" not in self.record:
            return None
        first = len(prefix(self.record)) + 1
        return first, min(self.chapter_count, first + size - 1)

    def finish_window(self, data: dict, bounds: tuple[int, int], *, source_coverage: dict | None = None) -> bool:
        """Persist the prefix or seal the complete plan in the same atomic save."""
        from .workshop_chapter_windows import VERSION, prefix
        cards = prefix(self.record)
        if bounds[0] != len(cards) + 1:
            raise DeliveryError("章卡批次不与已保存进度相邻。")
        complete = bounds[1] == self.chapter_count
        if complete:
            joined = {**data, "chapters": cards + data["chapters"]}
            parse_stage(json.dumps(joined, ensure_ascii=False), "chapter_plan", self.mode,
                        self.chapter_count, story_first=self.story_first)
        group = self.record.setdefault("chapter_windows", {"version": VERSION, "batches": []})
        group["batches"].append({"chapters": deepcopy(data["chapters"]),
                                 **({"source_coverage": source_coverage} if source_coverage is not None else {})})
        if complete:
            self.finish("chapter_plan", joined)
        else:
            self._save()
        return complete

    @property
    def stage(self) -> str:
        return next((stage for stage in STAGES if stage not in self.record["completed"]), "assembly")

    @property
    def saved_parts(self) -> dict:
        parts = self.record.get("assembly_parts", {})
        result = {part: artifact["content"] for part, artifact in parts.get("completed", {}).items()}
        result.update({item["part"]: item["content"] for item in parts.get("revisions", [])})
        return deepcopy(result)

    @property
    def active_part(self) -> str | None:
        parts = self.record.get("assembly_parts", {})
        return parts.get("active_part") or next((part for part in ASSEMBLY_PARTS
                                                if part not in parts.get("completed", {})), None)

    def part_event(self, action: str, part: str) -> dict:
        if part not in ASSEMBLY_PARTS:
            raise DeliveryError("未知的骨架整合小块。")
        return {**self.event(action, "assembly"), "event": f"delivery.part.{action}",
                "assembly_part": part, "assembly_part_label": ASSEMBLY_PART_LABELS[part],
                "completed_parts": len(self.record.get("assembly_parts", {}).get("completed", {})),
                "total_parts": len(ASSEMBLY_PARTS)}

    def begin_part(self, part: str) -> None:
        if self.stage != "assembly" or "assembly" in self.record["completed"]:
            raise DeliveryError("整合小块必须在完整故事和章节规划保存后开始。")
        parts = self.record.get("assembly_parts")
        completed = parts["completed"] if parts is not None else {}
        next_part = next((item for item in ASSEMBLY_PARTS if item not in completed), None)
        if part not in ASSEMBLY_PARTS or (next_part is not None and part != next_part):
            raise DeliveryError("不能跳过未完成的骨架整合小块。")
        if parts is None:
            parts = self.record["assembly_parts"] = {
                "protocol_version": ASSEMBLY_PROTOCOL_VERSION, "active_part": None,
                "completed": {}, "revisions": [],
            }
        parts["active_part"] = part
        self.record["active_stage"] = "assembly"
        self._save()

    def _part_artifact(self, part: str, data: dict, content_md: str | None) -> dict:
        parse_part(json.dumps(data, ensure_ascii=False), part, self.mode, self.chapter_count)
        return {"content": deepcopy(data), "content_md": content_md if content_md is not None else render_part(part, data),
                "saved_at": datetime.now(timezone.utc).isoformat()}

    def finish_part(self, part: str, data: dict, content_md: str | None = None) -> None:
        parts = self.record.get("assembly_parts")
        if parts is None or "assembly" in self.record["completed"]:
            raise DeliveryError("整合小块尚未开始或整合结果已保存。")
        if part in parts["completed"]:
            if self.saved_parts[part] != data:
                raise DeliveryError("已保存的小块不可覆盖；修复必须追加修订。")
            return
        expected = next((item for item in ASSEMBLY_PARTS if item not in parts["completed"]), None)
        if part != expected or parts["active_part"] != part:
            raise DeliveryError("不能跳过未完成的骨架整合小块。")
        parts["completed"][part] = self._part_artifact(part, data, content_md)
        parts["active_part"] = next((item for item in ASSEMBLY_PARTS if item not in parts["completed"]), None)
        self._save()

    def revise_part(self, part: str, data: dict, content_md: str | None = None) -> None:
        parts = self.record.get("assembly_parts")
        if (parts is None or len(parts["completed"]) != len(ASSEMBLY_PARTS)
                or parts["active_part"] != part or part not in ASSEMBLY_PARTS
                or "assembly" in self.record["completed"]):
            raise DeliveryError("小块修订须在全部小块保存后开始，且不能改写已发布整合。")
        parts["revisions"].append({"part": part, **self._part_artifact(part, data, content_md)})
        parts["active_part"] = None
        self._save()

    def clear_part_repair(self) -> None:
        """A successful local recheck may close a previously interrupted repair."""
        parts = self.record.get("assembly_parts")
        if (parts is None or len(parts["completed"]) != len(ASSEMBLY_PARTS)
                or "assembly" in self.record["completed"]):
            raise DeliveryError("仅可清理完整小块尚未发布时的修订进度。")
        if parts["active_part"] is not None:
            parts["active_part"] = None
            self._save()

    def event(self, action: str, stage: str | None = None) -> dict:
        stage = stage or self.stage
        return {"event": f"delivery.stage.{action}", "delivery_stage": stage,
                "stage_index": STAGES.index(stage) + 1, "completed_stages": len(self.record["completed"])}

    def begin(self) -> None:
        if len(self.record["completed"]) < len(STAGES):
            self.record["active_stage"] = self.stage
            self._save()

    def _save(self):
        # An explicitly requested fresh delivery may choose its mode in the
        # first story-design response. Do not persist a mutable/null identity;
        # per-turn intent already makes a pause before that result resumable.
        if self.mode is None:
            return
        callback = self.session.get("_save_delivery_checkpoint")
        if callback is not None:
            callback(deepcopy(self.record))

    def finish(self, stage: str, data: dict):
        if stage in self.record["completed"]:
            return
        if stage != self.stage:
            raise DeliveryError("不能跳过未完成的骨架交付阶段。")
        if stage == "story_design" and self.mode is None:
            parse_stage(json.dumps(data, ensure_ascii=False), stage, None,
                        self.expected_chapter_count, story_first=self.story_first)
            self.mode = data["narrative_mode"]
            self.record["narrative_mode"] = self.mode
        if stage == "assembly" and "assembly_parts" in self.record:
            parts = self.record["assembly_parts"]
            if len(parts["completed"]) != len(ASSEMBLY_PARTS) or parts["active_part"] is not None:
                raise DeliveryError("必须完成所有整合小块及修订，才能保存完整骨架。")
        self.record["completed"][stage] = {"content": deepcopy(data), "content_md": _markdown(stage, data),
                                            "saved_at": datetime.now(timezone.utc).isoformat()}
        self.record["active_stage"] = STAGES[len(self.record["completed"])] if len(self.record["completed"]) < 3 else None
        self._save()

    def context(self, base: dict) -> dict:
        result = {**base, "delivery_stage": self.stage, "delivery_mode": self.mode,
                "saved_story_stages": {stage: item["content"] for stage, item in self.record["completed"].items()
                                       if stage != "assembly"}}
        if "assembly_parts" in self.record:
            result["saved_assembly_parts"] = self.saved_parts
        return result

    def rules(self, *, window: bool = False) -> str:
        from core.story_design import enabled, DESIGN_RULES, CHAPTER_RULES, CHAPTER_CARD_RULES
        envelope = stage_envelope_rules(self.mode, self.expected_chapter_count)
        if window:
            from .workshop_chapter_windows import WINDOW_RULES
            if self.story_first:
                return envelope + CHAPTER_CARD_RULES + WINDOW_RULES
            return envelope + WINDOW_RULES + """
Return ONLY JSON with narrative_mode, total_chapters and chapters: a list of
{number:int,title:string,plan:string}. Each plan contains POV, concrete events,
motivated choices, causal advance, emotional change, setup/payoff references and
ending hook; link to the global causal chain and preserve the earned ending.
"""
        if enabled(self.session):
            return envelope + (DESIGN_RULES if self.stage == "story_design" else CHAPTER_RULES)
        common = envelope + """
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
