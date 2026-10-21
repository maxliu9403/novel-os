"""Persistent brainstorming sessions; model work is queued, never run in HTTP handlers."""
from __future__ import annotations

from core.usage_tracking import scoped_usage, cover_owner


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
import threading
import time
from typing import Literal
import uuid
import weakref

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .jobs import runner
from . import db, tenancy
from .project_operations import project_operations, ProjectMutationBlocked
from .routes import get_service
from .services import ProjectService
from .workshop_contract_repair import normalize_workshop_contracts
from .workshop_diagnostics import diagnostic_error_category, make_diagnostic_callback, runtime_progress

LOG = logging.getLogger("uvicorn.error.workshop")
REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills" / "novel-brainstorm-workshop"
FORMATS = ("markdown", "html", "docx", "epub", "pdf")
SCHEMA = "workshop.v2"
router = APIRouter(prefix="/api/workshop", tags=["workshop"])

# Some Docker Desktop bind mounts allow blocking flock calls from separate
# threads to overlap when the lock file is first created. Coordinate threads
# explicitly; retain flock below for cooperating processes. Key by resolved path so all
# service instances share a gate, while workspaces/sessions remain independent.
_SESSION_LOCKS = weakref.WeakValueDictionary()
_SESSION_LOCKS_GUARD = threading.Lock()


def _session_thread_lock(path: Path):
    key = str(path.resolve())
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SESSION_LOCKS[key] = lock
        return lock


class ModelResponseError(ValueError):
    """Safe, authored format error suitable for displaying without provider payloads."""


class DeliveryTimeoutError(TimeoutError):
    """A bounded delivery stopped; previously completed pieces remain resumable."""

    def __init__(self, request_seconds: float, *, task_expired: bool = False):
        self.request_seconds = request_seconds
        self.task_expired = task_expired
        super().__init__("Workshop delivery task timeout" if task_expired else "Workshop delivery request timeout")


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
    intent: Literal["discuss", "deliver"] = "discuss"


class PauseInput(RevisionInput):
    job_id: str = Field(min_length=1)


class TitleInput(RevisionInput):
    instructions: str = ""
    current_title: str = ""


class PrepareInput(RevisionInput):
    title: str | None = None


class UpdateInput(RevisionInput):
    input: SessionInput


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
    files = ["SKILL.md", "references/interaction.md", "references/creation-path.md",
             "references/story-exploration.md", "references/character-and-emotion.md",
             "references/hooks-and-payoffs.md", "references/prompt-contract.md",
             "references/narrative-format.md", "references/novel-classification.md",
             "references/launch-command.md", "references/title-naming.md", "assets/novel-skeleton-template.md"]
    files.insert(-1, "references/commercial-story-design.md")
    parts = {name: f"\n--- {name} ---\n{(SKILL / name).read_text(encoding='utf-8')}" for name in files}
    version = hashlib.sha256("\n".join(parts.values()).encode()).hexdigest()[:16]
    # Runtime profiles follow the author's operation, never a model's handoff.
    if phase == "exploration":
        selected = ["SKILL.md", "references/interaction.md", "references/creation-path.md",
                    "references/story-exploration.md", "references/character-and-emotion.md",
                    "references/hooks-and-payoffs.md"]
    elif phase == "delivery":
        # Naming is a separate operation, and the server owns launch commands.
        # The complete skeleton/engine contracts remain available for delivery.
        selected = [name for name in files if name not in {
            "references/launch-command.md", "references/title-naming.md",
        }]
        # Tool-free requests use the serialized example generated below for the
        # selected mode. Retain the reference's rules, not its Python demos or
        # a competing 80-chapter standalone example.
        narrative = parts["references/narrative-format.md"]
        narrative = re.sub(r"```(?:python|text)\n.*?```", "", narrative, flags=re.DOTALL)
        parts["references/narrative-format.md"] = narrative
    elif phase == "intake":
        selected = [name for name in files if name not in {
            "references/character-and-emotion.md", "references/hooks-and-payoffs.md",
            "references/launch-command.md",
        }]
    elif phase == "revision":
        selected = [name for name in files if name != "references/launch-command.md"]
    else:
        selected = files
    # Creative guidance comes first. Keep export contracts available for a full
    # brief on any turn, but do not turn them into an exploration questionnaire.
    export_files = {"references/prompt-contract.md", "references/narrative-format.md",
                    "references/novel-classification.md", "references/launch-command.md",
                    "references/title-naming.md", "assets/novel-skeleton-template.md"}
    creative = [parts[name] for name in selected if name not in export_files]
    export = [parts[name] for name in selected if name in export_files]
    return ("\n".join(creative) + "\n\nFINAL EXPORT REFERENCES — consult when producing the complete "
            "prompt_md; their fields are not questions to ask during story exploration.\n"
            + "\n".join(export)), version


_NARRATIVE_MODES = ("short_novel", "standalone_long", "multi_volume", "series_installment")


def _narrative_contract_reference(mode: str | None = None) -> str:
    """Supply serialized engine fields to the tool-free model, not helper arguments."""
    from core.narrative_format import infer_narrative_format
    from core.novel_classification import infer_classification

    classification = infer_classification(genre="Fantasy")
    examples = {}
    for mode in ((mode,) if mode in _NARRATIVE_MODES else _NARRATIVE_MODES):
        contract = infer_narrative_format(
            classification, chapters=20, target_words=12000, mode=mode,
            volume_count=2 if mode == "multi_volume" else 1,
            explicit_length=False, source="engine_recommended",
            **({"series_id": "example-series", "series_title": "Example Series",
                "series_book_number": 2, "planned_books": 3} if mode == "series_installment" else {}),
        ).to_dict()
        for key in ("format_id", "is_long_form", "volume_count", "chapter_target_words", "audit_policy"):
            contract.pop(key, None)
        examples[mode] = contract
    return """

ENGINE NARRATIVE FORMAT REFERENCE (serialized by the current engine):
These are schema examples ONLY, not author decisions or recommended story lengths.
Return ONE NARRATIVE_FORMAT_JSON object for the author's chosen mode. Adapt every
chapter/word target, volume boundary, series identity and suitability assessment
to the current story and latest decisions. Preserve approved unequal volume spans.
Use total_chapters (not chapters), selection_source (not source), and volume_number,
chapter_start, chapter_end, structural_role in every volume. Include the complete
long_form_suitability object and all six dimensions. The Python helper's argument
names and explicit_length flag are NOT the serialized output schema.
Omit optional engine-derived hashes and fields when composing a new contract;
never fabricate format_id. Keep confirmation_status pending_confirmation until
the author confirms the structure. Do not copy the examples' confirmation decisions.
""" + json.dumps(examples, ensure_ascii=False, indent=2)


def _skeleton_mode(prompt: str) -> str | None:
    """Read a serialized mode only; never guess story structure from keywords."""
    match = re.search(r"\[NARRATIVE_FORMAT_JSON\]\s*(.*?)\s*\[/NARRATIVE_FORMAT_JSON\]", prompt, re.DOTALL)
    if not match:
        return None
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", match.group(1), flags=re.IGNORECASE)
    try:
        contract = json.loads(raw)
    except (ValueError, TypeError):
        return None
    mode = contract.get("mode") if isinstance(contract, dict) else None
    return mode if mode in _NARRATIVE_MODES else None


def _commercial_contract_reference() -> str:
    from core import commercial_story as contract
    fields = {
        "protagonist_life_stage": contract.LIFE_STAGES,
        "invisible_labor": contract.INVISIBLE_LABOR,
        "sacred_asset": contract.SACRED_ASSETS,
        "boundary_transfer": contract.RESOURCE_DIMENSIONS,
        "beneficiary_role": contract.BENEFICIARY_ROLES,
        "proof_type": contract.PROOF_TYPES,
        "deadline_type": contract.DEADLINE_TYPES,
        "agency_source": contract.AGENCY_SOURCES,
        "action_cost": contract.ACTION_COSTS,
        "belonging_anchors": contract.BELONGING_ANCHORS,
        "relationship_shape": contract.RELATIONSHIP_SHAPES,
        "resource_dimension": contract.RESOURCE_DIMENSIONS,
        "action_sequence": contract.FREE_ARC_ACTIONS,
    }
    return """

COMMERCIAL STORY SERIALIZATION:
If the story uses COMMERCIAL_STORY_JSON, follow commercial-story-design.md and
these current engine enums. Put raw JSON directly between the block markers,
without an inner Markdown code fence. Use schema_version: 1, free_trial_arc
(not free_arc), and quality_budgets (not fixed_quality_budgets); do not add status.
Include exactly the fields in that reference. Each emotional_jobs value is an
integer 0–5 with at least three positive values; life_contexts contains 1–5
unique strings. The enums below apply to the corresponding nested fields;
belonging_anchors and action_sequence are arrays, other enum fields are strings.
""" + json.dumps({key: sorted(value) for key, value in fields.items()}, ensure_ascii=False)


def _complete(session: dict, skill: str) -> tuple[dict, dict]:
    from .workshop_delivery import serialized_inference
    # Native CLI cancellation drains the child before this gate is released.
    # A resumed turn cannot overlap the previous process for the same discussion.
    if session.get("_inference_lock_path") is not None:
        session["_turn_deadline"] = time.monotonic() + float(session.get("turn_timeout_seconds") or _timeout())
    with serialized_inference(session, now=time.monotonic):
        return _complete_impl(session, skill)


def _complete_impl(session: dict, skill: str) -> tuple[dict, dict]:
    from core import provider_settings
    from core.llm_client import LLMClient, LLMCancelledError
    from .workshop_delivery import DeliveryError, DeliveryPlan, author_context, parse_stage
    from .workshop_assembly import AssemblyError, AssemblyPlan, parse_part, repair_part

    route = provider_settings.resolve_text_route("workshop")
    # Accepting a turn freezes its timeout. Settings changed during inference apply next turn.
    timeout = float(session.get("turn_timeout_seconds") or route.get("timeout_seconds") or _timeout())
    session["_model_log"] = {"provider": route["provider"], "model": route["model"], "timeout_seconds": timeout}
    client_options = dict(provider=route["provider"], model=route["model"] or None,
                          base_url=route["base_url"] or None, api_key=route["api_key"] or None,
                          max_tokens=route["max_tokens"], reasoning_effort=route.get("reasoning_effort") or None,
                          **({"cancellation_event": session["_cancellation_event"]} if session.get("_cancellation_event") is not None else {}))
    transport = """

WEB WORKSHOP TRANSPORT (follow the Skill's creative rules within this interface):
You are brainstorming, not executing files, scripts, commands or tools. Treat the author's
initial premise as author_brief, preserve explicit identity facts, hard constraints and
confirmed decisions. A rough plot is open to proposed improvements; it is not automatically
a list of locked events. Distinguish proposals from changes the author has accepted.
Questions may be
answered in unlimited subsequent messages; do not pretend missing answers are confirmed.
Read the full conversation and current skeleton below. Do not repeat answered questions.
Lead substantive story discussions with concrete new story possibilities, not a checklist
or a request that the author invent the next plot event. For an open premise, normally offer
three genuinely different causal directions and recommend one with a reason. On subsequent
turns develop the chosen story through new protagonist choices, adaptive opposition,
relationship conflicts, earned reveals or the consequences of earlier wins. Each proposal
connects an event to a motivated choice, its cost, and a change in the later storyline.
"Continue" or "not exciting enough" calls for actual new plot development; it does not
confirm all pending alternatives. A delegated choice ("use your recommendation") does
authorize advancing the recommended direction. Do not regenerate three whole-book options
every turn. For a scoped edit, respect its scope and necessary causal dependencies. When
the author asks to settle the design or deliver, converge instead of introducing new forks.
Design the whole-book goal, major turns, escalation, climax and ending before detailed
chapter mapping. Keep character desire, fear, distinctive behavior and emotional choices
central. Resolve a detail now only if it changes a choice, causal link or outcome; otherwise
propose an optional assumption or leave it for drafting. Do not ask about costumes, precise
dialogue, prop specifications or prose polish to complete a skeleton. Ask only high-impact
unresolved questions that are needed now; zero questions is valid, even during exploration.
During exploration, put proposed story directions and the current causal storyline in
reply. Return prompt_md null and ready_for_confirmation false to retain any prior complete
skeleton without publishing tentative alternatives. This discussion is saved in conversation;
it need not regenerate all 17 export sections after each idea. The selected operation controls
whether this turn discusses proposals or delivers a skeleton; a complete brief alone does not
change that operation. An explicit delivery operation uses the full brief directly without
requiring more discussion rounds. Export schemas are delivery references, not the conversation agenda.
The initial_brief contains the latest saved basic settings. Explicit later changes to these
settings supersede older discussion decisions; update affected chapters and contracts.
The title is provisional during story design. Do not require final naming before the
skeleton is ready: the author will confirm the skeleton, request naming suggestions, and
choose the final title in a separate naming stage before launching.
previous_pending_questions preserves the most recent unanswered questions, including older
sessions whose assistant messages did not store question arrays. Resolve them using the latest
user answers; do not repeat answered questions or invent missing older question wording.
The questions field is a required JSON artifact, not a request for interactive tool input.
Even if a generic text-generation preamble says not to ask questions, record missing essential
author decisions here instead of inventing confirmation. This is the task's output contract.
Return ONLY one JSON object with these public response keys:
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
as prompt_md. If a complete skeleton cannot yet be designed, develop the story in reply and
keep prompt_md null; missing publication parameters do not prevent proposing story directions.
Only for an explicit delivery operation, fill every template section with actual story
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
    # A complete brief, old candidate, or model response is not authorization
    # to stop discussing. A verified legacy checkpoint is reusable only through
    # its explicit resume operation, which sets turn_intent below.
    delivering = session.get("turn_intent") == "deliver"
    profile = "delivery" if delivering else "exploration"
    narrative_mode = session["input"].get("mode")
    if "mode" not in session.get("pending_input_fields", []):
        # A later discussion may have revised the form's original mode. Only a
        # newly saved basic-setting change takes precedence over that design.
        narrative_mode = (_skeleton_mode(session.get("_previous_candidate", ""))
                          or _skeleton_mode(session.get("prompt_md", "")) or narrative_mode)

    def build_system() -> str:
        # Keep custom prompt callers compatible. Standard bundles are disclosed
        # by runtime phase without trimming the author's conversation.
        rules = _skill_bundle(profile)[0] if "--- SKILL.md ---" in skill else skill
        if profile == "delivery":
            return (rules + transport + _narrative_contract_reference(narrative_mode or "short_novel")
                    + _commercial_contract_reference())
        return rules + transport + """

CURRENT REQUEST PROFILE: AUTHOR-GUIDED EXPLORATION.
This request carries creative guidance and the FULL author context, but intentionally
omits detailed export schemas. Do not attempt all 17 sections or invent their fields here.
For story exploration, return the four public response fields above, prompt_md null and
ready_for_confirmation false. Develop useful concrete ideas now; missing export metadata
does not block brainstorming. Do not read files or invoke tools to obtain omitted references.
The author's selected operation is CONTINUE DISCUSSION. This operation controls scope even
when there is a complete brief, an existing skeleton, no unanswered questions, or a selected
direction. Choosing a route, answering intake questions, saying continue, or delegating a
city or other detail does NOT approve the whole story or authorize skeleton delivery.
After route selection, develop that route with concrete character conflicts, motivated
choices, major turns and consequences. Offer a manageable, coherent next design proposal
for the author to guide and revise; do not finish all later stages in the same response.
Distinguish confirmed decisions, this round's proposals, and unresolved meaningful choices.
Do not ask routine questions just to fill a round quota or repeat accepted choices.
Discuss changes to an existing skeleton in reply; preserve its saved version with prompt_md
null. Never emit requests_delivery or delivery_mode, and never silently start chapter mapping
or final assembly. The separate Generate/Update Complete Skeleton action authorizes delivery.
If the message asks for a complete artifact in this discussion operation, explain that action
briefly and still give a useful current design proposal. Do not claim to have generated it.
"""

    system = build_system()
    context = author_context(session)
    if delivering and session.get("_previous_candidate"):
        context["previous_unvalidated_skeleton"] = session["_previous_candidate"]
        context["candidate_instruction"] = (
            "This earlier candidate failed validation and is NOT an approved skeleton. "
            "Reuse its story content where consistent with the latest author decisions; "
            "repair its contracts using the engine reference. Return the complete revised skeleton.")
    user = json.dumps(context, ensure_ascii=False)
    LOG.info("workshop.model.start session=%s route=workshop provider=%s model=%s timeout=%s revision=%s",
             session["id"], route["provider"], route["model"], timeout, session["revision"])
    discussion_deadline = session.get("_turn_deadline") or time.monotonic() + timeout
    # Delivery is several bounded requests, not one request spending whatever
    # the first two stages left. The whole job is still capped at three request
    # allowances, measured from the original start (including gate and IO).
    deadline = discussion_deadline
    task_budget = timeout
    attempt = 0
    repair_count = 0
    scope_repairs = 0
    session.setdefault("skill_version", _skill_bundle()[1])
    plan = None
    assembly = None
    assembly_part = None
    assembly_repair_part = None
    if delivering and session.get("_delivery_record"):
        narrative_mode = session["_delivery_record"]["narrative_mode"]
        profile = "delivery"
        plan = DeliveryPlan(session, narrative_mode)
        deadline = discussion_deadline + timeout * 2
        task_budget = timeout * 3
    elif delivering and not (session.get("prompt_md") or session.get("_previous_candidate")):
        # The first real design call selects the narrative mode from the full
        # author context. No separate intent/handoff model call is needed.
        plan = DeliveryPlan(session, None)
        narrative_mode = None
        deadline = discussion_deadline + timeout * 2
        task_budget = timeout * 3

    def stage_request():
        nonlocal assembly, assembly_part
        assert plan is not None
        if plan.stage == "assembly":
            if "assembly" in plan.record["completed"]:
                return "", ""  # Saved final artifacts are verified locally.
            assembly = AssemblyPlan(plan.record["completed"]["story_design"]["content"],
                                    plan.record["completed"]["chapter_plan"]["content"], plan.saved_parts)
            assembly_part = assembly_repair_part or assembly.part
            if assembly_part is None:
                return "", ""  # All pieces are saved; do not ask the model to copy them.
            rules = _skill_bundle("delivery")[0] if "--- SKILL.md ---" in skill else skill
            part_context = assembly.context(plan.context(context), assembly_part)
            part_context["delivery_instruction"] = (
                "Complete only assembly_part using its dedicated JSON schema. The application assembles "
                "the final skeleton from saved parts and unchanged story/chapter text. Do not return "
                "the full skeleton or the four public response fields in this part request.")
            return (rules + _narrative_contract_reference(narrative_mode)
                    + _commercial_contract_reference() + assembly.rules(assembly_part),
                    json.dumps(part_context, ensure_ascii=False))
        # Early stages have creative guidance but no 17-section schema burden.
        rules = _skill_bundle("exploration")[0] if "--- SKILL.md ---" in skill else skill
        return rules + plan.rules(), json.dumps(plan.context(context), ensure_ascii=False)

    if plan is not None:
        system, user = stage_request()
    while True:
        attempt += 1
        cancellation = session.get("_cancellation_event")
        if cancellation is not None and cancellation.is_set():
            raise LLMCancelledError("Workshop inference was paused")
        remaining = min(timeout, deadline - time.monotonic())
        if remaining <= 0:
            if plan is not None:
                raise DeliveryTimeoutError(timeout, task_expired=True)
            raise TimeoutError("Workshop format repair exhausted the turn timeout")
        # SDKs freeze timeout when constructed; a fresh client also gives HTTP
        # repairs the remaining budget instead of another full turn timeout.
        diagnostic = make_diagnostic_callback(session, attempt=attempt, operation="brainstorm",
                                              route={**route, "timeout_seconds": remaining})
        stage = plan.stage if plan is not None else None
        if plan is not None:
            if diagnostic is not None and attempt == 1:
                for saved_stage in plan.record["completed"]:
                    diagnostic(plan.event("reused", saved_stage))
            plan.begin()
            if diagnostic is not None:
                diagnostic(plan.event("started"))
            if stage == "assembly" and assembly_part is not None:
                plan.begin_part(assembly_part)
                if diagnostic is not None:
                    diagnostic(plan.part_event("started", assembly_part))
        if diagnostic is not None:
            diagnostic({"event": "attempt.start", "timeout_seconds": remaining,
                        "task_budget_seconds": task_budget, "request_timeout_seconds": timeout,
                        "prompt_profile": profile, "narrative_mode": narrative_mode,
                        "system_bytes": len(system.encode("utf-8"))})
        try:
            if plan is not None and "assembly" in plan.record["completed"]:
                # Crash after stage save and before session publication: revalidate
                # locally rather than paying for the same completed result again.
                text = json.dumps(plan.record["completed"]["assembly"]["content"], ensure_ascii=False)
            elif stage == "assembly" and assembly_part is None:
                text = json.dumps(assembly.assemble(), ensure_ascii=False)
            else:
                # Saving a checkpoint and diagnostic fsyncs also spend this turn's
                # budget. Never give the child an earlier, stale time allowance.
                remaining = min(timeout, deadline - time.monotonic())
                if remaining <= 0:
                    if plan is not None:
                        raise DeliveryTimeoutError(timeout, task_expired=True)
                    raise TimeoutError("Workshop delivery exhausted the turn timeout")
                client = LLMClient(**client_options, timeout_seconds=remaining,
                                   **({"diagnostic_callback": diagnostic} if diagnostic is not None else {}))
                text = client.complete(system, user).strip()
        except Exception as exc:
            category = diagnostic_error_category(exc)
            if diagnostic is not None:
                diagnostic({"event": f"attempt.{category}" if category in {"timeout", "cancelled"} else "attempt.failed",
                            "error_category": category})
            if plan is not None and category == "timeout" and not isinstance(exc, DeliveryTimeoutError):
                raise DeliveryTimeoutError(timeout, task_expired=time.monotonic() >= deadline) from exc
            raise
        if cancellation is not None and cancellation.is_set():
            if diagnostic is not None:
                diagnostic({"event": "attempt.cancelled", "error_category": "cancelled"})
            raise LLMCancelledError("Workshop inference was paused")
        data = None
        validation_phase = "response_parse"
        try:
            if diagnostic is not None:
                diagnostic({"event": "validation.start", "phase": validation_phase})
            if stage == "assembly" and assembly_part is not None:
                part_data = parse_part(text, assembly_part, narrative_mode, plan.chapter_count)
                assembly.accept(assembly_part, part_data)
                if assembly_repair_part is not None:
                    plan.revise_part(assembly_part, part_data)
                else:
                    plan.finish_part(assembly_part, part_data)
                if diagnostic is not None:
                    diagnostic({"event": "attempt.completed"})
                    diagnostic(plan.part_event("completed", assembly_part))
                assembly_repair_part = None
                for key in ("previous_model_response", "validation_error", "repair_instruction", "repair_requires_skeleton"):
                    context.pop(key, None)
                system, user = stage_request()
                continue
            if plan is not None and stage != "assembly":
                data = parse_stage(text, stage, narrative_mode,
                                   plan.chapter_count if stage == "chapter_plan" else None)
                plan.finish(stage, data)
                narrative_mode = plan.mode
                if diagnostic is not None:
                    diagnostic({"event": "attempt.completed"})
                    diagnostic(plan.event("completed", stage))
                # A repair artifact belongs to its own stage, not the next one.
                for key in ("previous_model_response", "validation_error", "repair_instruction", "repair_requires_skeleton"):
                    context.pop(key, None)
                system, user = stage_request()
                continue
            data = _parse_response(text, session["prompt_md"], discussion=not delivering)
            if not delivering and (data.get("prompt_md") is not None or data["ready_for_confirmation"]
                                   or "requests_delivery" in data or "delivery_mode" in data):
                if diagnostic is not None:
                    diagnostic({"event": "validation.failure", "phase": "response_parse",
                                "error_category": "discussion_scope"})
                    diagnostic({"event": "attempt.failed", "phase": "response_parse",
                                "error_category": "discussion_scope"})
                if scope_repairs:
                    data = {"reply": "本轮模型未按讨论方式返回可用方案，已阻止自动生成或覆盖骨架。"
                            "全部对话与已有骨架均保留。你可以继续讨论；方案成熟后可点击“生成完整骨架”或“更新完整骨架”。",
                            "questions": list(session.get("questions", [])),
                            "prompt_md": None, "ready_for_confirmation": False}
                    if diagnostic is not None:
                        diagnostic({"event": "attempt.completed"})
                    break
                scope_repairs += 1
                if diagnostic is not None:
                    diagnostic({"event": "format_retry", "retry_count": scope_repairs,
                                "error_category": "discussion_scope"})
                # Do not recycle an unsolicited skeleton into the correction,
                # write it as a candidate, or add another delivery budget.
                for key in ("previous_model_response", "validation_error", "repair_requires_skeleton"):
                    context.pop(key, None)
                context["repair_instruction"] = (
                    "The selected operation is discussion, not delivery. Return a substantive next "
                    "story proposal in reply and only necessary questions; prompt_md MUST be null, "
                    "ready_for_confirmation MUST be false. Omit requests_delivery and delivery_mode. "
                    "Do not regenerate the whole skeleton or claim the author approved all design choices.")
                user = json.dumps(context, ensure_ascii=False)
                continue
            if "requests_delivery" in data or "delivery_mode" in data:
                raise ModelResponseError("请返回本次请求的完整内容，不能用内部交付标记代替。")
            if data.get("prompt_md") is not None:
                validation_phase = "skeleton_validation"
                if diagnostic is not None:
                    diagnostic({"event": "validation.start", "phase": validation_phase})
                # Validate inside the bounded repair loop, before publishing any version.
                # Persist failed candidates as well, so a later retry can reuse the work.
                raw_prompt = data["prompt_md"]
                data["prompt_md"], normalized_fields = normalize_workshop_contracts(raw_prompt)
                if normalized_fields and diagnostic is not None:
                    diagnostic({"event": "contract.normalized", "normalized_fields": len(normalized_fields)})
                candidate_path = session.get("_candidate_path")
                if candidate_path is not None:
                    candidate = Path(candidate_path)
                    # Retain the exact model artifact for audit; only the canonical
                    # candidate receives the narrow compatibility normalization.
                    _atomic(candidate.with_name(f"{candidate.stem}-attempt-{attempt}.md"), raw_prompt)
                    _atomic(candidate, data["prompt_md"])
                    validated = _validate_candidate(candidate)
                else:
                    with tempfile.TemporaryDirectory(prefix="workshop-candidate-") as directory:
                        candidate = Path(directory) / "prompt.md"
                        candidate.write_text(data["prompt_md"], encoding="utf-8")
                        validated = _validate_candidate(candidate)
                validation_phase = "input_revision_validation"
                if diagnostic is not None:
                    diagnostic({"event": "validation.start", "phase": validation_phase})
                _validate_input_revision(session, data["prompt_md"], validated)
            elif context.get("repair_requires_skeleton") or (
                session.get("input_needs_revision") and data["ready_for_confirmation"]
            ):
                raise ModelResponseError("本轮需要返回修复后的完整骨架，不能以空骨架或旧版本代替。")
            if plan is not None:
                plan.validate_assembly(data, _skeleton_mode(data.get("prompt_md") or ""))
                if "assembly" not in plan.record["completed"] and plan.saved_parts:
                    plan.clear_part_repair()
                plan.finish("assembly", data)
            if diagnostic is not None:
                diagnostic({"event": "attempt.completed"})
                if plan is not None:
                    diagnostic(plan.event("completed", "assembly"))
            break
        except (json.JSONDecodeError, ModelResponseError, DeliveryError, AssemblyError) as exc:
            if diagnostic is not None:
                category = "json_decode" if isinstance(exc, json.JSONDecodeError) else {
                    "response_parse": "response_schema", "skeleton_validation": "skeleton_contract",
                    "input_revision_validation": "input_revision",
                }[validation_phase]
                diagnostic({"event": "validation.failure", "phase": validation_phase, "error_category": category})
                diagnostic({"event": "attempt.failed", "phase": validation_phase, "error_category": category})
            if repair_count == 2:
                raise
            repair_count += 1
            LOG.warning("workshop.model.format_retry session=%s attempt=%s", session["id"], attempt)
            if diagnostic is not None:
                diagnostic({"event": "format_retry", "retry_count": repair_count, "error_category": category})
            if plan is not None and stage != "assembly":
                context.update(previous_model_response=text, validation_error=str(exc),
                               repair_instruction="Repair this stage's complete JSON and content. Preserve the full author context and saved story stages; do not change the saved mode or chapter count.")
                system, user = stage_request()
                continue
            if plan is not None and "assembly" in plan.record["completed"]:
                # Immutable saved output failing a changed validator must not be
                # silently regenerated during an explicit local publication retry.
                raise ModelResponseError("已保存整合结果未通过当前骨架校验，请重新发起修改；阶段成果仍保留。") from exc
            if plan is not None and stage == "assembly":
                target = assembly_part or repair_part(str(exc))
                if target is None:
                    raise ModelResponseError("本地整合校验未通过，已保存各部分；需要调整对应内容：" + str(exc)) from exc
                if assembly_part is None:
                    assembly_repair_part = target
                context.update(
                    previous_model_response=(text if assembly_part is not None else
                                             json.dumps(plan.saved_parts[target], ensure_ascii=False)),
                    validation_error=("Response is not valid JSON." if isinstance(exc, json.JSONDecodeError) else str(exc)),
                    repair_instruction=("Repair ONLY the requested assembly part. Preserve all saved story and chapter "
                                        "content and compatible parts. Return the part JSON, never the complete skeleton."),
                )
                system, user = stage_request()
                continue
            if data is not None and data.get("prompt_md") is not None:
                # Keep the last complete candidate even if the next attempt
                # fails to produce parseable transport JSON at all.
                context["previous_unvalidated_skeleton"] = data["prompt_md"]
                profile = "delivery"
                narrative_mode = plan.mode if plan is not None else _skeleton_mode(data["prompt_md"]) or narrative_mode
                system = build_system()
            context.update(
                previous_model_response=text,
                validation_error=("Response is not valid JSON." if isinstance(exc, json.JSONDecodeError) else str(exc)),
                repair_instruction=(
                    "Repair the reported format/contract error using the engine reference. "
                    "Return the complete transport object and complete corrected prompt_md, "
                    "preserving the story, author decisions, chapter count, chosen mode and volume spans. "
                    "Check all required serialized fields together, not just the first reported field. "
                    "Do not replace the story with the schema example or an earlier approved version."
                    if profile == "delivery" else
                    "Repair the reported response format. Retain the full author context and return the "
                    "discussion with prompt_md null and ready_for_confirmation false. Never emit "
                    "delivery fields or switch scope. Do not invent missing export schemas."),
                repair_requires_skeleton=context.get("repair_requires_skeleton", False) or (
                    data is not None and data.get("prompt_md") is not None),
            )
            if plan is not None:
                system, user = stage_request()
            else:
                user = json.dumps(context, ensure_ascii=False)
    return data, {"provider": route["provider"], "model": route["model"], "timeout_seconds": timeout}


def _parse_response(text: str, previous_prompt: str, *, discussion: bool = False) -> dict:
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
    if not discussion and data["ready_for_confirmation"] and not (data.get("prompt_md") or previous_prompt):
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
    from .workshop_delivery import DeliveryError
    from .workshop_assembly import AssemblyError
    from .workshop_checkpoints import CheckpointError
    retained = _preserved_context(has_prompt)
    if isinstance(exc, DeliveryTimeoutError):
        scope = (f"整轮交付上限 {exc.request_seconds * 3:g} 秒" if exc.task_expired
                 else f"单次请求上限 {exc.request_seconds:g} 秒")
        return f"骨架交付超时（{scope}）。已完成的阶段和补全部分已保存，可继续未完成部分；不会自动重试模型调用。{retained}"
    if isinstance(exc, (TimeoutError,)) or any(word in str(exc).lower() for word in ("timeout", "timed out")):
        return f"模型响应超时（本轮上限 {timeout_seconds:g} 秒）。{retained}可以携带完整上下文重试。"
    if isinstance(exc, json.JSONDecodeError):
        return f"模型响应不完整或不是有效 JSON。{retained}可以重试。"
    if isinstance(exc, (ModelResponseError, DeliveryError, AssemblyError)):
        detail = str(exc).replace("上一版完整骨架已保留", "").replace("上一版骨架已保留", "").strip("；，。 ")
        return f"{detail[:400]}。{retained}"
    if isinstance(exc, CheckpointError):
        return f"阶段成果未能安全保存或恢复，请检查存储与当前设定；已保存文件保持不变。{retained}"
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
    try:
        contract = NarrativeFormat.from_dict(json.loads(body))
    except KeyError as exc:
        field = str(exc.args[0])
        label = "（总章节数）" if field == "total_chapters" else ""
        raise ValueError(f"NARRATIVE_FORMAT_JSON 缺少必填字段 {field}{label}，请按引擎结构参考补齐。") from None
    source = contract.selection_source
    if source not in {"author_selected", "recommended_then_confirmed"}:
        source = "recommended_then_confirmed"
    data = contract.with_confirmation(source).to_dict()
    return text[:match.start()] + "[NARRATIVE_FORMAT_JSON]\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n[/NARRATIVE_FORMAT_JSON]" + text[match.end():]


def _validate_candidate(path: Path) -> dict:
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
            validated = _handoff().validate(check_path, REPO)
        from core.cover_handoff import parse_cover_handoff
        parse_cover_handoff(text)
    except (ValueError, KeyError, TypeError) as exc:
        raise ModelResponseError(f"模型骨架的结构合同尚不完整：{str(exc)[:240]}；上一版骨架已保留。") from None
    return validated


def _validate_input_revision(snapshot: dict, prompt: str, validated: dict) -> None:
    if not snapshot.get("input_needs_revision"):
        return
    if prompt.strip() == snapshot["prompt_md"].strip():
        raise ModelResponseError("模型返回了未更新的旧骨架，请继续落实已修改的基础设定。")
    pending = snapshot.get("pending_input_fields", [])
    for field, label in (("chapters", "章节数"), ("mode", "故事组织形式")):
        expected = snapshot["input"].get(field)
        if field in pending and expected and validated[field] != expected:
            raise ModelResponseError(f"模型骨架尚未落实新的{label}，请继续推导或修改基础设定。")


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
        with _session_thread_lock(path), (path / ".lock").open("a") as lock:
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

    def _history_path(self, session_id: str, filename: str) -> Path:
        root = self._path(session_id)
        directory = root / "history"
        path = directory / filename
        if directory.is_symlink() or path.is_symlink() or path.resolve().parent != root.resolve() / "history":
            raise HTTPException(409, "历史目录无法安全读取，尚未修改当前讨论。")
        return path

    def _log(self, data: dict, event: str, message: str, **fields):
        entry = {"time": _now(), "event": event, "message": message, **fields}
        data["logs"].append(entry)
        LOG.info("workshop.%s session=%s revision=%s %s", event, data["id"], data["revision"], json.dumps(entry, ensure_ascii=False))

    @staticmethod
    def _delivery_identity(data: dict, record: dict, *, skill_version: str | None = None,
                           allow_published: bool = False) -> None:
        from .workshop_checkpoints import CheckpointError
        from .workshop_delivery import checkpoint_fingerprint

        source_revision = data.get("delivery_source_revision")
        if (not isinstance(record, dict) or type(source_revision) is not int
                or source_revision < 0 or source_revision > data["revision"]
                or record.get("run_id") != data.get("delivery_run_id")
                or record.get("session_id") != data["id"]
                or record.get("source_revision") != source_revision):
            raise CheckpointError("Delivery checkpoint does not match the current discussion.")
        mode = record.get("narrative_mode")
        if not isinstance(mode, str) or mode not in _NARRATIVE_MODES:
            raise CheckpointError("Delivery checkpoint narrative mode is invalid.")
        published = (allow_published and data.get("delivery_published_run_id") == record["run_id"]
                     and "assembly" in record.get("completed", {})
                     and (data.get("status") in {"ready", "launched"}
                          or data.get("active_operation") == "titles"))
        # Published artifacts remain readable after a later naming operation or
        # Skill update; only active recovery requires the current exact version.
        expected_skill = record["skill_version"] if published else skill_version or data["skill_version"]
        if record.get("skill_version") != expected_skill:
            raise CheckpointError("Delivery checkpoint uses a different skill version.")
        if not published and record.get("input_sha256") != checkpoint_fingerprint(data, expected_skill):
            raise CheckpointError("Delivery checkpoint author context has changed.")

    def _load_delivery_checkpoint(self, data: dict, *, skill_version: str | None = None,
                                  allow_published: bool = False) -> dict | None:
        from .workshop_checkpoints import CheckpointError, load_checkpoint

        run_id = data.get("delivery_run_id")
        if run_id is None and data.get("delivery_source_revision") is None:
            return None
        source_revision = data.get("delivery_source_revision")
        if type(source_revision) is not int or not 0 <= source_revision <= data["revision"]:
            raise CheckpointError("Delivery checkpoint source revision is invalid.")
        if data.get("restart_required"):
            raise CheckpointError("Delivery checkpoint cannot resume after basic settings changed.")
        record = load_checkpoint(self._path(data["id"]) / "delivery", run_id)
        if record is not None:
            self._delivery_identity(data, record, skill_version=skill_version, allow_published=allow_published)
        return record

    def delivery_progress(self, data: dict) -> dict | None:
        """Pure checkpoint read: no model invocation or checkpoint repair on GET."""
        from .workshop_checkpoints import CheckpointError, project_checkpoint

        if data.get("delivery_run_id") is None and data.get("delivery_source_revision") is None:
            return None
        try:
            _, current_skill_version = _skill_bundle("delivery")
            record = self._load_delivery_checkpoint(data, skill_version=current_skill_version, allow_published=True)
            return project_checkpoint(record, data["status"], data["delivery_source_revision"]) if record else None
        except (CheckpointError, OSError, ValueError, TypeError):
            return {"status": "error", "resumable": False, "completed_stages": [],
                    "active_stage": None, "stages": [],
                    "invalid_reason": "已保存的阶段与当前设定或 Skill 不匹配，或记录未通过完整性检查；请重新推导。旧文件已保留。"}

    def _save_delivery_checkpoint(self, snapshot: dict, record: dict, cancel_event: threading.Event) -> None:
        from core.llm_client import LLMCancelledError
        from .workshop_checkpoints import save_checkpoint

        with self._locked(snapshot["id"]) as data:
            if cancel_event.is_set() or not self._current_operation(data, snapshot):
                raise LLMCancelledError("Workshop delivery checkpoint belongs to an inactive request")
            self._delivery_identity(data, record)
            save_checkpoint(self._path(data["id"]) / "delivery", data["delivery_run_id"], record)

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
            if data["status"] == "running":
                job = runner.get(data.get("job_id") or "")
                if job is None or job["status"] in {"done", "error"}:
                    data["status"] = "error"
                    reason = ("服务重启中断了本轮讨论。" if job is None else
                              "后台任务已结束，但本轮未保存有效结果。")
                    data["last_error"] = reason + _preserved_context(bool(data.get("prompt_md"))) + "请重试。"
                    # Do not expose raw job errors: provider failures may include
                    # request content or credentials. Retain only safe metadata.
                    self._log(data, "interrupted", data["last_error"],
                              job_id=data.get("job_id"), job_status=job["status"] if job else "missing")
                    self._save(data)
            return data

    def list_sessions(self, *, q: str = "", limit: int = 50, offset: int = 0, status: str = "") -> dict:
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
                        or data.get("status") not in {"draft", "running", "paused", "ready", "error", "launched"}
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
                if status and data["status"] != status:
                    continue
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
                    "restart_required": bool(data.get("restart_required")),
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
            if data.get("restart_required"):
                raise HTTPException(409, "基础设定已保存，请确认从头推导后继续；旧讨论不会自动用于新设定。")
            complete_input = all(data["input"].get(key) for key in
                                 ("chapters", "words_per_chapter", "language", "mode", "audience", "market"))
            phase = "revision" if data["prompt_md"] else "design" if data["messages"] or complete_input else "intake"
            skill, version = _skill_bundle(phase)
            timeout = _timeout()
            data["revision"] += 1
            data["messages"].append({"role": "user", "content": body.message.strip()})
            data.update(status="running", last_error=None, prepared=None, skill_version=version,
                        turn_intent=body.intent,
                        turn_timeout_seconds=timeout, active_operation="brainstorm", ready_for_confirmation=False,
                        paused_at=None, resumed_from_revision=None, delivery_run_id=uuid.uuid4().hex,
                        delivery_source_revision=data["revision"], delivery_published_run_id=None)
            if data.get("naming"):
                data["naming"].update(source_sha256="", confirmed_title=None, candidates=[])
            self._log(data, "turn_started", "模型正在按小说骨架 Skill 处理本轮意见。", timeout_seconds=timeout)
            return self._submit_inference(data, skill=skill)

    def restart(self, session_id: str, body: RevisionInput) -> dict:
        """Start a new reasoning round only after the author confirms saved basics."""
        with self._locked(session_id) as data:
            if data.get("restarted_from_revision") == body.revision:
                job = runner.get(data.get("restart_job_id") or "")
                if job is not None:
                    return job
                raise HTTPException(409, "这次从头推导已提交，请刷新查看保存的讨论状态。")
            self._available(data, body.revision)
            if not data.get("restart_required"):
                raise HTTPException(409, "请先保存修改后的基础设定，再确认从头推导。")
            skill, version = _skill_bundle("design")
            # Archive before clearing context. A failed durable write cannot start a model call.
            archived = json.loads(json.dumps(data))
            pending_revision = data.get("pending_restart_revision")
            if pending_revision is not None:
                if type(pending_revision) is not int or pending_revision < 0:
                    raise HTTPException(409, "旧讨论归档标识无效，尚未开始新的推导。")
                source_path = self._history_path(session_id, f"before-edit-{pending_revision}.json")
                try:
                    archived = json.loads(source_path.read_text(encoding="utf-8"))
                    if archived.get("id") != session_id or archived.get("revision") != pending_revision:
                        raise ValueError("Archive identity mismatch")
                except (OSError, ValueError, AttributeError):
                    raise HTTPException(409, "旧讨论归档无法读取，尚未开始新的推导。") from None
                archived["original_revision"] = pending_revision
                archived["revision"] = body.revision
                archived["replacement_input"] = data["input"]
            archived.pop("history", None)
            archived_at = _now()
            _atomic(self._history_path(session_id, f"{body.revision}.json"),
                    json.dumps(archived, ensure_ascii=False, indent=2))
            history = list(data.get("history", []))
            history.append({"revision": body.revision, "archived_at": archived_at,
                            "message_count": len(archived["messages"]), "has_prompt": bool(archived["prompt_md"]),
                            "title": archived["input"].get("title") or "未命名讨论"})
            data["revision"] += 1
            data.update(messages=[{"role": "user", "content": "请只依据当前基础设定，从头开始头脑风暴；提出有因果差异的故事方向并完善人物、冲突与结局。"}],
                        turn_intent="discuss",
                        prompt_md="", questions=[], naming=None, prepared=None, logs=[], history=history,
                        initial_input=json.loads(json.dumps(data["input"])), status="running", last_error=None,
                        skill_version=version, turn_timeout_seconds=_timeout(), active_operation="brainstorm",
                        ready_for_confirmation=False, paused_at=None, resumed_from_revision=None,
                        restart_required=False, pending_restart_revision=None, input_needs_revision=True,
                        restarted_from_revision=body.revision, context_start_revision=data["revision"],
                        delivery_run_id=uuid.uuid4().hex, delivery_source_revision=data["revision"],
                        delivery_published_run_id=None)
            self._log(data, "turn_started", "已按确认后的基础设定从头推导；旧讨论仅保留在历史中。",
                      timeout_seconds=data["turn_timeout_seconds"], restarted=True)
            return self._submit_inference(data, skill=skill)

    def history(self, session_id: str, revision: int) -> dict:
        with self._locked(session_id) as data:
            if not any(item.get("revision") == revision for item in data.get("history", [])):
                raise HTTPException(404, "找不到这版历史讨论。")
            path = self._history_path(session_id, f"{revision}.json")
            try:
                archived = json.loads(path.read_text(encoding="utf-8"))
                if archived.get("id") != session_id or archived.get("revision") != revision:
                    raise ValueError("Archive identity mismatch")
                return archived
            except (OSError, ValueError, AttributeError):
                raise HTTPException(409, "这版历史讨论暂时无法读取；当前讨论未受影响。") from None

    def _submit_inference(self, data: dict, *, skill: str | None = None) -> dict:
        """Called under the session lock; freeze identity before the thread starts."""
        session_id, revision = data["id"], data["revision"]
        cancel_event = threading.Event()
        titles = data.get("active_operation") == "titles"
        fn = (lambda: self._run_titles(session_id, revision, cancel_event)) if titles else (
            lambda: self._run_turn(session_id, skill or "", revision, cancel_event))
        job_id = runner.submit("workshop.titles" if titles else "workshop.turn", fn,
                               {"session_id": session_id}, unique_key=f"workshop:{self._path(session_id)}:{revision}",
                               cancel_event=cancel_event)
        data["job_id"] = job_id
        if data.get("context_start_revision") == revision and data.get("restarted_from_revision") == revision - 1:
            data["restart_job_id"] = job_id
        self._save(data)
        return runner.get(job_id)

    @staticmethod
    def _current_operation(data: dict, snapshot: dict) -> bool:
        return (data["status"] == "running" and data["revision"] == snapshot["revision"]
                and data["job_id"] == snapshot["job_id"])

    def pause(self, session_id: str, body: PauseInput) -> dict:
        with self._locked(session_id) as data:
            # The job identity makes an immediate pause safe even before the first
            # poll has replaced the client's optimistic pre-submit revision.
            if body.job_id != data.get("job_id") or data["status"] == "launched":
                raise HTTPException(409, "当前推导任务已变化，请刷新会话后重试。")
            if body.revision > data["revision"]:
                raise HTTPException(409, "会话版本不匹配，请刷新后重试。")
            if data["status"] != "running":
                return data
            data["revision"] += 1
            data.update(status="paused", last_error=None, paused_at=_now())
            self._log(data, "turn_paused", "已暂停本轮推导。已保存的上下文保留，继续时重新请求未完成的内容。",
                      operation=data.get("active_operation", "brainstorm"))
            self._save(data)
            # Signals only; process cleanup happens outside the file lock.
            runner.cancel(body.job_id)
            return data

    def resume(self, session_id: str, body: RevisionInput) -> dict:
        from .workshop_checkpoints import CheckpointError

        with self._locked(session_id) as data:
            if data["status"] == "running" and data.get("resumed_from_revision") == body.revision:
                return runner.get(data["job_id"])
            self._available(data, body.revision)
            if data.get("restart_required"):
                raise HTTPException(409, "基础设定已修改，请确认从头推导；旧阶段不会用于新设定。")
            if data["status"] not in {"paused", "error"}:
                raise HTTPException(409, "仅暂停或已有保存阶段的失败推导可以继续，请刷新当前会话。")
            skill, version = _skill_bundle("revision" if data["prompt_md"] else "design")
            try:
                checkpoint = (None if data.get("active_operation") == "titles" else
                              self._load_delivery_checkpoint(data, skill_version=version))
            except CheckpointError:
                raise HTTPException(409, "已保存阶段与当前设定或 Skill 不匹配，或完整性检查失败；请重新推导，旧记录已保留。") from None
            if data["status"] == "error" and (not checkpoint or not checkpoint["completed"]):
                raise HTTPException(409, "本轮尚未保存可恢复的阶段，请重新提交讨论要求。")
            data["revision"] += 1
            data["turn_intent"] = "deliver" if checkpoint else data.get("turn_intent", "discuss")
            data.update(status="running", last_error=None, paused_at=None, prepared=None,
                        turn_timeout_seconds=_timeout(), skill_version=version, resumed_from_revision=body.revision)
            if data.get("active_operation") != "titles" and not data.get("delivery_run_id"):
                data.update(delivery_run_id=uuid.uuid4().hex, delivery_source_revision=data["revision"],
                            delivery_published_run_id=None)
            self._log(data, "turn_started", "正在恢复已保存的阶段，仅继续尚未完成的内容。" if checkpoint else
                      "正在使用已保存的完整上下文，重新处理暂停前尚未完成的要求。",
                      operation=data.get("active_operation", "brainstorm"), timeout_seconds=data["turn_timeout_seconds"], resumed=True)
            return self._submit_inference(data, skill=skill)

    @scoped_usage("brainstorming", lambda a: a["self"]._path(a["session_id"]))
    def _run_turn(self, session_id: str, skill: str, revision: int, cancel_event: threading.Event):
        started = time.monotonic()
        with self._locked(session_id) as data:
            if data["status"] != "running" or data["revision"] != revision or cancel_event.is_set():
                LOG.info("workshop.worker.skipped session=%s expected_revision=%s revision=%s status=%s cancelled=%s",
                         session_id, revision, data["revision"], data["status"], cancel_event.is_set())
                return
            snapshot = dict(data)
        snapshot["_cancellation_event"] = cancel_event
        snapshot["_diagnostic_dir"] = self._path(session_id) / "diagnostics"
        snapshot["_candidate_path"] = self._path(session_id) / "candidates" / f"{revision}.md"
        snapshot["_inference_lock_path"] = self._path(session_id) / ".inference.lock"
        snapshot["_save_delivery_checkpoint"] = lambda record: self._save_delivery_checkpoint(snapshot, record, cancel_event)
        try:
            snapshot["_delivery_record"] = self._load_delivery_checkpoint(snapshot)
            try:
                earlier = sorted(
                    (path for path in snapshot["_candidate_path"].parent.glob("*.md")
                     if path.stem.isdigit() and int(snapshot.get("context_start_revision", 0)) <= int(path.stem) < revision),
                    key=lambda path: int(path.stem), reverse=True,
                )
                if snapshot.get("turn_intent") == "deliver" and earlier and not snapshot["_delivery_record"]:
                    previous_candidate = earlier[0].read_text(encoding="utf-8")
                    if previous_candidate != snapshot["prompt_md"]:
                        snapshot["_previous_candidate"] = previous_candidate
            except (OSError, UnicodeError):
                # Diagnostic candidates are optional. A corrupt one must not
                # strand the session or prevent use of the saved conversation.
                LOG.warning("workshop.candidate.unreadable session=%s", session_id)
            result, route = _complete(snapshot, skill)
            if cancel_event.is_set():
                return
            if snapshot.get("input_needs_revision") and result["ready_for_confirmation"] and result.get("prompt_md") is None:
                raise ModelResponseError("基础设定已修改，模型尚未返回更新后的完整骨架。请继续推导。")
            if result.get("prompt_md") is not None:
                candidate = self._path(session_id) / "candidates" / f'{snapshot["revision"]}.md'
                _atomic(candidate, result["prompt_md"])
                validated = _validate_candidate(candidate)
                _validate_input_revision(snapshot, result["prompt_md"], validated)
            with self._locked(session_id) as data:
                if not self._current_operation(data, snapshot):
                    return
                checkpoint = self._load_delivery_checkpoint(data)
                if checkpoint and "assembly" in checkpoint["completed"] and result.get("prompt_md") is not None:
                    data["delivery_published_run_id"] = checkpoint["run_id"]
                data["revision"] += 1
                if result.get("prompt_md") is not None:
                    data["prompt_md"] = result["prompt_md"]
                    data["input_needs_revision"] = False
                    data["pending_input_fields"] = []
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
                if not self._current_operation(data, snapshot):
                    return
                data.update(status="error", last_error=message)
                self._log(data, "turn_failed", message, error_type=type(exc).__name__,
                          elapsed_seconds=round(time.monotonic()-started, 2), **snapshot.get("_model_log", {}))
                self._save(data)
            raise RuntimeError(message) from None

    def update_input(self, session_id: str, body: UpdateInput) -> dict:
        with self._locked(session_id) as data:
            self._available(data, body.revision)
            new_input = body.input.model_dump()
            changes = {key: {"before": data["input"].get(key), "after": value}
                       for key, value in new_input.items() if data["input"].get(key) != value}
            if not changes:
                return data
            if data.get("pending_restart_revision") is None:
                _atomic(self._history_path(session_id, f'before-edit-{data["revision"]}.json'),
                        json.dumps(data, ensure_ascii=False, indent=2))
                data["pending_restart_revision"] = data["revision"]
            data.setdefault("initial_input", json.loads(json.dumps(data["input"])))
            data["pending_input_fields"] = sorted(set(data.get("pending_input_fields", [])) | set(changes))
            data["input"] = new_input
            data["revision"] += 1
            data.update(status="draft", last_error=None, prepared=None, ready_for_confirmation=False,
                        active_operation="brainstorm", input_needs_revision=True, restart_required=True,
                        paused_at=None, resumed_from_revision=None, delivery_run_id=None,
                        delivery_source_revision=None, delivery_published_run_id=None)
            if data.get("naming"):
                data["naming"].update(source_sha256="", confirmed_title=None, candidates=[])
            data["messages"].append({"role": "user", "content": "已修改基础设定，等待作者确认是否从头推导：\n\n```json\n"
                                     + json.dumps(changes, ensure_ascii=False, indent=2) + "\n```"})
            self._log(data, "input_updated", "基础设定已保存，尚未调用模型；等待确认从头推导，历史和用量保留。", fields=list(changes))
            self._save(data)
            return data

    def titles(self, session_id: str, body: TitleInput) -> dict:
        from .workshop_titles import clean_title
        current_title = clean_title(body.current_title) if body.current_title.strip() else ""
        with self._locked(session_id) as data:
            self._available(data, body.revision)
            if not data["prompt_md"] or not data["ready_for_confirmation"] or data["questions"]:
                raise HTTPException(422, "请先完成并确认小说骨架，再推荐书名。")
            confirmed = _confirm_format(data["prompt_md"])
            with tempfile.TemporaryDirectory(prefix="workshop-title-check-") as directory:
                candidate = Path(directory) / "prompt.md"
                candidate.write_text(confirmed, encoding="utf-8")
                _validate_candidate(candidate)
            digest = hashlib.sha256(confirmed.encode()).hexdigest()
            naming = data.get("naming") or {"requests": [], "candidates": [], "confirmed_title": None}
            if naming.get("source_sha256") != digest:
                naming.update(candidates=[], confirmed_title=None)
            naming["source_sha256"] = digest
            naming["requests"].append({"instructions": body.instructions.strip(), "created_at": _now(),
                                       "current_title": current_title, "source_sha256": digest})
            data["revision"] += 1
            data.update(prompt_md=confirmed, naming=naming, active_operation="titles", status="running",
                        last_error=None, prepared=None, turn_timeout_seconds=_timeout(), paused_at=None, resumed_from_revision=None)
            self._log(data, "turn_started", "已确认当前骨架，正在根据命名要求推荐书名。", operation="titles",
                      timeout_seconds=data["turn_timeout_seconds"])
            return self._submit_inference(data)

    @scoped_usage("brainstorming", lambda a: a["self"]._path(a["session_id"]))
    def _run_titles(self, session_id: str, revision: int, cancel_event: threading.Event):
        from .workshop_titles import complete_titles
        started = time.monotonic()
        with self._locked(session_id) as data:
            if data["status"] != "running" or data["revision"] != revision or cancel_event.is_set():
                LOG.info("workshop.worker.skipped session=%s expected_revision=%s revision=%s status=%s cancelled=%s",
                         session_id, revision, data["revision"], data["status"], cancel_event.is_set())
                return
            snapshot = json.loads(json.dumps(data))
        snapshot["_cancellation_event"] = cancel_event
        snapshot["_diagnostic_dir"] = self._path(session_id) / "diagnostics"
        try:
            rules = (SKILL / "references" / "title-naming.md").read_text(encoding="utf-8")
            candidates, route = complete_titles(snapshot, rules)
            with self._locked(session_id) as data:
                if not self._current_operation(data, snapshot):
                    return
                data["naming"]["candidates"] = candidates
                data["naming"]["requests"][-1]["candidates"] = candidates
                data["revision"] += 1
                data.update(status="ready", last_error=None)
                self._log(data, "turn_completed", "书名建议已生成，请选择或修改后确认最终书名。", operation="titles",
                          elapsed_seconds=round(time.monotonic()-started, 2), **route)
                self._save(data)
        except Exception as exc:
            message = "书名推荐未完成。" + _failure(exc, timeout_seconds=float(snapshot["turn_timeout_seconds"]), has_prompt=True)
            with self._locked(session_id) as data:
                if self._current_operation(data, snapshot):
                    data.update(status="error", last_error=message)
                    data["naming"]["requests"][-1]["error"] = message
                    self._log(data, "turn_failed", message, operation="titles", elapsed_seconds=round(time.monotonic()-started, 2))
                    self._save(data)
            raise RuntimeError(message) from None

    def prepare(self, session_id: str, body: PrepareInput) -> dict:
        with self._locked(session_id) as data:
            self._available(data, body.revision)
            if not data["prompt_md"] or not data["ready_for_confirmation"] or data["questions"]:
                raise HTTPException(422, "请先完成必要信息和小说骨架，再确认并生成启动命令。")
            confirmed = _confirm_format(data["prompt_md"])
            if data.get("naming") and body.title is None:
                raise HTTPException(422, "请选择或输入最终书名。")
            if body.title is not None:
                from .workshop_titles import rename_prompt
                confirmed = rename_prompt(confirmed, body.title)
            helper = _handoff()
            try:
                with tempfile.TemporaryDirectory(prefix="workshop-prepare-") as directory:
                    candidate = Path(directory) / "prompt.md"
                    candidate.write_text(confirmed, encoding="utf-8")
                    validated = helper.validate(candidate, REPO)
                    name = _slug(validated["title"]) + "-" + session_id[:8]
                    command, _ = helper.command(candidate, REPO, name, method_mode=data["input"].get("method_mode", "advisory"))
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(422, f"骨架格式尚需修正：{exc}") from exc
            _atomic(self._path(session_id) / "prepared.md", confirmed)
            # Keep shell quoting from the verified helper, replacing only final launcher line.
            filename = name + ".md"
            command = command.rsplit("\n", 1)[0] + "\n./deploy.sh novel " + shlex.quote("./prompt/" + filename)
            validated.pop("prompt", None)
            if confirmed != data["prompt_md"] and body.title is not None:
                data["revision"] += 1
            data["prompt_md"] = confirmed
            if body.title is not None:
                naming = data.get("naming") or {"requests": [], "candidates": []}
                naming.update(confirmed_title=validated["title"], source_sha256=hashlib.sha256(confirmed.encode()).hexdigest())
                data["naming"] = naming
                self._log(data, "title_confirmed", "用户已确认最终书名。", title=validated["title"])
            data.update(status="ready", last_error=None, input_needs_revision=False)
            _atomic(self._path(session_id) / "versions" / f'{data["revision"]}.md', confirmed)
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
                  status: Literal["", "draft", "running", "paused", "ready", "error", "launched"] = "",
                  service: WorkshopService = Depends(get_workshop_service)):
    return service.list_sessions(q=q, limit=limit, offset=offset, status=status)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, service: WorkshopService = Depends(get_workshop_service)):
    data = service.get(session_id)
    progress = runtime_progress(data, service._path(session_id) / "diagnostics")
    delivery = service.delivery_progress(data)
    result = {**data, "runtime_progress": progress} if progress is not None else data
    return {**result, "delivery_progress": delivery} if delivery is not None else result


@router.post("/sessions/{session_id}/turns", status_code=202)
def add_turn(session_id: str, body: TurnInput, service: WorkshopService = Depends(get_workshop_service)):
    return service.turn(session_id, body)


@router.post("/sessions/{session_id}/pause")
def pause_session(session_id: str, body: PauseInput, service: WorkshopService = Depends(get_workshop_service)):
    return service.pause(session_id, body)


@router.post("/sessions/{session_id}/resume", status_code=202)
def resume_session(session_id: str, body: RevisionInput, service: WorkshopService = Depends(get_workshop_service)):
    return service.resume(session_id, body)


@router.post("/sessions/{session_id}/restart", status_code=202)
def restart_session(session_id: str, body: RevisionInput, service: WorkshopService = Depends(get_workshop_service)):
    return service.restart(session_id, body)


@router.get("/sessions/{session_id}/history/{revision}")
def get_session_history(session_id: str, revision: int, service: WorkshopService = Depends(get_workshop_service)):
    return service.history(session_id, revision)


@router.post("/sessions/{session_id}/prepare")
def prepare_session(session_id: str, body: PrepareInput, service: WorkshopService = Depends(get_workshop_service)):
    try:
        return service.prepare(session_id, body)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"骨架格式尚需修正：{exc}") from exc


@router.post("/sessions/{session_id}/input")
def update_session_input(session_id: str, body: UpdateInput, service: WorkshopService = Depends(get_workshop_service)):
    return service.update_input(session_id, body)


@router.post("/sessions/{session_id}/titles", status_code=202)
def suggest_titles(session_id: str, body: TitleInput, service: WorkshopService = Depends(get_workshop_service)):
    try:
        return service.titles(session_id, body)
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
