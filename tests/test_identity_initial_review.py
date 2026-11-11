"""Fresh books can reach typed identity repair without an unrelated first defect."""
import json

from test_outline_planning import SOURCE, make_planner, review
from test_residual_identity_orchestration import (
    COURIER_SOURCE, GUARD_SOURCE, _identity_finding, _identity_model,
)


def test_initial_identity_finding_reaches_typed_repair_without_unrelated_story_edits(tmp_path):
    model = _identity_model()
    complete = model.complete

    def reply(system, user):
        if user.startswith('STAGE: semantic_review'):
            context = json.loads(user.rsplit('\n\n', 1)[1])
            model.calls.append(('review', context))
            candidate = context['candidate']
            repaired = (candidate['characters'][1]['pronouns'] == 'he/him'
                        and candidate.get('identity_constraints'))
            return json.dumps(review() if repaired else _identity_finding())
        if user.startswith('STAGE: coordinated_repair\n'):
            context = json.loads(user.rsplit('\n\n', 1)[1])
            model.calls.append(('initial_identity_recheck', context))
            # Respect the old chapter-only contract: do not invent unrelated
            # narrative changes or pretend that missing metadata was fixed.
            assert '/characters/1/pronouns' not in context['allowed_global_fields']
            issue_id = context['review']['issues'][0]['issue_id']
            evidence_ids = context['source_evidence_catalog']['issue_bindings'][0]['source_evidence_ids']
            return json.dumps({
                'status': 'corrected',
                'reason': 'Existing identity fields require a separate metadata operation.',
                'issues': [{'issue_id': issue_id, 'status': 'recheck',
                    'reason': 'The adopted passages support pronouns and unresolved identity; '
                              'no chapter-only edit can correct these existing registry fields.',
                    'source_evidence_ids': evidence_ids, 'chapter_numbers': []}],
                'chapter_replacements': [], 'global_edits': [],
            })
        return complete(system, user)

    model.complete = reply
    source = SOURCE + '\n' + GUARD_SOURCE + '\n' + COURIER_SOURCE
    candidate = make_planner(tmp_path, model, count=8, source=source).run()
    stages = [stage for stage, _ in model.calls]
    assert stages.count('identity') == 1
    assert stages[-1] == 'review'
    assert 'coordinated_group' not in stages
    assert candidate['characters'][1]['pronouns'] == 'he/him'
    assert candidate['characters'][1]['gender'] == ''
    assert candidate['identity_constraints'][0]['relation'] == 'unknown'

    resumed = _identity_model()
    assert make_planner(tmp_path, resumed, count=8, source=source).run() == candidate
    assert resumed.calls == []
