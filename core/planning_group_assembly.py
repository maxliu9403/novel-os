"""Combine independent derivatives of one frozen candidate without lateral writes."""
from copy import deepcopy


def merge(baseline, assembled, group, writable, *, residual=False):
    positions = {item['number']: i for i, item in enumerate(baseline['chapter_map'])}
    if group['chapters'] != baseline['chapters'] or group['chapter_map'] != baseline['chapter_map']:
        for key, pos in [('chapters', {n: n-1 for n in positions}), ('chapter_map', positions)]:
            for number, idx in pos.items():
                value = group[key][idx]
                if value == baseline[key][idx]:
                    continue
                if number not in writable or assembled[key][idx] != baseline[key][idx]:
                    raise ValueError('Independent repairs cannot change overlapping or unpermitted chapters')
                assembled[key][idx] = deepcopy(value)

    fields = ['assumptions', 'structural_risks'] if residual else ['assumptions']
    for field in fields:
        for idx, (old, new) in enumerate(zip(baseline.get(field, []), group.get(field, []), strict=True)):
            if old != new:
                if assembled[field][idx] not in (old, new):
                    raise ValueError('Independent repairs disagree on a shared generated ' + field)
                assembled[field][idx] = deepcopy(new)

    if residual and group.get('promise_ledger') != baseline.get('promise_ledger'):
        _merge_ledger(baseline, assembled, group)
    actors = baseline['characters']
    if group['characters'][:len(actors)] != actors:
        raise ValueError('An independent repair cannot replace an established character')
    registered = {entry['id']: entry for entry in assembled['characters']}
    for entry in group['characters'][len(actors):]:
        key = entry['id']
        if key in registered and registered[key] != entry:
            raise ValueError('Independent repairs disagree on a generated character identity')
        if key not in registered:
            assembled['characters'].append(deepcopy(entry))
            registered[key] = entry
    mutable = {'chapters','chapter_map','assumptions','characters'}
    if residual:
        mutable.update(('structural_risks', 'promise_ledger'))
    allowed_new = {'promise_ledger'} if residual else set()
    if set(baseline) - set(group) or set(group) - set(baseline) - allowed_new or any(group[k] != baseline[k] for k in baseline.keys() - mutable):
        raise ValueError('A repair altered an unauthorized global field')


def _merge_ledger(baseline, assembled, group):
    original = baseline.get('promise_ledger', [])
    proposed = group['promise_ledger']
    current = assembled.setdefault('promise_ledger', [])

    def identity(entry):
        return (entry.get('setup_id') or entry.get('id')) if isinstance(entry, dict) else None

    if len(proposed) < len(original):
        raise ValueError('A promise repair cannot remove ledger entries')
    for i, before in enumerate(original):
        after = proposed[i]
        if before == after:
            continue
        if not identity(before) or identity(before) != identity(after) or current[i] not in (before, after):
            raise ValueError('Independent repairs disagree on a registered promise')
        current[i] = deepcopy(after)
    for entry in proposed[len(original):]:
        key = identity(entry)
        matches = [row for row in current if identity(row) == key]
        if not key or (matches and matches != [entry]):
            raise ValueError('Independent repairs disagree on a new promise identity')
        if not matches:
            current.append(deepcopy(entry))
