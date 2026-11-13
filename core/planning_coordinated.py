"""Pure contracts for bounded, source-bound coordinated outline correction.

These validators never certify literary correctness. A caller must validate every
replacement through the existing chapter/global gates and freshly review the
complete assembled candidate, including disputed original findings.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

VERSION = 'coordinated-repair-v1'
REVIEW_NAME = 'review-coordinated-repair-v1'
PLAN_INSTRUCTION = '''STAGE: coordinated_repair_plan
Plan one bounded repair of the exact saved review findings. Read the complete
adopted source and declared global invariants in candidate, located through
frozen_global_design_reference. Candidate chapter bodies and derived chapter_map
entries are deliberately omitted at this dependency-planning stage; the complete
source chapter plans remain. Do not claim to have inspected omitted candidate data. For each
zero-based review issue return its classification, a concrete source-grounded
reason, requested_chapters that actually need correction, dependency_chapters that
must be read unchanged, and exact source_quotes. Include every issue exactly once.
contradiction and continuity_gap require requested chapters. interpretation means
the finding may be unsupported or advisory: explain it for fresh review, never
silently dismiss it or mark the book passed. For a chapter-scoped interpretation,
include that chapter in dependency_chapters so the reviewer reads its actual design.
source_conflict means author input is
needed; do not invent a canon decision. Use only existing chapter numbers. For a
chapter-scoped material correction include that chapter. Dependencies and requested
chapters are disjoint. A global finding may require coordinated chapter changes,
not replacement of character identities, endings, author choices or contracts.
A missing transition may receive the minimal bridge between source-established
events without inventing a major choice; that gap is not itself source_conflict.
Return ONLY {issues:[{issue_index:integer,
classification:'contradiction'|'continuity_gap'|'interpretation'|'source_conflict',
reason:string,requested_chapters:[integer],dependency_chapters:[integer],
source_quotes:[string]}]}. This plan is not approval or semantic review.
'''
INSTRUCTION = '''STAGE: coordinated_repair
Correct only requested_chapters against the adopted source, global invariants in
candidate (see frozen_global_design_reference), exact saved review issues and
supplied unchanged dependency chapters. The repair_scope explicitly declares the
included chapter_map entries and complete chapter bodies, plus distant omissions.
Do not claim to have inspected omitted entries; the full saved candidate remains
immutable on the server and receives a complete assembled review afterward. Preserve
source chapter assignments and all author choices. A missing transition may be
bridged minimally between two source-established events without a new major canon
decision; do not call every missing transition a source conflict. Return complete replacements
only for chapters actually changed. Every replacement gives the supplied exact
before_sha256, full after chapter, issue_indices and an exact source_quote. Keep
all existing fields, scene/list entries, IDs, source anchors and word budgets.
Execution contract structure, IDs, enums and numeric values are immutable; its
narrative text may be clarified consistently with the source. Do not shorten the
book, remove scenes, summarize away detail, invent identities or rewrite endings.
Global edits are optional and limited to allowed_global_fields: existing narrative
chapter_map fields for requested chapters. Use the supplied original JSON-pointer
paths; their indices refer to the full saved map, not the projected subset list.
Give exact before/after values. Never
edit immutable source, characters, global endings, classification or other contracts.
Return {status:'corrected'|'requires_author',reason:string,
issues:[{issue_index:integer,status:'corrected'|'recheck'|'requires_author',
reason:string,source_quotes:[string],chapter_numbers:[integer]}],
chapter_replacements:[{number:integer,before_sha256:string,after:object,
issue_indices:[integer],source_quote:string}],
global_edits:[{path:string,before:string,after:string,
issue_indices:[integer],source_quote:string}]}.
Cover every supplied issue exactly once. corrected requires an actual associated
change. recheck requires source-grounded rationale and no claimed correction; an
unsupported interpretation or advisory remains for the mandatory fresh review.
If any issue needs a new author decision or forbidden edit, use requires_author
and return no edits or replacements. Chapter numbers in dispositions explicitly
list the affected requested chapters; edits cannot reach unlisted chapters.
This output never passes review. The complete candidate, original findings and
rationales must be freshly reviewed before existing publication gates run.
'''

_MAP_FIELDS = ('purpose', 'incoming_consequence', 'outgoing_commitment')
_IDENTITY_FIELDS = frozenset({'id', 'number', 'chapter', 'schema_version', 'schema',
                              'target_words', 'source_anchor_ids'})
_CONTRACT_ENUM_FIELDS = frozenset({'type', 'status', 'required_status', 'kind', 'mode',
                                  'payoff_mode', 'outcome_match_mode', 'priority',
                                  'reader_jobs', 'belonging_anchors', 'resource_dimension',
                                  'satisfaction_type', 'hook_type'})


def chapter_hash(value: Any) -> str:
    """The same canonical value hash used by outline planner checkpoints."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _object(value: Any, fields: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f'{label} has invalid fields')


def _text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonblank text')


def _numbers(value: Any, existing: set[int], label: str) -> set[int]:
    if (not isinstance(value, list)
            or any(type(n) is not int or n not in existing for n in value)
            or len(set(value)) != len(value)):
        raise ValueError(f'{label} must list unique existing chapter numbers')
    return set(value)


def _existing(candidate: dict) -> set[int]:
    entries = candidate.get('chapter_map')
    if (not isinstance(entries, list) or not entries
            or any(not isinstance(c, dict) or type(c.get('number')) is not int for c in entries)):
        raise ValueError('Candidate requires a complete existing chapter map')
    numbers = {c['number'] for c in entries}
    if len(numbers) != len(entries) or numbers != set(range(1, len(entries) + 1)):
        raise ValueError('Candidate chapter map is incomplete or duplicated')
    return numbers


def _issues(review: dict, existing: set[int]) -> list[dict]:
    issues = review.get('issues')
    if not isinstance(issues, list) or not issues:
        raise ValueError('Coordinated repair requires existing review issues')
    for issue in issues:
        if not isinstance(issue, dict) or issue.get('scope') not in {'global', 'chapter'}:
            raise ValueError('Review issue has invalid scope')
        if issue['scope'] == 'chapter':
            _numbers([issue.get('chapter')], existing, 'Review issue chapter')
    return issues


def _index(value: Any, issues: list[dict], seen: set[int]) -> int:
    if type(value) is not int or not 0 <= value < len(issues) or value in seen:
        raise ValueError('Every review issue must be listed exactly once')
    seen.add(value)
    return value


def _quote(value: Any, source: str) -> None:
    if not isinstance(value, str) or not value.strip() or value not in source:
        raise ValueError('Repair evidence must be an exact adopted-source quotation')


def _quotes(values: Any, source: str) -> None:
    if not isinstance(values, list) or not values:
        raise ValueError('Each issue needs exact adopted-source quotations')
    for value in values:
        _quote(value, source)


def validate_plan(candidate: dict, review: dict, source: str, value: dict, *, verified_global_references=frozenset()) -> dict:
    """Validate full issue accounting without modifying the saved candidate."""
    _object(value, {'issues'}, 'Coordinated repair plan')
    existing = _existing(candidate)
    issues = _issues(review, existing)
    if not isinstance(value['issues'], list) or len(value['issues']) != len(issues):
        raise ValueError('Repair plan must cover every review issue')
    seen: set[int] = set()
    for item in value['issues']:
        _object(item, {'issue_index', 'classification', 'reason', 'requested_chapters',
                       'dependency_chapters', 'source_quotes'}, 'Repair plan issue')
        index = _index(item['issue_index'], issues, seen)
        classification = item['classification']
        if not isinstance(classification, str) or classification not in {
                'contradiction', 'continuity_gap', 'interpretation', 'source_conflict'}:
            raise ValueError('Repair plan issue has an invalid classification')
        _text(item['reason'], 'Repair plan reason')
        _quotes(item['source_quotes'], source)
        requested = _numbers(item['requested_chapters'], existing, 'Requested chapters')
        dependencies = _numbers(item['dependency_chapters'], existing, 'Dependency chapters')
        if requested & dependencies:
            raise ValueError('Requested and unchanged dependency chapters must be disjoint')
        if classification in {'contradiction', 'continuity_gap'}:
            if not requested and not (issues[index]['scope'] == 'global' and index in verified_global_references):
                raise ValueError('A material correction must request affected chapters')
            if issues[index]['scope'] == 'chapter' and issues[index]['chapter'] not in requested:
                raise ValueError('A chapter finding must include its affected chapter')
        elif requested:
            raise ValueError('Interpretation or source conflict cannot authorize chapter edits')
        elif (classification == 'interpretation' and issues[index]['scope'] == 'chapter'
              and issues[index]['chapter'] not in dependencies):
            raise ValueError('A chapter interpretation must read its original affected chapter')
    return copy.deepcopy(value)


def allowed_global_fields(candidate: dict, requested_chapters: list[int]) -> dict[str, str]:
    requested = _numbers(requested_chapters, _existing(candidate), 'Requested chapters')
    return {f'/chapter_map/{index}/{key}': item[key]
            for index, item in enumerate(candidate['chapter_map']) if item['number'] in requested
            for key in _MAP_FIELDS if isinstance(item.get(key), str)}


def _preserve_structure(before: Any, after: Any, path: str, *, contract: bool = False) -> None:
    """Protect saved structure and IDs; semantic fidelity needs fresh review."""
    if type(before) is not type(after):
        raise ValueError(f'Chapter replacement changes saved field type at {path}')
    if isinstance(before, dict):
        if not set(before).issubset(after):
            raise ValueError(f'Chapter replacement removes saved fields at {path}')
        if contract and set(before) != set(after):
            raise ValueError(f'Chapter replacement changes execution contract fields at {path}')
        for key, old in before.items():
            protected = (key in _IDENTITY_FIELDS or key.endswith(('_id', '_ids'))
                         or (contract and key in _CONTRACT_ENUM_FIELDS))
            if protected and chapter_hash(old) != chapter_hash(after[key]):
                raise ValueError(f'Chapter replacement changes immutable identity or contract at {path}/{key}')
            _preserve_structure(old, after[key], f'{path}/{key}', contract=contract or key == 'execution_contract')
    elif isinstance(before, list):
        if len(after) < len(before) or (contract and len(after) != len(before)):
            raise ValueError(f'Chapter replacement removes saved list entries at {path}')
        for index, old in enumerate(before):
            _preserve_structure(old, after[index], f'{path}/{index}', contract=contract)
    elif isinstance(before, str):
        if before.strip() and not after.strip():
            raise ValueError(f'Chapter replacement empties saved story text at {path}')
    elif contract and before != after:
        raise ValueError(f'Chapter replacement changes execution contract value at {path}')


def apply(candidate: dict, review: dict, source: str, value: dict,
          *, requested_chapters: list[int]) -> dict:
    """Apply a checked group proposal to a copy; never approve or mutate source."""
    _object(value, {'status', 'reason', 'issues', 'chapter_replacements', 'global_edits'},
            'Coordinated repair')
    if not isinstance(value['status'], str) or value['status'] not in {'corrected', 'requires_author'}:
        raise ValueError('Coordinated repair has invalid status')
    _text(value['reason'], 'Coordinated repair reason')
    existing = _existing(candidate)
    requested = _numbers(requested_chapters, existing, 'Requested chapters')
    issues = _issues(review, existing)
    if not isinstance(value['issues'], list) or len(value['issues']) != len(issues):
        raise ValueError('Coordinated repair must account for every review issue')
    if not isinstance(value['chapter_replacements'], list) or not isinstance(value['global_edits'], list):
        raise ValueError('Coordinated repair edits must be lists')
    dispositions: dict[int, dict] = {}
    seen: set[int] = set()
    for item in value['issues']:
        _object(item, {'issue_index', 'status', 'reason', 'source_quotes', 'chapter_numbers'},
                'Repair issue disposition')
        index = _index(item['issue_index'], issues, seen)
        if not isinstance(item['status'], str) or item['status'] not in {'corrected', 'recheck', 'requires_author'}:
            raise ValueError('Repair issue has an invalid disposition')
        _text(item['reason'], 'Repair issue reason')
        _quotes(item['source_quotes'], source)
        numbers = _numbers(item['chapter_numbers'], requested, 'Affected chapters')
        if item['status'] == 'corrected':
            if not numbers:
                raise ValueError('Corrected issue must identify affected requested chapters')
            if issues[index]['scope'] == 'chapter' and issues[index]['chapter'] not in numbers:
                raise ValueError('Corrected chapter issue omits its affected chapter')
        dispositions[index] = item
    unresolved = any(item['status'] == 'requires_author' for item in dispositions.values())
    if (value['status'] == 'requires_author') != unresolved:
        raise ValueError('Repair status contradicts issue dispositions')
    if unresolved:
        if value['chapter_replacements'] or value['global_edits']:
            raise ValueError('An unresolved correction cannot apply edits')
        return copy.deepcopy(candidate)

    chapters = candidate.get('chapters')
    if (not isinstance(chapters, list)
            or any(not isinstance(ch, dict) or type(ch.get('number')) is not int for ch in chapters)
            or len(chapters) != len(existing) or {ch['number'] for ch in chapters} != existing):
        raise ValueError('Repair requires all saved chapter bodies without omissions')
    chapter_positions = {ch['number']: index for index, ch in enumerate(chapters)}
    allowed = allowed_global_fields(candidate, requested_chapters)
    result = copy.deepcopy(candidate)
    covered: dict[int, set[int]] = {index: set() for index in dispositions}

    def bind(indices: Any, number: int) -> None:
        if (not isinstance(indices, list) or not indices
                or any(type(i) is not int or i not in dispositions for i in indices)
                or len(set(indices)) != len(indices)):
            raise ValueError('Each change must identify unique covered review issues')
        for index in indices:
            item = dispositions[index]
            if item['status'] != 'corrected' or number not in item['chapter_numbers']:
                raise ValueError('Change exceeds its explicitly corrected issue scope')
            covered[index].add(number)

    replaced: set[int] = set()
    for replacement in value['chapter_replacements']:
        _object(replacement, {'number', 'before_sha256', 'after', 'issue_indices', 'source_quote'},
                'Chapter replacement')
        number = replacement['number']
        if type(number) is not int or number not in requested or number in replaced:
            raise ValueError('Replacement chapter is unexpected or duplicated')
        previous = chapters[chapter_positions[number]]
        after = replacement['after']
        if replacement['before_sha256'] != chapter_hash(previous):
            raise ValueError('Chapter replacement precondition is stale')
        if not isinstance(after, dict) or after.get('number') != number:
            raise ValueError('Replacement must contain the complete matching chapter')
        _preserve_structure(previous, after, f'/chapters/{number}')
        if chapter_hash(previous) == chapter_hash(after):
            raise ValueError('Chapter replacement must make an actual correction')
        _quote(replacement['source_quote'], source)
        bind(replacement['issue_indices'], number)
        result['chapters'][chapter_positions[number]] = copy.deepcopy(after)
        replaced.add(number)

    edited: set[str] = set()
    for edit in value['global_edits']:
        _object(edit, {'path', 'before', 'after', 'issue_indices', 'source_quote'}, 'Global map edit')
        path = edit['path']
        if not isinstance(path, str) or path not in allowed or path in edited:
            raise ValueError('Global edit is outside allowed chapter map fields or repeated')
        if type(edit['before']) is not str or edit['before'] != allowed[path]:
            raise ValueError('Global map edit precondition is stale')
        _text(edit['after'], 'Global map edit text')
        if edit['after'] == edit['before']:
            raise ValueError('Global map edit must make an actual correction')
        _, _, position, key = path.split('/')
        number = candidate['chapter_map'][int(position)]['number']
        _quote(edit['source_quote'], source)
        bind(edit['issue_indices'], number)
        result['chapter_map'][int(position)][key] = edit['after']
        edited.add(path)

    for index, item in dispositions.items():
        if item['status'] == 'corrected' and covered[index] != set(item['chapter_numbers']):
            raise ValueError('Corrected issue must change every explicitly affected chapter')
    return result
