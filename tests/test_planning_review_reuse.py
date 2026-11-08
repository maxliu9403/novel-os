"""Exact passed review inputs can be reused; changed work must be reviewed anew."""
from copy import deepcopy
import json

import pytest

from core.planning_context import expand_review_context, review_context
from core.planning_review import WINDOW_RULES
from core.planning_review_reuse import passed_window, verification
from outline_planning import _REVIEW_PROMPT, content_hash
from test_outline_planning import PlanningModel, issue, make_planner, review


def candidate(planner):
    return {
        **deepcopy(planner.locked), 'title': 'Saved novel',
        'relationships': [{'history': 'Their costly trust remains fragile.'}],
        'chapter_map': [{'number': n, 'purpose': f'Preserve original choice {n}.'}
                        for n in range(1, planner.chapter_count + 1)],
        'chapters': [{'number': n, 'summary': f'Complete original chapter {n}.',
                      'scenes': [{'detail': f'Exact scene and emotional consequence {n}.'}]}
                     for n in range(1, planner.chapter_count + 1)],
    }


@pytest.fixture
def receipt(tmp_path):
    planner = make_planner(tmp_path, PlanningModel(), count=12)
    baseline = candidate(planner)
    context = {**planner._context(),
        'candidate': {**deepcopy(baseline), 'chapters': deepcopy(baseline['chapters'][:4])},
        'review_window': {'requested_chapters': [1, 2, 3], 'included_chapters': [1, 2, 3, 4],
                          'omitted_candidate_chapters': list(range(5, 13))}}
    instruction = _REVIEW_PROMPT.replace(
        'Review this exact whole-book candidate against the complete adopted source.',
        'Review the supplied candidate window against its declared source coverage.') + WINDOW_RULES
    planner._save('review-initial-window-001',
        {'candidate': content_hash(baseline), 'context': content_hash(context),
         'policy': content_hash(instruction)}, review())
    return planner, baseline, context, instruction


def test_exact_passed_receipt_reuses_without_model_calls_or_checkpoint_writes(receipt):
    planner, baseline, context, instruction = receipt
    before = {p: p.read_bytes() for p in planner.cache.iterdir()}
    result = passed_window(planner, baseline, 'review-initial', context, instruction)
    assert result == (review(), 'review-initial-window-001')
    assert planner.client.calls == []
    assert {p: p.read_bytes() for p in planner.cache.iterdir()} == before


@pytest.mark.parametrize('change', ['baseline', 'chapter', 'global', 'source', 'coverage', 'guidance', 'policy'])
def test_receipt_cannot_cover_changed_candidate_source_context_or_instructions(receipt, change):
    planner, baseline, context, instruction = receipt
    if change == 'baseline':
        # Even a change outside this supplied window identifies another original
        # candidate and cannot borrow the saved receipt's provenance.
        baseline['chapters'][-1]['summary'] = 'Another baseline entirely.'
    elif change == 'chapter':
        context['candidate']['chapters'][1]['summary'] = 'A changed local decision.'
    elif change == 'global':
        context['candidate']['relationships'][0]['history'] = 'Changed relationship history.'
    elif change == 'source':
        context['complete_adopted_source'] += ' New author requirement.'
    elif change == 'coverage':
        context['review_window']['requested_chapters'] = [2, 3]
    elif change == 'guidance':
        context['structured_brief']['author_requirement'] = 'Preserve this new constraint.'
    else:
        instruction += '\nA new review policy.'
    assert passed_window(planner, baseline, 'review-initial', context, instruction) is None
    assert planner.client.calls == []


def test_identical_blocked_receipt_cannot_be_reused_as_passing_evidence(receipt):
    planner, baseline, context, instruction = receipt
    path = planner.cache / 'review-initial-window-001.json'
    value = json.loads(path.read_text())
    value['payload'] = review(issue('chapter', 2))
    value['payload_hash'] = content_hash(value['payload'])
    path.write_text(json.dumps(value))
    assert passed_window(planner, baseline, 'review-initial', context, instruction) is None
    assert planner.client.calls == []


@pytest.mark.parametrize('problem', ['invalid_json', 'payload_hash', 'incomplete_coverage', 'unsupported_schema'])
def test_corrupt_matching_receipt_fails_before_any_fresh_model_call(receipt, problem):
    planner, baseline, context, instruction = receipt
    path = planner.cache / 'review-initial-window-001.json'
    value = json.loads(path.read_text())
    if problem == 'invalid_json':
        path.write_text('{')
    else:
        if problem == 'payload_hash':
            value['payload_hash'] = '0' * 64
        elif problem == 'incomplete_coverage':
            value['payload']['coverage']['source_fidelity'] = False
            value['payload_hash'] = content_hash(value['payload'])
        else:
            value['schema_version'] = 'unknown'
        path.write_text(json.dumps(value))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        passed_window(planner, baseline, 'review-initial', context, instruction)
    assert path.read_bytes() == before
    assert planner.client.calls == []


def test_receipt_from_another_generation_identity_is_not_reused(receipt):
    planner, baseline, context, instruction = receipt
    path = planner.cache / 'review-initial-window-001.json'
    value = json.loads(path.read_text())
    value['identity_hash'] = '0' * 64
    path.write_text(json.dumps(value))
    assert passed_window(planner, baseline, 'review-initial', context, instruction) is None
    assert planner.client.calls == []


def audit(baseline):
    return {'schema': 'coordinated-repair-v1', 'baseline_candidate_sha256': content_hash(baseline),
            'issues': [
                {'issue_index': 0, 'requested_chapters': [], 'dependency_chapters': [],
                 'classification': 'interpretation', 'original_issue': issue('global'),
                 'reason': 'An unscoped global interpretation must remain visible.',
                 'resolution': {'status': 'recheck'}},
                {'issue_index': 1, 'requested_chapters': [2], 'dependency_chapters': [1],
                 'classification': 'continuity_gap', 'original_issue': issue('chapter', 2),
                 'reason': 'The changed decision depends on chapter 1.',
                 'resolution': {'status': 'corrected'}},
                {'issue_index': 2, 'requested_chapters': [], 'dependency_chapters': [12],
                 'classification': 'interpretation', 'original_issue': issue('chapter', 12),
                 'reason': 'Recheck this finding against the unchanged final chapter.',
                 'resolution': {'status': 'recheck'}},
            ]}


def test_verification_keeps_every_original_finding_and_unscoped_global_in_all_windows():
    record = audit({'chapters': []})
    before = deepcopy(record)
    contexts = [{'review_window': {'included_chapters': ns}}
                for ns in ([1, 2, 3, 4], [3, 4, 5, 6, 7], [6, 7, 8, 9, 10], [9, 10, 11, 12])]
    expected = [{0, 1}, {0}, {0}, {0, 2}]
    covered = set()
    for context, indices in zip(contexts, expected, strict=True):
        original = deepcopy(context)
        result = verification(context, record)
        entries = result['coordinated_verification']['issues']
        actual = {item['issue_index'] for item in entries}
        assert actual == indices
        assert all(item == record['issues'][item['issue_index']] for item in entries)
        covered |= actual
        assert context == original
    assert covered == {0, 1, 2}
    assert verification({}, record)['coordinated_verification']['issues'] == record['issues']
    assert record == before


def test_audited_review_transport_roundtrips_full_verification_and_equal_contracts():
    source = 'The complete frozen source.'
    classification = {'complete_metadata': 'An exact original contract field. ' * 200}
    context = {'immutable_contracts': {'classification': classification},
               'review_window': {'included_chapters': [1, 2, 3]}}
    saved = {'classification': deepcopy(classification), 'chapters': [{'number': 2}]}
    original = {**verification(context, audit(saved)), 'candidate': saved}
    projected = review_context(verification(context, audit(saved)), saved, complete_source=source)
    assert 'review_representation' in projected
    assert expand_review_context(projected, complete_source=source) == original


def test_changed_chapter_is_freshly_reviewed_while_exact_unchanged_windows_reuse(tmp_path, monkeypatch):
    model = PlanningModel(reviews=[review(issue('chapter', 2)), review(), review(), review()])
    planner = make_planner(tmp_path, model, count=12)
    baseline = candidate(planner)
    monkeypatch.setattr(planner, '_review_fits',
        lambda instruction, context, **kwargs: 'review_window' in context
        and len(context['review_window']['requested_chapters']) <= 3)
    initial = planner._semantic_review(baseline, 'review-initial')
    assert initial['status'] == 'blocked'
    assert len(model.calls) == 4
    original_files = {p: p.read_bytes() for p in planner.cache.glob('review-initial*.json')}
    changed = deepcopy(baseline)
    changed['chapters'][1]['summary'] = 'The explicit repaired choice retains its original cost.'
    record = audit(baseline)
    record['issues'] = [record['issues'][1]]
    record['issues'][0]['issue_index'] = 0
    result = planner._semantic_review(changed, 'review-coordinated-repair-v1',
        repair_record=record, reuse_from='review-initial', baseline_candidate=baseline)
    assert result['status'] == 'passed'
    assert len(model.calls) == 5
    stage, fresh = model.calls[-1]
    assert stage == 'review'
    assert fresh['review_window']['requested_chapters'] == [1, 2, 3]
    assert fresh['candidate']['chapters'][1] == changed['chapters'][1]
    assert fresh['coordinated_verification']['issues'] == record['issues']
    assert 'review_representation' in fresh
    expanded = expand_review_context(fresh, complete_source=planner.source)
    assert expanded['coordinated_verification'] == fresh['coordinated_verification']
    assert expanded['candidate']['classification'] == baseline['classification']
    scope = result['review_scope']
    assert scope['complete_chapter_coverage'] == list(range(1, 13))
    assert [r['from_checkpoint'] for r in scope['reused_passed_windows']] == [
        'review-initial-window-002', 'review-initial-window-003', 'review-initial-window-004']
    assert all(p.read_bytes() == value for p, value in original_files.items())
    assert planner._semantic_review(changed, 'review-coordinated-repair-v1', repair_record=record,
        reuse_from='review-initial', baseline_candidate=baseline) == result
    assert len(model.calls) == 5


def test_changed_global_design_requires_all_windows_to_be_reviewed_again(tmp_path, monkeypatch):
    model = PlanningModel()
    planner = make_planner(tmp_path, model, count=12)
    baseline = candidate(planner)
    monkeypatch.setattr(planner, '_review_fits',
        lambda instruction, context, **kwargs: 'review_window' in context
        and len(context['review_window']['requested_chapters']) <= 3)
    planner._semantic_review(baseline, 'review-initial')
    assert len(model.calls) == 4
    changed = deepcopy(baseline)
    changed['chapter_map'][1]['purpose'] += ' Retain the required causal bridge.'
    result = planner._semantic_review(changed, 'review-coordinated-repair-v1',
        reuse_from='review-initial', baseline_candidate=baseline)
    assert result['status'] == 'passed'
    assert len(model.calls) == 8
    assert not result['review_scope'].get('reused_passed_windows')
    assert all(context['candidate']['chapter_map'] == changed['chapter_map'] for _, context in model.calls[4:])
