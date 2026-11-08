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


def windows(candidate, make_context, fits, *, prepare_context=None):
    """Preflight all requests before spending any calls. Never truncate a field.

    Bisect only as needed; preserve complete global design and complete chapter
    objects. Adjacent chapters overlap, so every boundary is inspected together.
    If one chapter plus its dependencies cannot fit, fail before model dispatch.
    """
    chapters = candidate['chapters']
    numbers = [c['number'] for c in chapters]
    if numbers != list(range(1, len(chapters) + 1)):
        raise ValueError('Review candidate must contain all chapters in order')
    result = []

    def plan(requested):
        included = sorted({n for r in requested for n in (r - 1, r, r + 1) if n in numbers})
        view = deepcopy(candidate)
        view['chapters'] = [deepcopy(c) for c in chapters if c['number'] in included]
        context = make_context(requested)
        context.update(candidate=view, review_window={
            'schema': VERSION, 'requested_chapters': requested, 'included_chapters': included,
            'omitted_candidate_chapters': [n for n in numbers if n not in included],
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
            raise ValueError(f'Chapter {requested[0]} and its complete review dependencies exceed the connection input limit; '
                             'nothing was truncated and no review calls were made. Use a connection with a larger input limit.')

    # Start with all chapters, not one request per chapter or planning batch.
    plan(numbers)
    return result
