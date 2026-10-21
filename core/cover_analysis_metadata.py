"""Source-bound publication fields, kept separate from fictional story facts."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from typing import Any, Mapping

import yaml

from .cover_sources import CoverSkeletonSnapshot

PUBLICATION_METADATA_VERSION = 'cover-publication-metadata.v1'
FIELDS = ('title', 'author', 'language', 'genre', 'target_audience', 'market_scope')
NESTED_FIELDS = ('genre_emotion_profile.primary_genre', 'commercial_visual_goal.market',
                 'commercial_visual_goal.audience_segment')
_LABELS = {
    'title': 'title', '书名': 'title', 'author': 'author', '作者': 'author',
    'language': 'language', '语言': 'language', '正文语言': 'language',
    'genre': 'genre', '题材': 'genre', '类型': 'genre',
    'audience': 'target_audience', 'target_audience': 'target_audience', '目标读者': 'target_audience',
    'market': 'market_scope', 'market_scope': 'market_scope', '发行地域': 'market_scope',
}


def _comparable(field: str, value: str) -> str:
    normalized = unicodedata.normalize('NFKC', value).casefold().strip()
    normalized = re.sub(r'\s+', ' ', normalized).replace('–', '-').replace('—', '-')
    if field == 'language':
        return {'en': 'english', '英文': 'english', '英语': 'english',
                'zh': 'chinese', '中文': 'chinese', '汉语': 'chinese'}.get(normalized, normalized)
    return normalized


def publication_context(snapshot: CoverSkeletonSnapshot) -> dict[str, Any]:
    """Read explicit primary headers, then compressed handoff as a fallback.

    Never infer language from the skeleton's prose, reader country, or title.
    Primary headers have authority over the handoff, matching source policy.
    Retain lower-priority alternatives as source context for independent review.
    """
    text = snapshot.primary_text
    document = next(item for item in snapshot.documents if item['document_id'] == 'primary')
    sources: list[dict[str, Any]] = []

    def add(field, value, start, end, kind, priority):
        if not isinstance(value, str) or (not value.strip() and field != 'author'):
            return
        quote = text[start:end]
        sources.append({'field': field, 'value': value.strip(), 'source_kind': kind, 'priority': priority,
                        'document_id': 'primary', 'document_sha256': document['sha256'],
                        'byte_start': len(text[:start].encode('utf-8')),
                        'byte_end': len(text[:end].encode('utf-8')), 'exact_quote': quote,
                        'quote_sha256': hashlib.sha256(quote.encode('utf-8')).hexdigest()})

    offset = 0
    fence = None
    for line in text.splitlines(keepends=True):
        if re.match(r'^#{2,6}\s', line):
            break
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
        if fence is None and not marker:
            match = re.match(r'^\s*(?:[-*]\s+)?([^:：\n]+)[:：]\s*(.*?)\s*$', line)
            if match:
                label = match[1].strip().strip('*').strip().casefold()
                if label in _LABELS:
                    value = match[2].strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('`', '"', "'"):
                        value = value[1:-1]
                    add(_LABELS[label], value, offset, offset + len(line.rstrip('\r\n')), 'primary_header', 0)
        offset += len(line)

    decoder = json.JSONDecoder()
    blocks = [(marker.end(), 'cover_handoff', 1) for marker in
              re.finditer(r'COVER_HANDOFF_BEGIN\s*(?:```(?:json)?\s*)?(?=\{)', text)]
    # Also support an explicitly embedded schema-v2 approved brief, without
    # treating arbitrary nested JSON titles or languages as book metadata.
    for marker in re.finditer(r'```json\s*\n\s*(?=\{)', text, re.IGNORECASE):
        start = marker.end()
        try:
            candidate, length = decoder.raw_decode(text[start:])
        except ValueError:
            continue
        if (isinstance(candidate, dict) and candidate.get('schema_version') == 2
                and isinstance(candidate.get('principal_characters'), list)
                and isinstance(candidate.get('core_conflict'), str)
                and re.match(r'\s*```', text[start + length:])
                and not any(start == item[0] for item in blocks)):
            blocks.append((start, 'embedded_cover_brief', 2))
    for start, kind, priority in blocks:
        try:
            body, length = decoder.raw_decode(text[start:])
        except ValueError:
            continue  # Malformed handoff remains part of ordinary raw-source analysis.
        tail = text[start + length:]
        if not isinstance(body, dict) or (kind == 'cover_handoff' and not re.match(r'\s*(?:```\s*)?COVER_HANDOFF_END', tail)):
            continue
        # Walk only the top-level JSON members to retain exact field spans;
        # nested series.title and character language/age are not publication fields.
        cursor = start + 1
        seen = set()
        while cursor < start + length:
            while text[cursor].isspace() or text[cursor] == ',':
                cursor += 1
            if text[cursor] == '}':
                break
            key_start = cursor
            key, consumed = decoder.raw_decode(text[cursor:])
            cursor += consumed
            while text[cursor].isspace() or text[cursor] == ':':
                cursor += 1
            value, consumed = decoder.raw_decode(text[cursor:])
            cursor += consumed
            if key in FIELDS:
                if key in seen:
                    raise ValueError(f'Conflicting publication metadata: duplicate handoff {key}')
                seen.add(key)
                add(key, value, key_start, cursor, kind, priority)
            if key in ('genre_emotion_profile', 'commercial_visual_goal') and isinstance(value, dict):
                for name in NESTED_FIELDS:
                    parent, child = name.split('.')
                    if key == parent:
                        add(name, value.get(child), key_start, cursor, kind, priority)

    def member(node, key):
        if not isinstance(node, yaml.MappingNode):
            return None
        matches = [value for label, value in node.value if label.value == key]
        if len(matches) > 1:
            raise ValueError(f'Conflicting publication metadata: duplicate {key}')
        return matches[0] if matches else None

    # Older skeletons put explicit release/audience settings in a bounded
    # audience-research block. Follow only these named paths, never generic
    # nested "market" entries in external research citations or character data.
    for fence in re.finditer(r'```(?:json|ya?ml)\s*\n(?P<body>.*?)\n```', text, re.IGNORECASE | re.DOTALL):
        try:
            root = yaml.compose(fence['body'], Loader=yaml.SafeLoader)
        except yaml.YAMLError:
            continue
        research = member(root, 'audience_research')
        if research is None and member(root, 'research_status') is not None:
            research = root
        paths = []
        if research is not None:
            paths.extend((('market_scope', member(member(research, 'market_scope'), 'release_scope')),
                          ('target_audience', member(member(research, 'target_audience'), 'primary_reader_segment')),
                          ('language', member(member(research, 'market_scope'), 'language'))))
        profile = member(root, 'audience_profile')
        if profile is not None:
            paths.extend((('market_scope', member(profile, 'release_scope')),
                          ('target_audience', member(profile, 'primary_reader_segment')),
                          ('language', member(profile, 'language_and_register'))))
        for field, node in paths:
            if isinstance(node, yaml.ScalarNode) and node.tag == 'tag:yaml.org,2002:str':
                add(field, node.value, fence.start('body') + node.start_mark.index,
                    fence.start('body') + node.end_mark.index, 'explicit_audience_settings', 3)

    fields = {}
    for field in (*FIELDS, *NESTED_FIELDS):
        candidates = [item for item in sources if item['field'] == field]
        if not candidates:
            continue
        priority = min(item['priority'] for item in candidates)
        selected = [item for item in candidates if item['priority'] == priority]
        if len({_comparable(field, item['value']) for item in selected}) > 1:
            raise ValueError(f'Conflicting publication metadata: {field}; correct the frozen source before analysis')
        fields[field] = selected[0]['value']
    return {'version': PUBLICATION_METADATA_VERSION, 'snapshot_sha256': snapshot.snapshot_sha256,
            'authority_policy': 'Explicit primary headers override compressed cover handoff; missing fields have no defaults.',
            'fields': {key: value for key, value in fields.items() if key in FIELDS},
            'nested_fields': {key: value for key, value in fields.items() if key in NESTED_FIELDS}, 'sources': sources,
            'missing_fields': [field for field in FIELDS if field not in fields]}


def require_publication_metadata(metadata: Mapping[str, Any]) -> None:
    missing = [field for field in FIELDS if field != 'author' and not metadata['fields'].get(field)]
    if missing:
        raise ValueError('Missing source-bound publication metadata: ' + ', '.join(missing)
                         + '. 请在骨架顶部或封面交接资料中明确填写；不能由模型猜测。')


def apply_publication_metadata(raw: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Bind only explicit source-owned fields; never repair story content here."""
    require_publication_metadata(metadata)
    result = copy.deepcopy(dict(raw))
    key = 'brief' if 'brief' in result else 'brief_payload'
    if isinstance(result.get(key), dict):
        result[key]['author'] = metadata['fields'].get('author', '')
        for field, value in metadata['fields'].items():
            result[key][field] = value
        for field, value in metadata.get('nested_fields', {}).items():
            parent, child = field.split('.')
            target = result[key].get(parent)
            if isinstance(target, dict) and not target.get(child):
                target[child] = value
    return result
