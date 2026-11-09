"""Source-backed completions for missing metadata in a global planning candidate."""
from __future__ import annotations

from copy import deepcopy
import re

from core.skeleton_projection import chapter_locations

INSTRUCTION = """STAGE: global_metadata
Complete only the requested missing character names and missing source anchor definitions.
For a name, select the correct source spelling from the supplied source_labels for that
character. Cite the original passage that establishes the identity.
For each undeclared anchor, read its chapter map row and quote the original chapter plan
that supports that specific reference. Select a unique passage and include the qualifiers that establish
the intended fact. If there is no supporting text, do not invent evidence.
Return only {"names":{"requested character ID":{"name":"verbatim label","quote":"exact
source passage"}},"anchors":{"requested anchor ID":"exact source passage"}}. Include
each requested ID exactly once. An empty group is {}. Story choices are frozen."""


def _identity_labels(row: dict) -> list[str]:
    key = row['id']
    if not isinstance(key, str) or not key.strip():
        raise ValueError('A character needs a textual identifier for scoped identity correction')
    label = re.sub(r'^(?:char|character)_', '', key).replace('_', ' ')
    aliases = row.get('aliases', [])
    if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
        raise ValueError('Character aliases cannot be matched unambiguously')
    labels = [label, *aliases]
    if isinstance(row.get('name'), str):
        labels.append(row['name'])
    return [value.strip() for value in labels if value.strip()]


def _name_targets(characters: list, source: str) -> dict:
    missing = [row for row in characters if row.get('name') in (None, '')]
    if not missing:
        return {}
    owners = {}
    for row in characters:
        for label in _identity_labels(row):
            owners.setdefault(label.casefold(), set()).add(row['id'])
    names = {}
    for row in missing:
        spellings = set()
        for label in _identity_labels(row):
            if owners[label.casefold()] != {row['id']}:
                continue
            # ASCII boundaries prevent e.g. "Ann" being sourced from "Anna".
            pattern = r'(?<![a-zA-Z0-9_])' + re.escape(label) + r'(?![a-zA-Z0-9_])'
            spellings.update(re.findall(pattern, source, flags=re.IGNORECASE))
        if not spellings:
            raise ValueError('Missing character name has no unambiguous source-backed ID or alias')
        names[row['id']] = {'character': row, 'source_labels': sorted(spellings)}
    return names


def targets(candidate: dict, source: str) -> dict:
    """The caller runs full validation on a throwaway copy to exclude other defects."""
    for group in ('characters', 'source_anchors', 'chapter_map'):
        if not isinstance(candidate.get(group), list) or any(not isinstance(row, dict) for row in candidate[group]):
            raise ValueError('Global metadata correction requires structured rows')
    names = _name_targets(candidate['characters'], source)
    registered = {anchor['id'] for anchor in candidate['source_anchors']}
    anchors: dict[str, list] = {}
    for row in candidate['chapter_map']:
        for ref in row.get('source_anchor_ids', []):
            if isinstance(ref, str) and ref.strip() and ref not in registered:
                anchors.setdefault(ref, []).append(row)
    return {'names': names, 'anchors': anchors}


def anchor_sources(requested: dict, source: str, count: int) -> dict:
    if not requested['anchors']:
        return {}
    locations = chapter_locations(source, count)
    spans = {}
    for ref, uses in requested['anchors'].items():
        chapters = {row['number'] for row in uses}
        if len(chapters) != 1 or ref.startswith('source_chapter_'):
            raise ValueError('Reference has no unique chapter scope for source binding')
        chapter = chapters.pop()
        if chapter not in locations:
            raise ValueError(f'Cannot locate original text for chapter {chapter}')
        location = locations[chapter]
        start, stop = location['source_chars']
        spans[ref] = (chapter, start, start + len(location['quote']), stop)
    return spans


def apply_values(candidate: dict, names: dict, anchors: list) -> dict:
    child = deepcopy(candidate)
    for row in child['characters']:
        if row['id'] in names:
            row['name'] = names[row['id']]
    child['source_anchors'].extend(anchors)
    return child


def apply_response(candidate: dict, requested: dict, response: dict, source: str, spans: dict) -> dict:
    if set(response) != set(requested) or any(
        not isinstance(response[group], dict) or set(response[group]) != set(fields)
        for group, fields in requested.items()
    ):
        raise ValueError('Metadata correction must address exactly the requested IDs')

    def quoted(value):
        return isinstance(value, str) and value.strip() and value in source

    names = {}
    for key, entry in response['names'].items():
        if not isinstance(entry, dict) or set(entry) != {'name', 'quote'}:
            raise ValueError('Character identity requires a name and its source passage')
        name, quote = entry['name'], entry['quote']
        if (name not in requested['names'][key]['source_labels'] or not quoted(quote)
                or not re.search(r'(?<![a-zA-Z0-9_])' + re.escape(name) + r'(?![a-zA-Z0-9_])', quote)):
            raise ValueError('Character identity must be attested by the adopted source')
        names[key] = name
    anchors = []
    for key, quote in response['anchors'].items():
        chapter, _, start, stop = spans[key]
        if not quoted(quote):
            raise ValueError('Every new source anchor requires an exact adopted-source quote')
        offset = source.find(quote)
        if offset < start or offset + len(quote) > stop or source.find(quote, offset + 1) >= 0:
            raise ValueError(f'Source passage must uniquely identify evidence in chapter {chapter}')
        anchors.append({'id': key, 'chapter': chapter, 'quote': quote, 'source_chars': [offset, offset + len(quote)]})
    return apply_values(candidate, names, anchors)
