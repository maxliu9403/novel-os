"""Durable chapter-card windows for staged skeleton delivery.

Windows hold the original card objects. Only a contiguous validated prefix can
be reused. The assembled chapter_plan is still the same downstream contract.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

VERSION = "workshop.chapter-windows.v1"


def window_width(total: int, max_tokens: int | None) -> int:
    # Short deliveries with ordinary output budgets retain their existing
    # two-call workflow. A small configured output window can also opt them in.
    budget = max_tokens if type(max_tokens) is int and max_tokens > 0 else 16384
    if total <= 24 and total * 560 <= budget:
        return total
    return max(1, min(6, budget // 560))


def prefix(record: dict) -> list[dict]:
    return [card for batch in record.get("chapter_windows", {}).get("batches", ())
            for card in batch["chapters"]]


def digest(previous: str, cards: list, source_coverage: dict | None = None) -> str:
    parts = [VERSION, previous, cards]
    if source_coverage is not None:
        parts.append(source_coverage)
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False,
                         allow_nan=False, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def scope(record: dict, bounds: tuple[int, int], requested: set[int]) -> dict:
    prior = prefix(record)
    # Every earlier title and declared setup/payoff is supplied verbatim. Exact
    # source cards (including earlier dialogue/choices) can be read by number.
    index = [{k: card[k] for k in ("number", "title", "setups_payoffs") if k in card}
             for card in prior]
    if prior:
        requested = requested | {prior[-1]["number"]}
    return {"first": bounds[0], "last": bounds[1], "prior_index": index,
            "prior_cards": [card for card in prior if card["number"] in requested]}


def source_request(text: str) -> tuple[str, object] | None:
    # Only a dedicated request is a lookup. Otherwise the normal validated
    # response parser retains ownership of fences and actionable schema errors.
    try:
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        value = json.loads(text)
    except ValueError:
        return None
    if isinstance(value, dict) and len(value) == 1:
        key = next(iter(value))
        if key in {"read_chapters", "read_original_chapters"}:
            return key, value[key]
    return None


@dataclass
class OriginalChapters:
    source: str
    index: list[dict]

    @classmethod
    def recognize(cls, source: str, count: int):
        """Only compact compiler cards with an unambiguous core-plot block qualify."""
        from core.skeleton_projection import chapter_locations, story_parts
        parts = story_parts(source)
        if not parts or parts["chapter_count"] != count:
            return None
        locations = chapter_locations(source, count)
        if len(locations) != count:
            return None
        index = []
        for number, location in locations.items():
            start, end = location["source_chars"]
            card = source[start:end]
            # Preserve everything outside the numbered scene lines, including
            # POV, reader promise, hook and every declared payoff. Edited or
            # multi-line layouts that cannot be recognized keep the full input.
            match = re.fullmatch(r"(### Chapter [^\n]+\n- 叙事视角：[^\n]+\n- 核心剧情：\n)"
                                 r"((?:  [0-9]+\. [^\n]+\n)+)"
                                 r"(- 看点／情绪回报：[^\n]+\n- 章末钩子／余韵：[^\n]+"
                                 r"(?:\n- 伏笔与回收：[^\n]+)?\n*)", card)
            if not match:
                return None
            index.append({"number": number, "heading_and_pov": match[1], "promises_and_hook": match[3]})
        return cls(source, index)

    def project(self, base: dict, bounds: tuple[int, int], requested: set[int]):
        from core.skeleton_projection import chapter_source
        included = sorted(set(range(bounds[0], bounds[1] + 1)) | requested)
        view = chapter_source(self.source, included, len(self.index))
        context = dict(base)
        context.pop("current_complete_skeleton")
        context["previous_skeleton"] = {"original_chapters": view, "original_chapter_index": self.index}
        coverage = {"source_sha256": view["source_sha256"], "included_chapters": view["included_chapters"]}
        return context, coverage


ORIGINAL_RULES = """
previous_skeleton contains the author's ORIGINAL skeleton's global sections, exact selected
chapters and a full index of original POV, promises, hooks and declared payoffs. It is
distinct from the new prior_cards. The current range and adjacent original chapter bodies
are supplied in full. Other core scene details remain in the bound original. For a needed
earlier OR future original chapter, return ONLY {"read_original_chapters":[numbers]} before
drafting; do not infer its missing scenes or character knowledge from the index. Apply every
explicit author amendment to the requested range. Omitted original chapter bodies have
not been inspected in this request. The combined windows must cover the whole original.
"""


WINDOW_RULES = """
Plan ONLY chapter_window.first through chapter_window.last (inclusive) in order.
Return JSON {narrative_mode,total_chapters,chapters:[complete cards]}. Keep
total_chapters as the whole book count and narrative_mode as saved. Each
card keeps the full required chapter design. Apply the complete author brief,
current story design, confirmed scene allocations and the established trajectory
toward later turns. Carry forward the preceding chapter's actual consequences.
prior_index identifies all earlier saved cards; prior_cards are exact source
records. For earlier facts, identities or delayed payoffs you need to verify,
return ONLY {"read_chapters":[earlier chapter numbers]} to read their exact text
before drafting this range. Never infer missing evidence from a title. A lookup
does not change the range. Do not return prior or future cards.
"""
