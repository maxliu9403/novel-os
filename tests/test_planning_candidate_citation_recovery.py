import json

import pytest

from core import planning_evidence_plan as evidence
from core.outline_planning import OutlinePlanningError, content_hash
from core.planning_coordinator import _authority_inputs
from core.planning_repair_context import build_plan_context
from test_planning_evidence_recovery import _baseline
from test_planning_recovery import seed_mixed
from test_project_tasks import _client, _seed


def failed_plan(project, *, wrong_leaf=False):
    """Archive a mixed plan and its correction with the formerly unsupported numeric citation."""
    planner = seed_mixed(project)
    candidate, review = _baseline(planner)
    path = '/volume_contracts/0/climax_chapter'
    stage = candidate['volume_contracts'][0]['climax_chapter']
    _authority_inputs(planner, candidate, review, evidence.VERSION)
    ctx = build_plan_context(candidate, review, planner.source, planner.brief, planner.locked,
                             {'target_words': planner.target_words})
    def plan(quote):
        return {'issues': [{'classification': 'contradiction', 'issue_index': i, 'reason': 'Restore the original timeline.',
                'affected_chapters': [3], 'writable_chapters': [3], 'dependency_chapters': [],
                'foundation_repairs': [], 'source_quotes': [planner.source[:25]], 'candidate_evidence': (
                    [{'path': path, 'quote': quote}] if i == 0 else [])} for i in range(2)]}
    class OldModel:
        def complete(self, system, user):
            return json.dumps(plan(f'"climax_chapter":{stage + int(wrong_leaf)}' if user.startswith(
                'STAGE: format_correction') else str(stage)))
    planner.client = OldModel()
    def old_validate(_):
        raise ValueError('issues[0].candidate_evidence[0] must quote the declared visible candidate field exactly')
    with pytest.raises(OutlinePlanningError):
        planner._call(evidence.VERSION + '-plan', evidence.PLAN_INSTRUCTION, ctx, old_validate)
    return planner


def test_api_exposes_recoverable_saved_citation_plan_without_writing(tmp_path):
    project, manifest, path = _seed(tmp_path, run_id='test-run')
    manifest.current_phase = 'outline'
    path.write_text(json.dumps(manifest.to_dict()))
    failed_plan(project)
    before = {p: p.read_bytes() for p in project.rglob('*') if p.is_file()}
    client = _client(tmp_path)
    detail = client.get('/api/projects/book/tasks/test-run').json()
    assert detail['can_resume']
    assert detail['planning']['recovery']['action'] == 'continue_saved_citation_plan'
    assert client.get('/api/projects/book').json()['task_can_resume']
    assert all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize('damage', ['wrong-leaf','source','first-prompt','corrected-response','missing-first',
                                     'missing-correction','later-group','later-plan','forged-author'])
def test_citation_recovery_rejects_unbound_incomplete_or_spent_plan(tmp_path, damage):
    from core.planning_recovery import candidate_citation_recovery_available
    project = tmp_path / 'book'
    planner = failed_plan(project, wrong_leaf=damage == 'wrong-leaf')
    prefix = evidence.VERSION
    if damage == 'source':
        (project / 'outputs/input/prompt.md').write_text('Something else.')
    elif damage == 'first-prompt':
        next((planner.cache / 'raw').glob(prefix + '-plan.*.prompt.txt')).write_text('Different prompt')
    elif damage == 'corrected-response':
        next((planner.cache / 'raw').glob(prefix + '-plan-format-correction.*.response.txt')).write_text('Different reply')
    elif damage == 'missing-first':
        (planner.cache / (prefix + '-plan-response.json')).unlink()
    elif damage == 'missing-correction':
        (planner.cache / (prefix + '-plan-format-correction-response.json')).unlink()
    elif damage == 'later-group':
        (planner.cache / 'raw'/(prefix + '-group-001.a.prompt.txt')).write_text('In flight')
    elif damage == 'later-plan':
        (planner.cache / (prefix + '-plan.json')).write_text('{}')
    elif damage == 'forged-author':
        path = planner.cache / (prefix + '-author-inputs.json')
        value = json.loads(path.read_bytes())
        value['payload'] = {'choices': [{'choice_id': 'unauthorized'}]}
        value['payload_hash'] = content_hash(value['payload'])
        path.write_text(json.dumps(value))
    assert not candidate_citation_recovery_available(project, 'test-run', planner.progress)
