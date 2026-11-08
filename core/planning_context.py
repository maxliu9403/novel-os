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
REVIEW_CONTEXT_VERSION = 'planning-review-context.v1'
_SHARED = 'shared_guidance'
_REVIEW_CONTRACT_FIELDS = (
    'narrative_format', 'classification', 'classification_design_requirements',
    'commercial_story_contract', 'commercial_story_contract_id',
)
_SOURCE_CONTRACT_REFERENCES = [
    'NARRATIVE_FORMAT_JSON -> immutable_contracts.narrative_format',
    'NOVEL_CLASSIFICATION_JSON -> immutable_contracts.classification',
]


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _value_hash(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def review_context(context: dict, candidate: dict, *, complete_source: str) -> dict:
    """Represent equal code-owned contracts once, only for semantic review.

    Story prose, chapter objects, global design and creative guidance are never
    rewritten. The input source and candidate stay immutable; the receipt is
    reversible against the exact frozen source, including its JSON whitespace.
    Unknown source layouts and unequal contracts retain their original values.
    """
    result = deepcopy(context)
    previous = result.get('review_representation')
    if previous is not None:
        if isinstance(previous, dict) and previous.get('schema') == REVIEW_CONTEXT_VERSION:
            original = expand_review_context(result, complete_source=complete_source)
            if candidate not in (result['candidate'], original['candidate']):
                raise ValueError('Review representation belongs to another candidate')
            return result
        # A caller-owned field must not be mistaken for our representation.
        result['candidate'] = deepcopy(candidate)
        return result
    result['candidate'] = deepcopy(candidate)
    original_hash = _value_hash(result)
    original_bytes = len(_canonical(result).encode())
    locked = result.get('immutable_contracts')
    if not isinstance(locked, dict):
        return result
    moved = []
    for key in _REVIEW_CONTRACT_FIELDS:
        if (key in candidate and key in locked
                and _canonical(candidate[key]) == _canonical(locked[key])):
            del result['candidate'][key]
            moved.append(key)

    source_record = None
    parts = story_parts(complete_source)
    projection = result.get('adopted_source_projection')
    representation = result.get('context_representation')
    if (parts and isinstance(projection, dict) and isinstance(representation, dict)
            and projection.get('source_sha256') == parts['source_sha256']
            and _canonical(parts['narrative_format']) == _canonical(locked.get('narrative_format'))
            and _canonical(parts['publication_metadata']) == _canonical(locked.get('classification'))):
        start = complete_source.rfind('\n[NARRATIVE_FORMAT_JSON]')
        tail = complete_source[start + 1:] if start >= 0 else ''
        text = projection.get('text')
        if (tail and isinstance(text, str) and text.endswith(tail)
                and projection.get('projected_bytes') == len(text.encode())):
            source_record = {'prior_context_representation': deepcopy(representation)}
            projection['text'] = text[:-len(tail)]
            projection['projected_bytes'] = len(projection['text'].encode())
            representation['source_contracts_in_immutable_contracts'] = list(_SOURCE_CONTRACT_REFERENCES)
    if moved or source_record:
        result['review_representation'] = {
            'schema': REVIEW_CONTEXT_VERSION,
            'source_sha256': hashlib.sha256(complete_source.encode()).hexdigest(),
            'original_context_sha256': original_hash,
            'candidate_contract_fields': moved,
            'source_terminal_contracts': source_record,
            'rule': 'Read every listed candidate contract field from immutable_contracts, '
                    'with exactly the same meaning and value. Source terminal contracts, '
                    'when indicated, are also represented there. Only equal code-owned '
                    'metadata is shared; all supplied story text and global/chapter design '
                    'remain verbatim. This representation does not narrow review coverage.',
        }
    if len(_canonical(result).encode()) >= original_bytes:
        return {**deepcopy(context), 'candidate': deepcopy(candidate)}
    return result


def expand_review_context(context: dict, *, complete_source: str) -> dict:
    """Reconstruct and verify the exact original review payload, without I/O."""
    result = deepcopy(context)
    receipt = result.get('review_representation')
    if not isinstance(receipt, dict) or receipt.get('schema') != REVIEW_CONTEXT_VERSION:
        return result
    del result['review_representation']
    if receipt.get('source_sha256') != hashlib.sha256(complete_source.encode()).hexdigest():
        raise ValueError('Review representation source changed')
    fields = receipt.get('candidate_contract_fields')
    if not isinstance(fields, list) or any(key not in _REVIEW_CONTRACT_FIELDS for key in fields):
        raise ValueError('Unknown review contract reference')
    for key in fields:
        if key in result['candidate'] or key not in result['immutable_contracts']:
            raise ValueError('Review contract reference changed')
        result['candidate'][key] = deepcopy(result['immutable_contracts'][key])
    source_record = receipt.get('source_terminal_contracts')
    if source_record:
        start = complete_source.rfind('\n[NARRATIVE_FORMAT_JSON]')
        if start < 0:
            raise ValueError('Review source terminal contracts missing')
        projection = result['adopted_source_projection']
        projection['text'] += complete_source[start + 1:]
        projection['projected_bytes'] = len(projection['text'].encode())
        result['context_representation'] = deepcopy(source_record['prior_context_representation'])
    if _value_hash(result) != receipt.get('original_context_sha256'):
        raise ValueError('Review representation does not reconstruct its original payload')
    return result


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
