"""Partition oversized design reviews, with explicit source and candidate coverage."""
from copy import deepcopy

VERSION = 'planning-review-windows.v1'
WINDOW_RULES = '''
This is a bounded review window, NOT a claim to have read the complete candidate.
All global candidate fields and the full chapter roadmap are present. Original
global source sections are present; source/candidate chapter coverage is explicit.
Check every requested chapter against its complete source plan and the adjacent
candidate chapters, including boundary transitions, future obligations and global
causal/relationship rules. Do not treat omitted distant chapter bodies as missing
from the book or as inspected prose. Review each required dimension within this
window; report material global contradictions as scope=global. Chapter issues must
name a supplied candidate chapter. The application combines ALL windows before
accepting coverage; this reply cannot approve the whole book by itself.
'''


class ReviewWindowTooLarge(ValueError):
    """One whole chapter plus its required neighbors does not fit."""


def scoped_prefix(name: str, candidate: dict) -> str:
    from core.outline_planning import content_hash
    return f"{name}-source-v1-{content_hash(candidate)}"


def windows(candidate, make_context, fits, *, prepare_context=None, requested_ranges=None):
    """Preflight all requests before spending any calls. Never truncate a field.

    Bisect only as needed; preserve complete global design and complete chapter
    objects. Adjacent chapters overlap, so every boundary is inspected together.
    If one chapter plus its dependencies cannot fit, fail before model dispatch.
    """
    chapters = candidate['chapters']
    numbers = [c['number'] for c in chapters]
    if numbers != list(range(1, len(chapters) + 1)):
        raise ValueError('Review candidate must contain all chapters in order')
    by_number = {c['number']: c for c in chapters}
    # Retain the original field order for saved requests. Copy only chapter
    # bodies which will actually enter each candidate window.
    global_view = {**candidate, 'chapters': []}
    result = []

    def plan(requested):
        member_numbers = {n for r in requested for n in (r - 1, r, r + 1) if n in by_number}
        included = sorted(member_numbers)
        view = deepcopy(global_view)
        view['chapters'] = [deepcopy(by_number[n]) for n in included]
        context = make_context(requested)
        context.update(candidate=view, review_window={
            'schema': VERSION, 'requested_chapters': requested, 'included_chapters': included,
            'omitted_candidate_chapters': [n for n in numbers if n not in member_numbers],
            'global_candidate_fields': [k for k in candidate if k != 'chapters'],
        })
        if prepare_context is not None:
            context = prepare_context(context)
        if fits(context):
            result.append(context)
        elif len(requested) > 1:
            middle = len(requested) // 2
            plan(requested[:middle])
            plan(requested[middle:])
        else:
            raise ReviewWindowTooLarge(f'Chapter {requested[0]} and its complete review dependencies exceed the connection input limit; '
                             'nothing was truncated and no review calls were made. Use a connection with a larger input limit.')

    # Start with all chapters, not one request per chapter or planning batch.
    if requested_ranges is None:
        requested_ranges = [numbers]
    if (not isinstance(requested_ranges, list) or any(not isinstance(group, list) or not group for group in requested_ranges)
            or [n for group in requested_ranges for n in group] != numbers):
        raise ValueError("Saved review ranges must cover every chapter in its original order")
    for group in requested_ranges:
        plan(group)
    return result


def source_aware_windows(planner, candidate, name, make_context, fits, prepare_context, policy):
    """Try the existing source contract first and retain old window files verbatim."""
    from core.outline_planning import content_hash

    prefix = scoped_prefix(name, candidate)
    deps = {"candidate": content_hash(candidate), "source": planner.identity["source_sha256"],
            "policy": policy, "version": "review-source-selection.v1"}
    manifest = planner._load(prefix + "-ranges", deps, lambda value: value, strict_dependencies=True)
    if manifest is None:
        try:
            return windows(candidate, lambda ns: make_context(ns, False), fits, prepare_context=prepare_context), name
        except ReviewWindowTooLarge:
            # Only a different, strictly recognized source view can reduce a
            # complete-source request. Unknown or already scoped views retain the failure.
            if ("complete_adopted_source" not in make_context([1], False)
                    or "adopted_source_projection" not in make_context([1], True)):
                raise

    def scoped_context(ns):
        context = make_context(ns, True)
        if len(ns) + 2 < planner.chapter_count and "adopted_source_projection" not in context:
            raise ValueError("Bounded source no longer resolves to its original chapter spans")
        return context

    def scoped_fits(context):
        ns = context["review_window"]["requested_chapters"]
        stem = f"{prefix}-window-{ns[0]:03d}-{ns[-1]:03d}"
        # A narrower connection may still consume already recorded judgments.
        # Actual dispatch always verifies the frozen prompt and transport limit.
        return ((planner.cache / f"{stem}.json").is_file() or (planner.cache / f"{stem}-response.json").is_file()
                or fits(context))

    ranges = manifest["requested_ranges"] if manifest is not None else None
    contexts = windows(candidate, scoped_context, scoped_fits, prepare_context=prepare_context, requested_ranges=ranges)
    updated = {"requested_ranges": [ctx["review_window"]["requested_chapters"] for ctx in contexts]}
    if manifest != updated:
        # Adopt only a complete preflight; narrowing the connection subdivides
        # its pending ranges, and a later larger connection keeps that work.
        planner._save(prefix + "-ranges", deps, updated)
    return contexts, prefix
