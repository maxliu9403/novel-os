"""Lossless candidate references and narrowly bounded legacy quote recovery.

This codec locates evidence. It never changes an editorial decision, rationale,
repair instruction, continuity report, state update, or manuscript.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Callable

VERSION = 'joint-editorial.v2'
CATALOG_VERSION = 'joint-review-paragraphs.v1'
BINDING_VERSION = 'joint-review-evidence-binding.v1'
_BLOCK = re.compile(r'\[JOINT_REVIEW_JSON\](.*?)\[/JOINT_REVIEW_JSON\]', re.S)
_OPEN_QUOTES = frozenset('"\'“‘«‹「『')
_CLOSE_QUOTES = frozenset('"\'”’»›」』')


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def build_catalog(manuscript: str) -> dict[str, Any]:
    """Partition every original character into stable, candidate-scoped paragraphs."""
    if not isinstance(manuscript, str) or not manuscript.strip():
        raise ValueError('Review evidence requires a nonempty complete candidate')
    separators = re.finditer(r'\r?\n[ \t]*\r?\n(?:[ \t]*\r?\n)*', manuscript)
    ends = [match.end() for match in separators]
    if not ends or ends[-1] != len(manuscript):
        ends.append(len(manuscript))
    entries, start = [], 0
    for end in ends:
        text = manuscript[start:end]
        if text.strip():
            entries.append({'id': f'p{len(entries) + 1:04d}', 'start': start, 'end': end, 'text': text})
            start = end
    if start != len(manuscript):
        # Whitespace is still part of the reviewed candidate, never omitted.
        entries[-1]['end'] = len(manuscript)
        entries[-1]['text'] = manuscript[entries[-1]['start']:]
    catalog = {'schema_version': CATALOG_VERSION, 'candidate_sha256': text_sha(manuscript),
               'offset_unit': 'unicode_codepoints', 'entries': entries}
    encoded = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {**catalog, 'catalog_sha256': text_sha(encoded)}


def _unique_object(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError('Review JSON cannot contain duplicate properties')
        value[key] = child
    return value


def _reject_constant(value):
    raise ValueError('Review JSON cannot contain nonfinite constants')


def _parse(response):
    matches = list(_BLOCK.finditer(response))
    if (len(matches) != 1 or response.count('[JOINT_REVIEW_JSON]') != 1
            or response.count('[/JOINT_REVIEW_JSON]') != 1):
        raise ValueError('联合审稿未返回唯一完整结果。')
    match = matches[0]
    block = match[1]
    value = json.loads(block, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if not isinstance(value, dict):
        raise ValueError('Review JSON must be an object')
    start = match.start(1) + len(block) - len(block.lstrip())
    end = match.end(1) - (len(block) - len(block.rstrip()))
    return value, start, end


def _occurrences(text: str, quote: str) -> list[dict[str, Any]]:
    result, offset = [], 0
    while True:
        start = text.find(quote, offset)
        if start < 0:
            return result
        result.append({'start': start, 'end': start + len(quote), 'text_sha256': text_sha(quote)})
        offset = start + 1


def _legacy_quote(quote: str, manuscript: str):
    if not quote.strip():
        raise ValueError('联合审稿的正文引用不匹配。')
    exact = _occurrences(manuscript, quote)
    if exact:
        return quote, exact
    candidates = []
    if quote[0] in _OPEN_QUOTES:
        candidates.append((1, quote[1:]))
    if quote[-1] in _CLOSE_QUOTES:
        candidates.append((1, quote[:-1]))
    if quote[0] in _OPEN_QUOTES and quote[-1] in _CLOSE_QUOTES:
        candidates.append((2, quote[1:-1]))
    for removed in (1, 2):
        matching = {}
        for rank, candidate in candidates:
            if rank == removed and candidate.strip():
                spans = _occurrences(manuscript, candidate)
                if spans:
                    matching[candidate] = spans
        if matching:
            if len(matching) == 1:
                candidate, spans = next(iter(matching.items()))
                if len(spans) == 1:
                    return candidate, spans
            break
    raise ValueError('联合审稿的正文引用不匹配。')


def decode_review(response: str, manuscript: str, *,
                  validate_metadata: Callable[[dict[str, Any]], Any]):
    """Validate judgments first, then resolve references without a semantic reroll.

    Returns the compatible in-memory review payload, adopted wire text, and an
    audit receipt. V2 wire text is retained byte-for-byte; its joined display
    quotes are never passed through the legacy contiguous-substring validator.
    """
    original, start, end = _parse(response)
    is_v2 = 'schema_version' in original
    if is_v2:
        if (set(original) != {'schema_version', 'candidate_sha256', 'catalog_sha256', 'checks'}
                or original['schema_version'] != VERSION or not isinstance(original['checks'], list)):
            raise ValueError('Unsupported or mixed joint review evidence protocol')
        checks = []
        for check in original['checks']:
            required = {'dimension', 'status', 'evidence_ids', 'reason'}
            if (not isinstance(check, dict) or not required <= set(check)
                    or set(check) - required - {'repair'}):
                raise ValueError('V2 review checks must select evidence IDs, never copied quotes')
            checks.append({**{k: v for k, v in check.items() if k != 'evidence_ids'}, 'quote': 'pending evidence'})
        compatible = {'candidate_sha256': original['candidate_sha256'], 'checks': checks}
    else:
        compatible = deepcopy(original)
    validate_metadata(compatible)
    catalog = build_catalog(manuscript)
    if is_v2 and original['catalog_sha256'] != catalog['catalog_sha256']:
        raise ValueError('Review evidence catalog differs from the complete candidate')
    entries = {entry['id']: entry for entry in catalog['entries']}
    bindings, changes = [], []
    for index, check in enumerate(original['checks']):
        binding = {'check_index': index, 'dimension': check['dimension']}
        if is_v2:
            identifiers = check['evidence_ids']
            if (not isinstance(identifiers, list) or not identifiers
                    or any(not isinstance(key, str) or key not in entries for key in identifiers)
                    or len(identifiers) != len(set(identifiers))):
                raise ValueError('Review evidence IDs must uniquely reference the bound candidate catalog')
            selected = [entries[key] for key in identifiers]
            compatible['checks'][index]['quote'] = '\n\n'.join(entry['text'] for entry in selected)
            binding.update(evidence_ids=list(identifiers), spans=[
                {'start': entry['start'], 'end': entry['end'], 'text_sha256': text_sha(entry['text'])}
                for entry in selected])
        else:
            quote, spans = _legacy_quote(check['quote'], manuscript)
            compatible['checks'][index]['quote'] = quote
            binding['spans'] = spans
            if quote != check['quote']:
                changes.append({'check_index': index, 'dimension': check['dimension'],
                                'operation': 'remove_extra_outer_display_quote',
                                'before': check['quote'], 'after': quote, 'spans': deepcopy(spans)})
        bindings.append(binding)
    derived = response
    if changes:
        derived = response[:start] + json.dumps(compatible, ensure_ascii=False, separators=(',', ':')) + response[end:]
    receipt = {'schema_version': BINDING_VERSION, 'protocol': VERSION if is_v2 else 'joint-editorial.v1',
               'candidate_sha256': catalog['candidate_sha256'], 'catalog_sha256': catalog['catalog_sha256'],
               'offset_unit': catalog['offset_unit'], 'raw_response_sha256': text_sha(response),
               'derived_response_sha256': text_sha(derived), 'bindings': bindings, 'changes': changes}
    return compatible, derived, receipt
