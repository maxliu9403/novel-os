"""A saved chapter plan never needs a second model transcription to publish."""
from copy import deepcopy
import json
import re

import pytest

from api import workshop
from api.workshop_assembly import AssemblyError, AssemblyPlan, ASSEMBLY_PARTS, parse_part
from test_workshop_api import complete_prompt
from workshop_delivery_fixtures import story_design, chapter_plan
from workshop_assembly_fixtures import parts_from_prompt
from test_workshop_handoff import make_prompt
from api.workshop_delivery import parse_stage


def test_assembly_reuses_every_saved_story_fact_and_chapter_without_model_transcription(tmp_path):
    story, chapters = story_design(), chapter_plan()
    chapters["chapters"][3]["plan"] += "\n原样保留证据：稀有字符𠀀 / 引号\" / 尾部细节。"
    parts = parts_from_prompt(complete_prompt(tmp_path))
    plan = AssemblyPlan(story, chapters)
    for part in ASSEMBLY_PARTS:
        assert plan.part == part
        request = plan.context({"initial_brief": {"premise": "完整作者事实"}, "conversation": [{"content": "全部历史"}]})
        assert request["assembly_part"] == part
        assert request["initial_brief"]["premise"] == "完整作者事实"
        assert request["saved_story_stages"]["chapter_plan"] == chapters
        assert "prompt_md" not in plan.rules().split("Return ONLY", 1)[-1]
        parsed = parse_part(json.dumps(parts[part]), part, "short_novel", 21)
        plan.accept(part, parsed)
    assert plan.part is None
    result = plan.assemble()
    prompt = result["prompt_md"]
    for value in story["story_design"].values():
        assert value in prompt
    for chapter in chapters["chapters"]:
        assert prompt.count(chapter["plan"]) == 1
    assert [int(n) for n in re.findall(r"^## (\d+)\.", prompt, re.M)] == list(range(1, 18))
    path = tmp_path / "assembled.md"
    path.write_text(prompt)
    assert workshop._validate_candidate(path)["chapters"] == 21


def test_partial_resume_only_requests_missing_parts_and_preserves_saved_artifacts(tmp_path):
    parts = parts_from_prompt(complete_prompt(tmp_path))
    saved = {part: {"content": parts[part], "content_md": "saved", "saved_at": "now"} for part in ASSEMBLY_PARTS[:3]}
    baseline = deepcopy(saved)
    plan = AssemblyPlan(story_design(), chapter_plan(), saved)
    assert plan.part == "reader_style"
    assert saved == baseline
    with pytest.raises(AssemblyError, match="未完成"):
        plan.assemble()


@pytest.mark.parametrize("change", [
    lambda d: d.update(prompt_md="whole monolithic skeleton"),
    lambda d: d["sections"].update({"12": "rewrite every chapter"}),
    lambda d: d.update(total_chapters=22),
    lambda d: d.update(narrative_mode="standalone_long"),
    lambda d: d["sections"].update({"6": ""}),
    lambda d: d["sections"].update({"6": "## 12. Sneaked full chapter plan"}),
])
def test_part_cannot_expand_back_into_monolithic_or_incomplete_delivery(tmp_path, change):
    data = parts_from_prompt(complete_prompt(tmp_path))["relationships_world"]
    change(data)
    with pytest.raises(AssemblyError):
        parse_part(json.dumps(data), "relationships_world", "short_novel", 21)


def test_contract_failures_are_caught_before_part_is_saved(tmp_path):
    parts = parts_from_prompt(complete_prompt(tmp_path))
    parts["publication"]["metadata"]["words"] += 1
    with pytest.raises(AssemblyError, match="publication"):
        parse_part(json.dumps(parts["publication"]), "publication", "short_novel", 21)
    parts["cover_handoff"]["sections"]["15"] = 'COVER_HANDOFF_BEGIN\n{"schema_version":2}\nCOVER_HANDOFF_END'
    with pytest.raises(AssemblyError, match="cover_handoff"):
        parse_part(json.dumps(parts["cover_handoff"]), "cover_handoff", "short_novel", 21)


@pytest.mark.parametrize("mode", ["short_novel", "standalone_long", "multi_volume", "series_installment"])
def test_assembled_contracts_preserve_each_approved_narrative_mode(tmp_path, mode):
    fixture = complete_prompt(tmp_path)
    contracts = make_prompt(tmp_path, mode=mode).read_text().split("## Engine contracts", 1)[1].split("## Chapters", 1)[0]
    parts = parts_from_prompt(fixture, mode)
    parts["publication"]["sections"]["1"] = contracts
    plan = AssemblyPlan(story_design(mode=mode), chapter_plan(mode=mode), parts)
    prompt = plan.assemble()["prompt_md"]
    path = tmp_path / f"{mode}.md"
    path.write_text(prompt)
    assert workshop._validate_candidate(path)["mode"] == mode
    if mode == "multi_volume":
        assert "volume_02" in prompt.split("## 9.")[1].split("## 10.")[0]
    if mode == "series_installment":
        assert "第 2 册，本次仅创作当前册" in prompt


def test_saved_part_correction_only_replaces_requested_supplement(tmp_path):
    parts = parts_from_prompt(complete_prompt(tmp_path))
    plan = AssemblyPlan(story_design(), chapter_plan(), parts)
    before = plan.assemble()["prompt_md"]
    correction = deepcopy(parts["relationships_world"])
    correction["sections"]["6"] += "\n明确关系在第七章发生变化。"
    assert plan.context({}, "relationships_world")["assembly_part"] == "relationships_world"
    assert "assembly_part=relationships_world" in plan.rules("relationships_world")
    plan.accept("relationships_world", correction)
    after = plan.assemble()["prompt_md"]
    assert after == before.replace(parts["relationships_world"]["sections"]["6"] + "\n\n## 7.",
                                   correction["sections"]["6"] + "\n\n## 7.")


def test_cross_part_contract_and_metadata_injection_rejected(tmp_path):
    parts = parts_from_prompt(complete_prompt(tmp_path))
    for body in ("[NARRATIVE_FORMAT_JSON]{}[/NARRATIVE_FORMAT_JSON]", "Primary audience: Different audience"):
        data = deepcopy(parts["relationships_world"])
        data["sections"]["6"] += "\n" + body
        with pytest.raises(AssemblyError, match="片段|元数据"):
            parse_part(json.dumps(data), "relationships_world", "short_novel", 21)


def test_duplicate_json_cannot_silently_replace_saved_chapter_count(tmp_path):
    data = parts_from_prompt(complete_prompt(tmp_path))["relationships_world"]
    raw = json.dumps(data).replace('"total_chapters": 21', '"total_chapters": 7, "total_chapters": 21')
    with pytest.raises(AssemblyError, match="重复"):
        parse_part(raw, "relationships_world", "short_novel", 21)


def test_accepted_stage_markdown_and_multiline_title_assemble_without_duplicate_structure(tmp_path):
    story = story_design()
    story["story_design"]["premise"] = "## 3. Saved story heading\n" + story["story_design"]["premise"]
    chapters = chapter_plan()
    chapters["chapters"][0]["title"] = "Choice one\nA second title line"
    chapters["chapters"][0]["plan"] = "### Chapter 1 — Already rendered\n## 12. Saved chapter section\n" + chapters["chapters"][0]["plan"]
    story = parse_stage(json.dumps(story), "story_design", "short_novel")
    chapters = parse_stage(json.dumps(chapters), "chapter_plan", "short_novel", 21)
    original_story, original_chapters = deepcopy(story), deepcopy(chapters)
    plan = AssemblyPlan(story, chapters, parts_from_prompt(complete_prompt(tmp_path)))
    prompt = plan.assemble()["prompt_md"]
    assert "#### 3. Saved story heading" in prompt
    assert "#### Chapter 1 — Already rendered" in prompt
    assert "#### 12. Saved chapter section" in prompt
    assert "### Chapter 1 — Choice one A second title line" in prompt
    assert json.dumps(chapters["chapters"][0]["title"], ensure_ascii=False) in prompt
    assert story == original_story and chapters == original_chapters
    path = tmp_path / "normalized-stage-headings.md"
    path.write_text(prompt)
    assert workshop._validate_candidate(path)["chapters"] == 21


def test_all_accepted_stage_extension_fields_survive_local_assembly(tmp_path):
    story = story_design()
    story["source_constraints"] = {"original_access": ["permit_A", "permit_B"]}
    story["story_design"]["subplot"] = {"unique_subplot": "The sibling kept the blue receipt", "quote": "literal ``` fence"}
    chapters = chapter_plan()
    chapters["sequence_checks"] = ["unique_whole_map_fact", {"unanswered": False}]
    chapters["chapters"][3]["foreshadowing"] = {"unique_extra_chapter_fact": "ch4:fs2", "nested": [None, 4, True]}
    story = parse_stage(json.dumps(story), "story_design", "short_novel")
    chapters = parse_stage(json.dumps(chapters), "chapter_plan", "short_novel", 21)
    plan = AssemblyPlan(story, chapters, parts_from_prompt(complete_prompt(tmp_path)))
    prompt = plan.assemble()["prompt_md"]
    records = [json.loads(text) for text in re.findall(r"```json\n(.*?)\n```", prompt, re.S)]
    assert {"source_constraints": story["source_constraints"]} in records
    assert {"subplot": story["story_design"]["subplot"]} in records
    assert {"sequence_checks": chapters["sequence_checks"]} in records
    assert {"foreshadowing": chapters["chapters"][3]["foreshadowing"]} in records
    for chapter in chapters["chapters"]:
        assert prompt.count(chapter["plan"]) == 1
    path = tmp_path / "retained-extensions.md"
    path.write_text(prompt)
    assert workshop._validate_candidate(path)["chapters"] == 21


@pytest.mark.parametrize("line", ["POV: Mason", "Primary audience: A different reader",
                                  "Point of view: First person", "Primary category: Mystery",
                                  "- **Language**: Chinese", "Author:", "  Title: Chapter-local title"])
def test_saved_metadata_lines_are_nested_without_overriding_book_metadata(tmp_path, line):
    story = story_design()
    story["story_design"]["characters"] += "\n" + line
    story["story_design"]["premise"] = line + "\n" + story["story_design"]["premise"]
    chapters = chapter_plan()
    chapters["chapters"][0]["plan"] = line + "\n" + chapters["chapters"][0]["plan"]
    story = parse_stage(json.dumps(story), "story_design", "short_novel")
    chapters = parse_stage(json.dumps(chapters), "chapter_plan", "short_novel", 21)
    original_story, original_chapters = deepcopy(story), deepcopy(chapters)
    plan = AssemblyPlan(story, chapters, parts_from_prompt(complete_prompt(tmp_path)))
    prompt = plan.assemble()["prompt_md"]
    path = tmp_path / "nested-metadata.md"
    path.write_text(prompt)
    assert workshop._validate_candidate(path)["chapters"] == 21
    assert f"> {line}" in prompt
    assert story == original_story and chapters == original_chapters
