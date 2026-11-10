"""A generated premise cannot authenticate a conflict between two author decisions."""
from copy import deepcopy

import pytest

from core import planning_evidence_plan as evidence

A = 'The original seal is destroyed.'
B = 'The court closes the case using the forged register.'
C = 'The original seal survives the fire intact.'
ASSUMPTION = 'Only an intact original seal could convince the court.'
SOURCE = A + '\n' + B
CANDIDATE = {'chapter_map': [{'number': n} for n in range(1, 5)], 'assumptions': [ASSUMPTION]}
REVIEW = {'issues': [{'scope': 'global', 'message': 'Is the original seal needed?'},
                     {'scope': 'chapter', 'chapter': 3, 'message': 'Track the register.'}]}


def issue(i=0):
    return {'issue_index': i, 'classification': 'source_conflict' if i == 0 else 'continuity_gap',
            'reason': 'Check the order of events.', 'affected_chapters': [1, 2] if i == 0 else [3, 4],
            'writable_chapters': [] if i == 0 else [3], 'dependency_chapters': [],
            'source_quotes': [A, B], 'candidate_evidence': [{'path': '/assumptions/0', 'quote': ASSUMPTION}]
            if i == 0 else []}


def verdict(verdict_type='candidate_drift'):
    return {'verdict': verdict_type, 'reason': 'Both events can occur. The court rests on the register.',
            'source_quotes': [A, B], 'writable_chapters': [1] if verdict_type == 'candidate_drift' else [],
            'conflict_basis': None}


def test_legacy_correction_reuses_raw_citations_only_for_exact_candidate_evidence():
    def old(i):
        v = issue(i)
        return {k:v[k] for k in ('issue_index', 'classification', 'reason', 'source_quotes')} | {
            'requested_chapters': [1, 2] if i == 0 else [3], 'dependency_chapters': []}
    corrected = {'issues': [old(i) for i in range(2)]}
    original = deepcopy(corrected)
    original['issues'][0]['source_quotes'].append(ASSUMPTION)
    converted = evidence.migrate_legacy(CANDIDATE, REVIEW, SOURCE, corrected, prior=original)
    item = converted['issues'][0]
    assert item['source_quotes'] == [A, B]
    assert item['candidate_evidence'] == [{'path':'/assumptions/0', 'quote':ASSUMPTION}]
    assert item['affected_chapters'] == [1, 2] and item['writable_chapters'] == []
    assert converted['issues'][1]['writable_chapters'] == [3]
    assert corrected['issues'][0]['requested_chapters'] == [1, 2]


def test_new_plan_reports_source_permission_and_chapter_errors_together():
    a, b = issue(), issue(1)
    a.update(source_quotes=[ASSUMPTION], writable_chapters=[1])
    b['candidate_evidence'] = [{'path':'/assumptions/0','quote':'A fabricated claim.'}]
    b['dependency_chapters'] = [3, True]
    with pytest.raises(ValueError) as err:
        evidence.validate_plan(CANDIDATE, REVIEW, SOURCE, {'issues':[a,b]})
    for field in ('issues[0].source_quotes', 'issues[0].writable_chapters',
                  'issues[1].candidate_evidence','issues[1].dependency_chapters'):
        assert field in str(err.value)


def test_only_source_drift_restores_writes_after_independent_adjudication():
    plan = evidence.validate_plan(CANDIDATE, REVIEW, SOURCE, {'issues':[issue(), issue(1)]})
    context = evidence.conflict_context(SOURCE, plan['issues'][0])
    assert context['complete_adopted_source'] == SOURCE
    with pytest.raises(ValueError):
        evidence.execution_plan(plan, {})
    v = evidence.validate_verdict(context, verdict())
    exe = evidence.execution_plan(plan, {0:v})
    assert exe['issues'][0]['classification'] == 'contradiction'
    assert exe['issues'][0]['requested_chapters'] == [1]
    assert exe['issues'][0]['dependency_chapters'] == [2]
    assert exe['issues'][1]['dependency_chapters'] == [4]
    clean = evidence.validate_verdict(context, verdict('compatible'))
    assert evidence.execution_plan(plan,{0:clean})['issues'][0]['requested_chapters'] == []
    with pytest.raises(ValueError):
        evidence.validate_verdict(context, verdict() | {'writable_chapters':[4]})


def test_two_independent_author_passages_required_to_defer_a_real_conflict():
    source = SOURCE + '\n' + C
    plan = evidence.validate_plan(CANDIDATE, REVIEW, source, {'issues':[issue(), issue(1)]})
    context = evidence.conflict_context(source, plan['issues'][0])
    basis = {'subject':'The same original seal', 'circumstances':'Its fate in the same fire',
             'incompatibility':'The same seal is destroyed and survives intact.'}
    proof = verdict('confirmed_conflict') | {'source_quotes':[A,C], 'conflict_basis':basis}
    for bad in ([A,ASSUMPTION], [A,A], [A], [A, A[4:]]):
        with pytest.raises(ValueError):
            evidence.validate_verdict(context, proof | {'source_quotes':bad})
    with pytest.raises(ValueError):
        evidence.validate_verdict(context, proof | {'conflict_basis':None})
    with pytest.raises(ValueError):
        evidence.validate_verdict(context, proof | {'writable_chapters':[1]})
    verified = evidence.validate_verdict(context, proof)
    exe = evidence.execution_plan(plan, {0:verified})
    assert exe['issues'][0]['classification'] == 'source_conflict'
    assert exe['issues'][0]['requested_chapters'] == []
    assert exe['issues'][1]['requested_chapters'] == [3]
