"""Source-bound reference repairs must preserve whole-book work and review it."""
import json
import pytest

from outline_planning import OutlinePlanningError
from test_outline_planning import PlanningModel, make_planner, review
from core import planning_correction

SOURCE = ('Mara refuses the sale. The crisis ends in Chapter 9. '
          'Chapter 11 resolves her independent work without moving the crisis.')
ISSUE = {'scope': 'global', 'message': 'The climax reference says 11; the source places it in 9.',
         'source_evidence': 'The crisis ends in Chapter 9.'}


class ReferenceModel(PlanningModel):
    def __init__(self, *, correction=None, fail_review=False, reviews=None):
        super().__init__(reviews=reviews or [review(ISSUE), review()])
        self.correction = correction or {'status': 'corrected', 'reason': 'Restore the adopted source timing.', 'edits': [
            {'path': '/volume_contracts/0/climax_chapter', 'before': 11, 'after': 9,
             'source_quote': 'The crisis ends in Chapter 9.', 'issue_indices': [0]}]}
        self.fail_review = fail_review

    def complete(self, system, user):
        if user.startswith('STAGE: global_reference_repair'):
            context = json.loads(user.rsplit('\n\n', 1)[1])
            self.calls.append(('global_repair', context))
            return json.dumps(self.correction)
        if user.startswith('STAGE: semantic_review') and self.fail_review and any(s == 'global_repair' for s, _ in self.calls):
            self.calls.append(('review_timeout', {}))
            raise TimeoutError('review interrupted')
        return super().complete(system, user)


def test_global_reference_repair_preserves_chapters_and_rechecks_exact_candidate(tmp_path):
    model = ReferenceModel()
    planner = make_planner(tmp_path, model, count=12, source=SOURCE)
    result = planner.run()
    assert [s for s, _ in model.calls][-3:] == ['review', 'global_repair', 'review']
    original = json.loads((planner.cache / 'global.json').read_text())['payload']
    assert original['volume_contracts'][0]['climax_chapter'] == 11
    assert result['volume_contracts'][0]['climax_chapter'] == 9
    assert next(o for o in result['volume_contracts'][0]['obligations'] if o['kind'] == 'climax')['due_chapter'] == 9
    saved_chapters = sum([json.loads((planner.cache / f'batch-{i:03}.json').read_text())['payload']['chapters'] for i in range(1, 4)], [])
    # The established foundation parser adds publication positioning metadata.
    assert all(all(final.get(k) == v for k, v in saved.items()) for final, saved in zip(result['chapters'], saved_chapters, strict=True))
    assert model.calls[-1][1]['candidate']['chapters'] == saved_chapters
    assert model.calls[-1][1]['candidate']['volume_contracts'][0]['climax_chapter'] == 9
    resumed = ReferenceModel()
    assert make_planner(tmp_path, resumed, count=12, source=SOURCE).run() == result
    assert resumed.calls == []


def test_review_timeout_reuses_saved_correction_without_regenerating(tmp_path):
    first = ReferenceModel(fail_review=True)
    with pytest.raises(TimeoutError):
        make_planner(tmp_path, first, count=12, source=SOURCE).run()
    resumed = ReferenceModel(reviews=[review()])
    result = make_planner(tmp_path, resumed, count=12, source=SOURCE).run()
    assert result['volume_contracts'][0]['climax_chapter'] == 9
    assert [s for s, _ in resumed.calls] == ['review']


def test_rejected_final_review_never_repeats_paid_correction_on_resume(tmp_path):
    first = ReferenceModel(reviews=[review(ISSUE), review(ISSUE)])
    with pytest.raises(OutlinePlanningError, match='still needs attention'):
        make_planner(tmp_path, first, count=12, source=SOURCE).run()
    resumed = ReferenceModel()
    with pytest.raises(OutlinePlanningError, match='still needs attention'):
        make_planner(tmp_path, resumed, count=12, source=SOURCE).run()
    assert resumed.calls == []
    progress = json.loads((tmp_path / 'planning/progress.json').read_text())
    assert progress['recovery']['retryable'] is False
    assert '不会' in progress['next_action']


@pytest.mark.parametrize('change', [
    {'path': '/chapters/0/number'}, {'path': '/volume_contracts/0/chapter_end'},
    {'path': '/characters/0/id'}, {'before': 10}, {'after': True}, {'after': 99},
    {'source_quote': 'A claim that does not exist in the adopted source.'}, {'issue_indices': [4]},
])
def test_unsupported_or_unbound_patch_is_never_published(tmp_path, change):
    model = ReferenceModel()
    model.correction['edits'][0].update(change)
    planner = make_planner(tmp_path, model, count=12, source=SOURCE)
    with pytest.raises(OutlinePlanningError):
        planner.run()
    assert not (planner.cache / 'validated.json').exists()
    assert not (planner.cache / f'{planning_correction.REVIEW_NAME}.json').exists()


def test_story_rewrite_is_explicitly_left_for_author(tmp_path):
    model = ReferenceModel(correction={'status': 'requires_author', 'reason': 'Changing the ending needs an author choice.', 'edits': []})
    planner = make_planner(tmp_path, model, count=12, source=SOURCE)
    with pytest.raises(OutlinePlanningError, match='Global design conflicts'):
        planner.run()
    assert planner.progress['recovery']['retryable'] is False
    assert [s for s, _ in model.calls][-1] == 'global_repair'


def test_source_omissions_and_reference_correct_together_without_replanning(tmp_path):
    facts = {
        '/reader_profile': 'English for adult women in the United States.',
        '/story_lead_plan': 'Before chapter 1, a separate 120–180 word Story Lead with no ending spoilers.',
        '/characters/0/background': 'Mara earns a partial income from instrument repair.',
    }
    source = SOURCE + ' ' + ' '.join(facts.values())
    issues = [ISSUE] + [{'scope': 'global', 'message': f'Missing {path}', 'source_evidence': fact}
                        for path, fact in facts.items()]
    model = ReferenceModel(reviews=[review(*issues), review()])
    model.correction['edits'] += [
        {'path': path, 'before': None, 'after': fact, 'source_quote': fact, 'issue_indices': [i]}
        for i, (path, fact) in enumerate(facts.items(), 1)]
    planner = make_planner(tmp_path, model, count=12, source=source)
    result = planner.run()
    repair_context = next(c for stage, c in model.calls if stage == 'global_repair')
    assert set(facts) <= set(repair_context['allowed_source_fields'])
    original = repair_context['candidate']
    assert model.calls[-1][1]['candidate']['chapters'] == original['chapters']
    for key in ('ending_contract', 'classification', 'narrative_format'):
        assert result[key] == original[key]
    assert result['characters'][0] == {**original['characters'][0], 'background': facts['/characters/0/background']}
    assert result['reader_profile'] == facts['/reader_profile']
    assert result['story_lead_plan'] == facts['/story_lead_plan']
    assert next(o for o in result['volume_contracts'][0]['obligations'] if o['kind'] == 'climax')['due_chapter'] == 9
    resumed = ReferenceModel()
    assert make_planner(tmp_path, resumed, count=12, source=source).run() == result
    assert resumed.calls == []


@pytest.mark.parametrize('change', [
    {'after': 'Replaced the saved background.'}, {'before': None}, {'after': {}},
    {'path': '/characters/0/arc'}, {'path': '/characters/99/background'},
    {'path': '/classification/reader_profile'}, {'path': '/story_lead_contract'},
    {'source_quote': 'This quote is invented.'}, {'issue_indices': []},
])
def test_additive_restoration_rejects_rewrites_and_unbound_facts(change):
    original = {'characters': [{'id': 'mara', 'background': 'Saved background.'}]}
    edit = {'path': '/characters/0/background', 'before': 'Saved background.',
            'after': 'Saved background.\n' + SOURCE, 'source_quote': SOURCE, 'issue_indices': [0]}
    edit.update(change)
    with pytest.raises(ValueError):
        planning_correction.apply(original, review(ISSUE), SOURCE,
            {'status': 'corrected', 'reason': 'Restore an omitted fact.', 'edits': [edit]}, 12)
    assert original['characters'][0]['background'] == 'Saved background.'


@pytest.mark.parametrize('final_blocked', [False, True])
def test_v1_corrected_checkpoints_retain_their_review_and_attempt_budget(tmp_path, final_blocked):
    from outline_planning import content_hash
    model = ReferenceModel(reviews=[review(ISSUE), review(ISSUE) if final_blocked else review()])
    planner = make_planner(tmp_path, model, count=12, source=SOURCE)
    if final_blocked:
        with pytest.raises(OutlinePlanningError):
            planner.run()
    else:
        planner.run()
    record = json.loads((planner.cache / f'{planning_correction.VERSION}.json').read_text())
    context = next(c for stage, c in model.calls if stage == 'global_repair')
    record['dependencies_hash'] = content_hash({'candidate': content_hash(context['candidate']),
        'review': content_hash(context['review']), 'policy': planning_correction.LEGACY_POLICY_HASH})
    (planner.cache / 'global-reference-repair-v1.json').write_text(json.dumps(record))
    (planner.cache / f'{planning_correction.REVIEW_NAME}.json').rename(planner.cache / 'review-global-reference-v1.json')
    for path in planner.cache.glob(f'{planning_correction.VERSION}*.json'):
        path.unlink()
    resumed = ReferenceModel()
    rerun = make_planner(tmp_path, resumed, count=12, source=SOURCE)
    if final_blocked:
        with pytest.raises(OutlinePlanningError, match='still needs attention'):
            rerun.run()
    else:
        assert rerun.run()['volume_contracts'][0]['climax_chapter'] == 9
    assert resumed.calls == []
