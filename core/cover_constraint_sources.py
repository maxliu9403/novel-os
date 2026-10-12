"""Resolve story-facing cover constraints against an immutable skeleton.

The brainstorm handoff is a compact, user-authored boundary.  It is not an
evidence source by itself: section references must resolve into the frozen
primary skeleton outside the ``COVER_HANDOFF`` JSON block.  This module keeps
that distinction in one place so analysis and the cover studio use the same
reference rules.
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from typing import Any

from .cover_handoff import parse_cover_handoff
from .cover_models_v2 import CoverConstraints
from .cover_sources import CoverSkeletonSnapshot


_HANDOFF = re.compile(
    r"COVER_HANDOFF_BEGIN\s*(?:```json\s*)?.*?COVER_HANDOFF_END",
    re.DOTALL,
)
_HANDOFF_JSON = re.compile(
    r"COVER_HANDOFF_BEGIN\s*(?:```json\s*)?(?P<body>\{.*?\})\s*(?:```\s*)?COVER_HANDOFF_END",
    re.DOTALL,
)
_SECTION = re.compile(r"(?m)^(?P<hash>#{1,6})\s+(?P<number>\d+)\.\s*(?P<title>.+?)\s*$")
_HEADING = re.compile(r"(?m)^(?P<hash>#{1,6})\s+(?P<title>.+?)\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_NODE_LINE = re.compile(
    r"(?im)^\s*(?:[-*+]\s*)?(?:node[_ -]?id|节点(?:id|ID)?|node)\s*[:=：]\s*['\"`]?"
    r"(?P<id>[A-Za-z0-9][A-Za-z0-9._-]*)['\"`]?\s*$"
)
_NODE_HEADING = re.compile(
    r"(?im)^\s*#{1,6}\s+.*?\[node[_ -]?(?P<id>[A-Za-z0-9][A-Za-z0-9._-]*)\]\s*$"
)


@dataclass(frozen=True)
class ConstraintSource:
    """A source-backed constraint reference resolved inside one snapshot."""

    reference: str
    section_number: int
    section_title: str
    text: str
    source_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "section_number": self.section_number,
            "section_title": self.section_title,
            "text": self.text,
            "source_sha256": self.source_sha256,
        }


def _story_text(snapshot: CoverSkeletonSnapshot) -> str:
    """Remove the machine handoff before resolving refs.

    A constraint mentioning ``section:99`` in its own JSON must never make
    section 99 appear to exist.  We therefore remove the whole fenced block,
    including nested JSON braces, before scanning headings and node anchors.
    """

    return _HANDOFF.sub("\n", snapshot.primary_text)


def _norm(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())


def _mask_fenced_code(text: str) -> str:
    """Hide examples from structural scans without shifting character offsets.

    Match the fence rules used by cover_review_units: up to three spaces of
    indentation, matching marker and sufficient length, and no closing info
    string. Returned excerpts still come from the untouched source text.
    """
    masked: list[str] = []
    fence: tuple[str, int] | None = None
    for raw_line in text.splitlines(keepends=True):
        marker = _FENCE.match(raw_line.rstrip("\r\n"))
        inside = fence is not None
        if fence is not None:
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1]
                    and not marker[2].strip(" \t")):
                fence = None
        elif marker and (marker[1][0] != "`" or "`" not in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            inside = True
        masked.append(re.sub(r"[^\r\n]", " ", raw_line) if inside else raw_line)
    return "".join(masked)


_SECTION_ALIASES = {
    "relationship_map": {"relationshipmap", "人物关系及变化", "人物关系", "关系地图"},
    "story_structure": {"storystructure", "全书分卷系列结构", "全书系列结构", "结构"},
}

# A constraint source is evidence for review, not an invitation to persist an
# entire novel repeatedly.  Keep enough context for a human audit while
# bounding durable records and model context growth.
MAX_CONSTRAINT_SOURCE_REFS = 32
MAX_CONSTRAINT_SOURCE_TEXT = 12_000


def _sections(snapshot: CoverSkeletonSnapshot) -> dict[int, tuple[str, str]]:
    text = _story_text(snapshot)
    scan_text = _mask_fenced_code(text)
    numbered = list(_SECTION.finditer(scan_text))
    if not numbered:
        return {}
    # Numbered chapter/subsection headings belong to the enclosing skeleton
    # section. Only the outermost numbered level defines section:<number>.
    level = min(len(match.group("hash")) for match in numbered)
    boundaries = [match for match in _HEADING.finditer(scan_text) if len(match.group("hash")) <= level]
    result: dict[int, tuple[str, str]] = {}
    for index, heading in enumerate(boundaries):
        match = _SECTION.fullmatch(heading.group())
        if match is None or len(match.group("hash")) != level:
            continue
        number = int(match.group("number"))
        if number in result:
            raise ValueError(f"duplicate skeleton section number: {number}")
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(text)
        result[number] = (match.group("title").strip(), text[heading.start():end])
    return result


def _section_matches_suffix(title: str, suffix: str) -> bool:
    wanted = _norm(suffix)
    title_norm = _norm(title)
    if not wanted or not title_norm:
        return False
    if wanted == title_norm:
        return True
    aliases = _SECTION_ALIASES.get(suffix.casefold())
    return aliases is not None and any(_norm(item) == title_norm for item in aliases)


def _resolve_reference(snapshot: CoverSkeletonSnapshot, reference: str) -> ConstraintSource:
    ref = str(reference or "").strip()
    match = re.fullmatch(r"section:(?P<number>\d+)(?:\.(?P<suffix>[^.]+(?:\.[^.]+)*))?", ref)
    if not match:
        raise ValueError(f"unsupported cover constraint source reference: {ref}")
    number = int(match.group("number"))
    sections = _sections(snapshot)
    if number not in sections:
        raise ValueError(f"cover constraint source section does not exist: {ref}")
    title, section_text = sections[number]
    suffix = match.group("suffix")
    if not suffix:
        return ConstraintSource(ref, number, title, section_text[:MAX_CONSTRAINT_SOURCE_TEXT], snapshot.source_content_sha256)
    if suffix.startswith("node:"):
        node_id = suffix.removeprefix("node:").strip()
        if not node_id:
            raise ValueError(f"empty node id in cover constraint source reference: {ref}")
        scan_text = _mask_fenced_code(section_text)
        # Match the original text so existing excerpt offsets are unchanged.
        # Masking before this whitespace-tolerant regex could make it absorb
        # an entire hidden fence before a real anchor and shift the excerpt.
        anchors = [item for pattern in (_NODE_LINE, _NODE_HEADING)
                   for item in pattern.finditer(section_text)
                   if item.group("id") == node_id
                   and scan_text[item.start("id"):item.end("id")] == node_id]
        if not anchors:
            raise ValueError(f"cover constraint node is not defined in section: {ref}")
        if len(anchors) != 1:
            raise ValueError(f"cover constraint node is ambiguous in section: {ref}")
        # Validate against the full source first.  Persist bounded context near
        # the anchor so late nodes remain resolvable and visible to reviewers.
        start = max(0, anchors[0].start() - 200)
        excerpt = section_text[start:start + MAX_CONSTRAINT_SOURCE_TEXT]
        return ConstraintSource(ref, number, title, excerpt, snapshot.source_content_sha256)
    if not _section_matches_suffix(title, suffix):
        raise ValueError(f"cover constraint section label does not match skeleton: {ref}")
    return ConstraintSource(ref, number, title, section_text[:MAX_CONSTRAINT_SOURCE_TEXT], snapshot.source_content_sha256)


def extract_cover_constraints(snapshot: CoverSkeletonSnapshot) -> CoverConstraints | None:
    """Read the optional constraints from the frozen COVER_HANDOFF only."""

    has_begin = "COVER_HANDOFF_BEGIN" in snapshot.primary_text
    has_end = "COVER_HANDOFF_END" in snapshot.primary_text
    if not has_begin and not has_end:
        # Legacy/structured source projects may not carry a handoff at all.
        # Their existing cover path remains valid and has no user constraint
        # contract to preserve.
        return None
    if not has_begin:
        raise ValueError("COVER_HANDOFF_BEGIN is missing")
    if not has_end:
        raise ValueError("COVER_HANDOFF_END is missing")
    if snapshot.primary_text.count("COVER_HANDOFF_BEGIN") != 1:
        raise ValueError("COVER_HANDOFF_BEGIN must occur exactly once")
    if snapshot.primary_text.count("COVER_HANDOFF_END") != 1:
        raise ValueError("COVER_HANDOFF_END must occur exactly once")

    # Parse the one complete block.  Do not fall back to a first-looking JSON
    # object: duplicate or malformed handoffs must fail closed rather than
    # silently selecting a different constraint contract.
    try:
        brief = parse_cover_handoff(snapshot.primary_text)
        constraints = getattr(brief, "cover_constraints", None)
    except (ValueError, TypeError) as exc:
        # Some historical v1 handoffs are valid JSON but cannot be hydrated
        # into today's complete CoverBrief.  Read the optional constraints
        # from that single, structurally verified block without accepting a
        # second or first-looking JSON object.
        match = _HANDOFF_JSON.search(snapshot.primary_text)
        if match is None:
            raise ValueError(f"cannot extract cover constraints from frozen handoff: {exc}") from exc
        try:
            payload = json.loads(match.group("body"))
        except json.JSONDecodeError as json_exc:
            raise ValueError(
                f"cannot extract cover constraints from frozen handoff: {json_exc.msg}"
            ) from json_exc
        if not isinstance(payload, dict):
            raise ValueError("COVER_HANDOFF JSON must be an object")
        raw_constraints = payload.get("cover_constraints")
        if raw_constraints is not None and not isinstance(raw_constraints, dict):
            raise ValueError("cover_constraints must be an object")
        constraints = None if raw_constraints is None else CoverConstraints.from_dict(raw_constraints)
        if constraints is not None and int(payload.get("schema_version") or 1) == 2:
            node_ids = {
                str(item.get("node_id")) for item in payload.get("decisive_story_nodes", [])
                if isinstance(item, dict) and item.get("node_id")
            }
            character_ids = {
                str(item.get("character_id")) for item in payload.get("principal_characters", [])
                if isinstance(item, dict) and item.get("character_id")
            }
            unknown_nodes = set(constraints.preferred_story_node_ids) - node_ids
            unknown_characters = set(constraints.preferred_character_ids) - character_ids
            if unknown_nodes or unknown_characters:
                raise ValueError(
                    "cover constraint preferred ids are not present in the frozen handoff: "
                    f"nodes={sorted(unknown_nodes)}, characters={sorted(unknown_characters)}"
                )
    if constraints is None:
        return None
    if len(constraints.source_refs) > MAX_CONSTRAINT_SOURCE_REFS:
        raise ValueError(
            f"cover constraint source_refs must contain at most {MAX_CONSTRAINT_SOURCE_REFS} references"
        )
    if len(set(constraints.source_refs)) != len(constraints.source_refs):
        raise ValueError("cover constraint source_refs must be unique")
    for reference in constraints.source_refs:
        _resolve_reference(snapshot, reference)
    return constraints


def resolve_constraint_sources(
    snapshot: CoverSkeletonSnapshot,
    constraints: CoverConstraints | None = None,
) -> tuple[ConstraintSource, ...]:
    """Resolve every source ref and bind it to the snapshot content digest."""

    if constraints is None:
        constraints = extract_cover_constraints(snapshot)
    if constraints is None:
        return ()
    return tuple(_resolve_reference(snapshot, ref) for ref in constraints.source_refs)


def constraint_source_evidence(
    snapshot: CoverSkeletonSnapshot,
    constraints: CoverConstraints | None = None,
) -> tuple[dict[str, Any], ...]:
    """Return deterministic evidence records for the source-bound constraints."""

    return tuple(item.to_dict() for item in resolve_constraint_sources(snapshot, constraints))


def source_constraints_payload(snapshot: CoverSkeletonSnapshot) -> dict[str, Any] | None:
    constraints = extract_cover_constraints(snapshot)
    return constraints.to_dict() if constraints is not None else None


def assert_constraints_unchanged(
    snapshot: CoverSkeletonSnapshot,
    model_constraints: CoverConstraints | None,
) -> None:
    """Reject LLM additions/deletions: the source contract is authoritative."""

    source = extract_cover_constraints(snapshot)
    source_payload = source.to_dict() if source is not None else None
    model_payload = model_constraints.to_dict() if model_constraints is not None else None
    if model_payload != source_payload:
        raise ValueError("Model cover_constraints must exactly preserve frozen user constraints")
