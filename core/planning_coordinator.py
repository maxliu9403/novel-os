"""Checkpointed coordination of mixed outline findings without regenerating a book."""
from __future__ import annotations

import copy
import hashlib

from core import planning_coordinated as contract
from core.planning_errors import PlanningInputTooLarge
from core.outline_planning import OutlinePlanningError, _json, content_hash
from core.planning_repair_context import build_plan_context, repair_contexts


def _groups(plan: dict, review: dict, chapter_count: int, evidence=None) -> list[dict]:
    """Merge corrections that write a chapter another correction must read."""
    groups = []
    proposals = {i['issue_index']: i for i in (evidence or {}).get('issues', [])}
    for item in plan['issues']:
        if not item['requested_chapters']:
            continue
        adjacent = {n for r in item['requested_chapters'] for n in (r - 1, r + 1)
                    if 1 <= n <= chapter_count}
        proposal = proposals.get(item['issue_index'], {})
        shared = {ref['path'] for ref in proposal.get('candidate_evidence', [])}
        if 'character_registration' in proposal.get('foundation_repairs', []):
            shared.add('/characters')
        groups.append({'fields': shared, 'requested_chapters': set(item['requested_chapters']),
                       'dependency_chapters': set(item['dependency_chapters']) | adjacent,
                       'indices': {item['issue_index']}})
    merged = True
    while merged:
        merged = False
        for left in range(len(groups)):
            for right in range(left + 1, len(groups)):
                a, b = groups[left], groups[right]
                if (a['requested_chapters'] & (b['requested_chapters'] | b['dependency_chapters'])
                        or b['requested_chapters'] & a['dependency_chapters']
                        or any(x == y or x.startswith(y+'/') or y.startswith(x+'/')
                               for x in a['fields'] for y in b['fields'])):
                    for key in a:
                        a[key] |= b[key]
                    groups.pop(right)
                    merged = True
                    break
            if merged:
                break
    return [{'requested_chapters': sorted(g['requested_chapters']),
             'dependency_chapters': sorted(g['dependency_chapters'] - g['requested_chapters']),
             'original_issue_indices': sorted(g['indices']),
             'issues': [copy.deepcopy(review['issues'][i]) for i in sorted(g['indices'])]}
            for g in groups]


def _limit(planner) -> int | None:
    from core.planning_capacity import input_limit
    return input_limit(planner.client, negotiate=True)


def _request_protocol(planner, name: str, context: dict, plan: dict) -> tuple[dict, str, bool, dict]:
    """Freeze new wire contracts without changing already completed requests."""
    from core import planning_repair_protocol as wire
    dependencies = {'context': content_hash(context), 'plan': content_hash(plan), 'protocol': wire.VERSION}

    def validate(value):
        if value != {'protocol': wire.VERSION}:
            raise ValueError('Unsupported frozen repair request protocol')
        return value

    marker = planner._load(name + '-protocol', dependencies, validate, strict_dependencies=True)
    legacy = marker is None and (
        (planner.cache / f'{name}.json').exists()
        or (planner.cache / f'{name}-response.json').exists()
        or (planner.cache / f'{name}-format-correction-response.json').exists()
        or any((planner.cache / 'raw').glob(f'{name}.*'))
    )
    if legacy:
        return context, contract.INSTRUCTION, False, dependencies
    return wire.build(context, planner.source), wire.INSTRUCTION, True, dependencies


def _apply_groups(planner, candidate, review, plan, groups, prefix, extra, limit, *, legacy=False, authority=None):
    from core.pipeline_runtime import runtime_stage
    global_design = {k: copy.deepcopy(v) for k, v in candidate.items() if k != 'chapters'}
    from core import planning_evidence_plan as evidence, planning_group_fields as fields
    requests, capacity_holds = [], []
    for index, group in enumerate(groups, 1):
        ordinal = index if legacy else min(group['original_issue_indices']) + 1
        name = f'{prefix}-group-{ordinal:03d}'
        # Preserve the exact old wire contract for any group that had already started.
        # Unsent groups in the same legacy run adopt the new permissions independently.
        frozen_group = legacy and (any(planner.cache.glob(name + '*.json')) or
                                   any((planner.cache / 'raw').glob(name + '*')))
        if legacy and not frozen_group:
            name = f'{evidence.VERSION}-continued-group-{index:03d}'
        scoped = [authority[i] for i in group['original_issue_indices'] if authority and i in authority]
        scoped_plan = plan if frozen_group else {'issues': [
            i for i in plan['issues'] if i['issue_index'] in group['original_issue_indices']]}
        extra_context = {**extra, 'coordinated_issue_plan': scoped_plan}
        if frozen_group:
            extra_context.pop('binding_author_choices', None)
        if scoped and not frozen_group:
            extra_context['source_authority'] = scoped
        context, = repair_contexts([group], candidate, global_design, review,
            source=planner.source, brief=planner.brief, locked=planner.locked,
            fits=lambda ctx: True,  # The fully serialized per-group wire request is checked below.
            extra_context=extra_context)
        wire_context, instruction, use_wire, deps = _request_protocol(planner, name, context, scoped_plan)
        if not frozen_group:
            instruction += fields.INSTRUCTION
        has_work = (any(planner.cache.glob(name + '*response.json')) or (planner.cache / f'{name}.json').is_file()
                    or any((planner.cache / 'raw').glob(name + '*.response.txt')))
        if not has_work and not planner._prompt_fits(instruction + '\n\n' + _json(wire_context), 'review', limit):
            capacity_holds.append({'stage': name, 'issue_indices': group['original_issue_indices'],
                                   'chapters': group['requested_chapters']})
            continue
        requests.append((name, frozen_group, context, wire_context, instruction, use_wire, deps, scoped_plan))
    assembled = copy.deepcopy(candidate)
    map_positions = {item['number']: index for index, item in enumerate(candidate['chapter_map'])}
    resolutions = {}
    reference_audits = {}
    questions = {}
    writable = {i['issue_index']: set(i['requested_chapters']) for i in plan['issues']}
    for index, request in enumerate(requests, 1):
        group_name, frozen_group, context, request_context, instruction, use_wire, protocol_dependencies, scoped_plan = request
        from core import planning_reference_recovery as references
        from core import planning_repair_protocol as wire
        numbers = context['requested_chapters']
        scoped_review = context['review']
        original_indices = context['repair_scope']['original_issue_indices']
        if use_wire:
            marker_name = group_name + '-protocol'
            if not (planner.cache / f'{marker_name}.json').exists():
                planner._save(marker_name, protocol_dependencies, {'protocol': wire.VERSION})
        group_dependencies = {'plan': content_hash(scoped_plan), 'context': content_hash(request_context),
                              'policy': content_hash(instruction)}
        metadata_dependencies = {'candidate': content_hash(candidate),
            'source': hashlib.sha256(planner.source.encode()).hexdigest(),
            'context': content_hash(context), 'policy': references.VERSION}
        saved_metadata = planner._load(group_name + '-metadata-v1', metadata_dependencies,
                                       lambda value: value, strict_dependencies=True)
        if saved_metadata is not None:
            group_dependencies['reference_metadata'] = content_hash(saved_metadata)
        recovered_audit = None

        def validate(value):
            fixed = contract.apply(candidate, scoped_review, planner.source, value, requested_chapters=numbers)
            if not frozen_group:
                for outcome in value['issues']:
                    if outcome['status'] == 'corrected' and not set(outcome['chapter_numbers']) <= writable[original_indices[outcome['issue_index']]]:
                        raise ValueError('Repair writes a chapter outside this individual issue’s permission')
            changed = [c for c in fixed['chapters'] if c != candidate['chapters'][c['number'] - 1]]
            if changed:
                planner._batch({'chapters': copy.deepcopy(changed)}, [c['number'] for c in changed], global_design)
            planner._global({k: copy.deepcopy(v) for k, v in fixed.items() if k != 'chapters'})
            return value

        def validate_response(value):
            if not frozen_group:
                value = fields.expand(value, candidate, numbers)
            if use_wire:
                return validate(wire.decode(context, planner.source, value))
            return validate(value)

        def recover_completed(value):
            nonlocal recovered_audit
            rebound = references.recover(context, planner.source, value, candidate=candidate)
            if rebound is None:
                return None
            result, audit_record = rebound
            validate(result)
            recovered_audit = audit_record
            return result

        planner._progress('semantic_review', 'running',
            f'协调修订 {index}/{len(requests)} 组 · 第 {"、".join(map(str, numbers))} 章；其他章节保持原文')
        patch = planner._load(group_name, group_dependencies, validate, strict_dependencies=True)
        if patch is None:
            with runtime_stage('planning.coordinated_repair', chapter=numbers[0]):
                try:
                    patch = planner._call(group_name, instruction, request_context, validate_response,
                        input_limit_bytes=limit, strict_dependencies=True,
                        **({'recover_completed': recover_completed} if not use_wire else {}))
                except PlanningInputTooLarge as exc:
                    capacity_holds.append({'stage': exc.stage_name, 'issue_indices': original_indices, 'chapters': numbers})
                    continue
                if recovered_audit is not None:
                    if saved_metadata is not None and saved_metadata != recovered_audit:
                        raise OutlinePlanningError('Saved reference binding differs from its completed response')
                    if saved_metadata is None:
                        planner._save(group_name + '-metadata-v1', metadata_dependencies, recovered_audit)
                    saved_metadata = recovered_audit
                    # A recovered result must never become indistinguishable
                    # from a model-selected citation after a restart.
                    group_dependencies['reference_metadata'] = content_hash(saved_metadata)
                planner._save(group_name, group_dependencies, patch)
        if saved_metadata is not None:
            if saved_metadata.get('output_patch_sha256') != content_hash(patch):
                raise OutlinePlanningError('Reference binding does not match the saved repair result')
            for original_index in original_indices:
                reference_audits[original_index] = copy.deepcopy(saved_metadata)
        if patch['status'] == 'requires_author':
            # A patch writer cannot escalate an interpretation into an author question.
            # Schedule independent source adjudication after completing the disjoint groups.
            for item in patch['issues']:
                if item['status'] == 'requires_author':
                    questions[original_indices[item['issue_index']]] = copy.deepcopy(item)
        # Every group is bound to the original candidate. _groups ensures no
        # other group edits its requested/read-only dependency chapters.
        fixed = contract.apply(candidate, scoped_review, planner.source, patch, requested_chapters=numbers)
        for n in numbers:
            assembled['chapters'][n - 1] = fixed['chapters'][n - 1]
            position = map_positions[n]
            assembled['chapter_map'][position] = fixed['chapter_map'][position]
        for item in patch['issues']:
            resolutions[original_indices[item['issue_index']]] = copy.deepcopy(item)

    return assembled, resolutions, reference_audits, questions, capacity_holds


def _provenance_plan(planner, candidate, review, context, prefix, limit, *, legacy=None, instruction=None):
    from core import planning_evidence_plan as evidence
    from core.pipeline_runtime import runtime_stage

    instruction = instruction or evidence.PLAN_INSTRUCTION
    dependencies = {'candidate': content_hash(candidate), 'review': content_hash(review),
                    'context': content_hash(context), 'policy': content_hash(instruction)}
    if legacy is not None:
        dependencies['legacy_replies'] = content_hash(legacy)
    validate = lambda value: evidence.validate_plan(candidate, review, planner.source, value)
    name = prefix + '-plan'
    saved = planner._load(name, dependencies, validate, strict_dependencies=True)
    if saved is not None:
        return saved
    if legacy is not None:
        try:
            saved = evidence.migrate_legacy(candidate, review, planner.source, legacy['latest'],
                                            prior=legacy.get('prior'), historical=True)
        except ValueError:
            # Only the receipt reader may supply this input. A paid but malformed
            # legacy reply is provenance, not permission to replay that obsolete policy.
            if _old_group_work(planner):
                raise
            context = {**context, 'historical_plan': {
                'verified_reply_hash': content_hash(legacy),
                'role': 'Archived model response failed the current evidence contract. '
                        'Use the complete saved source, review and global candidate to form a new plan.'}}
    if saved is None:
        with runtime_stage('planning.coordinated_repair_plan'):
            saved = planner._call(name, instruction, context, validate, strict_dependencies=True,
                                  recover_archived=True, input_limit_bytes=limit)
    planner._save(name, dependencies, saved)
    return saved


def _verified_execution(planner, plan, prefix, limit, choices):
    from core import planning_evidence_plan as evidence
    from core.pipeline_runtime import runtime_stage
    from core.planning_chapter_repair import _checkpoint

    verdicts, capacity_holds = {}, {}
    for item in plan['issues']:
        if item['classification'] != 'source_conflict':
            continue
        i = item['issue_index']
        context = evidence.conflict_context(planner.source, item)
        instruction = evidence.CONFLICT_INSTRUCTION
        if choices:
            context['binding_author_choices'] = choices
            instruction += ('\nEach binding_author_choice settles the corresponding two passages. Check the '
                            'candidate against the author’s chosen account. Do not ask an answered question again.')
        name = f'{prefix}-conflict-{i + 1:03d}'
        with runtime_stage('planning.coordinated_conflict_verification'):
            planner._progress('semantic_review', 'running', f'核对第 {i + 1} 项争议的作者原文和候选假设')
            try:
                verdicts[i] = _checkpoint(planner, name, instruction, context,
                    {'item': content_hash(item)}, lambda v: evidence.validate_verdict(context, v), limit)
            except PlanningInputTooLarge as exc:
                capacity_holds[i] = {'stage': exc.stage_name, 'issue_indices': [i],
                                     'chapters': item['affected_chapters']}
    available = {'issues': [i for i in plan['issues'] if i['issue_index'] not in capacity_holds]}
    execution = evidence.execution_plan(available, verdicts)
    for item in plan['issues']:
        i = item['issue_index']
        if i in capacity_holds:
            # This is an internal wait for evidence. No decision is offered to the author.
            execution['issues'].append({'issue_index': i, 'classification': 'pending_evidence',
                'requested_chapters': [], 'dependency_chapters': item['dependency_chapters'],
                'source_quotes': item['source_quotes'], 'reason': 'Complete source adjudication is pending.'})
    execution['issues'].sort(key=lambda i: i['issue_index'])
    return execution, verdicts, list(capacity_holds.values())



def independent_groups(plan, evidence_plan, review, count):
    """Freeze the closure of a pending choice, including writes made by dependent repairs."""
    proposals = {i['issue_index']: i for i in evidence_plan['issues']}
    conflicts = [i for i in plan['issues'] if i['classification'] in {'source_conflict','pending_evidence'}]
    affected = {n for i in conflicts for n in proposals[i['issue_index']]['affected_chapters']}
    reads = {n for i in conflicts for n in i['dependency_chapters']}
    held_paths = {ref['path'] for i in conflicts for ref in proposals[i['issue_index']]['candidate_evidence']}
    def shared_field(path):
        return any(path == held or held.startswith(path + '/') or path.startswith(held + '/')
                   for held in held_paths)

    groups = _groups(plan, review, count, evidence_plan)
    deferred = {i['issue_index'] for i in conflicts}
    pending = list(groups)
    while True:
        found = []
        for group in pending:
            writable = set(group['requested_chapters'])
            inputs = writable | set(group['dependency_chapters'])
            for i in group['original_issue_indices']:
                inputs.update(proposals[i]['affected_chapters'])
            shared = any(shared_field(ref['path']) for i in group['original_issue_indices']
                         for ref in proposals[i]['candidate_evidence'])
            if inputs & affected or writable & reads or shared:
                found.append(group)
        if not found:
            return pending, deferred
        for group in found:
            pending.remove(group)
            affected.update(group['requested_chapters'])
            reads.update(group['dependency_chapters'])
            deferred.update(group['original_issue_indices'])
            held_paths.update(ref['path'] for i in group['original_issue_indices']
                              for ref in proposals[i]['candidate_evidence'])


def _resolve_choices(planner, candidate, review, context_extra, prefix, evidence_plan, execution,
                     conflicts, answers, limit):
    """A choice settles a premise. A separate plan still specifies what may be changed."""
    from core import planning_evidence_plan as evidence

    proposals = {i['issue_index']: i for i in evidence_plan['issues']}
    edits = {i['issue_index']: i for i in execution['issues']}
    for conflict in conflicts:
        i = conflict['issue_index']
        decision = answers[i]
        original = proposals[i]
        finding = review['issues'][i]
        scoped = {**review, 'issues': [finding]}
        ctx = build_plan_context(candidate, scoped, planner.source, planner.brief, planner.locked, context_extra)
        ctx.update(binding_author_decision=decision, original_finding=conflict,
                   decision_affected_chapters=original['affected_chapters'])
        instruction = evidence.PLAN_INSTRUCTION + (
            '\nThe supplied binding_author_decision is the author’s choice between the quoted source '
            'statements for this issue. Accept that decision as binding when planning the correction. '
            'Specify writable chapters within decision_affected_chapters or return interpretation '
            'if the existing design already honors the decision. Do not ask the same question again.')
        selected = _provenance_plan(planner, candidate, scoped, ctx, f'{prefix}-choice-{i + 1:03d}',
                                    limit, instruction=instruction)['issues'][0]
        if selected['classification'] == 'source_conflict' or not set(selected['writable_chapters']) <= set(original['affected_chapters']):
            raise ValueError('The choice plan contradicts the answered question or exceeds its chapter scope')
        execution_item = evidence.execution_plan({'issues': [selected]}, {})['issues'][0]
        edits[i] = {**execution_item, 'issue_index': i}
        proposals[i] = {**selected, 'issue_index': i}
    return ({'issues': [proposals[i['issue_index']] for i in evidence_plan['issues']]},
            {'issues': [edits[i['issue_index']] for i in execution['issues']]})


def _record_candidate(planner, name, deps, candidate, audit):
    value = {'candidate': candidate, 'verification': audit}

    def validate(saved):
        if saved != value:
            raise ValueError('Saved repair candidate differs from the verified group results')
        return saved

    if planner._load(name, deps, validate, strict_dependencies=True) is None:
        planner._save(name, deps, value)


def _old_group_work(planner):
    return any(planner.cache.glob(contract.VERSION + '-group-*')) or any(
        (planner.cache / 'raw').glob(contract.VERSION + '-group-*')) or (
        planner.cache / (contract.VERSION + '-candidate.json')).exists()


def _authorized_items(plan, execution, decisions):
    permissions = {i['issue_index']: i for i in execution['issues']}
    items = []
    for proposal in plan['issues']:
        i = proposal['issue_index']
        item = permissions[i]
        items.append({**proposal, 'classification': item['classification'], 'reason': item['reason'],
                      'writable_chapters': item['requested_chapters'], 'source_quotes': item['source_quotes'],
                      **({'binding_author_decision': decisions[i]} if i in decisions else {})})
    return {'issues': items}


def _reconcile_derived(planner, candidate, review, plan, execution, blocked, decisions):
    from core.planning_assumptions import reconcile_assumptions
    from core.planning_foundation_links import repair as reconcile_links

    resolved = _authorized_items(plan, execution, decisions)
    derived, revisions = reconcile_assumptions(planner, candidate, review, resolved, blocked_indices=blocked)
    derived, links = reconcile_links(planner, derived, review, resolved, blocked_indices=blocked)
    return derived, [*revisions, *links]


def _residual_group(planner, candidate, review, plan, execution, group, decisions, deferred, choices):
    from core import planning_foundation_links as registry
    from core import planning_residual_edits as narrative
    from core import planning_residual_promises as promises

    members = set(group['original_issue_indices'])
    authorized = _authorized_items(plan, execution, decisions)
    permissions = {i['issue_index']: i for i in execution['issues']}
    read_scope = set(group['requested_chapters']) | set(group['dependency_chapters'])
    scoped = {'issues': [{**i, 'dependency_chapters': sorted(
                              (set(permissions[i['issue_index']]['dependency_chapters']) | read_scope)
                              - set(i['writable_chapters'])),
                          **({'prior_author_choices': choices} if choices else {})}
                         for i in authorized['issues'] if i['issue_index'] in members | deferred]}
    cast = {'issues': [{**i, 'foundation_repairs': ['character_registration']} for i in scoped['issues']
                       if i['issue_index'] in members and 'character_registration' in i.get('foundation_repairs', ())]}
    derived, registered = registry.repair(planner, candidate, review, cast, blocked_indices=deferred)
    derived, linked = promises.repair(planner, derived, review, scoped, blocked_indices=deferred)
    derived, edits = narrative.repair(planner, derived, review, scoped, blocked_indices=deferred)
    foundation = planner._global({k: copy.deepcopy(v) for k, v in derived.items() if k != 'chapters'})
    changed = [ch for ch, old in zip(derived['chapters'], candidate['chapters'], strict=True) if ch != old]
    if changed:
        planner._batch({'chapters': copy.deepcopy(changed)}, [ch['number'] for ch in changed], foundation)
    return derived, [*registered, *linked, *edits]


def _phase(planner, candidate, review, execution, evidence, groups, prefix, extra, limit, authority,
           decisions, *, legacy=False, residual=False, deferred=frozenset()):
    from core.planning_group_assembly import merge

    if legacy:
        return (*_apply_groups(planner, candidate, review, execution, groups, prefix, extra, limit,
                               legacy=True, authority=authority), [])
    assembled = copy.deepcopy(candidate)
    resolutions, bindings, late, holds, revisions = {}, {}, {}, [], []
    for group in groups:
        indices = group['original_issue_indices']
        scoped = {'issues': [i for i in evidence['issues'] if i['issue_index'] in indices]}
        try:
            if residual:
                # Disjoint chapter groups can still correct different clauses of
                # one global constraint. Read the latest verified derivative so
                # subsequent edits preserve the previous group's exact changes.
                group_baseline = copy.deepcopy(assembled)
                proposal, local_revisions = _residual_group(planner, group_baseline, review, evidence, execution,
                                                           group, decisions, deferred,
                                                           extra.get('binding_author_choices', []))
                merge(group_baseline, assembled, proposal, group['requested_chapters'], residual=True)
                revisions.extend(local_revisions)
                for i in indices:
                    resolutions[i] = {'status': 'recheck', 'reason': 'Independently verify the resulting fields and references.'}
                continue
            derived, local_revisions = _reconcile_derived(planner, candidate, review, scoped, execution,
                                                          set(), decisions)
        except PlanningInputTooLarge as exc:
            holds.append({'stage': exc.stage_name, 'issue_indices': indices, 'chapters': group['requested_chapters']})
            continue
        proposal, fixed, citations, disputed, withheld = _apply_groups(
            planner, derived, review, execution, [group], prefix, extra, limit, authority=authority)
        holds.extend(withheld)
        if withheld:
            continue
        merge(candidate, assembled, proposal, group['requested_chapters'])
        revisions.extend(local_revisions)
        resolutions.update(fixed)
        bindings.update(citations)
        late.update(disputed)
    return assembled, resolutions, bindings, late, holds, revisions


def _authority_inputs(planner, candidate, review, prefix):
    from core.planning_author_decisions import authorized_choices

    live = authorized_choices(planner)
    deps = {'candidate': content_hash(candidate), 'review': content_hash(review),
            'source': content_hash(planner.source)}
    def verify(saved):
        if (not isinstance(saved, dict) or set(saved) != {'choices'} or not isinstance(saved['choices'], list)
                or any(choice not in live for choice in saved['choices'])):
            raise ValueError('A frozen author choice no longer has its validated source and answer')
        return saved
    # Resuming the first phase after an answer must replay its original inputs.
    # The choice phase and subsequent follow-up snapshot the newly authorized choice.
    name = prefix + '-author-inputs'
    saved = planner._load(name, deps, verify, strict_dependencies=True)
    if saved is None:
        saved = {'choices': live}
        planner._save(name, deps, saved)
    return saved['choices']


def _new_interpretation_evidence(planner, candidate, audit, previous_review):
    """Allow a source-based re-reading once when its actual evidence has changed."""
    if not previous_review:
        return False
    from core.planning_terminal import _record

    prefix = previous_review.removeprefix('review-').split('-choices-')[0]
    path = planner.cache / (prefix + '-candidate.json')
    if not path.is_file():
        return False
    previous = _record(planner, prefix + '-candidate')['payload']
    if previous.get('candidate') != candidate:
        raise ValueError('Interpretation evidence must descend from the reviewed candidate')

    def evidence_key(item):
        verdict = item.get('source_adjudication', {}).get('verdict')
        return (item['classification'], tuple(sorted(item['source_quotes'])), verdict)

    old = {evidence_key(item) for item in previous['verification']['issues']}
    return any(item['classification'] == 'interpretation' and evidence_key(item) not in old
               for item in audit['issues'])


def _repair_round(planner, candidate, review, *, prefix, limit, extra, initial=True, late_plan=None, residual=False, previous_review=None):
    from core import planning_evidence_plan as evidence
    from core.planning_author_decisions import defer_for_author, pending_decisions
    from core.planning_recovery import legacy_evidence_plan

    baseline = candidate
    choices = _authority_inputs(planner, candidate, review, prefix)
    if choices:
        extra = {**extra, 'binding_author_choices': choices}
    ctx = build_plan_context(candidate, review, planner.source, planner.brief, planner.locked, extra)
    legacy = legacy_evidence_plan(planner, candidate, review) if initial else None
    instruction = None
    if residual:
        instruction = evidence.PLAN_INSTRUCTION.replace(
            '["payoff_binding"] for broken payoff references to existing setups',
            '["payoff_binding"] for a missing or mislocated source-required setup, reactivation, fulfillment, '
            'or dangling setup_id/promise_id (include each chapter whose registration or reference must change)'
        ).replace('Never use either tool to alter existing identities or setups.',
                  'Preserve stable identities. Correct the origin of a generated setup only with adopted evidence.')
    plan = (evidence.validate_plan(candidate, review, planner.source, late_plan) if late_plan is not None
            else _provenance_plan(planner, candidate, review, ctx, prefix, limit, legacy=legacy, instruction=instruction))
    execution, verdicts, capacity_holds = _verified_execution(planner, plan, prefix, limit, choices)
    proposals = {item['issue_index']: item for item in plan['issues']}
    conflicts = [{'issue_index': i, 'affected_chapters': proposals[i]['affected_chapters'],
                  'author_quotes': result['source_quotes'], 'reason': result['reason']}
                 for i, result in verdicts.items() if result['verdict'] == 'confirmed_conflict']
    historical_groups = initial and _old_group_work(planner)
    if historical_groups:
        if not legacy or conflicts:
            raise ValueError('Historical group receipts require their original authorized plan')
        old_plan = contract.validate_plan(candidate, review, planner.source, legacy['latest'])
        # Reuse only the original plan, policy, serialized context and candidate. Assembling
        # under a different new plan would silently invalidate paid work and frozen receipts.
        if any(i['classification'] == 'source_conflict' for i in old_plan['issues']):
            raise ValueError('Historical group receipts were created from an unauthorized plan')
        execution = old_plan
    if historical_groups:
        independent, deferred = _groups(execution, review, planner.chapter_count), set()
    else:
        independent, deferred = independent_groups(execution, plan, review, planner.chapter_count)
    authority = {item['issue_index']: {
        'finding': review['issues'][item['issue_index']]['message'],
        'generated_candidate_evidence': item['candidate_evidence'],
        **({'independent_verdict': verdicts[item['issue_index']]} if item['issue_index'] in verdicts else {}),
    } for item in plan['issues']}
    group_prefix = contract.VERSION if historical_groups else prefix
    assembled, resolutions, bindings, late, group_holds, revisions = _phase(
        planner, candidate, review, execution, plan, independent, group_prefix, extra, limit, authority,
        {}, legacy=historical_groups, residual=residual, deferred=deferred)
    capacity_holds.extend(group_holds)
    decisions = {}
    if conflicts:
        questions, decisions = pending_decisions(planner, conflicts)
        if not all(c['issue_index'] in decisions for c in conflicts):
            awaiting = deferred | {i for held in capacity_holds for i in held['issue_indices']}
            partial_audit = {'completed_issue_indices': sorted(resolutions),
                             'deferred_issue_indices': sorted(awaiting), 'question': questions,
                             'capacity_holds': capacity_holds}
            _record_candidate(planner, prefix + '-independent-' + content_hash(partial_audit)[:16],
                              {'evidence': content_hash(plan), 'audit': content_hash(partial_audit)},
                              assembled, partial_audit)
            planner.progress['completed_issue_indices'] = sorted(resolutions)
            planner.progress['deferred_issue_indices'] = sorted(awaiting)
            defer_for_author(planner, questions)
        plan, execution = _resolve_choices(planner, assembled, review, extra, prefix, plan, execution,
                                           conflicts, decisions, limit)
        _, still_held = independent_groups(execution, plan, review, planner.chapter_count)
        can_resume = deferred - still_held
        groups = _groups({'issues': [i for i in execution['issues'] if i['issue_index'] in can_resume]},
                         review, planner.chapter_count, plan)
        assembled, new_resolutions, new_bindings, new_late, new_holds, followup_revisions = _phase(
            planner, assembled, review, execution, plan, groups, prefix + '-decided',
            {**extra, 'binding_author_choices': [*choices, *decisions.values()]}, limit, authority, decisions,
            residual=residual, deferred=still_held)
        revisions.extend(followup_revisions)
        resolutions.update(new_resolutions)
        bindings.update(new_bindings)
        late.update(new_late)
        capacity_holds.extend(new_holds)
    if capacity_holds:
        progress = {'completed_issue_indices': sorted(resolutions), 'capacity_holds': capacity_holds}
        _record_candidate(planner, prefix + '-capacity-' + content_hash(progress)[:16],
                          {'plan': content_hash(execution), 'progress': content_hash(progress)}, assembled, progress)
        reason = '独立修订结果已保存。当前连接无法完整读取部分修订所需的依据，请提高连接容量后继续。'
        planner.progress.update(progress)
        planner.progress['recovery'] = {'retryable': True, 'action': 'change_planning_capacity', 'reason': reason}
        planner.progress['next_action'] = reason
        raise OutlinePlanningError(reason)
    audit = {'schema': group_prefix, 'baseline_candidate_sha256': content_hash(baseline), 'issues': []}
    for item in execution['issues']:
        i = item['issue_index']
        record = {**copy.deepcopy(item), 'original_issue': copy.deepcopy(review['issues'][i]),
                  'resolution': resolutions.get(i, {'status': 'recheck', 'reason': item['reason'],
                                                    'source_quotes': item['source_quotes']})}
        if i in bindings:
            record['reference_binding'] = bindings[i]
        if not historical_groups:
            record['evidence'] = next(proposal for proposal in plan['issues'] if proposal['issue_index'] == i)
            if i in verdicts:
                record['source_adjudication'] = verdicts[i]
            if i in decisions:
                record['binding_author_decision'] = decisions[i]
            if choices:
                record['prior_author_choices'] = choices
            changes = [v for v in revisions if i == v.get('issue_index') or i in v.get('issue_indices', ())]
            if changes:
                record['derived_revisions'] = changes
        audit['issues'].append(record)
    if late and late_plan is None:
        _record_candidate(planner, group_prefix + '-pre-adjudication',
                          {'plan': content_hash(execution), 'audit': content_hash(audit)}, assembled, audit)
        # Reconsider the entire affected group: the proposal was atomic and applied no edits.
        # The writer's explanation supplies a hypothesis, never a confirmed contradiction.
        reopened_groups = [g for g in _groups(execution, review, planner.chapter_count, plan)
                           if set(g['original_issue_indices']) & late.keys()]
        indices = sorted({i for g in reopened_groups for i in g['original_issue_indices']})
        data = []
        strings = evidence._candidate_strings(assembled)
        for new_i, old_i in enumerate(indices):
            item = copy.deepcopy(next(i for i in plan['issues'] if i['issue_index'] == old_i))
            item['issue_index'] = new_i
            # Paths refer to the original candidate. Include only references that are still
            # exact after any completed assumption repair.
            item['candidate_evidence'] = [ref for ref in item['candidate_evidence']
                                         if ref['quote'] in strings.get(ref['path'], '')]
            if old_i in late:
                item.update(classification='source_conflict', writable_chapters=[],
                            reason=late[old_i]['reason'], source_quotes=late[old_i]['source_quotes'])
            data.append(item)
        scoped = {**review, 'issues': [review['issues'][i] for i in indices]}
        try:
            assembled, _, resolved_audit = _repair_round(planner, assembled, scoped,
                                   prefix=prefix + '-disputed', limit=limit, extra=extra, initial=False,
                                   late_plan={'issues': data})
        except OutlinePlanningError:
            if (planner.progress.get('recovery') or {}).get('action') == 'choose_author_source':
                finished = (set(resolutions) - set(indices)) | {
                    indices[i] for i in planner.progress.get('completed_issue_indices', [])}
                planner.progress['completed_issue_indices'] = sorted(finished)
                planner.progress['deferred_issue_indices'] = sorted(
                    indices[i] for i in planner.progress.get('deferred_issue_indices', []))
            raise
        records = {i['issue_index']: i for i in audit['issues']}
        for entry in resolved_audit['issues']:
            original = records[indices[entry['issue_index']]]
            original['group_reassessment'] = entry
            original['resolution'] = entry['resolution']
    _record_candidate(planner, group_prefix + '-candidate', {'plan': content_hash(execution),
                      'audit': content_hash(audit)}, assembled, audit)
    if late_plan is not None:
        # The parent verifies the complete candidate with these resolutions and all
        # findings, including groups that had already been repaired.
        return assembled, None, audit
    if (residual and assembled == baseline and not (choices or decisions)
            and not _new_interpretation_evidence(planner, baseline, audit, previous_review)):
        # The same strict review already rejected these exact bytes. Rerolling it
        # without new evidence could silently pass an unchanged contradiction.
        return assembled, review, audit
    planner._progress('semantic_review', 'running', '修订结果已保存，逐项复核修订内容和作者原文')
    checked = planner._semantic_review(assembled, 'review-' + group_prefix,
            repair_record=audit, reuse_from='review-initial' if initial else previous_review, baseline_candidate=baseline)
    return assembled, checked, audit


def repair(planner, candidate: dict, review: dict, *, prior_specialized=False, authority_context=None) -> tuple[dict, dict]:
    """Repair the saved evidence and candidate without changing the original checkpoints."""
    from core import planning_evidence_plan as evidence

    old_attempts = ('repair', 'global-reference-repair-v1', 'global-source-restoration-v2')
    if not (prior_specialized or authority_context) and any(list(planner.cache.glob(f'{name}*.json')) or
           list((planner.cache / 'raw').glob(f'{name}.*')) for name in old_attempts):
        planner._needs_attention('已有其他定向修订记录，请从对应的保存结果继续。')
        raise OutlinePlanningError('Existing repair attempt cannot be reset by coordinated recovery')
    extra = {'target_words': planner.target_words,
             **({'confirmed_story_capacity': planner.capacity} if planner.capacity else {})}
    prefix = evidence.VERSION
    fresh_scoped = prior_specialized and not any(planner.cache.rglob(contract.VERSION + '-*'))
    if fresh_scoped:
        prefix += '-scoped-' + content_hash([candidate, review])[:16]
    if authority_context:
        from core.planning_author_decisions import authorized_choices
        if any(choice not in authorized_choices(planner) for choice in authority_context):
            raise OutlinePlanningError('Author repair requires verified choices from this run and source')
        extra['binding_author_choices'] = authority_context
        prefix += '-author-' + content_hash([authority_context, candidate, review])[:16]
    limit = _limit(planner)
    planner._progress('semantic_review', 'running', '核对作者原文，定位需要修订的章节及只读依赖')
    try:
        from core.planning_recovery import verified_terminal
        from core.planning_residual_loop import converge

        saved = verified_terminal(planner, candidate, review, prefix, include_label=True)
        review_name = None
        if saved is not None:
            assembled, checked, review_name = saved
        else:
            assembled, checked, audit = _repair_round(planner, candidate, review, prefix=prefix,
                                  limit=limit, extra=extra, initial=not (authority_context or fresh_scoped))
            review_name = 'review-' + audit['schema']
            # Resume any previously started v2 follow-up under the original wire contract.
            followup = prefix + '-followup'
            if checked['status'] != 'passed' and any(planner.cache.rglob(followup + '-*')):
                assembled, checked, audit = _repair_round(planner, assembled, checked, prefix=followup,
                                                          limit=limit, extra=extra, initial=False)
                review_name = 'review-' + audit['schema']

        def advance(current, outstanding, stage, reuse_from):
            fixed, result, _ = _repair_round(planner, current, outstanding, prefix=stage, limit=limit,
                                             extra=extra, initial=False, residual=True, previous_review=reuse_from)
            return fixed, result

        if checked['status'] == 'passed':
            return assembled, checked
        entry = 'residual-entry-' + content_hash([prefix, assembled, checked])[:24]
        bound_choices = _authority_inputs(planner, assembled, checked, entry)
        return converge(planner, assembled, checked, advance, authority=bound_choices,
                        previous_review=review_name)
    except PlanningInputTooLarge as exc:
        reason = '完整修订依据超出当前连接容量，已保留全部原文、候选和检查点。' + str(exc)
        planner.progress['recovery'] = {'retryable': True, 'action': 'change_planning_capacity', 'reason': reason}
        planner.progress['next_action'] = reason
        raise
    except (ValueError, OutlinePlanningError) as exc:
        if (planner.progress.get('recovery') or {}).get('action') not in {'choose_author_source', 'change_planning_capacity', 'inspect_residual_closure'}:
            planner._needs_attention('已保留原大纲和本轮修订记录。' + str(exc))
        if isinstance(exc, OutlinePlanningError):
            raise
        raise OutlinePlanningError(str(exc)) from exc
