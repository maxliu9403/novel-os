from copy import deepcopy

import pytest

from core import planning_residual_promises as promises


ORIGIN = 'At dawn the courier swears to return the compass.'
RECALL = 'At the station he remembers the promise.'
PAY1 = 'At the gates he gives her the compass.'
PAY2 = 'Inside she acknowledges the delivery.'
RESERVED = 'The map will burn when the compass returns.'
SOURCE = '\n'.join((ORIGIN, RECALL, PAY1, PAY2, RESERVED))


def book():
    chapters = [{'number':i, 'setups':[], 'payoffs':[], 'reserved_later_payoffs':[],
                 'scenes':[{'goal': f'Original scene {i}'}]} for i in range(1, 7)]
    chapters[0]['setups'] = [{'setup_id':'map','promise':'The map will burn.'}]
    chapters[2]['setups'] = [{'setup_id':'compass_station','horizon':'arc-level','promise':'A renewed vow.',
                              'intended_payoff_chapters':[5,6], 'legacy_field':'retain'}]
    chapters[3]['reserved_later_payoffs'] = [{'promise_id':'missing_finale','reserved_payoff':'The map burns.'}]
    chapters[4]['payoffs'] = [{'setup_id':'compass_station','payoff':'The compass returns.','extra':'retain'}]
    return {'assumptions':['Preserve original author interpretation.'], 'chapters':chapters,
            'ending_contract':{'plot_payoffs':[{'id':'burn_finale','setup_ids':['map'],'deadline':6}]},
            'promise_ledger':[{'setup_id':'map','setup_chapter':1,'promise':'The map will burn.'}]}


def item():
    return {'issue_index':0, 'classification':'continuity_gap','writable_chapters':[1,3,4,5,6],
            'affected_chapters':[1,3,4,5,6], 'dependency_chapters':[2], 'foundation_repairs':['payoff_binding'],
            'source_quotes':[ORIGIN,RECALL,PAY1,PAY2,RESERVED]}


FINDING = {'message':'Restore the compass promise from dawn and bind the missing finale.'}


def proposal():
    return {'registrations':[{'setup_id':'compass_station','previous_chapter':3,'origin_chapter':1,
                              'horizon':'arc-level','promise':'He promises to return the compass.',
                              'source_quote':ORIGIN,'events':[
                                  {'chapter':3,'status':'recalled','payoff':'He recalls his vow.',
                                   'source_quote':RECALL},
                                  {'chapter':5,'status':'paid','payoff':'He hands it over.','source_quote':PAY1},
                                  {'chapter':6,'status':'paid','payoff':'She accepts it.','source_quote':PAY2}]}],
            'bindings':[{'chapter':4,'field':'reserved_later_payoffs','index':0,'before':'missing_finale',
                         'target':'burn_finale','source_quote':RESERVED,'reason':'The registered map chain.'}]}


def test_move_legacy_setup_recall_it_and_activate_both_payoffs_in_a_single_copy():
    original = book()
    ctx = promises.build_context(original,FINDING,item(),SOURCE)
    fixed = promises.apply(original,ctx,proposal(),SOURCE)
    assert book() == original and fixed['assumptions'] == original['assumptions']
    assert fixed['chapters'][1] == original['chapters'][1]
    assert [c['scenes'] for c in fixed['chapters']] == [c['scenes'] for c in original['chapters']]
    assert fixed['chapters'][0]['setups'][0] == original['chapters'][0]['setups'][0]
    assert fixed['chapters'][0]['setups'][1]['legacy_field'] == 'retain'
    assert fixed['chapters'][2]['setups'] == []
    assert fixed['chapters'][2]['payoffs'][0]['promise_status'] == 'recalled'
    assert fixed['chapters'][4]['payoffs'][0]['extra'] == 'retain'
    assert [c['payoffs'][0]['setup_id'] for c in fixed['chapters'][4:]] == ['compass_station'] * 2
    assert fixed['promise_ledger'][-1]['setup_chapter'] == 1
    assert fixed['promise_ledger'][-1]['intended_payoff_chapters'] == [5,6]
    assert fixed['promise_ledger'][0] == original['promise_ledger'][0]
    assert fixed['chapters'][3]['reserved_later_payoffs'][0]['promise_id'] == 'burn_finale'
    assert fixed['ending_contract'] == original['ending_contract']


@pytest.mark.parametrize('change', ['held','fabricated','already_valid','unregistered','later','unplanted_chain',
                                      'duplicate_origin','wrong_previous','stale','repeat_slot','no_recall','read_only_event'])
def test_invalid_proposals_are_atomic(change):
    original, scope, patch = book(), item(), proposal()
    reg, link = patch['registrations'][0], patch['bindings'][0]
    if change == 'held':
        scope['writable_chapters'].remove(1)
    elif change == 'fabricated':
        reg['source_quote'] = 'He was an agent.'
    elif change == 'already_valid':
        original['chapters'][3]['reserved_later_payoffs'][0]['promise_id'] = 'map'
        link['before'] = 'map'
    elif change == 'unregistered':
        link['target'] = 'invented'
    elif change == 'later':
        original['chapters'][5]['setups'].append({'setup_id':'too_late','promise':'Late.'})
        link['target'] = 'too_late'
    elif change == 'unplanted_chain':
        original['ending_contract']['plot_payoffs'][0]['setup_ids'].append('unknown')
    elif change == 'duplicate_origin':
        original['chapters'][1]['setups'].append(deepcopy(original['chapters'][2]['setups'][0]))
    elif change == 'wrong_previous':
        reg['previous_chapter'] = None
    elif change == 'repeat_slot':
        patch['bindings'].append(deepcopy(link))
    elif change == 'no_recall':
        reg['events'][0]['status'] = 'paid'
    elif change == 'read_only_event':
        reg['events'][1]['chapter'] = 2
    ctx = promises.build_context(original,FINDING,scope,SOURCE)
    if change == 'stale':
        original['chapters'][4]['payoffs'][0]['payoff'] = 'Subsequent edit'
    unchanged = deepcopy(original)
    with pytest.raises(ValueError):
        promises.apply(original,ctx,patch,SOURCE)
    assert original == unchanged


def test_register_missing_origin_and_reject_implicit_out_of_scope_rebinding():
    original = book()
    original['chapters'][2]['setups'].clear()
    patch = proposal()
    patch['registrations'][0]['previous_chapter'] = None
    ctx = promises.build_context(original,FINDING,item(),SOURCE)
    fixed = promises.apply(original,ctx,patch,SOURCE)
    assert fixed['chapters'][0]['setups'][-1]['setup_id'] == 'compass_station'
    original['chapters'][1]['payoffs'].append({'setup_id':'compass_station'})
    ctx = promises.build_context(original,FINDING,item(),SOURCE)
    with pytest.raises(ValueError, match='implicitly rebind'):
        promises.apply(original,ctx,patch,SOURCE)


def test_legacy_discovery_once_checkpoint_replay_and_explicitly_unrelated_findings_skip_requests():
    class Planner:
        client = object()
        source = SOURCE
        def __init__(self):
            self.cached = {}
            self.calls = []
        def _prompt_fits(self,*args):
            return True
        def _load(self,name,deps,validate,**kw):
            return validate(self.cached[name]) if name in self.cached else None
        def _save(self,name,deps,value):
            self.cached[name] = deepcopy(value)
        def _call(self,name,instruction,context,validate,**kw):
            self.calls.append(instruction.splitlines()[0])
            return validate({'issue_indices':[0]} if instruction == promises.DISCOVERY else proposal())
    p, c, scope = Planner(), book(), item()
    del scope['foundation_repairs']
    rev = {'issues':[FINDING,{'message':'The empty cup was already full.'}]}
    evidence = {'issues':[scope,{**scope,'issue_index':1,'foundation_repairs':[]}]}
    first = promises.repair(p,c,rev,evidence,blocked_indices=set())
    assert first == promises.repair(p,c,rev,evidence,blocked_indices=set())
    assert promises.repair(p,c,rev,evidence,blocked_indices={0}) == (c,[])
    assert p.calls == [promises.DISCOVERY.splitlines()[0],promises.INSTRUCTION.splitlines()[0]]


def test_held_chapter_remains_read_only_even_if_another_finding_asks_to_write_it():
    class Planner:
        source = SOURCE
        client = object()
        def _prompt_fits(self,*args):
            return True
        def _load(self,*a,**k):
            return None
        def _save(self,*a,**k):
            pass
        def _call(self,name,instruction,context,validate,**kw):
            assert context['writable_chapters'] == [1,3,4,5]
            assert context['binding_author_decision'] == 'The heir accepts the delivery.'
            return validate(proposal())
    evidence = {'issues':[{**item(),'binding_author_decision':'The heir accepts the delivery.'},
                          {**item(),'issue_index':1,'classification':'source_conflict','affected_chapters':[6],
                           'writable_chapters':[]}]}
    with pytest.raises(ValueError,match='authorized'):
        promises.repair(Planner(),book(),{'issues':[FINDING,FINDING]},evidence,blocked_indices={1})


def test_numeric_source_ids_expand_to_exact_original_evidence_and_replay():
    original, scope, patch = book(), item(), proposal()
    registration = patch['registrations'][0]
    registration['source_quote'] = 0
    for index, event in enumerate(registration['events'], 1):
        event['source_quote'] = index
    patch['bindings'][0]['source_quote'] = 4
    ctx = promises.build_context(original, FINDING, scope, SOURCE)
    assert promises.apply(original, ctx, patch, SOURCE) == promises.apply(original, ctx, proposal(), SOURCE)
    assert patch['registrations'][0]['source_quote'] == 0
    for invalid in (-1, 5, True):
        patch['registrations'][0]['source_quote'] = invalid
        with pytest.raises(ValueError):
            promises.apply(original, ctx, patch, SOURCE)


def test_existing_embedded_origin_and_global_mirror_follow_restored_stable_identity():
    original = book()
    original['chapters'][2]['setups'][0]['setup_chapter'] = 3
    original['promise_ledger'].append({'setup_id':'compass_station','setup_chapter':3,
                                       'promise':'A renewed vow.', 'retained_note':'Keep the receipt.'})
    ctx = promises.build_context(original, FINDING, item(), SOURCE)
    fixed = promises.apply(original, ctx, proposal(), SOURCE)
    assert fixed['chapters'][0]['setups'][-1]['setup_chapter'] == 1
    assert fixed['promise_ledger'][-1]['setup_chapter'] == 1
    assert fixed['promise_ledger'][-1]['retained_note'] == 'Keep the receipt.'
    assert fixed['chapters'][2]['payoffs'][0]['promise_status'] == 'recalled'
    assert fixed['chapters'][4]['payoffs'][0]['promise_status'] == 'paid'
    assert fixed['chapters'][5]['payoffs'][0]['promise_status'] == 'paid'


def test_missing_setup_can_repair_explicitly_authorized_same_id_dangling_reference():
    original, patch = book(), proposal()
    original['chapters'][2]['setups'].clear()
    original['chapters'][3]['reserved_later_payoffs'][0]['promise_id'] = 'compass_station'
    patch['registrations'][0]['previous_chapter'] = None
    patch['bindings'][0].update(before='compass_station', target='compass_station')
    ctx = promises.build_context(original, FINDING, item(), SOURCE)
    fixed = promises.apply(original, ctx, patch, SOURCE)
    assert fixed['chapters'][3]['reserved_later_payoffs'][0]['promise_id'] == 'compass_station'
    assert fixed['promise_ledger'][-1]['setup_id'] == 'compass_station'
    patch['bindings'].clear()
    with pytest.raises(ValueError, match='implicitly rebind'):
        promises.apply(original, ctx, patch, SOURCE)


def test_previous_author_decisions_reach_registry_context():
    scope = {**item(), 'prior_author_choices':[{'choice_id':'accepted', 'author_quote':ORIGIN}],
             'binding_author_decision': {'choice_id':'current', 'author_quote':RECALL}}
    ctx = promises.build_context(book(), FINDING, scope, SOURCE)
    assert ctx['prior_author_choices'] == scope['prior_author_choices']
    assert ctx['binding_author_decision'] == scope['binding_author_decision']


def test_register_missing_setup_accepts_existing_runtime_foreshadowing_identity_format():
    original, patch = book(), proposal()
    original['chapters'][2]['setups'].clear()
    original['chapters'][4]['payoffs'].clear()
    patch['registrations'][0].update(setup_id='ch1:fs1', previous_chapter=None)
    ctx = promises.build_context(original, FINDING, item(), SOURCE)
    fixed = promises.apply(original, ctx, patch, SOURCE)
    assert fixed['chapters'][0]['setups'][-1]['setup_id'] == 'ch1:fs1'
    assert fixed['chapters'][2]['payoffs'][0]['setup_id'] == 'ch1:fs1'
    assert fixed['chapters'][2]['payoffs'][0]['promise_status'] == 'recalled'
