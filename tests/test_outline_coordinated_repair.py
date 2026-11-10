"""Fake-model end-to-end checks for source-bound mixed-scope repair recovery."""
import copy
import json

import pytest

from core import planning_evidence_plan as evidence
from core.planning_roadmap import MAX_RANGE
from outline_planning import OutlinePlanningError, content_hash
from test_outline_planning import PlanningModel, SOURCE, make_planner, review

QUOTE = 'Mara refuses the sale'
GLOBAL_ISSUE = {'scope': 'global', 'message': 'Preserve the refusal across the chapter transition.',
                'source_evidence': QUOTE}
CHAPTER_ISSUE = {'scope': 'chapter', 'chapter': 3, 'message': 'Make the prior refusal causally explicit.',
                 'source_evidence': QUOTE}
MIXED = review(GLOBAL_ISSUE, CHAPTER_ISSUE)


class CoordinatedModel(PlanningModel):
    def __init__(self, *, plan=None, reviews=None, fail_stage=None, fail_group=None,
                 map_edit=False, invalid_replacement=False, mutate_global=None):
        super().__init__(reviews=reviews or [copy.deepcopy(MIXED), review()], mutate_global=mutate_global)
        self.plan = plan
        self.fail_stage = fail_stage
        self.fail_group = fail_group
        self.map_edit = map_edit
        self.invalid_replacement = invalid_replacement
        self.group_count = 0

    def complete(self, system, user):
        context = json.loads(user.rsplit('\n\n', 1)[1])
        if user.startswith('STAGE: coordinated_source_adjudication'):
            self.calls.append(('source_adjudication', context))
            return json.dumps({'verdict': 'compatible', 'reason': 'A single source statement does not establish a conflict.',
                               'source_quotes': [QUOTE], 'writable_chapters': [], 'conflict_basis': None})
        if user.startswith('STAGE: coordinated_repair_plan'):
            self.calls.append(('coordinated_plan', context))
            if self.fail_stage == 'plan':
                raise TimeoutError('dependency plan interrupted')
            value = self.plan or {'issues': [{
                'issue_index': index, 'classification': 'continuity_gap',
                'reason': 'The source refusal must cause the existing chapter transition.',
                'requested_chapters': [3], 'dependency_chapters': [2, 4], 'source_quotes': [QUOTE],
            } for index in range(len(context['review']['issues']))]}
            return json.dumps(value)
        if user.startswith('STAGE: coordinated_repair'):
            self.calls.append(('coordinated_group', context))
            self.group_count += 1
            if self.fail_stage == 'group' or self.group_count == self.fail_group:
                raise TimeoutError('coordinated group interrupted')
            numbers = context['requested_chapters']
            dispositions = [{'issue_index': index, 'status': 'corrected',
                'reason': 'Preserve the source-backed refusal and its consequence.',
                'source_quotes': [QUOTE], 'chapter_numbers': context['coordinated_issue_plan']['issues'][index]['requested_chapters']}
                for index in range(len(context['review']['issues']))]
            replacements = []
            for number in numbers:
                before = next(ch for ch in context['candidate']['chapters'] if ch['number'] == number)
                after = copy.deepcopy(before)
                after['incoming_consequence'] = 'Mara has refused the sale, so she must rebuild the studio.'
                if self.invalid_replacement:
                    del after['bespoke_detail']
                replacements.append({'number': number,
                    'before_sha256': context['candidate_chapter_hashes'][str(number)], 'after': after,
                    'issue_indices': [i for i, d in enumerate(dispositions) if number in d['chapter_numbers']], 'source_quote': QUOTE})
            edits = []
            if self.map_edit:
                path, before = next((path, before) for path, before in context['allowed_global_fields'].items()
                                    if path.endswith('/incoming_consequence'))
                edits.append({'path': path, 'before': before,
                    'after': 'Refusing the sale leaves Mara responsible for the rebuilding cost.',
                    'issue_indices': list(range(len(dispositions))), 'source_quote': QUOTE})
            if context.get('wire_protocol'):
                issue_ids = [item['issue_id'] for item in context['review']['issues']]
                catalog = context['source_evidence_catalog']
                evidence_by_quote = {entry['quote']: entry['id'] for entry in catalog['entries']}
                for item in dispositions:
                    item['issue_id'] = issue_ids[item.pop('issue_index')]
                    item['source_evidence_ids'] = [evidence_by_quote[quote] for quote in item.pop('source_quotes')]
                for item in replacements + edits:
                    item['issue_ids'] = [issue_ids[index] for index in item.pop('issue_indices')]
                    item['source_evidence_id'] = evidence_by_quote[item.pop('source_quote')]
            return json.dumps({'status': 'corrected', 'reason': 'Restore the source consequence.',
                'issues': dispositions, 'chapter_replacements': replacements, 'global_edits': edits})
        if (user.startswith('STAGE: semantic_review') and 'coordinated_verification' in context
                and self.fail_stage == 'verification'):
            self.calls.append(('verification_timeout', context))
            raise TimeoutError('coordinated verification interrupted')
        return super().complete(system, user)


def saved_chapters(planner):
    return [chapter for path in sorted(planner.cache.glob('batch-*.json'))
            if '-response' not in path.name and '-format-correction' not in path.name
            for chapter in json.loads(path.read_text())['payload']['chapters']]


def original_checkpoints(planner):
    paths = [planner.cache / 'global.json', planner.cache / 'review-initial.json']
    paths.extend(planner.cache.glob('global-seed-*.json'))
    paths.extend(planner.cache.glob('roadmap-v1-*.json'))
    paths.extend(path for path in planner.cache.glob('batch-*.json')
                 if '-response' not in path.name and '-format-correction' not in path.name)
    return {path.name: path.read_bytes() for path in paths}


def assert_originals_retained(planner, baseline):
    assert {name: (planner.cache / name).read_bytes() for name in baseline} == baseline


@pytest.mark.parametrize(('count', 'global_stage'), [(8, 'global'), (60, 'seed')])
def test_mixed_scope_repairs_saved_chapter_and_reviews_complete_candidate_without_regeneration(tmp_path, count, global_stage):
    first = CoordinatedModel(map_edit=True)
    planner = make_planner(tmp_path, first, count=count)
    result = planner.run()
    stages = [stage for stage, _ in first.calls]
    assert stages[-4:] == ['review', 'coordinated_plan', 'coordinated_group', 'review']
    ranges = [ctx['requested_chapters'] for stage, ctx in first.calls if stage == 'roadmap']
    if global_stage == 'seed':
        assert [n for numbers in ranges for n in numbers] == list(range(1, count + 1))
        assert all(1 <= len(numbers) <= MAX_RANGE for numbers in ranges)
    else:
        assert ranges == []
    assert stages[:-4] == [global_stage] + ['roadmap'] * len(ranges) + ['batch'] * len(planner.ranges)
    source_chapters = saved_chapters(planner)
    assert len(source_chapters) == len(result['chapters']) == count
    for number, (saved, final) in enumerate(zip(source_chapters, result['chapters'], strict=True), 1):
        if number == 3:
            assert final['incoming_consequence'] != saved['incoming_consequence']
            assert final['bespoke_detail'] == saved['bespoke_detail']
        else:
            assert all(final.get(key) == value for key, value in saved.items())
    assert json.loads((planner.cache / 'global.json').read_text())['payload']['chapter_map'][2]['incoming_consequence'] == 'Prior cost'
    assert result['chapter_map'][2]['incoming_consequence'].startswith('Refusing the sale')
    plan_context = next(ctx for stage, ctx in first.calls if stage == 'coordinated_plan')
    assert plan_context['complete_adopted_source'] == SOURCE
    assert plan_context['candidate']['chapters'] == []
    assert 'chapter_map' not in plan_context['candidate']
    group = next(ctx for stage, ctx in first.calls if stage == 'coordinated_group')
    assert group['candidate_chapter_hashes']['3'] == content_hash(source_chapters[2])
    assert [{key: value for key, value in item.items() if key != 'issue_id'}
            for item in group['review']['issues']] == MIXED['issues']
    assert group['repair_scope']['readonly_chapters'] == [2, 4]
    assert group['adopted_source_projection']['text'] == SOURCE
    final_review = first.calls[-1][1]
    assert len(final_review['candidate']['chapters']) == count
    assert len(final_review['candidate']['chapter_map']) == count
    assert [item['original_issue'] for item in final_review['coordinated_verification']['issues']] == MIXED['issues']
    assert final_review['candidate']['chapters'][2]['incoming_consequence'] == result['chapters'][2]['incoming_consequence']
    baseline = original_checkpoints(planner)
    resumed = CoordinatedModel()
    assert make_planner(tmp_path, resumed, count=count).run() == result
    assert resumed.calls == []
    assert_originals_retained(planner, baseline)


def test_failed_complete_repair_response_is_preserved_and_never_regenerated_on_resume(tmp_path):
    first = CoordinatedModel(invalid_replacement=True)
    planner = make_planner(tmp_path, first, count=8)
    with pytest.raises(OutlinePlanningError, match='bounded format correction'):
        planner.run()
    assert [stage for stage, _ in first.calls][-3:] == ['coordinated_plan', 'coordinated_group', 'format']
    assert not (planner.cache / 'validated.json').exists()
    assert not (planner.cache / f'review-{evidence.VERSION}.json').exists()
    baseline = original_checkpoints(planner)
    resumed = CoordinatedModel()
    with pytest.raises(OutlinePlanningError, match='bounded format correction'):
        make_planner(tmp_path, resumed, count=8).run()
    assert resumed.calls == []
    assert_originals_retained(planner, baseline)


def test_blocked_postrepair_review_never_repeats_plan_or_repair_on_resume(tmp_path):
    first = CoordinatedModel(reviews=[copy.deepcopy(MIXED), copy.deepcopy(MIXED)])
    planner = make_planner(tmp_path, first, count=8)
    with pytest.raises(OutlinePlanningError, match='still needs attention'):
        planner.run()
    baseline = original_checkpoints(planner)
    resumed = CoordinatedModel()
    with pytest.raises(OutlinePlanningError, match='still needs attention'):
        make_planner(tmp_path, resumed, count=8).run()
    assert resumed.calls == []
    assert_originals_retained(planner, baseline)
    assert planner.progress['recovery']['retryable'] is False
    assert not (planner.cache / 'validated.json').exists()


@pytest.mark.parametrize('failed_stage,expected', [
    ('plan', ['coordinated_plan', 'coordinated_group', 'review']),
    ('group', ['coordinated_group', 'review']), ('verification', ['review']),
])
def test_interruption_resumes_unfinished_stage_and_reuses_saved_plan_and_group(tmp_path, failed_stage, expected):
    first = CoordinatedModel(fail_stage=failed_stage)
    planner = make_planner(tmp_path, first, count=8)
    with pytest.raises(TimeoutError):
        planner.run()
    baseline = original_checkpoints(planner)
    resumed = CoordinatedModel(reviews=[review()])
    result = make_planner(tmp_path, resumed, count=8).run()
    assert len(result['chapters']) == 8
    assert [stage for stage, _ in resumed.calls] == expected
    assert_originals_retained(planner, baseline)


def test_later_group_timeout_reuses_completed_group_and_all_original_batches(tmp_path):
    chapter_issue = {**CHAPTER_ISSUE, 'chapter': 6}
    findings = review(GLOBAL_ISSUE, chapter_issue)
    plan = {'issues': [{'issue_index': index, 'classification': 'continuity_gap',
        'reason': 'Make the original refusal explicit.', 'requested_chapters': [number],
        'dependency_chapters': [], 'source_quotes': [QUOTE]}
        for index, number in enumerate((3, 6))]}
    first = CoordinatedModel(plan=plan, fail_group=2, reviews=[findings, review()])
    planner = make_planner(tmp_path, first, count=8)
    with pytest.raises(TimeoutError):
        planner.run()
    baseline = original_checkpoints(planner)
    first_group = (planner.cache / f'{evidence.VERSION}-group-001.json').read_bytes()
    resumed = CoordinatedModel(reviews=[review()])
    result = make_planner(tmp_path, resumed, count=8).run()
    assert [stage for stage, _ in resumed.calls] == ['coordinated_group', 'review']
    assert resumed.calls[0][1]['requested_chapters'] == [6]
    assert all(result['chapters'][number - 1]['incoming_consequence'].startswith('Mara has refused') for number in (3, 6))
    assert (planner.cache / f'{evidence.VERSION}-group-001.json').read_bytes() == first_group
    assert_originals_retained(planner, baseline)


def test_disputed_findings_require_fresh_review_and_cannot_pass_through_classification(tmp_path):
    plan = {'issues': [{'issue_index': index, 'classification': 'interpretation',
        'reason': 'The original refusal may already establish this consequence.',
        'requested_chapters': [], 'dependency_chapters': [] if index == 0 else [3],
        'source_quotes': [QUOTE]} for index in range(2)]}
    first = CoordinatedModel(plan=plan, reviews=[copy.deepcopy(MIXED), copy.deepcopy(MIXED)])
    planner = make_planner(tmp_path, first, count=8)
    with pytest.raises(OutlinePlanningError, match='still needs attention'):
        planner.run()
    stages = [stage for stage, _ in first.calls]
    assert stages[-3:] == ['review', 'coordinated_plan', 'review']
    assert 'coordinated_group' not in stages
    assert first.calls[-1][1]['candidate']['chapters'] == saved_chapters(planner)
    assert all(item['resolution']['status'] == 'recheck'
               for item in first.calls[-1][1]['coordinated_verification']['issues'])
    assert not (planner.cache / 'validated.json').exists()
    resumed = CoordinatedModel()
    with pytest.raises(OutlinePlanningError, match='still needs attention'):
        make_planner(tmp_path, resumed, count=8).run()
    assert resumed.calls == []


def test_single_source_statement_does_not_block_for_an_author_decision(tmp_path):
    plan = {'issues': [{'issue_index': index, 'classification': 'source_conflict',
        'reason': 'A writer thinks a choice is required.', 'requested_chapters': [],
        'dependency_chapters': [], 'source_quotes': [QUOTE]} for index in range(2)]}
    first = CoordinatedModel(plan=plan)
    planner = make_planner(tmp_path, first, count=8)
    assert len(planner.run()['chapters']) == 8
    assert [stage for stage, _ in first.calls][-3:] == ['source_adjudication', 'source_adjudication', 'review']
    assert not list(planner.cache.glob('author-question-*.json'))


def test_original_unsorted_chapter_map_paths_remain_bound_to_the_correct_chapter(tmp_path):
    def reverse_map(value):
        value['chapter_map'].reverse()
    first = CoordinatedModel(map_edit=True, mutate_global=reverse_map)
    planner = make_planner(tmp_path, first, count=8)
    result = planner.run()
    original = json.loads((planner.cache / 'global.json').read_text())['payload']['chapter_map']
    assert [item['number'] for item in original] == list(range(8, 0, -1))
    group = next(ctx for stage, ctx in first.calls if stage == 'coordinated_group')
    assert '/chapter_map/5/incoming_consequence' in group['allowed_global_fields']
    for saved, changed in zip(original, result['chapter_map'], strict=True):
        assert saved['number'] == changed['number']
        if saved['number'] == 3:
            assert changed['incoming_consequence'].startswith('Refusing the sale')
        else:
            assert changed == saved
    assert first.calls[-1][1]['candidate']['chapter_map'] == result['chapter_map']


def test_adjacent_requested_chapters_share_one_group_without_stale_readonly_writes(tmp_path):
    findings = review(GLOBAL_ISSUE, {**CHAPTER_ISSUE, 'chapter': 4})
    plan = {'issues': [{'issue_index': index, 'classification': 'continuity_gap',
        'reason': 'The refusal must remain causal across the adjacent transition.',
        'requested_chapters': [number], 'dependency_chapters': [], 'source_quotes': [QUOTE]}
        for index, number in enumerate((3, 4))]}
    model = CoordinatedModel(plan=plan, reviews=[findings, review()])
    planner = make_planner(tmp_path, model, count=8)
    result = planner.run()
    groups = [context for stage, context in model.calls if stage == 'coordinated_group']
    assert len(groups) == 1
    group = groups[0]
    assert group['requested_chapters'] == [3, 4]
    assert group['repair_scope']['dependency_chapters'] == [2, 5]
    assert group['repair_scope']['readonly_chapters'] == [2, 5]
    assert group['repair_scope']['issue_ids'] == [item['issue_id'] for item in group['review']['issues']]
    assert len(set(group['repair_scope']['issue_ids'])) == 2
    assert [{key: value for key, value in item.items() if key != 'issue_id'}
            for item in group['review']['issues']] == findings['issues']
    assert not set(group['requested_chapters']) & set(group['repair_scope']['readonly_chapters'])
    for number in (3, 4):
        assert result['chapters'][number - 1]['incoming_consequence'].startswith('Mara has refused')
