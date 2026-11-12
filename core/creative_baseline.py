"""Lossless, derived creative authority for original and reference-based workshops.

This is a projection of existing author input and decisions, not another canon.
It never adopts a model proposal by inference, rewrites a historical source hash,
or treats a complete-looking story map as proof of author approval.
"""
from __future__ import annotations

from copy import deepcopy
import json

from .author_requirements import author_text, build_requirements, PRIORITY_RULES
from .creative_types import digest

VERSION = "creative-baseline.v1"
DEVELOPMENT_VERSION = "creative-development.v1"
CORE_SCOPE = "core_and_payoffs"

RULES = """
CREATIVE BASELINE:
The baseline is derived author authority, not another source of fictional facts.
Read full author statements in order and distinguish an instruction, an exploratory
question, a quoted example and a scoped delegation. Latest explicit corrections
override conflicting older choices; compatible choices remain. 'Continue' does
not adopt every option. Undecided areas stay open; a first model proposal is not
automatically an adopted route. Do not require route selection for an author who
already gave a complete story and asked you to develop it.
Deepen concrete motivations, choices, costs, power/relationship changes, emotional
payoffs and causal bridges. Preserve adopted core meaning and explicitly protected
scenes, while allowing equivalent staging, dialogue and compatible subplots.
Put optional changes of core direction in a clearly separate alternative. Do not
mix unadopted alternatives into the main design. Source observations, adopted new
fiction and unconfirmed assumptions retain separate authority. A source quote
proves provenance, not adoption. Unmentioned inherited items are not deleted.
Historical rejected/unverified candidates remain readable history, not current
design or evidence of author adoption. Do not ask authors to perform technical
reconciliation or approve ordinary professional detail.
""".strip()


def _item(identity, category, value, *, origin, adoption, path):
    return {"id": "commitment-" + digest(identity)[:24], "category": category,
            "value": deepcopy(value), "text": value if isinstance(value, str) else
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            "origin": origin, "adoption": deepcopy(adoption), "path": list(path)}


def build(session: dict) -> dict:
    """Build a verifiable snapshot without mutating a session or fetching sources.

    A new route selection explicitly supplies adoption_scope on the host-owned
    source_route_selection. Its absence retains the historical direction-only
    meaning, even if the source snapshot contains a very detailed complete route.
    """
    design = session.get("creative_design") or {}
    source = session.get("source_context") or {}
    route = source.get("selected_proposal")
    selection = session.get("source_route_selection") or source.get("selection") or {}
    items, entries = [], []
    initial = session.get("input") or {}
    for field in ("premise", "constraints"):
        text = initial.get(field)
        if ((session.get("input_origins") or {}).get(field, "author") == "author"
                and isinstance(text, str) and text.strip()):
            entries.append({"id": f"author-initial-{field}", "text": text, "source": f"input.{field}"})
    for index, message in enumerate(session.get("messages", [])):
        text = author_text(message)
        if text.strip():
            entries.append({"id": f"author-message-{index + 1}", "text": text,
                            "source": f"messages[{index}]"})
    for entry in entries:
        items.append(_item([session.get("id"), entry["id"]], "author_statement", entry["text"],
            origin="author", adoption={"kind": "exact_author_statement", "ref": entry["id"],
                "scope_rule": "Interpret the exact words; questions and quotations are not commands."},
            path=[entry["source"]]))

    # Resolve only explicit recorded decisions. Natural language is deliberately
    # kept above with its full wording rather than guessed with keyword rules.
    decisions = {row["proposal_id"]: row for row in design.get("decisions", []) if "proposal_id" in row}
    for record in design.get("rounds", []):
        for proposal in record.get("proposals", []):
            decision = decisions.get(proposal.get("id"))
            if decision and decision.get("status") in {"accepted", "revised"}:
                items.append(_item([session.get("id"), "proposal", proposal["id"]], "adopted_proposal",
                    {"proposal": proposal, "author_decision": decision}, origin="model_proposal",
                    adoption={"kind": "author_decision", "ref": proposal["id"], "status": decision["status"],
                              **({"message_index": decision["message_index"]} if type(decision.get("message_index")) is int else {})},
                    path=["creative_design", "decisions", proposal["id"]]))
    for index, action in enumerate((design.get("story_state") or {}).get("actions", [])):
        if action.get("mode") in {"select", "custom", "delegate"}:
            items.append(_item([session.get("id"), "action", index], "author_action", action,
                origin="author_action", adoption={"kind": "scoped_action", "ref": str(index),
                    "scope_rule": "Only this question; delegation is not blanket story authority."},
                path=["creative_design", "story_state", "actions", index]))

    full_route = isinstance(route, dict) and selection.get("adoption_scope") == CORE_SCOPE
    if isinstance(route, dict):
        route_id = route.get("route_id") or selection.get("route_id") or digest(route)
        identity = [session.get("id"), "route", route_id, selection.get("action_id")]
        scope = CORE_SCOPE if full_route else "discussion_direction"
        adopted = {"kind": "route_selection", "ref": selection.get("action_id"), "scope": scope,
                   **({"message_index": selection["adopted_at_message_index"]}
                      if type(selection.get("adopted_at_message_index")) is int else {})}
        items.append(_item([*identity, "title"], "direction", route.get("title", ""),
            origin="selected_route", adoption=adopted, path=["selected_proposal", "title"]))
        if full_route:
            fields = {"reader_promise": "reader_promise", "causal_arc": "causal_arc",
                      "character_relationship_arcs": "relationship", "emotional_design": "emotion",
                      "highlights": "highlight", "long_hooks_and_payoffs": "promise"}
            for field, category in fields.items():
                value = route.get(field)
                if value is None:
                    continue
                rows = list(enumerate(value)) if isinstance(value, list) else (
                    list(value.items()) if isinstance(value, dict) else [(None, value)])
                for key, row in rows:
                    path = ["selected_proposal", field] + ([] if key is None else [key])
                    items.append(_item([*identity, field, key], category, row, origin="selected_route",
                        adoption={**adopted, "scope_rule": "Protect adopted core function; explicitly optional details remain optional."}, path=path))
    if source.get("mode"):
        from .creation_mode import RULES as modes
        if source["mode"] not in modes:
            raise ValueError("来源创作模式无效。")
        items.append(_item([session.get("id"), "source-mode", source.get("context_sha256"), source["mode"]],
            "source_mode", modes[source["mode"]], origin="source_policy",
            adoption={"kind": "source_mode_selection", "mode": source["mode"],
                "scope_rule": "Source facts and future proposed fiction have separate authority."}, path=["source_context", "mode"]))

    state = design.get("story_state") or {}
    current = {"story_map": deepcopy(state.get("map")), "pending": deepcopy(state.get("pending")),
               "assumptions": deepcopy(state.get("assumptions", []))}
    dependencies = {"initial_brief": deepcopy(initial), "input_origins": deepcopy(session.get("input_origins", {})),
                    "conversation": deepcopy(session.get("messages", [])),
                    "current_complete_skeleton": session.get("prompt_md", ""),
                    "rounds": deepcopy(design.get("rounds", []))}
    development = session.get("development") or {}
    body = {"schema": VERSION, "session_id": session.get("id"),
            "session_revision": session.get("revision", 0), "items": items,
            "author_requirements": build_requirements("story", entries),
            "initial_preferences": {key: deepcopy(initial[key]) for key in
                ("title", "author", "genres", "creative_types", "audience_channel", "chapters",
                 "words_per_chapter", "target_total_words", "language", "mode", "audience", "market") if key in initial},
            "preference_scope": "Genre/Tags and readership are guidance, not adopted events or protagonist gender. Numeric preferences allocate space, not permission to cut the story. Later exact author changes retain priority.",
            "route_snapshot": deepcopy(route), "route_scope": CORE_SCOPE if full_route else
                "discussion_direction" if route else None,
            "source_identity": {key: source.get(key) for key in
                ("source_id", "source_sha256", "context_sha256", "mode", "evidence") if key in source},
            "source_author_feedback": deepcopy(source.get("author_feedback", [])),
            "current_design": current, "dependencies": dependencies,
            "candidate_history_status": deepcopy(development.get("candidate_history", [])),
            "last_accepted_candidate": deepcopy(development.get("accepted")),
            "priority": PRIORITY_RULES}
    from .creative_amendments import validate_adjustments
    previous = ((development.get("accepted") or {}).get("receipt") or {}).get("author_adjustments", [])
    # Historical proof survives later turns, but cannot migrate to a different
    # selection or an edited author statement by ID alone.
    if previous:
        current = {item["id"]: item for item in items}
        applicable = [row for row in previous if row.get("commitment_id") in current
                      and row.get("original_value_sha256") == digest(current[row["commitment_id"]]["value"])]
        # A newly recorded structured author decision may change a proposal's
        # complete value while retaining its ID. The earlier receipt stays in
        # history, but cannot amend this different, newly adopted obligation.
        body["author_adjustments"] = validate_adjustments(body, applicable)
    return {**body, "sha256": digest(body)}


def validate(baseline: dict) -> dict:
    if (not isinstance(baseline, dict) or baseline.get("schema") != VERSION
            or baseline.get("sha256") != digest({k: v for k, v in baseline.items() if k != "sha256"})):
        raise ValueError("创作依据校验失败，既有讨论与候选保持。")
    from .creative_amendments import validate_adjustments
    validate_adjustments(baseline)
    return deepcopy(baseline)


def context(session: dict) -> dict:
    """Read-only request projection; original full conversation remains supplied."""
    if (session.get("development") or {}).get("schema") != DEVELOPMENT_VERSION:
        return {}
    active = (session.get("development") or {}).get("active") or {}
    value = validate(active["baseline"]) if active.get("baseline") else build(session)
    # These are already supplied by author_context. Reference only values which
    # are EXACTLY equal now; a frozen resumed baseline can safely fall back to
    # its complete literal if the outer context differs.
    design = session.get("creative_design") or {}
    latest = {row["proposal_id"]: row for row in design.get("decisions", []) if "proposal_id" in row}
    adopted = [{"proposal": proposal, "author_decision": latest[proposal["id"]]}
        for record in design.get("rounds", []) for proposal in record.get("proposals", [])
        if latest.get(proposal.get("id"), {}).get("status") in {"accepted", "revised"}]
    provided = {"initial_brief": session.get("input", {}), "conversation": session.get("messages", []),
                "source_reference": session.get("source_context"),
                "creative_design": {"adopted_proposals": adopted,
                    "author_decisions": design.get("decisions", []), "story_state": design.get("story_state", {})}}

    def shared(container, key, path):
        actual = provided
        try:
            for part in path:
                actual = actual[part]
        except (KeyError, IndexError, TypeError):
            return False
        if key in container and container[key] == actual:
            container[key + "_ref"] = path
            del container[key]
            return True
        return False

    entries = value["author_requirements"]["entries"]
    author_paths = {}
    for entry in entries:
        ref = entry["source"]
        if ref.startswith("input."):
            path = ["initial_brief", ref[len("input."):]]
        elif ref.startswith("messages[") and ref.endswith("]"):
            index = int(ref[len("messages["):-1])
            message = session.get("messages", [])[index] if index < len(session.get("messages", [])) else {}
            field = "author_note" if message.get("action") in {"develop_story", "plan_capacity", "capacity_replan", "source_route"} else "content"
            path = ["conversation", index, field]
        else:
            continue
        if shared(entry, "text", path):
            author_paths[entry["id"]] = path

    for item in value["items"]:
        item.pop("text", None)  # exact display serialization of value, not another fact
        if item["category"] == "author_statement" and item["adoption"]["ref"] in author_paths:
            shared(item, "value", author_paths[item["adoption"]["ref"]])
        elif item["origin"] == "selected_route":
            shared(item, "value", ["source_reference", *item["path"]])
        elif item["category"] == "adopted_proposal":
            for index, record in enumerate(adopted):
                if record == item.get("value"):
                    shared(item, "value", ["creative_design", "adopted_proposals", index])
                    break
    shared(value, "route_snapshot", ["source_reference", "selected_proposal"])
    shared(value, "source_author_feedback", ["source_reference", "author_feedback"])
    for field, original in (("story_map", "map"), ("pending", "pending"), ("assumptions", "assumptions")):
        shared(value["current_design"], field, ["creative_design", "story_state", original])
    # Dependencies are the exact conversation/rounds already present in the
    # normal author context, not a reason to duplicate or remove those texts.
    value.pop("dependencies")
    value["snapshot_sha256"] = value.pop("sha256")
    value["representation"] = "exact-references.v1"
    value["projection_rule"] = ("A field ending _ref is a JSON key/index path to the exact complete value elsewhere in this request. "
        "Resolve it as the original field; no text was summarized. Item text is the display serialization of its value. "
        "Dependencies remain in complete conversation/current skeleton. snapshot_sha256 identifies the full saved baseline, not this request projection.")
    return {"creative_baseline": value}


def review_needed(candidate: dict, *, operation: str | None = None) -> bool:
    """Only host operations which cannot publish story changes may bypass review.

    The caller must not commit design_notes/map/capacity from explain/compare.
    Never infer exemption from a model's self-reported unchanged/changed IDs.
    """
    if operation in {"explain_only", "compare_only", "capacity_repartition"}:
        return False
    return isinstance(candidate, dict) and bool(candidate)


def view(session: dict) -> dict | None:
    if (session.get("development") or {}).get("schema") != DEVELOPMENT_VERSION:
        return None
    baseline = build(session)
    state = session["development"]
    selected = baseline.get("route_snapshot") or {}
    substantive = [item for item in baseline["items"] if item["category"] not in {"author_statement", "source_mode"}]
    direction = any(node.get("area") == "direction" and node.get("status") == "settled"
                    for node in ((baseline["current_design"].get("story_map") or {}).get("nodes") or []))
    developing = bool(substantive or direction)
    phase = "developing" if developing else "exploring"
    if state.get("status") in {"reviewing", "needs_revision", "needs_evidence", "needs_author"}:
        phase = state["status"] if state["status"] in {"reviewing", "needs_author"} else "needs_revision"
    changes = {row["commitment_id"]: row for row in baseline.get("author_adjustments", [])}
    commitments = []
    for item in baseline["items"]:
        row = {k: deepcopy(item[k]) for k in ("id", "category", "text", "adoption")}
        if item["id"] in changes:
            effective = changes[item["id"]]["effective_value"]
            row["text"] = ("已由作者撤销：" + row["text"] if effective is None else effective
                if isinstance(effective, str) else json.dumps(effective, ensure_ascii=False, separators=(",", ":")))
            row["adoption"]["author_adjustment"] = changes[item["id"]]["author_requirement_id"]
        commitments.append(row)
    emotions = deepcopy(selected.get("emotional_design", []))
    if baseline["route_scope"] == CORE_SCOPE:
        emotions = [deepcopy(changes[item["id"]]["effective_value"] if item["id"] in changes else item["value"])
                    for item in baseline["items"] if item["category"] == "emotion"
                    and (item["id"] not in changes or changes[item["id"]]["effective_value"] is not None)]
    return {"phase": phase, "title": (selected.get("title") or "当前创作方向") if developing else "正在探索故事方向",
            "commitments": commitments,
            "emotion_designs": emotions,
            "issues": deepcopy((state.get("last_assessment") or {}).get("issues", [])),
            "notice": state.get("notice", "保留已明确的要求，尚未决定的部分继续探索。" if phase == "exploring" else "沿当前方向深化人物选择、情绪回报与因果。")}
