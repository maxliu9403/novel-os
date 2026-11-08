"""Bind missing planning citations to exact, code-owned source locations.

An anchor identifies where to read. It never certifies that a model's scene is
faithful to that text. Full-source/semantic review remains the authority for that.
Existing citations are validated, not overwritten or guessed from similarity.
"""
from __future__ import annotations

import hashlib

from core.skeleton_projection import chapter_locations


def _references(chapter: dict, known: set[str], fallback: list[str] | None) -> None:
    key = 'source_anchor_ids'
    refs = chapter.get(key, [])
    if refs == [] and fallback:
        refs = list(fallback)
    if (not isinstance(refs, list) or not refs
            or any(not isinstance(ref, str) or not ref.strip() for ref in refs)
            or len(refs) != len(set(refs)) or not set(refs) <= known):
        raise ValueError(f"Chapter {chapter['number']} source_anchor_ids must contain unique known IDs; "
                         "missing references need an unambiguous source chapter or validated chapter map")
    chapter[key] = refs


def bind_global(chapters: list[dict], anchors: list[dict], source: str, count: int) -> None:
    """Called only after complete map coverage and exact model quotes validate."""
    locations = chapter_locations(source, count)
    digest = hashlib.sha256(source.encode()).hexdigest()[:12]
    locators = {n: {'id': f'source_chapter_{n}_{digest}', 'kind': 'chapter_locator',
                    'chapter': n, **location} for n, location in locations.items()}
    known = {a['id']: a for a in anchors}
    # A cached/model-supplied locator may not impersonate a different source span.
    for anchor in anchors:
        if anchor.get('kind') == 'chapter_locator':
            number = anchor.get('chapter')
            if type(number) is not int or anchor != locators.get(number):
                raise ValueError('Source chapter locator does not match the frozen source')
    for chapter in chapters:
        fallback = None
        if chapter.get('source_anchor_ids', []) == [] and chapter['number'] in locators:
            locator = locators[chapter['number']]
            identifier = locator['id']
            if identifier in known and known[identifier] != locator:
                raise ValueError('Source chapter locator ID collides with an existing anchor')
            if identifier not in known:
                anchors.append(locator)
                known[identifier] = locator
            fallback = [identifier]
        _references(chapter, set(known), fallback)


def bind_batch(chapters: list[dict], global_design: dict) -> None:
    """A chapter inherits only its own already validated map's references."""
    known = {a['id'] for a in global_design['source_anchors']}
    mapped = {c['number']: c['source_anchor_ids'] for c in global_design['chapter_map']}
    for chapter in chapters:
        _references(chapter, known, mapped.get(chapter['number']))
