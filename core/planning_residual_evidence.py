"""Bind all editor windows for one finding to the same source-grounded direction."""
from __future__ import annotations

from core.outline_planning import _json, content_hash
from core.skeleton_projection import chapter_locations

SELECT = """STAGE: coordinated_residual_evidence
Inspect every evidence field in this window against the finding and its adopted passages.
Select ALL fields needed to determine or constrain a correction, including any competing
states, existing bridges, exceptions, identity/knowledge limits and read-only constraints.
Do not choose which version wins. Return {"indices":[zero-based indices into evidence_fields]}.
The caller retrieves full original values at those positions. No summaries or edits."""
DECIDE = """STAGE: coordinated_residual_direction
Determine one minimal correction direction for this finding. The editor will use this
same direction in every window. Respect the adopted source and binding author choice.
Candidate text can establish an existing compatible bridge or intended uncertainty,
but cannot establish an author requirement. Explain exactly which facts to preserve,
which contradiction to correct, and how copied constraints should agree. Preserve all
compatible choices; do not invent facts to resolve ambiguity. If the available evidence
does not support a correction, return an empty direction with the reason. Return
{"direction":"...", "reason":"...", "source_indices":[indices into adopted_passages]}."""


def _leaves(prefix, value):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from _leaves(prefix + [key], child)
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from _leaves(prefix + [index], child)
    else:
        yield [prefix, value]


def scoped_material(planner, candidate, item):
    """Full material for named dependencies. Source locators reference actual frozen offsets."""
    numbers = set(item['affected_chapters']) | set(item['dependency_chapters'])
    writable = set(item['writable_chapters'])
    source = planner.source
    locators = chapter_locations(source, len(candidate['chapters']))
    if numbers and numbers <= set(locators):
        author = []
        first = locators[min(locators)]['source_chars'][0]
        last = locators[max(locators)]['source_chars'][1]
        for start, end in [(0, first), (last, len(source))]:
            if start < end:
                author.append([['adopted_source', start, end], source[start:end]])
        for n in sorted(numbers):
            start, end = locators[n]['source_chars']
            author.append([['adopted_source', start, end], source[start:end]])
    else:
        author = [[['adopted_source', 0, len(source)], source]]

    # Generated global contracts constrain a local correction even when the
    # finding did not quote them. They must enter the same complete evidence scan.
    globals_ = [[[key], value] for key, value in candidate.items() if key not in ('chapter_map', 'chapters')]
    readonly = [row for row in globals_ if row[0][0] not in ('assumptions', 'structural_risks')]
    selected = list(globals_)
    for section in ('chapter_map', 'chapters'):
        for i, row in enumerate(candidate[section]):
            if row['number'] in numbers:
                entry = [[section, i], row]
                selected.append(entry)
                if row['number'] not in writable:
                    readonly.append(entry)
    for citation in item.get('candidate_evidence', []):
        pointer = citation['path']
        # The plan references the pre-repair candidate. Existing operations can
        # revise its text; always show the actual current value at that address.
        parts = [p.replace('~1', '/').replace('~0', '~') for p in pointer.split('/')[1:]]
        node, path = candidate, []
        try:
            for part in parts:
                key = int(part) if isinstance(node, list) else part
                node = node[key]
                path.append(key)
        except (KeyError, ValueError, IndexError, TypeError):
            raise ValueError('Residual evidence references a missing candidate path') from None
        # The complete global object already includes this field. Traversing the
        # pointer above still verifies that the cited location survived repair.
    return author, readonly, selected


def protected_fields(selected, associations, writable_fields):
    """Include read-only leaves inside writable chapters in a single-window hearing."""
    editable = {tuple(path) for path, _ in writable_fields}
    complete = {tuple(path) for path, _ in associations}
    return [leaf for path, row in selected if tuple(path) not in complete
            for leaf in _leaves(path, row) if tuple(leaf[0]) not in editable]


def fits(planner, instruction, context, limit):
    return planner._prompt_fits(instruction + '\n\n' + _json(context), 'review', limit)


def _key(stage, root_hash, instruction, context):
    deps = {'candidate': root_hash, 'context': content_hash(context), 'policy': content_hash(instruction)}
    return 'residual-' + stage + '-v1-' + content_hash(deps)[:24], deps


def _issued(planner, name, deps):
    if planner._load(name, deps, lambda value: value, strict_dependencies=True) is not None:
        return True
    cache = getattr(planner, 'cache', None)
    return cache is not None and ((cache / (name + '-response.json')).exists()
                                  or any((cache / 'raw').glob(name + '.*.prompt.txt'))
                                  or any((cache / 'raw').glob(name + '.*.response.txt')))


def partitioned(planner, stage, root_hash, instruction, context, wants_split):
    """Once split, always replay this tree. Previously dispatched leaves retain their
    original receipt identities. Only unsent nodes may split when the route shrinks."""
    name, deps = _key(stage, root_hash, instruction, context)
    marker = name + '-split'

    def validate(record):
        if record != {'split': True}:
            raise ValueError('Invalid residual partition checkpoint')
        return record

    if planner._load(marker, deps, validate, strict_dependencies=True) is not None:
        return True
    if wants_split and not _issued(planner, name, deps):
        planner._save(marker, deps, {'split': True})
        return True
    return False


def pages(planner, stage, root_hash, instruction, base, key, rows, limit, page_bytes):
    """Partition only between complete fields. Dispatch reports accurate capacity
    for an indivisible request and can recover completed responses without a new call."""
    ctx = {**base, key: rows}
    too_large = len(_json(rows).encode()) > page_bytes or not fits(planner, instruction, ctx, limit)
    if len(rows) < 2 or not partitioned(planner, stage, root_hash, instruction, ctx, too_large):
        yield ctx
    else:
        mid = len(rows) // 2
        yield from pages(planner, stage, root_hash, instruction, base, key, rows[:mid], limit, page_bytes)
        yield from pages(planner, stage, root_hash, instruction, base, key, rows[mid:], limit, page_bytes)


def checkpoint(planner, stage, root_hash, instruction, context, validate, limit):
    name, deps = _key(stage, root_hash, instruction, context)
    result = planner._load(name, deps, validate, strict_dependencies=True)
    if result is None:
        result = planner._call(name, instruction, context, validate, input_limit_bytes=limit, strict_dependencies=True,
                               recover_archived=True)
        planner._save(name, deps, result)
    return result, name


def _indices(values, size):
    return (isinstance(values, list) and all(type(i) is int and 0 <= i < size for i in values)
            and len(set(values)) == len(values))


def direction(planner, root_hash, base, materials, limit, page_bytes):
    """Select original evidence before resolving the one shared edit direction."""
    fields = []
    for path, value in materials:
        if path[0] == 'adopted_source':
            # Stable, offset-addressed slices keep every original character. A
            # source paragraph can otherwise occupy the whole small-model request.
            start = path[1]
            for line in value.splitlines(keepends=True):
                end = start + len(line)
                fields.append([['adopted_source', start, end], line])
                start = end
        else:
            fields.extend(_leaves(path, value))
    scans, selected = [], []
    for page in pages(planner, 'select', root_hash, SELECT, base, 'evidence_fields', fields, limit, page_bytes):
        values = page['evidence_fields']

        def validate_selection(response):
            if (not isinstance(response, dict) or set(response) != {'indices'}
                    or not _indices(response['indices'], len(values))):
                raise ValueError('Residual evidence scan must select supplied field indices')
            return response

        result, name = checkpoint(planner, 'select', root_hash, SELECT, page, validate_selection, limit)
        scans.append({'checkpoint': name, 'inspected': len(values), 'selected': result['indices']})
        selected.extend(values[i] for i in sorted(result['indices']))
    ctx = {**base, 'selected_scoped_fields': selected}

    def validate(value):
        if (not isinstance(value, dict) or set(value) != {'direction', 'reason', 'source_indices'}
                or not isinstance(value['direction'], str) or not isinstance(value['reason'], str)
                or not value['reason'].strip() or not _indices(value['source_indices'], len(base['adopted_passages']))
                or (value['direction'].strip() and not value['source_indices'])):
            raise ValueError('Residual direction requires an explanation and source evidence for every proposed change')
        return value

    decision, name = checkpoint(planner, 'direction', root_hash, DECIDE, ctx, validate, limit)
    # Every editor gets all retrieved evidence as well as the single direction,
    # preserving the actual evidence even if the model oversimplified its reading.
    return ({'shared_edit_direction': decision,
             'scoped_evidence': selected},
            {'direction_checkpoint': name, 'evidence_scans': scans})
