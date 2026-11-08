from copy import deepcopy
from pathlib import Path
import pytest
from core import reader_investment as ri
from pipeline_models import RunSpec
from orchestrator import NovelOrchestrator
import creative_execution as ce


def test_new_run_freezes_method_old_manifest_does_not_adopt_it(monkeypatch):
    new = RunSpec(project_path='project')
    raw = new.to_dict()
    before = deepcopy(new.reader_investment)
    monkeypatch.setattr(ri, 'COMMON', 'a future method')
    assert RunSpec.from_dict(raw).reader_investment == before
    raw.pop('reader_investment')
    assert RunSpec.from_dict(raw).reader_investment is None
    assert RunSpec(project_path='other').reader_investment != before
    damaged = deepcopy(before)
    damaged['common'] += 'tampered'
    with pytest.raises(ValueError): RunSpec(project_path='project', reader_investment=damaged)


@pytest.mark.parametrize('agent', ce.AGENTS)
def test_frozen_handoff_reaches_every_agent_and_survives_resume(tmp_path, monkeypatch, agent):
    source = 'A scientist chooses to reveal an error that will cost her the mission.'
    inputs = tmp_path / 'outputs/input'
    inputs.mkdir(parents=True)
    (inputs / 'prompt.md').write_text(source)
    handoff = ce.make_handoff(source=source, origin={}, reader_investment=ri.snapshot())
    runtime = ce.CreativeExecution.open(project=tmp_path, run_id='new', handoff=handoff)
    assert 'READER INVESTMENT' in runtime._lock['system_prompts'][agent]
    assert ri.instructions(handoff['reader_investment'], agent) in runtime._lock['system_prompts'][agent]
    monkeypatch.setattr(ce, '_system_prompts', lambda: pytest.fail('resume read mutable prompts'))
    resumed = ce.CreativeExecution.open(project=tmp_path, run_id='new', handoff=handoff, expected_sha256=runtime.lock_sha256, already_started=True)
    assert resumed._lock == runtime._lock
    legacy_handoff = ce.make_handoff(source=source, origin={})
    assert RunSpec(project_path=str(tmp_path), quality_policy='evidence_v1', creative_handoff=legacy_handoff).reader_investment is None


@pytest.mark.parametrize('agent', ['architect', 'scribe', 'editor', 'continuity_guardian', 'style_curator'])
def test_no_type_profile_or_workshop_required_for_new_direct_book(agent):
    orchestrator = object.__new__(NovelOrchestrator)
    orchestrator.reader_investment = ri.snapshot()
    prompt, receipt = orchestrator._creative_prompt(agent, 'SOURCE_AND_PROTOCOL', 1)
    assert prompt.startswith('SOURCE_AND_PROTOCOL')
    assert ri.instructions(orchestrator.reader_investment, agent) in prompt
    assert receipt is None
    orchestrator.reader_investment = None
    assert orchestrator._creative_prompt(agent, 'SOURCE_AND_PROTOCOL', 1) == ('SOURCE_AND_PROTOCOL', None)


def test_staged_planning_has_frozen_method_without_extra_calls(tmp_path):
    from test_outline_planning import make_planner, PlanningModel
    class Capture(PlanningModel):
        def complete(self, system, user):
            assert 'READER INVESTMENT' in system
            return super().complete(system, user)
    client = Capture()
    policy = ri.snapshot()
    planner = make_planner(tmp_path, client, count=4, reader_investment=policy)
    assert planner.identity['reader_investment_sha256'] == policy['sha256']
    result = planner.run()
    assert len(result['chapters']) == 4
    count = len(client.calls)
    assert planner.run() == result
    assert len(client.calls) == count  # restored checkpoints buy no calls


def test_each_planning_call_uses_its_frozen_stage_method(tmp_path, monkeypatch):
    source = 'The investigator chooses to reveal the family secret.'
    inputs = tmp_path / 'outputs/input'
    inputs.mkdir(parents=True)
    (inputs / 'prompt.md').write_text(source)
    policy = ri.snapshot()
    handoff = ce.make_handoff(source=source, origin={}, reader_investment=policy)
    runtime = ce.CreativeExecution.open(project=tmp_path, run_id='new', handoff=handoff)
    monkeypatch.setattr(ri, 'COMMON', 'MUTABLE POLICY MUST NOT LEAK')
    requests = []

    class Capture:
        def complete(self, system, user):
            requests.append((system, user))
            return '{}'

    for stage in ('story_design', 'chapter_plan', 'review'):
        user, receipt = runtime.prepare('outline_planner', 'SOURCE', None, None, stage=stage)
        runtime.complete(Capture(), 'outline_planner', user, receipt)
        system = requests[-1][0]
        assert policy['stages'][stage] in system
        assert all(text not in system for key, text in policy['stages'].items() if key != stage)
        assert 'MUTABLE POLICY' not in system
        assert 'rules' not in receipt['context']
        assert ce.RULES in system
        assert receipt['request_bytes'] == len(system.encode()) + len(user.encode())
    assert len(requests) == 3
