"""Discussion requests keep full author sources while removing duplicate payloads."""
from copy import deepcopy
import json

from api.workshop_request import discussion_context, discussion_system
from api.workshop import _skill_bundle


def test_identical_proposals_are_referenced_without_losing_source_or_author_words():
    proposal = {'id': 'r1:p1', 'title': '选择', 'story': '完整事件、选择和后果' * 200}
    context = {'initial_brief': {'premise': '原作者完整提示'},
               'conversation': [{'role': 'user', 'content': '所有作者输入一字不删'}],
               'current_complete_skeleton': '原骨架全文',
               'creative_design': {'rounds': [{'source_revision': 1, 'proposals': [deepcopy(proposal)]}],
                   'adopted_proposals': [{'proposal': deepcopy(proposal), 'author_decision': {'note':'原始补充'}}],
                   'genre_context': {'profiles': ['完整题材指导']}, 'author_decisions': [{'note':'全部决定'}]}}
    before = deepcopy(context)
    result = discussion_context(context)
    assert context == before
    assert result['conversation'] == before['conversation']
    assert result['initial_brief'] == before['initial_brief']
    assert result['current_complete_skeleton'] == before['current_complete_skeleton']
    design = result['creative_design']
    assert design['adopted_proposals'] == before['creative_design']['adopted_proposals']
    assert design['rounds'][0]['proposals'][0] == {'id':'r1:p1', 'source':'adopted_proposals'}
    assert design['genre_context'] == before['creative_design']['genre_context']
    assert len(json.dumps(result)) < len(json.dumps(before))


def test_same_id_with_different_content_is_never_deduplicated():
    context = {'creative_design': {'adopted_proposals': [{'proposal': {'id':'same', 'story':'旧'}}],
                                    'rounds': [{'proposals':[{'id':'same', 'story':'新'}]}]}}
    result = discussion_context(context)
    assert result['creative_design']['rounds'] == context['creative_design']['rounds']


def test_discussion_profile_has_one_creative_reference_and_separate_delivery_contract():
    full, version = _skill_bundle('exploration')
    brief, brief_version = _skill_bundle('discussion')
    assert version == brief_version
    assert '## 7. 输出完整骨架' in full and '## 7. 输出完整骨架' not in brief
    assert brief.count('--- references/') == 1
    assert 'Genre/Tags' in brief and '先让读者在乎' in brief
    assert '选择推动下一件事' in brief and '有压力，也有兑现' in brief
    assert '## 7. 输出完整骨架' in _skill_bundle('delivery')[0]
    assert len(brief.encode()) < len(full.encode())
    assert 'capacity_plan' not in discussion_system({}, brief)
