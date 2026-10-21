"""Lossless, narrow compatibility fixes for generated workshop contracts.

These helpers never approve a candidate. The caller must validate the complete
returned skeleton with the normal engine/cover validators before publishing it.
"""
from __future__ import annotations

import json
import re


_COVER = re.compile(
    r"COVER_HANDOFF_BEGIN\s*(?:```json\s*)?(?P<body>\{.*?\})\s*(?:```\s*)?COVER_HANDOFF_END",
    re.DOTALL,
)


def normalize_workshop_contracts(prompt_md: str) -> tuple[str, list[str]]:
    """Wrap legacy visual-note strings without inventing or confirming facts.

    An empty visual_assumptions array and existing object entries are already
    canonical and remain unchanged. A bare string carries no field, value or
    confirmation status. Retain it verbatim as an explicitly unconfirmed visual
    note; do not interpret its prose as a confirmed character/setting attribute.
    Only the COVER_HANDOFF JSON body may change. Change records contain paths,
    never story text, so callers can safely record them as diagnostic metadata.
    """
    matches = list(_COVER.finditer(prompt_md))
    if len(matches) != 1:
        return prompt_md, []
    match = matches[0]
    try:
        payload = json.loads(match.group("body"))
    except (json.JSONDecodeError, TypeError):
        return prompt_md, []
    if not isinstance(payload, dict) or payload.get("schema_version") not in (2, "2"):
        return prompt_md, []
    assumptions = payload.get("visual_assumptions")
    if not isinstance(assumptions, list):
        return prompt_md, []
    changes = []
    normalized = []
    for index, item in enumerate(assumptions):
        if isinstance(item, str) and item.strip():
            normalized.append({
                "field": f"unspecified_visual_detail_{index + 1}",
                "proposed_value": item,
                "reason": "Preserved verbatim from the generated visual-assumption note; the specific visual field and approval remain unconfirmed.",
                "status": "pending_confirmation",
            })
            changes.append(f"COVER_HANDOFF.visual_assumptions[{index}]")
        else:
            # Do not drop malformed data or rewrite already-structured entries.
            # The normal validator still rejects any remaining contract error.
            normalized.append(item)
    if not changes:
        return prompt_md, []
    payload["visual_assumptions"] = normalized
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return prompt_md[:match.start("body")] + body + prompt_md[match.end("body"):], changes
