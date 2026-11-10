"""Expand small, source-bound chapter edits against an immutable saved candidate."""
from copy import deepcopy
from core.planning_chapter_repair import _changes

INSTRUCTION = """
For an authorized chapter replacement, prefer field_edits instead of repeating its full after
object: {number,before_sha256,issue_ids,source_evidence_id,field_edits:[{path:["scenes",0,"goal"],value:"..."}]}.
Paths start at the original chapter, arrays use zero-based integers. Use only unique, existing,
mutable leaf fields. Code reconstructs the entire chapter and validates all original protections.
For each issue, chapter_numbers and edits must stay within that issue's requested_chapters in
coordinated_issue_plan. An affected or dependency chapter grants no write permission. For an
unsubstantiated demand to change author canon use recheck with the exact source rationale.
source_authority distinguishes generated_candidate_evidence from independently checked author
statements. Some generated assumptions, character registrations or dangling payoff links may
already have been repaired in the supplied candidate. If the finding is now resolved, use
recheck with the actual evidence; an unnecessary extra change risks losing a valid story choice.
An author decision in binding_author_choices governs the two quoted passages for that issue.
"""


def expand(value, candidate, requested):
    if not isinstance(value, dict) or not isinstance(value.get('chapter_replacements'), list):
        raise ValueError('Repair response must list chapter_replacements')
    value = deepcopy(value)
    for change in value['chapter_replacements']:
        if not isinstance(change, dict) or 'field_edits' not in change:
            continue
        if 'after' in change or type(change.get('number')) is not int or change['number'] not in requested:
            raise ValueError('Field edits must address a single authorized original chapter')
        edits = change.pop('field_edits')
        if not isinstance(edits, list) or any(not isinstance(e, dict) or set(e) != {'path', 'value'} for e in edits):
            raise ValueError('Chapter field_edits must list path and value')
        number = change['number']
        fields = [{'chapter': number, **e} for e in edits]
        change['after'] = _changes({'edits': fields, 'unresolved': ''}, candidate, [number])['chapters'][0]
    return value
