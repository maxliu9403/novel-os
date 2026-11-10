"""Exercise a mixed review through real checkpointing, independent work and author resumption."""
from copy import deepcopy
import json

import pytest

from core import planning_evidence_plan as evidence
from core.planning_author_decisions import save_choices
from core.planning_coordinator import independent_groups
from core.planning_group_fields import expand
from outline_planning import OutlinePlanningError
from test_outline_coordinated_repair import CoordinatedModel, QUOTE, original_checkpoints, assert_originals_retained
from test_outline_planning import SOURCE, make_planner, review

YES = 'At noon Mara owns the studio.'
NO = 'At that same noon the buyer alone owns the studio.'
ORIGINAL = SOURCE + ' ' + YES + ' ' + NO


def finding(n):
    return {'scope':'chapter', 'chapter':n, 'message':f'Restore the original consequence at {n}.',
            'source_evidence':QUOTE}


def proposal(index, n, *, conflict=False, reads=()):
    return {'issue_index':index,'classification':'source_conflict' if conflict else 'continuity_gap',
            'reason':'Check the two incompatible owners.' if conflict else 'Preserve the refusal.',
            'affected_chapters':[n], 'writable_chapters':[] if conflict else [n],
            'dependency_chapters':list(reads),'source_quotes':[YES, NO] if conflict else [QUOTE],
            'candidate_evidence':[],'foundation_repairs':[]}


class DecisionModel(CoordinatedModel):
    def complete(self, system, user):
        ctx = json.loads(user.rsplit('\n\n', 1)[1])
        if user.startswith(evidence.CONFLICT_INSTRUCTION):
            self.calls.append(('conflict_verify', ctx))
            return json.dumps({'verdict':'confirmed_conflict','reason':'Two sole owners at the same time.',
                'source_quotes':[YES,NO],'conflict_basis':{'subject':'Sole owner of studio',
                'circumstances':'At noon','incompatibility':'Exclusive ownership cannot belong to both.'},
                'writable_chapters':[]})
        if user.startswith(evidence.PLAN_INSTRUCTION) and ctx.get('binding_author_decision'):
            self.calls.append(('choice_plan', ctx))
            return json.dumps({'issues':[proposal(0, 2)]})
        return super().complete(system,user)


def test_independent_repairs_complete_once_before_and_after_a_verified_author_decision(tmp_path):
    project = tmp_path / 'book'
    run = project / 'outputs/runs/test-run'
    source_path = project / 'outputs/input/prompt.md'
    source_path.parent.mkdir(parents=True)
    source_path.write_text(ORIGINAL)
    plan = {'issues':[proposal(0, 2, conflict=True),proposal(1, 3),proposal(2, 9)]}
    findings = review({'scope':'global','message':'Resolve the specified two owners.','source_evidence':YES},
                       finding(3),finding(9))
    model = DecisionModel(plan=plan, reviews=[findings, review()])
    planner = make_planner(run, model, count=12, source=ORIGINAL)
    with pytest.raises(OutlinePlanningError):
        planner.run()
    assert [(stage,c['requested_chapters']) for stage,c in model.calls if stage=='coordinated_group'] == [
        ('coordinated_group',[9])]
    assert planner.progress['completed_issue_indices'] == [2]
    assert planner.progress['deferred_issue_indices'] == [0,1]
    assert planner.progress['recovery']['action'] == 'choose_author_source'
    baseline = original_checkpoints(planner)
    independent = planner.cache / (evidence.VERSION + '-group-003.json')
    before = independent.read_bytes()
    save_choices(project, 'test-run', planner.progress['recovery']['request_id'],
                 [{'issue_index':0,'selected_quote':0}])
    resumed = DecisionModel(reviews=[review()])
    candidate = make_planner(run, resumed, count=12, source=ORIGINAL).run()
    assert [stage for stage,c in resumed.calls] == ['choice_plan','coordinated_group','review']
    assert resumed.calls[1][1]['requested_chapters'] == [2,3]
    assert resumed.calls[-1][1]['coordinated_verification']['issues'][0]['binding_author_decision']['author_quote'] == YES
    assert all(candidate['chapters'][n-1]['incoming_consequence'].startswith('Mara has refused') for n in [2,3,9])
    assert independent.read_bytes() == before
    assert_originals_retained(planner,baseline)


def test_pending_choice_freezes_transitive_writers_while_shared_readers_can_progress():
    items = [proposal(0,2,conflict=True,reads=[8]), proposal(1,8,reads=[12]),proposal(2,12),
             proposal(3,16,reads=[30]), proposal(4,23,reads=[30])]
    plan = {'issues':items}
    scopes = {'issues':[{'scope':'global'} for _ in items]}
    execution = evidence.execution_plan(plan,{0:{'verdict':'confirmed_conflict','reason':'exclusive',
                       'source_quotes':[YES,NO], 'writable_chapters':[]}})
    ready, held = independent_groups(execution,plan,scopes,30)
    assert held == {0,1,2}
    assert [g['requested_chapters'] for g in ready] == [[16],[23]]


def test_compact_repairs_preserve_large_original_leaves_and_reject_foreign_chapters():
    chapters = [{'number':1,'scenes':[{'goal':'A','details':'x'*5000}]},
                {'number':2,'scenes':[{'goal':'B','details':'y'*5000}]}]
    value = {'chapter_replacements':[{'number':1,'field_edits':[{'path':['scenes',0,'goal'],'value':'C'}]}]}
    result = expand(value, {'chapters':chapters}, [1])
    assert result['chapter_replacements'][0]['after'] == {'number':1,'scenes':[{'goal':'C','details':'x'*5000}]}
    assert chapters[0]['scenes'][0]['goal'] == 'A'
    with pytest.raises(ValueError):
        expand(value,{'chapters':chapters},[2])


def test_pending_global_field_freezes_transitively_related_distant_chapters():
    items = [proposal(0,2,conflict=True),proposal(1,9),proposal(2,16),proposal(3,24)]
    for item, paths in zip(items,[['/assumptions/1'],['/assumptions/1','/characters/0/role'],
                                  ['/characters/0/role'], ['/assumptions/10']]):
        item['candidate_evidence']=[{'path':path,'quote':'saved text'} for path in paths]
    result = evidence.execution_plan({'issues':items},{0:{'verdict':'confirmed_conflict','reason':'exclusive',
                            'source_quotes':[YES,NO],'writable_chapters':[]}})
    ready, held = independent_groups(result,{'issues':items},{'issues':[{'scope':'global'} for _ in items]},30)
    assert held == {0,1,2}
    assert [g['requested_chapters'] for g in ready] == [[24]]


class WriterRaisesConflict(DecisionModel):
    def complete(self,system,user):
        ctx = json.loads(user.rsplit('\n\n',1)[1])
        if (user.startswith('STAGE: coordinated_repair\n') and ctx['requested_chapters']==[2]
                and 'binding_author_choices' not in ctx):
            self.calls.append(('writer_hypothesis',ctx))
            issue = ctx['review']['issues'][0]['issue_id']
            evidence_id = ctx['source_evidence_catalog']['issue_bindings'][0]['source_evidence_ids'][0]
            return json.dumps({'status':'requires_author','reason':'The source appears incompatible.',
              'issues':[{'issue_id':issue,'status':'requires_author','reason':'The two sole owners conflict.',
                    'source_evidence_ids':[evidence_id],'chapter_numbers':[]}],
              'chapter_replacements':[],'global_edits':[]})
        return super().complete(system,user)


def test_writer_asks_for_author_only_after_independent_review_and_unrelated_work(tmp_path):
    plan={'issues':[{**proposal(0,2), 'source_quotes':[YES]},proposal(1,9)]}
    findings=review({'scope':'global','message':'Check the ownership of the studio.','source_evidence':YES},
                    finding(9))
    model=WriterRaisesConflict(plan=plan,reviews=[findings])
    planner=make_planner(tmp_path,model,count=12,source=ORIGINAL)
    with pytest.raises(OutlinePlanningError):
        planner.run()
    stages=[s for s,c in model.calls]
    assert stages[-3:]==['writer_hypothesis','coordinated_group','conflict_verify']
    assert planner.progress['recovery']['action']=='choose_author_source'
    assert planner.progress['completed_issue_indices'] == [1]
    assert planner.progress['deferred_issue_indices'] == [0]
    assert (planner.cache / (evidence.VERSION+'-group-002.json')).is_file()
    assert not (planner.cache/'validated.json').exists()


def test_started_legacy_groups_keep_receipts_and_unsent_groups_get_per_issue_permissions(tmp_path):
    from test_planning_metadata_recovery import seed_metadata_failure
    from core import planning_coordinated as legacy
    from test_planning_repair_context import source
    planner = seed_metadata_failure(tmp_path / 'book')
    # Model an old job interrupted after finishing group 1 and before sending group 2.
    for p in planner.cache.rglob(legacy.VERSION + '-group-002*'):
        if p.is_file():
            p.unlink()
    completed = planner.cache / (legacy.VERSION + '-group-001.json')
    original = completed.read_bytes()
    from test_outline_planning import PlanningModel
    class Observe(PlanningModel):
        def complete(self, system, user):
            ctx=json.loads(user.rsplit('\n\n',1)[1])
            assert user.startswith('STAGE: coordinated_repair\n') and 'field_edits' in user
            assert ctx['requested_chapters']==[9] and ctx['source_authority']
            raise TimeoutError('Stop at the first previously unsent group')
    with pytest.raises(TimeoutError,match='previously unsent'):
        make_planner(planner.directory.parent,Observe(),count=12,source=source()).run()
    assert completed.read_bytes()==original
    assert (planner.cache/(evidence.VERSION+'-continued-group-002-protocol.json')).exists()


def test_small_connection_finishes_independent_groups_and_resume_only_writes_pending_group(tmp_path, monkeypatch):
    plan = {'issues':[proposal(0,2),proposal(1,9)]}
    findings = review({'scope':'global','message':'Restore the refusal.','source_evidence':QUOTE},finding(9))
    model = CoordinatedModel(plan=plan,reviews=[findings,review()])
    planner = make_planner(tmp_path,model,count=12)
    fits = planner._prompt_fits
    def limited(prompt,*args,**kwargs):
        if prompt.startswith('STAGE: coordinated_repair\n'):
            return json.loads(prompt.rsplit('\n\n',1)[1])['requested_chapters'] != [2]
        return fits(prompt,*args,**kwargs)
    monkeypatch.setattr(planner,'_prompt_fits',limited)
    with pytest.raises(OutlinePlanningError, match='容量'):
        planner.run()
    assert planner.progress['recovery']['retryable'] is True
    independent = planner.cache/(evidence.VERSION+'-group-002.json')
    prior = independent.read_bytes()
    assert [c['requested_chapters'] for s,c in model.calls if s=='coordinated_group'] == [[9]]
    resumed = CoordinatedModel(reviews=[review()])
    assert make_planner(tmp_path,resumed,count=12).run()['chapters'][1]['incoming_consequence'].startswith('Mara')
    assert [s for s,c in resumed.calls] == ['coordinated_group','review']
    assert resumed.calls[0][1]['requested_chapters'] == [2]
    assert independent.read_bytes()==prior


def test_oversize_source_verification_is_not_an_author_question_and_preserves_unrelated_chapters(tmp_path,monkeypatch):
    from core.planning_errors import PlanningInputTooLarge
    plan={'issues':[proposal(0,2,conflict=True),proposal(1,9)]}
    findings=review({'scope':'global','message':'Check the sole owners.','source_evidence':YES},finding(9))
    model=DecisionModel(plan=plan,reviews=[findings,review()])
    planner=make_planner(tmp_path,model,count=12,source=ORIGINAL)
    call=planner._call
    def limited(name,instruction,*args,**kwargs):
        if instruction.startswith(evidence.CONFLICT_INSTRUCTION):
            raise PlanningInputTooLarge(name,90000,80000)
        return call(name,instruction,*args,**kwargs)
    monkeypatch.setattr(planner,'_call',limited)
    with pytest.raises(OutlinePlanningError, match='容量'):
        planner.run()
    assert planner.progress['recovery']['action']=='change_planning_capacity'
    assert not list(planner.cache.glob('author-question-*'))
    independent=planner.cache/(evidence.VERSION+'-group-002.json')
    prior=independent.read_bytes()
    resumed=DecisionModel()
    next_planner=make_planner(tmp_path,resumed,count=12,source=ORIGINAL)
    with pytest.raises(OutlinePlanningError,match='选择'):
        next_planner.run()
    assert [s for s,c in resumed.calls] == ['conflict_verify']
    assert next_planner.progress['recovery']['action']=='choose_author_source'
    assert independent.read_bytes()==prior
