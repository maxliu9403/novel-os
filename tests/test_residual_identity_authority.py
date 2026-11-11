"""Independent role metadata survives an unrelated, verified author decision."""
from copy import deepcopy
import json

import pytest

from core.planning_author_decisions import save_choices
from core.outline_planning import OutlinePlanningError
from test_outline_coordinated_repair import MIXED, original_checkpoints, assert_originals_retained
from test_outline_planning import make_planner, review
from test_planning_evidence_orchestration import DecisionModel, ORIGINAL, YES, NO, proposal


GUARD_SOURCE = 'The guard stands at the back door. He keeps the keys.'
SOURCE = ORIGINAL + '\n' + GUARD_SOURCE


class IdentityDecisionModel(DecisionModel):
    def __init__(self, *, reviews=None):
        def add_guard(foundation):
            foundation['characters'].append({'id': 'guard', 'name': 'guard', 'role': 'supporting',
                                              'gender': '', 'pronouns': '', 'aliases': []})
        super().__init__(reviews=reviews, mutate_global=add_guard)

    def complete(self, system, user):
        context = json.loads(user.rsplit('\n\n', 1)[1])
        if (user.startswith('STAGE: coordinated_repair_plan')
                and context['review']['issues'][0]['message'] == 'Resolve the two exclusive owners.'
                and not context.get('binding_author_decision')):
            self.calls.append(('identity_author_plan', context))
            return json.dumps({'issues': [proposal(0, 2, conflict=True),
                {**proposal(1, 9), 'source_quotes': [GUARD_SOURCE],
                 'candidate_evidence': [{'path': '/characters/1/id', 'quote': 'guard'}]}]})
        if user.startswith('STAGE: coordinated_residual_identity'):
            self.calls.append(('identity', context))
            assert context['permitted_characters'][0]['id'] == 'guard'
            assert context['permitted_characters'][0]['pronouns'] == ''
            return json.dumps({'identity_fields': [{'character_id': 'guard', 'field': 'pronouns',
                'before': '', 'value': 'he/him', 'source_indices': [0]}], 'identity_relations': []})
        return super().complete(system, user)


@pytest.mark.parametrize('selected', [0, 1])
def test_independent_identity_is_saved_before_author_wait_and_never_paid_twice(tmp_path, selected):
    project = tmp_path / 'book'
    run = project / 'outputs/runs/test-run'
    inputs = project / 'outputs/input'
    inputs.mkdir(parents=True)
    (inputs / 'prompt.md').write_text(SOURCE)
    findings = review(
        {'scope': 'global', 'message': 'Resolve the two exclusive owners.', 'source_evidence': YES},
        {'scope': 'chapter', 'chapter': 9, 'message': 'Record the source-supported guard pronouns.',
         'source_evidence': GUARD_SOURCE})
    model = IdentityDecisionModel(reviews=[deepcopy(MIXED), findings])
    planner = make_planner(run, model, count=12, source=SOURCE)
    with pytest.raises(OutlinePlanningError, match='选择'):
        planner.run()
    assert planner.progress['recovery']['action'] == 'choose_author_source'
    assert planner.progress['completed_issue_indices'] == [1]
    assert planner.progress['deferred_issue_indices'] == [0]
    assert [stage for stage, _ in model.calls].count('identity') == 1
    assert not (planner.cache / 'validated.json').exists()
    partial, = planner.cache.glob('residual-closure-v1-*-independent-*.json')
    saved = json.loads(partial.read_text())['payload']
    assert saved['candidate']['characters'][1]['pronouns'] == 'he/him'
    assert saved['candidate']['characters'][1]['gender'] == ''
    audit, = saved['verification']['identity_revisions']
    assert audit['issue_index'] == 1 and audit['operation'] == 'identity_metadata'
    assert saved['verification']['deferred_issue_indices'] == [0]
    originals = original_checkpoints(planner)
    receipts = {p: p.read_bytes() for p in planner.cache.rglob('*') if p.is_file()}

    # Waiting is a replay, not another attempt to charge for an already repaired role.
    waiting = IdentityDecisionModel()
    with pytest.raises(OutlinePlanningError, match='选择'):
        make_planner(run, waiting, count=12, source=SOURCE).run()
    assert waiting.calls == []
    assert all(path.read_bytes() == raw for path, raw in receipts.items())

    save_choices(project, 'test-run', planner.progress['recovery']['request_id'],
                 [{'issue_index': 0, 'selected_quote': selected}])
    resumed = IdentityDecisionModel(reviews=[review()])
    candidate = make_planner(run, resumed, count=12, source=SOURCE).run()
    stages = [stage for stage, _ in resumed.calls]
    assert stages == ['choice_plan', 'residual_narrative', 'review']
    assert candidate['characters'][1]['pronouns'] == 'he/him'
    assert candidate['characters'][1]['gender'] == ''
    assert resumed.calls[0][1]['binding_author_decision']['author_quote'] == [YES, NO][selected]
    final_review = resumed.calls[-1][1]
    assert final_review['candidate']['characters'][1]['pronouns'] == 'he/him'
    assert final_review['coordinated_verification']['issues'][0]['binding_author_decision']['author_quote'] == [YES, NO][selected]
    assert all(path.read_bytes() == raw for path, raw in receipts.items())
    assert_originals_retained(planner, originals)
    assert (inputs / 'prompt.md').read_text() == SOURCE

    completed = IdentityDecisionModel()
    assert make_planner(run, completed, count=12, source=SOURCE).run() == candidate
    assert completed.calls == []
