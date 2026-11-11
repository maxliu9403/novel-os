"""Exercise terminal continuation through native planning and immutable checkpoint resume."""
from copy import deepcopy
import json

import pytest

from test_outline_coordinated_repair import (
    CoordinatedModel, MIXED, GLOBAL_ISSUE, QUOTE, make_planner, review,
    original_checkpoints, assert_originals_retained,
)


class ResidualModel(CoordinatedModel):
    def __init__(self, *, interrupt=False):
        super().__init__(mutate_global=lambda value: value.update(
            structural_risks=[f'Constraint {i} incorrectly requires a sale.' for i in range(4)]))
        self.interrupt = interrupt
        self.edits = 0

    def complete(self, system, user):
        context = json.loads(user.rsplit('\n\n', 1)[1])
        if user.startswith('STAGE: semantic_review'):
            self.calls.append(('review', context))
            risks = context['candidate']['structural_risks']
            if not any('incorrectly' in text for text in risks):
                return json.dumps(review())
            return json.dumps(review(GLOBAL_ISSUE) if 'coordinated_verification' in context else deepcopy(MIXED))
        if user.startswith('STAGE: coordinated_repair_plan'):
            self.calls.append(('coordinated_plan', context))
            return json.dumps({'issues': [
                {'issue_index':i, 'classification':'contradiction', 'reason':'Honor the explicit refusal.',
                 'affected_chapters':[3], 'writable_chapters':[3], 'dependency_chapters':[2,4],
                 'source_quotes':[QUOTE], 'candidate_evidence':[], 'foundation_repairs':[]}
                for i in range(len(context['review']['issues']))]})
        if user.startswith('STAGE: coordinated_residual_narrative'):
            self.calls.append(('residual_narrative', context))
            self.edits += 1
            if self.interrupt and self.edits == 2:
                raise TimeoutError('Interrupted between completed residual rounds')
            path, old = next((p, text) for p, text in context['fields']
                             if p[0] == 'structural_risks' and 'incorrectly' in text)
            return json.dumps({'edits':[{'path':path, 'old_span':old,
                'replacement':old.replace('incorrectly requires a sale', 'preserves the refusal'),
                'source_indices':[0]}], 'reason':'Remove one source contradiction and retain the other obligations.'})
        return super().complete(system, user)


@pytest.mark.parametrize('count', [8, 100])
def test_native_terminal_repair_runs_until_complete_review_and_reuses_all_results(tmp_path, count):
    model = ResidualModel()
    planner = make_planner(tmp_path, model, count=count)
    result = planner.run()
    assert model.edits == 4
    assert len(result['chapters']) == count
    assert all('preserves' in text for text in result['structural_risks'])
    results = [json.loads(path.read_text())['payload'] for path in
               planner.cache.glob('review-residual-closure-v1-*.json')]
    assert sum('coverage' in payload for payload in results) == 4
    snapshot = original_checkpoints(planner)
    resumed = ResidualModel()
    assert make_planner(tmp_path, resumed, count=count).run() == result
    assert resumed.calls == []
    assert_originals_retained(planner, snapshot)


def test_interrupted_residual_round_reuses_completed_edits_and_reviews(tmp_path):
    first = ResidualModel(interrupt=True)
    planner = make_planner(tmp_path, first, count=60)
    with pytest.raises(TimeoutError, match='between completed residual rounds'):
        planner.run()
    before = {f:f.read_bytes() for f in planner.cache.rglob('*.json')}
    resumed = ResidualModel()
    result = make_planner(tmp_path, resumed, count=60).run()
    assert len(result['chapters']) == 60
    assert resumed.edits == 3
    assert resumed.calls[0][0] == 'residual_narrative'
    assert all(f.read_bytes() == payload for f,payload in before.items())
    assert not any(stage in ('global','seed','roadmap','batch','coordinated_group') for stage,_ in resumed.calls)
