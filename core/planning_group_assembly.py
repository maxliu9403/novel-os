"""Combine independent derivatives of one frozen candidate without lateral writes."""
from copy import deepcopy


def merge(baseline, assembled, group, writable):
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

    # Derived global changes have only two permitted forms: replacing cited assumption
    # slots and appending verified character records. All other global data stays frozen.
    for idx, (old, new) in enumerate(zip(baseline['assumptions'], group['assumptions'], strict=True)):
        if old != new:
            if assembled['assumptions'][idx] not in (old, new):
                raise ValueError('Independent repairs disagree on a shared generated assumption')
            assembled['assumptions'][idx] = deepcopy(new)
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
    if set(group) != set(baseline) or any(group[k] != baseline[k] for k in baseline.keys() - mutable):
        raise ValueError('A repair altered an unauthorized global field')
