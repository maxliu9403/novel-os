"""Spent coordinated attempts cannot become new calls through stale bindings."""
import json

import pytest

from core import planning_coordinated as contract, planning_review_reuse, planning_repair_protocol
from outline_planning import OutlinePlanningError
from test_outline_coordinated_repair import CoordinatedModel, MIXED, make_planner, review


def snapshot(planner):
    return {path: path.read_bytes() for path in planner.cache.rglob('*') if path.is_file()}


def assert_unchanged(before):
    assert all(path.read_bytes() == data for path, data in before.items())


@pytest.mark.parametrize('receipt', ['plan-response', 'plan-format-correction-response'])
@pytest.mark.parametrize('binding', ['dependencies_hash', 'identity_hash'])
def test_exhausted_plan_response_with_changed_binding_never_spends_again(tmp_path, receipt, binding):
    first = CoordinatedModel(plan={'issues': []})
    planner = make_planner(tmp_path, first, count=8)
    with pytest.raises(OutlinePlanningError, match='bounded format correction'):
        planner.run()
    assert [stage for stage, _ in first.calls][-2:] == ['coordinated_plan', 'format']
    assert planner.progress['recovery']['retryable'] is False
    path = planner.cache / f'{contract.VERSION}-{receipt}.json'
    value = json.loads(path.read_text())
    value[binding] = '0' * 64
    path.write_text(json.dumps(value))
    before = snapshot(planner)
    resumed = CoordinatedModel(reviews=[review()])
    with pytest.raises(OutlinePlanningError, match='different inputs or policy.*cannot be reset'):
        make_planner(tmp_path, resumed, count=8).run()
    assert resumed.calls == []
    assert_unchanged(before)
    assert not (planner.cache / 'validated.json').exists()


@pytest.mark.parametrize('stage', ['plan', 'group', 'verification'])
def test_changed_coordinated_policy_cannot_reset_completed_stage_allowance(tmp_path, monkeypatch, stage):
    if stage == 'plan':
        first = CoordinatedModel(fail_stage='group')
    elif stage == 'group':
        first = CoordinatedModel(fail_stage='verification')
    else:
        first = CoordinatedModel(reviews=[MIXED, MIXED])
    planner = make_planner(tmp_path, first, count=8)
    with pytest.raises((OutlinePlanningError, TimeoutError)):
        planner.run()
    before = snapshot(planner)
    if stage == 'plan':
        monkeypatch.setattr(contract, 'PLAN_INSTRUCTION', contract.PLAN_INSTRUCTION + '\nChanged dependency policy.')
    elif stage == 'group':
        monkeypatch.setattr(planning_repair_protocol, 'INSTRUCTION',
                            planning_repair_protocol.INSTRUCTION + '\nChanged correction policy.')
    else:
        monkeypatch.setattr(planning_review_reuse, 'RULES', planning_review_reuse.RULES + '\nChanged verification policy.')
    resumed = CoordinatedModel(reviews=[review()])
    recovered = make_planner(tmp_path, resumed, count=8)
    assert recovered.identity_hash == planner.identity_hash
    with pytest.raises(OutlinePlanningError, match='different inputs or policy.*cannot be reset'):
        recovered.run()
    assert resumed.calls == []
    # The deterministic assembled candidate is re-saved before verification;
    # its payload is unchanged, while every completed paid stage stays intact.
    if stage == 'verification':
        candidate_path = planner.cache / f'{contract.VERSION}-candidate.json'
        original = json.loads(before.pop(candidate_path))
        current = json.loads(candidate_path.read_text())
        assert current['payload'] == original['payload']
        assert current['payload_hash'] == original['payload_hash']
    assert_unchanged(before)
    assert not (planner.cache / 'validated.json').exists()


def test_corrupt_completed_group_binding_is_terminal_instead_of_repaired_from_raw(tmp_path):
    first = CoordinatedModel(fail_stage='verification')
    planner = make_planner(tmp_path, first, count=8)
    with pytest.raises(TimeoutError):
        planner.run()
    path = planner.cache / f'{contract.VERSION}-group-001.json'
    value = json.loads(path.read_text())
    value['dependencies_hash'] = '0' * 64
    path.write_text(json.dumps(value))
    before = snapshot(planner)
    resumed = CoordinatedModel(reviews=[review()])
    with pytest.raises(OutlinePlanningError, match='different inputs or policy.*cannot be reset'):
        make_planner(tmp_path, resumed, count=8).run()
    assert resumed.calls == []
    assert_unchanged(before)


def test_metadata_recovery_uses_latest_complete_format_reply_not_recoverable_original(tmp_path):
    from core.planning_recovery import metadata_recovery_available
    from outline_planning import content_hash
    from test_outline_planning import PlanningModel, make_planner as baseline_planner
    from test_planning_metadata_recovery import seed_metadata_failure

    project = tmp_path / 'book'
    latest = 'The latest saved correction preserves the choice and its later cost.'
    planner = seed_metadata_failure(project, original_index_error=False, latest_consequence=latest)
    name = contract.VERSION + '-group-002'
    original = json.loads(json.loads((planner.cache / f'{name}-response.json').read_text())['payload']['raw'])
    formatted = json.loads(json.loads((planner.cache / f'{name}-format-correction-response.json').read_text())['payload']['raw'])
    assert original['chapter_replacements'][0]['after']['incoming_consequence'] != latest
    assert formatted['chapter_replacements'][0]['after']['incoming_consequence'] == latest
    assert metadata_recovery_available(project, 'test-run', planner.progress)
    before = snapshot(planner)

    resumed = PlanningModel()
    result = baseline_planner(planner.directory.parent, resumed, count=12, source=planner.source).run()
    assert [stage for stage, _ in resumed.calls] == ['review']
    assert result['chapters'][8]['incoming_consequence'] == latest
    canonical = json.loads((planner.cache / f'{name}.json').read_text())['payload']
    assert canonical['chapter_replacements'] == [{**formatted['chapter_replacements'][0],
                                                 'source_quote': 'Exact source choice 9:'}]
    metadata = json.loads((planner.cache / f'{name}-metadata-v1.json').read_text())['payload']
    assert metadata['input_patch_sha256'] == content_hash(formatted)
    assert metadata['input_patch_sha256'] != content_hash(original)
    assert metadata['output_patch_sha256'] == content_hash(canonical)
    verification = resumed.calls[0][1]['coordinated_verification']
    assert verification['issues'][1]['reference_binding'] == metadata
    assert_unchanged(before)


@pytest.mark.parametrize('receipt_change', ['missing', 'changed_provenance'])
def test_recovered_group_cannot_resume_without_its_exact_metadata_receipt(tmp_path, monkeypatch, receipt_change):
    from outline_planning import content_hash
    from test_outline_planning import PlanningModel, make_planner as baseline_planner
    from test_planning_metadata_recovery import seed_metadata_failure

    planner = seed_metadata_failure(tmp_path / 'book')
    planner.client = PlanningModel()
    save = planner._save

    def stop_before_candidate(name, *args):
        if name == contract.VERSION + '-candidate':
            raise TimeoutError('Stopped after canonical group save, before assembled audit save')
        return save(name, *args)

    monkeypatch.setattr(planner, '_save', stop_before_candidate)
    with pytest.raises(TimeoutError):
        planner.run()
    assert planner.client.calls == []
    path = planner.cache / f'{contract.VERSION}-group-002-metadata-v1.json'
    assert path.exists()
    assert (planner.cache / f'{contract.VERSION}-group-002.json').exists()
    if receipt_change == 'missing':
        path.unlink()
    else:
        record = json.loads(path.read_text())
        record['payload']['evidence_origin'] = 'model_selected'
        record['payload']['model_selected_source_evidence'] = True
        record['payload_hash'] = content_hash(record['payload'])
        path.write_text(json.dumps(record))
    before = snapshot(planner)
    resumed = PlanningModel()
    with pytest.raises(OutlinePlanningError, match='(?i)reference|metadata|different inputs'):
        baseline_planner(planner.directory.parent, resumed, count=12, source=planner.source).run()
    assert resumed.calls == []
    assert_unchanged(before)
    assert not (planner.cache / 'validated.json').exists()


@pytest.mark.parametrize('stop_before', ['group-002', 'candidate'])
def test_metadata_recovery_restart_retains_provenance_without_new_repair_calls(tmp_path, monkeypatch, stop_before):
    from test_outline_planning import PlanningModel, make_planner as baseline_planner
    from test_planning_metadata_recovery import seed_metadata_failure

    planner = seed_metadata_failure(tmp_path / 'book')
    planner.client = PlanningModel()
    save = planner._save

    def interrupt(name, *args):
        if name == contract.VERSION + '-' + stop_before:
            raise TimeoutError('Stopped after durable metadata receipt')
        return save(name, *args)

    monkeypatch.setattr(planner, '_save', interrupt)
    with pytest.raises(TimeoutError):
        planner.run()
    assert planner.client.calls == []
    before = snapshot(planner)
    receipt_path = planner.cache / f'{contract.VERSION}-group-002-metadata-v1.json'
    metadata = json.loads(receipt_path.read_text())['payload']
    resumed = PlanningModel()
    baseline_planner(planner.directory.parent, resumed, count=12, source=planner.source).run()
    assert [stage for stage, _ in resumed.calls] == ['review']
    assert resumed.calls[0][1]['coordinated_verification']['issues'][1]['reference_binding'] == metadata
    assert_unchanged(before)
