"""Source-bound corrections to existing role metadata and unresolved role identity."""
from __future__ import annotations

from copy import deepcopy
import re

from core.identity_constraints import validate_identity_constraints
from core.outline_planning import _json, content_hash
from core.planning_capacity import input_limit
from core import planning_residual_edits as narrative
from core import planning_residual_evidence as evidence


INSTRUCTION = """STAGE: coordinated_residual_identity
Resolve only this finding's existing role metadata and identity uncertainty. A prior
plan or review proposes a correction, but only adopted source and verified author
choices establish author facts. permitted_characters are existing role records.
Keep every stable ID, name, alias, personal history and chapter binding unchanged.
Record source-supported pronouns independently from gender. A source pronoun alone
does not establish an otherwise unstated gender fact. Fill or correct gender only
when the adopted source establishes it. Do not infer from a name, occupation or
romantic role, and do not transfer attributes between possibly identical roles.
Return {"identity_fields":[{"character_id":"existing_id","field":"pronouns",
"before":"exact current value","value":"he/him","source_indices":[0]}],
"identity_relations":[{"character_ids":["role_a","role_b"],"relation":"unknown",
"reason":"why their relationship remains unestablished","source_evidence":[
{"character_id":"role_a","source_index":0},{"character_id":"role_b","source_index":1}]}]}.
identity_fields supports only gender and pronouns. Use before=null for an absent
field. The indexes address adopted_passages exactly. Return [] for unnecessary
operations. Correct only attributes implicated by the finding. Preserve other
compatible candidate choices and prior_identity_repairs. Source silence does not
prohibit a compatible candidate-planned transition; do not expand this metadata
operation into a narrative rewrite. An unknown relation records uncertainty, not
a merger or a claim of distinct people. Both roles require their own original
occurrence evidence. Do not emit an already recorded pair. The complete candidate
still receives semantic review after these proposals are validated."""

_CHARACTER_PATH = re.compile(r'/characters/(0|[1-9][0-9]*)(?:/.*)?')


def _targets(candidate, finding, item):
    text = _json(finding)
    ids = {row['id'] for row in candidate['characters']
           if re.search(r'(?<![\w:-])' + re.escape(row['id']) + r'(?![\w:-])', text)}
    for reference in item.get('candidate_evidence', []):
        match = _CHARACTER_PATH.fullmatch(reference.get('path', ''))
        if match and int(match[1]) < len(candidate['characters']):
            ids.add(candidate['characters'][int(match[1])]['id'])
    return ids


def _appearances(candidate):
    result = {row['id']: set() for row in candidate['characters']}
    patterns = {key: re.compile(r'(?<![\w:-])' + re.escape(key) + r'(?![\w:-])') for key in result}
    # Roles can occur in nested scene contracts, chapter references or roadmap
    # obligations. Inspect all fields by explicit ID, without guessing by name.
    for section in ('chapter_map', 'chapters'):
        for chapter in candidate.get(section, []):
            text = _json(chapter)
            for key, pattern in patterns.items():
                if pattern.search(text):
                    result[key].add(chapter['number'])
    return result


def build_context(planner, candidate, finding, item, *, held_ids=(), prior_repairs=()):
    targets = _targets(candidate, finding, item)
    if not targets:
        return None
    affected, writable = set(item['affected_chapters']), set(item['writable_chapters'])
    dependencies = set(item['dependency_chapters'])
    if (any(type(n) is not int for n in affected | writable | dependencies)
            or not writable <= affected or writable & dependencies):
        raise ValueError('Identity repair requires valid per-finding chapter permissions')
    appearances = _appearances(candidate)
    allowed = targets - set(held_ids)
    if not allowed:
        return None
    # A shared metadata correction needs every explicit occurrence as evidence,
    # not permission to rewrite those chapters. Discover dependencies locally;
    # a proposal's incomplete chapter list must not strand an otherwise valid fix.
    occurrences = set().union(*(appearances[key] for key in allowed))
    discovered = occurrences - affected - dependencies
    inspected = affected | dependencies | occurrences
    scope_discovery = {'policy': 'existing-role-occurrences-v1',
                       'declared_affected_chapters': sorted(affected),
                       'declared_dependency_chapters': sorted(dependencies),
                       'added_read_only_chapters': sorted(discovered),
                       'role_occurrences': {key: sorted(appearances[key]) for key in sorted(allowed)}}
    source = planner.source
    paragraphs = narrative._quote_context(source, item['source_quotes'])
    inspected_item = {**item, 'dependency_chapters': sorted(dependencies | discovered)}
    author, _, _ = evidence.scoped_material(planner, candidate, inspected_item)
    existing = validate_identity_constraints(candidate.get('identity_constraints'), appearances, source=source)
    return {'finding': deepcopy(finding), 'issue_index': item['issue_index'],
            'resolution_basis': item['reason'], 'affected_chapters': sorted(affected),
            'writable_chapters': sorted(writable), 'read_only_chapters': sorted(inspected - writable),
            'scope_discovery': scope_discovery,
            'candidate_chapters': [deepcopy(row) for row in candidate['chapters'] if row['number'] in inspected],
            'chapter_map': [deepcopy(row) for row in candidate['chapter_map'] if row['number'] in inspected],
            'permitted_characters': [deepcopy(row) for row in candidate['characters'] if row['id'] in allowed],
            'role_occurrences': {key: sorted(appearances[key]) for key in sorted(allowed)},
            'existing_identity_constraints': existing, 'adopted_passages': list(item['source_quotes']),
            'source_paragraphs': paragraphs, 'source_material': author,
            'prior_identity_repairs': deepcopy(list(prior_repairs)),
            **{key: deepcopy(item[key]) for key in ('prior_author_choices', 'binding_author_decision') if key in item}}


def _indices(values, quotes):
    if (not isinstance(values, list) or not values or any(type(i) is not int or not 0 <= i < len(quotes) for i in values)
            or len(set(values)) != len(values)):
        raise ValueError('Identity correction requires valid adopted-source indices')
    return [quotes[i] for i in values]


def apply(candidate, context, response, source):
    if (not isinstance(response, dict) or set(response) != {'identity_fields', 'identity_relations'}
            or any(not isinstance(value, list) for value in response.values())):
        raise ValueError('Identity repair requires field corrections and identity relations')
    allowed = {row['id']: row for row in context['permitted_characters']}
    quotes = context['adopted_passages']
    if not quotes or any(not isinstance(q, str) or not q.strip() or q not in source for q in quotes):
        raise ValueError('Identity repair requires exact original author passages')
    derived = deepcopy(candidate)
    rows = {row['id']: row for row in derived['characters']}
    written = set()
    for change in response['identity_fields']:
        if not isinstance(change, dict) or set(change) != {'character_id', 'field', 'before', 'value', 'source_indices'}:
            raise ValueError('Identity field correction requires a source-bound existing field')
        key, field, value = change['character_id'], change['field'], change['value']
        if (not isinstance(key, str) or key not in allowed or field not in ('gender', 'pronouns')
                or (key, field) in written or not isinstance(value, str) or value.strip() != value
                or value == (allowed[key].get(field) or '')):
            raise ValueError('Identity correction exceeds its unique role and attribute permissions')
        if change['before'] != allowed[key].get(field) or rows[key].get(field) != change['before']:
            raise ValueError('Identity correction references a stale original value')
        _indices(change['source_indices'], quotes)
        rows[key][field] = value
        written.add((key, field))
    constraints = validate_identity_constraints(candidate.get('identity_constraints'), rows, source=source)
    pairs = {tuple(row['character_ids']) for row in constraints}
    for change in response['identity_relations']:
        if (not isinstance(change, dict) or set(change) != {'character_ids', 'relation', 'reason', 'source_evidence'}
                or not isinstance(change['character_ids'], list) or len(change['character_ids']) != 2
                or any(not isinstance(key, str) or key not in allowed for key in change['character_ids'])
                or not isinstance(change['source_evidence'], list)):
            raise ValueError('Identity relation must stay within the finding’s permitted existing roles')
        cited = []
        for reference in change['source_evidence']:
            if not isinstance(reference, dict) or set(reference) != {'character_id', 'source_index'}:
                raise ValueError('Identity relation needs a source occurrence for each role')
            quote, = _indices([reference['source_index']], quotes)
            cited.append({'character_id': reference['character_id'], 'quote': quote})
        record, = validate_identity_constraints([{**change, 'source_evidence': cited}], allowed, source=source)
        pair = tuple(record['character_ids'])
        if pair in pairs:
            raise ValueError('Identity relation is already registered or duplicated')
        pairs.add(pair)
        constraints.append(record)
    if response['identity_relations']:
        derived['identity_constraints'] = validate_identity_constraints(constraints, rows, source=source)
    return derived


def repair(planner, candidate, review, evidence_plan, *, blocked_indices):
    appearances = _appearances(candidate)
    held_ids = set()
    for item in evidence_plan['issues']:
        if item['issue_index'] in blocked_indices:
            held_ids.update(_targets(candidate, review['issues'][item['issue_index']], item))
            held_ids.update(key for key, chapters in appearances.items() if chapters & set(item['affected_chapters']))
    derived, audit = candidate, []
    limit = input_limit(planner.client, negotiate=True)
    for item in evidence_plan['issues']:
        index = item['issue_index']
        if (index in blocked_indices or item['classification'] not in ('contradiction', 'continuity_gap')
                or not item['writable_chapters']):
            continue
        context = build_context(planner, derived, review['issues'][index], item,
                                held_ids=held_ids, prior_repairs=audit)
        if context is None:
            continue
        def validate(value):
            apply(derived, context, value, planner.source)
            return value
        patch, checkpoint = evidence.checkpoint(planner, 'identity', content_hash(derived), INSTRUCTION,
                                                context, validate, limit)
        derived = apply(derived, context, patch, planner.source)
        audit.append({'operation': 'identity_metadata', 'issue_index': index, 'checkpoint': checkpoint,
                      'proposal': deepcopy(patch), 'source_quotes': list(item['source_quotes']),
                      'scope_discovery': deepcopy(context['scope_discovery'])})
    return derived, audit
