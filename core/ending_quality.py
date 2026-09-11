"""Book-level ending contract, payoff ledger, and deterministic quality gate.

The chapter continuity engine answers "is this chapter consistent?".  This
module answers the different book-level question: "did the promises made by
the story actually reach a supported ending?"  The contract and ledger are
derived quality artifacts; chapter canon continues to flow through proposals
and promotion receipts.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from state_manager import StoryState


ENDING_CONTRACT_RELATIVE = "outputs/input/ending_contract.json"
PAYOFF_LEDGER_RELATIVE = "outputs/state/payoff_ledger.json"
ENDING_REPORT_RELATIVE = "outputs/feedback/book_completion_report.json"
ARC_LIFECYCLE_STAGES = {"beginning", "middle", "climax", "resolution"}
OUTCOME_MATCH_MODES = {"auto", "exact", "normalized", "contains"}
_CONTAINS_IGNORED_TERMS = ("以及", "并且", "同时", "此外", "的")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_ending_contract(project: Path | str) -> Dict[str, Any]:
    """Load the explicit Architect contract, or an empty disabled contract."""
    root = Path(project)
    path = root / ENDING_CONTRACT_RELATIVE
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("ending contract must be a JSON object")
        return payload

    foundation_path = root / "outputs/input/foundation.json"
    if foundation_path.is_file():
        foundation = json.loads(foundation_path.read_text(encoding="utf-8"))
        if isinstance(foundation, dict) and isinstance(foundation.get("ending_contract"), dict):
            return dict(foundation["ending_contract"])
    return {"schema_version": 1, "enforce": False}


def ensure_quality_ledgers(project: Path | str, contract: Optional[Dict[str, Any]] = None) -> Dict[str, Path]:
    """Persist normalized ending and payoff ledgers and return their paths."""
    root = Path(project)
    payload = dict(contract or load_ending_contract(root))
    payload.setdefault("schema_version", 1)
    payload.setdefault("enforce", False)
    contract_path = root / ENDING_CONTRACT_RELATIVE
    _write_json(contract_path, payload)

    ledger_path = root / PAYOFF_LEDGER_RELATIVE
    previous: Dict[str, Any] = {}
    if ledger_path.is_file():
        try:
            loaded = json.loads(ledger_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
        except json.JSONDecodeError:
            previous = {}
    previous_items = {
        str(item.get("id")): item
        for item in _as_list(previous.get("items"))
        if isinstance(item, dict) and item.get("id")
    }
    items: List[Dict[str, Any]] = []
    for raw in _as_list(payload.get("plot_payoffs")):
        if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
            continue
        item_id = str(raw["id"]).strip()
        old = previous_items.get(item_id, {})
        items.append({
            "id": item_id,
            "setup_ids": [str(value) for value in _as_list(raw.get("setup_ids")) if str(value).strip()],
            "required_payoff": str(raw.get("required_payoff") or "").strip(),
            "deadline": raw.get("deadline"),
            "allow_intentional_open": bool(raw.get("allow_intentional_open", False)),
            "status": str(old.get("status") or "planted"),
            "payoff_evidence": list(old.get("payoff_evidence") or []),
            "updated_at": str(old.get("updated_at") or _now()),
        })
    ledger = {
        "schema_version": 1,
        "contract_enforced": bool(payload.get("enforce", False)),
        "updated_at": _now(),
        "items": items,
    }
    _write_json(ledger_path, ledger)
    return {"ending_contract": contract_path, "payoff_ledger": ledger_path}


def _resolved_foreshadowing(state: StoryState) -> Dict[str, Dict[str, Any]]:
    resolved: Dict[str, Dict[str, Any]] = {}
    for chapter in state.chapters.values():
        ids = list(getattr(chapter, "foreshadowing_resolved_ids", []) or [])
        notes = list(getattr(chapter, "foreshadowing_resolved", []) or [])
        for index, source_id in enumerate(ids):
            resolved[str(source_id).strip().lower()] = {
                "chapter": chapter.number,
                "note": notes[index] if index < len(notes) else "",
            }
    return resolved


def _payoff_events(state: StoryState) -> Dict[str, Dict[str, Any]]:
    """Select completion evidence, not merely the last mention of a payoff.

    A recall revisits an established result; it does not revoke an earlier
    paid event. Preserve the first completion and its source chapter. For
    payoffs without completion evidence, retain the latest declared status.
    Persisted chapter keys may be lexicographically ordered, so traverse the
    numeric chapter sequence rather than relying on mapping insertion order.
    """
    events: Dict[str, Dict[str, Any]] = {}
    for chapter in sorted(state.chapters.values(), key=lambda item: item.number):
        for event in _as_list(getattr(chapter, "payoff_events", [])):
            if not isinstance(event, dict):
                continue
            payoff_id = str(event.get("payoff_id") or "").strip()
            status = str(event.get("status") or "").strip().lower()
            if payoff_id and status in {"paid", "intentional_open", "recalled"}:
                if events.get(payoff_id, {}).get("status") == "paid":
                    continue
                events[payoff_id] = {**event, "status": status, "chapter": chapter.number}
    return events


def _thread_status(state: StoryState, thread_id: str) -> Optional[str]:
    thread = state.plot_threads.get(thread_id)
    return thread.status if thread is not None else None


def _arc_stage(state: StoryState, character_id: str) -> Optional[str]:
    character = state.characters.get(character_id)
    if character is None:
        return None
    if character.arc_progress >= 100:
        return "resolution"
    return str(character.arc_stage or "beginning").strip().lower()


def _outcome_state(state: StoryState, character_id: str) -> Optional[str]:
    character = state.characters.get(character_id)
    if character is None:
        return None
    return str(character.outcome_state or "").strip().lower()


def _normalized_outcome_text(value: Any) -> str:
    """Normalize formatting without attempting semantic interpretation.

    NFKC handles full-width forms, while retaining letters, numbers, CJK
    characters, and underscores. Punctuation and whitespace are formatting
    noise for the explicitly opt-in ``normalized``/``contains`` modes.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return "".join(char for char in text if char.isalnum() or char == "_")


def _outcome_aliases(raw: Dict[str, Any], required_outcome: str) -> List[str]:
    values = [required_outcome]
    for value in _as_list(raw.get("required_outcome_aliases")):
        alias = str(value or "").strip().lower()
        if alias and alias not in values:
            values.append(alias)
    return values


def _outcome_match_mode(contract: Dict[str, Any], raw: Dict[str, Any]) -> str:
    value = raw.get("outcome_match_mode")
    if value is None:
        value = raw.get("match_mode")
    if value is None:
        value = contract.get("outcome_match_mode", contract.get("match_mode", "auto"))
    return str(value or "auto").strip().lower()


def _is_ascii_outcome_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_]+", str(value).strip().lower()))


def _contains_outcome(actual: str, expected: str) -> bool:
    """Check an explicitly richer outcome without substring ID collisions."""
    expected_normalized = _normalized_outcome_text(expected)
    if not expected_normalized:
        return False
    # ``dependence`` is a substring of ``independence``. Treat simple ASCII
    # outcome identifiers as tokens so ``contains`` remains safe for IDs.
    expected_text = unicodedata.normalize("NFKC", expected).casefold()
    expected_tokens = re.findall(r"[a-z0-9_]+", expected_text)
    if len(expected_tokens) == 1 and expected_normalized == expected_tokens[0]:
        actual_text = unicodedata.normalize("NFKC", actual).casefold()
        return expected_normalized in re.findall(r"[a-z0-9_]+", actual_text)
    actual_normalized = _normalized_outcome_text(actual)
    if expected_normalized in actual_normalized:
        return True
    # Natural-language contracts often gain connective words inside an
    # existing clause. Ignore only a small, explicit set in ``contains``;
    # semantic synonyms still require ``required_outcome_aliases``.
    for term in _CONTAINS_IGNORED_TERMS:
        expected_normalized = expected_normalized.replace(term, "")
        actual_normalized = actual_normalized.replace(term, "")
    return expected_normalized in actual_normalized


def _outcome_matches(actual: str, expected_values: Iterable[str], mode: str) -> bool:
    expected_values = list(expected_values)
    if mode == "auto":
        normalized_values = [_normalized_outcome_text(value) for value in expected_values]
        if normalized_values and all(_is_ascii_outcome_id(value) for value in expected_values):
            return actual in expected_values
        if normalized_values and all(
            normalized and _is_ascii_outcome_id(normalized)
            for normalized in normalized_values
        ):
            normalized_actual = _normalized_outcome_text(actual)
            return normalized_actual in normalized_values
        return _outcome_matches(actual, expected_values, "contains")
    if mode == "exact":
        return actual in expected_values
    if mode == "normalized":
        normalized_actual = _normalized_outcome_text(actual)
        return any(
            normalized_actual == _normalized_outcome_text(expected)
            for expected in expected_values
        )
    if mode == "contains":
        return any(_contains_outcome(actual, expected) for expected in expected_values)
    return False


def _evidence_for_arc(state: StoryState, character_id: str) -> List[str]:
    evidence: List[str] = []
    character = state.characters.get(character_id)
    if character is not None and character.outcome_evidence.strip():
        evidence.append(f"canon: {character.outcome_evidence.strip()}")
    for chapter in state.chapters.values():
        update = chapter.arc_state_updates if hasattr(chapter, "arc_state_updates") else []
        for item in _as_list(update):
            if isinstance(item, dict) and str(item.get("character_id") or "") == character_id:
                evidence.append(f"ch{chapter.number}: {item.get('evidence') or item.get('state') or ''}".strip())
    return evidence


@dataclass
class EndingReport:
    status: str
    finale_window: Dict[str, int]
    critical: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    info: List[Dict[str, Any]] = field(default_factory=list)
    report_path: Path = Path()
    ledger_path: Path = Path()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": _now(),
            "status": self.status,
            "finale_window": self.finale_window,
            "critical": self.critical,
            "warnings": self.warnings,
            "info": self.info,
            "report_path": str(self.report_path),
            "ledger_path": str(self.ledger_path),
        }


def evaluate_ending(project: Path | str, as_of_chapter: Optional[int] = None) -> EndingReport:
    """Evaluate the final story state against its ending contract."""
    root = Path(project)
    contract = load_ending_contract(root)
    paths = ensure_quality_ledgers(root, contract)
    state = StoryState(str(root))
    configured_total = int(state.metadata.get("target_chapters") or 0)
    highest = max(state.chapters, default=0)
    final_chapter = as_of_chapter or configured_total or highest
    window = contract.get("finale_window") or {}
    start = int(window.get("start_chapter") or max(1, final_chapter - 4))
    end = int(window.get("end_chapter") or final_chapter)
    finale_window = {"start_chapter": start, "end_chapter": end}
    critical: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    info: List[Dict[str, Any]] = []

    if not contract.get("enforce", False):
        info.append({"category": "ending_contract_disabled", "message": "No enforced ending contract was supplied."})
    else:
        main = contract.get("main_conflict") or {}
        thread_id = str(main.get("thread_id") or "").strip()
        required_status = str(main.get("required_status") or "resolved").strip().lower()
        actual_status = _thread_status(state, thread_id) if thread_id else None
        if not thread_id or actual_status is None:
            critical.append({"category": "main_conflict_missing", "message": f"Main conflict thread {thread_id or '[missing]'} is not present in canon."})
        elif actual_status != required_status:
            critical.append({"category": "main_conflict_unresolved", "message": f"Main conflict {thread_id} is {actual_status}, expected {required_status}."})

        for raw in _as_list(contract.get("character_arcs")):
            if not isinstance(raw, dict):
                continue
            character_id = str(raw.get("character_id") or "").strip()
            if character_id not in state.characters:
                critical.append({"category": "character_arc_missing", "entity_id": character_id, "message": f"Character arc {character_id or '[missing]'} is not present in canon."})
                continue

            required_stage = str(raw.get("required_arc_stage") or "").strip().lower()
            required_outcome = str(raw.get("required_outcome") or "").strip().lower()
            if not required_stage and not required_outcome:
                legacy_state = str(
                    raw.get("required_end_state") or "resolution"
                ).strip().lower()
                if legacy_state in ARC_LIFECYCLE_STAGES:
                    required_stage = legacy_state
                else:
                    required_outcome = legacy_state

            if required_stage:
                actual_stage = _arc_stage(state, character_id)
                if actual_stage != required_stage:
                    critical.append({"category": "character_arc_unclosed", "entity_id": character_id, "message": f"Character {character_id} arc stage is {actual_stage or 'missing'}, expected {required_stage}.", "evidence": _evidence_for_arc(state, character_id)})

            if required_outcome:
                actual_outcome = _outcome_state(state, character_id)
                match_mode = _outcome_match_mode(contract, raw)
                if match_mode not in OUTCOME_MATCH_MODES:
                    critical.append({
                        "category": "ending_contract_invalid",
                        "entity_id": character_id,
                        "message": (
                            f"Character {character_id} has unsupported outcome_match_mode "
                            f"{match_mode!r}; expected one of {sorted(OUTCOME_MATCH_MODES)}."
                        ),
                    })
                    continue
                expected_values = _outcome_aliases(raw, required_outcome)
                if actual_outcome is None or not _outcome_matches(
                    actual_outcome, expected_values, match_mode
                ):
                    critical.append({
                        "category": "character_outcome_unclosed",
                        "entity_id": character_id,
                        "message": (
                            f"Character {character_id} semantic outcome is "
                            f"{actual_outcome or 'missing'}, expected {required_outcome} "
                            f"(match_mode={match_mode})."
                        ),
                        "match_mode": match_mode,
                        "evidence": _evidence_for_arc(state, character_id),
                    })
                elif not _evidence_for_arc(state, character_id):
                    critical.append({"category": "character_outcome_evidence_missing", "entity_id": character_id, "message": f"Character {character_id} reaches semantic outcome {required_outcome} without observable evidence."})

        resolved = _resolved_foreshadowing(state)
        payoff_events = _payoff_events(state)
        ledger_items: List[Dict[str, Any]] = []
        for raw in _as_list(contract.get("plot_payoffs")):
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item_id = str(item.get("id") or "").strip()
            setup_ids = [str(value).strip().lower() for value in _as_list(item.get("setup_ids"))]
            matches = [resolved[source_id] for source_id in setup_ids if source_id in resolved]
            event = payoff_events.get(item_id)
            if event and event.get("status") == "paid":
                matches.append(event)
            if matches:
                item["status"] = "paid"
                item["payoff_evidence"] = matches
            elif event and event.get("status") == "intentional_open":
                item["status"] = "intentional_open"
                warnings.append({"category": "payoff_unresolved", "entity_id": item_id, "message": f"Payoff {item_id} remains open by chapter evidence."})
            elif bool(item.get("allow_intentional_open", False)):
                item["status"] = "intentional_open"
                warnings.append({"category": "payoff_unresolved", "entity_id": item_id, "message": f"Payoff {item_id} remains open by contract."})
            else:
                item["status"] = "blocked"
                critical.append({"category": "core_payoff_unresolved", "entity_id": item_id, "message": f"Required payoff {item_id} has no resolved evidence before chapter {final_chapter}."})
            ledger_items.append(item)

        final = state.get_chapter(final_chapter)
        if final is None or final.status not in {"complete", "validated", "edited"}:
            critical.append({"category": "final_chapter_missing", "message": f"Finale chapter {final_chapter} is not a complete chapter state."})
        elif not getattr(final, "ending_evidence", None):
            critical.append({"category": "irreversible_change_missing", "message": f"Finale chapter {final_chapter} has no explicit ending evidence."})
        if contract.get("antagonist_outcome", {}).get("required"):
            outcome = contract["antagonist_outcome"]
            outcome_thread = str(outcome.get("thread_id") or "").strip()
            outcome_status = _thread_status(state, outcome_thread) if outcome_thread else None
            if outcome_thread and outcome_status != str(outcome.get("required_status") or "resolved").lower():
                critical.append({"category": "antagonist_outcome_unclosed", "entity_id": outcome_thread, "message": f"Antagonist outcome thread {outcome_thread} is {outcome_status or 'missing'}."})

        ledger_path = paths["payoff_ledger"]
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["items"] = ledger_items
        ledger["updated_at"] = _now()
        _write_json(ledger_path, ledger)

    report = EndingReport(
        status="fail" if critical else "pass",
        finale_window=finale_window,
        critical=critical,
        warnings=warnings,
        info=info,
        report_path=root / ENDING_REPORT_RELATIVE,
        ledger_path=paths["payoff_ledger"],
    )
    _write_json(report.report_path, report.to_dict())
    return report


__all__ = [
    "ENDING_CONTRACT_RELATIVE",
    "PAYOFF_LEDGER_RELATIVE",
    "ENDING_REPORT_RELATIVE",
    "EndingReport",
    "evaluate_ending",
    "ensure_quality_ledgers",
    "load_ending_contract",
]
