"""Source-grounded uncertainty between role records, independent of character state."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Iterable

RULES = (
    'An unknown identity relation means the adopted source has not established whether the two '
    'role records describe the same person or different people. Their stable IDs address the '
    'recorded roles and do not prove distinct people. Preserve this uncertainty in prose and '
    'review. Do not merge or rename the records, assert either identity conclusion, or transfer '
    'knowledge, memories, pronouns, actions, injuries or fate from one record to the other. '
    'Use only the source-supported attributes of the role in the current scene. A source '
    'pronoun establishes its referent and pronoun usage, not an otherwise unstated gender fact.'
)


def validate_identity_constraints(value: Any, known_ids: Iterable[str], *, source: str | None = None) -> list[dict]:
    """Validate the complete contract; absence leaves older canon byte-for-byte compatible."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError('identity_constraints must be a list')
    known = set(known_ids)
    result, pairs = [], set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {'character_ids', 'relation', 'reason', 'source_evidence'}:
            raise ValueError('Identity constraint requires character_ids, relation, reason and source_evidence')
        ids = entry['character_ids']
        if (not isinstance(ids, list) or len(ids) != 2 or any(not isinstance(i, str) or i not in known for i in ids)
                or ids[0] == ids[1]):
            raise ValueError('Identity constraint must address two different existing role IDs')
        pair = tuple(sorted(ids))
        if pair in pairs:
            raise ValueError('An identity pair may have only one constraint')
        pairs.add(pair)
        if entry['relation'] != 'unknown':
            raise ValueError('An uncertainty constraint must preserve an unknown identity relation')
        if not isinstance(entry['reason'], str) or not entry['reason'].strip():
            raise ValueError('Identity constraint requires the source-grounded reason for uncertainty')
        evidence = entry['source_evidence']
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('Identity constraint requires source evidence for both role occurrences')
        cited = set()
        for reference in evidence:
            if not isinstance(reference, dict) or set(reference) != {'character_id', 'quote'}:
                raise ValueError('Identity evidence must bind a role ID to its source quotation')
            cid, quote = reference['character_id'], reference['quote']
            if (not isinstance(cid, str) or cid not in pair or not isinstance(quote, str)
                    or not quote.strip() or (source is not None and quote not in source)):
                raise ValueError('Identity evidence must quote the supplied source for a referenced role')
            cited.add(cid)
        if cited != set(pair):
            raise ValueError('Identity constraint requires source evidence for both role occurrences')
        result.append({**deepcopy(entry), 'character_ids': list(pair)})
    return sorted(result, key=lambda row: row['character_ids'])


def state_identity_constraints(state: Any) -> list[dict]:
    return validate_identity_constraints(getattr(state, 'story_bible', {}).get('identity_constraints'),
                                         getattr(state, 'characters', {}))


def render_identity_constraints(constraints: list[dict]) -> str:
    if not constraints:
        return ''
    # This is mandatory evidence, never subject to the optional context pack's soft budget.
    return ('## Identity constraints\n' + RULES + '\n\n' +
            json.dumps(constraints, ensure_ascii=False, sort_keys=True, indent=2) + '\n\n')
