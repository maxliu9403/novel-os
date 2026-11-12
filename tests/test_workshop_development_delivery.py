"""Review and repair the actual full skeleton, never an older stage snapshot."""
from copy import deepcopy

import pytest

from core.story_design import render
from test_story_first_engine import design  # noqa: F401
from test_workshop_api import complete_prompt


def test_modern_actual_document_roundtrip_and_final_global_changes_are_visible(design):
    from api.workshop_development_delivery import build_candidate, render_candidate
    session, story, chapters = design
    source = render(session, story, chapters)['prompt_md'].replace('They choose a bounded alliance.',
        'FINAL CANDIDATE: She rejects the proposed alliance.')
    candidate = build_candidate(source)
    assert len(candidate['chapter_plan']) == 2
    assert 'FINAL CANDIDATE' in str(candidate['story_design'])
    assert render_candidate(source, candidate, deepcopy(candidate)) == source
    revised = deepcopy(candidate)
    revised['chapter_plan'][0]['text'] = revised['chapter_plan'][0]['text'].replace(
        'Iris returns it and asks for the truth.', 'Iris accepts the loss of her position and returns the key.')
    updated = render_candidate(source, candidate, revised)
    assert updated == source.replace('Iris returns it and asks for the truth.',
        'Iris accepts the loss of her position and returns the key.', 1)


def test_legacy_seventeen_sections_and_global_narrative_repair_are_exact(tmp_path):
    from api.workshop_development_delivery import build_candidate, render_candidate
    source = complete_prompt(tmp_path)
    original = build_candidate(source)
    assert len(original['story_design']['sections']) == 17
    assert len(original['chapter_plan']) == 21
    assert render_candidate(source, original, deepcopy(original)) == source
    revised = deepcopy(original)
    part = next(row for row in revised['story_design']['sections'][12]['parts'] if row['kind'] == 'narrative')
    part['text'] = part['text'].replace('重获自主', '付出职位代价后重获自主')
    updated = render_candidate(source, original, revised)
    assert '付出职位代价后重获自主' in updated
    assert build_candidate(updated)['chapter_plan'] == original['chapter_plan']


def test_legacy_assembly_chapter_extensions_and_nested_same_chapter_wrappers_are_lossless(tmp_path):
    from api.workshop_assembly import _chapter, _supplement
    from api.workshop_development_delivery import build_candidate, render_candidate
    from core.story_markdown import parse_markdown, module_number
    source = complete_prompt(tmp_path)
    document = parse_markdown(source)
    section = next(row for row in document.headings if row.level == 2 and module_number(row.title)[0] == 12)
    body = '\n\n' + '\n\n'.join(_chapter({
        'number': number, 'title': f'Choice {number}\nOriginal subtitle',
        'plan': f'### Chapter {number}: Original wrapper\nShe pays the real cost before choosing freedom.',
        'target_words': 550, 'capacity_scene_ids': [f'scene_{number}'],
        'causal_detail': f'Consequence {number} remains explicit.'}) for number in range(1, 22))
    body += _supplement('完整章节规划补充记录', {'causal_dependency': 'Chapter 21 pays off Chapter 1.'}) + '\n\n'
    source = ''.join(document.lines[:section.heading_index + 1]) + body + ''.join(document.lines[section.end_index:])
    candidate = build_candidate(source)
    assert len(candidate['chapter_plan']) == 21
    assert '原始多行章标题' in candidate['chapter_plan'][0]['text']
    assert '本章补充规划记录' in candidate['chapter_plan'][0]['text']
    assert '#### Chapter 1: Original wrapper' in candidate['chapter_plan'][0]['text']
    assert 'Consequence 21 remains explicit.' in candidate['chapter_plan'][-1]['text']
    assert '完整章节规划补充记录' not in candidate['chapter_plan'][-1]['text']
    assert 'Chapter 21 pays off Chapter 1.' in str(candidate['story_design'])
    assert render_candidate(source, candidate, candidate).encode() == source.encode()
    # Extra narrative remains writable, while allocation and original title
    # fields in exactly the same legacy JSON record remain host-owned.
    revised = deepcopy(candidate)
    revised['chapter_plan'][0]['text'] = revised['chapter_plan'][0]['text'].replace(
        'Consequence 1 remains explicit.', 'She loses her position; that cost causes the next decision.')
    assert 'She loses her position' in render_candidate(source, candidate, revised)
    for old, new in [('550', '1'), ('scene_1', 'scene_99'), ('Original subtitle', 'Different original title')]:
        revised = deepcopy(candidate)
        revised['chapter_plan'][0]['text'] = revised['chapter_plan'][0]['text'].replace(old, new)
        with pytest.raises(ValueError):
            render_candidate(source, candidate, revised)


@pytest.mark.parametrize('extension', [
    '### 本章补充规划记录\n\nThis is unstructured prose, not an assembly record.\n',
    '### 本章补充规划记录\n\n```json\n{"target_words":550}\n```\nUnowned trailing prose.\n',
    '### 本章补充规划记录\n\n```json\n{"a":1,"a":2}\n```\n',
    '### 原始多行章标题\n\n```json\n{"not_title":"Ambiguous"}\n```\n',
])
def test_familiar_extension_title_does_not_hide_ambiguous_chapter_boundaries(design, extension):
    from api.workshop_development_delivery import build_candidate
    source = render(*design)['prompt_md'].replace('### Chapter 2', extension + '\n### Chapter 2')
    with pytest.raises(ValueError, match='补充记录|原始章标题'):
        build_candidate(source)


def test_legacy_extension_cannot_precede_its_chapter_or_repeat_and_nested_wrong_chapter_is_rejected(design):
    from api.workshop_development_delivery import build_candidate
    source = render(*design)['prompt_md']
    extension = '### 本章补充规划记录\n\n```json\n{"target_words":550}\n```\n\n'
    for changed in (source.replace('### Chapter 1', extension + '### Chapter 1'),
                    source.replace('### Chapter 2', extension + extension + '### Chapter 2'),
                    source.replace('### Chapter 2', '#### Chapter 99: Unowned chapter\nUnknown story.\n\n### Chapter 2')):
        with pytest.raises(ValueError, match='所属章节|嵌套章节'):
            build_candidate(changed)


@pytest.mark.parametrize('headings', [
    ('### 第1章：选择', '### 第二章：归还'),
    ('#### Chapter 1: Choice', '#### CHAPTER 2 — Return'),
    ('### 1. Choice', '### 2、Return'),
])
def test_chapter_heading_formats_and_crlf_preserve_all_bytes(design, headings):
    from api.workshop_development_delivery import build_candidate, render_candidate
    source = render(*design)['prompt_md'].replace('### Chapter 1 · Choice 1', headings[0]).replace(
        '### Chapter 2 · Choice 2', headings[1]).replace('\n', '\r\n')
    candidate = build_candidate(source)
    assert [c['number'] for c in candidate['chapter_plan']] == [1, 2]
    assert render_candidate(source, candidate, candidate).encode() == source.encode()


def test_fenced_examples_and_global_map_extension_do_not_become_chapters(design):
    from api.workshop_development_delivery import build_candidate, render_candidate
    source = render(*design)['prompt_md'].replace('## 5.',
        '### 完整章节规划补充记录\n完整全局补充，不属于最后一章。\n\n## 5.')
    source = source.replace('A modern town.', 'A modern town.\n\n````markdown\n## 88. Fake\n### Chapter 99\n```\n````')
    candidate = build_candidate(source)
    assert 'Chapter 99' in str(candidate['story_design'])
    assert '完整全局补充' not in candidate['chapter_plan'][-1]['text']
    assert render_candidate(source, candidate, candidate) == source


@pytest.mark.parametrize('change', [
    lambda value: value['chapter_plan'].pop(),
    lambda value: value['chapter_plan'][0].update(number=9),
    lambda value: value['chapter_plan'][0].update(heading='### Chapter 1 · New title\n'),
    lambda value: value['story_design'].update(header=value['story_design']['header'].replace('Words:', 'Fake:')),
    lambda value: value['story_design']['sections'][0].update(heading='## 1. Changed\n'),
    lambda value: value.update(origin_source_sha256='f' * 64),
])
def test_repair_cannot_change_host_layout_or_metadata(design, change):
    from api.workshop_development_delivery import build_candidate, render_candidate
    source = render(*design)['prompt_md']
    before = build_candidate(source)
    after = deepcopy(before)
    change(after)
    with pytest.raises(ValueError):
        render_candidate(source, before, after)
    assert render_candidate(source, before, before) == source


def test_machine_contract_and_chapter_target_cannot_change(design):
    from api.workshop_development_delivery import build_candidate, render_candidate
    source = render(*design)['prompt_md'].replace('- 叙事视角：Mara', '- Target_words: 800\n- 叙事视角：Mara', 1)
    before = build_candidate(source)
    changed = deepcopy(before)
    changed['chapter_plan'][0]['text'] = changed['chapter_plan'][0]['text'].replace('800', '1')
    with pytest.raises(ValueError):
        render_candidate(source, before, changed)
    changed = deepcopy(before)
    contracts = [part for section in changed['story_design']['sections'] for part in section['parts']
                 if part['kind'] == 'host_contract']
    assert contracts
    contracts[0]['text'] = contracts[0]['text'].replace('2', '100')
    with pytest.raises(ValueError):
        render_candidate(source, before, changed)


def test_inline_engine_blocks_remain_exact_readonly_contracts(tmp_path):
    from api.workshop_development_delivery import build_candidate, render_candidate
    source = complete_prompt(tmp_path).replace('COVER_HANDOFF_BEGIN\n', 'COVER_HANDOFF_BEGIN ').replace(
        '\nCOVER_HANDOFF_END', ' COVER_HANDOFF_END')
    original = build_candidate(source)
    assert render_candidate(source, original, original) == source
    revised = deepcopy(original)
    part = next(part for section in revised['story_design']['sections'] for part in section['parts']
                if part.get('text', '').startswith('COVER_HANDOFF_BEGIN'))
    part['text'] = part['text'].replace('schema_version', 'schema_changed')
    with pytest.raises(ValueError):
        render_candidate(source, original, revised)


@pytest.mark.parametrize('change', [
    lambda text: text.replace('### Chapter 2', '### Chapter 3'),
    lambda text: text.replace('Chapters: 2', 'Chapters: 3'),
    lambda text: text.replace('## 5.', '### Unknown map extension\nLOST?\n## 5.'),
    lambda text: text + '\n```unclosed',
    lambda text: text + '\n## 7. Unknown extension\nKEEP',
])
def test_unknown_or_incomplete_layout_blocks_without_a_lossy_candidate(design, change):
    from api.workshop_development_delivery import build_candidate, DeliveryProjectionError
    source = change(render(*design)['prompt_md'])
    with pytest.raises(DeliveryProjectionError):
        build_candidate(source)


def test_hundred_complete_chapters_are_individual_review_units(design):
    from api.workshop_development_delivery import build_candidate, render_candidate
    from core.creative_development import _units
    session, story, chapters = deepcopy(design)
    story['total_chapters'] = chapters['total_chapters'] = session['input']['chapters'] = 100
    chapters['chapters'] = [{**deepcopy(chapters['chapters'][0]), 'number': n, 'title': f'Choice {n}'} for n in range(1, 101)]
    source = render(session, story, chapters)['prompt_md']
    candidate = build_candidate(source)
    units = [row for row in _units(candidate) if row['path'][0] == 'chapter_plan']
    assert len(units) == 100
    assert all(row['value']['text'].strip() for row in units)
    assert render_candidate(source, candidate, candidate) == source


def test_actual_delivery_uses_existing_scoped_repair_and_rechecks_result(design, tmp_path):
    from api.workshop_development_delivery import build_candidate, render_candidate
    from api.workshop_development_repair import repair
    from core.creative_development import DevelopmentReview
    from test_creative_development import Model, baseline, passed, conflict
    source = render(*design)['prompt_md']
    candidate = build_candidate(source)
    before = candidate['chapter_plan'][0]['text']
    replacement = before.replace('Iris returns it and asks for the truth.',
                                 'Iris accepts the real cost and returns the key freely.')
    judge = Model(lambda payload, number: conflict(payload, path=['chapter_plan', 0, 'text'],
        quote='Iris returns it and asks for the truth.') if number == 1 else passed())
    editor = Model(lambda payload, _: {'edits': [{'path': ['chapter_plan', 0, 'text'], 'value': replacement}],
                                      'evidence_requests': []})
    def revise(current, issues, context):
        revised = repair(editor, current, issues, context)
        render_candidate(source, candidate, revised)
        return revised
    checker = DevelopmentReview(tmp_path, baseline())
    result = checker.assess(judge, candidate, repair=revise)
    assert result['status'] == 'passed' and result['receipt']['scope'] == 'delivery'
    assert len(judge.calls) == 2 and len(editor.calls) == 1
    assert render_candidate(source, candidate, result['candidate']) == source.replace(before, replacement, 1)
    assert checker.assess(judge, candidate, repair=revise) == result
    assert len(judge.calls) == 2 and len(editor.calls) == 1  # resumed work buys nothing again


def test_small_context_pages_all_hundred_actual_chapters_without_losing_globals(design, tmp_path):
    from api.workshop_development_delivery import build_candidate
    from core.creative_development import DevelopmentReview
    from test_creative_development import Model, baseline, passed
    session, story, chapters = deepcopy(design)
    story['total_chapters'] = chapters['total_chapters'] = session['input']['chapters'] = 100
    chapters['chapters'] = [{**deepcopy(chapters['chapters'][0]), 'number': n, 'title': f'Choice {n}'} for n in range(1, 101)]
    candidate = build_candidate(render(session, story, chapters)['prompt_md'])
    def respond(payload, _):
        if ['chapter_plan', 99] in [row['path'] for row in payload['candidate_units']] and not payload['requested_evidence']:
            return {'status': 'needs_evidence', 'issues': [], 'evidence_requests': [
                {'source': 'candidate', 'path': ['chapter_plan', 0]}]}
        return passed()
    model = Model(respond, limit=24000)
    result = DevelopmentReview(tmp_path, baseline()).assess(model, candidate)
    assert result['status'] == 'passed'
    seen = {row['path'][1]: row['value'] for call in model.calls for row in call['candidate_units']
            if row['path'][0] == 'chapter_plan'}
    assert [seen[i] for i in range(100)] == candidate['chapter_plan']
    assert all(call['global_design'] == candidate['story_design'] for call in model.calls)
    assert any(call['requested_evidence'] and call['requested_evidence'][0]['value'] == candidate['chapter_plan'][0]
               for call in model.calls)
    assert 2 <= len(model.calls) < 20


def test_inserted_boundary_contract_or_tampered_original_is_never_adopted(design):
    from api.workshop_development_delivery import build_candidate, render_candidate
    source = render(*design)['prompt_md']
    candidate = build_candidate(source)
    for suffix in ('\n### Chapter 3 · Smuggled\nAn unauthorized chapter.\n',
                   '\n[STORY_CAPACITY_JSON]\n{}\n[/STORY_CAPACITY_JSON]\n',
                   '\nWords: 1\n', '\nDefault Working Title: Replaced title\n',
                   '\n\u3000Title: Hidden metadata override\n'):
        revised = deepcopy(candidate)
        revised['chapter_plan'][0]['text'] += suffix
        with pytest.raises(ValueError):
            render_candidate(source, candidate, revised)
    with pytest.raises(ValueError):
        render_candidate(source + '\nChanged after review.', candidate, candidate)
