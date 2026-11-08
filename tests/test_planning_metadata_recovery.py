"""Read-only eligibility for deterministic recovery of saved citation metadata."""
from copy import deepcopy
import json

import pytest

from core import planning_coordinated as contract
from core.planning_context import REVIEW_CONTEXT_VERSION
from core.planning_coordinator import _groups
from core.planning_recovery import metadata_recovery_available
from core.planning_repair_context import build_plan_context, build_repair_context
from outline_planning import OutlinePlanningError, content_hash
from test_outline_planning import PlanningModel, chapter, global_design, make_planner, review
from test_planning_repair_context import source


def seed_metadata_failure(project, *, original_index_error=True, latest_consequence=None):
    run = project / 'outputs/runs/test-run'
    planner = make_planner(run, PlanningModel(), count=12, source=source())
    inputs = project / 'outputs/input'
    inputs.mkdir(parents=True)
    (inputs / 'prompt.md').write_text(planner.source)
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
    findings = review({'scope': 'global', 'message': 'Preserve the earlier source choice.',
                      'source_evidence': 'Exact source choice 3:'},
                     {'scope': 'chapter', 'chapter': 9, 'message': 'Clarify the later source consequence.',
                      'source_evidence': 'Exact source choice 9:'})
    planner._save('review-initial', {'candidate': content_hash(candidate), 'representation': REVIEW_CONTEXT_VERSION}, findings)
    plan = {'issues': [{'issue_index': i, 'classification': 'continuity_gap',
             'reason': 'Clarify the source-supported transition.', 'requested_chapters': [n],
             'dependency_chapters': [], 'source_quotes': [f'Exact source choice {n}:']}
             for i, n in enumerate((3, 9))]}
    extra = {'target_words': planner.target_words}
    plan_context = build_plan_context(candidate, findings, planner.source, planner.brief, planner.locked, extra)
    planner._save(contract.VERSION + '-plan', {'candidate': content_hash(candidate), 'review': content_hash(findings),
        'context': content_hash(plan_context), 'policy': content_hash(contract.PLAN_INSTRUCTION)}, plan)
    for index, group in enumerate(_groups(plan, findings, 12), 1):
        context = build_repair_context(candidate, design, findings, group['requested_chapters'],
            source=planner.source, brief=planner.brief, locked=planner.locked,
            dependency_chapters=group['dependency_chapters'], issues=group['issues'],
            original_issue_indices=group['original_issue_indices'],
            extra_context={**extra, 'coordinated_issue_plan': plan})
        number = group['requested_chapters'][0]
        before = deepcopy(chapters[number - 1])
        after = {**deepcopy(before), 'incoming_consequence': 'The established prior choice causes this transition.'}
        quote = f'Exact source choice {number}:' if index == 1 else before['incoming_consequence']
        patch = {'status': 'corrected', 'reason': 'Clarify only the planned transition.',
            'issues': [{'issue_index': 0, 'status': 'corrected', 'reason': 'The original choice remains unchanged.',
                        'source_quotes': [quote], 'chapter_numbers': [number]}],
            'chapter_replacements': [{'number': number, 'before_sha256': content_hash(before), 'after': after,
                                     'issue_indices': [0], 'source_quote': quote}], 'global_edits': []}
        name = f'{contract.VERSION}-group-{index:03d}'
        if index == 1:
            contract.apply(candidate, context['review'], planner.source, patch, requested_chapters=[number])
            planner._save(name, {'plan': content_hash(plan), 'context': content_hash(context),
                                'policy': content_hash(contract.INSTRUCTION)}, patch)
            continue
        original = deepcopy(patch)
        if original_index_error:
            original['issues'][0]['issue_index'] = 1
            original['chapter_replacements'][0]['issue_indices'] = [1]
        if latest_consequence is not None:
            patch['chapter_replacements'][0]['after']['incoming_consequence'] = latest_consequence

        class Returned(PlanningModel):
            def complete(self, system, user):
                self.calls.append(user)
                return json.dumps(patch if user.startswith('STAGE: format_correction') else original)

        planner.client = Returned()
        with pytest.raises(OutlinePlanningError, match='bounded format correction'):
            planner._call(name, contract.INSTRUCTION, context,
                lambda value: contract.apply(candidate, context['review'], planner.source, value,
                                              requested_chapters=[number]), strict_dependencies=True)
    planner.progress.update(status='failed', active_step='semantic_review', completed_batches=3)
    planner._needs_attention('保存的修订仍未通过来源引用合同。')
    planner._write_json(planner.directory / 'progress.json', planner.progress)
    return planner


def test_saved_metadata_failure_has_read_only_recovery_without_new_generation(tmp_path):
    project = tmp_path / 'book'
    planner = seed_metadata_failure(project)
    before = {p: p.read_bytes() for p in project.rglob('*') if p.is_file()}
    calls = list(planner.client.calls)
    assert metadata_recovery_available(project, 'test-run', planner.progress)
    assert {p: p.read_bytes() for p in project.rglob('*') if p.is_file()} == before
    assert planner.client.calls == calls


@pytest.mark.parametrize('problem', ['source', 'brief', 'plan_binding', 'prior_group_hash',
    'original_response_hash', 'format_response_hash', 'format_prompt', 'raw_response',
    'missing_original', 'missing_format', 'prior_metadata', 'candidate_saved', 'review_saved', 'unknown_later_group'])
def test_metadata_upgrade_requires_all_prior_bindings_and_no_spent_child(tmp_path, problem):
    project = tmp_path / 'book'
    planner = seed_metadata_failure(project)
    name = contract.VERSION + '-group-002'
    if problem in {'source', 'brief'}:
        path = project / 'outputs/input' / ('prompt.md' if problem == 'source' else 'brief.json')
        path.write_text('changed' if problem == 'source' else '{}')
    elif problem in {'plan_binding', 'prior_group_hash', 'original_response_hash', 'format_response_hash'}:
        filename = {'plan_binding': contract.VERSION + '-plan', 'prior_group_hash': contract.VERSION + '-group-001',
                    'original_response_hash': name + '-response',
                    'format_response_hash': name + '-format-correction-response'}[problem]
        path = planner.cache / f'{filename}.json'
        value = json.loads(path.read_text())
        value['dependencies_hash' if problem == 'plan_binding' else 'payload_hash'] = '0' * 64
        path.write_text(json.dumps(value))
    elif problem in {'format_prompt', 'raw_response'}:
        pattern = (name + '-format-correction.*.prompt.txt' if problem == 'format_prompt'
                   else name + '.*.response.txt')
        path = next((planner.cache / 'raw').glob(pattern))
        path.write_text(path.read_text() + ' changed')
    elif problem in {'missing_original', 'missing_format'}:
        suffix = '-response' if problem == 'missing_original' else '-format-correction-response'
        (planner.cache / f'{name}{suffix}.json').unlink()
    else:
        filename = {'prior_metadata': name + '-metadata-v1', 'candidate_saved': contract.VERSION + '-candidate',
                    'review_saved': contract.REVIEW_NAME, 'unknown_later_group': contract.VERSION + '-group-003-response'}[problem]
        (planner.cache / f'{filename}.json').write_text('{}')
    assert not metadata_recovery_available(project, 'test-run', planner.progress)


def test_api_projects_metadata_recovery_without_modifying_failed_records(tmp_path):
    from test_project_tasks import _client, _seed
    project, manifest, path = _seed(tmp_path, run_id='test-run')
    manifest.current_phase = 'outline'
    path.write_text(json.dumps(manifest.to_dict()))
    planner = seed_metadata_failure(project)
    runtime = path.parent / 'progress.json'
    runtime.write_text(json.dumps({'schema_version': 1, 'run_id': 'test-run', 'status': 'failed',
                                  'phase': 'outline', 'next_action': 'Old generic retry hint.'}))
    before = {p: p.read_bytes() for p in project.rglob('*') if p.is_file()}
    client = _client(tmp_path)
    detail = client.get('/api/projects/book/tasks/test-run').json()
    assert detail['can_resume'] is True
    assert detail['planning']['recovery']['action'] == 'repair_saved_metadata'
    assert '不重新生成章节' in detail['progress']['next_action']
    assert detail['progress']['next_action'] == detail['planning']['recovery']['reason']
    assert client.get('/api/projects/book').json()['task_can_resume'] is True
    assert all(p.read_bytes() == value for p, value in before.items())
    assert planner.progress['recovery']['retryable'] is False
