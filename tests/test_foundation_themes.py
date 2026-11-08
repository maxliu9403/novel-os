"""Rich Architect themes must survive the strict execution-contract boundary."""
import copy
import json

import pytest

from artifacts import ArtifactStore
from contracts import AuthorIntent, StoryContract
from pipeline_runner import PipelineRunner
from core.foundation_themes import theme_statements
from outline_planning import OutlinePlanningError
from orchestrator import NovelOrchestrator
from test_outline_planning import PlanningModel, SOURCE, make_planner


THEMES = [
    {'name': 'Care without exoneration', 'argument': 'Treatment does not erase harmful choices.'},
    'Earned independence',
]


def test_real_contract_persistence_keeps_theme_name_and_argument(tmp_path):
    inputs = tmp_path / 'outputs/input'
    inputs.mkdir(parents=True)
    (inputs / 'brief.json').write_text(json.dumps({'title': 'North Door', 'genre': 'Suspense', 'premise': 'A costly choice.'}))
    source = json.dumps({'title': 'North Door', 'themes': THEMES})
    (inputs / 'foundation.json').write_text(source)
    revision_id = PipelineRunner._persist_story_contract(tmp_path)
    store = ArtifactStore(tmp_path)
    contract = StoryContract.from_dict(json.loads(store.read_text(revision_id)))
    assert contract.themes == ('Care without exoneration: Treatment does not erase harmful choices.', 'Earned independence')
    assert (inputs / 'foundation.json').read_text() == source


def test_rich_themes_reach_final_planner_gate_unchanged(tmp_path):
    def with_themes(value):
        value['themes'] = copy.deepcopy(THEMES)
    model = PlanningModel(mutate_global=with_themes)
    result = make_planner(tmp_path, model, count=4).run()
    assert result['themes'] == THEMES


def test_execution_contract_remains_strict_about_non_text_themes():
    with pytest.raises(ValueError, match='themes must contain only strings'):
        StoryContract(title='North Door', genre='Suspense',
                      intent=AuthorIntent(premise='A choice.', target_audience='Adult'), themes=THEMES)


def test_published_outline_can_commit_its_execution_contract_and_resume_without_model(tmp_path):
    project = tmp_path / 'project'
    orch = NovelOrchestrator(str(project), state_update_mode='proposal_only')
    orch.outline_planning_mode = 'staged'
    orch.pipeline_run_id = 'themes-run'
    def with_themes(value):
        value['themes'] = copy.deepcopy(THEMES)
    model = PlanningModel(mutate_global=with_themes)
    orch._llm = model
    inputs = orch.outputs_dir / 'input'
    inputs.mkdir(exist_ok=True)
    (inputs / 'prompt.md').write_text(SOURCE)
    (inputs / 'brief.json').write_text(json.dumps({'title': 'The Studio', 'genre': "Women's Fiction", 'premise': SOURCE}))
    orch.plan_outline(num_chapters=4, target_words=4000)
    saved = (inputs / 'foundation.json').read_bytes()
    revision = PipelineRunner._persist_story_contract(project)
    contract = json.loads(ArtifactStore(project).read_text(revision))
    assert contract['themes'][0] == 'Care without exoneration: Treatment does not erase harmful choices.'
    orch._write_foundation_story_bible(json.loads(saved))
    assert 'Treatment does not erase harmful choices.' in (orch.outputs_dir / 'story_bible.md').read_text()
    model.calls.clear()
    orch.plan_outline(num_chapters=4, target_words=4000)
    PipelineRunner._persist_story_contract(project)
    assert model.calls == []
    assert (inputs / 'foundation.json').read_bytes() == saved


@pytest.mark.parametrize('themes', [[False], [42], [{'name': ''}], [{'name': 'Agency', 'argument': []}],
    [{'name': 'Agency', 'argument': 'Retain this.', 'extra_detail': 'Do not silently drop this.'}],
    [{'argument': 'Missing name.'}], [['nested']]])
def test_malformed_themes_stop_before_chapter_generation(tmp_path, themes):
    model = PlanningModel(mutate_global=lambda value: value.update(themes=themes))
    with pytest.raises(OutlinePlanningError, match='themes'):
        make_planner(tmp_path, model, count=4).run()
    assert [stage for stage, _ in model.calls] == ['global', 'format']


def test_text_only_contract_ids_remain_stable():
    args = dict(title='North Door', genre='Suspense', intent=AuthorIntent(premise='A choice.', target_audience='Adult'))
    legacy = [' Agency ', '  ', 'Bounded forgiveness']
    assert StoryContract(**args, themes=legacy).contract_id == StoryContract(**args, themes=theme_statements(legacy)).contract_id
    assert theme_statements([{'name': 'Agency'}]) == ('Agency',)


@pytest.mark.parametrize('themes', ['Not a list', {'name': 'Not a list'}, False, 3])
def test_non_list_themes_are_not_silently_iterated_or_discarded(themes):
    with pytest.raises(ValueError, match='list'):
        theme_statements(themes)
