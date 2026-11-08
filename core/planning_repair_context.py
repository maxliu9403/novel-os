"""Lossless, declared views for coordinated outline repair, without model I/O.

The complete candidate stays with the caller. Each repair request sees all global
invariants plus complete selected roadmap entries and chapter objects. A capacity callback
must account for the actual instruction, system and creative-execution context.
No group is split, evidence shortened, or source rewritten to make it fit.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from core.planning_context import planning_context, review_context
from core.planning_coordinated import allowed_global_fields
from core.skeleton_projection import chapter_locations, chapter_source

VERSION = 'coordinated-repair-context.v1'
_RESERVED = {
    'candidate', 'frozen_global_design', 'review', 'requested_chapters',
    'complete_adopted_source', 'adopted_source_projection', 'structured_brief',
    'immutable_contracts', 'context_representation', 'review_representation',
    'repair_scope', 'repair_dependencies', 'candidate_chapter_hashes',
    'allowed_global_fields', 'frozen_global_design_reference',
}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _inputs(candidate, review, source, brief, locked):
    if not isinstance(candidate, dict) or not isinstance(candidate.get('chapters'), list):
        raise ValueError('Repair candidate must contain complete ordered chapters')
    chapters = candidate['chapters']
    if (not chapters or any(not isinstance(c, dict) or type(c.get('number')) is not int
                            for c in chapters)
            or [c['number'] for c in chapters] != list(range(1, len(chapters) + 1))):
        raise ValueError('Repair candidate must contain complete ordered chapters')
    roadmap = candidate.get('chapter_map')
    if (not isinstance(roadmap, list)
            or any(not isinstance(c, dict) or type(c.get('number')) is not int for c in roadmap)
            or sorted(c['number'] for c in roadmap) != list(range(1, len(chapters) + 1))):
        raise ValueError('Repair candidate must contain a complete unique chapter map')
    if not isinstance(source, str) or not source.strip():
        raise ValueError('Repair requires the complete frozen source')
    if not isinstance(brief, dict) or not isinstance(locked, dict):
        raise ValueError('Repair brief and immutable contracts must be objects')
    if (not isinstance(review, dict) or review.get('status') != 'blocked'
            or not isinstance(review.get('issues'), list) or not review['issues']
            or any(not isinstance(issue, dict) for issue in review['issues'])):
        raise ValueError('Repair requires a blocked review with complete issue objects')
    return chapters, {k: deepcopy(v) for k, v in candidate.items() if k != 'chapters'}


def _numbers(value, count, label, *, empty=False):
    if (not isinstance(value, (list, tuple)) or (not value and not empty)
            or any(type(n) is not int or not 1 <= n <= count for n in value)
            or len(set(value)) != len(value)):
        raise ValueError(f'{label} must contain unique chapter numbers within the candidate')
    return sorted(value)


def _base(source, brief, locked, count, extra_context):
    if extra_context is not None and not isinstance(extra_context, dict):
        raise ValueError('Extra repair context must be an object')
    extra = deepcopy(extra_context or {})
    if _RESERVED.intersection(extra):
        raise ValueError('Extra repair context cannot replace frozen inputs or coverage')
    if 'chapter_count' in extra and (type(extra['chapter_count']) is not int
                                     or extra['chapter_count'] != count):
        raise ValueError('Repair context chapter count differs from complete candidate')
    return {**planning_context(source, brief, locked), **extra, 'chapter_count': count}


def _issue_view(review, issues):
    # Old review windows describe historical inspection coverage, not repair
    # dependencies. Preserve every issue byte and other review-level data.
    return {**{k: deepcopy(v) for k, v in review.items()
               if k not in {'issues', 'review_scope'}}, 'issues': deepcopy(issues)}


def _bindings(candidate, global_design, review, source):
    return {'representation': VERSION, 'candidate': _hash(candidate),
            'global': _hash(global_design), 'review': _hash(review),
            'source_sha256': hashlib.sha256(source.encode()).hexdigest()}


def _source_projection(source, requested, dependencies, included, count):
    projection = chapter_source(source, requested, count)
    if projection['mode'] == 'complete' or projection['included_chapters'] == included:
        return projection
    locations = chapter_locations(source, count)
    if len(locations) != count:
        # An ambiguous or empty original chapter cannot authorize omission.
        return chapter_source(source, list(range(1, count + 1)), count)
    omitted = [n for n in range(1, count + 1) if n not in included]
    chunks, cursor = [], 0
    for number in omitted:
        start, stop = locations[number]['source_chars']
        chunks.append(source[cursor:start])
        cursor = stop
    text = ''.join(chunks) + source[cursor:]
    projection.update(text=text, dependency_chapters=list(dependencies),
                      included_chapters=list(included), omitted_chapters=omitted,
                      projected_bytes=len(text.encode()),
                      reason='All global sections retained verbatim, with complete requested and '
                             'adjacent chapter plans plus explicit dependency chapter plans. '
                             'Only declared distant chapter-plan bodies are omitted.')
    return projection


def build_plan_context(candidate, review, source, brief, locked, extra_context=None):
    """Plan dependencies using full source, global invariants and all issues.

    Detailed candidate chapter bodies and derived chapter-map entries are omitted;
    every original source chapter plan remains. This view cannot certify the
    omitted candidate content or treat it as absent from the saved book.
    """
    chapters, global_design = _inputs(candidate, review, source, brief, locked)
    numbers = list(range(1, len(chapters) + 1))
    context = _base(source, brief, locked, len(chapters), extra_context)
    context.update(frozen_global_design_reference='candidate contains global invariants; chapter-map coverage is declared separately',
                   review=_issue_view(review, review['issues']),
                   repair_dependencies=_bindings(candidate, global_design, review, source),
                   repair_scope={
                       'schema': VERSION, 'mode': 'dependency_plan',
                       'requested_chapters': [], 'dependency_chapters': [],
                       'included_chapters': [], 'omitted_candidate_chapters': numbers,
                       'included_source_chapters': numbers, 'omitted_source_chapters': [],
                       'global_candidate_fields': [k for k in global_design if k != 'chapter_map'],
                       'omitted_global_candidate_fields': ['chapter_map'],
                       'included_chapter_map_entries': [], 'omitted_chapter_map_entries': numbers,
                       'original_issue_indices': list(range(len(review['issues']))),
                       'historical_review_windows_omitted': 'review_scope' in review,
                       'rule': 'All global invariants and the complete adopted source are present. '
                               'Detailed candidate chapter bodies and candidate chapter_map entries '
                               'are NOT supplied; original source chapter plans remain complete. Plan requested '
                               'repairs and explicit dependencies from these inputs; do not claim '
                               'to have inspected the omitted candidate bodies.',
                   })
    view = {k: v for k, v in global_design.items() if k != 'chapter_map'}
    return review_context(context, {**view, 'chapters': []}, complete_source=source)


def build_repair_context(candidate, frozen_global_design, review, requested_chapters, *,
                         source, brief, locked, dependency_chapters=(), issues=None,
                         original_issue_indices=None, extra_context=None):
    """Build one indivisible repair group, keeping all supplied evidence intact."""
    chapters, global_design = _inputs(candidate, review, source, brief, locked)
    if not isinstance(frozen_global_design, dict) or _hash(frozen_global_design) != _hash(global_design):
        raise ValueError('Frozen global design differs from the original repair candidate')
    count = len(chapters)
    requested = _numbers(requested_chapters, count, 'Requested chapters')
    dependencies = _numbers(dependency_chapters, count, 'Dependency chapters', empty=True)
    if set(requested).intersection(dependencies):
        raise ValueError('Dependency chapters must be separate from requested chapters')
    selected_issues = review['issues'] if issues is None else issues
    if not isinstance(selected_issues, list) or not selected_issues:
        raise ValueError('Each repair group needs complete original issue objects')
    if original_issue_indices is not None:
        indices = list(original_issue_indices) if isinstance(original_issue_indices, (list, tuple)) else []
        if (len(indices) != len(selected_issues)
                or any(type(i) is not int or not 0 <= i < len(review['issues']) for i in indices)
                or len(set(indices)) != len(indices)
                or any(_hash(issue) != _hash(review['issues'][index])
                       for issue, index in zip(selected_issues, indices))):
            raise ValueError('Original issue indices must bind every exact group issue uniquely')
    elif issues is None:
        # The default is the entire original list, whose indices are unambiguous
        # even if two findings happen to contain byte-identical evidence.
        indices = list(range(len(review['issues'])))
    else:
        indices = []
        for issue in selected_issues:
            matches = [i for i, original in enumerate(review['issues']) if _hash(issue) == _hash(original)]
            if not isinstance(issue, dict) or len(matches) != 1 or matches[0] in indices:
                raise ValueError('Repair group issue is changed, duplicated, ambiguous, or absent; '
                                 'supply exact original issue indices')
            indices.append(matches[0])
    included = sorted(set(dependencies).union(
        n for r in requested for n in (r - 1, r, r + 1) if 1 <= n <= count))
    projection = _source_projection(source, requested, dependencies, included, count)
    context = _base(source, brief, locked, count, extra_context)
    context.pop('complete_adopted_source')
    context.get('context_representation', {}).update(source_contracts_in_immutable_contracts=[])
    context.update(
        adopted_source_projection=projection,
        frozen_global_design_reference='candidate contains global invariants; chapter-map coverage is declared separately',
        requested_chapters=requested,
        review=_issue_view(review, selected_issues),
        allowed_global_fields=allowed_global_fields(candidate, requested),
        candidate_chapter_hashes={str(n): _hash(chapters[n - 1]) for n in requested},
        repair_dependencies={**_bindings(candidate, global_design, review, source),
                             'requested_chapters': requested, 'dependency_chapters': dependencies,
                             'issues': _hash(selected_issues)},
        repair_scope={
            'schema': VERSION, 'mode': 'chapter_repair',
            'requested_chapters': requested, 'dependency_chapters': dependencies,
            'included_chapters': included,
            'readonly_chapters': [n for n in included if n not in requested],
            'omitted_candidate_chapters': [n for n in range(1, count + 1) if n not in included],
            'included_source_chapters': list(projection['included_chapters']),
            'omitted_source_chapters': list(projection['omitted_chapters']),
            'global_candidate_fields': list(global_design), 'original_issue_indices': indices,
            'complete_global_candidate_fields': [k for k in global_design if k != 'chapter_map'],
            'included_chapter_map_entries': included,
            'omitted_chapter_map_entries': [n for n in range(1, count + 1) if n not in included],
            'historical_review_windows_omitted': 'review_scope' in review,
            'rule': 'candidate.chapters and candidate.chapter_map contain complete selected entries. '
                    'All remaining original global fields are present unchanged; only equal '
                    'code-owned contracts may be referenced in immutable_contracts. '
                    'All original global source sections and the declared source chapters are '
                    'retained verbatim; only identical terminal contracts may be represented in '
                    'immutable_contracts. Requested chapters include adjacent context; explicit '
                    'dependencies are included in full. Only requested_chapters may be replaced; dependencies and adjacent '
                    'chapters are read-only. Omitted distant chapter bodies are neither missing '
                    'from the saved book nor inspected by this request. Omitted distant chapter_map '
                    'entries also remain in the saved candidate and are NOT reread by this request. Issue indices in this '
                    'request address the supplied review.issues, starting at zero. allowed_global_fields '
                    'paths address original full-candidate array positions, never projected list positions.',
        })
    return review_context(context, {**global_design,
                                   'chapter_map': [deepcopy(entry) for entry in global_design['chapter_map']
                                                   if entry['number'] in included],
                                   'chapters': [deepcopy(chapters[n - 1]) for n in included]},
                          complete_source=source)


def repair_contexts(groups, candidate, frozen_global_design, review, *, source, brief, locked,
                    fits, extra_context=None):
    """Preflight EVERY group before returning any dispatchable request.

    ``fits(context)`` must include instruction/system bytes, envelope reserve and
    frozen creative injection. False refuses the entire sequence before calls.
    Groups are causal units and are never split to satisfy transport capacity.
    """
    if not isinstance(groups, list) or not groups or not callable(fits):
        raise ValueError('Repair requires explicit nonempty groups and a full-request capacity callback')
    contexts = []
    for index, group in enumerate(groups, 1):
        required = {'requested_chapters', 'dependency_chapters', 'issues'}
        if (not isinstance(group, dict) or not required.issubset(group)
                or not set(group).issubset(required | {'original_issue_indices'})):
            raise ValueError('Repair groups must declare requested chapters, dependencies and exact issues')
        context = build_repair_context(candidate, frozen_global_design, review,
            group['requested_chapters'], source=source, brief=brief, locked=locked,
            dependency_chapters=group['dependency_chapters'], issues=group['issues'],
            original_issue_indices=group.get('original_issue_indices'),
            extra_context=extra_context)
        if not fits(deepcopy(context)):
            raise ValueError(f'Repair group {index} and its complete source, global design, issue evidence '
                             'and chapter dependencies exceed the connection input limit; nothing was '
                             'truncated, no group was split and no repair calls were made. '
                             'Use a connection with a larger input limit.')
        contexts.append(context)
    return contexts
