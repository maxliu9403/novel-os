"""Versioned creative guidance, separate from the publication classification contract.

Freeze the complete selected resource snapshot in a discussion. No inference,
genre ranking, catalog migration, or reader-quality certification happens here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .novel_classification import PRIMARY_GENRES, STORY_TYPES, TONES, SETTINGS

RESOURCE = Path(__file__).resolve().parents[1] / "resources" / "genre-design" / "v1.json"


def _key(value: str) -> str:
    return " ".join(value.strip().casefold().replace("’", "'").split())


def genre_guidance(genres: list[str]) -> dict:
    raw = RESOURCE.read_bytes()
    resource = json.loads(raw)
    entries = (*PRIMARY_GENRES, *STORY_TYPES, *TONES, *SETTINGS)
    selections = []
    packs = []
    for label in genres:
        # Exact aliases only. Inference from a premise could adopt rejected ideas;
        # substring matching could conflate the author's custom compound genres.
        matches = [entry for entry in entries
                   if _key(label) in {_key(alias) for alias in (*entry.aliases, *entry.legacy_labels)}]
        ids = [entry.id for entry in matches]
        matched = [pack for pack in resource["packs"] if set(ids).intersection(pack["matches"])]
        selections.append({
            "selection": label, "catalog_ids": ids,
            "catalog_axes": list(dict.fromkeys(entry.axis for entry in matches)),
            "design_requirements": [entry.design_requirement for entry in matches if entry.design_requirement],
            "coverage": "specialized_guidance" if matched else "general_guidance",
            "pack_ids": [pack["id"] for pack in matched],
        })
        for pack in matched:
            if pack not in packs:
                packs.append(pack)
    return deepcopy({"version": resource["version"], "resource_sha256": hashlib.sha256(raw).hexdigest(),
                     "maturity": resource["maturity"], "general": resource["general"],
                     "selections": selections, "packs": packs})
