from copy import deepcopy
from types import SimpleNamespace

import pytest

from core.outline_planning import OutlinePlanningError
from core.planning_residual_loop import converge
from core.planning_group_assembly import merge


def review(*messages):
    return {'status': 'blocked' if messages else 'passed', 'issues': [{'message': m} for m in messages]}


def planner():
    records = {}
    def load(name, deps, validate, **options):
        if name in records:
            assert records[name][0] == deps
            return validate(records[name][1])
    return SimpleNamespace(progress={}, records=records, _progress=lambda *args: None, _load=load,
                           _save=lambda name, deps, record: records.update({name: (deps, record)}))


def test_progress_can_repair_more_than_two_verified_rounds_and_retain_every_original_field():
    start = {'chapters': [{'x': 'wrong', 'y': 'wrong', 'z': 'wrong', 'final': 'wrong'}]}
    checks = []
    def advance(current, outstanding, stage, prev):
        checks.append(stage)
        next_book = deepcopy(current)
        slot = next(k for k,v in current['chapters'][0].items() if v == 'wrong')
        next_book['chapters'][0][slot] = 'corrected'
        return next_book, review() if slot == 'final' else review('same remaining issue')
    fixed, final = converge(planner(), start, review('same remaining issue'), advance, authority=[])
    assert final['status'] == 'passed' and len(set(checks)) == 4
    assert set(fixed['chapters'][0].values()) == {'corrected'}
    assert set(start['chapters'][0].values()) == {'wrong'}


@pytest.mark.parametrize('values', [['A'], ['B', 'A'], ['B', 'C']])
def test_repeated_candidates_and_rephrasing_same_field_cannot_earn_extra_attempts(values):
    first = {'chapters': [{'reason': 'A'}]}
    result = planner()
    for _ in range(2):
        changes = iter(values)
        def advance(current, outstanding, stage, prev):
            value = next(changes)
            return {'chapters': [{'reason': value}]}, review('same issue reworded ' + value)
        with pytest.raises(OutlinePlanningError, match='未产生可验证的新进展'):
            converge(result, first, review('original'), advance, authority=[])
        assert len(result.records) == 1
        assert result.progress['recovery']['action'] == 'inspect_residual_closure'


def test_registry_can_gain_one_witness_without_allowing_infinite_appends():
    book = {'chapters': [], 'promise_ledger': [{'setup_id':'initial'}]}
    result = planner()
    def advance(current, outstanding, stage, prev):
        fixed = deepcopy(current)
        fixed['promise_ledger'].append({'setup_id': str(len(fixed['promise_ledger']))})
        return fixed, review('same conflict')
    with pytest.raises(OutlinePlanningError, match='未产生可验证的新进展'):
        converge(result, book, review('same conflict'), advance, authority=[])
    assert len(book['promise_ledger']) == 1


def test_merging_independent_promise_and_risk_changes_preserves_global_authority():
    baseline = {'chapter_map':[{'number':1}], 'chapters':[{'number':1}], 'characters':[],
                'assumptions':[], 'structural_risks':['one','two'], 'ending_contract':{},
                'promise_ledger':[{'setup_id':'old','setup_chapter':2}]}
    combined = deepcopy(baseline)
    for n in range(2):
        child = deepcopy(baseline)
        child['structural_risks'][n] += ' fixed'
        child['promise_ledger'].append({'setup_id':f'p{n}'})
        merge(baseline, combined, child, [], residual=True)
    assert combined['structural_risks'] == ['one fixed','two fixed']
    assert [x['setup_id'] for x in combined['promise_ledger']] == ['old','p0','p1']
    wrong = deepcopy(baseline)
    wrong['ending_contract']['payoff'] = 'rewritten'
    with pytest.raises(ValueError, match='unauthorized global'):
        merge(baseline, combined, wrong, [], residual=True)
    wrong = deepcopy(baseline)
    wrong['promise_ledger'][0]['setup_id'] = 'replacement'
    with pytest.raises(ValueError, match='registered promise'):
        merge(baseline, combined, wrong, [], residual=True)
