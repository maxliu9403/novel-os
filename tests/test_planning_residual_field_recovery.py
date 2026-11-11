from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from core import planning_residual_field_recovery as recovery
from core.outline_planning import _json


def context():
    return {'finding': {'message': 'Preserve the unidentified witness and the sealed door.'},
            'resolution_basis': 'Restore the stated uncertainty and preserve the existing seal.',
            'adopted_passages': ['The witness remains unidentified.', 'The door stays closed.'],
            'source_paragraphs': ['The witness remains unidentified. The door stays closed.'],
            'prior_author_choices': [{'choice_id': 'verified-choice'}],
            'fields': [[['chapters', 0, 'summary'], 'A witness remains unnamed beside the sealed door.'],
                       [['chapters', 0, 'goal'], 'Keep the seal broken.'],
                       [['chapters', 0, 'conflict'], 'Turn back, then turn back.']]}


def response():
    return {'reason': 'Preserve the source-supported uncertainty and the door.', 'edits': [
        {'path': ['chapters', 0, 'summary'], 'old_span': 'is distinct from',
         'replacement': 'may be the same person as', 'source_indices': [0]},
        {'path': ['chapters', 0, 'goal'], 'old_span': 'broken',
         'replacement': 'intact', 'source_indices': [1]}]}


class Planner:
    def __init__(self, callback=None, max_bytes=None):
        self.client = SimpleNamespace()
        self.saved, self.calls = {}, []
        self.callback, self.max_bytes = callback, max_bytes

    def _prompt_fits(self, prompt, stage, limit):
        return self.max_bytes is None or len(prompt.encode()) <= self.max_bytes

    def _load(self, name, deps, validate, **options):
        assert options['strict_dependencies']
        if name in self.saved:
            saved_deps, value = self.saved[name]
            assert saved_deps == deps
            return validate(deepcopy(value))

    def _save(self, name, deps, value):
        self.saved[name] = (deepcopy(deps), deepcopy(value))

    def _call(self, name, instruction, page, validate, **options):
        assert options['strict_dependencies'] and options['recover_archived']
        assert 'repair_fields' not in options
        self.calls.append(deepcopy(page))
        value = (self.callback(page) if self.callback else
                 {'fields': [{'index': row['index'], 'value': row['current_value'] + ' Their identity remains uncertain.'}
                             for row in page['fields']]})
        return validate(value)


def test_one_unbindable_span_preserves_all_valid_edits_and_reuses_field_receipt():
    p, ctx, original = Planner(), context(), response()
    raw = _json(original)
    fixed = recovery.repair(p, raw, ctx, None)
    assert fixed['edits'][1:] == original['edits'][1:]
    assert fixed['reason'] == original['reason']
    assert fixed['edits'][0]['path'] == original['edits'][0]['path']
    assert fixed['edits'][0]['source_indices'] == [0]
    assert fixed['edits'][0]['old_span'] == ctx['fields'][0][1]
    assert p.calls[0]['fields'] == [{'index': 0, 'current_value': ctx['fields'][0][1],
                                   'proposed_edit': original['edits'][0]}]
    assert p.calls[0]['prior_author_choices'] == ctx['prior_author_choices']
    assert recovery.repair(p, raw, ctx, None) == fixed and len(p.calls) == 1
    assert ctx == context() and original == response()


def test_ambiguous_repeated_span_requires_model_full_field_value():
    original = response()
    original['edits'][0].update(path=['chapters', 0, 'conflict'], old_span='turn back', replacement='wait')
    ctx = context()
    ctx['fields'][2][1] = 'turn back, then turn back.'
    p = Planner()
    fixed = recovery.repair(p, json.dumps(original), ctx, None)
    assert fixed['edits'][0]['old_span'] == ctx['fields'][2][1]
    assert len(p.calls) == 1


@pytest.mark.parametrize('change', [
    {'path': ['chapters', 1, 'summary']}, {'path': ['chapters', False, 'summary']},
    {'source_indices': []}, {'source_indices': [False]}, {'source_indices': [2]},
    {'old_span': ''}, {'old_span': None}, {'replacement': None},
])
def test_invalid_authority_or_structure_cannot_enter_field_recovery(change):
    original, p = response(), Planner()
    original['edits'][0].update(change)
    assert recovery.repair(p, json.dumps(original), context(), None) is None
    assert not p.calls and not p.saved


def test_duplicate_paths_and_invalid_valid_sibling_cannot_be_hidden_by_span_recovery():
    for mutate in (
        lambda rows: rows.append(deepcopy(rows[0])),
        lambda rows: rows[1].update(source_indices=[True]),
        lambda rows: rows[1].update(path=['chapters', 2, 'goal']),
        lambda rows: rows[1].update(old_span='Keep the seal broken.', replacement=''),
    ):
        original, p = response(), Planner()
        mutate(original['edits'])
        assert recovery.repair(p, json.dumps(original), context(), None) is None
        assert not p.calls


@pytest.mark.parametrize('reply', [
    {'fields': []}, {'fields': [{'index': 1, 'value': 'unauthorized'}]},
    {'fields': [{'index': False, 'value': 'boolean index'}]},
    {'fields': [{'index': 0, 'value': ''}]},
    {'fields': [{'index': 0, 'value': 'changed'}, {'index': 0, 'value': 'duplicated'}]},
    {'fields': [{'index': 0, 'value': 'changed', 'path': ['new path']}]},
])
def test_response_cannot_change_unrequested_fields_or_invent_bindings(reply):
    original = response()
    with pytest.raises(ValueError, match='Field recovery'):
        recovery.repair(Planner(lambda _: reply), json.dumps(original), context(), None)
    assert original == response()


def test_interrupted_multi_field_recovery_replays_completed_pages_without_truncating():
    original, ctx = response(), context()
    original['edits'][1]['old_span'] = 'wrong guessed substring'
    ctx['fields'][0][1] += 'Complete qualifying detail. ' * 150
    ctx['fields'][1][1] += 'A different complete qualifier. ' * 150
    p = Planner(max_bytes=8_000)

    def stop(page):
        if len(p.calls) == 2:
            raise RuntimeError('interrupted second field')
        return {'fields': [{'index': row['index'], 'value': row['current_value'] + ' Keep the source constraint.'}
                           for row in page['fields']]}

    p.callback = stop
    with pytest.raises(RuntimeError, match='interrupted second field'):
        recovery.repair(p, json.dumps(original), ctx, None)
    p.callback = None
    fixed = recovery.repair(p, json.dumps(original), ctx, None)
    assert len(p.calls) == 3 and p.calls[1] == p.calls[2]
    assert fixed['edits'][0]['old_span'] == ctx['fields'][0][1]
    assert fixed['edits'][1]['old_span'] == ctx['fields'][1][1]
    assert all(len(_json(page).encode()) < p.max_bytes for page in p.calls)


def test_different_completed_response_gets_a_distinct_recovery_lineage():
    p, original = Planner(), response()
    recovery.repair(p, json.dumps(original), context(), None)
    original['reason'] = 'The newest completed correction has this exact explanation.'
    recovery.repair(p, json.dumps(original), context(), None)
    assert len(p.calls) == 2
    assert p.calls[0]['completed_response_sha256'] != p.calls[1]['completed_response_sha256']
