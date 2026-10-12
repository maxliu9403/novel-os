"""External source reviews allocate one immutable, source-bound correction."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from core.cover_analysis import CoverAnalysisError, load_analysis
from core.cover_analysis_reviews import start_review_revision, assert_analysis_usable
from core.cover_sources import canonical_hash, capture_source
from tests.test_cover_analysis_snapshot import ModelFixture, analyzer, source
from tests.test_cover_workflow import workflow, approve


def review_for(parent):
    payload = parent.to_dict()
    evidence = payload['evidence'][0]
    return {
        'schema_version': 'cover-external-source-review.v1', 'review_kind': 'agent',
        'reviewer_id': 'independent-fixture', 'analysis_id': parent.analysis_id,
        'analysis_sha256': parent.analysis_sha256, 'snapshot_id': parent.snapshot_id,
        'snapshot_sha256': parent.snapshot_sha256,
        'source_content_sha256': parent.source_content_sha256,
        'source_bundle_sha256': parent.source_bundle_sha256,
        'configuration_sha256': canonical_hash(parent.configuration),
        'synthesis_sha256': parent.global_checkpoint['synthesis_sha256'],
        'issues': [{'issue_id': 'location', 'problem': 'Wrong location in event',
                    'target_path': '/events/0/location', 'observed_value': 'apartment',
                    'evidence_refs': ['evidence:' + evidence['evidence_id']],
                    'exact_source_text': evidence['exact_quote'],
                    'suggested_correction': 'Use apartment entry.'}],
    }


def correcting_model():
    model = ModelFixture()
    def mutate(request, result):
        if request.get('semantic_revision'):
            result['events'][0]['location'] = 'apartment entry'
        return result
    model.mutate = mutate
    return model


def start(context, parent, report=None, model=None):
    return start_review_revision(context, parent_analysis_id=parent.analysis_id,
        expected_analysis_sha256=parent.analysis_sha256,
        external_review=report or review_for(parent), analyzer=analyzer(model or correcting_model()))


def test_correction_reuses_extraction_preserves_parent_and_is_idempotent(source):
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    folder = context.cover_root / 'analyses' / parent.analysis_id
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    model = correcting_model()
    child = start(context, parent, model=model)
    assert child.analysis_id != parent.analysis_id
    assert child.coverage_status == 'complete'
    assert child.events[0]['location'] == 'apartment entry'
    assert child.segment_results == parent.segment_results
    assert not any(c['phase'] in {'segment', 'aggregate'} for c in model.calls)
    assert sum(c['phase'] == 'global' for c in model.calls) == 1
    assert any(c.get('mode') == 'source_segment' for c in model.calls)
    assert any(c.get('mode') == 'global_cross_segment' for c in model.calls)
    assert all(a['attempt_id'] not in {a['attempt_id'] for a in parent.attempts} for a in child.attempts)
    assert child.semantic_revision['verification_feedback'][0]['review_kind'] == 'agent'
    assert 'attempt_id' not in child.semantic_revision['verification_feedback'][0]
    calls = len(model.calls)
    assert start(context, parent, model=model).analysis_id == child.analysis_id
    assert len(model.calls) == calls
    assert before == {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    with pytest.raises(CoverAnalysisError, match='challenged'):
        assert_analysis_usable(context, parent.analysis_id)
    assert_analysis_usable(context, child.analysis_id)


def test_unchanged_challenged_fact_cannot_complete_even_when_provider_passes(source):
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    with pytest.raises(CoverAnalysisError) as error:
        start(context, parent, model=ModelFixture())
    assert error.value.code == 'analysis_review_unresolved'
    child = load_analysis(context, error.value.analysis_id)
    assert child.coverage_status != 'complete'
    assert child.semantic_revision['round'] == 1


@pytest.mark.parametrize('field', ['analysis_id', 'analysis_sha256', 'snapshot_id', 'snapshot_sha256',
    'source_content_sha256', 'source_bundle_sha256', 'configuration_sha256', 'synthesis_sha256'])
def test_review_cannot_cross_any_parent_binding(source, field):
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    report = review_for(parent)
    report[field] = 'different'
    with pytest.raises(CoverAnalysisError):
        start(context, parent, report)
    assert not list((context.cover_root / 'analysis-reviews').glob('*.json'))
    assert_analysis_usable(context, parent.analysis_id)


@pytest.mark.parametrize('change', [
    {'observed_value': 'apartment entry'}, {'target_path': '/events/99/location'},
    {'target_path': '/events/00/location'}, {'target_path': '/events/~2/location'},
    {'evidence_refs': ['evidence:missing']}, {'exact_source_text': 'invented quotation'},
    {'evidence_refs': []}, {'extra': 'not permitted'}, {'suggested_correction': ''},
])
def test_review_issue_contract_fails_before_any_allocation(source, change):
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    report = review_for(parent)
    report['issues'][0].update(change)
    with pytest.raises(CoverAnalysisError):
        start(context, parent, report)
    assert not list((context.cover_root / 'analysis-reviews').glob('*.json'))


@pytest.mark.parametrize('field,value', [('review_kind', 'human'), ('review_kind', 'provider'),
    ('reviewer_id', ''), ('schema_version', 'wrong'), ('extra', True)])
def test_review_identity_is_strictly_agent_attributed(source, field, value):
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    report = review_for(parent)
    report[field] = value
    with pytest.raises(CoverAnalysisError):
        start(context, parent, report)


def test_configuration_or_expected_revision_change_never_allocates(source):
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    with pytest.raises(CoverAnalysisError, match='unchanged complete'):
        start_review_revision(context, parent_analysis_id=parent.analysis_id,
            expected_analysis_sha256='0' * 64, external_review=review_for(parent), analyzer=analyzer(ModelFixture()))
    with pytest.raises(CoverAnalysisError, match='exact parent configuration'):
        start_review_revision(context, parent_analysis_id=parent.analysis_id,
            expected_analysis_sha256=parent.analysis_sha256, external_review=review_for(parent),
            analyzer=analyzer(ModelFixture(), max_segment_bytes=12000))
    assert not list((context.cover_root / 'analysis-reviews').glob('*.json'))


def test_crash_after_ledger_is_fail_closed_and_same_request_resumes(source, monkeypatch):
    from core import cover_analysis_reviews as module
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    seed = module._seed_child
    def crash(*args):
        raise RuntimeError('simulated interruption before child allocation')
    monkeypatch.setattr(module, '_seed_child', crash)
    with pytest.raises(RuntimeError):
        start(context, parent)
    with pytest.raises(CoverAnalysisError, match='challenged'):
        analyzer(ModelFixture()).analyze(context, snapshot, analysis_id=parent.analysis_id)
    report = review_for(parent)
    report['issues'][0]['problem'] += ' changed'
    with pytest.raises(CoverAnalysisError, match='different review'):
        start(context, parent, report)
    monkeypatch.setattr(module, '_seed_child', seed)
    assert start(context, parent).coverage_status == 'complete'


def test_crash_after_reserve_reuses_the_empty_child(source, monkeypatch):
    from core import cover_analysis_reviews as module
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    save = module._save
    def crash(*args):
        raise RuntimeError('simulated interruption before child seed write')
    monkeypatch.setattr(module, '_save', crash)
    with pytest.raises(RuntimeError):
        start(context, parent)
    ledger = json.loads((context.cover_root / 'analysis-reviews' / (parent.analysis_id + '.json')).read_text())
    assert load_analysis(context, ledger['child_analysis_id']).coverage_status == 'pending'
    monkeypatch.setattr(module, '_save', save)
    assert start(context, parent).analysis_id == ledger['child_analysis_id']


def test_concurrent_identical_reviews_share_one_child_and_one_provider_revision(source):
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    model = correcting_model()
    barrier = threading.Barrier(2)
    def run():
        barrier.wait(timeout=5)
        return start(context, parent, model=model)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert results[0].analysis_id == results[1].analysis_id
    assert sum(c['phase'] == 'global' for c in model.calls) == 1


def test_concurrent_different_reviews_only_allocate_one_child(source):
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    barrier = threading.Barrier(2)
    def run(index):
        report = review_for(parent)
        report['reviewer_id'] += str(index)
        barrier.wait(timeout=5)
        try:
            return start(context, parent, report)
        except CoverAnalysisError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert sum(isinstance(result, CoverAnalysisError) for result in results) == 1
    assert [result.code for result in results if isinstance(result, CoverAnalysisError)] == ['analysis_conflict']


def test_approved_parent_and_preexisting_cache_clone_are_blocked(workflow):
    flow, parent, director, _ = workflow
    direction = approve(flow, flow.plan(parent.analysis_id, director))
    clone_snapshot = capture_source(flow.context, load_snapshot_selector(flow.context, parent))
    clone = analyzer(director._complete).analyze(flow.context, clone_snapshot)
    assert clone.reused_from_analysis_id == parent.analysis_id
    report = review_for(parent)
    report['issues'][0]['observed_value'] = parent.events[0]['location']
    model = correcting_model()
    original = model.mutate
    def mutate(request, result):
        result = original(request, result)
        if request.get('semantic_revision'):
            result['events'][0]['event_id'] = 'event-0'
            result['events'][0]['location'] = 'doorway'
        return result
    model.mutate = mutate
    child = start(flow.context, parent, report, model)
    for analysis_id in (parent.analysis_id, clone.analysis_id):
        with pytest.raises(CoverAnalysisError, match='challenged'):
            flow.plan(analysis_id, director)
    with pytest.raises(CoverAnalysisError, match='challenged'):
        flow.generation_arguments(direction.direction_id, direction.direction_sha256, reserve=False)
    assert_analysis_usable(flow.context, child.analysis_id)


def load_snapshot_selector(context, analysis):
    from core.cover_sources import load_snapshot
    return load_snapshot(context, analysis.snapshot_id).selector


def test_challenged_cache_never_projects_parent_onto_a_new_snapshot(source, monkeypatch):
    from core import cover_analysis_reviews as module
    context, snapshot, _ = source
    model = ModelFixture()
    parent = analyzer(model).analyze(context, snapshot)
    def crash(*args):
        raise RuntimeError('stop after review registration')
    monkeypatch.setattr(module, '_seed_child', crash)
    with pytest.raises(RuntimeError):
        start(context, parent)
    before = len(model.calls)
    new_snapshot = capture_source(context, snapshot.selector)
    result = analyzer(model).analyze(context, new_snapshot)
    assert len(model.calls) > before
    assert not result.to_dict().get('reused_from_analysis_id')


def test_spent_correction_cannot_be_reset_through_new_review_or_cache_clone(source):
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    child = start(context, parent)
    clone = analyzer(ModelFixture()).analyze(context, capture_source(context, snapshot.selector))
    assert clone.reused_from_analysis_id == child.analysis_id
    for previous in (child, clone):
        report = review_for(previous)
        report['issues'][0]['observed_value'] = 'apartment entry'
        with pytest.raises(CoverAnalysisError) as error:
            start(context, previous, report)
        assert error.value.code == 'analysis_semantic_repair_exhausted'


@pytest.mark.parametrize('artifact', ['ledger', 'marker', 'feedback', 'evidence', 'null_marker', 'missing_marker'])
def test_child_and_review_tampering_fails_closed(source, artifact):
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    child = start(context, parent)
    if artifact == 'ledger':
        path = context.cover_root / 'analysis-reviews' / (parent.analysis_id + '.json')
        payload = json.loads(path.read_text())
        payload['external_review']['reviewer_id'] = 'tampered'
    else:
        path = context.cover_root / 'analyses' / child.analysis_id / 'analysis.json'
        payload = json.loads(path.read_text())
        if artifact == 'marker':
            payload['external_review_revision']['review_sha256'] = '0' * 64
        elif artifact == 'feedback':
            payload['semantic_revision']['verification_feedback'][0]['review_kind'] = 'provider'
        elif artifact == 'null_marker':
            payload['external_review_revision'] = None
        elif artifact == 'missing_marker':
            payload.pop('external_review_revision')
        else:
            payload['evidence'][0]['statement'] += ' counterfeit'
        payload['analysis_sha256'] = canonical_hash({k: v for k, v in payload.items() if k != 'analysis_sha256'})
    path.write_text(json.dumps(payload))
    with pytest.raises(CoverAnalysisError):
        load_analysis(context, child.analysis_id)


def test_pending_child_cannot_bypass_registration_by_direct_analyze(source, monkeypatch):
    from core import cover_analysis_reviews as module
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    def crash(*args):
        raise RuntimeError('interrupted seed')
    monkeypatch.setattr(module, '_save', crash)
    with pytest.raises(RuntimeError):
        start(context, parent)
    ledger = json.loads((context.cover_root / 'analysis-reviews' / (parent.analysis_id + '.json')).read_text())
    model = ModelFixture()
    with pytest.raises(CoverAnalysisError, match='missing its provenance'):
        analyzer(model).analyze(context, snapshot, analysis_id=ledger['child_analysis_id'])
    assert model.calls == []


def test_source_rejection_after_correction_exhausts_the_single_round(source):
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    model = correcting_model()
    correct = model.mutate
    def reject(request, result):
        result = correct(request, result)
        if request.get('mode') == 'source_segment':
            document = request['documents'][0]
            evidence = next(row for row in parent.evidence if row['segment_id'] == document['segment_id'])
            result['checks'][0]['supported'] = False
            return {**result, 'status': 'fail', 'issues': [{
                'segment_id': document['segment_id'], 'problem': 'Another source contradiction remains',
                'exact_source_text': evidence['exact_quote'],
                'evidence_refs': ['evidence:' + evidence['evidence_id']]}]}
        return result
    model.mutate = reject
    with pytest.raises(CoverAnalysisError) as error:
        start(context, parent, model=model)
    assert error.value.code == 'analysis_semantic_repair_exhausted'
    child = load_analysis(context, error.value.analysis_id)
    assert child.semantic_revision['status'] == 'exhausted'
    calls = len(model.calls)
    with pytest.raises(CoverAnalysisError) as second:
        start(context, parent, model=model)
    assert second.value.code == 'analysis_semantic_repair_exhausted'
    assert len(model.calls) == calls


def test_stable_event_id_catches_reordered_unchanged_error(source):
    context, snapshot, _ = source
    parent = analyzer(ModelFixture()).analyze(context, snapshot)
    model = ModelFixture()
    def reorder(request, result):
        if request.get('semantic_revision'):
            result['events'].insert(0, {**result['events'][0], 'event_id': 'other', 'location': 'doorway'})
        return result
    model.mutate = reorder
    with pytest.raises(CoverAnalysisError) as error:
        start(context, parent, model=model)
    assert error.value.code == 'analysis_review_unresolved'


def test_directed_relationship_identity_does_not_confuse_same_actor_recipients():
    from core.cover_analysis_reviews import _assert_targets_changed
    previous = {'brief_payload': {'relationship_map': [
        {'from_character_id': 'richard', 'to_character_id': 'julianne', 'power_balance': 'father figure'},
        {'from_character_id': 'richard', 'to_character_id': 'leo', 'power_balance': 'father figure'}]}}
    report = {'issues': [{'issue_id': 'julianne', 'target_path': '/brief_payload/relationship_map/0/power_balance',
                          'observed_value': 'father figure'}]}
    corrected = deepcopy(previous)
    corrected['brief_payload']['relationship_map'][0]['power_balance'] = 'romantic partner'
    corrected['brief_payload']['relationship_map'].reverse()
    _assert_targets_changed(report, previous, corrected, 'analysis-fixture')
    with pytest.raises(CoverAnalysisError):
        _assert_targets_changed(report, previous, previous, 'analysis-fixture')
