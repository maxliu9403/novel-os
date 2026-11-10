"""Attach acknowledged author decisions to the candidate's actual publication review."""
from copy import deepcopy

RULES = """
binding_author_choices are authenticated decisions made by the author about the paired
original passages. The selected author_quote takes precedence for that disputed fact;
do not ask the same question. Verify that the candidate follows it. Do not just echo
the selection: inspect the candidate and explain its treatment. Return author_choice_checks
as one {choice_id, status, reason} per supplied choice. status is honored, contradicted,
or outside_window (only for a bounded window without relevant material). A contradiction
must block with a specific issue. Never pass a contradiction.
"""


def inspect_result(review, choices, *, bounded=False):
    if not choices:
        return review
    rows = review.get('author_choice_checks')
    expected = {c['choice_id'] for c in choices}
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError('Every binding author choice needs a candidate review result')
    seen = set()
    allowed = {'honored', 'contradicted'} | ({'outside_window'} if bounded else set())
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('choice_id'),str)
                or row['choice_id'] not in expected or row['choice_id'] in seen or row.get('status') not in allowed
                or not isinstance(row.get('reason'),str) or not row['reason'].strip()):
            raise ValueError('Invalid or duplicate author choice review')
        if row['status'] == 'contradicted' and review['status'] == 'passed':
            raise ValueError('A candidate contradicting an author decision cannot pass')
        seen.add(row['choice_id'])
    return review


def reduce_windows(results, choices):
    if not choices:
        return {}
    checks = []
    for choice in choices:
        rows = [row for r in results for row in r if row['choice_id'] == choice['choice_id']]
        relevant = [row for row in rows if row['status'] != 'outside_window']
        if not relevant:
            raise ValueError('No review window checked the candidate against the binding author choice')
        checks.append(deepcopy(next((row for row in relevant if row['status'] == 'contradicted'),relevant[0])))
    return {'author_choice_checks':checks}


def freeze_initial(planner, choices):
    # Resume the same original issue/repair ledger when an author answers mid-run.
    # Before publication an additional gate checks any choice newer than this snapshot.
    deps = {'stage':'review-initial','version':1}
    name = 'review-initial-authority-snapshot'
    def validate(saved):
        if (not isinstance(saved,dict) or not isinstance(saved.get('choices'),list)
                or any(c not in choices for c in saved['choices'])):
            raise ValueError('Historical review references an invalidated author choice')
        return saved
    saved = planner._load(name,deps,validate,strict_dependencies=True)
    if saved is not None:
        return saved['choices']
    if not choices or any(planner.cache.glob('review-initial*.json')) or any((planner.cache/'raw').glob('review-initial*')):
        return []
    planner._save(name,deps,{'choices':choices})
    return choices
