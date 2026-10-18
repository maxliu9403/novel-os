"""Source-cited agent reviews allocate an auditable child, never edit a parent.

The immutable ledger is written before the child: interrupted setup fails closed
for the challenged parent and the same request can finish that setup. Suggestions
are review opinions supplied to the existing one-round semantic correction; they
are never applied as patches or recorded as provider attempts.
"""
from __future__ import annotations

import copy
import json
import re
import threading
from contextlib import contextmanager
from typing import Any, Mapping

import fcntl

from .cover_analysis import (
    CoverAnalysisError, CoverStoryAnalysis, CoverStoryAnalyzer, _analysis_dir,
    _analysis_lock, _now, _save, _validate_partial_extraction, load_analysis,
    reserve_analysis, segment_snapshot,
)
from .cover_sources import CoverSourceContext, _atomic_json, _safe_path, _store_root, canonical_hash, load_snapshot

REVIEW_SCHEMA = 'cover-external-source-review.v1'
LEDGER_SCHEMA = 'cover-analysis-review-ledger.v1'
_LOCK = threading.Lock()
_REPORT_FIELDS = {'schema_version', 'review_kind', 'reviewer_id', 'analysis_id', 'analysis_sha256',
                  'snapshot_id', 'snapshot_sha256', 'source_content_sha256', 'source_bundle_sha256',
                  'configuration_sha256', 'synthesis_sha256', 'issues'}
_ISSUE_FIELDS = {'issue_id', 'problem', 'target_path', 'observed_value', 'evidence_refs',
                 'exact_source_text', 'suggested_correction'}
_REVISION_IDENTITY = ('round', 'snapshot_sha256', 'configuration_sha256', 'previous_input_sha256',
                      'previous_synthesis_sha256', 'previous_analysis', 'verification_feedback')
_COPIED = ('coverage', 'segment_results', 'evidence', 'global_facts', 'aggregation_progress',
           'aggregation_checkpoints', 'global_checkpoint')


def _error(message, *, code='analysis_review_invalid', analysis_id=''):
    return CoverAnalysisError(code, message, analysis_id=analysis_id)


def _ledger_path(context, parent_id):
    _analysis_dir(context, parent_id)  # Validate before interpolating any path.
    return _safe_path(context.cover_root, f'analysis-reviews/{parent_id}.json')


@contextmanager
def _review_lock(context):
    root = _store_root(context, 'analysis-reviews')
    with _LOCK, _safe_path(root, '.review.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _load_ledger(context, parent_id):
    path = _ledger_path(context, parent_id)
    if not path.exists():
        return None
    try:
        ledger = json.loads(path.read_text(encoding='utf-8'))
        identity = {k: v for k, v in ledger.items() if k != 'ledger_sha256'}
        report = ledger['external_review']
        if (ledger['schema_version'] != LEDGER_SCHEMA or ledger['owner_scope'] != context.owner_scope
                or ledger['parent_analysis_id'] != parent_id or report['analysis_id'] != parent_id
                or ledger['parent_analysis_sha256'] != report['analysis_sha256']
                or ledger['review_sha256'] != canonical_hash(report)
                or ledger['review_id'] != 'review-' + ledger['review_sha256'][:32]
                or ledger['ledger_sha256'] != canonical_hash(identity)
                or ledger['child_analysis_id'] != _child_id(context, report)):
            raise ValueError('ledger binding mismatch')
        return ledger
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _error(f'External review ledger integrity failed: {exc}', analysis_id=parent_id) from exc


def _child_id(context, report):
    return 'analysis-' + canonical_hash({'scope': context.owner_scope,
        'parent': report['analysis_id'], 'parent_sha256': report['analysis_sha256'],
        'review_sha256': canonical_hash(report)})[:32]


def _tokens(pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/') or re.search(r'~(?![01])', pointer):
        raise _error('target_path must be an RFC 6901 JSON pointer')
    return [part.replace('~1', '/').replace('~0', '~') for part in pointer[1:].split('/')]


def _at(value, parts):
    for part in parts:
        if isinstance(value, list):
            if not re.fullmatch(r'0|[1-9][0-9]*', part):
                raise _error('target_path contains an invalid array index')
            value = value[int(part)]
        else:
            value = value[part]
    return value


def _validate_report(report, parent):
    try:
        if (not isinstance(report, dict) or set(report) != _REPORT_FIELDS
                or report['schema_version'] != REVIEW_SCHEMA or report['review_kind'] != 'agent'
                or not isinstance(report['reviewer_id'], str) or not report['reviewer_id'].strip()):
            raise _error('External review schema or agent reviewer identity is invalid')
        expected = {key: parent[key] for key in ('analysis_id', 'analysis_sha256', 'snapshot_id',
            'snapshot_sha256', 'source_content_sha256', 'source_bundle_sha256')}
        expected.update(configuration_sha256=canonical_hash(parent['configuration']),
                        synthesis_sha256=parent['global_checkpoint']['synthesis_sha256'])
        if any(report[key] != value for key, value in expected.items()):
            raise _error('External review source, synthesis or configuration binding mismatch')
        synthesis = parent['global_checkpoint']['synthesis']
        if canonical_hash(synthesis) != report['synthesis_sha256']:
            raise _error('Parent synthesis hash mismatch')
        evidence = {'evidence:' + row['evidence_id']: row for row in parent['evidence']}
        issues, seen = report['issues'], set()
        if not isinstance(issues, list) or not 1 <= len(issues) <= 100:
            raise _error('External review requires 1 to 100 source-cited issues')
        for issue in issues:
            if not isinstance(issue, dict) or set(issue) != _ISSUE_FIELDS:
                raise _error('Invalid external review issue schema')
            for key in ('issue_id', 'problem', 'suggested_correction', 'exact_source_text'):
                if not isinstance(issue[key], str) or not issue[key].strip():
                    raise _error(f'External review issue requires {key}')
            if issue['issue_id'] in seen:
                raise _error('External review issue IDs must be unique')
            seen.add(issue['issue_id'])
            observed = _at(synthesis, _tokens(issue['target_path']))
            if canonical_hash(observed) != canonical_hash(issue['observed_value']):
                raise _error('observed_value does not match the exact parent synthesis target')
            refs = issue['evidence_refs']
            if (not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in evidence for ref in refs)
                    or len(set(refs)) != len(refs)
                    or not any(issue['exact_source_text'] in evidence[ref]['exact_quote'] for ref in refs)):
                raise _error('exact_source_text must occur verbatim within one cited exact_quote')
        canonical_hash(report)  # Reject non-JSON values, including NaN.
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        if isinstance(exc, CoverAnalysisError):
            raise
        raise _error(f'Invalid external review: {exc}') from exc


def _extraction_hash(payload):
    return canonical_hash({key: payload[key] for key in ('segment_results', 'evidence')})


def _feedback(ledger, parent):
    report = ledger['external_review']
    refs = {ref for issue in report['issues'] for ref in issue['evidence_refs']}
    return [{'kind': 'external_review', 'review_kind': 'agent', 'reviewer_id': report['reviewer_id'],
             'review_id': ledger['review_id'], 'review_sha256': ledger['review_sha256'],
             'segment_ids': sorted({row['segment_id'] for row in parent['evidence']
                                    if 'evidence:' + row['evidence_id'] in refs}),
             'report': {'status': 'fail', 'scope': 'targeted_external_review',
                        'issues': copy.deepcopy(report['issues'])}}]


def _marker(ledger):
    return {key: ledger[key] for key in ('parent_analysis_id', 'parent_analysis_sha256',
        'review_id', 'review_sha256', 'ledger_sha256', 'extraction_sha256')}


def _child_ledger(context, analysis_id):
    """The allocation survives removal of a child's optional-looking marker.

    Review ledgers are deliberately few (one per challenged parent), and scanning
    them avoids a second index write with a crash window between two authorities.
    """
    root = _safe_path(context.cover_root, 'analysis-reviews')
    if root.exists():
        for path in root.glob('*.json'):
            ledger = _load_ledger(context, path.stem)
            if ledger['child_analysis_id'] == analysis_id:
                return ledger
    return None


def _walk(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _entity_identity(value):
    if not isinstance(value, dict):
        return None
    # Relationships are directed; from alone would match unrelated recipients.
    if 'from_character_id' in value and 'to_character_id' in value:
        return {key: value[key] for key in ('from_character_id', 'to_character_id')}
    for key in ('character_id', 'event_id', 'claim_id', 'relationship_id'):
        if key in value:
            return {key: value[key]}
    return None


def _assert_targets_changed(report, previous, synthesis, analysis_id):
    """Reject demonstrably unchanged targets; source review still judges meaning.

    IDs anchor array entities across reordering. Text/object values are searched
    through their whole anchored entity, so moving a relationship to another list
    index is not a correction. This is a regression guard, not semantic proof.
    """
    for issue in report['issues']:
        parts, node, anchor, suffix = _tokens(issue['target_path']), previous, None, []
        for offset, part in enumerate(parts):
            identity = _entity_identity(node)
            if identity:
                anchor, suffix = identity, parts[offset:]
            node = _at(node, [part])
        identity = _entity_identity(node)
        if identity:
            anchor, suffix = identity, []
        observed = issue['observed_value']
        if anchor:
            candidates = [item for item in _walk(synthesis) if isinstance(item, dict)
                          and all(item.get(k) == v for k, v in anchor.items())]
        else:
            try:
                candidates = [_at(synthesis, parts)]
            except (KeyError, IndexError, TypeError):
                candidates = []
        unchanged = False
        for candidate in candidates:
            if isinstance(observed, (str, dict, list)):
                unchanged |= any(canonical_hash(value) == canonical_hash(observed) for value in _walk(candidate))
            else:
                try:
                    unchanged |= canonical_hash(_at(candidate, suffix) if anchor else candidate) == canonical_hash(observed)
                except (KeyError, IndexError, TypeError):
                    pass
        if unchanged:
            raise _error(f"Source review issue {issue['issue_id']} remains unchanged after the single correction",
                         code='analysis_review_unresolved', analysis_id=analysis_id)


def validate_review_child(context, payload, *, completed=False, allow_pending_reservation=False):
    """Validate durable child provenance on reads and before completion."""
    marker = payload.get('external_review_revision')
    if marker is None:
        if 'external_review_revision' in payload:
            raise _error('External review child provenance cannot be null')
        ledger = _child_ledger(context, payload['analysis_id'])
        if ledger:
            if (allow_pending_reservation and payload['coverage_status'] == 'pending'
                    and not payload['attempts'] and not payload['segment_results']
                    and not payload['configuration'] and not payload.get('semantic_revision')):
                return  # Only the registrar may seed this reservation before use.
            raise _error('Allocated external review child is missing its provenance')
        return
    try:
        ledger = _load_ledger(context, marker['parent_analysis_id'])
        if not ledger or marker != _marker(ledger) or ledger['child_analysis_id'] != payload['analysis_id']:
            raise _error('External review child ledger binding mismatch')
        parent = load_analysis(context, ledger['parent_analysis_id']).to_dict()
        _validate_report(ledger['external_review'], parent)
        if (parent['analysis_sha256'] != ledger['parent_analysis_sha256']
                or parent['coverage_status'] != 'complete'
                or any(payload[key] != parent[key] for key in ('owner_scope', 'snapshot_id', 'snapshot_sha256',
                    'source_content_sha256', 'source_bundle_sha256', 'configuration', 'cache_key'))
                or _extraction_hash(payload) != ledger['extraction_sha256']
                or _extraction_hash(parent) != ledger['extraction_sha256']):
            raise _error('External review child source or extraction binding mismatch')
        revision = payload['semantic_revision']
        synthesis_configuration = {key: value for key, value in parent['configuration'].items()
                                   if key not in ('verification_policy_version', 'review_unit_version')}
        if (revision['round'] != 1 or revision['snapshot_sha256'] != parent['snapshot_sha256']
                or revision['configuration_sha256'] != canonical_hash(synthesis_configuration)
                or revision['previous_input_sha256'] != parent['global_checkpoint']['input_sha256']
                or revision['previous_synthesis_sha256'] != parent['global_checkpoint']['synthesis_sha256']
                or revision['previous_analysis'] != parent['global_checkpoint']['synthesis']
                or revision['verification_feedback'] != _feedback(ledger, parent)
                or revision['binding_sha256'] != canonical_hash({key: revision[key] for key in _REVISION_IDENTITY})):
            raise _error('External review semantic correction provenance mismatch')
        if completed:
            if (revision['status'] != 'complete'
                    or revision['result_sha256'] != payload['global_checkpoint']['synthesis_sha256']
                    or revision['request_sha256'] != payload['global_checkpoint']['input_sha256']):
                raise _error('External review correction did not complete')
            _assert_targets_changed(ledger['external_review'], revision['previous_analysis'],
                                    payload['global_checkpoint']['synthesis'], payload['analysis_id'])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        if isinstance(exc, CoverAnalysisError):
            raise
        raise _error(f'Invalid external review child provenance: {exc}', analysis_id=payload['analysis_id']) from exc


def assert_analysis_usable(context, analysis_id, payload=None, *, _seen=None):
    """Challenged parents stay historical even after a corrected child completes."""
    seen = set() if _seen is None else _seen
    if analysis_id in seen:
        raise _error('Analysis reuse ancestry contains a cycle')
    seen.add(analysis_id)
    if _load_ledger(context, analysis_id) is not None:
        raise _error('This analysis is challenged by a source review; use its corrected child',
                     code='analysis_review_required', analysis_id=analysis_id)
    payload = payload or load_analysis(context, analysis_id).to_dict()
    validate_review_child(context, payload, completed=payload['coverage_status'] == 'complete')
    if payload.get('reused_from_analysis_id'):
        assert_analysis_usable(context, payload['reused_from_analysis_id'], _seen=seen)


def _assert_budget(context, parent):
    seen = set()
    while True:
        analysis_id = parent['analysis_id']
        if analysis_id in seen:
            raise _error('Analysis ancestry contains a cycle')
        seen.add(analysis_id)
        if (parent.get('semantic_revision') or parent.get('external_review_revision')
                or parent.get('reviewed_source_amendment') or parent.get('inherited_semantic_revision')):
            raise _error('The single source-bound semantic correction is already allocated',
                         code='analysis_semantic_repair_exhausted', analysis_id=analysis_id)
        ancestor = parent.get('reused_from_analysis_id')
        if not ancestor:
            return
        if _load_ledger(context, ancestor):
            raise _error('A reused ancestor already has an external source review', code='analysis_review_required')
        parent = load_analysis(context, ancestor).to_dict()


def _seed_child(context, snapshot, parent, ledger, analyzer):
    child_id = ledger['child_analysis_id']
    folder = _analysis_dir(context, child_id)
    if not folder.exists():
        reserve_analysis(context, snapshot, analysis_id=child_id)
    elif not _safe_path(folder, 'analysis.json').exists():
        # A reserve interrupted between mkdir and atomic write has no content to
        # preserve. Never delete a nonempty/corrupt analysis directory.
        if any(folder.iterdir()):
            raise _error('Interrupted child reservation contains unrecognized artifacts')
        folder.rmdir()
        reserve_analysis(context, snapshot, analysis_id=child_id)
    with _analysis_lock(context, child_id):
        child = load_analysis(context, child_id).to_dict()
        if child.get('external_review_revision'):
            validate_review_child(context, child, completed=child['coverage_status'] == 'complete')
            return
        if (child['coverage_status'] != 'pending' or child['attempts'] or child['segment_results']
                or child['snapshot_id'] != parent['snapshot_id']):
            raise _error('Existing child is not an empty reservation for this source review')
        for key in _COPIED:
            if key in parent:
                child[key] = copy.deepcopy(parent[key])
        revision = {'round': 1, 'snapshot_sha256': snapshot.snapshot_sha256,
            'configuration_sha256': canonical_hash(analyzer._synthesis_configuration()),
            'previous_input_sha256': parent['global_checkpoint']['input_sha256'],
            'previous_synthesis_sha256': parent['global_checkpoint']['synthesis_sha256'],
            'previous_analysis': copy.deepcopy(parent['global_checkpoint']['synthesis']),
            'verification_feedback': _feedback(ledger, parent)}
        revision.update(binding_sha256=canonical_hash(revision), status='pending',
            verification_policy_version=parent['configuration'].get('verification_policy_version'),
            review_unit_version=parent['configuration'].get('review_unit_version'))
        child.update(configuration=copy.deepcopy(parent['configuration']), cache_key=parent['cache_key'],
            coverage_status='partial', phase='global', verification={}, verification_progress={'segments': {}},
            external_review_revision=_marker(ledger), semantic_revision=revision)
        validate_review_child(context, child)
        _save(context, child)


def start_review_revision(context: CoverSourceContext, *, parent_analysis_id: str,
                          expected_analysis_sha256: str, external_review: Mapping[str, Any],
                          analyzer: CoverStoryAnalyzer, cancel_event=None) -> CoverStoryAnalysis:
    """Register a strict agent review and resume its one source-verified child."""
    report = copy.deepcopy(dict(external_review))
    with _review_lock(context):
        parent = load_analysis(context, parent_analysis_id).to_dict()
        if parent['coverage_status'] != 'complete' or parent['analysis_sha256'] != expected_analysis_sha256:
            raise _error('Expected an unchanged complete parent analysis', code='analysis_conflict')
        _validate_report(report, parent)
        if parent['configuration'] != analyzer.configuration:
            raise _error('Review correction must use the exact parent configuration', code='analysis_conflict')
        snapshot = load_snapshot(context, parent['snapshot_id'])
        _validate_partial_extraction(parent, snapshot, segment_snapshot(snapshot,
            max_segment_bytes=parent['configuration']['max_segment_bytes'],
            segmenter_version=parent['configuration']['segmenter_version']))
        ledger = _load_ledger(context, parent_analysis_id)
        if ledger:
            if ledger['review_sha256'] != canonical_hash(report):
                raise _error('A different review already allocated this parent correction', code='analysis_conflict')
        else:
            _assert_budget(context, parent)
            review_hash = canonical_hash(report)
            ledger = {'schema_version': LEDGER_SCHEMA, 'owner_scope': context.owner_scope,
                'parent_analysis_id': parent_analysis_id, 'parent_analysis_sha256': parent['analysis_sha256'],
                'review_id': 'review-' + review_hash[:32], 'review_sha256': review_hash,
                'child_analysis_id': _child_id(context, report), 'created_at': _now(),
                'extraction_sha256': _extraction_hash(parent), 'external_review': report}
            ledger['ledger_sha256'] = canonical_hash(ledger)
            _atomic_json(_ledger_path(context, parent_analysis_id), ledger)
        _seed_child(context, snapshot, parent, ledger, analyzer)
    # The existing child lock serializes duplicate resumes. Never hold the review
    # lock during provider calls; other reviewed projects remain independent.
    return analyzer.analyze(context, snapshot, force=True, analysis_id=ledger['child_analysis_id'],
                            cancel_event=cancel_event)
