"""An outcome label must not become an impossible canonical thread status."""
import json

import pytest

from ending_evidence import EndingEvidenceError, EndingEvidenceStore, contract_sha256
from ending_quality import evaluate_ending
from outline_planning import OutlinePlanningError
from state_manager import PlotThread, StoryState
from test_ending_evidence import evidence_book
from test_outline_planning import PlanningModel, make_planner
from plot_thread_contract import (
    canonical_arc_stage,
    normalize_ending_arc_stages,
    validate_ending_statuses,
)


def test_planning_rejects_semantic_outcome_in_status_before_writing(tmp_path):
    model = PlanningModel(mutate_global=lambda value: value['ending_contract']['main_conflict'].update(
        required_status='resolved_with_bounded_autonomy'))
    with pytest.raises(OutlinePlanningError, match='required_status'):
        make_planner(tmp_path, model, count=2).run()
    assert not any(stage == 'batch' for stage, _ in model.calls)


def test_completed_is_a_legacy_contract_alias_only():
    contract = {
        'enforce': True,
        'character_arcs': [
            {'character_id': 'char_001', 'required_arc_stage': 'completed'}
        ],
        'main_conflict': {'thread_id': 'main', 'required_status': 'resolved'},
        'antagonist_outcome': {'required': False},
    }

    validate_ending_statuses(contract)
    assert canonical_arc_stage('completed') == 'resolution'
    normalized = normalize_ending_arc_stages(contract)

    assert normalized['character_arcs'][0]['required_arc_stage'] == 'resolution'
    assert contract['character_arcs'][0]['required_arc_stage'] == 'completed'


def test_unknown_arc_stage_is_rejected_at_contract_boundary():
    contract = {
        'enforce': True,
        'character_arcs': [
            {'character_id': 'char_001', 'required_arc_stage': 'finished'}
        ],
        'main_conflict': {'thread_id': 'main', 'required_status': 'resolved'},
        'antagonist_outcome': {'required': False},
    }

    with pytest.raises(ValueError, match='required_arc_stage'):
        validate_ending_statuses(contract)


def _legacy_main(evidence_book):
    project, contract, payload, _ = evidence_book
    state = StoryState(str(project))
    state.plot_threads['main'] = PlotThread('main', 'Autonomy', 'Bounded work', 'main', status='resolved')
    state.save_state()
    contract.update(character_arcs=[], plot_payoffs=[], finale_window={'start_chapter': 1, 'end_chapter': 1},
        main_conflict={'thread_id': 'main', 'required_status': 'resolved_with_bounded_autonomy',
                       'protagonist_choice': 'Leave the old arrangement', 'consequence': 'Independent work with limits'})
    (project / 'outputs/input/ending_contract.json').write_text(json.dumps(contract))
    source = payload['claims'][0]['sources']
    payload.update(contract_sha256=contract_sha256(contract), claims=[{
        'kind': 'main_conflict', 'thread_id': 'main', 'status': 'resolved',
        'required_status': 'resolved_with_bounded_autonomy',
        'evidence': 'Reviewed the specific choice and consequence against approved prose.', 'sources': source,
    }])
    return project, contract, payload


def test_legacy_semantic_status_requires_source_bound_review(evidence_book):
    project, contract, payload = _legacy_main(evidence_book)
    before = (project / 'outputs/state/story_state.json').read_bytes()
    report = evaluate_ending(project, as_of_chapter=1)
    assert any(item['category'] == 'main_conflict_status_invalid' for item in report.critical)
    review = EndingEvidenceStore(project).record(payload)
    report = evaluate_ending(project, as_of_chapter=1)
    assert not any(item['category'].startswith('main_conflict') for item in report.critical)
    assert any(item.get('review_id') == review['review_id'] for item in report.info)
    assert (project / 'outputs/state/story_state.json').read_bytes() == before
    assert json.loads((project / 'outputs/input/ending_contract.json').read_text()) == contract


@pytest.mark.parametrize('change', ['active', 'wrong_obligation', 'false_quote', 'supported_status', 'outside_finale', 'unresolved_claim'])
def test_review_cannot_override_real_conflict_or_unbound_sources(evidence_book, change):
    project, contract, payload = _legacy_main(evidence_book)
    if change == 'active':
        state = StoryState(str(project))
        state.plot_threads['main'].status = 'active'
        state.save_state()
    elif change == 'wrong_obligation':
        payload['claims'][0]['required_status'] = 'resolved_with_revenge'
    elif change == 'false_quote':
        payload['claims'][0]['sources'][0]['quote'] = 'An invented ending.'
    elif change == 'supported_status':
        contract['main_conflict']['required_status'] = 'active'
        (project / 'outputs/input/ending_contract.json').write_text(json.dumps(contract))
        payload['contract_sha256'] = contract_sha256(contract)
        payload['claims'][0]['required_status'] = 'active'
    elif change == 'outside_finale':
        contract['finale_window'] = {'start_chapter': 2, 'end_chapter': 3}
        (project / 'outputs/input/ending_contract.json').write_text(json.dumps(contract))
        payload['contract_sha256'] = contract_sha256(contract)
    else:
        payload['claims'][0]['status'] = 'active'
    with pytest.raises(EndingEvidenceError):
        EndingEvidenceStore(project).record(payload)


def test_review_stales_when_canon_or_original_obligation_changes(evidence_book):
    project, contract, payload = _legacy_main(evidence_book)
    EndingEvidenceStore(project).record(payload)
    state = StoryState(str(project))
    state.plot_threads['main'].status = 'active'
    state.save_state()
    assert any(item['category'] == 'ending_evidence_invalid' for item in evaluate_ending(project).critical)
    state.plot_threads['main'].status = 'resolved'
    state.save_state()
    contract['main_conflict']['consequence'] = 'A different author obligation.'
    (project / 'outputs/input/ending_contract.json').write_text(json.dumps(contract))
    assert any(item['category'] == 'ending_evidence_invalid' for item in evaluate_ending(project).critical)


@pytest.mark.parametrize('field', ['main_conflict', 'antagonist_outcome'])
@pytest.mark.parametrize('status', ['', None, ['resolved'], 'resolved_with_victory'])
def test_both_new_contract_paths_reject_non_lifecycle_status(field, status):
    contract = {'enforce': True, 'main_conflict': {'thread_id': 'main', 'required_status': 'resolved'},
                'antagonist_outcome': {'required': True, 'thread_id': 'opposition', 'required_status': 'resolved'}}
    contract[field]['required_status'] = status
    with pytest.raises(ValueError, match=f'{field}.required_status'):
        validate_ending_statuses(contract)
