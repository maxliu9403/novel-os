import json

import pytest


@pytest.mark.parametrize('mode', ['staged', 'legacy'])
def test_cli_new_run_planning_mode(tmp_path, mode):
    from orchestrator import main
    prompt = tmp_path / 'source.md'
    prompt.write_text('A story.')
    project = tmp_path / 'book'
    args = ['run', '--project', str(project), '--prompt', str(prompt), '--dry-run']
    if mode == 'legacy':
        args += ['--outline-planning-mode', mode]
    assert main(args) == 0
    saved = next((project / 'outputs/runs').glob('*/run.json'))
    assert json.loads(saved.read_text())['spec']['outline_planning_mode'] == mode


@pytest.mark.parametrize('upgrade', [False, True])
def test_cli_resume_only_passes_explicit_upgrade(tmp_path, monkeypatch, upgrade):
    from orchestrator import main
    from pipeline_runner import PipelineRunner
    from pipeline_models import RunManifest, RunSpec
    calls = []
    def resume(self, run_id, project_path, **options):
        calls.append(options)
        result = RunManifest.new(RunSpec(project_path=str(tmp_path)), run_id)
        result.status = 'completed'
        return result
    monkeypatch.setattr(PipelineRunner, 'resume', resume)
    args = ['resume', '--project', str(tmp_path), '--run-id', 'run']
    if upgrade:
        args += ['--outline-planning-mode', 'staged']
    assert main(args) == 0
    assert calls == [{'approval_policy': None, 'approve_chapter': None,
                      **({'outline_planning_mode': 'staged'} if upgrade else {})}]
