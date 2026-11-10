from copy import deepcopy
import pytest

from core import planning_foundation_links as links

SOURCE = 'The station clerk keeps the ring. The clerk returns it at closing.'
QUOTE = 'The station clerk keeps the ring.'


def fixture():
    return {'characters':[{'id':'mara','name':'Mara','aliases':['M.'],'role':'Archivist'}],
            'promise_ledger': [],
            'chapters': [
                {'number':1,'summary':'Mara meets the station clerk.', 'character_ids':['mara'],
                 'setups':[{'setup_id':'ring_held','promise':'The clerk keeps the ring.'}],
                 'payoffs':[]},
                {'number':2,'summary':'The station clerk returns the ring.', 'character_ids':['mara'],
                 'setups':[],'payoffs':[{'setup_id':'ring_held','payoff':'The ring returns.'},
                                        {'setup_id':'ring_on_file','payoff':'She receives the ring.'}]}]}


def test_same_book_scope_cannot_change_existing_identities_valid_links_or_another_chapter():
    candidate = fixture()
    item = {'issue_index':0, 'classification':'contradiction', 'source_quotes':[QUOTE],
            'affected_chapters':[1,2], 'writable_chapters':[2], 'dependency_chapters':[1]}
    issue = {'message':'Add the missing clerk and bind ring_on_file.'}
    context = links.build_context(candidate, issue, item, SOURCE, ['character_registration','payoff_binding'])
    assert [s['before'] for s in context['dangling_payoffs']] == ['ring_on_file']
    proposal = {'characters':[{'id':'clerk','name':'station clerk','source_label':'station clerk',
                                'appearances':[{'chapter':2,'quote':'The clerk returns it at closing.'}]}],
                'payoff_links':[{'chapter':2,'index':1,'before':'ring_on_file','setup_id':'ring_held',
                                   'source_quote':'The clerk returns it at closing.',
                                   'reason':'The returned ring pays off the ring held by the clerk.'}]}
    derived = links.apply(candidate, context, proposal)
    assert derived['chapters'][1]['character_ids']==['mara','clerk']
    assert derived['characters'][1]['pronouns']=='' and derived['characters'][1]['name']=='station clerk'
    assert derived['chapters'][1]['payoffs'][1]['setup_id']=='ring_held'
    assert derived['chapters'][0]==candidate['chapters'][0] and derived['characters'][0]==candidate['characters'][0]
    assert candidate==fixture()
    for patch in [{'index':0,'before':'ring_held'}, {'chapter':1}, {'setup_id':'invented'},
                  {'source_quote':'A quote the author never wrote.'}]:
        invalid=deepcopy(proposal)
        invalid['payoff_links'][0].update(patch)
        with pytest.raises(ValueError):
            links.apply(candidate,context,invalid)
    for patch in [{'name':'M.'},{'id':'mara'},{'id':'..'},{'name':'Madeup Spanner'},
                  {'appearances':[{'chapter':1,'quote':QUOTE}]}]:
        invalid=deepcopy(proposal)
        invalid['characters'][0].update(patch)
        with pytest.raises(ValueError):
            links.apply(candidate,context,invalid)


def test_duplicate_definitions_and_links_outside_specific_review_scope_are_read_only():
    candidate=fixture()
    candidate['chapters'][0]['setups'].append({'setup_id':'ring_held','promise':'A different promise.'})
    candidate['chapters'][1]['payoffs'].append({'setup_id':'unrelated','payoff':'A different payoff.'})
    item={'issue_index':0,'writable_chapters':[2],'dependency_chapters':[1], 'source_quotes':[QUOTE]}
    context=links.build_context(candidate,{'message':'Repair ring_on_file.'},item,SOURCE,['payoff_binding'])
    assert [s['before'] for s in context['dangling_payoffs']]==['ring_on_file']
    assert not context['eligible_setups']
    assert links.apply(candidate,context,{'characters':[],'payoff_links':[]})==candidate


def test_registry_requests_never_rewrite_a_held_chapter_or_ask_author_on_ambiguity():
    class Planner:
        source=SOURCE
        chapter_count=2
        client=object()
        def _load(self,*a,**kw): return None
        def _save(self,*a): pass
        def _prompt_fits(self,*a,**kw): return True
        def _call(self,name,policy,context,validate,**kw):
            assert policy.startswith('STAGE: planning_registry_repair')
            return validate({'characters':[],'payoff_links':[]})
    issue={'issue_index':0,'classification':'continuity_gap','affected_chapters':[2],
           'writable_chapters':[2],'dependency_chapters':[1],'source_quotes':[QUOTE],
           'foundation_repairs':['payoff_binding']}
    c=fixture()
    rev={'issues':[{'message':'Check ring_on_file.'}]}
    for blocked in ({0},set()):
        result,audit=links.repair(Planner(),c,rev,{'issues':[issue]},blocked_indices=blocked)
        assert result==c
        assert len(audit)==(0 if blocked else 1)


def test_legacy_findings_discover_registry_semantically_once_and_replay_saved_child():
    class Planner:
        source = SOURCE
        client = object()
        def __init__(self):
            self.cache = {}
            self.stages = []
        def _prompt_fits(self, *args):
            return True
        def _load(self, name, deps, validate, **kwargs):
            return validate(self.cache[name]) if name in self.cache else None
        def _save(self, name, deps, data):
            self.cache[name] = deepcopy(data)
        def _call(self, name, policy, ctx, validate, **kwargs):
            self.stages.append(policy.splitlines()[0])
            if policy == links.DISCOVERY:
                assert ctx['issues'][0]['finding']['message'] == 'Rétablir la liaison de la promesse.'
                return validate({'issues':[{'issue_index':0,'foundation_repairs':['payoff_binding']}]})
            dangling = ctx['dangling_payoffs']
            assert len(dangling) == 1
            return validate({'characters':[], 'payoff_links':[{
                'chapter':2,'index':dangling[0]['index'],'before':dangling[0]['before'],
                'setup_id':'ring_held','reason':'The same ring is returned.','source_quote':QUOTE}]})
    p=Planner()
    c=fixture()
    item={'issue_index':0,'classification':'continuity_gap','writable_chapters':[2],
          'affected_chapters':[2], 'dependency_chapters':[1],'source_quotes':[QUOTE]}
    review={'issues':[{'message':'Rétablir la liaison de la promesse.'}]}
    a=links.repair(p,c,review,{'issues':[item]},blocked_indices=set())
    b=links.repair(p,c,review,{'issues':[item]},blocked_indices=set())
    assert a==b and c==fixture()
    assert a[0]['chapters'][1]['payoffs'][1]['setup_id']=='ring_held'
    assert p.stages == [links.DISCOVERY.splitlines()[0],links.INSTRUCTION.splitlines()[0]]


def test_new_global_character_cannot_activate_an_unrelated_dangling_reference():
    candidate=fixture()
    candidate['chapters'][0]['character_ids'].append('clerk')
    item={'issue_index':0,'writable_chapters':[2],'dependency_chapters':[], 'source_quotes':[QUOTE]}
    ctx=links.build_context(candidate,{},item,SOURCE,['character_registration'])
    patch={'characters':[{'id':'clerk','name':'station clerk','source_label':'station clerk',
                           'appearances':[{'chapter':2,'quote':'The clerk returns it at closing.'}]}],
           'payoff_links':[]}
    with pytest.raises(ValueError,match='out-of-scope'):
        links.apply(candidate,ctx,patch)
