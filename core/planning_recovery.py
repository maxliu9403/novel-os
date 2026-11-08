"""Read-only eligibility for an explicit retry with the source-restoration policy."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from core.planning_correction import VERSION


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_restoration_available(project: Path, run_id: str, progress: dict) -> bool:
    """Offer recovery for v1's unsupported-scope result, never a failed review.

    This is a capability projection, not a state mutation or semantic approval.
    Native resume revalidates every dependency and the complete resulting plan.
    """
    if (progress.get('status') != 'failed'
            or (progress.get('recovery') or {}).get('retryable') is not False
            or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', run_id)):
        return False
    identity_hash = progress.get('identity_hash')
    if not isinstance(identity_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', identity_hash):
        return False
    root = project / 'outputs' / 'runs' / run_id / 'planning' / 'checkpoints' / identity_hash

    def read(path: Path) -> dict:
        if path.resolve() != path.absolute() or path.stat().st_size > 512_000:
            raise ValueError('Not a bounded real checkpoint')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Not a checkpoint object')
        return value

    def payload(name: str) -> dict:
        record = read(root / f'{name}.json')
        value = record['payload']
        if (record.get('schema_version') != 'outline-planning.v1'
                or record.get('identity_hash') != identity_hash
                or record.get('payload_hash') != _hash(value) or not isinstance(value, dict)):
            raise ValueError('Invalid checkpoint')
        return value

    try:
        if root.resolve() != root.absolute() or any(root.glob(f'{VERSION}*.json')):
            return False
        if (root / 'review-global-reference-v1.json').exists():
            return False
        identity = read(root / 'identity.json')
        source = project / 'outputs' / 'input' / 'prompt.md'
        if source.resolve() != source.absolute() or source.stat().st_size > 4_000_000:
            return False
        if _hash(identity) != identity_hash or hashlib.sha256(source.read_bytes()).hexdigest() != identity.get('source_sha256'):
            return False
        old = payload('global-reference-repair-v1')
        review = payload('review-initial')
        return (old.get('status') == 'requires_author' and old.get('edits') == []
                and review.get('status') == 'blocked' and bool(review.get('issues'))
                and all(isinstance(i, dict) and i.get('scope') == 'global' for i in review['issues']))
    except (OSError, ValueError, TypeError, KeyError):
        return False
