import copy

import pytest

from core import planning_residual_edits as narrative


class Planner:
    client = object()
    source = 'The door stays closed. It bears a blue seal.\n\nThe messenger goes unnamed.'

    def __init__(self, response, *, max_bytes=None):
        self.response = response
        self.max_bytes = max_bytes
        self.calls, self.saved = [], {}
        self.evidence, self.directions = [], []

    def _prompt_fits(self, text, *_):
        return self.max_bytes is None or len(text.encode()) <= self.max_bytes

    def _load(self, name, deps, validate, **kwargs):
        assert kwargs['strict_dependencies']
        if name in self.saved:
            saved_deps, value = self.saved[name]
            assert saved_deps == deps
            return validate(copy.deepcopy(value))

    def _save(self, name, deps, value):
        self.saved[name] = (deps, copy.deepcopy(value))

    def _call(self, name, instruction, context, validate, **kwargs):
        assert kwargs['strict_dependencies']
        assert kwargs['recover_archived']
        if 'evidence_fields' in context:
            self.evidence.append(copy.deepcopy(context))
            # Select the actual witness in the read-only chapter; the editor
            # must be able to inspect its original path and exact bytes.
            return validate({'indices': [i for i,(path, _) in enumerate(context['evidence_fields'])
                                         if path == ['chapters', 1, 'summary']]})
        if 'selected_scoped_fields' in context or 'scoped_material' in context:
            self.directions.append(copy.deepcopy(context))
            return validate({'direction':'Preserve the seal and the existing witness account.',
                             'reason':'The door stays closed.', 'source_indices':[0]})
        self.calls.append(copy.deepcopy(context))
        return validate(self.response(context))


def book():
    return {'chapter_map': [{'number': 2, 'ending_obligations': ['a seal is broken.']},
                            {'number': 1, 'ending_obligations': ['a seal is broken.']}],
            'chapters': [{'number': 1, 'summary': 'a seal is broken.',
                          'continuity_obligations': ['a seal is broken. Keep the letter.'],
                          'scenes': [{'pov_character_id': 'id_closed', 'continuity': ['a seal is broken.']}],
                          'execution_contract': {'preserve_facts': ['a seal is broken.'],
                                                 'reader_jobs': ['belonging']},
                          'setups': [{'setup_id': 'registered', 'promise': 'a seal is broken.'}],
                          'reserved_later_payoffs': [{'promise_id': 'registered'}, 'a seal is broken.']},
                         {'number': 2, 'summary': 'a seal is broken.'}],
            'structural_risks': ['a seal is broken.', 'Keep the storm.'],
            'assumptions': ['a seal is broken.'], 'source_anchors': [{'id': 'a', 'quote': 'a seal is broken.'}],
            'ending_contract': {'resolution': 'a seal is broken.'}}


def issue(index=0, **extra):
    return {'issue_index': index, 'classification': 'contradiction', 'reason': 'Preserve the seal.',
            'affected_chapters': [1, 2], 'writable_chapters': [1], 'dependency_chapters': [2],
            'source_quotes': ['The door stays closed.'], 'candidate_evidence': [], **extra}


def respond(ctx):
    return {'edits': [{'path': path, 'old_span': 'is broken', 'replacement': 'stays intact',
                       'source_indices': [0]}
                      for path, text in ctx['fields'] if 'is broken' in text],
            'reason': 'Keep the door and its seal intact.'}


def repair(planner, candidate, issues, *, blocked=()):
    review = {'issues': [{'message': 'The seal must remain unbroken.'} for _ in issues]}
    return narrative.repair(planner, candidate, review, {'issues': issues}, blocked_indices=set(blocked))


def test_closes_global_roadmap_and_nested_mirrors_with_one_request_and_audit():
    p, candidate = Planner(respond), book()
    new, audits = repair(p, candidate, [issue()])
    assert len(p.calls) == len(audits) == 1 and candidate == book()
    assert [['chapters', 1], candidate['chapters'][1]] in p.calls[0]['scoped_evidence']
    assert [['chapter_map', 0], candidate['chapter_map'][0]] in p.calls[0]['scoped_evidence']
    assert new['chapters'][1] == candidate['chapters'][1]
    assert new['chapter_map'][0] == candidate['chapter_map'][0]
    assert new['chapter_map'][1]['ending_obligations'] == ['a seal stays intact.']
    assert new['chapters'][0]['continuity_obligations'] == ['a seal stays intact. Keep the letter.']
    assert new['chapters'][0]['execution_contract']['preserve_facts'] == ['a seal stays intact.']
    assert new['chapters'][0]['scenes'][0] == {'pov_character_id':'id_closed', 'continuity':['a seal stays intact.']}
    assert new['chapters'][0]['setups'] == [{'setup_id': 'registered', 'promise': 'a seal stays intact.'}]
    assert new['chapters'][0]['reserved_later_payoffs'] == [{'promise_id': 'registered'}, 'a seal stays intact.']
    assert new['structural_risks'] == ['a seal stays intact.', 'Keep the storm.']
    assert new['assumptions'] == ['a seal stays intact.']
    assert new['ending_contract'] == candidate['ending_contract']
    assert audits[0]['edits'] and all(edit['source_quotes'] == ['The door stays closed.']
                                      for edit in audits[0]['edits'])
    assert p.calls[0]['chapter_numbers_by_position']['chapter_map'] == {'1': 1}


def test_stopped_window_resumes_from_receipts_and_does_not_trim_fields(monkeypatch):
    monkeypatch.setattr(narrative, '_PAGE_BYTES', 250)
    p, candidate = Planner(respond), book()

    def stop_second(ctx):
        if len(p.calls) == 2:
            raise RuntimeError('stopped')
        return respond(ctx)

    p.response = stop_second
    with pytest.raises(RuntimeError, match='stopped'):
        repair(p, candidate, [issue()])
    assert candidate == book()
    assert sum(key.startswith('residual-narrative-') and not key.endswith('-split') for key in p.saved) == 1
    p.response = respond
    new, receipts = repair(p, candidate, [issue()])
    inspected = [tuple(path) for page in [p.calls[0], *p.calls[2:]] for path, _ in page['fields']]
    assert len(set(inspected)) == len(inspected) == sum(r['inspected_fields'] for r in receipts)
    assert new['chapters'][0]['continuity_obligations'] == ['a seal stays intact. Keep the letter.']
    assert len(p.directions) == 1 and p.evidence
    assert all(page['shared_edit_direction'] == p.calls[0]['shared_edit_direction'] for page in p.calls)
    assert all([[ 'chapters', 1, 'summary'], 'a seal is broken.'] in ctx['scoped_evidence']
               for ctx in p.calls)
    assert all(receipt['direction_checkpoint'] == receipts[0]['direction_checkpoint'] for receipt in receipts)
    all_inspected = [path for ctx in p.evidence for path, _ in ctx['evidence_fields']]
    assert ['chapters', 0, 'setups', 0, 'setup_id'] in all_inspected
    assert ['chapter_map', 0, 'ending_obligations', 0] in all_inspected
    source_rows = [row for ctx in p.evidence for row in ctx['evidence_fields'] if row[0][0] == 'adopted_source']
    assert ''.join(value for _, value in source_rows) == p.source
    assert all(p.source[path[1]:path[2]] == value for path, value in source_rows)
    calls = len(p.calls)
    assert repair(p, candidate, [issue()]) == (new, receipts) and len(p.calls) == calls


def test_completed_direct_request_is_reused_when_capacity_shrinks():
    p, candidate = Planner(respond), book()
    first = repair(p, candidate, [issue()])
    p.max_bytes = 1
    assert repair(p, candidate, [issue()]) == first
    assert len(p.calls) == 1 and not p.evidence and not p.directions


def test_interrupted_partition_remains_frozen_when_capacity_increases(monkeypatch):
    monkeypatch.setattr(narrative, '_PAGE_BYTES', 250)
    p, candidate = Planner(respond), book()

    def stop(ctx):
        if len(p.calls) == 2:
            raise RuntimeError('second editor request did not finish')
        return respond(ctx)

    p.response = stop
    with pytest.raises(RuntimeError, match='did not finish'):
        repair(p, candidate, [issue()])
    completed = copy.deepcopy(p.calls[0])
    seen = [row for ctx in p.evidence for row in ctx['evidence_fields']]
    monkeypatch.setattr(narrative, '_PAGE_BYTES', 10_000_000)
    p.response = respond
    new, _ = repair(p, candidate, [issue()])
    assert completed not in p.calls[2:]
    assert seen == [row for ctx in p.evidence for row in ctx['evidence_fields']]
    assert len(p.directions) == 1
    assert new['chapters'][0]['summary'] == 'a seal stays intact.'


def test_global_contracts_and_author_choices_reach_both_editor_modes(monkeypatch):
    for page_bytes in (250, 48 * 1024):
        monkeypatch.setattr(narrative, '_PAGE_BYTES', page_bytes)
        p = Planner(respond)
        choices = [{'choice_id': 'verified-choice', 'selected_author_quote': 'The door stays closed.'}]
        decision = {'selected_author_quote': 'The messenger goes unnamed.'}
        repair(p, book(), [issue(prior_author_choices=choices, binding_author_decision=decision)])
        contexts = [*p.evidence, *p.directions, *p.calls]
        assert contexts and all(ctx['prior_author_choices'] == choices and
                                ctx['binding_author_decision'] == decision for ctx in contexts)
        if p.evidence:
            inspected = [path for ctx in p.evidence for path, _ in ctx['evidence_fields']]
            assert ['ending_contract', 'resolution'] in inspected
            assert ['source_anchors', 0, 'quote'] in inspected
        else:
            assert [['ending_contract'], book()['ending_contract']] in p.calls[0]['scoped_evidence']


def test_nested_promise_text_has_editable_narrative_and_read_only_identity():
    candidate = book()
    chapter = candidate['chapters'][0]
    chapter['payoffs'] = [{'setup_id': 'registered', 'payoff': 'a seal is broken.', 'promise_status': 'paid'}]
    chapter['reserved_later_payoffs'][0]['reserved_payoff'] = 'a seal is broken.'
    p = Planner(respond)
    new, _ = repair(p, candidate, [issue()])
    assert new['chapters'][0]['payoffs'] == [{'setup_id': 'registered', 'payoff': 'a seal stays intact.',
                                          'promise_status': 'paid'}]
    assert new['chapters'][0]['reserved_later_payoffs'][0] == {'promise_id': 'registered',
                                                            'reserved_payoff': 'a seal stays intact.'}
    assert all(path[-1] not in ('promise_id', 'setup_id', 'promise_status') for path, _ in p.calls[0]['fields'])


def test_scoped_source_keeps_full_global_text_and_exact_read_dependency_chapters():
    from core.planning_residual_evidence import scoped_material
    from core.skeleton_projection import chapter_locations
    from test_skeleton_projection import source

    p, candidate = Planner(respond), book()
    p.source = source(count=2)
    rows, readonly, selected = scoped_material(p, candidate, issue())
    locations = chapter_locations(p.source, 2)
    assert ''.join(value for _, value in sorted(rows, key=lambda row: row[0][1])) == p.source
    assert all(p.source[path[1]:path[2]] == value for path, value in rows)
    assert any(path[1:] == locations[2]['source_chars'] for path, _ in rows)
    assert [['chapters', 1], candidate['chapters'][1]] in readonly
    assert [['ending_contract'], candidate['ending_contract']] in selected


def test_held_global_field_remains_visible_as_a_read_only_constraint():
    candidate = book()
    held = issue(1, affected_chapters=[2], writable_chapters=[], classification='source_conflict',
                 candidate_evidence=[{'path':'/assumptions/0','quote':'is broken'}])
    p = Planner(respond)
    new, _ = repair(p, candidate, [issue(), held], blocked=[1])
    assert new['assumptions'] == candidate['assumptions']
    assert [['assumptions', 0], candidate['assumptions'][0]] in p.calls[0]['scoped_evidence']


@pytest.mark.parametrize('change', [
    {'path': ['chapters', 1, 'summary']}, {'path': ['chapters', 0, 'scenes', 0, 'pov_character_id']},
    {'path': ['chapters', 0, 'execution_contract', 'reader_jobs', 0]},
    {'path': ['ending_contract', 'resolution']}, {'path': ['chapters', False, 'summary']},
    {'path': ['chapter_map', 0, 'ending_obligations', 0]}, {'source_indices': [False]},
    {'source_indices': []}, {'source_indices': [1]}, {'old_span': 'impossible quote'},
    {'old_span': 'a seal is broken.', 'replacement': ' '},
])
def test_out_of_scope_stale_or_unsourced_edits_rejected_atomically(change):
    first = {'path': ['chapters',0,'summary'], 'old_span': 'is broken', 'replacement':'stays intact',
             'source_indices':[0]}
    second = {'path': ['structural_risks', 0], 'old_span': 'is broken', 'replacement':'stays intact',
              'source_indices':[0], **change}
    p = Planner(lambda _: {'reason':'Cited the source.', 'edits':[first, second]})
    candidate = book()
    with pytest.raises(ValueError):
        repair(p, candidate, [issue()])
    assert candidate == book() and not p.saved


def test_separate_findings_cannot_grant_each_other_write_access_or_evidence():
    second = issue(1, writable_chapters=[2], dependency_chapters=[1], source_quotes=['The messenger goes unnamed.'])
    checked = []
    def verify(ctx):
        allowed = ctx['writable_chapters'][0]
        assert all(path[1] == allowed - 1 for path, _ in ctx['fields'] if path[0] == 'chapters')
        checked.append((allowed, ctx['adopted_passages']))
        return {'reason':'No change warranted.', 'edits':[]}
    candidate = book()
    new, audits = repair(Planner(verify), candidate, [issue(), second])
    assert new == candidate and len(audits) == 2
    assert checked == [(1, ['The door stays closed.']), (2, ['The messenger goes unnamed.'])]


def test_global_only_and_held_authority_and_exact_source_protection():
    p = Planner(respond)
    held = issue(1, affected_chapters=[1], writable_chapters=[], classification='source_conflict',
                 candidate_evidence=[{'path':'/assumptions/0','quote':'is broken'}])
    new, _ = repair(p, book(), [issue(), held], blocked=[1])
    assert new['structural_risks'][0] == 'a seal stays intact.'
    assert new['assumptions'] == book()['assumptions'] and new['chapters'] == book()['chapters']
    assert p.calls[0]['writable_chapters'] == []
    for quotes in [[], ['a seal is broken.']]:
        with pytest.raises(ValueError, match='evidence'):
            repair(p, book(), [issue(source_quotes=quotes)])
