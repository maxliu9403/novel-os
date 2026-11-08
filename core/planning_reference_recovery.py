"""Deterministic recovery of legacy repair citation metadata, never story text.

A completed proposal sometimes quoted the candidate defect instead of the adopted
source. Only that recognizable mistake can inherit already validated plan evidence.
Original responses remain the caller's immutable records. The returned child is
not a model-selected citation or a semantic approval; normal full review is required.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from core import planning_coordinated as contract
from core.skeleton_projection import chapter_locations

VERSION = 'coordinated-reference-recovery-v1'


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _bound_inputs(context: dict, source: str, candidate: dict) -> tuple[dict[int, list[str]], dict]:
    """Refuse stale or fabricated inputs, including a changed projected candidate."""
    scope = context['repair_scope']
    if scope.get('mode') != 'chapter_repair':
        raise ValueError('Metadata recovery requires an original chapter repair context')
    requested = context['requested_chapters']
    if requested != scope['requested_chapters']:
        raise ValueError('Repair scope changed')
    bindings = context['repair_dependencies']
    if (bindings['candidate'] != contract.chapter_hash(candidate)
            or bindings['source_sha256'] != hashlib.sha256(source.encode()).hexdigest()):
        raise ValueError('Original candidate or source binding changed')
    if context['allowed_global_fields'] != contract.allowed_global_fields(candidate, requested):
        raise ValueError('Allowed original map fields changed')
    chapters = {chapter['number']: chapter for chapter in candidate['chapters']}
    if context['candidate_chapter_hashes'] != {
            str(number): contract.chapter_hash(chapters[number]) for number in requested}:
        raise ValueError('Original chapter hash binding changed')
    supplied = context['candidate']
    if not isinstance(supplied, dict) or not isinstance(supplied.get('chapters'), list):
        raise ValueError('Original supplied chapter objects are missing')
    if [item['number'] for item in supplied['chapters']] != scope['included_chapters']:
        raise ValueError('Supplied chapter coverage changed')
    for key, value in supplied.items():
        if key in {'chapters', 'chapter_map'}:
            original = {item['number']: item for item in candidate[key]}
            if (not isinstance(value, list) or len({item['number'] for item in value}) != len(value)
                    or any(item != original[item['number']] for item in value)):
                raise ValueError('Supplied candidate entries differ from their frozen original')
        elif key not in candidate or value != candidate[key]:
            raise ValueError('Supplied global field differs from its frozen original')

    review = context['review']
    originals = scope['original_issue_indices']
    if (review.get('status') != 'blocked' or not isinstance(originals, list)
            or len(originals) != len(review['issues'])
            or any(type(index) is not int or index < 0 for index in originals)
            or len(set(originals)) != len(originals)):
        raise ValueError('Issue mapping is not unique')
    plan_items = context['coordinated_issue_plan']['issues']
    by_index = {}
    for item in plan_items:
        index = item['issue_index']
        if type(index) is not int or index < 0 or index in by_index:
            raise ValueError('Original plan indices are not unique')
        by_index[index] = item
    quotes = {}
    for local, original in enumerate(originals):
        item = by_index[original]
        values = item['source_quotes']
        if (item['classification'] not in {'contradiction', 'continuity_gap', 'interpretation'}
                or not isinstance(values, list) or not values
                or any(not isinstance(q, str) or not q.strip() or q not in source for q in values)):
            raise ValueError('Plan evidence is not exact current source evidence')
        quotes[local] = values
    locations = chapter_locations(source, len(chapters))
    if set(locations) != set(chapters):
        raise ValueError('Original source chapters cannot be uniquely located')
    return quotes, locations


def _without_citations(value: dict) -> dict:
    result = deepcopy(value)
    for issue in result['issues']:
        issue.pop('source_quotes', None)
    for field in ('chapter_replacements', 'global_edits'):
        for edit in result[field]:
            edit.pop('source_quote', None)
    return result


def recover(context: dict, source: str, patch: dict, *, candidate: dict) -> tuple[dict, dict] | None:
    """Recover only recognizable candidate-as-source citations, without I/O.

    No issue-index normalization occurs. Every other field, including full chapter
    replacements, remains exact. Unrecognized/ambiguous evidence, stale bindings,
    structural defects or an already valid patch return None. The unchanged strict
    contract must accept the resulting child before it can be returned.
    """
    try:
        if (not isinstance(context, dict) or not isinstance(candidate, dict)
                or not isinstance(source, str) or not source.strip()
                or not isinstance(patch, dict) or patch.get('status') != 'corrected'):
            return None
        quotes, locations = _bound_inputs(context, source, candidate)
        review, requested = context['review'], context['requested_chapters']
        try:
            contract.apply(candidate, review, source, patch, requested_chapters=requested)
        except (KeyError, TypeError, ValueError):
            pass
        else:
            return None
        canonical = deepcopy(patch)
        strings = tuple(_strings(context['candidate']))
        changes = []
        original_indices = context['repair_scope']['original_issue_indices']

        def invalid_candidate_quote(value):
            if not isinstance(value, str) or not value.strip():
                raise ValueError('Malformed source quotation cannot be recovered')
            if value in source:
                return False
            if not any(value in original for original in strings):
                raise ValueError('An invented quotation cannot inherit source evidence')
            return True

        def record(path, before, after, indices, invalid):
            changes.append({'path': path, 'before': deepcopy(before), 'after': deepcopy(after),
                            'invalid_candidate_quotes': list(invalid),
                            'original_issue_indices': [original_indices[index] for index in indices],
                            'evidence_origin': 'validated_plan'})

        for position, issue in enumerate(canonical['issues']):
            index = issue['issue_index']
            if type(index) is not int or index not in quotes:
                return None
            values = issue['source_quotes']
            if not isinstance(values, list) or not values:
                return None
            invalid = [value for value in values if invalid_candidate_quote(value)]
            if invalid:
                inherited = deepcopy(quotes[index])
                record(f'/issues/{position}/source_quotes', values, inherited, [index], invalid)
                issue['source_quotes'] = inherited

        for field in ('chapter_replacements', 'global_edits'):
            for position, edit in enumerate(canonical[field]):
                before = edit['source_quote']
                if not invalid_candidate_quote(before):
                    continue
                indices = edit['issue_indices']
                if (not isinstance(indices, list) or not indices
                        or any(type(index) is not int or index not in quotes for index in indices)):
                    return None
                if field == 'chapter_replacements':
                    number = edit['number']
                else:
                    path = edit['path']
                    if path not in context['allowed_global_fields']:
                        return None
                    number = candidate['chapter_map'][int(path.split('/')[2])]['number']
                span = source[slice(*locations[number]['source_chars'])]
                relevant = {quote for index in indices for quote in quotes[index] if quote in span}
                if len(relevant) != 1:
                    return None
                inherited = next(iter(relevant))
                record(f'/{field}/{position}/source_quote', before, inherited, indices, [before])
                edit['source_quote'] = inherited

        if not changes or _without_citations(patch) != _without_citations(canonical):
            return None
        # All preconditions, identities, budgets, structures, issue coverage and
        # exact-source checks remain those of the unmodified correction contract.
        contract.apply(candidate, review, source, canonical, requested_chapters=requested)
        audit = {
            'schema': VERSION,
            'evidence_origin': 'validated_plan',
            'input_patch_sha256': contract.chapter_hash(patch),
            'output_patch_sha256': contract.chapter_hash(canonical),
            'context_sha256': contract.chapter_hash(context),
            'candidate_sha256': contract.chapter_hash(candidate),
            'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'plan_sha256': contract.chapter_hash(context['coordinated_issue_plan']),
            'changes': changes,
            'model_selected_source_evidence': False,
            'semantic_review_required': True,
        }
        return canonical, audit
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return None
