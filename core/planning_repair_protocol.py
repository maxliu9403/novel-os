"""Stable issue/evidence identifiers for new coordinated repair requests only.

The wire codec never changes a chapter body or performs a repair. It references
exact quotations from the already validated dependency plan, then expands the
model's selected identifiers into the existing strict correction contract. Old
contexts, prompts and receipts remain a separate immutable protocol.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from core.planning_context import expand_review_context, review_context
from core.skeleton_projection import chapter_locations

VERSION = 'coordinated-repair-protocol.v2'
INSTRUCTION = '''STAGE: coordinated_repair
Correct only requested_chapters against the adopted source and the declared
candidate context. The repair_scope lists complete supplied chapter bodies and
chapter_map entries and the distant omissions. Preserve all existing fields,
scene/list entries, IDs, source anchors, word budgets, author choices and global
invariants. Return full replacements only for chapters actually changed. Each
replacement uses its supplied before_sha256. Unrequested chapters are read-only.
Execution-contract structure, IDs, enums and numeric values remain immutable.
Only source-consistent narrative text may be clarified. Do not shorten, remove
scenes, replace identities, change endings or invent new decisions.

Preserve source-established actors, actions and mechanisms. A missing transition
may receive only a minimal bridge between established events. Do not replace a
physical action with a different mechanism to explain a continuity gap: for
example, remote hacking/unlocking is not allowed unless the adopted source
already establishes that mechanism. If the source leaves an author decision
unresolved, return requires_author. A source quotation proves provenance, not
that a proposed new action follows from it. Disputed interpretations still need
the mandatory independent review; do not silently certify them as corrected.

Use only the stable issue_id values in review.issues and coordinated_issue_plan.
Do not return numeric issue indices. source_evidence_catalog is the only source
of evidence IDs: its quote fields are exact adopted-source text. The original
review's source_evidence may describe or quote a candidate error, and is NOT
automatically adopted-source evidence. Never translate, paraphrase, invent or
copy a candidate sentence as a source quotation. Select source_evidence_ids for
each issue from its catalog binding. A replacement/global edit's singular
source_evidence_id must belong to at least one of its referenced issue_ids;
each issue must still carry its own source evidence. Return IDs, not quotations.

Optional global edits are restricted to allowed_global_fields. Use those exact
original full-candidate JSON-pointer paths, not projected-list positions, and
give exact before values. All other global fields, source and contracts are
immutable. Every issue must appear exactly once. corrected needs an associated
change; recheck preserves a source-grounded dispute for final review and claims
no correction. If any issue requires author input, return requires_author with
no replacements or global edits. None of these dispositions approves the book.

Return ONLY {status:'corrected'|'requires_author',reason:string,
issues:[{issue_id:string,status:'corrected'|'recheck'|'requires_author',
reason:string,source_evidence_ids:[string],chapter_numbers:[integer]}],
chapter_replacements:[{number:integer,before_sha256:string,after:object,
issue_ids:[string],source_evidence_id:string}],
global_edits:[{path:string,before:string,after:string,
issue_ids:[string],source_evidence_id:string}]}.
The complete assembled candidate and all original findings are reviewed after
strict local validation; only the existing final publication gates can accept it.
'''

_PLAN_FIELDS = {'issue_index', 'classification', 'reason', 'requested_chapters',
                'dependency_chapters', 'source_quotes'}
_INDEX_FIELDS = {'issue_index', 'original_issue_index', 'group_issue_index'}


def _hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f'{label} has invalid fields')


def _list(value, label):
    if not isinstance(value, list):
        raise ValueError(f'{label} must be a list')
    return value


def _bindings(context, source):
    if not isinstance(context, dict) or not isinstance(source, str) or not source:
        raise ValueError('Stable repair protocol requires its frozen context and source')
    original = expand_review_context(context, complete_source=source)
    source_sha = hashlib.sha256(source.encode()).hexdigest()
    dependencies = original.get('repair_dependencies', {})
    if dependencies.get('source_sha256') != source_sha:
        raise ValueError('Stable repair protocol source differs from frozen dependencies')
    review_sha = dependencies.get('review')
    if not isinstance(review_sha, str) or len(review_sha) != 64:
        raise ValueError('Stable repair protocol requires its original review binding')
    issues = original.get('review', {}).get('issues')
    indices = original.get('repair_scope', {}).get('original_issue_indices')
    if (not isinstance(issues, list) or not issues or any(not isinstance(i, dict) for i in issues)
            or not isinstance(indices, list) or len(indices) != len(issues)
            or any(type(i) is not int or i < 0 for i in indices) or len(set(indices)) != len(indices)):
        raise ValueError('Stable repair protocol requires exact original issue bindings')
    plan = original.get('coordinated_issue_plan', {}).get('issues')
    if not isinstance(plan, list):
        raise ValueError('Stable repair protocol requires the validated dependency plan')
    by_index = {}
    for item in plan:
        _object(item, _PLAN_FIELDS, 'Validated plan issue')
        index = item['issue_index']
        if type(index) is not int or index < 0 or index in by_index:
            raise ValueError('Validated plan issue indices must be unique')
        by_index[index] = item
    if any(index not in by_index for index in indices):
        raise ValueError('Stable repair issue is absent from the validated plan')
    locations = chapter_locations(source, original.get('chapter_count', 0))
    entries, issue_records, seen_issue_ids = {}, [], set()
    for local_index, (original_index, issue) in enumerate(zip(indices, issues)):
        identifier = 'i_' + _hash({'review_sha256': review_sha,
                                   'original_issue_index': original_index,
                                   'issue_sha256': _hash(issue)})[:12]
        if identifier in seen_issue_ids:
            raise ValueError('Stable issue identifier collision; no ambiguous request may be sent')
        seen_issue_ids.add(identifier)
        item = by_index[original_index]
        quotes = item['source_quotes']
        if not isinstance(quotes, list) or not quotes:
            raise ValueError('Validated plan issue needs exact adopted-source quotations')
        evidence_ids = []
        for quote in quotes:
            if not isinstance(quote, str) or not quote.strip() or quote not in source:
                raise ValueError('Evidence catalog accepts only exact adopted-source plan quotations')
            spans, offset = [], 0
            while True:
                start = source.find(quote, offset)
                if start < 0:
                    break
                spans.append([start, start + len(quote)])
                offset = start + 1
            digest = _hash({'source_sha256': source_sha, 'quote': quote, 'source_chars': spans})
            evidence_id = 's_' + digest[:12]
            entry = {
                'id': evidence_id, 'sha256': digest, 'kind': 'verbatim_adopted_source', 'quote': quote,
                'source_chars': spans,
                'chapters': sorted({n for n, location in locations.items() for start, end in spans
                                    if location['source_chars'][0] <= start
                                    and end <= location['source_chars'][1]}),
            }
            if evidence_id in entries and entries[evidence_id] != entry:
                raise ValueError('Stable source evidence identifier collision; no ambiguous request may be sent')
            entries[evidence_id] = entry
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
        issue_records.append({'id': identifier, 'local_index': local_index,
                              'issue': issue, 'plan': item, 'evidence_ids': evidence_ids})
    return original, source_sha, review_sha, entries, issue_records


def build(context: dict, source: str) -> dict:
    """Create a reversible wire view without changing any included story data."""
    result, source_sha, review_sha, entries, records = _bindings(context, source)
    candidate = result.pop('candidate')
    result['review']['issues'] = [
        {**{key: deepcopy(value) for key, value in record['issue'].items() if key not in _INDEX_FIELDS},
         'issue_id': record['id']} for record in records]
    result['coordinated_issue_plan'] = {'issues': [
        {**{key: deepcopy(value) for key, value in record['plan'].items()
            if key not in {'issue_index', 'source_quotes'}},
         'issue_id': record['id'], 'source_evidence_ids': list(record['evidence_ids'])}
        for record in records]}
    scope = result['repair_scope']
    scope.pop('original_issue_indices')
    scope['issue_ids'] = [record['id'] for record in records]
    scope['rule'] = (
        'All declared candidate chapters and chapter_map entries are complete. Other global fields '
        'and declared source coverage remain unchanged; identical code-owned contracts may be '
        'referenced in immutable_contracts. Only requested chapters and allowed_global_fields '
        'may change. Dependencies, adjacent chapters and all omitted saved content are read-only. '
        'Omitted content remains on the server and was not inspected by this request. Global edit '
        'paths use original full-candidate indices. All issue/evidence references use the supplied '
        'stable IDs; review source_evidence can quote candidate errors and is not source authority.')
    result['source_evidence_catalog'] = {
        'schema': 'coordinated-source-evidence.v1', 'source_sha256': source_sha,
        'entries': list(entries.values()),
        'issue_bindings': [{'issue_id': record['id'], 'source_evidence_ids': list(record['evidence_ids'])}
                           for record in records],
        'rule': 'Exact adopted-source quotations from the validated scoped issue plan. '
                'Character intervals are zero-based [start,end); repeated quotations retain every '
                'matching source location. Provenance is not proof of a proposed narrative inference.',
    }
    result['wire_protocol'] = {
        'schema': VERSION, 'original_context_sha256': _hash(context),
        'source_sha256': source_sha, 'original_review_sha256': review_sha,
        'scope': 'Only this group’s issue plan is supplied; all original issue accounting stays server-side.',
    }
    # Rebind the existing lossless contract receipt to THIS wire context. Keeping
    # the old receipt after changing protocol metadata would not round-trip.
    return review_context(result, candidate, complete_source=source)


def decode(context: dict, source: str, value: dict) -> dict:
    """Expand trusted IDs to the unchanged strict local contract, without I/O.

    ``context`` is the ORIGINAL server-side group context, not model-supplied
    context/catalog data. The caller still runs its existing contract.apply,
    global/chapter validators and mandatory final semantic review.
    """
    _, _, _, entries, records = _bindings(context, source)
    issue_map = {record['id']: record for record in records}
    _object(value, {'status', 'reason', 'issues', 'chapter_replacements', 'global_edits'}, 'Stable repair response')

    def references(ids):
        if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in issue_map for i in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('Repair changes need unique known issue IDs from this group')
        return [issue_map[i]['local_index'] for i in ids]

    def quote(identifier, issue_ids):
        if (not isinstance(identifier, str) or identifier not in entries
                or not any(identifier in issue_map[i]['evidence_ids'] for i in issue_ids)):
            raise ValueError('Source evidence ID is unknown or outside its referenced issue scope')
        return entries[identifier]['quote']

    canonical_issues, seen = [], set()
    for item in _list(value['issues'], 'Repair issues'):
        _object(item, {'issue_id', 'status', 'reason', 'source_evidence_ids', 'chapter_numbers'}, 'Stable repair issue')
        identifier = item['issue_id']
        indices = references([identifier])
        if identifier in seen:
            raise ValueError('Every stable issue ID must appear exactly once')
        seen.add(identifier)
        evidence = item['source_evidence_ids']
        if (not isinstance(evidence, list) or not evidence
                or any(not isinstance(e, str) for e in evidence) or len(set(evidence)) != len(evidence)):
            raise ValueError('Every repair issue needs unique source evidence IDs')
        canonical_issues.append({'issue_index': indices[0], 'status': deepcopy(item['status']),
            'reason': deepcopy(item['reason']), 'chapter_numbers': deepcopy(item['chapter_numbers']),
            'source_quotes': [quote(e, [identifier]) for e in evidence]})
    if seen != set(issue_map):
        raise ValueError('Every stable issue ID must appear exactly once')
    replacements = []
    for item in _list(value['chapter_replacements'], 'Chapter replacements'):
        _object(item, {'number', 'before_sha256', 'after', 'issue_ids', 'source_evidence_id'}, 'Stable chapter replacement')
        indices = references(item['issue_ids'])
        replacements.append({**{k: deepcopy(item[k]) for k in ('number', 'before_sha256', 'after')},
            'issue_indices': indices, 'source_quote': quote(item['source_evidence_id'], item['issue_ids'])})
    edits = []
    for item in _list(value['global_edits'], 'Global edits'):
        _object(item, {'path', 'before', 'after', 'issue_ids', 'source_evidence_id'}, 'Stable global edit')
        indices = references(item['issue_ids'])
        edits.append({**{k: deepcopy(item[k]) for k in ('path', 'before', 'after')},
            'issue_indices': indices, 'source_quote': quote(item['source_evidence_id'], item['issue_ids'])})
    return {'status': deepcopy(value['status']), 'reason': deepcopy(value['reason']),
            'issues': canonical_issues, 'chapter_replacements': replacements, 'global_edits': edits}
