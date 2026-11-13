"""Bind a planning citation to a JSON leaf of the candidate actually shown."""
from __future__ import annotations

import json
import math


def _field(candidate, path):
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError
    node = candidate
    member = None
    for depth, token in enumerate(path[1:].split('/')):
        key = token.replace('~1', '/').replace('~0', '~')
        if token != key.replace('~', '~0').replace('/', '~1'):
            raise ValueError
        if depth == 0 and key in ('chapters', 'chapter_map'):
            raise ValueError
        if isinstance(node, dict):
            node = node[key]
            member = key
        elif isinstance(node, list) and token.isascii() and token.isdecimal():
            index = int(token)
            if str(index) != token:
                raise ValueError
            node = node[index]
            member = None
        else:
            raise ValueError
    return node, member


def _same_json(left, right):
    return (type(left) is type(right)
            and json.dumps(left, allow_nan=False) == json.dumps(right, allow_nan=False))


def _old_scalar_quote(quote, literal, member):
    if not isinstance(quote, str):
        return False
    stripped = quote.strip(' \t\r\n')
    if stripped == literal:
        return True
    if member is None or not stripped.startswith('"'):
        return False
    key, end = json.JSONDecoder().raw_decode(stripped)
    tail = stripped[end:].lstrip(' \t\r\n')
    return key == member and tail.startswith(':') and tail[1:].strip(' \t\r\n') == literal


def bind(reference, candidate, visible):
    """Return the canonical citation or None. A citation grants no edit rights."""
    if not isinstance(reference, dict) or set(reference) not in ({'path', 'quote'}, {'path', 'value'}):
        return None
    path = reference['path']
    try:
        field, member = _field(visible, path)
        if isinstance(field, (list, dict)):
            return None
        if visible is not candidate and not _same_json(field, _field(candidate, path)[0]):
            return None
        if isinstance(field, str):
            quote = reference.get('quote')
            if isinstance(quote, str) and ((field == quote == '') or (quote.strip() and quote in field)):
                return {'path': path, 'quote': quote}
            return None
        if field is not None and (type(field) not in (int, bool, float)
                                  or type(field) is float and not math.isfinite(field)):
            return None
        literal = json.dumps(field, allow_nan=False)
        if ('value' in reference and _same_json(field, reference['value'])
                or 'quote' in reference and _old_scalar_quote(reference['quote'], literal, member)):
            return {'path': path, 'quote': literal}
    except (ValueError, KeyError, IndexError, TypeError):
        pass
    return None
