"""Cross-group reconstruction must retain already accepted source-bound corrections."""
from copy import deepcopy
from types import SimpleNamespace

from core import planning_coordinator


def test_disjoint_chapter_repairs_can_correct_different_parts_of_same_global_risk(monkeypatch):
    candidate = {'chapters': [{'number': n} for n in range(1, 5)],
                 'chapter_map': [{'number': n} for n in range(1, 5)],
                 'characters': [], 'assumptions': [],
                 'structural_risks': ['Keep a recoverable body. Treat the roles as distinct.']}
    groups = [{'original_issue_indices': [i], 'requested_chapters': [n], 'dependency_chapters': []}
              for i, n in enumerate((1, 4))]
    evidence = {'issues': [{'issue_index': i} for i in range(2)]}
    fixes = [('Keep a recoverable body.', 'The corpse is unrecoverable.'),
             ('Treat the roles as distinct.', 'The relationship remains unknown.')]
    inputs = []

    def repair(planner, baseline, review, plan, execution, group, decisions, deferred, choices):
        inputs.append(deepcopy(baseline))
        result = deepcopy(baseline)
        old, new = fixes[group['original_issue_indices'][0]]
        result['structural_risks'][0] = result['structural_risks'][0].replace(old, new)
        return result, []

    monkeypatch.setattr(planning_coordinator, '_residual_group', repair)
    assembled, *_ = planning_coordinator._phase(SimpleNamespace(), candidate, {}, {}, evidence, groups,
                                                'test', {}, None, {}, {}, residual=True)
    assert assembled['structural_risks'] == ['The corpse is unrecoverable. The relationship remains unknown.']
    assert candidate['structural_risks'] == ['Keep a recoverable body. Treat the roles as distinct.']
    assert inputs[1]['structural_risks'] == ['The corpse is unrecoverable. Treat the roles as distinct.']


def test_residual_author_answer_reuses_completed_independent_edits_and_frozen_entry(tmp_path):
    import json
    import pytest
    from core import planning_evidence_plan as evidence
    from core.planning_author_decisions import save_choices
    from core.outline_planning import OutlinePlanningError
    from test_outline_planning import make_planner, review
    from test_outline_coordinated_repair import MIXED, QUOTE
    from test_planning_evidence_orchestration import DecisionModel, ORIGINAL, YES, proposal, finding

    pending = review({'scope': 'global', 'message': 'Resolve the two explicit owners.', 'source_evidence': YES},
                     finding(3), finding(9))

    class ResidualDecisionModel(DecisionModel):
        def complete(self, system, user):
            ctx = json.loads(user.rsplit('\n\n', 1)[1])
            if user.startswith('STAGE: coordinated_repair_plan') and not ctx.get('binding_author_decision'):
                self.calls.append(('planned_residual' if ctx['review']['issues'] == pending['issues'] else 'planned_initial', ctx))
                plan = ([proposal(0, 2, conflict=True), proposal(1, 3), proposal(2, 9)]
                        if ctx['review']['issues'] == pending['issues'] else [proposal(0, 3), proposal(1, 3)])
                return json.dumps({'issues': plan})
            if user.startswith('STAGE: coordinated_residual_narrative'):
                self.calls.append(('residual_narrative', ctx))
                edits = []
                for path, before in ctx['fields']:
                    if path[0] == 'chapters' and path[-1] == 'incoming_consequence':
                        edits.append({'path': path, 'old_span': before,
                                      'replacement': 'The preserved refusal leads to this specific consequence.',
                                      'source_indices': [ctx['adopted_passages'].index(QUOTE)]})
                return json.dumps({'edits': edits, 'reason': 'Restore the exact author-established consequence.'})
            return super().complete(system, user)

    project = tmp_path / 'book'
    run = project / 'outputs/runs/test-run'
    source_path = project / 'outputs/input/prompt.md'
    source_path.parent.mkdir(parents=True)
    source_path.write_text(ORIGINAL)
    initial_model = ResidualDecisionModel(reviews=[deepcopy(MIXED), pending])
    first = make_planner(run, initial_model, count=12, source=ORIGINAL)
    with pytest.raises(OutlinePlanningError, match='选择'):
        first.run()
    assert first.progress['recovery']['action'] == 'choose_author_source'
    edits = [ctx for stage, ctx in initial_model.calls if stage == 'residual_narrative']
    assert [ctx['writable_chapters'] for ctx in edits] == [[9]]
    snapshots = {p: p.read_bytes() for p in first.cache.rglob('*') if p.is_file()}
    entry_path, = first.cache.glob('residual-entry-*-author-inputs.json')
    assert json.loads(entry_path.read_text())['payload'] == {'choices': []}
    save_choices(project, 'test-run', first.progress['recovery']['request_id'],
                 [{'issue_index': 0, 'selected_quote': 0}])
    resumed_model = ResidualDecisionModel(reviews=[review()])
    result = make_planner(run, resumed_model, count=12, source=ORIGINAL).run()
    resumed_edits = [ctx for stage, ctx in resumed_model.calls if stage == 'residual_narrative']
    assert [ctx['writable_chapters'] for ctx in resumed_edits] == [[2], [3]]
    assert all(ctx.get('prior_author_choices') for ctx in resumed_edits)
    assert all(result['chapters'][n - 1]['incoming_consequence'].startswith('The preserved refusal')
               for n in (2, 3, 9))
    assert not [stage for stage, _ in resumed_model.calls if stage in ('planned_initial', 'planned_residual', 'conflict_verify')]
    assert all(path.read_bytes() == raw for path, raw in snapshots.items())
    assert list(first.cache.glob('residual-entry-*-author-inputs.json')) == [entry_path]
