"""Lossless text projection at the rich-foundation / execution-contract boundary.

The foundation retains structured themes for planning and review. Older text-only
consumers receive both the name and argument; arbitrary objects are never silently
stringified or reduced to their names.
"""
from __future__ import annotations


def theme_statements(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError('Foundation themes must be a list of strings or {name, argument} records')
    result = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            if item.strip():
                result.append(item.strip())
            continue
        if (not isinstance(item, dict) or not {'name'} <= item.keys() <= {'name', 'argument'}
                or not isinstance(item['name'], str) or not item['name'].strip()
                or ('argument' in item and (not isinstance(item['argument'], str) or not item['argument'].strip()))):
            raise ValueError(f'Foundation themes[{index}] must be text or a record with nonblank name and optional argument strings; other fields cannot be discarded')
        result.append(item['name'].strip() + (': ' + item['argument'].strip() if 'argument' in item else ''))
    return tuple(result)
