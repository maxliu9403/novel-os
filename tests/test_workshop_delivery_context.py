"""The model reads stage-specific sources; persistent story inputs stay complete."""
from copy import deepcopy
import hashlib
import json

import pytest

from api.workshop_delivery import author_context
from api.workshop_request import delivery_context
from core.skeleton_projection import story_parts
from core.story_design import render
from test_story_first_engine import design  # noqa: F401


def context(design, stage):
    session, story, chapters = deepcopy(design)
    session['prompt_md'] = render(session, story, chapters)['prompt_md']
    session['messages'] = [
        {'role': 'user', 'content': 'Keep her profession and the first promise.'},
        {'role': 'assistant', 'content': 'Earlier alternatives.', 'questions': ['An old question?']},
        {'role': 'user', 'content': 'Adopt the second option; preserve her agency.'},
        {'role': 'assistant', 'content': 'Latest option: a bounded alliance.'},
        {'role': 'user', 'content': 'Keep that, but give Iris an independent choice.'},
    ]
    # No creative policy is needed for the source view.
    session.pop('creative_design')
    base = author_context(session)
    base.update(delivery_stage=stage, delivery_mode='short_novel', saved_story_stages={})
    base['source_reference'] = {'analysis': {'story_summary': 'Complete source facts.',
        'uncertainties': ['Unknown ending.']}, 'continuation_plan': {'proposal': 'Not adopted.'}}
    base['creative_design'] = {
        'adopted_proposals': [{'proposal': 'A second option.', 'status': 'accepted'}],
        'genre_context': {'documents': [{'markdown': '# Arbitrary guide\nDo not truncate this.'}]},
        'story_state': {'map': {'synopsis': 'Earlier map.'},
            'pending': {'topic': 'Author still decides'}, 'assumptions': [{'status': 'unresolved'}]},
    }
    if stage == 'chapter_plan':
        base['saved_story_stages']['story_design'] = story
    return base, story, chapters


def test_story_split_retains_exact_globals_chapters_and_all_structure(design):
    value, _, chapters = context(design, 'story_design')
    source = value['current_complete_skeleton']
    parts = story_parts(source)
    assert parts['source_sha256'] == hashlib.sha256(source.encode()).hexdigest()
    assert parts['source_bytes'] == len(source.encode())
    assert parts['chapter_count'] == len(chapters['chapters'])
    assert len(parts['chapter_index']) == len(chapters['chapters'])
    assert all(line in source for line in parts['chapter_index'])
    assert parts['chapter_text'] in source and parts['header'] in source
    for chapter in chapters['chapters']:
        for line in chapter['core_plot'] + chapter['setups_payoffs'] + [chapter['reader_value'], chapter['ending_hook']]:
            assert line in parts['chapter_text']
    # Only the terminal machine contracts were decoded, not story prose.
    assert '[NOVEL_CLASSIFICATION_JSON]' not in parts['global_text']
    assert parts['narrative_format']['total_chapters'] == 2
    assert parts['publication_metadata']


@pytest.mark.parametrize('mutate', [
    lambda s: s.replace('<!-- story-first.v1 -->', '<!-- unknown -->'),
    lambda s: s + '\n## 7. Custom extension\nKEEP',
    lambda s: s + '\nUnknown extra prose after the contracts.',
    lambda s: s.replace('### Chapter 2', '### Chapter 3'),
    lambda s: s.replace('Chapters: 2', 'Chapters: 5'),
    lambda s: s.replace('## 3.', '## 8.'),
    lambda s: s.replace('[NARRATIVE_FORMAT_JSON]', '[NARRATIVE_FORMAT_JSON]\nnot JSON'),
    lambda s: s + '\n```unclosed',
])
def test_unknown_source_falls_back_to_complete_context(design, mutate):
    value, _, _ = context(design, 'story_design')
    value['current_complete_skeleton'] = mutate(value['current_complete_skeleton'])
    assert story_parts(value['current_complete_skeleton']) is None
    assert delivery_context(value) == value


def test_global_request_reads_full_conversation_and_declares_deferred_chapters(design):
    value, story, _ = context(design, 'story_design')
    original = deepcopy(value)
    result = delivery_context(value)
    assert value == original
    assert result['conversation'] == original['conversation']
    assert result['source_reference'] == original['source_reference']
    assert result['creative_design'] == original['creative_design']
    assert result['initial_brief'] == original['initial_brief']
    assert 'current_complete_skeleton' not in result
    view = result['previous_skeleton']
    assert view['scope'] == 'global_design'
    assert 'chapter_plan' in view['deferred_to_chapter_plan']
    for key in ('characters', 'ending', 'decisions', 'world_rules', 'causal_chain', 'setups_payoffs', 'voice'):
        assert story['story_design'][key] in view['global_text']


def test_chapter_request_retains_all_author_words_source_and_original_scenes(design):
    value, _, _ = context(design, 'chapter_plan')
    original = deepcopy(value)
    result = delivery_context(value)
    assert value == original
    assert result['source_reference'] == original['source_reference']
    assert result['initial_brief'] == original['initial_brief']
    assert result['saved_story_stages'] == original['saved_story_stages']
    assert result['creative_design']['genre_context'] == original['creative_design']['genre_context']
    assert result['creative_design']['adopted_proposals'] == original['creative_design']['adopted_proposals']
    assert result['creative_design']['story_state']['assumptions'] == original['creative_design']['story_state']['assumptions']
    assert result['creative_design']['story_state']['pending'] == original['creative_design']['story_state']['pending']
    assert len(result['conversation']) == len(original['conversation'])
    for i, message in enumerate(original['conversation']):
        if message['role'] == 'user' or i == 3:
            assert result['conversation'][i] == message
    assert result['conversation_scope']['omitted_assistant_indexes'] == [1]
    assert result['previous_skeleton']['chapter_text'] == story_parts(value['current_complete_skeleton'])['chapter_text']


def test_no_validated_global_no_reduction_and_unknown_classification_preserved(design):
    value, _, _ = context(design, 'chapter_plan')
    value['saved_story_stages'] = {}
    assert delivery_context(value) == value
    value['delivery_stage'] = 'story_design'
    value['initial_brief'].pop('publication_taxonomy')
    assert delivery_context(value)['previous_skeleton']['publication_metadata']


def test_coordinator_projects_both_requests_then_locally_renders_complete_story(design, monkeypatch):
    from api import workshop
    from core import llm_client
    from api.workshop_design import new_design
    session, story, chapters = deepcopy(design)
    session['creative_design'] = new_design(session['input'], guided=False)
    session['prompt_md'] = render(session, story, chapters)['prompt_md']
    session['messages'] = context(design, 'chapter_plan')[0]['conversation']
    before = deepcopy(session)
    calls = []
    monkeypatch.setattr(workshop, '_skill_bundle', lambda *a: ('Design concrete stories.', 'fixture'))
    monkeypatch.setattr('core.provider_settings.resolve_text_route', lambda _: {
        'provider': 'fake', 'model': 'test', 'timeout_seconds': 60, 'base_url': None,
        'api_key': None, 'max_tokens': 8192})
    class Client:
        def __init__(self, **kwargs): pass
        def complete(self, system, user):
            data = json.loads(user)
            calls.append(data)
            return json.dumps(story if data['delivery_stage'] == 'story_design' else chapters)
    monkeypatch.setattr(llm_client, 'LLMClient', Client)
    result, _ = workshop._complete(session, 'Design concrete stories.')
    assert [c['previous_skeleton']['scope'] for c in calls] == ['global_design', 'all_original_chapters']
    assert result['prompt_md'] == before['prompt_md']
    assert result['ready_for_confirmation']
    assert session['messages'] == before['messages']
    assert session['prompt_md'] == before['prompt_md']
