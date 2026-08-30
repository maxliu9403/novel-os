"""Parse approved brainstorm handoffs and derive distinct cover concepts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

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


def parse_cover_handoff_v2(prompt_text: str):
    """Parse the existing handoff and normalize it without changing v1 callers."""
    from .cover_normalizer import normalize_cover_brief

    brief = parse_cover_handoff(prompt_text)
    return normalize_cover_brief(
        brief,
        source_prompt_sha256=brief.source_prompt_sha256,
        foundation_sha256=brief.foundation_sha256,
    )


def resolve_cover_brief(
    project_path: str | Path,
    *,
    prompt_text: str | None = None,
) -> CoverBrief:
    """Load a current handoff or adapt durable artifacts from a legacy project."""
    project = Path(project_path)
    prompt_path = project / "outputs" / "input" / "prompt.md"
    text = prompt_text if prompt_text is not None else prompt_path.read_text(encoding="utf-8")
    if BEGIN in text or END in text:
        return parse_cover_handoff(text)
    return _legacy_cover_brief(project, text)


def resolve_cover_brief_v2(
    project_path: str | Path,
    *,
    prompt_text: str | None = None,
):
    """Resolve a current or legacy project into the v2 facts contract."""
    from .cover_normalizer import normalize_cover_brief, normalize_legacy_project

    project = Path(project_path)
    prompt_path = project / "outputs" / "input" / "prompt.md"
    text = prompt_text if prompt_text is not None else prompt_path.read_text(encoding="utf-8")
    if BEGIN in text or END in text:
        return parse_cover_handoff_v2(text)
    return normalize_legacy_project(project, text)


def _legacy_cover_brief(project: Path, prompt_text: str) -> CoverBrief:
    inputs = project / "outputs" / "input"
    brief = _read_json_object(inputs / "brief.json")
    foundation_path = inputs / "foundation.json"
    foundation = _read_json_object(foundation_path)
    state = _read_json_object(project / "outputs" / "state" / "story_state.json")
    standalone_ending = _read_json_object(inputs / "ending_contract.json")

    metadata = _object(state.get("metadata"))
    bible = _object(state.get("story_bible"))
    characters = _objects(foundation.get("characters"))
    if not characters:
        characters = _objects(state.get("characters"))
    protagonist = _character(characters, "protagonist") or (characters[0] if characters else {})
    antagonist = _character(characters, "antagonist")

    threads = _objects(foundation.get("plot_threads"))
    main_thread = _thread(threads, "main") or (threads[0] if threads else {})
    decisive_thread = next(
        (
            item for item in threads
            if str(item.get("type") or "").casefold() in {"mystery", "romance"}
        ),
        {},
    )
    if not decisive_thread:
        decisive_thread = next((item for item in threads if item is not main_thread), main_thread)

    ending = _object(foundation.get("ending_contract")) or standalone_ending
    main_conflict = _object(ending.get("main_conflict"))
    emotional = _object(ending.get("emotional_contract"))
    payoffs = _objects(ending.get("plot_payoffs"))
    first_payoff = payoffs[0] if payoffs else {}
    setting = _object(foundation.get("setting")) or _object(bible.get("setting"))
    themes = foundation.get("themes") or bible.get("themes") or []

    title = _text(
        foundation.get("title"), metadata.get("title"), brief.get("title"), project.name
    )
    genre = _text(brief.get("genre"), metadata.get("genre"), bible.get("genre"), "Fiction")
    language = _text(brief.get("language"), metadata.get("language"), bible.get("language"), "English")
    audience = _text(
        brief.get("audience"), metadata.get("audience"), bible.get("audience"),
        f"readers of {genre}",
    )
    premise = _text(foundation.get("premise"), metadata.get("premise"), bible.get("premise"))
    protagonist_name = _text(protagonist.get("name"), protagonist.get("full_name"), "the protagonist")
    antagonist_name = _text(antagonist.get("name"), antagonist.get("full_name"), "the opposing force")
    antagonist_goal = _text(antagonist.get("external_goal"), main_thread.get("description"), premise)
    payoff = _text(first_payoff.get("required_payoff"), decisive_thread.get("description"), premise)

    location = _text(setting.get("primary_location"))
    world_signals = []
    if location and _declares_fictional(location):
        world_signals.append(location)
    else:
        world_signals.append("the story's fictionalized setting")
    period = _text(setting.get("time_period"))
    if period:
        world_signals.append(period)

    source_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    foundation_sha = ""
    if foundation_path.is_file():
        foundation_sha = hashlib.sha256(foundation_path.read_bytes()).hexdigest()

    payload = {
        "schema_version": 1,
        "title": title,
        "author": _text(brief.get("author"), metadata.get("author")),
        "language": language,
        "genre": genre,
        "target_audience": audience,
        "market_scope": f"{language} serialized fiction",
        "core_task": _text(
            protagonist.get("external_goal"), main_thread.get("description"), premise
        ),
        "core_conflict": _text(premise, main_thread.get("description"), antagonist_goal),
        "emotional_promise": _text(
            emotional.get("reader_emotion"), metadata.get("tone"), bible.get("tone"),
            themes[0] if isinstance(themes, list) and themes else "earned emotional resolution",
        ),
        "protagonist": {
            "role": _text(protagonist.get("role"), "protagonist"),
            "visual_identity": _text(
                protagonist.get("physical_description"), f"{protagonist_name}, the protagonist"
            ),
            "agency_signal": _text(
                protagonist.get("strength"), main_conflict.get("protagonist_choice"),
                protagonist.get("external_goal"),
            ),
        },
        "relationship_or_power_contrast": (
            f"{protagonist_name} against {antagonist_name}, who seeks to {antagonist_goal}"
        ),
        "decisive_story_node": _text(
            decisive_thread.get("description"), main_thread.get("description"), premise
        ),
        "secondary_task": {
            "story_function": _text(
                first_payoff.get("id"), decisive_thread.get("name"), "supporting story payoff"
            ),
            "visual_signal": payoff,
        },
        "world_signals": world_signals,
        "title_direction": {
            "hierarchy": "large exact title",
            "preferred_zone": "top",
            "readability": "mobile_thumbnail",
        },
        "forbidden_elements": [
            "real place names", "identifiable real-city landmarks", "unsupported ending spoilers",
        ],
    }
    return CoverBrief.from_dict(
        payload,
        source_prompt_sha256=source_sha,
        foundation_sha256=foundation_sha,
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Legacy cover source {path.name} is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Legacy cover source {path.name} must contain a JSON object")
    return value


def _object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        values = value.values()
    elif isinstance(value, list):
        values = value
    else:
        return []
    return [dict(item) for item in values if isinstance(item, Mapping)]


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _character(characters: list[dict[str, Any]], role: str) -> dict[str, Any]:
    wanted = role.casefold()
    return next(
        (
            item for item in characters
            if str(item.get("role") or "").strip().casefold() == wanted
        ),
        {},
    )


def _thread(threads: list[dict[str, Any]], thread_type: str) -> dict[str, Any]:
    wanted = thread_type.casefold()
    return next(
        (
            item for item in threads
            if str(item.get("type") or "").strip().casefold() == wanted
        ),
        {},
    )


def _declares_fictional(value: str) -> bool:
    folded = value.casefold()
    return any(marker in folded for marker in ("fictional", "fictionalized", "imaginary", "invented", "虚构"))


def _uses_western_market(brief: CoverBrief) -> bool:
    """Select the Western commercial profile from the locked market branch."""
    market = f"{brief.language} {brief.market_scope}".casefold()
    return any(
        marker in market
        for marker in (
            "english",
            "英文",
            "英语",
            "united states",
            "美国",
            "u.s.",
            "us market",
            "north america",
            "北美",
            "american",
            "欧美",
            "western",
            "europe",
        )
    )


def _western_market_direction() -> list[str]:
    """Return model-facing direction for premium English-market cover art."""
    return [
        "Market visual direction: target the United States and North American serialized-fiction market.",
        "Casting and identity: preserve only identity traits explicitly established in the approved story. Do not infer or invent ethnicity, nationality, or other identity traits from the release market.",
        "Aesthetic: premium US commercial fiction magazine cover and entertainment key art, with polished editorial photography, cinematic production design, sophisticated color grading, realistic skin texture, and a high-end campaign finish; avoid a stock-photo look.",
        "Story feeling: make the image feel like a scene, not a generic portrait; show one frozen cinematic story moment with visible action, reaction, and stakes, so the viewer can infer what just happened and what is about to happen.",
        "Western editorial typography: choose a premium display serif or refined modern grotesk that fits the genre, with precise kerning, deliberate tracking, strong hierarchy, and generous title-safe space for mobile thumbnail readability.",
    ]


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
    lines = [
        "Create a finished, high-conversion commercial cover for serialized fiction.",
        "Canvas: portrait 2:3, preserve the provider-supported resolution, and keep it readable as a mobile feed thumbnail.",
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
    ]
    if _uses_western_market(brief):
        # Keep market direction close to the visual instructions so the image
        # model treats it as art direction rather than optional metadata.
        lines[1:1] = _western_market_direction()
    return "\n".join(lines)


def refresh_cover_concept_prompt(brief: CoverBrief, concept: CoverConcept) -> str:
    """Recompile a persisted concept with the current prompt policy."""
    return _generation_prompt(
        brief,
        {
            "scene": concept.focal_scene,
            "composition": concept.composition,
            "palette": concept.palette,
            "secondary": concept.secondary_signal,
        },
    )
