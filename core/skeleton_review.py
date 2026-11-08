"""Evidence-bound editorial proposals; applying them never calls a model.

The author reviews meaning. Code verifies exact source, non-overlapping edits and
version identity; the caller must validate the resulting whole-document contracts.
"""
from __future__ import annotations
import hashlib
import json
import re
from typing import Literal
from pydantic import Field
from .story_capacity import Record, digest

VERSION = 'skeleton-review.v1'
CATEGORIES = ('opening', 'causality', 'reader_value', 'character', 'capacity', 'payoff')
RULES = '''
EVIDENCE-BOUND EDITORIAL REVIEW (skeleton-review.v1):
In this existing delivery_review call, add review alongside sections. Do not buy
another review turn. Check opening identification/conflict and the first reward;
genre mechanisms and cause/effect; consequential choices and relationship change;
on-page time for difficult choices, reversals and aftermath; earned ending payoffs.
An emotion label, a cliffhanger count or passing a schema is not reader engagement.
Do not force anger, betrayal, a twist or a fixed beat formula on every genre.
review = {summary:string, checked:['opening','causality','reader_value','character','capacity','payoff'],
 issues:[{chapter:positive_int,category:one_of_checked,quote:exact_unique_contiguous_excerpt,
 problem:concrete_reader_consequence,replacement:complete_replacement_for_that_excerpt}],
 notes:[remaining_nonlocal_concerns_or_unverified_assumptions]}.
At most 8 HIGH-IMPACT non-overlapping edits and 6 notes. No quota: issues may be [].
Quote verbatim from that chapter's saved plan; include enough surrounding text to
locate it uniquely. Replacement is a usable revised plan passage, not 'improve this'
or a prose chapter. Preserve adopted cast, plot route, constraints, ending and length.
For changes that require a new major choice or coordinated global rewrite, explain
in notes; do not disguise them as a local patch. Do not claim a human accepted your
proposal, that edits were applied, that contracts were checked or prose was read.
Do not edit supplemental machine records, chapter headings, protocol blocks or
transport instructions. Budget changes require a new capacity confirmation.
'''


class Issue(Record):
    chapter: int = Field(ge=1)
    category: Literal['opening', 'causality', 'reader_value', 'character', 'capacity', 'payoff']
    quote: str = Field(min_length=8, max_length=8000)
    problem: str = Field(min_length=1, max_length=1600)
    replacement: str = Field(min_length=8, max_length=10000)


class Review(Record):
    summary: str = Field(min_length=1, max_length=1600)
    checked: list[str] = Field(min_length=6, max_length=6)
    issues: list[Issue] = Field(default_factory=list, max_length=8)
    notes: list[str] = Field(default_factory=list, max_length=6)


def source_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


def _chapter_spans(prompt: str) -> dict[int, tuple[int, int]]:
    section = re.search(r'^##\s+12\.[^\n]*\n', prompt, re.M)
    end = re.search(r'^##\s+13\.', prompt, re.M)
    if not section or not end or section.end() >= end.start():
        raise ValueError('找不到完整分章细纲范围。')
    matches = list(re.finditer(r'^###\s+(?:Chapter\s+(\d+)\b|第\s*(\d+)\s*章)[^\n]*\n',
                               prompt[section.end():end.start()], re.M | re.I))
    spans = {}
    for i, match in enumerate(matches):
        number = int(match[1] or match[2])
        if number in spans:
            raise ValueError('章节定位不唯一。')
        start = section.end() + match.end()
        stop = section.end() + matches[i + 1].start() if i + 1 < len(matches) else end.start()
        # The assembly owns these records, including confirmed chapter budgets.
        # Editorial patches can change the plan prose, never that metadata.
        supplement = re.search(r'^###\s+(?:本章补充规划记录|原始多行章标题)\s*$', prompt[start:stop], re.M)
        spans[number] = (start, start + supplement.start() if supplement else stop)
    return spans


def _edits(prompt: str, issues: list[dict]) -> list[tuple[int, int, str]]:
    spans = _chapter_spans(prompt)
    edits = []
    for issue in issues:
        if issue['chapter'] not in spans:
            raise ValueError('修订引用的章节不存在。')
        start, end = spans[issue['chapter']]
        excerpt = prompt[start:end]
        quote, replacement = issue['quote'], issue['replacement']
        if quote == replacement or excerpt.count(quote) != 1:
            raise ValueError('修订原文必须在对应章节唯一定位，且新旧内容不能相同。')
        if re.search(r'^#{1,3}\s|\[(?:/?[A-Z_]+JSON|/?STORY_|/?SCRIBE_)|COVER_HANDOFF_', replacement, re.M):
            raise ValueError('局部修订不能添加章节标题或机器合同。')
        position = start + excerpt.index(quote)
        edits.append((position, position + len(quote), replacement))
    edits.sort()
    if any(left[1] > right[0] for left, right in zip(edits, edits[1:])):
        raise ValueError('修订原文互相重叠，请合并为一项建议。')
    return edits


def record(prompt: str, raw: dict | None, revision: int) -> dict:
    identity = {'schema': VERSION, 'source_sha256': source_hash(prompt), 'source_revision': revision}
    try:
        if len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode()) > 112_000:
            raise ValueError('复核记录过长。')
        review = Review.model_validate(raw)
        if set(review.checked) != set(CATEGORIES):
            raise ValueError('复核维度不完整。')
        if any(not isinstance(note, str) or not note.strip() or len(note) > 1600 for note in review.notes):
            raise ValueError('复核说明无效。')
        issues = [issue.model_dump() | {'id': f'issue_{i + 1}'} for i, issue in enumerate(review.issues)]
        _edits(prompt, issues)
        body = {**identity, 'summary': review.summary, 'checked': review.checked,
                'issues': issues, 'notes': review.notes}
        return {**body, 'status': 'proposed' if issues or review.notes else 'clear', 'review_sha256': digest(body)}
    except (ValueError, TypeError):
        return {**identity, 'status': 'unavailable', 'issues': [],
                'notice': '本轮复核建议未形成可定位的修订记录；骨架已保存，不能据此认定没有故事质量问题。'}


def revise(prompt: str, review: dict, selected_ids: list[str]) -> str:
    if review.get('status') != 'proposed' or review.get('source_sha256') != source_hash(prompt):
        raise ValueError('修订建议与当前骨架不匹配，请先更新复核。')
    body = {k: v for k, v in review.items() if k not in {'status', 'review_sha256'}}
    if digest(body) != review.get('review_sha256'):
        raise ValueError('修订记录校验失败。')
    known = {issue['id'] for issue in review['issues']}
    if len(set(selected_ids)) != len(selected_ids) or not set(selected_ids) <= known:
        raise ValueError('所选修订不存在或重复。')
    edits = _edits(prompt, [issue for issue in review['issues'] if issue['id'] in selected_ids])
    for start, end, replacement in reversed(edits):
        prompt = prompt[:start] + replacement + prompt[end:]
    return prompt


def annotate_resolution(prompt: str, review: dict, selected_ids: list[str]) -> str:
    """Keep the check's provenance without presenting old advice as current work."""
    if not selected_ids:
        return prompt
    marker = re.search(r'^##\s+17\.[^\n]*\n', prompt, re.M)
    if marker is None:
        raise ValueError('缺少骨架交付检查记录。')
    issues = {item['id']: item for item in review['issues']}
    applied = '\n'.join(f"- 第 {issues[key]['chapter']} 章：{issues[key]['problem']}（已采用对应修订）" for key in selected_ids)
    original = prompt[marker.end():].strip()
    return (prompt[:marker.end()] + '\n### 当前修订结果\n\n' + applied
            + '\n\n已按作者选择修改对应原文，并完成代码结构校验。未采用的建议保留原设计；其他待核实事项继续保留。'
              '正文尚未创作，本次不代表读者体验已经验证。\n\n### 修订前复核记录（历史）\n\n' + original + '\n')
