"""Keep frozen generated design obligations distinct from adopted-source evidence.

Catalog entries retain complete original strings and typed paths. They attest only
to what the saved model design said, never to an author requirement or story fact.
No function performs I/O, model calls, textual normalization or semantic approval.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

SCHEMA = 'planning-design-references.v1'
VERSION = SCHEMA
PREFIX = 'design_'
AUTHORITY = (
    'Provenance of model-generated plans in the frozen seed, not author testimony, '
    'occurred events, POV knowledge or an immutable story obligation. Preserve the '
    'historical text as evidence of its origin. The adopted source and current '
    'reviewed design govern execution; do not reinstate a superseded interpretation '
    'from this historical reference. References alone do not certify story quality.'
)

# Explicit narrative roots. Unknown extensions and transport/contract metadata
# cannot acquire design authority merely by appearing in a saved JSON object.
NARRATIVE_FIELDS = frozenset({
    'title', 'premise', 'logline', 'thematic_argument', 'themes', 'setting',
    'characters', 'institutions', 'relationships', 'plot_threads', 'causal_chain',
    'act_beat_map', 'opening_investment', 'promise_ledger', 'chronology',
    'knowledge_boundaries', 'volume_contracts', 'style', 'assumptions',
    'structural_risks', 'deliberate_open_threads', 'ending_contract',
})
_METADATA = frozenset({
    'id', 'ids', 'schema', 'schema_version', 'version', 'sha256', 'source_sha256',
    'source_anchors', 'source_anchor_ids', 'source_quotes', 'source_quote', 'quote',
    'source_evidence', 'source_evidence_ids', 'source_evidence_id', 'source_chars',
    'design_references', 'design_reference_ids', 'chapter_map', 'chapters',
    'narrative_format', 'commercial_story_contract', 'commercial_story_contract_id',
    'classification', 'classification_design_requirements', 'immutable_contracts',
})
_CATALOG_FIELDS = {'schema', 'authority', 'seed_sha256', 'source_sha256', 'entries', 'sha256'}
_ENTRY_FIELDS = {'id', 'origin', 'path', 'text', 'text_sha256'}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _text_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _design_id(value):
    return isinstance(value, str) and re.fullmatch(PREFIX + '[0-9a-f]{24}', value) is not None


def _path(value):
    return (isinstance(value, list) and bool(value)
            and isinstance(value[0], str) and value[0] in NARRATIVE_FIELDS
            and all((isinstance(part, str) and bool(part) and part not in _METADATA)
                    or (type(part) is int and part >= 0) for part in value))


def _leaves(value, path):
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('Design seed objects require textual field names')
        for key in sorted(value):
            if key not in _METADATA:
                yield from _leaves(value[key], [*path, key])
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _leaves(child, [*path, index])
    elif isinstance(value, str) and value.strip():
        yield path, value


def _identifier(seed_sha256, source_sha256, path, text):
    return PREFIX + _hash({'schema': SCHEMA, 'seed_sha256': seed_sha256,
        'source_sha256': source_sha256, 'path': path, 'text': text})[:24]


def _sealed(seed_sha256, source_sha256, entries):
    value = {'schema': SCHEMA, 'authority': AUTHORITY, 'seed_sha256': seed_sha256,
             'source_sha256': source_sha256, 'entries': entries}
    return {**value, 'sha256': _hash(value)}


def build_catalog(seed, source_sha256):
    """Catalog every eligible complete narrative leaf of the exact frozen seed."""
    if not isinstance(seed, dict) or not _digest(source_sha256):
        raise ValueError('Design catalog requires a frozen seed and source digest')
    seed_sha256 = _hash(seed)
    entries, seen = [], set()
    for root in sorted(NARRATIVE_FIELDS & seed.keys()):
        for path, text in _leaves(seed[root], [root]):
            identifier = _identifier(seed_sha256, source_sha256, path, text)
            if identifier in seen:
                raise ValueError('Design reference identifier collision')
            seen.add(identifier)
            entries.append({'id': identifier, 'origin': 'model_design', 'path': path,
                            'text': text, 'text_sha256': _text_hash(text)})
    return _sealed(seed_sha256, source_sha256, entries)


def validate_catalog(value, source_sha256=None, seed=None):
    """Validate a whole catalog or a subset, optionally against its original seed.

    A self-consistent digest is integrity evidence, not independent provenance;
    callers receiving untrusted catalog objects must supply their frozen seed.
    """
    if (not isinstance(value, dict) or set(value) != _CATALOG_FIELDS
            or value['schema'] != SCHEMA or value['authority'] != AUTHORITY or not _digest(value['seed_sha256'])
            or not _digest(value['source_sha256']) or not _digest(value['sha256'])
            or not isinstance(value['entries'], list)):
        raise ValueError('Invalid design reference catalog')
    if source_sha256 is not None and value['source_sha256'] != source_sha256:
        raise ValueError('Design reference catalog belongs to a different source')
    if seed is not None and (not isinstance(seed, dict) or _hash(seed) != value['seed_sha256']):
        raise ValueError('Design reference catalog belongs to a different seed')
    identifiers, paths = set(), set()
    for entry in value['entries']:
        if (not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS
                or entry['origin'] != 'model_design' or not _path(entry['path'])
                or not isinstance(entry['text'], str) or not entry['text'].strip()
                or entry['text_sha256'] != _text_hash(entry['text'])
                or entry['id'] != _identifier(value['seed_sha256'], value['source_sha256'],
                                               entry['path'], entry['text'])):
            raise ValueError('Invalid design reference entry')
        if entry['id'] in identifiers or tuple(entry['path']) in paths:
            raise ValueError('Duplicate design reference identifier or path')
        identifiers.add(entry['id'])
        paths.add(tuple(entry['path']))
        if seed is not None:
            node = seed
            for part in entry['path']:
                if ((isinstance(node, list) and type(part) is int and part < len(node))
                        or (isinstance(node, dict) and isinstance(part, str) and part in node)):
                    node = node[part]
                else:
                    raise ValueError('Design reference path is absent from the frozen seed')
            if not isinstance(node, str) or node != entry['text']:
                raise ValueError('Design reference text differs from the frozen seed')
    unsigned = {key: child for key, child in value.items() if key != 'sha256'}
    if _hash(unsigned) != value['sha256']:
        raise ValueError('Design reference catalog digest mismatch')
    return deepcopy(value)


def select_catalog(catalog, ids):
    """Retain exact entries in catalog order; selecting unknown IDs is an error."""
    value = validate_catalog(catalog)
    if (not isinstance(ids, (list, tuple, set, frozenset))
            or any(not isinstance(identifier, str) for identifier in ids)):
        raise ValueError('Design reference selection requires known identifiers')
    wanted = set(ids)
    known = {entry['id'] for entry in value['entries']}
    if not wanted <= known:
        raise ValueError('Unknown design reference selection')
    return _sealed(value['seed_sha256'], value['source_sha256'],
                   [entry for entry in value['entries'] if entry['id'] in wanted])


def _rows(rows, known):
    if not isinstance(rows, list):
        raise ValueError('Design references require chapter rows')
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or type(row.get('number')) is not int
                or row['number'] < 1 or row['number'] in result):
            raise ValueError('Design references require unique numbered chapter rows')
        ids = row.get('design_reference_ids', [])
        if (not isinstance(ids, list) or any(not isinstance(i, str) or i not in known for i in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('Chapter design references must be unique known design IDs')
        source_ids = row.get('source_anchor_ids', [])
        if isinstance(source_ids, list) and any(_design_id(i) for i in source_ids):
            raise ValueError('A design reference cannot be presented as a source anchor')
        result[row['number']] = ids
    return result


def references(rows, catalog, inherited_rows=None):
    """Validate rows and inherit omitted detailed-chapter references in place.

    Explicit detailed-chapter references must preserve that chapter's exact mapped
    set. No absent legacy field is introduced unless nonempty references exist.
    Validation finishes before any inheritance is applied.
    """
    known = {entry['id'] for entry in validate_catalog(catalog)['entries']} if catalog is not None else set()
    actual = _rows(rows, known)
    if inherited_rows is None:
        return
    inherited = _rows(inherited_rows, known)
    pending = []
    for row in rows:
        number = row['number']
        if number not in inherited:
            raise ValueError('Chapter has no matching mapped design references')
        expected = inherited[number]
        if 'design_reference_ids' in row:
            if set(actual[number]) != set(expected):
                raise ValueError('Chapter cannot drop or replace its mapped design references')
        elif expected:
            pending.append((row, list(expected)))
    for row, ids in pending:
        row['design_reference_ids'] = ids


def _occurrences(value, targets, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in targets:
                yield (*path, ('key', key))
            yield from _occurrences(child, targets, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _occurrences(child, targets, (*path, index))
    elif isinstance(value, str) and value in targets:
        yield path


def reclassify(candidate, catalog, source, known_source_anchors):
    """Return a lossless provenance child only for exact unique seed-leaf quotes.

    The caller supplies the FULL build_catalog result, not a selected subset, so
    duplicate eligible leaf text remains ambiguous. Original strict roadmap and
    semantic validators still govern the returned child. Unsupported defects
    return None; corrupt catalog authority raises ValueError.
    """
    if not isinstance(source, str):
        raise ValueError('Design reclassification requires the frozen source')
    catalog = validate_catalog(catalog, source_sha256=_text_hash(source))
    if (not isinstance(candidate, dict) or not {'source_anchors', 'chapter_map'} <= candidate.keys()
            or set(candidate) - {'source_anchors', 'chapter_map', 'seed_conflicts'}
            or candidate.get('seed_conflicts', []) != []
            or not isinstance(candidate['source_anchors'], list)
            or not isinstance(known_source_anchors, list)):
        return None
    by_text = {}
    for entry in catalog['entries']:
        by_text.setdefault(entry['text'], []).append(entry)
    known, supplied, changes = {}, {}, {}
    for anchor in known_source_anchors:
        if (not isinstance(anchor, dict) or not isinstance(anchor.get('id'), str)
                or not anchor['id'].strip() or _design_id(anchor['id'])
                or anchor['id'] in known or not isinstance(anchor.get('quote'), str)
                or not anchor['quote'].strip() or anchor['quote'] not in source):
            return None
        known[anchor['id']] = anchor
    for index, anchor in enumerate(candidate['source_anchors']):
        if (not isinstance(anchor, dict) or not isinstance(anchor.get('id'), str)
                or not anchor['id'].strip() or _design_id(anchor['id'])
                or anchor['id'] in supplied or not isinstance(anchor.get('quote'), str)
                or not anchor['quote'].strip()):
            return None
        identifier, quote = anchor['id'], anchor['quote']
        supplied[identifier] = anchor
        if identifier in known:
            if anchor != known[identifier]:
                return None
        elif quote not in source:
            matches = by_text.get(quote, [])
            if set(anchor) != {'id', 'quote'} or len(matches) != 1:
                return None
            changes[identifier] = (index, matches[0])
    if not changes:
        return None
    try:
        references(candidate['chapter_map'], catalog)
    except ValueError:
        return None
    valid_source = (known.keys() | supplied.keys()) - changes.keys()
    accounted = {('source_anchors', index, 'id') for index, _ in changes.values()}
    uses = {identifier: [] for identifier in changes}
    for index, row in enumerate(candidate['chapter_map']):
        ids = row.get('source_anchor_ids')
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(i, str) or i not in valid_source | changes.keys() for i in ids)
                or len(set(ids)) != len(ids)):
            return None
        affected = changes.keys() & set(ids)
        if affected and not (set(ids) & valid_source):
            return None
        for position, identifier in enumerate(ids):
            if identifier in changes:
                accounted.add(('chapter_map', index, 'source_anchor_ids', position))
                uses[identifier].append({'row_index': index, 'chapter': row['number'],
                                         'source_reference_index': position})
    if any(not rows for rows in uses.values()) or set(_occurrences(candidate, changes)) != accounted:
        return None
    child, mappings = deepcopy(candidate), []
    child['source_anchors'] = [anchor for anchor in child['source_anchors'] if anchor['id'] not in changes]
    for identifier, (index, entry) in changes.items():
        for use in uses[identifier]:
            row = child['chapter_map'][use['row_index']]
            row['source_anchor_ids'].remove(identifier)
            refs = row.setdefault('design_reference_ids', [])
            if entry['id'] not in refs:
                refs.append(entry['id'])
        mappings.append({'source_anchor_id': identifier, 'source_anchor_index': index,
                         'design_reference_id': entry['id'], 'path': deepcopy(entry['path']),
                         'text': entry['text'], 'text_sha256': entry['text_sha256'],
                         'rows': uses[identifier]})
    return child, mappings
