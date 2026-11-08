"""Lossless request views of frozen planning inputs; never edit saved sources.

Identical contracts share one location. Identical guidance paragraphs share one
definition with explicit application sites. This is not a summary or a budget
truncator: unknown formats and unequal values remain intact.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json

from core.skeleton_projection import story_parts

VERSION = 'planning-context.v1'
_SHARED = 'shared_guidance'


def planning_context(source: str, brief: dict, locked: dict) -> dict:
    brief = deepcopy(brief)
    inherited = []
    for key, value in locked.items():
        if key in brief and brief[key] == value:
            del brief[key]
            inherited.append(key)
    parts = story_parts(source)
    moved = []
    if (parts and parts['narrative_format'] == locked.get('narrative_format')
            and parts['publication_metadata'] == locked.get('classification')):
        # story_parts verifies both terminal blocks and rejects extra sections,
        # trailing text, duplicate JSON fields, malformed fences and chapter gaps.
        start = source.rfind('\n[NARRATIVE_FORMAT_JSON]')
        if start >= 0:
            source = source[:start + 1]
            moved = ['NARRATIVE_FORMAT_JSON -> immutable_contracts.narrative_format',
                     'NOVEL_CLASSIFICATION_JSON -> immutable_contracts.classification']
    result = {'complete_adopted_source': source, 'structured_brief': brief,
              'immutable_contracts': deepcopy(locked)}
    if inherited or moved:
        result['context_representation'] = {
            'schema': VERSION,
            'brief_fields_in_immutable_contracts': inherited,
            'source_contracts_in_immutable_contracts': moved,
            'rule': 'Read the listed fields from immutable_contracts. Only identical code-owned metadata '
                    'is represented once. Metadata deduplication does not change or summarize story prose.',
        }
    return result


def _reference(identifier: str) -> str:
    return f'[Apply shared_guidance.{identifier} here verbatim.]'


def compact_guidance(value):
    """Factor long, byte-identical paragraphs in structured guidance only.

    Freeform Markdown remains indivisible. No approximate matching, whitespace
    normalization, stage reinterpretation or selection changes are allowed.
    """
    if not isinstance(value, dict) or _SHARED in value:
        return deepcopy(value)
    documents = value.get('documents')
    if not isinstance(documents, list):
        return deepcopy(value)
    counts = Counter()
    for doc in documents:
        sections = doc.get('sections', {}) if isinstance(doc, dict) else {}
        if not isinstance(sections, dict):
            continue
        for text in sections.values():
            if isinstance(text, str):
                counts.update(p for p in text.split('\n\n') if len(p.encode()) >= 256)
    repeated = {p: 'g_' + hashlib.sha256(p.encode()).hexdigest()[:16]
                for p, count in counts.items() if count > 1}
    if not repeated:
        return deepcopy(value)
    # Avoid interpreting an author-written reference as one of our replacements.
    original = json.dumps(value, ensure_ascii=False)
    if any(_reference(identifier) in original for identifier in repeated.values()):
        return deepcopy(value)
    result = deepcopy(value)
    for doc in result['documents']:
        sections = doc.get('sections', {}) if isinstance(doc, dict) else {}
        if not isinstance(sections, dict):
            continue
        for name, text in sections.items():
            if isinstance(text, str):
                sections[name] = '\n\n'.join(_reference(repeated[p]) if p in repeated else p
                                              for p in text.split('\n\n'))
    result[_SHARED] = {identifier: p for p, identifier in repeated.items()}
    # Tiny repeats should not increase the prompt through reference overhead.
    size = lambda obj: len(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode())
    return result if size(result) < size(value) else deepcopy(value)
