"""Turn a user prompt into durable Novel OS project inputs."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, TextIO

from commercial_story import parse_commercial_story_block
from novel_classification import (
    design_requirements,
    infer_classification,
    parse_classification_block,
)
from narrative_format import (
    infer_narrative_format,
    parse_narrative_format_block,
)
from state_manager import StoryState, initialize_project


_FIELD_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?"
    r"(title|default working title|author|genre|primary category|audience|primary audience|"
    r"language|tone|pov|point of view|chapters|words)"
    r"(?:\*\*)?\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)

_FIELD_ALIASES = {
    "default_working_title": "working_title",
    "primary_category": "genre",
    "primary_audience": "audience",
    "point_of_view": "pov",
}


def _strip_markdown(value: str) -> str:
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", value.strip())
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    return cleaned.strip().rstrip(".").strip()


@dataclass
class PromptIntakeResult:
    project_path: str
    raw_prompt: str
    brief: Dict[str, Any]
    prompt_path: str
    brief_path: str
    story_bible_path: str


def _read_source(source: str | Path, stdin_text: Optional[str], stdin: Optional[TextIO]) -> str:
    if str(source) == "-":
        if stdin_text is not None:
            return stdin_text
        return (stdin or sys.stdin).read()
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def _prompt_fields(prompt: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for line in prompt.splitlines():
        match = _FIELD_RE.match(line)
        if match:
            key = match.group(1).lower().replace(" ", "_")
            key = _FIELD_ALIASES.get(key, key)
            value = match.group(2)
            if key == "working_title":
                emphasized = re.search(r"\*\*(.*?)\*\*", value)
                if emphasized:
                    value = emphasized.group(1)
            fields[key] = _strip_markdown(value)
    return fields


def _heading_title(prompt: str) -> str:
    for line in prompt.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line.strip())
        if match:
            title = match.group(1).strip()
            if re.match(r"^(?:master\s+)?prompt\b", title, re.IGNORECASE):
                return ""
            return title
    return ""


def _premise(prompt: str) -> str:
    """Use the first prose paragraph as a hint, never as a replacement for raw input."""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", prompt) if block.strip()]
    for block in blocks:
        if not block.startswith("#") and not _FIELD_RE.match(block.splitlines()[0]):
            return block[:2000]
    return ""


def _number(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _infer_length(prompt: str) -> tuple[Optional[int], Optional[int]]:
    chapter_range = re.search(
        r"(\d{1,3})\s*[-–]\s*(\d{1,3})\s+chapters?\b",
        prompt,
        re.IGNORECASE,
    )
    exact_chapters = re.search(r"\b(\d{1,3})\s+chapters?\b", prompt, re.IGNORECASE)
    chapters = (
        max(int(chapter_range.group(1)), int(chapter_range.group(2)))
        if chapter_range
        else int(exact_chapters.group(1)) if exact_chapters else None
    )

    per_chapter = re.search(
        r"([\d,]+)\s*[-–]\s*([\d,]+)[^\n]{0,40}?words?\s+per\s+chapter",
        prompt,
        re.IGNORECASE,
    )
    words = None
    if chapters and per_chapter:
        low = int(per_chapter.group(1).replace(",", ""))
        high = int(per_chapter.group(2).replace(",", ""))
        words = chapters * ((low + high) // 2)
    return chapters, words


def build_brief(prompt: str, overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    overrides = dict(overrides or {})
    fields = _prompt_fields(prompt)
    inferred_chapters, inferred_words = _infer_length(prompt)
    title = str(overrides.get("title") or fields.get("title") or _heading_title(prompt) or "Untitled")
    genre = str(overrides.get("genre") or fields.get("genre") or "Fiction")
    chapters = _number(overrides.get("chapters") or fields.get("chapters") or inferred_chapters, 32)
    words = _number(overrides.get("words") or fields.get("words") or inferred_words, 80000)
    brief = {
        "title": title,
        "working_title": str(overrides.get("working_title") or fields.get("working_title") or ""),
        "genre": genre,
        "author": str(overrides.get("author") or fields.get("author") or "").strip(),
        "audience": str(overrides.get("audience") or fields.get("audience") or ""),
        "language": str(overrides.get("language") or fields.get("language") or "English"),
        "tone": str(overrides.get("tone") or fields.get("tone") or ""),
        "pov": str(overrides.get("pov") or fields.get("pov") or fields.get("point_of_view") or ""),
        "target_chapters": max(1, chapters),
        "target_words": max(max(1, chapters), words),
        "premise": str(overrides.get("premise") or _premise(prompt)),
        "content_policy": str(overrides.get("content_policy") or ""),
        "must_have": list(overrides.get("must_have") or []),
        "forbidden": list(overrides.get("forbidden") or []),
        "assumptions": [
            "The source prompt is authoritative and is preserved verbatim.",
            "Fields supplied by CLI overrides take precedence over prompt hints.",
        ],
    }
    locked_classification = parse_classification_block(prompt)
    classification = locked_classification or infer_classification(
        genre=genre,
        premise=brief["premise"],
        audience=brief["audience"],
        tone=brief["tone"],
        raw_prompt=prompt,
        chapters=brief["target_chapters"],
    )
    explicit_length = any((
        overrides.get("chapters") is not None,
        fields.get("chapters") is not None,
        inferred_chapters is not None,
    ))
    narrative_format = parse_narrative_format_block(prompt)
    if narrative_format is not None:
        if explicit_length and narrative_format.total_chapters != brief["target_chapters"]:
            raise ValueError(
                "NARRATIVE_FORMAT_JSON total_chapters conflicts with the prompt chapter target"
            )
        if overrides.get("words") is not None and narrative_format.target_words != brief["target_words"]:
            raise ValueError(
                "NARRATIVE_FORMAT_JSON target_words conflicts with the CLI word target"
            )
        brief["target_chapters"] = narrative_format.total_chapters
        brief["target_words"] = narrative_format.target_words
        if locked_classification is None:
            classification = infer_classification(
                genre=genre,
                premise=brief["premise"],
                audience=brief["audience"],
                tone=brief["tone"],
                raw_prompt=prompt,
                chapters=brief["target_chapters"],
            )
    else:
        narrative_format = infer_narrative_format(
            classification,
            chapters=brief["target_chapters"],
            target_words=brief["target_words"],
            premise=brief["premise"],
            raw_prompt=prompt,
            explicit_length=explicit_length,
        )
    brief["classification"] = classification.to_dict()
    brief["classification_design_requirements"] = list(
        design_requirements(classification)
    )
    brief["narrative_format"] = narrative_format.to_dict()
    commercial_story = parse_commercial_story_block(prompt)
    if commercial_story is not None:
        brief["commercial_story_contract"] = commercial_story.to_dict()
        brief["commercial_story_contract_id"] = commercial_story.contract_id
    return brief


def _story_bible_markdown(brief: Mapping[str, Any]) -> str:
    def bullets(values: Any) -> str:
        items = values if isinstance(values, list) else []
        return "\n".join(f"- {item}" for item in items) or "- None specified"

    return f"""# Story Bible: {brief['title']}

## Premise
{brief.get('premise') or '[To be expanded by Architect]'}

## Audience and Language
- Audience: {brief.get('audience') or '[Not specified]'}
- Language: {brief.get('language') or '[Not specified]'}

## Genre and Tone
- Genre: {brief.get('genre') or '[Not specified]'}
- Tone: {brief.get('tone') or '[To be refined]'}
- POV: {brief.get('pov') or '[To be refined]'}

## Novel OS Classification
```json
{json.dumps(brief.get('classification') or {}, indent=2, ensure_ascii=False)}
```

### Story-Type Design Requirements
{bullets(brief.get('classification_design_requirements'))}

## Structure Targets
- Chapters: {brief.get('target_chapters')}
- Words: {brief.get('target_words')}

## Author-Controlled Narrative Format
```json
{json.dumps(brief.get('narrative_format') or {}, indent=2, ensure_ascii=False)}
```

## Must Include
{bullets(brief.get('must_have'))}

## Forbidden
{bullets(brief.get('forbidden'))}

## Source
The complete, unmodified source prompt is stored at `outputs/input/prompt.md`.
"""


def ingest_prompt(
    project_path: str | Path,
    source: str | Path,
    overrides: Optional[Mapping[str, Any]] = None,
    *,
    stdin_text: Optional[str] = None,
    stdin: Optional[TextIO] = None,
    generate_author: bool = True,
) -> PromptIntakeResult:
    project = Path(project_path)
    raw_prompt = _read_source(source, stdin_text, stdin)
    if not raw_prompt.strip():
        raise ValueError("Prompt is empty")

    brief = build_brief(raw_prompt, overrides)
    state_file = project / "outputs" / "state" / "story_state.json"
    state = (
        StoryState(str(project))
        if state_file.exists()
        else initialize_project(
            str(project), brief["title"], brief["genre"],
            author=brief["author"], language=brief["language"],
        )
    )
    for key in ("title", "genre", "author", "audience", "language", "tone", "pov"):
        if brief.get(key):
            state.set_metadata(key, brief[key])
    state.set_metadata("classification", brief["classification"])
    state.set_metadata("narrative_format", brief["narrative_format"])
    state.set_metadata("target_chapters", brief["target_chapters"])
    state.set_metadata("target_word_count", brief["target_words"])
    state.update_story_bible("premise", brief.get("premise", ""))
    state.update_story_bible("audience", brief.get("audience", ""))
    state.update_story_bible("language", brief.get("language", ""))
    state.update_story_bible("tone", brief.get("tone", ""))
    state.update_story_bible("pov", brief.get("pov", ""))
    state.update_story_bible("content_policy", brief.get("content_policy", ""))
    state.update_story_bible("must_have", brief.get("must_have", []))
    state.update_story_bible("forbidden", brief.get("forbidden", []))
    state.update_story_bible("prompt_source", "outputs/input/prompt.md")
    state.update_story_bible("classification", brief["classification"])
    state.update_story_bible("narrative_format", brief["narrative_format"])
    state.update_story_bible(
        "classification_design_requirements",
        brief["classification_design_requirements"],
    )
    if brief.get("commercial_story_contract"):
        state.set_metadata(
            "commercial_story_contract_id", brief["commercial_story_contract_id"]
        )
        state.update_story_bible(
            "commercial_story_contract", brief["commercial_story_contract"]
        )

    state.save_state()
    input_dir = project / "outputs" / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = input_dir / "prompt.md"
    brief_path = input_dir / "brief.json"
    bible_path = project / "outputs" / "story_bible.md"
    prompt_path.write_text(raw_prompt, encoding="utf-8")
    if generate_author:
        from book_author import ensure_project_author
        brief["author"] = ensure_project_author(project)
    else:
        brief["author"] = state.metadata.get("author", "")
    brief_path.write_text(json.dumps(brief, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    bible_path.write_text(_story_bible_markdown(brief), encoding="utf-8")

    return PromptIntakeResult(
        project_path=str(project),
        raw_prompt=raw_prompt,
        brief=brief,
        prompt_path=str(prompt_path),
        brief_path=str(brief_path),
        story_bible_path=str(bible_path),
    )
