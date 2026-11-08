import json

import pytest

from core import planning_correction
from core.planning_recovery import source_restoration_available
from outline_planning import OutlinePlanningError, content_hash
from test_outline_global_repair import ReferenceModel, SOURCE
from test_outline_planning import make_planner, review


def seed_v1(project):
    run = project / 'outputs/runs/test-run'
    inputs = project / 'outputs/input'
    inputs.mkdir(parents=True)
    (inputs / 'prompt.md').write_text(SOURCE)
    model = ReferenceModel(correction={'status': 'requires_author', 'reason': 'Outside reference scope.', 'edits': []})
    planner = make_planner(run, model, count=12, source=SOURCE)
    with pytest.raises(OutlinePlanningError):
        planner.run()
    record = json.loads((planner.cache / f'{planning_correction.VERSION}.json').read_text())
    context = next(c for stage, c in model.calls if stage == 'global_repair')
    record['dependencies_hash'] = content_hash({'candidate': content_hash(context['candidate']),
        'review': content_hash(context['review']), 'policy': planning_correction.LEGACY_POLICY_HASH})
    (planner.cache / 'global-reference-repair-v1.json').write_text(json.dumps(record))
    for path in planner.cache.glob(f'{planning_correction.VERSION}*.json'):
        path.unlink()
    return planner


def test_v1_unsupported_scope_can_explicitly_resume_with_new_policy(tmp_path):
    project = tmp_path / 'book'
    first = seed_v1(project)
    before = {p: p.read_bytes() for p in first.cache.glob('*.json')}
    assert source_restoration_available(project, 'test-run', first.progress)
    assert all(p.read_bytes() == value for p, value in before.items())
    model = ReferenceModel(reviews=[review()])
    result = make_planner(first.directory.parent, model, count=12, source=SOURCE).run()
    assert result['volume_contracts'][0]['climax_chapter'] == 9
    assert [stage for stage, _ in model.calls] == ['global_repair', 'review']
    assert not source_restoration_available(project, 'test-run', first.progress)
    assert all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize('problem', ['source_changed', 'bad_hash', 'mixed_review', 'new_attempt', 'review_failed', 'symlink', 'bad_identity'])
def test_recovery_does_not_offer_unverified_or_exhausted_upgrade(tmp_path, problem):
    project = tmp_path / 'book'
    planner = seed_v1(project)
    if problem == 'source_changed':
        (project / 'outputs/input/prompt.md').write_text('Changed source.')
    elif problem == 'bad_identity':
        planner.progress['identity_hash'] = '../outside'
    elif problem in {'bad_hash', 'mixed_review'}:
        path = planner.cache / 'review-initial.json'
        data = json.loads(path.read_text())
        data['payload']['issues'][0]['scope'] = 'chapter'
        if problem == 'mixed_review':
            data['payload_hash'] = content_hash(data['payload'])
        path.write_text(json.dumps(data))
    elif problem == 'new_attempt':
        (planner.cache / f'{planning_correction.VERSION}-response.json').write_text('{}')
    elif problem == 'review_failed':
        (planner.cache / 'review-global-reference-v1.json').write_text('{}')
    elif problem == 'symlink':
        path = planner.cache / 'global-reference-repair-v1.json'
        moved = tmp_path / 'outside.json'
        path.rename(moved)
        path.symlink_to(moved)
    assert not source_restoration_available(project, 'test-run', planner.progress)
