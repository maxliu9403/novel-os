"""Story-first delivery contract and deterministic, readable skeleton rendering.

The model designs scenes; this module owns serialization. Saved global design and
chapter cards are the source, not another request to paraphrase them into a template.
Literary adequacy is deliberately not inferred from successful schema validation.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

VERSION = "story-first.v1"
MARKER = f"<!-- {VERSION} -->"
Text = Annotated[str, Field(min_length=1, max_length=40000)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class ChapterCard(Record):
    number: int = Field(ge=1, le=511)
    title: Text
    pov: Text
    core_plot: list[Text] = Field(min_length=1, max_length=12)
    reader_value: Text
    ending_hook: Text
    setups_payoffs: list[Text] = Field(default_factory=list, max_length=12)

    def markdown(self) -> str:
        lines = [f"### Chapter {self.number} · {self.title}",
                 f"- 叙事视角：{self.pov}", "- 核心剧情："]
        lines.extend(f"  {i}. {event}" for i, event in enumerate(self.core_plot, 1))
        lines.extend([f"- 看点／情绪回报：{self.reader_value}", f"- 章末钩子／余韵：{self.ending_hook}"])
        if self.setups_payoffs:
            lines.append("- 伏笔与回收：" + "；".join(self.setups_payoffs))
        return "\n".join(lines)


class StoryDesign(Record):
    title: Text
    author: Text
    premise: Text
    characters: Text
    world_rules: Text
    causal_chain: Text
    ending: Text
    decisions: Text
    voice: Text
    setups_payoffs: Text


DESIGN_RULES = """
STORY-FIRST DESIGN. Adopt the latest author choices and supplied source intent.
Carry forward concrete scenes already designed in conversation; fill meaningful gaps,
do not replace them with another story. Author decides major direction; develop
specific dramatic actions, relationships, opposition, earned rewards and aftermath.
Genre/Tags must affect what happens. No mandatory emotion sequence or revenge formula.
Return narrative_mode, total_chapters and story_design with these nonempty strings:
title, author (respect supplied author; otherwise propose a pen name), premise,
characters, world_rules, causal_chain, ending, decisions, voice, setups_payoffs.
Distinguish source facts, adopted choices and delegated new design. Do not write prose,
publication contracts, cover JSON or a 17-section template. The code owns packaging.
For multi_volume or series_installment also return narrative_format using the supplied
engine format schema, with meaningful volume boundaries and supplied series metadata.
"""

CHAPTER_RULES = """
Return narrative_mode, total_chapters and chapters, exactly once for each 1..N.
Each chapter: {number:int,title:string,pov:string,core_plot:[string],
reader_value:string,ending_hook:string,setups_payoffs:[string]}.
core_plot describes actual scenes: who acts, what happens, why it matters, and what
changes. Include expressive dialogue/action ideas and relational consequences, not
abstract labels. Preserve the saved whole-book route, confirmed capacity and scenes
already designed in discussion. Develop missing scenes without reopening adopted choices.
reader_value describes this chapter's earned experience, not a numerical quality score.
A quiet aftermath or other character's POV can matter without a protagonist decision.
Hooks arise from events; resolution may end in meaningful emotional closure.
Keep early rewards and later payoffs distinct; connect subplots through consequences.
No plan field: the application renders these same cards for reader and execution.
"""


def enabled(session: dict) -> bool:
    return session.get("creative_design", {}).get("policy", {}).get("story_engine") == VERSION


class NarrativeFormatValidationError(ValueError):
    """The story is structurally valid; only its packaging contract is invalid."""


def normalize_format_envelope(data: dict) -> dict:
    """Unwrap one unambiguous mode envelope without changing any field value.

    Older engine examples were indexed by mode. Some providers reproduced that
    index inside narrative_format. This is transport normalization, not inference;
    conflicting/multiple envelopes remain invalid and all domain checks still run.
    """
    mode = data.get('narrative_mode')
    value = data.get('narrative_format')
    if (mode in {'multi_volume', 'series_installment'} and isinstance(value, dict)
            and set(value) == {mode} and isinstance(value[mode], dict)
            and value[mode].get('mode') == mode):
        data = deepcopy(data)
        data['narrative_format'] = data['narrative_format'][mode]
    return data


def validate_stage(data: dict, stage: str) -> dict:
    """Validate semantics-bearing shapes before checkpoint save, without pruning text."""
    if stage == "story_design":
        StoryDesign.model_validate(data["story_design"])
        if data["narrative_mode"] in {"multi_volume", "series_installment"}:
            from core.narrative_format import NarrativeFormat
            data = normalize_format_envelope(data)
            try:
                if not isinstance(data.get("narrative_format"), dict):
                    raise ValueError("全局故事设计缺少完整分卷／系列配置。")
                structure = NarrativeFormat.from_dict(data["narrative_format"])
                if (structure.mode, structure.total_chapters) != (data["narrative_mode"], data["total_chapters"]):
                    raise ValueError("分卷／系列配置与全书组织形式或章数不一致。")
            except ValueError as exc:
                raise NarrativeFormatValidationError(str(exc)) from None
    else:
        cards = [ChapterCard.model_validate({k: v for k, v in item.items() if k not in {"target_words", "capacity_scene_ids"}}) for item in data["chapters"]]
        if [c.number for c in cards] != list(range(1, data["total_chapters"] + 1)):
            raise ValueError("章节设计缺章、重复或顺序错误。")
    return data


def render(session: dict, global_design: dict, chapter_plan: dict) -> dict:
    from core.narrative_format import NarrativeFormat, infer_narrative_format
    from core.publication_taxonomy import classification, bind_classification

    global_design = validate_stage(global_design, "story_design")
    validate_stage(chapter_plan, "chapter_plan")
    if (global_design["total_chapters"], global_design["narrative_mode"]) != (
            chapter_plan["total_chapters"], chapter_plan["narrative_mode"]):
        raise ValueError("故事设计与章卡的篇幅或组织形式不一致。")
    story = StoryDesign.model_validate(global_design["story_design"])
    # Allocation is code-owned; stage binding may add budget fields to cards.
    cards = [ChapterCard.model_validate({k: v for k, v in item.items()
              if k not in {"target_words", "capacity_scene_ids"}}) for item in chapter_plan["chapters"]]
    initial = session["input"]
    count = len(cards)
    allocation = (session.get("capacity", {}).get("confirmed") or {}).get("option")
    total = allocation["total_words"] if allocation else count * initial["words_per_chapter"]
    from core.publication_taxonomy import creation_channel
    taxonomy = classification(initial["publication_taxonomy"], count,
                              audience_channel=creation_channel(initial))
    mode = global_design["narrative_mode"]
    if mode in {"short_novel", "standalone_long"}:
        format_contract = infer_narrative_format(taxonomy, chapters=count, target_words=total,
            mode=mode, premise=story.premise, explicit_length=True, source="prompt_explicit")
    else:
        format_contract = NarrativeFormat.from_dict(global_design["narrative_format"])
        if (format_contract.mode, format_contract.total_chapters, format_contract.target_words) != (mode, count, total):
            raise ValueError("分卷设计必须遵循已确认的全书篇幅。")
    # Plain header values cannot create a second metadata field or heading.
    single = lambda value: re.sub(r"\s+", " ", str(value)).strip()
    header = "\n".join(f"{key}: {single(value)}" for key, value in {
        "Title": initial.get("title") or story.title, "Author": initial.get("author") or story.author,
        "Genre": taxonomy.to_dict()["display_labels"]["en-US"]["primary_genre"],
        "Audience": initial.get("audience") or {'female': 'Female readership', 'male': 'Male readership'}.get(initial.get('audience_channel'), 'General'),
        "Language": initial.get("language") or "English",
        "Tone": story.voice, "POV": " / ".join(dict.fromkeys(c.pov for c in cards)),
        "Chapters": count, "Words": total,
    }.items())
    sections = [
        ("故事承诺与创作决定", story.premise + "\n\n" + story.decisions),
        ("人物关系与世界规则", story.characters + "\n\n" + story.world_rules),
        ("全书故事走向", story.causal_chain),
        ("逐章故事设计", "\n\n".join(c.markdown() for c in cards)),
        ("伏笔、回收与阅读承诺", story.setups_payoffs),
        ("高潮结局与正文表达", story.ending + "\n\n" + story.voice),
    ]
    prompt = MARKER + "\n" + header + "\n\n" + "\n\n".join(
        f"## {i}. {title}\n\n{body}" for i, (title, body) in enumerate(sections, 1))
    prompt += "\n\n[NARRATIVE_FORMAT_JSON]\n" + json.dumps(format_contract.to_dict(), ensure_ascii=False, indent=2) + "\n[/NARRATIVE_FORMAT_JSON]\n"
    return {"reply": "完整故事与章卡已保存，可查看骨架后启动正文。", "questions": [],
            "prompt_md": bind_classification(prompt, initial), "ready_for_confirmation": True}
