"""Bounded publication supplements and lossless local skeleton assembly.

The model never transcribes the already-saved chapter plan. Each request owns
only a few missing sections; complete parts can be saved independently. This
module neither persists data nor approves/publishes a skeleton: the existing
whole-document workshop validator remains the final gate.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re


ASSEMBLY_PROTOCOL_VERSION = "workshop.assembly-parts.v1"
ASSEMBLY_PARTS = ("publication", "relationships_world", "secrets_payoffs",
                  "reader_style", "cover_handoff", "delivery_review")
ASSEMBLY_PART_LABELS = {
    "publication": "创作规格", "relationships_world": "人物关系与世界规则",
    "secrets_payoffs": "秘密与伏笔", "reader_style": "阅读回报与文风",
    "cover_handoff": "封面故事交接", "delivery_review": "交付复核",
}
PART_SECTIONS = {
    "publication": (1, 2, 4), "relationships_world": (6, 7),
    "secrets_payoffs": (8, 10), "reader_style": (11, 14),
    "cover_handoff": (15,), "delivery_review": (17,),
}
SECTION_TITLES = {
    1: "创作规格与结构配置", 2: "目标读者、地域与语言", 3: "故事核心与完整梗概",
    4: "硬性限制", 5: "人物身份、性格与心理档案", 6: "人物关系及变化",
    7: "世界规则、时间线与现实约束", 8: "秘密、知情范围与证据链",
    9: "全书／分卷／系列结构", 10: "伏笔、揭示与回收总表", 11: "开篇吸引力与阅读回报",
    12: "完整分章细纲", 13: "结局与人物归宿", 14: "文风与正文创作规范",
    15: "封面交接与交付要求", 16: "假设与选定决策记录", 17: "交付前检查记录",
}
_METADATA = ("title", "author", "genre", "audience", "language", "tone", "pov", "words")
_MODES = {"short_novel", "standalone_long", "multi_volume", "series_installment"}
_GUIDANCE = {
    "publication": """Supply metadata and only sections 1, 2, 4. Metadata is an object with
title, author (may be empty for automatic pen name), genre, audience, language, tone,
pov (all single-line strings), and words (the chosen positive integer total target).
Derive values from accepted author decisions; do not copy schema example values.
Section 1 contains exactly one complete NOVEL_CLASSIFICATION_JSON and one complete
NARRATIVE_FORMAT_JSON block, plus the actual creation specifications and counting policy.
Keep narrative_mode, total_chapters, word target, series identity and volume boundaries
consistent with the saved design. Pending confirmation is permitted until UI approval.
Section 2 explains the accepted audience, release market versus story setting, language,
reading promise and creative implications. Section 4 records actual hard constraints and
where they apply; do not promote generic preferences into author requirements.""",
    "relationships_world": """Supply only sections 6 and 7. Build a concrete relationship
ledger with stable character references, power, expectations, actual trust/conflict changes
and final states, linked to saved chapters. Establish world rules, resources, realistic
limits, dates, durations and knowledge boundaries. Reuse the complete saved character
identities and arcs; do not rewrite or change the character bible. Mark unverified real-world
details as requiring verification instead of claiming research occurred.""",
    "secrets_payoffs": """Supply only sections 8 and 10. Make the secret/evidence ledger and
setup/payoff ledger coherent with EVERY saved chapter and the ending. Use the existing
stable IDs from saved plans; name chapter numbers, access paths/costs, knowledge limits,
fair misreadings, reveal consequences and payoff windows. Do not invent additional major
twists, quietly drop setup IDs or reopen the ending. Record when no item is applicable.
Ensure references can guide the later ending_contract without claiming it already exists.""",
    "reader_style": """Supply only sections 11 and 14. Section 11 gives concrete opening
collision, emotional identification, early chapter rewards, retained later promises and
the story_lead_contract/marker protocol. Include a valid COMMERCIAL_STORY_JSON only when
the selected commercial/retention branch requires it; derive its exact fields from the
provided references and saved story. Do not add a generic empty commercial contract.
Section 14 supplies the actual POV/tense/voice, character-specific dialogue, scene rhythm,
emotional transition and draft review guidance, along with existing Agent state/continuity
and payoff protocols. Do not write prose samples, chapter drafts or re-copy chapter plans.""",
    "cover_handoff": """Supply only section 15. Produce exactly one complete schema_version
2 COVER_HANDOFF_BEGIN / COVER_HANDOFF_END JSON object using the provided schema. It is a
story-facts handoff, not cover art direction. Preserve every relevant principal identity,
relationship and story constraint, with source_refs to this skeleton and stable story IDs.
Never invent confirmed appearance facts; use the schema's explicitly unconfirmed assumptions
where needed. Do not add cover concepts, prompts, image calls or design-review claims.
Reuse metadata from the saved publication part exactly. Include the normal manuscript
delivery formats and explain that covers are generated separately by the cover workflow.""",
    "delivery_review": """Supply only section 17, a concise public design check of the full
saved story, chapter plan and all saved supplements. Check continuity, author constraints,
character choices, causality, ending/payoff references and consistency between contracts.
Report concrete design assumptions or remaining verification needs honestly. This is a
design record, not a rewrite or private chain of thought. Parser validation is performed
by the application after local assembly; do not claim it ran already. Draft/prose review
is not_run and creation has not started. Do not repeat the full chapter map or any other
section. The application will still apply the unchanged whole-document validation gate.""",
}


class AssemblyError(ValueError):
    """Safe, part-scoped validation failure without raw story output."""


def _fail(part: str, message: str):
    raise AssemblyError(f"骨架交付片段 {part}：{message}")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AssemblyError("骨架交付片段存在重复 JSON 字段。")
        result[key] = value
    return result


def _body_valid(body: str) -> bool:
    return (isinstance(body, str) and bool(body.strip())
            and not re.search(r"^##\s+\d+\.", body, re.M)
            and not re.search(r"\{\{[^}]+\}\}|\b(?:TODO|TBD)\b|后续略|待补充", body)
            and body.count("```") % 2 == 0)


def _contract_scope(sections: dict, part: str):
    # A small section may not smuggle a second machine contract into a later
    # document location and thereby override another saved part.
    owners = {"NOVEL_CLASSIFICATION_JSON": ("publication", "1"),
              "NARRATIVE_FORMAT_JSON": ("publication", "1"),
              "COMMERCIAL_STORY_JSON": ("reader_style", "11"),
              "COVER_HANDOFF_BEGIN": ("cover_handoff", "15"),
              "COVER_HANDOFF_END": ("cover_handoff", "15")}
    from core.prompt_intake import _FIELD_RE, _FIELD_ALIASES
    for section, body in sections.items():
        for marker, location in owners.items():
            if marker in body and location != (part, section):
                _fail(part, f"{marker} 不属于当前片段或章节。")
        for line in body.splitlines():
            match = _FIELD_RE.match(line)
            if match:
                key = match[1].lower().replace(" ", "_")
                key = _FIELD_ALIASES.get(key, key)
                if key in {*_METADATA, "chapters"}:
                    _fail(part, "正文不能重复顶层元数据字段或其别名，请使用带引号键名的 JSON。")


def parse_part(text: str, part_id: str, mode: str, chapter_count: int) -> dict:
    """Validate a complete scoped artifact before it becomes a checkpoint."""
    if part_id not in PART_SECTIONS:
        _fail(str(part_id), "未知的片段。")
    stripped = text.strip()
    if stripped.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.I | re.S)
        stripped = match[1] if match else stripped
    try:
        data = json.loads(stripped, object_pairs_hook=_strict_object,
                          parse_constant=lambda _: _fail(part_id, "不能包含非有限 JSON 数值。"))
    except (json.JSONDecodeError, TypeError):
        _fail(part_id, "结果不是完整的 JSON 对象。")
    expected = {"part_id", "narrative_mode", "total_chapters", "sections"}
    if part_id == "publication":
        expected.add("metadata")
    if not isinstance(data, dict) or set(data) != expected:
        _fail(part_id, "只返回当前片段规定的字段，不能返回完整骨架或额外片段。")
    if (data["part_id"] != part_id or mode not in _MODES or data["narrative_mode"] != mode
            or type(data["total_chapters"]) is not int or data["total_chapters"] != chapter_count):
        _fail(part_id, "片段身份、组织形式或章节数与已保存设计不一致。")
    sections = data["sections"]
    if (not isinstance(sections, dict) or set(sections) != {str(n) for n in PART_SECTIONS[part_id]}
            or not all(_body_valid(body) for body in sections.values())):
        _fail(part_id, "必须完整填写当前节，且不能包含编号节标题、占位内容或未闭合代码块。")
    _contract_scope(sections, part_id)
    try:
        if part_id == "publication":
            metadata = data["metadata"]
            if (not isinstance(metadata, dict) or set(metadata) != set(_METADATA)
                    or type(metadata["words"]) is not int or metadata["words"] < chapter_count
                    or any(not isinstance(metadata[k], str) or "\n" in metadata[k] or "\r" in metadata[k]
                           or (k != "author" and not metadata[k].strip()) for k in _METADATA[:-1])):
                _fail(part_id, "需要完整的单行元数据和有效总字数。")
            from core.novel_classification import parse_classification_block
            from core.narrative_format import parse_narrative_format_block
            for name in ("NOVEL_CLASSIFICATION_JSON", "NARRATIVE_FORMAT_JSON"):
                if (len(re.findall(rf"\[{name}\]", sections["1"], re.I)) != 1
                        or len(re.findall(rf"\[/{name}\]", sections["1"], re.I)) != 1):
                    _fail(part_id, f"需要唯一完整的 {name} 合同。")
            classification = parse_classification_block(sections["1"])
            narrative = parse_narrative_format_block(sections["1"])
            if classification is None or narrative is None:
                _fail(part_id, "分类或结构合同缺失。")
            if (narrative.mode != mode or narrative.total_chapters != chapter_count
                    or narrative.target_words != metadata["words"]):
                _fail(part_id, "结构合同必须与已保存组织形式、章节数和元数据总字数相同。")
        elif part_id == "cover_handoff":
            from core.cover_handoff import parse_cover_handoff
            from .workshop_contract_repair import normalize_workshop_contracts
            normalized, _ = normalize_workshop_contracts(sections["15"])
            cover = parse_cover_handoff(normalized)
            if cover.schema_version != 2:
                _fail(part_id, "需要完整的第 2 版封面故事交接。")
        elif part_id == "reader_style":
            from core.commercial_story import parse_commercial_story_block
            parse_commercial_story_block(sections["11"])
    except AssemblyError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        _fail(part_id, f"合同尚不完整：{str(exc)[:220]}")
    return data


def _section(number: int, body: str) -> str:
    return f"## {number}. {SECTION_TITLES[number]}\n\n{body}"


def _normalize_saved_body(body: str) -> str:
    """Nest legacy rendered headings/metadata under the owned book structure.

    Old stage validation accepted Markdown chapter/section wrappers inside a
    free-form field, as well as natural ``POV:`` chapter labels. The intake scans
    the entire document, so those local labels must not become book metadata.
    Only heading levels and quote prefixes change; all original line text and
    story content remain intact, and ordinary bodies remain byte-for-byte equal.
    """
    body = re.sub(r"^##(?=\s+[0-9]+\.\s)", "####", body, flags=re.M)
    body = re.sub(r"^###(?=\s+Chapter\s+[0-9]+\b)", "####", body, flags=re.M | re.I)
    from core.prompt_intake import _FIELD_RE
    # Handoff validation also counts empty canonical fields (notably Author:),
    # while intake's nonempty matcher supplies all recognized metadata aliases.
    empty_field = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?(?:"
                             + "|".join((*_METADATA, "chapters"))
                             + r")(?:\*\*)?\s*:[ \t]*$", re.I)
    return "".join(
        "> " + line if _FIELD_RE.match(line.rstrip("\r\n")) or empty_field.match(line.rstrip("\r\n")) else line
        for line in body.splitlines(keepends=True)
    )


def _supplement(label: str, fields: dict) -> str:
    if not fields:
        return ""
    # Encoding literal backticks preserves their JSON value without letting a
    # quoted extension field accidentally close the document's Markdown fence.
    encoded = json.dumps(fields, ensure_ascii=False, indent=2).replace("`", "\\u0060")
    return f"\n\n### {label}\n\n```json\n{encoded}\n```"


def _extra_fields(value: dict, known: set[str]) -> dict:
    return {key: item for key, item in value.items() if key not in known}


def _chapter(chapter: dict) -> str:
    title = chapter["title"]
    lines = title.splitlines()
    heading_title = " ".join(lines) if len(lines) > 1 or "\r" in title or "\n" in title else title
    rendered = f"### Chapter {chapter['number']} — {heading_title}\n\n{_normalize_saved_body(chapter['plan'])}"
    if heading_title != title:
        rendered += _supplement("原始多行章标题", {"title": title})
    return rendered + _supplement("本章补充规划记录", _extra_fields(chapter, {"number", "title", "plan"}))


def render_part(part_id: str, data: dict) -> str:
    return "\n\n".join(_section(number, data["sections"][str(number)]) for number in PART_SECTIONS[part_id])


def repair_part(error: str) -> str | None:
    """Locate a failing supplement; never fall back to rewriting the whole book."""
    for part in ASSEMBLY_PARTS:
        if part in error:
            return part
    lowered = error.lower()
    if any(token in lowered for token in ("cover", "封面")):
        return "cover_handoff"
    if any(token in lowered for token in ("commercial", "story_lead")):
        return "reader_style"
    if any(token in lowered for token in ("narrative", "classification", "metadata", "结构配置", "章节数", "总字数")):
        return "publication"
    match = re.search(r"第\s*(\d+)\s*节", error)
    if match:
        number = int(match[1])
        return next((part for part, sections in PART_SECTIONS.items() if number in sections), None)
    return None


class AssemblyPlan:
    """Pure plan over complete story artifacts and scoped saved supplements."""

    def __init__(self, story_data: dict, chapter_data: dict, saved_parts: dict | None = None):
        self.story = deepcopy(story_data)
        self.chapters = deepcopy(chapter_data)
        self.mode = self.story.get("narrative_mode")
        self.chapter_count = self.story.get("total_chapters")
        fields = self.story.get("story_design", {})
        if (self.mode not in _MODES or type(self.chapter_count) is not int or self.chapter_count < 1
                or self.chapters.get("narrative_mode") != self.mode
                or self.chapters.get("total_chapters") != self.chapter_count
                or not all(isinstance(fields.get(k), str) and fields[k].strip() for k in
                           ("premise", "characters", "causal_chain", "ending", "decisions"))):
            raise AssemblyError("本地整合需要完整且一致的已保存故事与章节规划。")
        chapters = self.chapters.get("chapters")
        if (not isinstance(chapters, list) or len(chapters) != self.chapter_count or any(
                not isinstance(c, dict) or type(c.get("number")) is not int or c["number"] != n
                or not isinstance(c.get("title"), str) or not c["title"].strip()
                or not isinstance(c.get("plan"), str) or not c["plan"].strip()
                for n, c in enumerate(chapters, 1))):
            raise AssemblyError("本地整合需要全部且顺序一致的已保存章节。")
        self.parts = {}
        for part_id, artifact in (saved_parts or {}).items():
            data = artifact.get("content", artifact)
            self.parts[part_id] = parse_part(json.dumps(data, ensure_ascii=False), part_id, self.mode, self.chapter_count)

    @property
    def part(self) -> str | None:
        return next((part for part in ASSEMBLY_PARTS if part not in self.parts), None)

    def _part(self, part_id: str | None) -> str:
        part = part_id or self.part
        if part not in PART_SECTIONS:
            raise AssemblyError("没有待生成的交付片段。")
        return part

    def context(self, base: dict, part_id: str | None = None) -> dict:
        part = self._part(part_id)
        return {**deepcopy(base), "delivery_stage": "assembly", "delivery_mode": self.mode,
                "assembly_protocol": ASSEMBLY_PROTOCOL_VERSION, "assembly_part": part,
                "saved_story_stages": {"story_design": deepcopy(self.story), "chapter_plan": deepcopy(self.chapters)},
                "saved_assembly_parts": deepcopy(self.parts)}

    def rules(self, part_id: str | None = None) -> str:
        part = self._part(part_id)
        example = {"part_id": part, "narrative_mode": self.mode, "total_chapters": self.chapter_count,
                   "sections": {str(n): f"Complete body of section {n}; no numbered ## heading" for n in PART_SECTIONS[part]}}
        if part == "publication":
            example["metadata"] = {k: "actual accepted value" for k in _METADATA}
            example["metadata"]["words"] = "positive integer actual target, not this schema string"
        return f"""
CURRENT REQUEST PROFILE: BOUNDED SKELETON ASSEMBLY PART ({ASSEMBLY_PROTOCOL_VERSION}).
This dedicated transport replaces the full-skeleton response format in the references.
Read the FULL author context, both saved story stages and every saved assembly part.
Preserve approved facts, decisions, constraints, chapter count and ending. Do not reopen
whole-book alternatives. The application will insert the saved premise, character bible,
causal chain, ending, decisions and EVERY saved chapter plan verbatim. Do not transcribe
those sections or return a complete document. Complete only assembly_part={part}.
Use concrete source-grounded facts with sufficient detail for writing; do not compress
away evidence or invent missing user approvals. Keep this response to the requested
supplement, referencing saved chapter/character/payoff IDs instead of repeating them.
Section bodies omit numbered ## section headings and top metadata labels. Keep nested
metadata in JSON with quoted keys, and close all JSON/Markdown fences. Do not invoke
tools, produce chapter prose, generate images or claim execution of external validation.
{_GUIDANCE[part]}
Return ONLY this JSON shape (fill actual complete content; no additional response keys):
{json.dumps(example, ensure_ascii=False)}
"""

    def accept(self, part_id: str, data: dict):
        self.parts[part_id] = parse_part(json.dumps(data, ensure_ascii=False), part_id, self.mode, self.chapter_count)

    def assemble(self) -> dict:
        if self.part is not None:
            raise AssemblyError("交付片段尚未完成，不能发布局部骨架。")
        saved_story = self.story["story_design"]
        story_fields = {"premise", "characters", "causal_chain", "ending", "decisions"}
        story = {key: _normalize_saved_body(saved_story[key]) for key in story_fields}
        metadata = self.parts["publication"]["metadata"]
        headers = [f"# {metadata['title']}"]
        for key in _METADATA[:-1]:
            label = "POV" if key == "pov" else key.title()
            headers.append(f"{label}: {metadata[key]}")
        headers.extend((f"Chapters: {self.chapter_count}", f"Words: {metadata['words']}"))
        body = "\n".join(headers) + "\n\n" + story["premise"]
        sections = {3: story["premise"] + "\n\n" + story["causal_chain"]
                       + _supplement("故事设计补充记录", _extra_fields(saved_story, story_fields)),
                    5: story["characters"], 9: story["causal_chain"],
                    12: "\n\n".join(_chapter(chapter) for chapter in self.chapters["chapters"])
                        + _supplement("完整章节规划补充记录", _extra_fields(self.chapters, {"narrative_mode", "total_chapters", "chapters"})),
                    13: story["ending"],
                    16: story["decisions"] + _supplement("全局设计补充记录", _extra_fields(self.story, {"narrative_mode", "total_chapters", "story_design"}))}
        for data in self.parts.values():
            sections.update({int(number): text for number, text in data["sections"].items()})
        # The chosen structure is already validated in publication. Showing its
        # actual serialized spans here adds no model-invented structure or facts.
        from core.narrative_format import parse_narrative_format_block
        narrative = parse_narrative_format_block(sections[1])
        if narrative.mode in {"multi_volume", "series_installment"}:
            spans = "\n".join(f"- 第 {v.volume_number} 卷（{v.volume_id}）：第 {v.chapter_start}–{v.chapter_end} 章；{v.structural_role}" for v in narrative.volumes)
            sections[9] += "\n\n### 已选结构范围\n\n" + spans
            if narrative.mode == "series_installment":
                sections[9] += f"\n\n系列《{narrative.series_title}》第 {narrative.series_book_number} 册，本次仅创作当前册。"
        prompt = body + "\n\n" + "\n\n".join(_section(n, sections[n]) for n in range(1, 18)) + "\n"
        return {"reply": "### 骨架已整理\n\n已复用保存的故事设计和完整章节规划，补齐创作规格与交付内容。请查看并确认本版骨架。",
                "prompt_md": prompt, "ready_for_confirmation": True, "questions": []}
