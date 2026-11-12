"""Optional, source-bound design advice. Never author approval or delivery authority."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

VERSION = "guided-choices.v1"
AREAS = ("premise", "characters", "plot", "ending")
RULES = """
GUIDED CHOICES AND CONVERGENCE (guided-choices.v1):
Help the author choose; do not expect them to invent the plot. At a consequential
unresolved fork, offer 2-3 concrete alternatives with choices, costs, downstream
effects and a justified recommendation. Usually one decision group; at most two
closely related groups. Keep settled story choices. Local edits and explanations
need no invented options. Give concise option summaries; put detailed development
in reply without repeating every option word-for-word. Free author additions can
revise any option. Combining incompatible options needs a coherent proposal, not
automatic adoption of both. 'Continue' alone is not approval of every alternative.

Prioritize a complete causal route from opening through major turns to climax and
ending before expanding procedures, institutions or minor subplots. If recent
turns develop the same local issue without advancing the whole-book route, address
the most important remaining gap. Do not add a new branch just to keep discussing.
Recommend generating the skeleton once core choices are settled/delegated, the
major causal route and ending are coherent, and no identity-changing issue remains.
Names, dialogue, detailed chapter plans and export JSON can be completed during
explicit delivery. No minimum/maximum discussion rounds or literary pass score.
If ready, stop expanding and explain why generating is the useful next action.
This recommendation NEVER sets ready_for_confirmation=true, writes prompt_md, or
requests delivery. Only the author's explicit deliver operation does that.

Add OPTIONAL design_notes.guidance in the SAME response, without another call:
{
 "groups":[{"topic":"one consequential choice", "why":"why decide it now",
   "option_indexes":[1,2], "recommended_index":1}],
 "coverage":[{"area":"premise|characters|plot|ending",
   "status":"missing|proposed|settled|conflict", "summary":"concise current design",
   "evidence":[{"message_index":0,"quote":"exact author words"}]}],
 "next_action":"choose|refine|generate", "reason":"why this next action",
 "missing":["only core decisions still needed"]
}
Return exactly one coverage item per area. premise includes genre's causal role;
characters includes goals/relationships/opposition; plot includes major turns and
climax; ending includes choice, costs, payoff and fit to length/reader experience.
Option indexes are 1-based positions in this response's proposals; the server
assigns IDs. Each group is single-choice, 2-3 options, no overlapping groups; at
most 6 proposals overall (this replaces the default three-proposal limit).
Recommendations must belong to their group. Use null
recommended_index when no justified recommendation; never preselect for the author.
Omit groups for edits/answers/convergence. At convergence use proposals:[] and
questions:[] unless a real unresolved decision remains; next_focus may be omitted.
Evidence uses zero-based indexes into conversation, USER messages only, with exact
quotes. For explicit initial_brief facts use {"input_field":"premise","quote":"exact
text"}. Cite only evidence relevant to the specific claim and its scope. A proposed
plot, a generic 'continue', or permission to brainstorm cannot prove adoption of
specific choices. Later author changes supersede earlier choices; mark affected
items proposed/conflict until coordinated. settled requires explicit author words,
an adopted proposal, or scoped delegation. This is advisory interpretation, not
permission to fabricate author decisions. These private fields never go in exports.
"""


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Evidence(_Record):
    message_index: int | None = Field(default=None, ge=0)
    input_field: Literal["premise", "constraints", "audience", "market", "language", "mode", "title"] | None = None
    quote: str = Field(min_length=1, max_length=1200)


class SourceRouteEvidence(_Record):
    author_action_ref: str = Field(min_length=8, max_length=64)


class Coverage(_Record):
    area: Literal["premise", "characters", "plot", "ending"]
    status: Literal["missing", "proposed", "settled", "conflict"]
    summary: str = Field(min_length=1, max_length=1400)
    evidence: list[Evidence] = Field(default_factory=list, max_length=4)


class Group(_Record):
    topic: str = Field(min_length=1, max_length=300)
    why: str = Field(min_length=1, max_length=700)
    option_indexes: list[int] = Field(min_length=2, max_length=3)
    recommended_index: int | None = None


class Guidance(_Record):
    groups: list[Group] = Field(default_factory=list, max_length=2)
    coverage: list[Coverage] = Field(default_factory=list, max_length=4)
    next_action: Literal["choose", "refine", "generate"]
    reason: str = Field(min_length=1, max_length=1200)
    missing: list[str] = Field(default_factory=list, max_length=6)


def enabled(design: dict | None) -> bool:
    return (design or {}).get("policy", {}).get("guided_choices_version") == VERSION


def enable(design: dict) -> dict:
    """Explicit upgrade or new design only; preserve frozen type references."""
    updated = deepcopy(design)
    if not enabled(updated):
        updated["policy"]["guided_choices_version"] = VERSION
        updated["policy"]["rules"] += RULES
        updated["policy_sha256"] = hashlib.sha256(json.dumps(
            updated["policy"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return updated


def _evidence_valid(item: Evidence, session: dict, assistant_index: int) -> bool:
    if isinstance(item, SourceRouteEvidence):
        from core.source_route_context import enabled
        source = session.get('source_context')
        return (enabled(source) and source.get('selection', {}).get('scope') == 'discussion_direction'
                and source['selection'].get('action_id') == item.author_action_ref
                and item.author_action_ref in session.get('source_route_operations', {}))
    if (item.message_index is None) == (item.input_field is None):
        return False
    if item.input_field is not None:
        value = session["input"].get(item.input_field)
        return isinstance(value, str) and item.quote in value
    if item.message_index >= assistant_index:
        return False
    message = session["messages"][item.message_index]
    from core.author_requirements import author_text
    return item.quote in author_text(message)


def record_guidance(raw: object, record: dict, session: dict, questions: list) -> dict:
    """Bad advice cannot discard usable proposals or trigger paid repair."""
    unavailable = {"status": "unavailable", "reviewed_revision": session["revision"],
                   "notice": "本轮设计进展未完整返回，可继续讨论或主动生成骨架。"}
    try:
        advice = Guidance.model_validate(raw)
        from . import workshop_story
        story_workspace = workshop_story.enabled(session.get('creative_design'))
        if story_workspace and len(advice.groups) > 1:
            raise ValueError('one major decision per round')
        if (advice.coverage or not story_workspace) and {item.area for item in advice.coverage} != set(AREAS):
            raise ValueError("duplicate coverage")
        if any(not s.strip() or len(s) > 1000 for s in advice.missing):
            raise ValueError("invalid missing decision")
        proposals = record["proposals"]
        groups, used = [], set()
        for index, group in enumerate(advice.groups):
            options = group.option_indexes
            if (len(set(options)) != len(options) or set(options) & used
                    or any(i < 1 or i > len(proposals) for i in options)
                    or group.recommended_index is not None and group.recommended_index not in options):
                raise ValueError("invalid option references")
            used.update(options)
            groups.append({"id": f"r{record['source_revision']}:g{index + 1}",
                           "topic": group.topic, "why": group.why,
                           "option_ids": [proposals[i - 1]["id"] for i in options],
                           "recommended_id": proposals[group.recommended_index - 1]["id"] if group.recommended_index else None})
        coverage = []
        for item in advice.coverage:
            if any(not _evidence_valid(e, session, record["message_index"]) for e in item.evidence):
                raise ValueError("unverifiable author evidence")
            entry = item.model_dump(exclude_none=True)
            if entry["status"] == "settled" and not item.evidence:
                entry["status"] = "proposed"
            coverage.append(entry)
        # These are eligibility checks for a recommendation, not semantic proof.
        from core.story_design import enabled as story_first
        # Story-first sessions intentionally defer optional execution settings:
        # chapter count comes from the separately adopted capacity plan. Their
        # readiness is finalized against this round's validated story map below.
        complete_basics = story_first(session) or all(session["input"].get(k) for k in
                              ("chapters", "words_per_chapter", "language", "mode", "audience", "market"))
        can_recommend = (advice.next_action == "generate" and not groups and not proposals
                         and not advice.missing and not questions and not record.get("conflicts")
                         and complete_basics
                         and (story_workspace or all(c["status"] == "settled" for c in coverage)))
        # Modern guidance is finalized against this round's validated story map
        # by record_story before persistence. Coverage is only for older clients.
        action = "generate" if can_recommend else "choose" if groups else "refine"
        reason = advice.reason
        if advice.next_action == "generate" and not can_recommend:
            reason = "当前仍有待确认内容或缺少作者依据，建议先协调；也可主动要求生成骨架。"
        return {"status": "assessed", "reviewed_revision": session["revision"],
                "groups": groups, "coverage": [] if story_workspace else coverage, "next_action": action,
                "reason": reason, "missing": advice.missing}
    except (ValidationError, ValueError, TypeError, IndexError, KeyError):
        return unavailable


def selected_options(session: dict, proposal_ids: list[str]) -> list[dict]:
    """Validate the entire selection before any author decision is appended."""
    design = session.get("creative_design") or {}
    from . import workshop_story
    if workshop_story.enabled(design):
        return workshop_story.selected(session, proposal_ids)
    rounds = design.get("rounds", [])
    if not enabled(design) or not rounds or len(set(proposal_ids)) != len(proposal_ids):
        raise ValueError("方案选择已变化，请刷新当前讨论。")
    latest = rounds[-1]
    groups = (latest.get("guidance") or {}).get("groups", [])
    mapping = {p["id"]: p for p in latest["proposals"]}
    seen = set()
    for pid in proposal_ids:
        group = next((g for g in groups if pid in g["option_ids"]), None)
        if group is None or pid not in mapping or group["id"] in seen:
            raise ValueError("同一问题只能选择一个方案；组合想法请写在补充中。")
        seen.add(group["id"])
    return [mapping[pid] for pid in proposal_ids]


def supersede_siblings(design: dict, proposal_id: str, decision: dict) -> None:
    """An explicit exclusive choice replaces a sibling, preserving the ledger."""
    if decision["status"] not in {"accepted", "revised"}:
        return
    group = next((g for r in design.get("rounds", []) for g in
                  (r.get("guidance") or {}).get("groups", []) if proposal_id in g["option_ids"]), None)
    if group is None:
        return
    latest = {d["proposal_id"]: d for d in design["decisions"]}
    for sibling in group["option_ids"]:
        if sibling != proposal_id and latest.get(sibling, {}).get("status") in {"accepted", "revised"}:
            design["decisions"].append({**decision, "proposal_id": sibling, "status": "deferred",
                                       "note": "已改选同一问题的另一方案。", "superseded_by": proposal_id})
