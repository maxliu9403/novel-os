"""Flexible author Markdown reaches every stage without changing frozen formats."""
import json
from pathlib import Path

import pytest

from core import creative_types as ct
from core.knowledge_markdown import prepare_markdown, document_body
from test_creative_types import ref

BODY = '**【系统定位】**\n作者的自由说明。  \n\n---\n\n## 一、 核心基调 (Tone)\n人物作出有代价的选择。\n\n### 嵌套标题\n| 动作 | 结果 |\n|---|---|\n| 选择 | 改变 |\n\n## 任意标题\n保留末尾。\n## 任意标题\n重复标题也合法。\n'


def test_custom_v2_import_preserves_body_and_reaches_all_stages(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    original = store.get('tag.dark_romance')
    header = original['draft'].split('---', 2)[1]
    imported = '---' + header + '---\n' + BODY
    prepared = prepare_markdown(imported)
    assert prepared['document']['schema'] == 'novel-knowledge.v3'
    assert document_body(prepared['markdown']) == BODY
    updated = store.mutate('activate', original['id'], revision=0,
                           operation_id='freeform-import', markdown=prepared['markdown'])
    assert updated['versions'][0] == original['versions'][0]
    snapshot = store.snapshot([ref(store.get('genre.romance')), ref(updated)])
    assert snapshot['compiler_version'] == 'knowledge-compiler.v4'
    for stage in ct.STAGE_SECTIONS:
        compiled = ct.compile_snapshot(snapshot, stage)
        assert compiled['documents'][1]['markdown'] == BODY
        assert compiled['projection_manifest'][1]['source_mode'] == 'complete_markdown_body'
        assert compiled['projection_manifest'][1]['excluded_sections'] == []
        assert compiled['interpretation']['schema'] == 'generative-guidance.v2'
    from core.creative_execution import make_handoff
    handoff = make_handoff(source='已确认的完整故事骨架', origin={}, type_snapshot=snapshot)
    assert all(c['documents'][1]['markdown'] == BODY for c in handoff['stage_contexts'].values())


def test_plain_md_needs_identity_once_then_can_be_edited_without_header():
    assert prepare_markdown(BODY) == {'needs_identity': True, 'body': BODY}
    identity = dict(id='tag.custom_example', kind='tag', name='自定义', genre_ids=['genre.romance'])
    prepared = prepare_markdown(BODY, identity=identity)
    changed = prepare_markdown('纯文本正文\n', base_markdown=prepared['markdown'])
    assert changed['document']['id'] == identity['id']
    assert changed['document']['genre_ids'] == identity['genre_ids']
    assert document_body(changed['markdown']) == '纯文本正文\n'
    assert changed['document']['missing_sections'] == []


@pytest.mark.parametrize('body', [BODY, '没有标题也可以。\n', '# 一个标题\n正文\n', '---\n这是普通分割线后的正文\n', BODY.replace('\n', '\r\n')])
def test_free_body_has_no_heading_contract_and_keeps_exact_bytes(body):
    prepared = prepare_markdown(body, identity=dict(id='genre.example', kind='genre', name='示例'))
    assert document_body(prepared['markdown']) == body
    assert ct.parse_document(prepared['markdown'], enabled=True)['body'] == body


def test_blank_draft_is_allowed_but_cannot_be_activated():
    prepared = prepare_markdown('', identity=dict(id='genre.example', kind='genre', name='示例'))
    assert prepared['document']['body'] == ''
    with pytest.raises(ct.CreativeTypeError, match='正文'):
        ct.parse_document(prepared['markdown'], enabled=True)


def test_unsafe_metadata_and_oversized_content_are_not_hidden_by_import():
    for md in ['---\nschema: novel-knowledge.v2\nid: tag.a\nid: tag.b\n---\n正文',
               '---\nschema: novel-knowledge.v2\nname: !!python/object:builtins.object {}\n---\n正文']:
        with pytest.raises(ct.CreativeTypeError):
            prepare_markdown(md)
    with pytest.raises(ct.CreativeTypeError, match='未截断|上限'):
        prepare_markdown('文' * ct.DOCUMENT_LIMIT, identity=dict(id='genre.example', kind='genre', name='示例'))


def test_pre_upgrade_frozen_contexts_stay_identical():
    cases = json.loads((Path(__file__).parent / 'fixtures/knowledge-before-generative-v3.json').read_text())
    for case in cases.values():
        for stage, expected in case['context_sha256'].items():
            assert ct.digest(ct.compile_snapshot(case['snapshot'], stage)) == expected


def test_full_file_pasted_into_body_keeps_one_envelope_and_saved_identity(tmp_path):
    original = ct.CreativeTypeStore(tmp_path).get('tag.dark_romance')['draft']
    header = original.split('---', 2)[1]
    pasted = '---' + header + '---\n' + BODY
    edited = prepare_markdown(pasted, base_markdown=original)
    assert document_body(edited['markdown']) == BODY
    assert edited['document']['id'] == 'tag.dark_romance'
    assert edited['markdown'].count('schema: novel-knowledge.') == 1
    # Re-editing an already nested legacy paste also unwraps only file metadata.
    nested = prepare_markdown(pasted, identity=dict(id='tag.dark_romance', kind='tag', name='黑暗浪漫', genre_ids=['genre.romance']))
    repaired = prepare_markdown(nested['markdown'], base_markdown=original)
    assert repaired['document']['body'] == BODY
    # Ordinary Markdown separators / quoted examples are still author prose.
    fenced = '```markdown\n' + pasted + '\n```'
    assert prepare_markdown(fenced, base_markdown=original)['document']['body'] == fenced


def test_full_file_paste_cannot_replace_record_identity(tmp_path):
    store = ct.CreativeTypeStore(tmp_path)
    original = store.get('tag.dark_romance')['draft']
    other = store.get('genre.romance')['draft']
    with pytest.raises(ct.CreativeTypeError, match='另一份资料'):
        prepare_markdown(other, base_markdown=original)
