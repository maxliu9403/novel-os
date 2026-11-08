"""Bounded synthetic context recall probe. No manuscript or external source data.

Passing proves one sampled input was usable, not full context visibility,
literary quality, provider identity, or reliability at a larger size.
"""
from __future__ import annotations

import json
import secrets

SIZES = (16000, 64000, 100000, 112000)
SYSTEM = ("Read the complete synthetic archive. Return ONLY a JSON object mapping "
          "opening, early, middle, late, ending to the exact code in the corresponding "
          "ANCHOR record. Follow this system output format. Do not summarize or invent codes.")
KEYS = ("opening", "early", "middle", "late", "ending")


def build_probe(size: int) -> tuple[str, str, dict[str, str]]:
    if size not in SIZES:
        raise ValueError("Unsupported context probe size")
    expected = {key: secrets.token_hex(8) for key in KEYS}
    budget = size - len(SYSTEM.encode())
    sections = []
    for index, key in enumerate(KEYS):
        anchor = f"\nANCHOR {key}: {expected[key]}\n"
        target = budget // 5 + (budget % 5 if index == 4 else 0)
        filler = []
        length = 0
        while length < target:
            filler.append(f"Archive {secrets.token_hex(12)}. A resident faced a choice about trust, duty and a changing relationship. The consequence remained part of this scene.\n")
            length += len(filler[-1])
        text = "".join(filler)[:target - len(anchor)]
        # Edge records catch loss at the beginning and at the end, not just mid-input.
        sections.append(text + anchor if index == 4 else anchor + text)
    return SYSTEM, "".join(sections), expected


def assess(reply: str, expected: dict[str, str]) -> dict:
    text = reply.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        value = None
    checks = {key: isinstance(value, dict) and value.get(key) == code for key, code in expected.items()}
    return {"passed": all(checks.values()), "checks": checks,
            "matched": sum(checks.values()), "total": len(checks),
            "scope": "仅验证本次合成材料的五处信息读取；不代表小说质量、完整上下文覆盖或更长输入可用"}
