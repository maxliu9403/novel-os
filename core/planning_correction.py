"""Bounded restoration of source facts and derived chapter references.

The source quote is an auditable anchor, not semantic proof. The caller must
review the entire corrected candidate and run its normal structural gates.
"""
from __future__ import annotations

import copy
import json

VERSION = 'global-source-restoration-v2'
REVIEW_NAME = 'review-global-source-v2'
# Fingerprint of the deployed v1 instruction, retained only to validate and reuse
# its checkpoints. No legacy prompt is called by new executions.
LEGACY_POLICY_HASH = 'dbe5897aae6fa3da69906b6c2fd12e35c2ece3c7272d775c7bc6ef6532d0255f'
INSTRUCTION = '''STAGE: global_reference_repair
Restore omitted source facts and execution chapter references in the saved global candidate.
The original skeleton and its chapter events are authoritative. Template positions
are defaults, not author choices. Distinguish the main dramatic climax from later
character resolution/aftermath. Do not move events to satisfy a template.
Only change paths in allowed_references or allowed_source_fields. Reference edits
use chapter integers. Source fields are text: restore missing background for an
existing character, the reader/market profile, or the pre-chapter Story Lead plan
(placement, length, hook and spoiler boundary, NOT lead prose). These do not change
publication classification or chapter budgets. Source text fields are append-only:
preserve the exact existing string followed by a newline and the missing details;
use before:null only for a missing/null field. A quote is evidence, not permission
to invent or reinterpret canon. Do not introduce new decisions, scenes, identities,
world rules or motivations; do not rewrite chapters, endings, IDs, budgets,
publication metadata or arrays. If canon conflicts, stop for the author.
Derived volume obligations (including volume_XX_climax.due_chapter) are rebuilt
by code from the edited climax_chapter BEFORE review. Do NOT patch these generated
fields or reject a valid reference fix merely because the old obligation is stale.
For each edit give the exact before value, an after value, a verbatim source_quote
from complete_adopted_source, and zero-based
issue_indices it resolves. Every issue must be covered; at most 8 edits.
Return {status:'corrected'|'requires_author',reason:string,
edits:[{path:string,before:integer|string|null,after:integer|string,source_quote:string,issue_indices:[integer]}]}.
If source is contradictory or correcting any issue needs changes outside the allowed
paths, return requires_author with a concrete reason and edits:[]. Never manufacture
a patch to satisfy the format. The complete corrected candidate is reviewed next;
this response does not certify success or author approval.
'''


def references(candidate: dict) -> dict[str, int]:
    allowed = {}
    for field, key in (('volume_contracts', 'climax_chapter'), ('plot_threads', 'resolution_chapter')):
        for index, item in enumerate(candidate.get(field, [])):
            if isinstance(item, dict) and type(item.get(key)) is int:
                allowed[f'/{field}/{index}/{key}'] = item[key]
    return allowed


def source_fields(candidate: dict) -> dict[str, str | None]:
    """Explicit, additive narrative slots; no arbitrary JSON-pointer mutation."""
    fields = {f'/{key}': candidate.get(key) for key in ('reader_profile', 'story_lead_plan')}
    for index, character in enumerate(candidate.get('characters', [])):
        if isinstance(character, dict) and character.get('id'):
            fields[f'/characters/{index}/background'] = character.get('background')
    return {path: value for path, value in fields.items() if value is None or isinstance(value, str)}


def apply(candidate: dict, review: dict, source: str, value: dict, chapter_count: int) -> dict:
    if (not isinstance(value, dict) or set(value) != {'status', 'reason', 'edits'}
            or value.get('status') not in {'corrected', 'requires_author'}
            or not isinstance(value.get('reason'), str) or not value['reason'].strip()
            or len(value['reason']) > 2000 or not isinstance(value.get('edits'), list)
            or len(json.dumps(value, ensure_ascii=False).encode()) > 24000):
        raise ValueError('Global source correction has an invalid contract')
    if value['status'] == 'requires_author':
        if value['edits']:
            raise ValueError('An unresolved correction cannot apply edits')
        return copy.deepcopy(candidate)
    edits = value['edits']
    if not 1 <= len(edits) <= 8:
        raise ValueError('Source correction needs 1–8 explicit edits')
    allowed = references(candidate)
    source_slots = source_fields(candidate)
    seen, covered = set(), set()
    result = copy.deepcopy(candidate)
    issues = review['issues']
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'path', 'before', 'after', 'source_quote', 'issue_indices'}:
            raise ValueError('Reference edit fields are invalid')
        path, before, after = edit['path'], edit['before'], edit['after']
        if not isinstance(path, str) or path in seen:
            raise ValueError('Correction path is invalid or repeated')
        if path in allowed:
            if (type(before) is not int or before != allowed[path]
                    or type(after) is not int or not 1 <= after <= chapter_count or after == before):
                raise ValueError('Reference edit is stale or out of range')
        elif path in source_slots:
            if (before != source_slots[path] or type(before) is not type(source_slots[path])
                    or not isinstance(after, str) or not after.strip() or len(after) > 6000
                    or (before and not after.startswith(before + '\n'))
                    or not after[len(before or ''):].strip()):
                raise ValueError('Source restoration must append to the exact saved text without rewriting it')
        else:
            raise ValueError('Correction is outside allowed paths')
        quote = edit['source_quote']
        if not isinstance(quote, str) or not 8 <= len(quote) <= 2000 or quote not in source:
            raise ValueError('Reference correction needs an exact adopted-source quotation')
        indices = edit['issue_indices']
        if (not isinstance(indices, list) or not indices or any(type(i) is not int or not 0 <= i < len(issues) for i in indices)
                or len(set(indices)) != len(indices) or any(issues[i]['scope'] != 'global' for i in indices)):
            raise ValueError('Reference correction must identify its actual global review issues')
        # The two allowlists restrict syntax, including character array indices.
        parts = path.strip('/').split('/')
        if len(parts) == 1:
            result[parts[0]] = after
        else:
            field, index, key = parts
            result[field][int(index)][key] = after
        seen.add(path)
        covered.update(indices)
    if covered != set(range(len(issues))):
        raise ValueError('Reference correction does not address every review issue')
    return result
