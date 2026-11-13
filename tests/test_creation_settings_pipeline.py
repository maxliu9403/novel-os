"""Real route input, story compiler and intake; inference is replaced locally."""
import json

import pytest

from api import workshop
from core.creative_baseline import build
from core.pipeline_runner import PipelineRunner
from core.prompt_intake import ingest_prompt
from core.state_manager import StoryState
from core.story_design import render
from test_source_route_handoff import setup_source
from test_story_first_engine import design  # noqa: F401
from test_workshop_api import client, finish, model_result  # noqa: F401


@pytest.mark.parametrize('settings,expected_language,expected_audience', [
    ({}, 'English', '欧美女性读者'),
    ({'language': '简体中文', 'audience': '中文科幻读者', 'market': '中国大陆'},
     '简体中文', '中文科幻读者'),
])
def test_reference_creation_settings_reach_story_contract(
    client, monkeypatch, tmp_path, design, settings, expected_language, expected_audience,
):
    _, _, source_design, saved, body = setup_source(client)
    original_route = json.dumps(saved, ensure_ascii=False, sort_keys=True)
    constraints = '保留女主的独立结局；不得改变原有的情绪回报。\n保留全部资料出处。'
    body['input'].update(chapters=2, words_per_chapter=800, target_total_words=1600,
                         constraints=constraints, **settings)
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    response = client.post('/api/workshop/source-route-handoffs', json=body)
    assert response.status_code == 202, response.text
    session = finish(client, response.json()['session_id'])
    assert session['input']['language'] == expected_language
    assert session['input']['audience'] == expected_audience
    assert session['input']['market'] == settings.get('market', '欧美')
    assert session['input']['constraints'] == constraints

    baseline = build(session)
    assert baseline['initial_preferences']['language'] == expected_language
    assert baseline['initial_preferences']['audience'] == expected_audience
    assert baseline['initial_preferences']['market'] == settings.get('market', '欧美')
    assert any(entry['text'] == constraints for entry in baseline['author_requirements']['entries'])
    assert baseline['route_snapshot'] == saved['routes'][0]

    # Chinese discussion/route text must not change the selected prose language.
    _, global_design, chapter_design = design
    result = render(session, global_design, chapter_design)
    source = tmp_path / 'compiled-creation.md'
    source.write_text(result['prompt_md'], encoding='utf-8')
    project = tmp_path / 'created-novel'
    intake = ingest_prompt(project, source, generate_author=False)
    assert intake.brief['language'] == expected_language
    assert intake.brief['audience'] == expected_audience
    PipelineRunner._persist_story_contract(project)
    state = StoryState(str(project))
    assert state.metadata['language'] == expected_language
    assert state.metadata['audience'] == expected_audience
    from core.artifacts import ArtifactStore
    artifacts = ArtifactStore(project)
    revision = artifacts.get_head(0, 'story_contract')
    contract = json.loads(artifacts.read_text(revision.revision_id))
    assert contract['intent']['language'] == expected_language
    assert contract['intent']['target_audience'] == expected_audience
    assert json.dumps(source_design.get_route_set(saved['sha256']), ensure_ascii=False,
                      sort_keys=True) == original_route
