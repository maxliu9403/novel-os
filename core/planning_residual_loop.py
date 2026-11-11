"""Continue verified outline repairs while they make measurable changes to the saved book."""
from __future__ import annotations

from core.outline_planning import OutlinePlanningError, content_hash

VERSION = 'residual-closure-v1'


def _slots(node, path=()):
    """Fix the set of possible progress witnesses at entry, including empty registries."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _slots(value, (*path, key))
    elif isinstance(node, list) and node:
        for index, value in enumerate(node):
            yield from _slots(value, (*path, index))
    else:
        yield path


def _value(candidate, path):
    for key in path:
        try:
            candidate = candidate[key]
        except (KeyError, IndexError, TypeError):
            return _MISSING
    return candidate


_MISSING = object()


def converge(planner, candidate, review, repair_round, *, authority, previous_review=None):
    """A repeated review message is not progress. Every blocked pass must earn another pass.

    The best outstanding count or a previously untouched original field must advance.
    The witness set is finite and does not grow with model-generated records. Re-entering
    the same candidate or offering only a paraphrase on already-spent fields terminates.
    All successful exits require the ordinary complete semantic review.
    """
    if review['status'] == 'passed':
        return candidate, review
    remaining = set(_slots(candidate))
    # Appending an entry to a nonempty registry does not change an existing scalar.
    # Its length can earn one witness, irrespective of how many entries a writer adds.
    registries = {('promise_ledger',), ('characters',)}
    registries.update(('chapters', i, k) for i, ch in enumerate(candidate.get('chapters', []))
                      for k in ('setups', 'payoffs') if isinstance(ch.get(k), list))
    anchor = content_hash([VERSION, candidate, review, authority])
    best = len(review['issues'])
    seen = {content_hash(candidate)}
    while review['status'] != 'passed':
        prefix = VERSION + '-' + content_hash([anchor, candidate, review])[:24]
        planner._progress('semantic_review', 'running',
                          f'继续修订终审留下的 {len(review["issues"])} 项问题，复用已有章节')
        fixed, checked = repair_round(candidate, review, prefix, previous_review)
        if checked['status'] == 'passed':
            return fixed, checked
        before, after = content_hash(candidate), content_hash(fixed)
        witness = {p for p in remaining if _value(candidate, p) != _value(fixed, p)}
        grown = {p for p in registries if isinstance(_value(candidate, p), list)
                 and isinstance(_value(fixed, p), list)
                 and len(_value(candidate, p)) != len(_value(fixed, p))}
        count = len(checked['issues'])
        if after in seen or (not witness and not grown and count >= best):
            record = {'before': before, 'after': after, 'input_review': content_hash(review),
                      'final_review': content_hash(checked), 'best_outstanding': best,
                      'outstanding': count, 'reason': 'repeated_candidate' if after in seen else 'no_new_progress'}
            name = prefix + '-stalled'
            deps = {'anchor': anchor, 'record': content_hash(record)}
            def verify(value):
                if value != record:
                    raise ValueError('Saved residual stop does not match verified candidate and review')
                return value
            if planner._load(name, deps, verify, strict_dependencies=True) is None:
                planner._save(name, deps, record)
            reason = f'本轮复核仍有 {count} 项问题，修订未产生可验证的新进展。最新候选和逐项核查已保存。'
            planner.progress['recovery'] = {'retryable': False, 'action': 'inspect_residual_closure',
                                            'reason': reason}
            planner.progress['next_action'] = reason
            raise OutlinePlanningError(reason + ' ' + '；'.join(i['message'] for i in checked['issues']))
        remaining.difference_update(witness)
        registries.difference_update(grown)
        best = min(best, count)
        seen.add(after)
        candidate, review = fixed, checked
        previous_review = 'review-' + prefix
    return candidate, review
