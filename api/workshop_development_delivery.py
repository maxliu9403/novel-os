"""Lossless, reversible review units for the actual delivered Markdown.

The saved stage data is historical evidence, not a substitute for the candidate
the author will receive. This pure adapter keeps every source character, exposes
complete chapters as review units, and permits scoped narrative repair without
letting a model rewrite host-owned metadata, contracts or document boundaries.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from core.story_markdown import chapter_title, module_number, parse_markdown


class DeliveryProjectionError(ValueError):
    """The complete candidate remains saved; no partial view can claim success."""


def _fail(reason):
    raise DeliveryProjectionError('完整骨架尚不能安全分段核验：' + reason + '；原文与检查点保留，未截断或采用局部结果。')


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


_MARKER = re.compile(
    r'(?:\[(/?)([A-Z][A-Z0-9_]*_JSON)\]|'
    r'(COVER_HANDOFF_(BEGIN|END))|<!-- (/?)NOVEL_OS_RUNTIME_PROTOCOL_V1 -->)')
_METADATA = re.compile(
    r'^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?'
    r'(?:title|default working title|author|genre|primary category|audience|primary audience|'
    r'language|tone|pov|point of view|chapters|words|'
    r'target_words|word_count_target|capacity_scene_ids|chapter_number|'
    r'字数目标|目标字数|章节字数|全书字数|总字数|章节数|章数)'
    r'(?:\*\*)?[ \t]*[:：][^\r\n]*(?:\r?\n|$)', re.M | re.I)
_CHAPTER_METADATA = re.compile(
    r'^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?'
    r'(?:target_words|word_count_target|capacity_scene_ids|chapter_number|'
    r'字数目标|目标字数|章节字数|章节编号)'
    r'(?:\*\*)?[ \t]*[:：][^\r\n]*(?:\r?\n|$)', re.M | re.I)


def _protected_spans(text):
    """Keep complete engine blocks exact, including their original formatting."""
    spans, opened = [], None
    for marker in _MARKER.finditer(text):
        closing, key, cover, cover_kind, runtime_close = marker.groups()
        if cover:
            key, closing = 'COVER_HANDOFF', cover_kind == 'END'
        elif key is None:
            key, closing = 'NOVEL_OS_RUNTIME_PROTOCOL_V1', bool(runtime_close)
        if not closing:
            if opened is not None:
                _fail('宿主合同标记嵌套或缺少结束标记')
            opened = (key, marker.start())
        else:
            if opened is None or opened[0] != key:
                _fail('宿主合同标记无法完整配对')
            spans.append((opened[1], marker.end()))
            opened = None
    if opened is not None:
        _fail('宿主合同没有完整结束')
    # Metadata aliases inside narrative sections are immutable too. JSON lines
    # have quoted keys and therefore remain inside their complete contract span.
    from core.prompt_intake import _FIELD_RE
    offset = 0
    for line in text.splitlines(keepends=True):
        end_offset = offset + len(line)
        if (_FIELD_RE.match(line) or _METADATA.fullmatch(line)) and not any(
                start <= offset and end_offset <= end for start, end in spans):
            spans.append((offset, end_offset))
        offset = end_offset
    spans.sort()
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        _fail('宿主元数据范围重叠')
    return spans


def _parts(text):
    parts, cursor = [], 0
    for start, end in _protected_spans(text):
        if cursor < start:
            parts.append({'kind': 'narrative', 'text': text[cursor:start]})
        parts.append({'kind': 'host_contract', 'text': text[start:end]})
        cursor = end
    if cursor < len(text):
        parts.append({'kind': 'narrative', 'text': text[cursor:]})
    return parts


_CHAPTER_SUPPLEMENTS = {'原始多行章标题', '本章补充规划记录'}
_SUPPLEMENT_METADATA = {'number', 'chapter_number', 'title', 'target_words', 'word_count_target', 'capacity_scene_ids'}


def _supplement_data(heading):
    """Recognize the exact lossless extension form emitted by legacy assembly.

    A familiar heading alone cannot turn arbitrary following prose into a known
    extension. Its entire body must be one complete JSON object fence; chapter
    boundaries and any additional headings remain the outer parser's concern.
    """
    document = parse_markdown(heading.body)
    if len(document.fences) != 1:
        _fail('章节补充记录不是唯一完整的 JSON 记录')
    fence = document.fences[0]
    if (not fence.closed or fence.info.strip().lower() != 'json'
            or ''.join(document.lines[:fence.opening_index]).strip()
            or ''.join(document.lines[fence.end_index:]).strip()):
        _fail('章节补充记录包含无法明确归属的文字或边界')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate field')
            result[key] = value
        return result
    try:
        value = json.loads(''.join(document.lines[fence.opening_index + 1:fence.body_end_index]),
                           object_pairs_hook=unique)
    except (ValueError, TypeError):
        _fail('章节补充记录不是完整且无歧义的 JSON 对象')
    if not isinstance(value, dict) or not value:
        _fail('章节补充记录没有完整字段')
    if heading.title == '原始多行章标题' and (set(value) != {'title'} or not isinstance(value['title'], str)):
        _fail('原始章标题记录无效')
    return value


def _chapter_split(document, section, next_index, count):
    lines = document.lines
    nested = [heading for heading in document.headings
              if section.heading_index < heading.heading_index < next_index]
    matches = [(heading, chapter_title(heading.title, allow_numbered=True)) for heading in nested]
    matched = [(heading, title[0]) for heading, title in matches if title is not None]
    level = min((heading.level for heading, _ in matched), default=0)
    chapters = [(heading, number) for heading, number in matched if heading.level == level]
    if ([number for _, number in chapters] != list(range(1, count + 1))
            or not chapters or len({heading.level for heading, _ in chapters}) != 1):
        _fail('章节编号、数量或标题层级不完整')
    if level not in {3, 4}:
        _fail('章节标题层级不明确')
    extension = [heading for heading in nested if heading.title == '完整章节规划补充记录']
    if (len(extension) > 1 or (extension and (extension[0].heading_index <= chapters[-1][0].heading_index
                                             or extension[0].level != level))):
        _fail('章节补充记录的位置不明确')
    known = {heading.heading_index for heading, _ in chapters} | {h.heading_index for h in extension}
    stop = extension[0].heading_index if extension else next_index
    seen = set()
    for heading in nested:
        if heading.title not in _CHAPTER_SUPPLEMENTS or heading.level != level:
            continue
        owner = next(((chapter, number) for chapter, number in reversed(chapters)
                      if chapter.heading_index < heading.heading_index < stop), None)
        if owner is None or (owner[1], heading.title) in seen:
            _fail('章节补充记录没有唯一所属章节')
        seen.add((owner[1], heading.title))
        _supplement_data(heading)
        known.add(heading.heading_index)
    # Legacy assembly nests a chapter's own rendered wrapper one level deeper.
    # Preserve that wrapper as chapter content, but never accept a different
    # chapter number hidden beneath another chapter's body.
    for heading in nested:
        title = chapter_title(heading.title)
        if title is None or heading.level <= level:
            continue
        owner = next(((chapter, number) for chapter, number in reversed(chapters)
                      if chapter.heading_index < heading.heading_index < stop), None)
        if owner is None or title[0] != owner[1] or heading.level != level + 1:
            _fail('嵌套章节标题与所属章节不一致')
    if any(heading.level <= level and heading.heading_index not in known for heading in nested):
        _fail('章节区含有无法归属的同级段落')
    rows = []
    for index, (heading, number) in enumerate(chapters):
        end = chapters[index + 1][0].heading_index if index + 1 < count else stop
        body = ''.join(lines[heading.heading_index + 1:end])
        if not body.strip():
            _fail(f'第 {number} 章没有完整正文设计')
        rows.append({'number': number, 'heading': lines[heading.heading_index], 'text': body})
    prefix = ''.join(lines[section.heading_index + 1:chapters[0][0].heading_index])
    suffix = ''.join(lines[stop:next_index])
    return rows, [*_parts(prefix), {'kind': 'chapters'}, *_parts(suffix)]


def build_candidate(source: str) -> dict:
    """Return complete global sections and complete chapter units, without IO.

    `story_design` is supplied in every independent review request. The existing
    review/repair coordinator owns allowed paths, evidence requests and paging;
    no summary, replacement model call or approval is introduced here.
    """
    if not isinstance(source, str) or not source.strip():
        _fail('候选为空')
    document = parse_markdown(source)
    if any(not fence.closed for fence in document.fences):
        _fail('代码围栏没有结束')
    sections = [heading for heading in document.headings if heading.level == 2]
    compact = source.startswith('<!-- story-first.v1 -->')
    expected = 6 if compact else 17
    if [module_number(heading.title)[0] for heading in sections] != list(range(1, expected + 1)):
        _fail('不是完整的六节或历史十七节布局')
    header = ''.join(document.lines[:sections[0].heading_index])
    counts = re.findall(r'^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?Chapters(?:\*\*)?[ \t]*:[ \t]*(\d+)[ \t]*\r?$',
                        header, re.M | re.I)
    if len(counts) != 1 or not 1 <= int(counts[0]) <= 511:
        _fail('缺少唯一且明确的总章数')
    count, map_number = int(counts[0]), 4 if compact else 12
    global_sections, chapter_plan = [], None
    for index, section in enumerate(sections):
        end = sections[index + 1].heading_index if index + 1 < len(sections) else len(document.lines)
        number = index + 1
        if number == map_number:
            chapter_plan, parts = _chapter_split(document, section, end, count)
        else:
            parts = _parts(''.join(document.lines[section.heading_index + 1:end]))
        global_sections.append({'number': number, 'heading': document.lines[section.heading_index], 'parts': parts})
    candidate = {'story_design': {'header': header, 'sections': global_sections},
                 'chapter_plan': chapter_plan, 'origin_source_sha256': _sha(source)}
    if _render(candidate) != source:
        _fail('无法证明完整原文被逐字保留')
    return candidate


def _render(candidate):
    chapters = ''.join(row['heading'] + row['text'] for row in candidate['chapter_plan'])
    text = candidate['story_design']['header']
    for section in candidate['story_design']['sections']:
        text += section['heading']
        for part in section['parts']:
            text += chapters if part['kind'] == 'chapters' else part['text']
    return text


def _immutable(candidate):
    result = deepcopy(candidate)
    for section in result['story_design']['sections']:
        for part in section['parts']:
            if part['kind'] == 'narrative':
                if not isinstance(part['text'], str):
                    _fail('全局修订不是完整文字')
                part['text'] = '<narrative>'
    for chapter in result['chapter_plan']:
        text = chapter['text']
        if not isinstance(text, str) or not text.strip():
            _fail('章节修订不是完整文字')
        supplements = []
        chapter_level = len(re.match(r'^[ ]{0,3}(#{1,6})[ \t]+', chapter['heading']).group(1))
        for heading in parse_markdown(text).headings:
            if heading.title in _CHAPTER_SUPPLEMENTS and heading.level == chapter_level:
                fields = _supplement_data(heading)
                supplements.append({'heading': heading.title,
                    'metadata': fields if heading.title == '原始多行章标题' else
                        {key: fields[key] for key in _SUPPLEMENT_METADATA if key in fields}})
        chapter['text'] = {'protected_metadata': [match.group(0) for match in _CHAPTER_METADATA.finditer(text)],
                           'protected_supplements': supplements,
                           'protected_contracts': [text[a:b] for a, b in _protected_spans(text)]}
    return result


def render_candidate(source: str, original: dict, revised: dict) -> str:
    """Reassemble a checked scoped repair without rewriting any host contract.

    A caller must still run the existing final skeleton validator and commit
    under its normal revision/cancellation guard. This function neither saves
    data nor treats deterministic structure checks as literary approval.
    """
    expected = build_candidate(source)
    def exact(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if exact(original) != exact(expected):
        _fail('修订依据与实际候选骨架不一致')
    try:
        if exact(_immutable(original)) != exact(_immutable(revised)):
            _fail('修订改变了章节身份、标题、宿主元数据或篇幅合同')
        result = _render(revised)
        reparsed = build_candidate(result)
        reparsed['origin_source_sha256'] = original['origin_source_sha256']
        if exact(reparsed) != exact(revised):
            _fail('修订插入了新的边界或改变了不可写结构')
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise DeliveryProjectionError('骨架修订结构无效；完整原候选已保留。') from exc
    return result
