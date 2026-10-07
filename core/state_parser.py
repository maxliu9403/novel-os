"""
Novel OS - Agent Output Parser

Parses the structured update blocks that agents emit (per AGENTS.md) and
applies their contents to StoryState. This is what makes the "persistent
memory" claim true: without it, agent output is discarded after rendering.

Block tags recognized:
  [REVISED_CHAPTER] (manuscript payload; closing tag may be omitted)
  [SCRIBE_STATE_UPDATE] ... [/SCRIBE_STATE_UPDATE]
  [EDITOR_ANALYSIS] / [EDITOR_STATE_UPDATE]
  [CONTINUITY_REPORT] / [CONTINUITY_STATE_UPDATE]
  [STYLE_ANALYSIS] / [STYLE_STATE_UPDATE]

Field syntax supported (both forms):
  Field: value
  Field:
    - item one
    - item two

Closing tag is optional we accept either [/TAG] or "stop at next [TAG]".
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from commercial_story import (
    BELONGING_ANCHORS,
    HOOK_TYPES,
    READER_JOBS,
    RESOURCE_DIMENSIONS,
    SATISFACTION_TYPES,
)

if TYPE_CHECKING:
    from state_manager import StoryState


# ---------------------------------------------------------------- block extract

_KNOWN_TAGS = {
    "CHAPTER_CONTRACT",
    "REVISED_CHAPTER",
    "SCRIBE_STATE_UPDATE",
    "EDITOR_ANALYSIS",
    "EDITOR_STATE_UPDATE",
    "CONTINUITY_REPORT",
    "CONTINUITY_STATE_UPDATE",
    "STYLE_ANALYSIS",
    "STYLE_STATE_UPDATE",
}


_ZERO_WIDTH_RE = re.compile(r"[\u200B\u200C\u200D\uFEFF]")
_PROTOCOL_TAG_RE = re.compile(
    r"\[(?P<closing>/?)\s*(?P<tag>[A-Za-z][A-Za-z0-9 _-]*)\s*\]"
)


def normalize_agent_output(text: str) -> str:
    """Normalize known protocol tags without rewriting ordinary Markdown links."""
    cleaned = _ZERO_WIDTH_RE.sub("", text or "")

    def canonicalize(match: re.Match[str]) -> str:
        tag = re.sub(r"[\s_-]+", "_", match.group("tag")).upper()
        if tag not in _KNOWN_TAGS:
            return match.group(0)
        return f"[{match.group('closing')}{tag}]"

    return _PROTOCOL_TAG_RE.sub(canonicalize, cleaned)


def extract_block(text: str, tag: str) -> Optional[str]:
    """Return the inner text of [TAG]...[/TAG], or [TAG]... up to next known tag."""
    text = normalize_agent_output(text)
    open_pat = re.compile(rf"\[{re.escape(tag)}\]", re.IGNORECASE)
    close_pat = re.compile(rf"\[/{re.escape(tag)}\]", re.IGNORECASE)

    open_m = open_pat.search(text)
    if not open_m:
        return None
    start = open_m.end()
    close_m = close_pat.search(text, start)
    if close_m:
        return text[start:close_m.start()].strip()

    # No closing tag stop at the next [KNOWN_TAG] occurrence
    rest = text[start:]
    next_tag = re.search(r"\[/?[A-Z_]+\]", rest)
    if next_tag and next_tag.group(0)[1:-1].lstrip("/").upper() in _KNOWN_TAGS:
        return rest[:next_tag.start()].strip()
    return rest.strip()


def extract_manuscript_block(text: str, tag: str = "REVISED_CHAPTER") -> Optional[str]:
    """Extract a manuscript payload from an agent protocol response.

    Manuscript blocks are allowed to be truncated by a provider.  In that
    case ``extract_block`` uses the next recognized protocol block as the
    boundary, keeping state metadata out of the saved prose.  A missing
    opening block still returns ``None`` so callers can enforce the contract.
    """
    normalized_tag = re.sub(r"[\s_-]+", "_", tag.strip()).upper()
    if normalized_tag != "REVISED_CHAPTER":
        raise ValueError("manuscript block tag must be REVISED_CHAPTER")
    return extract_block(text, normalized_tag)


# ---------------------------------------------------------------- field parser

_FIELD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_ ]*?)\s*:\s*(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.+)$")


def parse_fields(block: str) -> Dict[str, Any]:
    """Parse a block of 'Field: value' / 'Field:\\n  - item' lines into a dict.

    Field keys are normalized to lower_snake_case.
    Values are str, or List[str] for bulleted/multi-line fields.
    """
    out: Dict[str, Any] = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        i += 1
        if not line.strip():
            continue
        m = _FIELD_RE.match(line.lstrip())
        if not m:
            continue
        key = _normalize_key(m.group(1))
        rest = m.group(2).strip()

        # Collect any following indented bullets as a list
        bullets: List[str] = []
        while i < len(lines):
            peek = lines[i]
            bm = _BULLET_RE.match(peek)
            if bm:
                bullets.append(bm.group(1).strip())
                i += 1
                continue
            if peek.strip() == "" and i + 1 < len(lines) and _BULLET_RE.match(lines[i + 1]):
                i += 1
                continue
            break

        if bullets:
            # If 'rest' was empty, bullets ARE the value; otherwise prepend rest.
            out[key] = bullets if not rest else [rest] + bullets
        elif rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            if not inner or inner.lower() in ("list", "count", "none"):
                out[key] = []
            else:
                out[key] = [s.strip() for s in inner.split(",") if s.strip()]
        else:
            out[key] = rest
    return out


def _normalize_key(raw: str) -> str:
    return re.sub(r"\s+", "_", raw.strip()).lower()


_READER_VALUE_UPDATE_FIELDS = {
    "report_id",
    "candidate_sha256",
    "reader_jobs",
    "belonging_anchors",
    "resource_dimension",
    "resource_change",
    "satisfaction_type",
    "hook_type",
    "protagonist_caused_turn",
}
_COMMERCIAL_REPORT_RE = re.compile(r"^commercial-chapter-report:[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_reader_value_update(update: Any) -> None:
    if not isinstance(update, dict) or set(update) != _READER_VALUE_UPDATE_FIELDS:
        raise ValueError("reader_value_updates must use the exact nine-field shape")
    if not _COMMERCIAL_REPORT_RE.fullmatch(str(update["report_id"])):
        raise ValueError("reader_value_updates.report_id is invalid")
    if not _SHA256_RE.fullmatch(str(update["candidate_sha256"])):
        raise ValueError("reader_value_updates.candidate_sha256 is invalid")
    jobs = update["reader_jobs"]
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 3 or len(set(jobs)) != len(jobs):
        raise ValueError("reader_value_updates.reader_jobs must contain 1-3 unique values")
    if any(item not in READER_JOBS for item in jobs):
        raise ValueError("reader_value_updates.reader_jobs has invalid value")
    anchors = update["belonging_anchors"]
    if not isinstance(anchors, list) or len(anchors) > 2 or len(set(anchors)) != len(anchors):
        raise ValueError("reader_value_updates.belonging_anchors must contain 0-2 unique values")
    if any(item not in BELONGING_ANCHORS for item in anchors):
        raise ValueError("reader_value_updates.belonging_anchors has invalid value")
    if update["resource_dimension"] not in RESOURCE_DIMENSIONS:
        raise ValueError("reader_value_updates.resource_dimension has invalid value")
    if not isinstance(update["resource_change"], str) or not update["resource_change"].strip():
        raise ValueError("reader_value_updates.resource_change must be nonblank")
    if update["satisfaction_type"] not in SATISFACTION_TYPES:
        raise ValueError("reader_value_updates.satisfaction_type has invalid value")
    if update["hook_type"] not in HOOK_TYPES:
        raise ValueError("reader_value_updates.hook_type has invalid value")
    if type(update["protagonist_caused_turn"]) is not bool:
        raise ValueError("reader_value_updates.protagonist_caused_turn must be a boolean")


# ---------------------------------------------------------------- per-agent

def parse_architect(text: str) -> Dict[str, Any]:
    """Parse a chapter contract or a structured chapter-outline proposal."""
    block = extract_block(text, "CHAPTER_CONTRACT")
    if block:
        try:
            contract = json.loads(block)
        except json.JSONDecodeError as exc:
            raise ValueError("architect chapter contract must be valid JSON") from exc
        if not isinstance(contract, dict):
            raise ValueError("architect chapter contract must be a JSON object")
        return {"chapter_contract": contract}

    normalized = normalize_agent_output(text).strip()
    if re.search(r"^#\s+Chapter\s+\d+\b", normalized, re.IGNORECASE | re.MULTILINE):
        return {"chapter_outline": normalized}
    return {}


def parse_scribe(text: str) -> Dict[str, Any]:
    block = extract_block(text, "SCRIBE_STATE_UPDATE")
    return parse_fields(block) if block else {}


def parse_editor(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for tag in ("EDITOR_ANALYSIS", "EDITOR_STATE_UPDATE"):
        block = extract_block(text, tag)
        if block:
            out.update(parse_fields(block))
    return out


def parse_continuity(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for tag in ("CONTINUITY_REPORT", "CONTINUITY_STATE_UPDATE"):
        block = extract_block(text, tag)
        if block:
            out.update(parse_fields(block))
    return out


def parse_style(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for tag in ("STYLE_ANALYSIS", "STYLE_STATE_UPDATE"):
        block = extract_block(text, tag)
        if block:
            out.update(parse_fields(block))
    return out


# ---------------------------------------------------------------- applier

def _resolve_character_id(state: "StoryState", name: str) -> Optional[str]:
    """Match a free-text character reference to an existing character id."""
    name = name.strip().strip(".,;:")
    if not name:
        return None
    if name in state.characters:
        return name
    direct = state.get_character_by_name(name)
    if direct:
        return direct.id
    # Legacy first/last-name references are safe only when unique. Never
    # attribute an update to the first relative with a shared surname.
    lower = name.casefold()
    matches = [
        char.id for char in state.characters.values()
        if any(
            lower == value.casefold() or lower in value.casefold().split()
            for value in [char.full_name, *char.aliases]
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _split_pair(item: str, sep_chars: str = ":-—") -> Tuple[str, str]:
    for sep in sep_chars:
        if sep in item:
            left, _, right = item.partition(sep)
            return left.strip(), right.strip()
    return item.strip(), ""


_PLACEHOLDER = {"none", "n/a", "0", "[none]", "[n/a]", "[]", "(none)", "-"}

# Stable setup identifiers are part of the ending contract. Keep accepting
# the original generated ``chN:fsM`` form, while recognizing the explicit
# contract form used by long-form prompts. Do not treat arbitrary prose that
# merely contains ``ch`` or ``:fs`` as an identifier.
_STABLE_FORESHADOWING_ID_RE = re.compile(
    r"^ch\d+:(?:fs\d+|setup:payoff_[A-Za-z0-9][A-Za-z0-9_-]*)$"
)


def _is_stable_foreshadowing_id(value: str) -> bool:
    return bool(_STABLE_FORESHADOWING_ID_RE.fullmatch(value.strip()))

def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip().lower() not in _PLACEHOLDER]
    s = str(value).strip()
    if not s or s.lower() in _PLACEHOLDER:
        return []
    return [s]


def _parse_pipe_update(raw: str) -> Tuple[str, Dict[str, str]]:
    """Parse the compact, human-readable update contract used by agents.

    Example::

        plot_001 | status=resolved | milestone=Acquisition closed | chapter=8

    Values are split only at the first equals sign, so notes and milestones may
    contain punctuation freely. The first segment is the entity id unless it
    is itself a ``key=value`` pair.
    """
    parts = [part.strip() for part in str(raw).split("|") if part.strip()]
    if not parts:
        return "", {}
    fields: Dict[str, str] = {}
    entity = ""
    for index, part in enumerate(parts):
        if "=" in part:
            key, value = part.split("=", 1)
            fields[_normalize_key(key)] = value.strip()
        elif index == 0:
            entity = part.strip()
    entity = fields.pop("thread_id", fields.pop("id", entity)).strip()
    return entity, fields


def _parse_optional_chapter(value: Any, fallback: int) -> int:
    text = str(value or "").strip().lower()
    if text in ("", "none", "null", "n/a", "-", "0"):
        return fallback
    try:
        return max(0, int(text))
    except (TypeError, ValueError):
        return fallback


def _apply_plot_thread_updates(
    state: "StoryState",
    chapter_number: int,
    chapter: Any,
    raw_updates: Any,
    source: str,
    log: List[str],
) -> None:
    """Apply explicit plot-thread updates without inferring from prose.

    Unknown ids are logged rather than silently creating canon. This keeps an
    agent typo from adding a second thread that the continuity engine cannot
    relate to the outline.
    """
    for raw in _as_list(raw_updates):
        thread_id, fields = _parse_pipe_update(raw)
        thread = state.get_plot_thread(thread_id)
        if not thread:
            log.append(f"[{source}] unknown plot thread referenced: {thread_id!r}")
            continue

        status = fields.get("status")
        status_ignored = False
        if status:
            normalized_status = status.strip().lower()
            if normalized_status in {"active", "resolved", "abandoned", "foreshadowed"}:
                reopen = fields.get("reopen", "").strip().lower() in {
                    "1", "true", "yes", "y", "reopen",
                }
                # Resolution/abandonment are terminal facts by default. A
                # language model often restates an old thread as active while
                # validating a later chapter; accepting that transition makes
                # a stale paraphrase reopen an overdue plot. Reopening is an
                # author-level decision and must be explicit in the protocol.
                if (
                    thread.status in {"resolved", "abandoned"}
                    and normalized_status not in {"resolved", "abandoned"}
                    and not reopen
                ):
                    status_ignored = True
                    log.append(
                        f"[{source}] ignored {thread_id} status={normalized_status!r}; "
                        f"thread is terminal ({thread.status}), use reopen=true"
                    )
                else:
                    thread.status = normalized_status
            else:
                log.append(f"[{source}] ignored invalid status for {thread_id}: {status!r}")

        if status_ignored:
            chapter.plot_thread_updates.append({
                "thread_id": thread_id,
                "status": thread.status,
                "chapter": chapter_number,
                "target_resolution_chapter": thread.target_resolution_chapter,
                "milestone": "",
                "ignored": True,
            })
            continue

        if "target_resolution_chapter" in fields or "resolution_chapter" in fields:
            raw_target = fields.get("target_resolution_chapter", fields.get("resolution_chapter"))
            target_text = str(raw_target or "").strip().lower()
            if target_text in ("", "none", "null", "n/a", "-", "0"):
                thread.target_resolution_chapter = None
            else:
                try:
                    thread.target_resolution_chapter = max(0, int(target_text))
                except ValueError:
                    log.append(
                        f"[{source}] ignored invalid target chapter for {thread_id}: {raw_target!r}"
                    )

        update_chapter = _parse_optional_chapter(
            fields.get("chapter", fields.get("last_updated_chapter")),
            chapter_number,
        )
        if update_chapter:
            thread.last_updated_chapter = max(thread.last_updated_chapter, update_chapter)

        milestone = fields.get("milestone") or fields.get("milestones")
        if milestone:
            already_recorded = any(
                str(item.get("description", "")).strip() == milestone.strip()
                and int(item.get("chapter", 0) or 0) == update_chapter
                for item in thread.milestones
            )
            if not already_recorded:
                state.add_milestone_to_thread(thread_id, milestone.strip(), update_chapter)
            else:
                thread.last_updated_chapter = max(thread.last_updated_chapter, update_chapter)

        chapter.plot_thread_updates.append({
            "thread_id": thread_id,
            "status": thread.status,
            "chapter": update_chapter,
            "target_resolution_chapter": thread.target_resolution_chapter,
            "milestone": milestone.strip() if milestone else "",
        })

        log.append(
            f"[{source}] {thread_id} ({thread.name}): status={thread.status!r}, "
            f"last_updated_chapter={thread.last_updated_chapter}"
        )


def _apply_character_references(
    state: "StoryState",
    chapter_number: int,
    chapter: Any,
    raw_references: Any,
    source: str,
    log: List[str],
) -> None:
    """Record an on-page reference or documented off-page absence.

    This intentionally does not add the character to ``characters_present`` or
    bump ``last_appearance_chapter``. A reference is evidence of continuity,
    not evidence that the character physically appeared in the scene.
    """
    for raw in _as_list(raw_references):
        character_id, fields = _parse_pipe_update(raw)
        cid = _resolve_character_id(state, character_id)
        if not cid:
            log.append(f"[{source}] unknown character reference: {character_id!r}")
            continue
        character = state.characters[cid]
        reference_chapter = _parse_optional_chapter(fields.get("chapter"), chapter_number)
        character.last_reference_chapter = max(character.last_reference_chapter, reference_chapter)
        note = fields.get("note") or fields.get("absence") or fields.get("reason")
        if note:
            character.absence_note = note.strip()
        chapter.character_references.append({
            "character_id": cid,
            "chapter": reference_chapter,
            "note": note.strip() if note else "",
        })
        log.append(
            f"[{source}] {character.full_name}: referenced in ch{reference_chapter}"
        )


def _apply_payoff_events(
    chapter: Any,
    raw_events: Any,
    chapter_number: int,
    source: str,
    log: List[str],
) -> None:
    """Record explicit book-level payoff evidence without inferring it."""
    for raw in _as_list(raw_events):
        payoff_id, fields = _parse_pipe_update(raw)
        if not payoff_id:
            log.append(f"[{source}] ignored payoff event without id")
            continue
        status = str(fields.get("status") or "recalled").strip().lower()
        if status not in {"planted", "recalled", "paid", "intentional_open", "blocked"}:
            log.append(f"[{source}] ignored invalid payoff status for {payoff_id}: {status!r}")
            continue
        event_chapter = _parse_optional_chapter(fields.get("chapter"), chapter_number)
        event = {
            "payoff_id": payoff_id,
            "status": status,
            "chapter": event_chapter,
            "evidence": fields.get("evidence") or fields.get("note") or fields.get("payoff") or "",
        }
        if event not in chapter.payoff_events:
            chapter.payoff_events.append(event)
        log.append(f"[{source}] payoff {payoff_id}: status={status}")


def _apply_arc_state_updates(
    state: "StoryState",
    chapter: Any,
    raw_updates: Any,
    chapter_number: int,
    source: str,
    log: List[str],
) -> None:
    """Apply explicit character arc evidence and keep the state index current."""
    for raw in _as_list(raw_updates):
        character_id, fields = _parse_pipe_update(raw)
        cid = _resolve_character_id(state, character_id)
        if not cid:
            log.append(f"[{source}] unknown character arc referenced: {character_id!r}")
            continue
        character = state.characters[cid]
        stage = str(fields.get("stage") or fields.get("arc_stage") or "").strip().lower()
        if stage and stage not in {"beginning", "middle", "climax", "resolution"}:
            log.append(f"[{source}] ignored invalid arc stage for {cid}: {stage!r}")
            stage = ""
        progress_text = fields.get("progress") or fields.get("arc_progress")
        progress: Optional[int] = None
        if progress_text not in (None, ""):
            try:
                progress = max(0, min(100, int(str(progress_text).strip())))
            except ValueError:
                log.append(f"[{source}] ignored invalid arc progress for {cid}: {progress_text!r}")
        if stage:
            character.arc_stage = stage
        if progress is not None:
            character.arc_progress = progress
        outcome = str(
            fields.get("outcome") or fields.get("outcome_state") or ""
        ).strip().lower()
        evidence = str(
            fields.get("evidence")
            or fields.get("choice")
            or fields.get("state")
            or ""
        ).strip()
        if outcome:
            character.outcome_state = outcome
            if evidence:
                character.outcome_evidence = evidence
        event = {
            "character_id": cid,
            "chapter": chapter_number,
            "stage": stage or character.arc_stage,
            "progress": character.arc_progress,
            "outcome": outcome or character.outcome_state,
            "evidence": evidence,
        }
        if event not in chapter.arc_state_updates:
            chapter.arc_state_updates.append(event)
        outcome_log = f", outcome={character.outcome_state}" if character.outcome_state else ""
        log.append(
            f"[{source}] {character.full_name}: "
            f"arc={character.arc_stage}/{character.arc_progress}{outcome_log}"
        )


def _apply_ending_evidence(
    chapter: Any,
    raw_evidence: Any,
    source: str,
    log: List[str],
) -> None:
    for evidence in _as_list(raw_evidence):
        value = str(evidence).strip()
        if not value or value.lower() in _PLACEHOLDER:
            continue
        if value not in chapter.ending_evidence:
            chapter.ending_evidence.append(value)
            log.append(f"[{source}] ending evidence recorded: {value[:60]}")


def apply_to_state(
    state: "StoryState",
    chapter_number: int,
    parsed: Dict[str, Any],
    source: str,
) -> List[str]:
    """Mutate StoryState from a parsed agent block. Returns a change log."""
    log: List[str] = []
    reader_updates = parsed.get("reader_value_updates")
    if reader_updates is not None:
        if source != "continuity_guardian":
            raise ValueError("reader_value_updates require continuity_guardian")
        if not isinstance(reader_updates, list):
            raise ValueError("reader_value_updates must be a list")
        for update in reader_updates:
            _validate_reader_value_update(update)
            report_id = update["report_id"]
            candidate_sha = update["candidate_sha256"]
            for existing_chapter in state.chapters.values():
                for existing in existing_chapter.reader_value_updates:
                    if existing.get("report_id") == report_id and existing.get("candidate_sha256") != candidate_sha:
                        raise ValueError("reader_value_updates report_id is bound to a different candidate")

    chapter = state.get_chapter(chapter_number) or state.create_chapter(chapter_number)

    # ----- Final-derived chapter metadata
    chapter_metadata = parsed.get("chapter_metadata")
    if chapter_metadata is not None:
        if not isinstance(chapter_metadata, dict):
            raise ValueError("chapter_metadata must be an object")
        allowed_metadata = {"title", "pov", "location", "time", "word_count"}
        unknown_metadata = set(chapter_metadata) - allowed_metadata
        if unknown_metadata:
            raise ValueError(
                f"chapter_metadata has unsupported fields: {sorted(unknown_metadata)}"
            )
        for field, attribute in (
            ("title", "title"),
            ("pov", "pov_character"),
            ("location", "location"),
            ("time", "time"),
        ):
            value = chapter_metadata.get(field)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"chapter_metadata.{field} must be a nonblank string")
            setattr(chapter, attribute, value.strip())
            log.append(f"[{source}] ch{chapter_number} {field} updated")
        if "word_count" in chapter_metadata:
            word_count = chapter_metadata["word_count"]
            if (
                isinstance(word_count, bool)
                or not isinstance(word_count, int)
                or word_count < 0
            ):
                raise ValueError(
                    "chapter_metadata.word_count must be a nonnegative integer"
                )
            chapter.word_count = word_count
            log.append(f"[{source}] ch{chapter_number} word_count = {word_count}")

    # ----- characters present -> chapter cast + bump last_appearance_chapter
    present_names: List[str] = []
    for raw in _as_list(parsed.get("characters_present")):
        cid = _resolve_character_id(state, raw)
        if cid:
            state.characters[cid].last_appearance_chapter = chapter_number
            present_names.append(state.characters[cid].full_name)
            log.append(f"[{source}] {state.characters[cid].full_name}: appeared in ch{chapter_number}")
        else:
            present_names.append(str(raw).strip())
            log.append(f"[{source}] unknown character referenced: {raw!r}")
    if present_names:
        chapter.characters_present = present_names

    # ----- emotional shifts: "Name: new state"
    for item in _as_list(parsed.get("emotional_shifts")):
        name, new_state = _split_pair(item)
        cid = _resolve_character_id(state, name)
        if cid and new_state:
            state.characters[cid].emotional_state = new_state
            log.append(f"[{source}] {state.characters[cid].full_name}: emotional_state -> {new_state!r}")

    # ----- updated character positions (continuity guardian)
    for item in _as_list(parsed.get("updated_character_positions")):
        name, location = _split_pair(item)
        cid = _resolve_character_id(state, name)
        if cid and location:
            state.update_character_location(cid, location, chapter_number)
            log.append(f"[{source}] {state.characters[cid].full_name}: location -> {location!r}")

    # ----- key events -> timeline + chapter notes
    for ev in _as_list(parsed.get("key_events")):
        chapter.plot_advances.append(ev)
        log.append(f"[{source}] ch{chapter_number} event logged: {ev[:60]}")

    # ----- foreshadowing
    for fs in _as_list(parsed.get("foreshadowing_planted")):
        chapter.foreshadowing_planted.append(fs)
        log.append(f"[{source}] foreshadowing planted: {fs[:60]}")
    for fs in _as_list(parsed.get("foreshadowing_resolved")):
        resolved_id, fields = _parse_pipe_update(fs)
        if _is_stable_foreshadowing_id(resolved_id):
            if resolved_id not in chapter.foreshadowing_resolved_ids:
                chapter.foreshadowing_resolved_ids.append(resolved_id)
            note = fields.get("note") or fields.get("description") or resolved_id
            chapter.foreshadowing_resolved.append(note)
            log.append(f"[{source}] foreshadowing resolved: {resolved_id}")
        else:
            chapter.foreshadowing_resolved.append(fs)
            log.append(f"[{source}] foreshadowing resolved: {fs[:60]}")

    # ----- explicit continuity metadata
    _apply_plot_thread_updates(
        state,
        chapter_number,
        chapter,
        parsed.get("plot_thread_updates"),
        source,
        log,
    )
    _apply_character_references(
        state,
        chapter_number,
        chapter,
        parsed.get("character_references"),
        source,
        log,
    )
    _apply_payoff_events(
        chapter,
        parsed.get("payoff_events"),
        chapter_number,
        source,
        log,
    )
    _apply_arc_state_updates(
        state,
        chapter,
        parsed.get("arc_state_updates"),
        chapter_number,
        source,
        log,
    )
    _apply_ending_evidence(chapter, parsed.get("ending_evidence"), source, log)

    if reader_updates is not None:
        for update in reader_updates:
            identity = (update["report_id"], update["candidate_sha256"])
            if not any(
                (existing.get("report_id"), existing.get("candidate_sha256")) == identity
                for existing in chapter.reader_value_updates
            ):
                chapter.reader_value_updates.append(dict(update))
                log.append(
                    f"[{source}] verified reader value recorded: {update['report_id']}"
                )

    # ----- new information / facts
    new_facts = _as_list(parsed.get("new_information_revealed")) + _as_list(parsed.get("new_facts_established"))
    for fact in new_facts:
        chapter.new_information.append(fact)
        log.append(f"[{source}] new fact: {fact[:60]}")

    # ----- editor quality scores
    for key in ("quality_score_before", "quality_score_after"):
        if key in parsed:
            m = re.search(r"(\d+(?:\.\d+)?)", str(parsed[key]))
            if m:
                chapter.quality_scores[key] = float(m.group(1))
                log.append(f"[{source}] {key} = {m.group(1)}")

    # ----- continuity status & issues
    if "status" in parsed:
        status = str(parsed["status"]).upper().strip()
        chapter.continuity_checks["status"] = status
        chapter.continuity_checks["validated_at"] = datetime.now().isoformat()
        log.append(f"[{source}] continuity status: {status}")
    for severity in ("critical_issues", "warnings"):
        items = _as_list(parsed.get(severity))
        if items:
            chapter.continuity_checks[severity] = items
            log.append(f"[{source}] {severity}: {len(items)}")

    # ----- style scores
    for key in ("consistency_score", "genre_adherence", "voice_strength"):
        if key in parsed:
            m = re.search(r"(\d+(?:\.\d+)?)", str(parsed[key]))
            if m:
                chapter.quality_scores[f"style_{key}"] = float(m.group(1))
                log.append(f"[{source}] style {key} = {m.group(1)}")

    chapter.last_modified = datetime.now().isoformat()
    return log


# ---------------------------------------------------------------- top-level

_DISPATCH = {
    "architect": parse_architect,
    "scribe": parse_scribe,
    "editor": parse_editor,
    "continuity_guardian": parse_continuity,
    "style_curator": parse_style,
}


def parse_agent_output(agent_name: str, agent_output: str) -> Dict[str, Any]:
    """Parse an agent state-update block without mutating StoryState."""
    parser = _DISPATCH.get(agent_name)
    if not parser:
        return {}
    return parser(agent_output)


def ingest_agent_output(
    state: "StoryState",
    chapter_number: int,
    agent_name: str,
    agent_output: str,
) -> List[str]:
    """One-call entry point. Parses + applies + returns change log (may be empty)."""
    parsed = parse_agent_output(agent_name, agent_output)
    if not parsed:
        return []
    return apply_to_state(state, chapter_number, parsed, source=agent_name)
