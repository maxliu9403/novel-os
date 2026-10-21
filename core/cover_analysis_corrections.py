"""Explicit, source-cited amendments after the automatic correction is exhausted.

Registration never calls a provider. A new child keeps the parent's extraction,
applies only reviewed narrative fields, then runs the normal independent review.
The immutable allocation ledger prevents retries or missing markers from turning
this manual recovery into a fresh automatic generation budget.
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
    CoverAnalysisError, CoverStoryAnalysis, _analysis_dir, _analysis_lock, _now,
    _save, _validate_global, _validate_partial_extraction, load_analysis,
    reserve_analysis, segment_snapshot,
)
from .cover_sources import _atomic_json, _safe_path, _store_root, canonical_hash, load_snapshot

SCHEMA = 'cover-reviewed-source-amendment.v1'
_LOCK = threading.Lock()
_COPIED = ('coverage', 'segment_results', 'evidence', 'global_facts', 'aggregation_progress',
           'aggregation_checkpoints')
_OP_FIELDS = {'path', 'old_value', 'new_value', 'evidence_refs', 'exact_source_text'}
_NARRATIVE = {
    'story': {'core_conflict', 'protagonist_goal', 'stakes', 'reader_promise'},
    'characters': set(),
    'relationships': {'relationship', 'epoch'},
    'events': {'epoch', 'location', 'action', 'reaction', 'consequence'},
    'suspense': {'question', 'withheld_answer', 'reveal_epoch'},
    'visual_assets': {'description'},
    'critical_claims': {'statement'},
}
_BRIEF_TEXT = {'core_conflict', 'core_task', 'emotional_promise', 'visual_hook', 'spoiler_boundary'}
_ENV_TEXT = {'fictional_place', 'era', 'social_class', 'daily_life', 'cultural_context'}
_ENV_ARRAYS = {'primary_spaces', 'recurring_objects', 'economic_signals', 'daily_rituals'}


def _error(message, *, code='analysis_amendment_invalid', analysis_id=''):
    return CoverAnalysisError(code, message, analysis_id=analysis_id)


def _ledger_path(context, parent_id):
    _analysis_dir(context, parent_id)
    return _safe_path(context.cover_root, f'analysis-amendments/{parent_id}.json')


@contextmanager
def _registration_lock(context):
    root = _store_root(context, 'analysis-amendments')
    with _LOCK, _safe_path(root, '.amendment.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _child_id(context, report):
    return 'analysis-' + canonical_hash({'scope': context.owner_scope, 'review': report})[:32]


def _read_ledger(context, parent_id):
    path = _ledger_path(context, parent_id)
    if not path.exists():
        return None
    try:
        ledger = json.loads(path.read_text(encoding='utf-8'))
        report = ledger['review']
        if (ledger['schema_version'] != SCHEMA or ledger['owner_scope'] != context.owner_scope
                or ledger['parent_analysis_id'] != parent_id or report['parent_analysis_id'] != parent_id
                or ledger['review_sha256'] != canonical_hash(report)
                or ledger['ledger_sha256'] != canonical_hash({k: v for k, v in ledger.items() if k != 'ledger_sha256'})
                or ledger['child_analysis_id'] != _child_id(context, report)):
            raise ValueError('amendment ledger binding mismatch')
        return ledger
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise _error(f'Source amendment ledger integrity failed: {exc}') from exc


def _allocated_child(context, analysis_id):
    root = _safe_path(context.cover_root, 'analysis-amendments')
    if root.exists():
        for path in root.glob('analysis-*.json'):
            ledger = _read_ledger(context, path.stem)
            if ledger['child_analysis_id'] == analysis_id:
                return ledger
    return None


def _marker(ledger):
    return {key: ledger[key] for key in ('parent_analysis_id', 'child_analysis_id', 'review_sha256', 'ledger_sha256')}


def _tokens(path):
    if not isinstance(path, str) or not path.startswith('/') or re.search(r'~(?![01])', path):
        raise _error('Amendment path must be an RFC 6901 JSON pointer')
    return [part.replace('~1', '/').replace('~0', '~') for part in path[1:].split('/')]


def _index(part):
    return bool(re.fullmatch(r'0|[1-9][0-9]*', part))


def _at(value, parts):
    for part in parts:
        if isinstance(value, list):
            if not _index(part):
                raise _error('Invalid amendment array index')
            value = value[int(part)]
        else:
            value = value[part]
    return value


def _allowed(parts):
    if len(parts) == 2 and parts[0] == 'story':
        return parts[1] in _NARRATIVE['story'] | {'evidence_refs'}
    if len(parts) == 3 and parts[0] in _NARRATIVE and _index(parts[1]):
        return parts[2] in _NARRATIVE[parts[0]] | {'evidence_refs'}
    if parts[:1] == ['brief_payload']:
        if len(parts) == 2:
            return parts[1] in _BRIEF_TEXT
        if parts[1:2] == ['lived_environment']:
            return ((len(parts) == 3 and parts[2] in _ENV_TEXT)
                    or (len(parts) == 4 and parts[2] in _ENV_ARRAYS and _index(parts[3])))
    return False


def _apply(parent, amendments):
    """Only existing narrative strings and append-only references can change."""
    if not isinstance(amendments, list) or not 1 <= len(amendments) <= 32:
        raise _error('Source amendment requires 1 to 32 explicit field changes')
    synthesis = copy.deepcopy(parent['global_checkpoint']['synthesis'])
    evidence = {'evidence:' + row['evidence_id']: row for row in parent['evidence']}
    seen = set()
    try:
        for op in amendments:
            if not isinstance(op, dict) or set(op) != _OP_FIELDS:
                raise _error('Invalid source amendment operation schema')
            parts = _tokens(op['path'])
            if not _allowed(parts) or op['path'] in seen:
                raise _error('Amendments may change each existing narrative field only once; IDs, cast and metadata are immutable')
            seen.add(op['path'])
            old = _at(synthesis, parts)
            if canonical_hash(old) != canonical_hash(op['old_value']) or old == op['new_value']:
                raise _error('Amendment old_value mismatch or unchanged replacement')
            refs, quote = op['evidence_refs'], op['exact_source_text']
            if (not isinstance(refs, list) or not refs or any(not isinstance(r, str) or r not in evidence for r in refs)
                    or len(set(refs)) != len(refs) or not isinstance(quote, str) or not quote.strip()
                    or not any(quote in evidence[r]['exact_quote'] for r in refs)):
                raise _error('Every amendment requires known source refs and a verbatim quote')
            new = op['new_value']
            if parts[-1] == 'evidence_refs':
                if (not isinstance(old, list) or not isinstance(new, list) or new[:len(old)] != old
                        or len(new) <= len(old) or any(not isinstance(r, str) or r not in evidence for r in new)
                        or len(set(new)) != len(new) or not set(new[len(old):]).issubset(set(refs))):
                    raise _error('Evidence changes may only append cited known refs without deleting or reordering existing refs')
            elif not isinstance(old, str) or not isinstance(new, str) or not new.strip():
                raise _error('Narrative amendments replace nonempty text only')
            container = _at(synthesis, parts[:-1])
            container[int(parts[-1]) if isinstance(container, list) else parts[-1]] = copy.deepcopy(new)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        if isinstance(exc, CoverAnalysisError):
            raise
        raise _error(f'Invalid amendment target: {exc}') from exc
    return synthesis


def _bindings(parent):
    return {
        'parent_analysis_id': parent['analysis_id'], 'parent_payload_sha256': canonical_hash(parent),
        **{key: parent[key] for key in ('snapshot_id', 'snapshot_sha256', 'source_content_sha256', 'source_bundle_sha256')},
        'configuration_sha256': canonical_hash(parent['configuration']),
        'extraction_sha256': canonical_hash({key: parent[key] for key in ('coverage', 'segment_results', 'evidence')}),
        'previous_input_sha256': parent['global_checkpoint']['input_sha256'],
        'previous_synthesis_sha256': parent['global_checkpoint']['synthesis_sha256'],
    }


def _validate_report(context, report, parent):
    if (report.get('schema_version') != SCHEMA or report.get('review_kind') not in {'agent', 'human'}
            or not isinstance(report.get('reviewer_id'), str) or not report['reviewer_id'].strip()):
        raise _error('A named source reviewer is required')
    if any(report.get(k) != v for k, v in _bindings(parent).items()):
        raise _error('Source amendment parent, checkpoint, source, configuration or extraction binding mismatch')
    if (parent['phase'] != 'failed' or parent['coverage_status'] not in {'partial', 'failed'}
            or parent.get('last_error', {}).get('code') != 'analysis_semantic_repair_exhausted'
            or parent.get('semantic_revision', {}).get('status') != 'exhausted'
            or parent.get('reviewed_source_amendment')):
        raise _error('Source amendments require an unchanged exhausted automatic-correction parent')
    snapshot = load_snapshot(context, parent['snapshot_id'])
    segments = segment_snapshot(snapshot, max_segment_bytes=parent['configuration']['max_segment_bytes'],
                                segmenter_version=parent['configuration']['segmenter_version'])
    _validate_partial_extraction(parent, snapshot, segments)
    if (set(parent['segment_results']) != {s.segment_id for s in segments}
            or parent['coverage']['failed_segment_ids']):
        raise _error('Source amendments require complete preserved extraction')
    checkpoint = parent['global_checkpoint']
    if canonical_hash(checkpoint['synthesis']) != checkpoint['synthesis_sha256']:
        raise _error('Parent synthesis checkpoint digest mismatch')
    previous = _validate_global(checkpoint['synthesis'], snapshot, parent['evidence'])
    if previous != checkpoint['synthesis']:
        raise _error('Parent synthesis does not round-trip under the current contract')
    amended = _apply(parent, report['amendments'])
    validated = _validate_global(amended, snapshot, parent['evidence'])
    if amended != validated:
        raise _error('Amendment cannot change undeclared fields during schema validation')
    if len(json.dumps(validated, ensure_ascii=False).encode('utf-8')) > parent['configuration']['max_request_bytes'] * .30:
        raise _error('Amended synthesis exceeds the existing synthesis byte budget; no generation is permitted')
    return validated


def _checkpoint(ledger, synthesis):
    return {'input_sha256': canonical_hash({'schema_version': SCHEMA,
                'review_sha256': ledger['review_sha256'], 'parent': ledger['review']['parent_payload_sha256']}),
            'synthesis_sha256': canonical_hash(synthesis), 'synthesis': synthesis}


def validate_source_amendment(context, payload, *, completed=False, allow_pending_reservation=False):
    marker = payload.get('reviewed_source_amendment')
    if marker is None:
        if 'reviewed_source_amendment' in payload:
            raise _error('Source amendment provenance cannot be null')
        ledger = _allocated_child(context, payload['analysis_id'])
        if ledger:
            if (allow_pending_reservation and payload['coverage_status'] == 'pending' and not payload['attempts']
                    and not payload['segment_results'] and not payload['configuration']):
                return
            raise _error('Allocated source amendment child is missing its provenance')
        return
    try:
        ledger = _read_ledger(context, marker['parent_analysis_id'])
        if not ledger or marker != _marker(ledger) or payload['analysis_id'] != ledger['child_analysis_id']:
            raise _error('Source amendment child ledger binding mismatch')
        parent = load_analysis(context, ledger['parent_analysis_id']).to_dict()
        synthesis = _validate_report(context, ledger['review'], parent)
        if (any(payload.get(key) != parent.get(key) for key in (*_COPIED, 'configuration', 'cache_key',
                    'owner_scope', 'snapshot_id', 'snapshot_sha256', 'source_content_sha256', 'source_bundle_sha256'))
                or payload.get('inherited_semantic_revision') != parent.get('semantic_revision')
                or payload.get('semantic_revision') or payload.get('external_review_revision')
                or payload.get('global_checkpoint') != _checkpoint(ledger, synthesis)
                or any(a.get('phase') != 'verify' for a in payload['attempts'])):
            raise _error('Source amendment extraction, synthesis or inherited correction history changed')
        if completed and any(payload.get(key) != value for key, value in synthesis.items()):
            raise _error('Completed source amendment does not match the reviewed fields')
    except (KeyError, TypeError, ValueError, OSError) as exc:
        if isinstance(exc, CoverAnalysisError):
            raise
        raise _error(f'Invalid source amendment provenance: {exc}') from exc


def _exhausted_under_configuration(payload, configuration=None):
    """A policy upgrade remains subject to the analyzer's existing migration gate."""
    from .cover_analysis import VERIFICATION_POLICY_VERSION, REVIEW_UNIT_VERSION
    revision = payload.get('semantic_revision') or {}
    if payload.get('coverage_status') == 'complete' or revision.get('status') != 'exhausted':
        return False
    current = configuration if configuration is not None else {
        'verification_policy_version': VERIFICATION_POLICY_VERSION,
        'review_unit_version': REVIEW_UNIT_VERSION,
    }
    return all(revision.get(key) == current.get(key)
               for key in ('verification_policy_version', 'review_unit_version'))


def source_amendment_recovery(context, payload, *, configuration=None):
    """Read-only recovery guidance; never allocate, dispatch or bless a review.

    A parent link is navigation only. The caller must explicitly select the
    child and request its independent verification; no request is redirected.
    """
    analysis_id = payload['analysis_id']
    marker = payload.get('reviewed_source_amendment')
    if marker:
        validate_source_amendment(context, payload, completed=payload['coverage_status'] == 'complete')
        if payload.get('source_amendment_failure'):
            return {'kind': 'amendment_failed', 'can_resume': False,
                    'analysis_id': analysis_id, 'parent_analysis_id': marker['parent_analysis_id'],
                    'message': '来源修订仍未通过独立核查，不能重复继续。原始分析和修订结果已保留，请先检查具体问题。'}
        if payload['coverage_status'] != 'complete':
            return {'kind': 'amendment_pending', 'can_resume': True,
                    'analysis_id': analysis_id, 'parent_analysis_id': marker['parent_analysis_id'],
                    'message': '来源局部修订已保存，尚未完成独立核查。继续将复用已有提取结果，仅核查修订后的分析，不重新出图。'}
        return None
    try:
        ledger = _read_ledger(context, analysis_id)
        if ledger:
            child = load_analysis(context, ledger['child_analysis_id']).to_dict()
            validate_source_amendment(context, child, completed=child['coverage_status'] == 'complete')
            return {'kind': 'amendment_available', 'can_resume': False,
                    'analysis_id': child['analysis_id'], 'parent_analysis_id': analysis_id,
                    'message': '此历史分析的自动修正已耗尽；已保存独立的来源修订。请查看修订后的分析，原分析不会重复执行。'}
    except (CoverAnalysisError, OSError, KeyError, TypeError):
        return {'kind': 'amendment_failed', 'can_resume': False,
                'parent_analysis_id': analysis_id,
                'message': '来源修订记录尚未完整保存或未通过完整性检查，暂不能继续。请先检查保存的修订记录。'}
    if _exhausted_under_configuration(payload, configuration):
        return {'kind': 'source_correction_required', 'can_resume': False,
                'parent_analysis_id': analysis_id,
                'message': '此分析的自动修正已耗尽；再次继续不会重新核查。需要先依据原文保存局部修订，再对修订后的分析独立核查。已有提取结果和错误记录会保留。'}
    return None


def assert_source_amendment_usable(context, payload, *, configuration=None):
    if _read_ledger(context, payload['analysis_id']):
        recovery = source_amendment_recovery(context, payload, configuration=configuration)
        error = _error(recovery['message'], code='analysis_amendment_required', analysis_id=payload['analysis_id'])
        error.details = {'recovery': recovery}
        raise error
    validate_source_amendment(context, payload, completed=payload['coverage_status'] == 'complete')
    if payload.get('source_amendment_failure'):
        recovery = source_amendment_recovery(context, payload, configuration=configuration)
        error = _error(recovery['message'], code='analysis_amendment_verification_failed',
                       analysis_id=payload['analysis_id'])
        error.details = {'recovery': recovery}
        raise error
    if _exhausted_under_configuration(payload, configuration):
        recovery = source_amendment_recovery(context, payload, configuration=configuration)
        summary = (payload.get('semantic_revision') or {}).get('exhaustion_summary')
        message = recovery['message'] + (f'\n原核查问题：{summary}' if summary else '')
        error = _error(message, code='analysis_semantic_repair_exhausted',
                       analysis_id=payload['analysis_id'])
        error.details = {'recovery': recovery}
        raise error


def _seed_child(context, snapshot, parent, ledger):
    child_id = ledger['child_analysis_id']
    folder = _analysis_dir(context, child_id)
    if not folder.exists():
        reserve_analysis(context, snapshot, analysis_id=child_id)
    elif not _safe_path(folder, 'analysis.json').exists():
        if any(folder.iterdir()):
            raise _error('Interrupted amendment reservation contains unrecognized artifacts')
        folder.rmdir()
        reserve_analysis(context, snapshot, analysis_id=child_id)
    with _analysis_lock(context, child_id):
        child = load_analysis(context, child_id).to_dict()
        if child.get('reviewed_source_amendment'):
            return CoverStoryAnalysis(child)
        if (child['coverage_status'] != 'pending' or child['attempts'] or child['segment_results']
                or child['snapshot_id'] != parent['snapshot_id']):
            raise _error('Existing amendment child is not an empty reservation')
        for key in _COPIED:
            if key in parent:
                child[key] = copy.deepcopy(parent[key])
        synthesis = _validate_report(context, ledger['review'], parent)
        child.update(configuration=copy.deepcopy(parent['configuration']), cache_key=parent['cache_key'],
            coverage_status='partial', phase='verify', verification={}, verification_progress={'segments': {}},
            reviewed_source_amendment=_marker(ledger),
            inherited_semantic_revision=copy.deepcopy(parent['semantic_revision']),
            global_checkpoint=_checkpoint(ledger, synthesis))
        child.update(copy.deepcopy(synthesis))
        validate_source_amendment(context, child)
        _save(context, child)
        return CoverStoryAnalysis(child)


def prepare_source_amendment(context, *, parent_analysis_id: str, expected_parent_sha256: str,
                             reviewer_id: str, amendments: list[Mapping[str, Any]],
                             review_kind: str = 'agent') -> CoverStoryAnalysis:
    """Register an explicit audited child, without dispatching any model call.

    Resume the returned analysis_id through the ordinary analyze/API operation.
    Each parent permits one distinct reviewed amendment; exact retries recover
    the same child after interruption or completion. No parent data is changed.
    """
    with _registration_lock(context), _analysis_lock(context, parent_analysis_id):
        parent = load_analysis(context, parent_analysis_id).to_dict()
        if canonical_hash(parent) != expected_parent_sha256:
            raise _error('Expected unchanged parent payload', code='analysis_conflict')
        report = {'schema_version': SCHEMA, 'review_kind': review_kind, 'reviewer_id': reviewer_id,
                  **_bindings(parent), 'amendments': copy.deepcopy(amendments)}
        _validate_report(context, report, parent)
        ledger = _read_ledger(context, parent_analysis_id)
        if ledger:
            if ledger['review_sha256'] != canonical_hash(report):
                raise _error('A different source amendment already owns this parent', code='analysis_conflict')
        else:
            ledger = {'schema_version': SCHEMA, 'owner_scope': context.owner_scope,
                'parent_analysis_id': parent_analysis_id, 'review': report,
                'review_sha256': canonical_hash(report), 'child_analysis_id': _child_id(context, report),
                'created_at': _now()}
            ledger['ledger_sha256'] = canonical_hash(ledger)
            _atomic_json(_ledger_path(context, parent_analysis_id), ledger)
        return _seed_child(context, load_snapshot(context, parent['snapshot_id']), parent, ledger)
