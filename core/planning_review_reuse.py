"""Reuse only identical, previously passed review windows after a bounded edit.

The old receipt must belong to the exact original candidate. The new window must
have the same instructions AND complete supplied context; changed global design,
source, chapter content, coverage or correction evidence invalidates reuse.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def passed_window(planner, baseline: dict, name: str, context: dict, instruction: str) -> tuple[dict, str] | None:
    from core.outline_planning import content_hash

    if not re.fullmatch(r'[a-zA-Z0-9_-]+', name):
        raise ValueError('Invalid review checkpoint name')
    dependencies = {'candidate': content_hash(baseline), 'context': content_hash(context),
                    'policy': content_hash(instruction)}
    # Read through the planner's identity, dependency, payload and contract checks.
    # A matching hash alone never turns an old blocked result into a pass.
    for path in sorted(Path(planner.cache).glob(f'{name}-window-*.json')):
        if not re.fullmatch(re.escape(name) + r'-window-\d{3}(?:-\d{3})?\.json', path.name):
            continue
        record = json.loads(path.read_text(encoding='utf-8'))
        if record.get('dependencies_hash') != content_hash(dependencies):
            continue
        result = planner._load(path.stem, dependencies, planner._review)
        if result is not None and result['status'] == 'passed':
            return result, path.stem
    return None


RULES = '''
The coordinated_verification record is a proposed resolution, NOT approval.
Recheck each relevant original issue against the actual supplied candidate and
source. Confirm corrections, continuity bridges and all unchanged author choices.
A bridge must connect source-established actions, not replace their actor or
mechanism, or grant an unestablished capability. Code-bound reference metadata
proves source location only; it does not certify the proposed inference or repair.
Distinguish incompatible established facts from a missing transition and from an
interpretation that the evidence does not uniquely require. Never waive a genuine
contradiction because a repair model disputes it. A character's fallible judgement
is not inherently impossible; assess its stated cause, information and consequences.
Do not impose a preferred tactic as canon. Any still-material issue must block.
The ordinary source, identity, emotional depth and ending checks all still apply.
'''


def verification(context: dict, audit: dict | None) -> dict:
    """Attach full resolution records only to windows containing their dependencies."""
    if audit is None:
        return context
    numbers = set(context.get('review_window', {}).get('included_chapters', []))
    records = [item for item in audit['issues'] if not numbers or
               not (item['requested_chapters'] or item['dependency_chapters']) or
               numbers.intersection(item['requested_chapters'] + item['dependency_chapters'])]
    if records:
        context = {**context, 'coordinated_verification': {
            'schema': audit['schema'], 'baseline_candidate_sha256': audit['baseline_candidate_sha256'],
            'issues': records,
        }}
    return context
