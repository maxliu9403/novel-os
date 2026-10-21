"""Explicit reviewed source amendments do not replenish automatic repair budgets."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from core.cover_analysis import CoverAnalysisError, load_analysis, director_projection
from core.cover_analysis_corrections import prepare_source_amendment, source_amendment_recovery
from core.cover_sources import canonical_hash, capture_source
from tests.test_cover_analysis_snapshot import ModelFixture, analyzer, source


def failed_parent(source, *, policy=None):
    context, snapshot, _ = source
    model = ModelFixture()

    def reject(request, result):
        if request.get('mode') == 'source_segment':
            document = request['documents'][0]
            refs = result['checks'][0]['evidence_refs']
            result['checks'][0]['supported'] = False
            return {**result, 'status': 'fail', 'issues': [{
                'segment_id': document['segment_id'], 'problem': 'Preserve the qualified location',
                'exact_source_text': document['text'], 'evidence_refs': refs}]}
        return result

    model.mutate = reject
    engine = analyzer(model)
    if policy:
        engine.configuration['verification_policy_version'] = policy
    with pytest.raises(CoverAnalysisError) as error:
        engine.analyze(context, snapshot)
    parent = load_analysis(context, error.value.analysis_id)
    assert parent.semantic_revision['status'] == 'exhausted'
    return parent


def operations(parent):
    evidence = parent.evidence[0]
    return [{'path': '/events/0/location', 'old_value': 'apartment',
             'new_value': 'apartment entry; the exact room remains a source design assumption',
             'evidence_refs': ['evidence:' + evidence['evidence_id']],
             'exact_source_text': evidence['exact_quote']}]


def prepare(context, parent, **kwargs):
    return prepare_source_amendment(context, parent_analysis_id=parent.analysis_id,
        expected_parent_sha256=canonical_hash(parent.to_dict()), reviewer_id='independent-reviewer',
        amendments=operations(parent), **kwargs)


def test_amendment_retains_parent_and_runs_only_all_independent_reviews(source):
    context, snapshot, _ = source
    parent = failed_parent(source)
    folder = context.cover_root / 'analyses' / parent.analysis_id
    originals = {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    child = prepare(context, parent)
    assert child.analysis_id != parent.analysis_id
    assert child.coverage_status == 'partial'
    assert child.attempts == []
    assert child.segment_results == parent.segment_results
    assert child.inherited_semantic_revision == parent.semantic_revision
    assert 'semantic_revision' not in child.to_dict()
    model = ModelFixture()
    complete = analyzer(model).analyze(context, snapshot, analysis_id=child.analysis_id)
    assert complete.coverage_status == 'complete'
    assert model.calls and all(c['phase'] == 'verify' for c in model.calls)
    assert {s for c in model.calls if c.get('mode') == 'source_segment'
            for s in [d['segment_id'] for d in c['documents']]} == set(parent.coverage['expected_segment_ids'])
    assert any(c.get('mode') == 'global_cross_segment' for c in model.calls)
    assert load_analysis(context, complete.analysis_id).analysis_sha256 == complete.analysis_sha256
    assert 'design assumption' in director_projection(complete.to_dict())['events'][0]['location']
    assert originals == {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    assert prepare(context, parent).analysis_id == child.analysis_id


@pytest.mark.parametrize('damage', ['parent_sha', 'old_value', 'quote', 'refs', 'id', 'cast', 'metadata'])
def test_invalid_amendment_is_rejected_before_allocation(source, damage):
    context, _, _ = source
    parent = failed_parent(source)
    changes = operations(parent)
    expected = canonical_hash(parent.to_dict())
    if damage == 'parent_sha': expected = '0' * 64
    elif damage == 'old_value': changes[0]['old_value'] = 'different'
    elif damage == 'quote': changes[0]['exact_source_text'] = 'not in the source'
    elif damage == 'refs': changes[0]['evidence_refs'] = ['evidence:missing']
    elif damage == 'id': changes[0].update(path='/events/0/event_id', old_value='door_choice', new_value='new_id')
    elif damage == 'cast': changes[0].update(path='/events/0/participants/0', old_value='char_mara', new_value='char_oren')
    else: changes[0].update(path='/brief_payload/title', old_value=parent.brief_payload['title'], new_value='New title')
    with pytest.raises(CoverAnalysisError):
        prepare_source_amendment(context, parent_analysis_id=parent.analysis_id,
            expected_parent_sha256=expected, reviewer_id='independent-reviewer', amendments=changes)
    assert not list((context.cover_root / 'analysis-amendments').glob('analysis-*.json'))


def test_different_review_conflicts_and_deleted_marker_cannot_bypass_ledger(source):
    context, snapshot, _ = source
    parent = failed_parent(source)
    child = prepare(context, parent)
    altered = operations(parent)
    altered[0]['new_value'] += ' Another change.'
    with pytest.raises(CoverAnalysisError, match='different'):
        prepare_source_amendment(context, parent_analysis_id=parent.analysis_id,
            expected_parent_sha256=canonical_hash(parent.to_dict()), reviewer_id='independent-reviewer',
            amendments=altered)
    path = context.cover_root / 'analyses' / child.analysis_id / 'analysis.json'
    payload = json.loads(path.read_text())
    payload.pop('reviewed_source_amendment')
    path.write_text(json.dumps(payload))
    with pytest.raises(CoverAnalysisError, match='provenance'):
        load_analysis(context, child.analysis_id)
    model = ModelFixture()
    with pytest.raises(CoverAnalysisError):
        analyzer(model).analyze(context, snapshot, analysis_id=child.analysis_id)
    assert model.calls == []


def test_failed_source_review_does_not_schedule_global_repair_or_repeat(source):
    context, snapshot, _ = source
    parent = failed_parent(source)
    child = prepare(context, parent)
    model = ModelFixture()

    def reject(request, result):
        if request.get('mode') == 'source_segment':
            document = request['documents'][0]
            result['checks'][0]['supported'] = False
            result.update(status='fail', issues=[{'segment_id': document['segment_id'],
                'problem': 'Still wrong', 'exact_source_text': document['text'],
                'evidence_refs': result['checks'][0]['evidence_refs']}])
        return result

    model.mutate = reject
    with pytest.raises(CoverAnalysisError) as error:
        analyzer(model).analyze(context, snapshot, analysis_id=child.analysis_id)
    assert error.value.code == 'analysis_amendment_verification_failed'
    assert model.calls and all(c['phase'] == 'verify' for c in model.calls)
    recovery = source_amendment_recovery(context, load_analysis(context, child.analysis_id).to_dict())
    assert recovery['kind'] == 'amendment_failed'
    assert recovery['can_resume'] is False
    count = len(model.calls)
    with pytest.raises(CoverAnalysisError) as error:
        analyzer(model).analyze(context, snapshot, analysis_id=child.analysis_id)
    assert error.value.code == 'analysis_amendment_verification_failed'
    assert len(model.calls) == count


def test_exhausted_parent_resume_is_read_only_and_cannot_dispatch(source):
    context, snapshot, _ = source
    parent = failed_parent(source)
    before = {p.relative_to(context.cover_root): p.read_bytes()
              for p in context.cover_root.rglob('*') if p.is_file()}
    recovery = source_amendment_recovery(context, parent.to_dict())
    assert recovery['kind'] == 'source_correction_required'
    assert recovery['can_resume'] is False
    model = ModelFixture()
    with pytest.raises(CoverAnalysisError) as error:
        analyzer(model).analyze(context, snapshot, analysis_id=parent.analysis_id)
    assert error.value.code == 'analysis_semantic_repair_exhausted'
    assert error.value.details['recovery'] == recovery
    assert model.calls == []
    assert before == {p.relative_to(context.cover_root): p.read_bytes()
                      for p in context.cover_root.rglob('*') if p.is_file()}


def test_read_only_recovery_tracks_child_without_redirecting_parent(source):
    context, snapshot, _ = source
    parent = failed_parent(source)
    child = prepare(context, parent)
    before = {p.relative_to(context.cover_root): p.read_bytes()
              for p in context.cover_root.rglob('*') if p.is_file()}
    recovery = source_amendment_recovery(context, parent.to_dict())
    assert recovery['kind'] == 'amendment_available'
    assert recovery['analysis_id'] == child.analysis_id
    assert recovery['can_resume'] is False
    child_recovery = source_amendment_recovery(context, child.to_dict())
    assert child_recovery['kind'] == 'amendment_pending'
    assert child_recovery['can_resume'] is True
    assert child_recovery['parent_analysis_id'] == parent.analysis_id
    model = ModelFixture()
    with pytest.raises(CoverAnalysisError) as error:
        analyzer(model).analyze(context, snapshot, analysis_id=parent.analysis_id)
    assert error.value.code == 'analysis_amendment_required'
    assert error.value.details['recovery'] == recovery
    assert model.calls == []
    assert before == {p.relative_to(context.cover_root): p.read_bytes()
                      for p in context.cover_root.rglob('*') if p.is_file()}
    complete = analyzer(model).analyze(context, snapshot, analysis_id=child.analysis_id)
    assert source_amendment_recovery(context, complete.to_dict()) is None


def test_child_transport_failure_remains_recoverable(source):
    context, snapshot, _ = source
    parent = failed_parent(source)
    child = prepare(context, parent)
    def unavailable(*args):
        raise RuntimeError('transport unavailable')
    with pytest.raises(CoverAnalysisError):
        analyzer(unavailable).analyze(context, snapshot, analysis_id=child.analysis_id)
    failed = load_analysis(context, child.analysis_id)
    assert not failed.to_dict().get('source_amendment_failure')
    assert source_amendment_recovery(context, failed.to_dict())['can_resume'] is True
    assert analyzer(ModelFixture()).analyze(context, snapshot, analysis_id=child.analysis_id).coverage_status == 'complete'


def test_parent_does_not_offer_missing_amendment_child(source):
    context, _, _ = source
    parent = failed_parent(source)
    child = prepare(context, parent)
    (context.cover_root / 'analyses' / child.analysis_id / 'analysis.json').unlink()
    recovery = source_amendment_recovery(context, parent.to_dict())
    assert recovery['kind'] == 'amendment_failed'
    assert recovery['can_resume'] is False
    assert 'analysis_id' not in recovery


def test_completed_amendment_cache_clone_keeps_provenance_and_spent_budget(source):
    from core.cover_analysis_reviews import _assert_budget
    context, snapshot, _ = source
    parent = failed_parent(source)
    child = prepare(context, parent)
    model = ModelFixture()
    complete = analyzer(model).analyze(context, snapshot, analysis_id=child.analysis_id)
    model.calls.clear()
    new_snapshot = capture_source(context, snapshot.selector)
    clone = analyzer(model).analyze(context, new_snapshot)
    assert model.calls == []
    assert clone.reused_from_analysis_id == complete.analysis_id
    assert clone.inherited_semantic_revision == parent.semantic_revision
    assert 'reviewed_source_amendment' not in clone.to_dict()
    assert clone.global_checkpoint == complete.global_checkpoint
    assert load_analysis(context, clone.analysis_id).coverage_status == 'complete'
    for record in (complete, clone):
        with pytest.raises(CoverAnalysisError) as error:
            _assert_budget(context, record.to_dict())
        assert error.value.code == 'analysis_semantic_repair_exhausted'


def test_oversized_replacement_cannot_dispatch_or_allocate(source):
    context, _, _ = source
    parent = failed_parent(source)
    changes = operations(parent)
    changes[0]['new_value'] = 'x' * parent.configuration['max_request_bytes']
    with pytest.raises(CoverAnalysisError, match='byte budget'):
        prepare_source_amendment(context, parent_analysis_id=parent.analysis_id,
            expected_parent_sha256=canonical_hash(parent.to_dict()), reviewer_id='reviewer', amendments=changes)
    assert not list((context.cover_root / 'analysis-amendments').glob('analysis-*.json'))


def test_invalid_amended_checkpoint_never_falls_back_to_global(source, monkeypatch):
    import core.cover_analysis as module
    context, snapshot, _ = source
    parent = failed_parent(source)
    child = prepare(context, parent)
    original = module.apply_publication_metadata

    def changed_metadata(raw, publication):
        value = deepcopy(original(raw, publication))
        value['story']['stakes'] = 'Changed during normalization'
        return value

    monkeypatch.setattr(module, 'apply_publication_metadata', changed_metadata)
    model = ModelFixture()
    with pytest.raises(CoverAnalysisError, match='no generation was dispatched'):
        analyzer(model).analyze(context, snapshot, analysis_id=child.analysis_id)
    assert model.calls == []


def test_interrupted_registration_is_idempotent_and_cannot_run_unseeded_child(source, monkeypatch):
    from core import cover_analysis_corrections as module
    context, snapshot, _ = source
    parent = failed_parent(source)

    def crash(*args):
        raise RuntimeError('interrupt before seeded write')

    with monkeypatch.context() as patch:
        patch.setattr(module, '_save', crash)
        with pytest.raises(RuntimeError):
            prepare(context, parent)
    ledger = json.loads((context.cover_root / 'analysis-amendments' / (parent.analysis_id + '.json')).read_text())
    model = ModelFixture()
    with pytest.raises(CoverAnalysisError, match='provenance'):
        analyzer(model).analyze(context, snapshot, analysis_id=ledger['child_analysis_id'])
    assert model.calls == []
    child = prepare(context, parent)
    assert child.analysis_id == ledger['child_analysis_id']
    assert analyzer(model).analyze(context, snapshot, analysis_id=child.analysis_id).coverage_status == 'complete'


def test_duplicate_concurrent_registration_allocates_one_child(source):
    context, _, _ = source
    parent = failed_parent(source)
    barrier = threading.Barrier(2)

    def run(_):
        barrier.wait(timeout=5)
        return prepare(context, parent)

    with ThreadPoolExecutor(max_workers=2) as pool:
        children = list(pool.map(run, range(2)))
    assert children[0].analysis_id == children[1].analysis_id
    assert len(list((context.cover_root / 'analysis-amendments').glob('analysis-*.json'))) == 1


@pytest.mark.parametrize('damage', ['parent', 'extraction', 'checkpoint', 'marker', 'null_marker', 'inherited', 'provider'])
def test_child_read_rejects_source_provenance_damage(source, damage):
    context, _, _ = source
    parent = failed_parent(source)
    child = prepare(context, parent)
    path = context.cover_root / 'analyses' / child.analysis_id / 'analysis.json'
    payload = json.loads(path.read_text())
    if damage == 'parent':
        path = context.cover_root / 'analyses' / parent.analysis_id / 'analysis.json'
        payload = json.loads(path.read_text())
        payload['updated_at'] = 'Changed parent revision'
    elif damage == 'extraction': payload['evidence'][0]['statement'] += ' unsupported'
    elif damage == 'checkpoint': payload['global_checkpoint']['synthesis']['events'][0]['location'] += ' unsupported'
    elif damage == 'marker': payload['reviewed_source_amendment']['review_sha256'] = '0' * 64
    elif damage == 'null_marker': payload['reviewed_source_amendment'] = None
    elif damage == 'inherited': payload['inherited_semantic_revision']['status'] = 'pending'
    else: payload['attempts'] = [{'phase': 'global', 'status': 'complete'}]
    path.write_text(json.dumps(payload))
    with pytest.raises(CoverAnalysisError):
        load_analysis(context, child.analysis_id)


def test_existing_brief_narrative_text_is_supported(source):
    context, _, _ = source
    parent = failed_parent(source)
    changes = operations(parent)
    previous = parent.brief_payload['lived_environment']['economic_signals'][0]
    changes.append({**changes[0], 'path': '/brief_payload/lived_environment/economic_signals/0',
                    'old_value': previous, 'new_value': previous + ' (source design assumption)'})
    child = prepare_source_amendment(context, parent_analysis_id=parent.analysis_id,
        expected_parent_sha256=canonical_hash(parent.to_dict()), reviewer_id='reviewer', amendments=changes)
    assert child.brief_payload['lived_environment']['economic_signals'][0].endswith('(source design assumption)')


@pytest.mark.parametrize('old,new', [('cover-story-verification.v2', 'cover-story-verification.v3'),
                                     ('cover-story-verification.v3', 'cover-story-verification.v4')])
def test_configuration_change_rejects_before_any_child_write(source, old, new):
    context, snapshot, _ = source
    parent = failed_parent(source, policy=old)
    child = prepare(context, parent)
    path = context.cover_root / 'analyses' / child.analysis_id / 'analysis.json'
    before = path.read_bytes()
    model = ModelFixture()
    engine = analyzer(model)
    engine.configuration['verification_policy_version'] = new
    with pytest.raises(CoverAnalysisError, match='exact parent configuration'):
        engine.analyze(context, snapshot, analysis_id=child.analysis_id)
    assert path.read_bytes() == before
    assert model.calls == []
    assert load_analysis(context, child.analysis_id).configuration == parent.configuration


def test_marker_child_cannot_be_rebound_to_another_identical_snapshot(source):
    context, snapshot, _ = source
    parent = failed_parent(source)
    child = prepare(context, parent)
    replacement = capture_source(context, snapshot.selector)
    path = context.cover_root / 'analyses' / child.analysis_id / 'analysis.json'
    payload = json.loads(path.read_text())
    for key in ('snapshot_id', 'snapshot_sha256', 'source_content_sha256', 'source_bundle_sha256'):
        payload[key] = getattr(replacement, key)
    path.write_text(json.dumps(payload))
    with pytest.raises(CoverAnalysisError, match='changed'):
        load_analysis(context, child.analysis_id)
    model = ModelFixture()
    with pytest.raises(CoverAnalysisError):
        analyzer(model).analyze(context, replacement, analysis_id=child.analysis_id)
    assert model.calls == []
