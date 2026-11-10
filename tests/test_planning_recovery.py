import json
from unittest.mock import patch

import pytest

from core import planning_correction
from core.planning_recovery import coordinated_repair_available, source_restoration_available
from outline_planning import OutlinePlanningError, content_hash
from test_outline_global_repair import ReferenceModel, SOURCE
from test_outline_planning import PlanningModel, chapter, global_design, issue, make_planner, review


def seed_v1(project):
    run = project / 'outputs/runs/test-run'
    inputs = project / 'outputs/input'
    inputs.mkdir(parents=True)
    (inputs / 'prompt.md').write_text(SOURCE)
    model = ReferenceModel(correction={'status': 'requires_author', 'reason': 'Outside reference scope.', 'edits': []})
    planner = make_planner(run, model, count=12, source=SOURCE)
    # Seed the historical stop. New tasks escalate this old model result to evidence review.
    with patch.object(planner, '_coordinate', side_effect=OutlinePlanningError('Historical scope stop')), \
            pytest.raises(OutlinePlanningError):
        planner.run()
    record = json.loads((planner.cache / f'{planning_correction.VERSION}.json').read_text())
    context = next(c for stage, c in model.calls if stage == 'global_repair')
    record['dependencies_hash'] = content_hash({'candidate': content_hash(context['candidate']),
        'review': content_hash(context['review']), 'policy': planning_correction.LEGACY_POLICY_HASH})
    (planner.cache / 'global-reference-repair-v1.json').write_text(json.dumps(record))
    for path in planner.cache.glob(f'{planning_correction.VERSION}*.json'):
        path.unlink()
    planner.progress.update(recovery={'retryable': False, 'action': 'inspect_saved_design',
                                      'reason': 'Historical unsupported scope'})
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


def seed_mixed(project, *, creative_lock=False):
    """Retain a pre-upgrade mixed failure without invoking the new repair path."""
    from core.planning_context import REVIEW_CONTEXT_VERSION
    run = project / 'outputs/runs/test-run'
    planner = make_planner(run, PlanningModel(), count=12, source=SOURCE)
    if creative_lock:
        lock = {'run_id': 'test-run', 'handoff': {'source_sha256': planner.identity['source_sha256']}}
        planner.identity['creative_lock_sha256'] = content_hash(lock)
        planner.identity_hash = content_hash(planner.identity)
        planner.cache = planner.cache.parent / planner.identity_hash
        planner.progress['identity_hash'] = planner.identity_hash
        planner._write_json(run / 'creative-design/lock.json', lock)
    inputs = project / 'outputs/input'
    inputs.mkdir(parents=True)
    (inputs / 'prompt.md').write_text(SOURCE)
    (inputs / 'brief.json').write_text(json.dumps(planner.brief))
    planner._write_json(planner.cache / 'identity.json', planner.identity)
    design = planner._global(global_design({**planner._context(), 'volume_contract_template': planner.volume_template}))
    planner._save('global', {}, design)
    chapters = []
    for index, numbers in enumerate(planner.ranges, 1):
        batch = planner._batch({'chapters': [chapter(n) for n in numbers]}, numbers, design)
        planner._save(f'batch-{index:03d}', {'global': content_hash(design), 'previous': content_hash(chapters)}, batch)
        chapters.extend(batch['chapters'])
    candidate = {**design, 'chapters': chapters}
    planner._save('review-initial', {'candidate': content_hash(candidate), 'representation': REVIEW_CONTEXT_VERSION},
                  review(issue('global'), issue('chapter')))
    planner.progress.update(status='failed', active_step='semantic_review', completed_batches=len(planner.ranges))
    planner._needs_attention('全局与章节问题需要协调修改；不会通过普通恢复反复调用模型。')
    planner._write_json(planner.directory / 'progress.json', planner.progress)
    return planner


def test_verified_mixed_failure_offers_one_read_only_coordinated_upgrade(tmp_path):
    project = tmp_path / 'book'
    planner = seed_mixed(project)
    before = {path: path.read_bytes() for path in project.rglob('*') if path.is_file()}
    assert coordinated_repair_available(project, 'test-run', planner.progress)
    assert {path: path.read_bytes() for path in project.rglob('*') if path.is_file()} == before
    assert planner.client.calls == []


@pytest.mark.parametrize('problem', [
    'source_changed', 'brief_changed', 'identity_changed', 'bad_identity', 'incomplete_batches',
    'bad_global_hash', 'bad_batch_dependency', 'bad_review_dependency', 'missing_batch',
    'chapter_number_changed', 'global_only', 'chapter_only', 'incomplete_review', 'chapter_outside_book',
    'coordinated_plan', 'coordinated_raw_attempt', 'coordinated_review', 'old_global_attempt',
    'old_chapter_attempt', 'published', 'symlink',
])
def test_mixed_recovery_requires_complete_bound_unspent_checkpoints(tmp_path, problem):
    project = tmp_path / 'book'
    planner = seed_mixed(project)
    if problem == 'source_changed':
        (project / 'outputs/input/prompt.md').write_text('Changed source')
    elif problem == 'brief_changed':
        (project / 'outputs/input/brief.json').write_text('{}')
    elif problem == 'identity_changed':
        path = planner.cache / 'identity.json'
        value = json.loads(path.read_text())
        value['target_words'] += 1
        path.write_text(json.dumps(value))
    elif problem == 'bad_identity':
        planner.progress['identity_hash'] = '../outside'
    elif problem == 'incomplete_batches':
        planner.progress['completed_batches'] -= 1
    elif problem == 'missing_batch':
        (planner.cache / 'batch-002.json').unlink()
    elif problem in {'bad_global_hash', 'bad_batch_dependency', 'bad_review_dependency', 'chapter_number_changed'}:
        name = ('global' if problem == 'bad_global_hash' else
                'review-initial' if problem == 'bad_review_dependency' else 'batch-002')
        path = planner.cache / f'{name}.json'
        value = json.loads(path.read_text())
        if problem == 'chapter_number_changed':
            value['payload']['chapters'][0]['number'] = 99
            value['payload_hash'] = content_hash(value['payload'])
        else:
            value['payload_hash' if problem == 'bad_global_hash' else 'dependencies_hash'] = '0' * 64
        path.write_text(json.dumps(value))
    elif problem in {'global_only', 'chapter_only', 'incomplete_review', 'chapter_outside_book'}:
        path = planner.cache / 'review-initial.json'
        value = json.loads(path.read_text())
        if problem == 'global_only':
            value['payload']['issues'] = value['payload']['issues'][:1]
        elif problem == 'chapter_only':
            value['payload']['issues'] = value['payload']['issues'][1:]
        elif problem == 'incomplete_review':
            value['payload']['coverage']['source_fidelity'] = False
        else:
            value['payload']['issues'][1]['chapter'] = 13
        value['payload_hash'] = content_hash(value['payload'])
        path.write_text(json.dumps(value))
    elif problem == 'symlink':
        path = planner.cache / 'batch-001.json'
        moved = tmp_path / 'outside.json'
        path.rename(moved)
        path.symlink_to(moved)
    else:
        name = {'coordinated_plan': 'coordinated-repair-v1-plan-response.json',
                'coordinated_raw_attempt': 'raw/coordinated-repair-v1-plan.request.prompt.txt',
                'coordinated_review': 'review-coordinated-repair-v1.json',
                'old_global_attempt': f'{planning_correction.VERSION}-response.json',
                'old_chapter_attempt': 'repair-response.json', 'published': 'validated.json'}[problem]
        path = planner.cache / name
        path.parent.mkdir(exist_ok=True)
        path.write_text('{}')
    assert not coordinated_repair_available(project, 'test-run', planner.progress)


def test_mixed_recovery_verifies_frozen_creative_lock(tmp_path):
    project = tmp_path / 'book'
    planner = seed_mixed(project, creative_lock=True)
    assert coordinated_repair_available(project, 'test-run', planner.progress)
    lock = planner.directory.parent / 'creative-design/lock.json'
    value = json.loads(lock.read_text())
    value['handoff']['source_sha256'] = '0' * 64
    lock.write_text(json.dumps(value))
    assert not coordinated_repair_available(project, 'test-run', planner.progress)
