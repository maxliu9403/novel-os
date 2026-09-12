import json
import zipfile
from pathlib import Path

import pytest

from h5_acceptance import inspect_package, ContractError
from h5_import import object_digest

SAMPLES = Path(__file__).resolve().parents[1] / 'docs/examples/h5-import'


def test_read_only_preflight_checks_real_baseline_but_does_not_claim_h5_execution():
    result = inspect_package(SAMPLES / '01-introduction-80-chapters-4-volumes.zip')
    assert result['engine_contract_status'] == 'passed'
    assert result['external_import_status'] == 'pending'
    assert result['chapter_count'] == 80
    assert result['volume_count'] == 4
    assert result['introduction_count'] == 1
    assert result['chapters'][0]['number'] == 1
    assert result['chapters'][20]['volume_id'] == 'volume_02'


def rewritten(tmp_path, mutate):
    with zipfile.ZipFile(SAMPLES / '01-introduction-80-chapters-4-volumes.zip') as source:
        files = {p: source.read(p) for p in source.namelist()}
    mutate(files)
    path = tmp_path / 'changed.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return path


def test_mime_regression_is_detected_even_when_package_digest_is_recomputed(tmp_path):
    def mutate(files):
        m = json.loads(files['package-manifest.json'])
        next(e for e in m['files'] if e['path'] == 'book.epub')['media_type'] = 'application/octet-stream'
        m['package_revision_sha256'] = object_digest(m['files'])
        files['package-manifest.json'] = json.dumps(m).encode()
    with pytest.raises(ContractError, match='epub_media_type'):
        inspect_package(rewritten(tmp_path, mutate))


def test_unindexed_private_file_and_duplicate_zip_path_are_rejected(tmp_path):
    path = rewritten(tmp_path, lambda files: files.update({'outputs/quality/methods/report.json': b'private'}))
    with pytest.raises(ContractError, match='unindexed_zip_member'):
        inspect_package(path)
    path = rewritten(tmp_path, lambda files: None)
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(path, 'a') as archive:
            archive.writestr('book.md', b'duplicate')
    with pytest.raises(ContractError, match='duplicate_zip_path'):
        inspect_package(path)


def test_acceptance_plan_checks_cover_only_semantics_and_keeps_h5_pending():
    from h5_acceptance import build_acceptance_plan, verify_importer_receipt
    plan = build_acceptance_plan([{
        'id': 'cover-update', 'action': 'cover_only', 'free_trial_end': 3,
        'package': str(SAMPLES / '02-same-book-cover-only-update.zip'),
        'baseline': str(SAMPLES / '01-introduction-80-chapters-4-volumes.zip'),
    }])
    case = plan['cases'][0]
    assert set(case['changed_components']) == {'selected_cover_sha256', 'cover_metadata_sha256'}
    assert case['expected_outcome'] == 'updated'
    assert case['incoming']['chapters'][20]['number'] > case['free_trial_end']
    result = verify_importer_receipt(plan, {'schema_version': 'novel-h5-acceptance-receipt.v1',
                                           'plan_sha256': plan['plan_sha256'], 'importer': None, 'results': []})
    assert result['status'] == 'pending'
    assert result['current_importer_verified'] is False


def test_new_compile_package_fixtures_cover_free_windows_multivolume_updates_and_errors(tmp_path):
    from scripts.build_method_validation_samples import build_samples
    plan = build_samples(tmp_path / 'suite')
    cases = {c['id']: c for c in plan['cases']}
    assert cases['short-free-3']['free_trial_end'] == 3
    assert cases['short-free-4']['free_trial_end'] == 4
    long = cases['long-48']['incoming']
    assert long['chapter_count'] == 48 and long['volume_count'] == 4
    assert long['chapters'][12]['volume_id'] == 'volume_02'
    assert long['chapters'][12]['chapter_in_volume'] == 1
    assert cases['introduction-update']['incoming']['versions']['content_sha256'] == long['versions']['content_sha256']
    assert set(cases['chapter-update']['changed_components']) == {'content_sha256', 'epub_sha256'}
    assert sum(c['action'] == 'reject_invalid' for c in cases.values()) == 4
    for name in ('01-introduction-80-chapters-4-volumes.zip', '02-same-book-cover-only-update.zip', '03-different-book-same-structure.zip'):
        assert (tmp_path / 'suite' / name).read_bytes() == (SAMPLES / name).read_bytes()


def test_empty_self_checksummed_plan_is_not_external_acceptance():
    from h5_acceptance import verify_importer_receipt
    plan = {'schema_version': 'novel-h5-acceptance-plan.v1', 'cases': []}
    plan['plan_sha256'] = object_digest(plan)
    with pytest.raises(ValueError):
        verify_importer_receipt(plan, {'schema_version': 'novel-h5-acceptance-receipt.v1',
                                      'plan_sha256': plan['plan_sha256'], 'importer': None, 'results': []})


def test_indexed_private_debug_json_disguised_as_book_export_is_rejected(tmp_path):
    from hashlib import sha256
    def mutate(files):
        raw = b'{"method_lock_sha256":"private","raw_critique":"private"}'
        files['meta/debug.json'] = raw
        m = json.loads(files['package-manifest.json'])
        m['files'].append({'path': 'meta/debug.json', 'size': len(raw), 'sha256': sha256(raw).hexdigest(),
                           'role': 'book_export', 'media_type': 'application/json', 'cover_selection_state': ''})
        m['package_revision_sha256'] = object_digest(m['files'])
        files['package-manifest.json'] = json.dumps(m).encode()
    with pytest.raises(ContractError):
        inspect_package(rewritten(tmp_path, mutate))


def test_sample_builder_rejects_even_an_identity_only_novel_project(tmp_path):
    from project_identity import ensure_project_instance_id
    from scripts.build_method_validation_samples import build_samples
    project = tmp_path / 'novel'
    ensure_project_instance_id(project)
    with pytest.raises(ValueError, match='outside Novel OS'):
        build_samples(project / 'nested-suite')
    assert not (project / 'nested-suite').exists()


def observed(snapshot, *, prefix='book-one', free=3):
    return {
        'book_pk': prefix, 'source_book_id': snapshot['book_id'], 'introduction_count': snapshot['introduction_count'],
        'versions': dict(snapshot['versions']),
        'volume_rows': [{**v, 'volume_pk': prefix + ':' + v['volume_id']} for v in snapshot['volumes']],
        'chapter_rows': [{**{k: c[k] for k in ('chapter_id', 'number', 'volume_id', 'chapter_in_volume')},
                         'chapter_pk': prefix + ':chapter:' + str(c['number']),
                         'volume_pk': prefix + ':' + c['volume_id'], 'is_free': c['number'] <= free}
                        for c in snapshot['chapters']],
        **{key: {'count': 1, 'sha256': char * 64} for key, char in [('progress', 'a'), ('unlocks', 'b'), ('collections', 'c')]},
    }


def receipt_for(plan):
    results = []
    for case in plan['cases']:
        after_source = case['before'] if case['action'] in {'reject_invalid', 'history_replay'} else case['incoming']
        results.append({'case_id': case['id'], 'package_sha256': case['package_sha256'], 'outcome': case['expected_outcome'],
                        'error_code': case['expected_error'] or '',
                        'book_count_before': 1, 'book_count_after': 2 if case['expected_outcome'] == 'created' else 1,
                        'before': observed(case['before'], free=case['free_trial_end']) if case['before'] else None,
                        'after': observed(after_source, free=case['free_trial_end'],
                                          prefix='book-two' if case['action'] == 'different_book' else 'book-one')})
    return {'schema_version': 'novel-h5-acceptance-receipt.v1', 'plan_sha256': plan['plan_sha256'],
            'importer': {'name': 'fixture-importer', 'version': 'fixture-1', 'environment': 'test',
                         'executed_at': '2026-09-12T00:00:00Z', 'trace_sha256': 'd' * 64}, 'results': results}


def cover_plan():
    from h5_acceptance import build_acceptance_plan
    return build_acceptance_plan([{'id': 'cover', 'action': 'cover_only', 'free_trial_end': 3,
                                   'package': str(SAMPLES / '02-same-book-cover-only-update.zip'),
                                   'baseline': str(SAMPLES / '01-introduction-80-chapters-4-volumes.zip')}])


def test_valid_external_claims_stay_explicitly_external():
    from h5_acceptance import verify_importer_receipt
    plan = cover_plan()
    result = verify_importer_receipt(plan, receipt_for(plan))
    assert result['status'] == 'externally_reported_pass'
    assert result['current_importer_verified'] is False


@pytest.mark.parametrize('change', [
    lambda r: r.update(book_count_after=2),
    lambda r: r.update(book_count_before=0, book_count_after=0),
    lambda r: r.update(error_code='database_unavailable'),
    lambda r: r['after'].update(book_pk='recreated'),
    lambda r: r['after']['chapter_rows'][0].update(chapter_pk='recreated'),
    lambda r: r['after']['chapter_rows'][20].update(is_free=True),
    lambda r: r['after']['chapter_rows'][20].update(volume_pk='wrong-volume'),
    lambda r: r['after']['volume_rows'].pop(),
    lambda r: r['after']['volume_rows'][0].update(title='not-the-expected-title'),
    lambda r: r['after']['progress'].update(sha256='e' * 64),
    lambda r: r['before']['unlocks'].update(count=0),
    lambda r: r.update(package_sha256='0' * 64),
])
def test_update_receipts_detect_lost_identity_free_counts_volumes_and_user_state(change):
    from h5_acceptance import verify_importer_receipt
    plan = cover_plan()
    receipt = receipt_for(plan)
    change(receipt['results'][0])
    with pytest.raises(ValueError):
        verify_importer_receipt(plan, receipt)


def test_database_failure_is_not_evidence_of_rejecting_an_invalid_epub(tmp_path):
    from h5_acceptance import build_acceptance_plan, verify_importer_receipt
    def mutate(files):
        m = json.loads(files['package-manifest.json'])
        next(e for e in m['files'] if e['path'] == 'book.epub')['media_type'] = 'application/octet-stream'
        m['package_revision_sha256'] = object_digest(m['files'])
        files['package-manifest.json'] = json.dumps(m).encode()
    bad = rewritten(tmp_path, mutate)
    plan = build_acceptance_plan([{'id': 'bad', 'action': 'reject_invalid', 'free_trial_end': 3,
                                   'package': str(bad), 'expected_error': 'epub_media_type',
                                   'baseline': str(SAMPLES / '01-introduction-80-chapters-4-volumes.zip')}])
    receipt = receipt_for(plan)
    assert verify_importer_receipt(plan, receipt)['status'] == 'externally_reported_pass'
    receipt['results'][0]['error_code'] = 'database_unavailable'
    with pytest.raises(ContractError, match='unexpected_rejection_error'):
        verify_importer_receipt(plan, receipt)


def test_different_book_receipt_needs_existing_baseline_in_library_count():
    from h5_acceptance import build_acceptance_plan, verify_importer_receipt
    plan = build_acceptance_plan([{'id': 'different', 'action': 'different_book', 'free_trial_end': 3,
                                   'package': str(SAMPLES / '03-different-book-same-structure.zip'),
                                   'baseline': str(SAMPLES / '01-introduction-80-chapters-4-volumes.zip')}])
    receipt = receipt_for(plan)
    assert verify_importer_receipt(plan, receipt)['status'] == 'externally_reported_pass'
    receipt['results'][0].update(book_count_before=0, book_count_after=1)
    with pytest.raises(ContractError, match='baseline_missing_from_library_count'):
        verify_importer_receipt(plan, receipt)


def test_initial_import_into_empty_library_has_one_book_afterward():
    from h5_acceptance import build_acceptance_plan, verify_importer_receipt
    plan = build_acceptance_plan([{'id': 'initial', 'action': 'initial', 'free_trial_end': 3,
                                   'package': str(SAMPLES / '01-introduction-80-chapters-4-volumes.zip')}])
    receipt = receipt_for(plan)
    receipt['results'][0].update(book_count_before=0, book_count_after=1)
    assert verify_importer_receipt(plan, receipt)['status'] == 'externally_reported_pass'


@pytest.mark.parametrize('path', ['h5-publication/fixture/lock.json', 'h5-publication/pkg-fixture/meta/publication_package.json'])
def test_internal_schema_is_not_a_public_h5_projection_even_under_an_allowed_path(tmp_path, path):
    from hashlib import sha256
    def mutate(files):
        raw = b'{"schema":"novel-method-lock.v1","policy":{"mode":"advisory"},"assets":{}}'
        files[path] = raw
        m = json.loads(files['package-manifest.json'])
        m['files'].append({'path': path, 'size': len(raw), 'sha256': sha256(raw).hexdigest(),
                           'role': 'h5_publication_object', 'media_type': 'application/json', 'cover_selection_state': ''})
        m['package_revision_sha256'] = object_digest(m['files'])
        files['package-manifest.json'] = json.dumps(m).encode()
    with pytest.raises(ContractError):
        inspect_package(rewritten(tmp_path, mutate))
