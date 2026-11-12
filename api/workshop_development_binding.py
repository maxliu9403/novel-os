"""Host-only bindings for exact title/metadata changes after delivery review.

These transformations do not review or authorize new narrative. Callers apply
their existing deterministic packaging or author-selected title operation first,
then bind its complete before/after bytes under the Workshop session lock.
"""
from copy import deepcopy

from core.creative_execution import make_development_basis, text_hash


def rebind_development_source(session: dict, before_source: str, after_source: str, kind: str) -> bool:
    """Keep the original review and append only a verified host transformation.

    Legacy skeletons have no delivery verification and stay on their old handoff
    contract. Build the replacement completely before assigning it, so stale or
    incomplete receipts cannot partially rewrite the current session.
    """
    development = session.get('development') or {}
    verification = development.get('delivery_verification')
    if verification is None:
        return False
    if kind not in {'title', 'packaging'}:
        raise ValueError('仅书名和宿主元数据整理可沿用既有核验；故事修订须重新核验。')
    before_sha256, after_sha256 = text_hash(before_source), text_hash(after_source)
    basis = make_development_basis(verification, before_sha256)
    if before_source == after_source:
        return False
    updated = deepcopy(verification)
    updated['reviewed_source_sha256'] = basis['reviewed_source_sha256']
    updated['source_sha256'] = after_sha256
    updated['transformations'] = [*basis['transformations'], {
        'kind': kind, 'before_sha256': before_sha256, 'after_sha256': after_sha256,
    }]
    # The session stores the host verification shape; frozen handoffs separately
    # carry a hashed development-basis record. Normalize if a future reader fed
    # that record back through this host seam.
    if updated.get('schema') == basis['schema']:
        from core.creative_execution import digest
        updated['sha256'] = digest({key: value for key, value in updated.items() if key != 'sha256'})
    make_development_basis(updated, after_sha256)
    session['development']['delivery_verification'] = updated
    return True
