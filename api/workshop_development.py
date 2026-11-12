"""One durable promotion seam shared by original and source-based workshops.

The existing worker owns locking and cancellation. This module saves completed
generation before independent assessment; it never edits a live session from a
model callback, adopts a proposal, or replaces an existing delivery checkpoint.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import time

from core.creative_baseline import DEVELOPMENT_VERSION, build, validate, view
from core.creative_types import digest
from core.reference_assets import atomic_json
from core.source_route_context import _managed


def enabled(session):
    return (session.get('development') or {}).get('schema') == DEVELOPMENT_VERSION


def initialize(session):
    if not enabled(session):
        session['development'] = {'schema': DEVELOPMENT_VERSION, 'status': 'active',
            'initialized_revision': session['revision'], 'candidate_history': []}


def begin(session, *, read_only_origin=None):
    """Explicit new work is a migration boundary; GET and old resume never are."""
    initialize(session)
    state = session['development']
    baseline = build(session)
    state['active'] = {'run_id': session['delivery_run_id'], 'baseline': baseline,
                       'source_revision': session['revision']}
    if read_only_origin is not None:
        state['active']['read_only_origin'] = deepcopy(read_only_origin)
    state['status'] = 'active'


def _design_identity(session):
    return digest({key: session.get(key) for key in ('input', 'source_context', 'source_route_selection',
        'prompt_md', 'creative_design')})


def preserve_for_explanation(session):
    previous = (session.get('development') or {}).get('reply_only') or {}
    return {'identity': _design_identity(session), 'revision': session['revision'],
            'guidance_revision': previous.get('source_revision') if previous.get('current_revision') == session['revision'] else session['revision'],
            'values': {key: deepcopy(session[key]) for key in
                ('capacity', 'questions', 'ready_for_confirmation', 'design_needs_revision', 'naming') if key in session}}


def restore_explanation(session, snapshot, judged=None):
    origin = snapshot.get('development', {}).get('active', {}).get('read_only_origin')
    if (not origin or origin['identity'] != _design_identity(session)
            or (snapshot.get('turn_scope') != 'explanation' and
                (judged is None or judged['status'] != 'passed' or judged['receipt'].get('state_effect') != 'no_change'))):
        return False
    session.update(deepcopy(origin['values']))
    session['messages'][-1].update(scope='explanation_or_unadopted_comparison',
                                   questions=list(session.get('questions', [])))
    from . import workshop_capacity
    capacity = session.get('capacity')
    if capacity and capacity.get('status') == 'proposed':
        capacity.setdefault('basis_history', []).append({'basis_sha256': capacity.get('basis_sha256'),
            'source_revision': origin['revision'], 'review_sha256': digest(judged['receipt']) if judged else None,
            'reason': 'verified_reply_only' if judged else 'host_scoped_explanation'})
        capacity['basis_sha256'] = workshop_capacity.digest(workshop_capacity.source(session))
    session['development']['reply_only'] = {'source_revision': origin.get('guidance_revision', origin['revision']),
        'current_revision': session['revision'], 'identity': origin['identity']}
    return True


def _directory(snapshot):
    state = snapshot['development']['active']
    import re
    if not re.fullmatch(r'[a-f0-9]{32}', str(state.get('run_id', ''))):
        raise ValueError('创作运行身份无效，未覆盖已有结果。')
    return _managed(snapshot['_source_evidence_root'], 'development-runs', state['run_id'])


def journal_complete(client, system, user, snapshot):
    """Persist full ordinary discussion output through the common request journal."""
    from core.source_request_session import SourceRequestSession
    identity = digest({'system': system, 'user': user})
    path = _managed(_directory(snapshot), 'generation-requests', identity + '.json')
    if not path.exists():
        atomic_json(path, {'system': system, 'user': user, 'state': 'prepared'})
    return SourceRequestSession(path, identity, kind='evidence').execute(client)


def _read_generation(path):
    value = json.loads(path.read_text('utf-8'))
    if value.get('sha256') != digest({k: v for k, v in value.items() if k != 'sha256'}):
        raise ValueError('已保存创作回复校验失败，未重复生成。')
    return value


def _restore_result(saved):
    result = deepcopy(saved['result'])
    if saved.get('source_receipt') is not None:
        from .workshop_capacity import _SourceReceipt
        receipt = deepcopy(saved['source_receipt'])
        if not isinstance(receipt.get('texts'), list) or any(not isinstance(s, str) for s in receipt['texts']):
            raise ValueError('保存的来源引用范围无法验证，未重发请求。')
        receipt['texts'] = tuple(receipt['texts'])
        result['_capacity_source_receipt'] = _SourceReceipt(**receipt)
    return result


def _pending_generation(snapshot):
    pending = snapshot['development'].get('pending_candidate')
    if not pending or snapshot.get('turn_scope') == 'explanation' or snapshot.get('capacity_replan'):
        return None
    import re
    if not all(re.fullmatch(r'[a-f0-9]{64}', str(pending.get(key, ''))) for key in ('binding_sha256', 'candidate_sha256')):
        raise ValueError('待修订候选身份无效，保留当前讨论。')
    if not re.fullmatch(r'[a-f0-9]{32}', str(pending.get('generation_run_id', ''))):
        raise ValueError('待修订生成回执身份无效。')
    root = snapshot['_source_evidence_root']
    judged = _read_generation(_managed(root, 'creative-development', pending['binding_sha256'], 'result.json'))
    if judged['receipt']['candidate_sha256'] != pending['candidate_sha256'] or digest(judged['candidate']) != pending['candidate_sha256']:
        raise ValueError('待修订候选校验失败，未重新生成。')
    saved = _read_generation(_managed(root, 'development-runs', pending['generation_run_id'], 'generation.json'))
    result = _restore_result(saved)
    if judged['receipt']['scope'] == 'delivery':
        from .workshop_development_delivery import build_candidate, render_candidate
        from . import workshop_capacity
        from core.publication_taxonomy import bind_classification
        source = workshop_capacity.bind(saved['authority_snapshot'], bind_classification(result['prompt_md'], saved['authority_snapshot']['input']))
        result['prompt_md'] = render_candidate(source, build_candidate(source), judged['candidate'])
    else:
        result.update(deepcopy(judged['candidate']))
    return result, saved['route'], saved['authority_snapshot']


def complete(snapshot, skill, generator):
    """Return saved or newly completed generation and its original evidence basis."""
    active = snapshot['development']['active']
    validate(active['baseline'])
    path = _managed(_directory(snapshot), 'generation.json')
    if path.exists():
        saved = _read_generation(path)
        if saved['baseline_sha256'] != active['baseline']['sha256']:
            raise ValueError('创作候选属于其他依据版本，未覆盖。')
        return _restore_result(saved), saved['route'], saved['authority_snapshot']
    pending = _pending_generation(snapshot)
    if pending:
        result, route, authority = pending
    else:
        authority = {k: deepcopy(v) for k, v in snapshot.items() if not k.startswith('_')}
        editorial = snapshot['development'].get('editorial_candidate')
        if editorial:
            result = {'reply': '已按你选择的修订更新骨架，并完成创作依据核验。',
                'questions': [], 'prompt_md': editorial['prompt_md'], 'ready_for_confirmation': True}
            route = {'provider': 'local', 'model': 'author-selected-edits'}
        else:
            result, route = generator(snapshot, skill)
    # A paused worker must preserve a reply it has already obtained. Promotion
    # remains guarded by the caller's cancellation and operation checks.
    public = {k: deepcopy(v) for k, v in result.items() if not k.startswith('_')}
    receipt = result.get('_capacity_source_receipt')
    from .workshop_capacity import _SourceReceipt
    saved = {'baseline_sha256': active['baseline']['sha256'], 'result': public,
             'route': route, 'authority_snapshot': authority,
             'source_receipt': asdict(receipt) if isinstance(receipt, _SourceReceipt) else None}
    atomic_json(path, {**saved, 'sha256': digest(saved)})
    return result, route, authority


def _client(snapshot, operation):
    from core.provider_settings import resolve_text_route
    from core.llm_client import LLMClient
    from .workshop_diagnostics import make_diagnostic_callback
    route = resolve_text_route('workshop')
    timeout = float(snapshot.get('turn_timeout_seconds') or route.get('timeout_seconds') or 900)
    diagnostic = make_diagnostic_callback(snapshot, attempt=1, operation=operation,
        route={'provider': route['provider'], 'model': route['model'], 'timeout_seconds': timeout})
    return LLMClient(provider=route['provider'], model=route['model'] or None,
        base_url=route['base_url'] or None, api_key=route['api_key'] or None,
        max_tokens=route['max_tokens'], reasoning_effort=route.get('reasoning_effort') or None,
        timeout_seconds=timeout, cancellation_event=snapshot.get('_cancellation_event'),
        diagnostic_callback=diagnostic,
        **({'planning_input_bytes': route['planning_input_bytes']} if route.get('planning_input_bytes') else {}))


def _repair(client, candidate, issues, context):
    from .workshop_development_repair import repair
    return repair(client, candidate, issues, context)


def assess(snapshot, result):
    from core.creative_development import DevelopmentReview
    from core.source_route_context import enabled as has_source, read_packet
    baseline = snapshot['development']['active']['baseline']
    packet = read_packet(snapshot['_source_evidence_root'], snapshot['source_context']) if has_source(snapshot.get('source_context')) else None
    candidate = {k: deepcopy(v) for k, v in result.items() if not k.startswith('_')}
    delivery = result.get('prompt_md') is not None
    if delivery:
        from .workshop_development_delivery import build_candidate, render_candidate
        candidate = build_candidate(result['prompt_md'])
    client = _client(snapshot, 'creative-review')
    checker = DevelopmentReview(snapshot['_source_evidence_root'], baseline,
        cancelled=getattr(snapshot.get('_cancellation_event'), 'is_set', None))
    def repair(value, issues, context):
        revised = _repair(client, value, issues, context)
        if delivery:
            render_candidate(result['prompt_md'], candidate, revised)
        return revised
    from .workshop_delivery import serialized_inference
    gate = {**snapshot, '_turn_deadline': time.monotonic() + float(snapshot.get('turn_timeout_seconds') or 900)}
    with serialized_inference(gate, now=time.monotonic):
        judged = checker.assess(client, candidate, evidence_packet=packet, repair=repair)
    if judged['status'] == 'passed' and delivery:
        result['prompt_md'] = render_candidate(result['prompt_md'], candidate, judged['candidate'])
    elif judged['status'] == 'passed':
        repaired = deepcopy(judged['candidate'])
        # Parse all transport fields again after a scoped narrative correction.
        from .workshop import _parse_response
        parsed = _parse_response(json.dumps(repaired, ensure_ascii=False), snapshot.get('prompt_md', ''), discussion=True)
        result.update(parsed)
    return judged


def apply_assessment(session, judged):
    state = session['development']
    receipt = deepcopy(judged['receipt'])
    state['candidate_history'].append({'candidate_sha256': receipt['candidate_sha256'],
        'baseline_sha256': receipt['baseline_sha256'], 'status': judged['status'],
        'source_revision': receipt['session_revision']})
    state['status'] = 'active' if judged['status'] == 'passed' else judged['status']
    state['last_assessment'] = {'status': judged['status'], 'issues': deepcopy(judged['issues']), 'receipt': receipt}
    if judged['status'] == 'passed':
        state.pop('pending_candidate', None)
        state.pop('editorial_candidate', None)
        if receipt.get('state_effect') != 'no_change':
            state.pop('reply_only', None)
            state['accepted'] = {'candidate_sha256': receipt['candidate_sha256'],
                                 'baseline_sha256': receipt['baseline_sha256'], 'receipt': receipt}
        state['notice'] = '本轮扩展已按当前创作依据核对；具体场面仍可继续深化。'
    else:
        state['pending_candidate'] = {'binding_sha256': receipt['binding_sha256'],
            'candidate_sha256': receipt['candidate_sha256'], 'scope': receipt['scope'],
            'generation_run_id': state['active']['run_id']}
        state['notice'] = ('本轮候选已完整保存，尚未纳入当前设计；可继续修订或补充创作要求。'
                           if judged['status'] != 'needs_author' else '本轮发现影响核心走向的取舍，请查看具体问题；原设计与候选保留。')


def can_resume(session):
    return bool(enabled(session) and session.get('development', {}).get('active'))


def require_deliverable(session):
    """A retained old skeleton is not permission to launch a conflicting design."""
    from fastapi import HTTPException
    state = session.get('development') or {}
    if state.get('status') in {'reviewing', 'needs_revision', 'needs_author', 'needs_evidence'}:
        raise HTTPException(422, '本轮设计尚未通过协调，原骨架与候选保留；请先继续修订。')
    if state.get('delivery_verification'):
        from core.creative_execution import make_development_basis, text_hash
        try:
            make_development_basis(state['delivery_verification'], text_hash(session.get('prompt_md', '')))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None


def recovery_view(session, root):
    """Advertise saved-generation recovery only after read-only verification.

    A pending request journal does not prove generation completed. In particular,
    this projection never offers an unknown request as a reusable story reply.
    """
    if (root is None or not can_resume(session) or session.get('status') not in {'paused', 'error'}
            or session.get('restart_required') or session.get('active_operation') == 'titles'):
        return None
    try:
        active = session['development']['active']
        baseline = validate(active['baseline'])
        if (active['run_id'] != session.get('delivery_run_id')
                or active['source_revision'] != session.get('delivery_source_revision')
                or baseline.get('session_id') != session.get('id')):
            return None
        saved = _read_generation(_managed(_directory({**session, '_source_evidence_root': root}), 'generation.json'))
        if saved['baseline_sha256'] != baseline['sha256'] or saved['authority_snapshot']['id'] != session['id']:
            return None
        result = saved['result']
        if (not isinstance(result, dict) or not isinstance(result.get('reply'), str) or not result['reply'].strip()
                or not isinstance(result.get('questions'), list)
                or any(not isinstance(question, str) for question in result['questions'])
                or type(result.get('ready_for_confirmation')) is not bool
                or (result.get('prompt_md') is not None and not isinstance(result['prompt_md'], str))):
            return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return {'kind': 'saved_generation', 'can_resume': True, 'label': '继续已保存的设计核验',
            'detail': '本轮完整回复已保存，将继续核验与修订，不重新生成这轮故事。'}
