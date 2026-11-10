"""Exercise migration and author choice through real persisted planning contracts."""
from copy import deepcopy
import json
import threading

import pytest

from api.jobs import JobRunner
from core import planning_coordinated as v1, planning_correction
from core.planning_author_decisions import defer_for_author, pending_decisions, save_choices
from core.planning_recovery import evidence_recovery_available, legacy_evidence_plan
from core.planning_repair_context import build_plan_context
from outline_planning import content_hash, OutlinePlanningError
from test_planning_recovery import seed_mixed
from test_project_tasks import _client, _seed, _wait_job


def _baseline(planner):
    get = lambda name: json.loads((planner.cache / (name + '.json')).read_text())['payload']
    chapters = [chapter for n in range(len(planner.ranges)) for chapter in get(f'batch-{n + 1:03d}')['chapters']]
    return {**get('global'), 'chapters': chapters}, get('review-initial')


def _legacy(project):
    planner = seed_mixed(project)
    candidate, review = _baseline(planner)
    plan = {'issues': [{'issue_index': n, 'classification': 'source_conflict' if n == 0 else 'continuity_gap',
             'reason': 'Inspect the two passages.', 'requested_chapters': [3], 'dependency_chapters': [],
             'source_quotes': [planner.source[:25]]} for n in (0, 1)]}
    original = deepcopy(plan)
    original['issues'][0]['source_quotes'].append('A generated hypothesis absent from the author source.')

    class OldReplies:
        def complete(self, system, user):
            return json.dumps(plan if user.startswith('STAGE: format_correction') else original)

    planner.client = OldReplies()
    context = build_plan_context(candidate, review, planner.source, planner.brief, planner.locked,
                                 {'target_words': planner.target_words})
    with pytest.raises(OutlinePlanningError):
        planner._call(v1.VERSION + '-plan', v1.PLAN_INSTRUCTION, context,
                      lambda x: v1.validate_plan(candidate, review, planner.source, x))
    return planner, candidate, review, original, plan


def test_double_invalid_legacy_receipts_migrate_as_evidence_without_changing_a_byte(tmp_path):
    project = tmp_path / 'book'
    planner, candidate, review, original, final = _legacy(project)
    before = {p: p.read_bytes() for p in project.rglob('*') if p.is_file()}
    assert legacy_evidence_plan(planner, candidate, review) == {'prior': original, 'latest': final}
    assert evidence_recovery_available(project, 'test-run', planner.progress)
    assert {p: p.read_bytes() for p in project.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('change', ['quote', 'wire', 'dependency', 'source', 'foreign-identity', 'spent'])
def test_legacy_migration_fails_closed_on_tampering_or_completed_migration(tmp_path, change):
    project = tmp_path / 'book'
    planner, *_ = _legacy(project)
    name = v1.VERSION + '-plan-format-correction'
    if change in {'quote', 'wire'}:
        p = next((planner.cache / 'raw').glob(name + ('.*.response.txt' if change == 'quote' else '.*.prompt.txt')))
        p.write_text(p.read_text() + ' changed')
    elif change in {'dependency', 'foreign-identity'}:
        p = planner.cache / (name + '-response.json')
        obj = json.loads(p.read_text())
        obj['dependencies_hash' if change == 'dependency' else 'identity_hash'] = '1' * 64
        p.write_text(json.dumps(obj))
    elif change == 'source':
        (project / 'outputs/input/prompt.md').write_text('A changed author statement.')
    else:
        (planner.cache / 'coordinated-evidence-v2-plan.json').write_text('{}')
    assert not evidence_recovery_available(project, 'test-run', planner.progress)


def test_legacy_global_only_author_result_can_migrate_without_granting_edits(tmp_path):
    project = tmp_path / 'book'
    planner = seed_mixed(project)
    candidate, review = _baseline(planner)
    review['issues'] = [review['issues'][0]]
    from core.planning_context import REVIEW_CONTEXT_VERSION
    planner._save('review-initial', {'candidate': content_hash(candidate), 'representation': REVIEW_CONTEXT_VERSION}, review)
    deps = {'candidate': content_hash(candidate), 'review': content_hash(review),
            'policy': content_hash(planning_correction.INSTRUCTION)}
    patch = {'status': 'requires_author', 'reason': 'Outside the old allowlist.', 'edits': []}
    planner._save(planning_correction.VERSION, deps, patch)
    assert evidence_recovery_available(project, 'test-run', planner.progress)
    planner._save(planning_correction.VERSION, {**deps, 'policy': 'stale policy'}, patch)
    assert not evidence_recovery_available(project, 'test-run', planner.progress)


def _waiting(project):
    planner = seed_mixed(project)
    passages = [planner.source[:20], planner.source[-25:]]
    conflicts = [{'issue_index': 4, 'affected_chapters': [2, 3], 'author_quotes': passages,
                  'reason': 'The author supplied two mutually exclusive outcomes.'}]
    questions, answers = pending_decisions(planner, conflicts)
    assert not answers
    with pytest.raises(ValueError):
        defer_for_author(planner, questions)
    planner.progress.update(completed_issue_indices=[1, 1, 2], deferred_issue_indices=[4])
    planner._write_json(planner.directory / 'progress.json', planner.progress)
    return planner, conflicts, questions


def test_author_chooses_from_original_passages_only_and_resume_uses_frozen_choice(tmp_path):
    project = tmp_path / 'book'
    planner, conflicts, questions = _waiting(project)
    choice = [{'issue_index': 4, 'selected_quote': 1}]
    save_choices(project, 'test-run', questions['request_id'], choice)
    save_choices(project, 'test-run', questions['request_id'], choice)
    assert pending_decisions(planner, conflicts)[1] == {4: {**choice[0], 'author_quote': conflicts[0]['author_quotes'][1]}}
    with pytest.raises(ValueError):
        save_choices(project, 'test-run', questions['request_id'], [{'issue_index': 4, 'selected_quote': 0}])
    (project / 'outputs/input/prompt.md').write_text('Changed after the request')
    with pytest.raises(ValueError):
        save_choices(project, 'test-run', questions['request_id'], choice)


def test_task_route_accepts_current_author_choice_and_starts_one_worker(tmp_path, monkeypatch):
    from api import project_tasks, project_task_summary
    from pipeline_runner import PipelineRunner
    project, manifest, path = _seed(tmp_path, run_id='test-run')
    manifest.current_phase = 'outline'
    path.write_text(json.dumps(manifest.to_dict()))
    planner, conflicts, questions = _waiting(project)
    jobs = JobRunner()
    for module in (project_tasks, project_task_summary):
        monkeypatch.setattr(module, 'runner', jobs)
    release = threading.Event()
    seen = []
    def execute(self, run_id, project_path, **kw):
        seen.append(pending_decisions(planner, conflicts)[1])
        release.wait(3)
        manifest.status = 'completed'
        return manifest
    monkeypatch.setattr(PipelineRunner, 'resume', execute)
    client = _client(tmp_path)
    url = '/api/projects/book/tasks/test-run'
    detail = client.get(url).json()
    assert detail['planning']['author_decisions'] == questions and not detail['can_resume']
    assert detail['planning']['completed_issue_count'] == 2
    assert detail['planning']['deferred_issue_count'] == 1
    data = {'request_id': questions['request_id'], 'choices': [{'issue_index': 4, 'selected_quote': 1}]}
    try:
        assert client.post(url + '/resume').status_code == 409
        assert client.post(url + '/resume', json={'author_decisions': {**data, 'request_id': '0' * 64}}).status_code == 409
        assert client.post(url + '/resume', json={'author_decisions': {**data, 'choices': [
            {'issue_index': 8, 'selected_quote': 0}]}}).status_code == 409
        result = client.post(url + '/resume', json={'author_decisions': data})
        assert result.status_code == 202, result.text
        assert client.post(url + '/resume', json={'author_decisions': data}).json() == result.json()
        assert client.post(url + '/resume', json={'author_decisions': {**data, 'choices': [
            {'issue_index': 4, 'selected_quote': 0}]}}).status_code == 409
    finally:
        release.set()
    _wait_job(jobs, result.json()['job_id'])
    assert seen == [{4: {'issue_index': 4, 'selected_quote': 1, 'author_quote': conflicts[0]['author_quotes'][1]}}]


def test_cli_status_and_choice_use_the_same_author_request(tmp_path, monkeypatch, capsys):
    from orchestrator import main
    from pipeline_runner import PipelineRunner

    project, manifest, path = _seed(tmp_path, run_id='test-run')
    manifest.current_phase = 'outline'
    path.write_text(json.dumps(manifest.to_dict()))
    planner, conflicts, questions = _waiting(project)
    args = ['--project', str(project), '--run-id', 'test-run']
    assert main(['run-status', *args]) == 0
    assert json.loads(capsys.readouterr().out)['author_decisions'] == questions
    choices_seen = []
    def execute(self, manifest, project, store, **kwargs):
        choices_seen.append(pending_decisions(planner, conflicts)[1][4]['author_quote'])
        manifest.status = 'completed'
        return manifest
    monkeypatch.setattr(PipelineRunner, '_execute', execute)
    assert main(['resume', *args, '--author-request', questions['request_id'], '--author-choice', '4', '2']) == 0
    assert choices_seen == [conflicts[0]['author_quotes'][1]]


@pytest.mark.parametrize('interruption', ['request_dispatched', 'saved_bad_json', 'invalid_final_json'])
def test_legacy_interruption_or_parse_failure_keeps_lineage_for_a_fresh_plan(tmp_path, interruption):
    from core.outline_planning import _json
    project = tmp_path / 'book'
    planner = seed_mixed(project)
    candidate, review = _baseline(planner)
    name = v1.VERSION + '-plan'
    ctx = build_plan_context(candidate, review, planner.source, planner.brief, planner.locked,
                             {'target_words': planner.target_words})
    if interruption == 'invalid_final_json':
        class BadReply:
            def complete(self, system, user):
                return 'An incomplete JSON reply'
        planner.client = BadReply()
        with pytest.raises(OutlinePlanningError):
            planner._call(name, v1.PLAN_INSTRUCTION, ctx, lambda v: v1.validate_plan(candidate, review, planner.source, v))
    else:
        text = v1.PLAN_INSTRUCTION + '\n\n' + _json(ctx)
        raw = planner.cache / 'raw'
        raw.mkdir(exist_ok=True)
        (raw / (name + '.req.prompt.txt')).write_text(text)
        if interruption == 'saved_bad_json':
            (raw / (name + '.req.response.txt')).write_text('Unexpected non-JSON reply')
            planner._save(name + '-response', {'prompt_hash': content_hash(text)}, {'raw': 'Unexpected non-JSON reply'})
    old = legacy_evidence_plan(planner, candidate, review)
    assert old['latest'] is None and old['retained_artifact_sha256']
    assert evidence_recovery_available(project, 'test-run', planner.progress)


def test_old_group_that_requested_author_reenters_evidence_review(tmp_path):
    from core.planning_coordinator import _groups
    from core.planning_repair_context import build_repair_context
    project = tmp_path / 'book'
    planner = seed_mixed(project)
    candidate, review = _baseline(planner)
    plan = {'issues': [{'issue_index': i, 'classification': 'continuity_gap', 'reason': 'Clarify the source.',
        'source_quotes': [planner.source[:24]], 'requested_chapters': [3], 'dependency_chapters': []}
        for i in range(len(review['issues']))]}
    extra = {'target_words': planner.target_words}
    ctx = build_plan_context(candidate, review, planner.source, planner.brief, planner.locked, extra)
    planner._save(v1.VERSION + '-plan', {'candidate': content_hash(candidate), 'review': content_hash(review),
        'context': content_hash(ctx), 'policy': content_hash(v1.PLAN_INSTRUCTION)}, plan)
    group, = _groups(plan, review, planner.chapter_count)
    ctx = build_repair_context(candidate, {k:v for k,v in candidate.items() if k != 'chapters'}, review,
        group['requested_chapters'], source=planner.source, brief=planner.brief, locked=planner.locked,
        issues=group['issues'], dependency_chapters=group['dependency_chapters'],
        original_issue_indices=group['original_issue_indices'], extra_context={**extra, 'coordinated_issue_plan': plan})
    patch = {'status': 'requires_author', 'reason': 'Writer believed the source was in conflict.',
        'issues': [{'issue_index': i, 'status': 'requires_author', 'reason': 'Confirm source evidence.',
                    'source_quotes': [planner.source[:24]], 'chapter_numbers': [3]} for i in range(len(review['issues']))],
        'global_edits': [], 'chapter_replacements': []}
    name = v1.VERSION + '-group-001'
    planner._save(name, {'plan': content_hash(plan), 'context': content_hash(ctx), 'policy': content_hash(v1.INSTRUCTION)}, patch)
    assert evidence_recovery_available(project, 'test-run', planner.progress)
    (planner.cache / (v1.VERSION + '-candidate.json')).write_text('{}')
    assert not evidence_recovery_available(project, 'test-run', planner.progress)


def test_completed_old_blocked_review_migrates_once_and_only_with_matching_candidate(tmp_path):
    from core.planning_context import REVIEW_CONTEXT_VERSION
    from core.planning_review_reuse import RULES
    from core.planning_recovery import coordinated_repair_available
    project = tmp_path / 'book'
    planner = seed_mixed(project)
    candidate, review = _baseline(planner)
    extra = {'target_words': planner.target_words}
    plan = {'issues': [{'issue_index': i, 'classification': 'interpretation', 'reason': 'Recheck the evidence.',
        'source_quotes': [planner.source[:24]], 'requested_chapters': [], 'dependency_chapters': [3]}
        for i in range(len(review['issues']))]}
    ctx = build_plan_context(candidate, review, planner.source, planner.brief, planner.locked, extra)
    planner._save(v1.VERSION + '-plan', {'candidate': content_hash(candidate), 'review': content_hash(review),
        'context': content_hash(ctx), 'policy': content_hash(v1.PLAN_INSTRUCTION)}, plan)
    audit = {'schema': v1.VERSION, 'baseline_candidate_sha256': content_hash(candidate), 'issues': [
        {**item, 'original_issue': review['issues'][i], 'resolution': {'status': 'recheck',
         'reason': item['reason'], 'source_quotes': item['source_quotes']}} for i, item in enumerate(plan['issues'])]}
    name = v1.VERSION + '-candidate'
    planner._save(name, {'plan': content_hash(plan), 'audit': content_hash(audit)}, {'candidate': candidate, 'verification': audit})
    deps = {'candidate': content_hash(candidate), 'representation': REVIEW_CONTEXT_VERSION,
            'coordinated_verification': content_hash(audit), 'verification_policy': content_hash(RULES)}
    planner._save(v1.REVIEW_NAME, deps, review)
    assert evidence_recovery_available(project, 'test-run', planner.progress)
    planner._save(v1.REVIEW_NAME, {**deps, 'candidate': 'stale'}, review)
    assert not evidence_recovery_available(project, 'test-run', planner.progress)
    (planner.cache / 'coordinated-evidence-v2-plan-response.json').write_text('{}')
    assert not coordinated_repair_available(project, 'test-run', planner.progress)


def test_standalone_uses_its_own_frozen_source_for_the_same_author_decision(tmp_path):
    from core.planning_author_decisions import save_standalone_choices

    planner, issues, questions = _waiting(tmp_path / 'book')
    request = questions['request_id']
    choices = [{'issue_index': 4, 'selected_quote': 0}]
    save_standalone_choices(planner, request, choices)
    assert pending_decisions(planner, issues)[1][4]['author_quote'] == issues[0]['author_quotes'][0]
    planner.source += ' This source changed.'
    with pytest.raises(ValueError):
        save_standalone_choices(planner, request, choices)


def test_author_passage_choice_survives_model_issue_scope_and_quote_reordering(tmp_path):
    from core.planning_author_decisions import authorized_choices
    project = tmp_path / 'book'
    old, conflicts, questions = _waiting(project)
    first_directory = old.cache
    passages = conflicts[0]['author_quotes']
    save_choices(project, 'test-run', questions['request_id'], [{'issue_index': 4, 'selected_quote': 1}])
    previous = authorized_choices(old)
    assert len(previous) == 1 and previous[0]['author_quote'] == passages[1]
    old_files = {f: f.read_bytes() for f in first_directory.glob('author-*.json')}
    planner = deepcopy(old)
    planner.identity = {**old.identity, 'model': {'model': 'different-model'}}
    planner.identity_hash = content_hash(planner.identity)
    planner.cache = first_directory.parent / planner.identity_hash
    planner._write_json(planner.cache / 'identity.json', planner.identity)
    changed = {**conflicts[0], 'issue_index': 9, 'author_quotes': list(reversed(passages)), 'affected_chapters': [5]}
    pending, answers = pending_decisions(planner, [changed])
    assert pending['issues'] == []
    assert answers == {9: {'issue_index': 9, 'selected_quote': 0, 'author_quote': passages[1]}}
    assert authorized_choices(planner) == previous
    assert not list(planner.cache.glob('author-*.json'))
    fresh = {**changed, 'issue_index': 2, 'author_quotes': [old.source[5:16], old.source[-19:]]}
    pending, answers = pending_decisions(planner, [fresh, changed])
    assert pending['issues'] == [fresh] and list(answers) == [9]
    assert all(f.read_bytes() == data for f, data in old_files.items())
    planner.source += ' Changed author input.'
    assert authorized_choices(planner) == []


@pytest.mark.parametrize('tamper', ['foreign_run', 'unbound_identity', 'missing_question', 'answer_hash'])
def test_cross_model_source_authority_validates_every_original_receipt(tmp_path, tamper):
    from core.planning_author_decisions import authorized_choices
    project = tmp_path / 'book'
    planner, _, questions = _waiting(project)
    save_choices(project, 'test-run', questions['request_id'], [{'issue_index': 4, 'selected_quote': 1}])
    if tamper == 'foreign_run':
        # Preserve a perfectly intact old request and old answer in another run.
        import shutil
        other = project / 'outputs/runs/another-run/planning'
        shutil.copytree(planner.directory, other)
        planner.directory = other
    elif tamper == 'unbound_identity':
        (planner.cache / 'identity.json').write_text('{}')
    elif tamper == 'missing_question':
        (planner.cache / f"author-question-{questions['request_id']}.json").unlink()
    else:
        answer = planner.cache / f"author-answer-{questions['request_id']}.json"
        data = json.loads(answer.read_text())
        data['choices'][0]['selected_quote'] = 0
        answer.write_text(json.dumps(data))
    with pytest.raises((ValueError, FileNotFoundError)):
        authorized_choices(planner)


@pytest.mark.parametrize('kind', ['global_v1', 'global_v2', 'chapter'])
def test_specialized_final_review_is_bound_to_the_revised_design_before_upgrade(tmp_path, kind):
    from core.planning_context import REVIEW_CONTEXT_VERSION
    from core.planning_recovery import specialized_review_availability
    from test_outline_global_repair import ISSUE, ReferenceModel
    from test_outline_planning import issue

    project = tmp_path / 'book'
    planner = seed_mixed(project)
    baseline, initial = _baseline(planner)
    initial['issues'] = [ISSUE] if kind.startswith('global') else [issue('chapter', 3)]
    planner._save('review-initial', {'candidate': content_hash(baseline), 'representation': REVIEW_CONTEXT_VERSION}, initial)
    deps = {'candidate': content_hash(baseline), 'review': content_hash(initial)}
    if kind == 'chapter':
        # The legacy failure did not set a recovery hint for chapter-only results.
        del planner.progress['recovery']
        revised = deepcopy(baseline)
        revised['chapters'][2]['goal'] = 'Restore the choice at the studio.'
        planner._save('repair', deps, {'chapters': [revised['chapters'][2]]})
        label = 'review-repaired'
    else:
        legacy = kind == 'global_v1'
        patch = ReferenceModel().correction
        planner._save('global-reference-repair-v1' if legacy else planning_correction.VERSION, {**deps,
            'policy': planning_correction.LEGACY_POLICY_HASH if legacy else content_hash(planning_correction.INSTRUCTION)}, patch)
        revised = planning_correction.apply(baseline, initial, planner.source, patch, planner.chapter_count)
        chapters = revised.pop('chapters')
        revised = {**planner._global(revised), 'chapters': chapters}
        label = 'review-global-reference-v1' if legacy else planning_correction.REVIEW_NAME
    assert revised != baseline
    deps = {'candidate': content_hash(revised), 'representation': REVIEW_CONTEXT_VERSION}
    planner._save(label, deps, initial)
    assert specialized_review_availability(project, 'test-run', planner.progress) is True
    planner._save(label, deps, {**initial, 'status': 'passed', 'issues': []})
    assert specialized_review_availability(project, 'test-run', planner.progress) is None
    planner._save(label, {**deps, 'candidate': content_hash(baseline)}, initial)
    assert specialized_review_availability(project, 'test-run', planner.progress) is False
