"""Capacity-aware chapter repair against immutable planning checkpoints.

Each discovery window reads the target and owns disjoint complete chapter bodies.
The model identifies dependencies; code validates coverage and retrieves original
chapters, never a model's paraphrase. The final semantic gates judge the changes.
"""
from copy import deepcopy

from core.outline_planning import REVIEW_CHECKS, _json, content_hash
from core.planning_capacity import input_limit
from core.planning_context import review_context

VERSION = 'repair-evidence-v1'
LEGACY_RULES = (
    "\nSTAGE: targeted_repair\nReturn only the listed chapters, "
    "correcting the cited issues. Preserve all valid scene/emotional details and frozen global design. "
    "Do not change author choices, contracts, identities, end states, or unlisted chapters."
)
DISCOVER = """STAGE: semantic_review_evidence_discovery
Read the complete original source and candidate material supplied for every scan_chapter.
Against the original findings and requested chapter bodies, identify ALL chapters in this
window that can constrain the proposed fix (cause, knowledge, character arc, foreshadowing,
reveal, payoff, ending, or other author choice). Include uncertain dependencies. Consider
both source facts and candidate details. Return full chapter numbers, never summaries.
Return {"inspected_chapters":[all scan_chapters in order],"needed_chapters":[numbers from
scan_chapters],"unresolved":""}. If the supplied material cannot be evaluated, explain in
unresolved. This response selects material; it cannot clear a finding or approve a repair."""
EDIT = """STAGE: chapter_batch_evidence_repair
Repair the findings for requested_chapters using the complete listed source and candidate
chapter bodies, frozen global design and author inputs. Distant dependencies identified
by the evidence scan are read-only. Preserve every other narrative choice and detail.
Return {"edits":[{"chapter":7,"path":["scenes",0,"subtext"],"value":"corrected text"}],
"unresolved":""}. Each path is relative to one original chapter, using exact field names
and zero-based list positions. Change only affected existing fields. Narrative string
lists (including reserved_later_payoffs) can be replaced as lists; edit individual fields
inside scenes and other objects. One edit may resolve findings in multiple chapters.
If any correction needs more evidence, another chapter, or a change to a frozen choice,
return no edits and explain in unresolved."""
VERIFY = """STAGE: semantic_review_repair_dependencies
Recheck each supplied original finding against the revised target chapters, the complete
retrieved source and candidate dependency chapters, global design and author commitments.
Check whether the corrected chapters create new contradictions with any supplied evidence.
If evidence needed to decide is missing, report a blocking issue. Return
{status:passed|blocked,coverage:{each required check:true},issues:[{scope:chapter|global,
chapter:integer when applicable,message:specific finding,source_evidence:original source
quotation or contract reference}]}. Pass only when all original findings are resolved and
all required checks were performed on this evidence. This is not the final whole-book gate."""


def _context(planner, candidate, issues, targets, included, *, scan=None, verify=False):
    """Preserve full global map, author inputs and each selected complete chapter."""
    count = planner.chapter_count
    included = sorted(included)
    context = planner._context(included if len(included) < count else None, project_source=True)
    view = {**candidate, 'chapters': [candidate['chapters'][n - 1] for n in included]}
    context.update(requested_chapters=targets, issues=issues,
                   evidence_scope={'included_chapters': included, 'total_chapters': count,
                                   'readonly_chapters': [n for n in included if n not in targets]})
    if scan is not None:
        context['scan_chapters'] = scan
    if verify:
        context['required_checks'] = REVIEW_CHECKS
    return review_context(context, view, complete_source=planner.source)


def _fits(planner, context, instruction, limit):
    stage = 'chapter_plan' if instruction.startswith('STAGE: chapter_batch') else 'review'
    return planner._prompt_fits(instruction + '\n\n' + _json(context), stage, limit)


def _adjacent(targets, count):
    return {n for target in targets for n in (target - 1, target, target + 1) if 1 <= n <= count}


def _findings(review, targets):
    return [issue for issue in review['issues'] if issue['chapter'] in targets]


def _plan(planner, candidate, review, targets, limit):
    """Freeze a feasible scan partition before dispatching its first request."""
    all_chapters = list(range(1, planner.chapter_count + 1))
    issues = _findings(review, targets)
    full = _context(planner, candidate, issues, targets, all_chapters)
    if (_fits(planner, full, EDIT, limit) and _fits(planner, _context(
            planner, candidate, issues, targets, all_chapters, verify=True), VERIFY, limit)):
        return [{'targets': targets, 'scan': []}]  # The full book is available at edit time.

    def partition(page):
        included = set(targets).union(page)
        context = _context(planner, candidate, issues, targets, included, scan=page)
        if _fits(planner, context, DISCOVER, limit):
            return [page]
        if len(page) == 1:
            return None
        middle = len(page) // 2
        left = partition(page[:middle])
        if left is None:
            return None
        right = partition(page[middle:])
        return None if right is None else left + right

    pages = partition(all_chapters)
    if pages is not None:
        return [{'targets': targets, 'scan': pages}]
    if len(targets) == 1:
        raise ValueError(f'Chapter {targets[0]} and an indivisible evidence window exceed input capacity; '
                         'source, candidate and checkpoints are preserved. A larger connection is required.')
    middle = len(targets) // 2
    return (_plan(planner, candidate, review, targets[:middle], limit)
            + _plan(planner, candidate, review, targets[middle:], limit))


def _manifest(value, targets, count):
    groups = value.get('groups')
    if (not isinstance(groups, list) or not groups
            or any(not isinstance(g, dict) or not isinstance(g.get('targets'), list)
                   or not g['targets'] or not isinstance(g.get('scan'), list) for g in groups)):
        raise ValueError('Invalid chapter evidence plan')
    if _json([n for g in groups for n in g['targets']]) != _json(targets):
        raise ValueError('Evidence plan does not cover every repair target once')
    for group in groups:
        pages = group['scan']
        if any(not isinstance(page, list) or not page for page in pages):
            raise ValueError('Invalid evidence scan window')
        if pages and _json([n for page in pages for n in page]) != _json(list(range(1, count + 1))):
            raise ValueError('Evidence windows must cover every original chapter exactly once')
    return value


def _checkpoint(planner, label, instruction, context, deps, validate, limit):
    bindings = {**deps, 'context': content_hash(context), 'policy': content_hash(instruction)}
    saved = planner._load(label, bindings, validate, strict_dependencies=True)
    if saved is None:
        saved = planner._call(label, instruction, context, validate,
                              input_limit_bytes=limit, strict_dependencies=True, recover_archived=True)
        planner._save(label, bindings, saved)
    return saved


def _selection(value, page):
    needed = value.get('needed_chapters')
    if (set(value) != {'inspected_chapters', 'needed_chapters', 'unresolved'}
            or _json(value.get('inspected_chapters')) != _json(page)
            or not isinstance(needed, list)
            or any(type(n) is not int or n not in page for n in needed)
            or len(set(needed)) != len(needed)
            or not isinstance(value.get('unresolved'), str)):
        raise ValueError('Evidence response must inspect the whole supplied window and select only its chapter numbers')
    return value


def _changes(value, candidate, targets):
    edits = value.get('edits')
    reason = value.get('unresolved')
    if set(value) != {'edits', 'unresolved'} or not isinstance(edits, list) or not isinstance(reason, str):
        raise ValueError('Repair must return edits and unresolved')
    if reason.strip():
        if edits:
            raise ValueError('An unresolved repair cannot also apply changes')
        return None
    chapters = {n: deepcopy(candidate['chapters'][n - 1]) for n in targets}
    paths = {n: set() for n in targets}
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'chapter', 'path', 'value'}:
            raise ValueError('Each repair edit needs chapter, path and value')
        n, path, new = edit['chapter'], edit['path'], edit['value']
        if (type(n) is not int or n not in chapters or not isinstance(path, list) or not path
                or any(type(key) not in (str, int) for key in path)
                or any(isinstance(key, str) and (key in ('id', 'number', 'schema', 'schema_version',
                                                         'chapter', 'target_words')
                                                 or key.endswith(('_id', '_ids'))) for key in path)):
            raise ValueError('Repair must address a mutable field in a requested chapter')
        key_path = tuple(path)
        if any(p[:len(key_path)] == key_path or key_path[:len(p)] == p for p in paths[n]):
            raise ValueError('Repair edits must address distinct, non-overlapping fields')
        parent = chapters[n]
        for key in path:
            if isinstance(parent, list):
                if type(key) is not int or not 0 <= key < len(parent):
                    raise ValueError('Repair list index is outside the saved chapter')
            elif not isinstance(parent, dict) or key not in parent:
                raise ValueError('Repair field is missing from the saved chapter')
            old = parent[key]
            container = parent
            parent = old
        if (type(old) is not type(new) or isinstance(old, dict)
                or (isinstance(old, list) and not all(isinstance(v, str) for v in old + new))
                or (isinstance(old, str) and old.strip() and not new.strip())
                or new == old):
            raise ValueError('Set a changed leaf value or narrative scalar list; retain complete containers')
        container[path[-1]] = deepcopy(new)
        paths[n].add(key_path)
    if not edits:
        raise ValueError('A resolved repair must make a concrete change')
    return {'chapters': list(chapters.values())}


def _combine(groups):
    """Chapters that read one another must be written and checked together."""
    result = []
    for targets, included in groups:
        independent = []
        for previous, reads in result:
            if targets & reads or previous & included:
                targets = targets | previous
                included = included | reads
            else:
                independent.append((previous, reads))
        independent.append((targets, included))
        result = independent
    return [(sorted(targets), sorted(included)) for targets, included in result]


def repair_chapters(planner, candidate, global_design, review, numbers):
    dependencies = {'candidate': content_hash(candidate), 'review': content_hash(review)}
    validate = lambda value: planner._batch(value, numbers, global_design)
    saved = planner._load('repair', dependencies, validate)
    if saved is not None:
        return saved

    plan_name = VERSION + '-plan'
    deps = {**dependencies, 'policy': content_hash([VERSION, DISCOVER, EDIT, VERIFY])}
    validate_plan = lambda v: _manifest(v, numbers, planner.chapter_count)
    plan = planner._load(plan_name, deps, validate_plan, strict_dependencies=True)
    # A cached partition is immutable. The dispatch boundary resolves capacity
    # only for outstanding requests; replaying receipts works offline.
    limit = None
    if plan is None:
        legacy = planner.batch_prompt + LEGACY_RULES
        complete = {**planner._context(), 'frozen_global_design': global_design, 'candidate': candidate,
                    'requested_chapters': numbers, 'review': review}
        previous_response = ((planner.cache / 'repair-response.json').exists()
                             or any((planner.cache / 'raw').glob('repair.*.response.txt')))
        if not previous_response:
            limit = input_limit(planner.client)
            if not _fits(planner, complete, legacy, limit):
                limit = input_limit(planner.client, negotiate=True)
        if previous_response or (len(numbers) <= planner.batch_size and _fits(planner, complete, legacy, limit)):
            # Honor completed former responses. A refused dispatch with only a
            # prompt may start the new protocol without resetting any answer.
            batch = planner._call('repair', legacy, complete, validate, recover_archived=True,
                                  strict_dependencies=previous_response, input_limit_bytes=limit)
            planner._save('repair', dependencies, batch)
            return batch
        plan = validate_plan({'groups': _plan(planner, candidate, review, numbers, limit)})
        planner._save(plan_name, deps, plan)
    deps['plan'] = content_hash(plan)
    groups = []
    for group_index, group in enumerate(plan['groups'], 1):
        targets, pages = group['targets'], group['scan']
        included = _adjacent(targets, planner.chapter_count) if pages else set(range(1, planner.chapter_count + 1))
        for index, page in enumerate(pages, 1):
            planner._progress('semantic_review', 'running',
                              f'查找第 {targets[0]} 章等修订所需的跨章证据，已定位到第 {page[0]}–{page[-1]} 章')
            ctx = _context(planner, candidate, _findings(review, targets), targets, set(targets).union(page), scan=page)
            label = f'{VERSION}-scan-{group_index:03d}-{index:03d}'
            record = _checkpoint(planner, label, DISCOVER, ctx, deps, lambda v: _selection(v, page), limit)
            if record['unresolved'].strip():
                raise ValueError('Evidence discovery incomplete; checkpoints preserved: ' + record['unresolved'])
            included.update(record['needed_chapters'])
        groups.append((set(targets), included))
    replacements = {}
    for index, (targets, included) in enumerate(_combine(groups), 1):
        ctx = _context(planner, candidate, _findings(review, targets), targets, included)

        def validate_edits(value):
            batch = _changes(value, candidate, targets)
            if batch is not None:
                planner._batch(batch, targets, global_design)
            return value

        record = _checkpoint(planner, f'{VERSION}-edit-{index:03d}', EDIT, ctx, deps, validate_edits, limit)
        if record['unresolved'].strip():
            raise ValueError('Chapter repair requires further evidence or author input: ' + record['unresolved'])
        batch = planner._batch(_changes(record, candidate, targets), targets, global_design)
        replacements.update((chapter['number'], chapter) for chapter in batch['chapters'])
        # This joint review sees the exact changed chapters and their remote
        # dependencies together. Ordinary whole-book review still runs afterward.
        changed = {**candidate, 'chapters': [replacements.get(c['number'], c) for c in candidate['chapters']]}
        confirmation = _context(planner, changed, _findings(review, targets), targets, included, verify=True)
        checked = _checkpoint(planner, f'{VERSION}-verify-{index:03d}', VERIFY, confirmation, deps,
                              planner._review, limit)
        if checked['status'] != 'passed':
            raise ValueError('Revised chapter contradicts its dependency evidence; checkpoints preserved: '
                             + '; '.join(issue['message'] for issue in checked['issues']))
    return validate({'chapters': [replacements[n] for n in numbers]})
