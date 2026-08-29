"""Parse approved brainstorm handoffs and derive distinct cover concepts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

try:
    from .cover_models import CoverBrief, CoverConcept
except ImportError:  # pragma: no cover - legacy CLI imports core modules top-level
    from cover_models import CoverBrief, CoverConcept


BEGIN = "COVER_HANDOFF_BEGIN"
END = "COVER_HANDOFF_END"
_BLOCK = re.compile(
    rf"{BEGIN}\s*(?:```json\s*)?(?P<body>\{{.*?\}})\s*(?:```\s*)?{END}",
    re.DOTALL,
)


def parse_cover_handoff(prompt_text: str) -> CoverBrief:
    text = str(prompt_text or "")
    if BEGIN not in text or END not in text:
        raise ValueError(f"Prompt must contain {BEGIN} and {END}")
    matches = list(_BLOCK.finditer(text))
    if len(matches) != 1:
        raise ValueError("Prompt must contain exactly one cover handoff block")
    try:
        payload: Any = json.loads(matches[0].group("body"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cover handoff is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Cover handoff JSON must be an object")
    prompt_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return CoverBrief.from_dict(payload, source_prompt_sha256=prompt_sha)


def build_cover_concepts(brief: CoverBrief, *, count: int = 4) -> list[CoverConcept]:
    if not 3 <= count <= 5:
        raise ValueError("Cover concept count must be between 3 and 5")

    secondary = brief.secondary_task.get("visual_signal") or "one subordinate story object"
    world = ", ".join(brief.world_signals) or "the story's fictional setting"
    protagonist = brief.protagonist
    strategies = [
        {
            "id": "protagonist_confrontation",
            "scene": (
                f"{protagonist['visual_identity']} visibly {protagonist['agency_signal']}; "
                f"the opposition is staged through {brief.relationship_or_power_contrast}"
            ),
            "composition": "protagonist dominant in the foreground, opposition compressed behind",
            "palette": "high-contrast genre palette with one urgent accent color",
            "secondary": secondary,
        },
        {
            "id": "decisive_story_node",
            "scene": brief.decisive_story_node,
            "composition": "freeze the irreversible instant as one cinematic focal action",
            "palette": "dramatic light separating the decision from a restrained background",
            "secondary": secondary,
        },
        {
            "id": "symbolic_evidence",
            "scene": f"the protagonist controls the visual evidence: {secondary}",
            "composition": "character-led close composition with the symbolic object sharply readable",
            "palette": "dark neutral field with a bright evidence highlight",
            "secondary": secondary,
        },
        {
            "id": "world_relationship_pressure",
            "scene": (
                f"{protagonist['visual_identity']} under visible pressure from "
                f"{brief.relationship_or_power_contrast} in {world}"
            ),
            "composition": "clear protagonist silhouette, environmental lines applying pressure inward",
            "palette": "world-specific atmospheric color with warm/cool character separation",
            "secondary": secondary,
        },
        {
            "id": "emotional_reversal",
            "scene": (
                f"the moment {brief.emotional_promise} becomes visible through "
                f"{protagonist['agency_signal']}"
            ),
            "composition": "intimate expression in foreground, defeated pressure receding behind",
            "palette": "controlled transition from oppressive shadow to earned light",
            "secondary": secondary,
        },
    ]

    concepts: list[CoverConcept] = []
    for index, strategy in enumerate(strategies[:count], start=1):
        prompt = _generation_prompt(brief, strategy)
        concepts.append(CoverConcept(
            concept_id=f"concept-{index:02d}-{strategy['id']}",
            visual_strategy=strategy["id"],
            focal_scene=strategy["scene"],
            composition=strategy["composition"],
            palette=strategy["palette"],
            secondary_signal=strategy["secondary"],
            title_treatment=(
                f"Render the exact {brief.language} title once in the "
                f"{brief.title_direction.get('preferred_zone') or 'top'} area"
            ),
            generation_prompt=prompt,
        ))
    brief.validate_concepts(concepts)
    return concepts


def _generation_prompt(brief: CoverBrief, strategy: dict[str, str]) -> str:
    forbidden = ", ".join(brief.forbidden_elements) or "unsupported spoilers"
    world = ", ".join(brief.world_signals) or "fictional story setting"
    return "\n".join([
        "Create a finished, high-conversion commercial cover for serialized fiction.",
        "Canvas: portrait 2:3, exact 2048x3072 pixels, readable as a mobile feed thumbnail.",
        f"Target audience: {brief.target_audience}.",
        f"Genre and market signal: {brief.genre}; {brief.market_scope}.",
        f"Core conflict: {brief.core_conflict}",
        f"Primary focal scene: {strategy['scene']}.",
        f"Composition: {strategy['composition']}.",
        f"Palette: {strategy['palette']}.",
        f"Fictional world signals: {world}.",
        f"Use only one subordinate story signal: {strategy['secondary']}.",
        f"The emotional promise is {brief.emotional_promise}.",
        f'Render the exact title "{brief.title}" exactly once in {brief.language}.',
        "The title must be large, correctly spelled, and readable at thumbnail size.",
        "Do not add an author name, subtitle, tagline, logo, watermark, or any other text.",
        f"Exclude: {forbidden}; real place names and identifiable real-city landmarks.",
        "Keep one dominant conflict. Do not create a collage or multiple competing scenes.",
    ])
