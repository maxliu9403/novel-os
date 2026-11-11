import json

import pytest

from api.project_task_summary import task_sidecar
from core import planning_evidence_plan
from core.planning_coordinator import _repair_round
from core.planning_recovery import residual_recovery_availability, verified_terminal
from core.outline_planning import content_hash
from test_outline_coordinated_repair import CoordinatedModel, QUOTE
from test_outline_planning import issue, review
from test_planning_evidence_recovery import _baseline
from test_planning_recovery import seed_mixed

PREFIX = planning_evidence_plan.VERSION


def archived_rounds(project):
    planner = seed_mixed(project)
    original, initial = _baseline(planner)
    next_review = review(issue('chapter', 4))
    planner.client = CoordinatedModel(reviews=[next_review])
    extra = {'target_words': planner.target_words}
    candidate, checked, _ = _repair_round(planner, original, initial, prefix=PREFIX, limit=None, extra=extra)
    final_review = review(issue('chapter', 5))
    planner.client = CoordinatedModel(reviews=[final_review], plan={'issues': [{
        'issue_index': 0, 'classification': 'continuity_gap', 'reason': 'Restore the causal connection.',
        'requested_chapters': [4], 'dependency_chapters': [3], 'source_quotes': [QUOTE]}]})
    revised, final, _ = _repair_round(planner, candidate, checked, prefix=PREFIX + '-followup', limit=None,
                                      extra=extra, initial=False)
    planner.progress.update(status='failed')
    planner._needs_attention('Previously bounded correction stopped after the second review.')
    planner._write_json(planner.directory / 'progress.json', planner.progress)
    return planner, original, initial, revised, final


def test_completed_followup_uses_latest_immutable_outline_in_native_and_studio(tmp_path):
    project = tmp_path / 'book'
    planner, original, initial, revised, final = archived_rounds(project)
    before = {p: p.read_bytes() for p in project.rglob('*') if p.is_file()}
    planner.client.calls.clear()
    assert verified_terminal(planner, original, initial, PREFIX) == (revised, final)
    for number in (3, 4):
        assert revised['chapters'][number - 1] != original['chapters'][number - 1]
    result = task_sidecar(project, 'test-run', 'planning/progress.json')
    assert result['recovery']['retryable'] and result['recovery']['action'] == 'repair_saved_residuals'
    assert not planner.client.calls
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('problem', ['candidate', 'audit', 'verdict', 'parent', 'source', 'identity'])
def test_changed_lineage_cannot_reopen_an_older_completed_verdict(tmp_path, problem):
    project = tmp_path / 'book'
    planner, original, initial, _, _ = archived_rounds(project)
    if problem == 'source':
        (project / 'outputs/input/prompt.md').write_text('Changed the original.')
    elif problem == 'parent':
        (planner.cache / ('review-' + PREFIX + '.json')).unlink()
    else:
        name = ('review-' + PREFIX + '-followup') if problem == 'verdict' else PREFIX + '-followup-candidate'
        path = planner.cache / (name + '.json')
        record = json.loads(path.read_text())
        if problem == 'identity':
            record['identity_hash'] = '0' * 64
        elif problem == 'verdict':
            record['dependencies_hash'] = '0' * 64
        elif problem == 'audit':
            record['payload']['verification']['issues'][0]['original_issue']['message'] = 'foreign issue'
        else:
            record['payload']['candidate']['chapters'][0]['goal'] = 'foreign candidate'
        record['payload_hash'] = content_hash(record['payload'])
        path.write_text(json.dumps(record))
    assert residual_recovery_availability(project, 'test-run', planner.progress) is False
    if problem != 'source':
        with pytest.raises(ValueError):
            verified_terminal(planner, original, initial, PREFIX)


def test_partial_followup_keeps_its_original_response_namespace(tmp_path):
    project = tmp_path / 'book'
    planner, original, initial, _, _ = archived_rounds(project)
    (planner.cache / ('review-' + PREFIX + '-followup.json')).unlink()
    # The final response was saved. The normal follow-up resumes its promotion.
    assert (planner.cache / ('review-' + PREFIX + '-followup-response.json')).is_file()
    assert verified_terminal(planner, original, initial, PREFIX) is None
