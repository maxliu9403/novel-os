"""Recover only unbindable narrative spans from a preserved, otherwise valid reply."""
from __future__ import annotations

from copy import deepcopy
import hashlib

from core.outline_planning import _parse_object, content_hash
from core import planning_residual_edits as narrative
from core import planning_residual_evidence as evidence


INSTRUCTION = """STAGE: coordinated_residual_field_recovery
The preceding edit used a substring that cannot uniquely bind to its actual field.
Repair only each supplied field. current_value is the complete actual saved text.
proposed_edit records the intended correction from the completed response, but its
old_span is unreliable. Read the finding and cited adopted source before deciding
the precise full corrected value. Preserve every unrelated clause, qualifier and
compatible story choice. Keep the field's language and honor prior author choices.
Return only {"fields":[{"index":0,"value":"complete corrected field text"}]}.
Use each supplied index exactly once. Do not return a path or old substring. The
caller preserves all other completed edits verbatim and binds your full value to
the actual saved field. Do not invent author facts or approve the complete book."""


def _targets(raw, context):
    """Validate every permission and citation while isolating only a span mismatch."""
    try:
        proposal = _parse_object(raw)
        probe = deepcopy(proposal)
        visible = {tuple(path): value for path, value in context['fields']}
        targets = []
        for index, edit in enumerate(proposal['edits']):
            path, old, replacement = edit['path'], edit['old_span'], edit['replacement']
            if (not isinstance(path, list) or any(type(part) not in (str, int) for part in path)
                    or not isinstance(old, str) or not old.strip()
                    or not isinstance(replacement, str) or replacement == old):
                return None
            before = visible[tuple(path)]
            if before.count(old) != 1:
                targets.append({'index': index, 'current_value': before, 'proposed_edit': deepcopy(edit)})
                # Only the substring binding is substituted in this validation
                # probe. All paths, permissions, source indices and other edits
                # still go through the existing unchanged validator.
                probe['edits'][index]['old_span'] = before
                probe['edits'][index]['replacement'] = before + '\n'
        narrative._validate(probe, context)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return (proposal, targets) if targets else None


def repair(planner, raw: str, context: dict, limit) -> dict | None:
    found = _targets(raw, context)
    if found is None:
        return None
    proposal, targets = found
    base = {key: deepcopy(context[key]) for key in (
        'finding', 'resolution_basis', 'adopted_passages', 'source_paragraphs',
        'prior_author_choices', 'binding_author_decision', 'shared_edit_direction',
    ) if key in context}
    base['completed_response_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
    base['original_context_sha256'] = content_hash(context)
    lineage = content_hash(base)
    corrected = deepcopy(proposal)
    for page in evidence.pages(planner, 'field-recovery', lineage, INSTRUCTION, base,
                               'fields', targets, limit):
        expected = {row['index']: row for row in page['fields']}

        def validate(reply):
            if not isinstance(reply, dict) or set(reply) != {'fields'} or not isinstance(reply['fields'], list):
                raise ValueError('Field recovery must return the requested indexed field values')
            seen = set()
            for row in reply['fields']:
                if (not isinstance(row, dict) or set(row) != {'index', 'value'}
                        or type(row['index']) is not int or row['index'] not in expected
                        or row['index'] in seen or not isinstance(row['value'], str) or not row['value'].strip()
                        or row['value'] == expected[row['index']]['current_value']):
                    raise ValueError('Field recovery requires a unique authorized index and a complete changed value')
                seen.add(row['index'])
            if seen != set(expected):
                raise ValueError('Field recovery must cover every requested field exactly once')
            return reply

        result, _ = evidence.checkpoint(planner, 'field-recovery', lineage, INSTRUCTION, page, validate, limit)
        for row in result['fields']:
            edit = corrected['edits'][row['index']]
            edit['old_span'] = expected[row['index']]['current_value']
            edit['replacement'] = row['value']
    return narrative._validate(corrected, context)
