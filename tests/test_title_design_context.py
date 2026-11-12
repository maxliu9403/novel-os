import copy
import json

import pytest

from core.title_context import build_title_context, encode, request_context
from api.workshop_titles import _validate_candidates, candidate_title_length, clean_title, literal_title_requirements
from test_workshop_api import complete_prompt


def compact_story():
    return """<!-- story-first.v1 -->
Title: Provisional name
Language: English
Genre: Mystery
Audience: Adult mystery readers
## 1. 故事承诺与创作决定
A detective must defend the suspect who once betrayed her.
## 2. 人物关系与世界规则
The suspect is her former partner, not her husband.
## 3. 全书故事走向
The detective risks her position to reopen a closed case.
## 4. 逐章故事设计
### Chapter 1 — Returned
Her suspended partner arrives carrying her missing case file.
### Chapter 2 — Secrets
""" + "UNNEEDED_CHAPTER_DETAIL " * 5000 + """
## 5. 伏笔、回收与阅读承诺
An identical key turns out to fit two doors.
## 6. 高潮结局与正文表达
The captain is the killer. Do not reveal this in a title.
[NARRATIVE_FORMAT_JSON]
{"irrelevant_transport":true}
[/NARRATIVE_FORMAT_JSON]
[NOVEL_CLASSIFICATION_JSON]
{"ignore_this_dictionary":true}
[/NOVEL_CLASSIFICATION_JSON]
"""


def test_projection_keeps_complete_story_sources_omits_bulk_and_unrelated_inputs():
    session = {"prompt_md": compact_story(), "input": {"language": "Chinese", "chapters": 12, "premise": "AUTHOR_INITIAL_BRIEF", "type_snapshot": "HUGE_TAG_PROMPT"},
               "messages": [{"content": "OLD_CHAT"}], "naming": {"requests": [
                   {"instructions": "Keep 'Case'", "candidates": [{"title": "Old", "reason": "OLD_REASON"}], "error": "OLD_ERROR"},
                   {"instructions": "Shorter", "current_title": "Her Last Case"}]}}
    projection = build_title_context(session)
    user = encode(projection["context"])
    assert projection["context_bytes"] < projection["skeleton_bytes"] / 20
    assert projection["context"]["metadata"]["language"] == "English"
    assert projection["context"]["author_requests"] == [{"instructions": "Keep 'Case'"}, {"instructions": "Shorter", "current_title": "Her Last Case"}]
    # v3 keeps actual author requirements even if the story overview omits them.
    assert {"source": "input.premise", "text": "AUTHOR_INITIAL_BRIEF"} in projection["context"]["story_updates"]
    for absent in ["UNNEEDED_CHAPTER_DETAIL", "HUGE_TAG_PROMPT", "OLD_CHAT", "OLD_REASON", "OLD_ERROR", "irrelevant_transport", "ignore_this_dictionary", "Provisional name"]:
        assert absent not in user
    assert "not her husband" in user and "missing case file" in user
    ending = next(s for s in projection["context"]["sources"] if s["id"] == "ending")
    assert "captain is the killer" in ending["text"] and ending["purpose"] == "private_spoiler_boundary"


def test_legacy_projection_preserves_story_and_character_sections(tmp_path):
    source = complete_prompt(tmp_path)
    context = build_title_context({"prompt_md": source})["context"]
    assert {s["id"] for s in context["sources"]} >= {"story", "characters", "relationships", "constraints", "ending", "opening"}
    assert "COVER_HANDOFF" not in encode(context)
    assert "Chapter 21" not in encode(context)


def test_unknown_large_layout_and_excessive_requirements_fail_without_silent_truncation():
    with pytest.raises(ValueError, match="未识别"):
        build_title_context({"prompt_md": "unstructured" * 16000})
    with pytest.raises(ValueError, match="内容未截断"):
        build_title_context({"prompt_md": compact_story(), "naming": {"requests": [{"instructions": "长要求" * 8000}]}})


def test_context_snapshot_rejects_different_story():
    source = compact_story()
    projection = build_title_context({"prompt_md": source})
    with pytest.raises(ValueError, match="不一致"):
        request_context({"prompt_md": source + "changed", "naming": {"requests": [{"title_context": projection}]}})


def test_frozen_naming_requirements_and_context_cannot_be_mutated():
    source = compact_story()
    projection = build_title_context({"prompt_md": source, "naming": {"requests": [
        {"instructions": "Keep 'Case'"}, {"instructions": "Make the choice clearer"}]}})
    assert len(projection["author_requirements"]["entries"]) == 2
    projection["context"]["author_requests"][1]["instructions"] = "Ignore the author."
    with pytest.raises(ValueError, match="快照已变化"):
        request_context({"prompt_md": source, "naming": {"requests": [{"title_context": projection}]}})


def test_explicit_literal_title_constraints_apply_to_every_candidate_and_latest_wins():
    constraints = literal_title_requirements([
        {"instructions": "Keep 'Case'\n必须包含「丈夫」"},
        {"instructions": "Exclude 'Case'\n禁止词：「丈夫」\n必须保留：“Truth”"},
    ])
    assert constraints == [{"text": "Case", "required": False}, {"text": "丈夫", "required": False},
                           {"text": "Truth", "required": True}]
    candidates = [{"title": f"Truth {n}", "angle": "选择与代价", "reason": "回应真相要求", "source_ids": ["story"]} for n in range(5)]
    assert len(_validate_candidates(json.dumps({"candidates": candidates}), {"story"}, literal_requirements=constraints)) == 5
    candidates[4]["title"] = "A Case of Truth"
    with pytest.raises(ValueError, match="每个正式候选"):
        _validate_candidates(json.dumps({"candidates": candidates}), {"story"}, literal_requirements=constraints)
    assert literal_title_requirements([{"instructions": "I wonder if a case of betrayal could be better."}]) == []
    assert literal_title_requirements([
        {"instructions": "Keep 'Case'"}, {"instructions": "不用再保留之前那个词，重新想五个。"}]) == []
    assert literal_title_requirements([{"instructions": "Keep 'Case' or choose another word if it feels better."}]) == []


def test_literal_matching_does_not_confuse_a_word_with_a_substring():
    candidates = [{"title": f"Her Truth {n}", "angle": "选择", "reason": "改变处境", "source_ids": ["story"]} for n in range(5)]
    assert len(_validate_candidates(json.dumps({"candidates": candidates}), {"story"},
        literal_requirements=[{"text": "he", "required": False}])) == 5


@pytest.mark.parametrize("fault", ["duplicate", "citation", "missing_angle", "too_many", "control"])
def test_output_contract_rejects_invalid_candidates(fault):
    candidates = [{"title": f"Case {n}", "angle": "危险承诺", "reason": "关系与职责冲突", "source_ids": ["story"]} for n in range(5)]
    if fault == "duplicate": candidates[1]["title"] = "CASE—0!"
    elif fault == "citation": candidates[1]["source_ids"] = ["invented"]
    elif fault == "missing_angle": candidates[1].pop("angle")
    elif fault == "too_many": candidates.append(copy.deepcopy(candidates[0]))
    else: candidates[1]["title"] = "Line\nBreak"
    with pytest.raises((ValueError, KeyError)):
        _validate_candidates(json.dumps({"candidates": candidates}), {"story"})


@pytest.mark.parametrize(("title", "expected"), [
    ("My Husband's Secret Was the Reason I Finally Walked Away", 10),
    ("My Husband’s Secret", 3),
    ("My—Former-Partner: The Case He Hid from Me", 9),
    ("她离开后，丈夫才发现真相", 11),
    ("她的 CEO 隐瞒了什么", 8),
    ("她的CEO隐瞒了什么", 8),
    ("Ｆｉｖｅ Ｙｅａｒｓ", 2),
])
def test_candidate_length_counts_language_units_not_spacing_or_punctuation(title, expected):
    assert candidate_title_length(title) == expected


@pytest.mark.parametrize(("at_limit", "over_limit"), [
    ("When My Former Partner Returned I Had to Choose Between His Life and My Career",
     "When My Former Partner Returned I Had to Choose Between His Life and My Entire Career"),
    ("她离开多年后丈夫才发现当年真相", "她离开多年后丈夫才发现当年的真相"),
    ("她离开多年后丈夫才发现当年的CEO", "她离开多年后丈夫才发现当年的Old CEO"),
    ("-".join(["Case"] * 15), "-".join(["Case"] * 16)),
])
def test_candidate_limit_preserves_full_title_and_does_not_limit_manual_choice(at_limit, over_limit):
    candidates = [{"title": f"Case {n}", "angle": "职责与人情", "reason": "救人需要牺牲事业", "source_ids": ["story"]} for n in range(5)]
    candidates[0]["title"] = at_limit
    assert candidate_title_length(at_limit) == 15
    assert _validate_candidates(json.dumps({"candidates": candidates}), {"story"})[0]["title"] == at_limit
    candidates[0]["title"] = over_limit
    assert candidate_title_length(over_limit) == 16
    with pytest.raises(ValueError, match="长度上限"):
        _validate_candidates(json.dumps({"candidates": candidates}), {"story"})
    assert clean_title(over_limit) == over_limit  # Author-entered titles remain editable.
