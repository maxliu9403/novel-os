"""Checkpointed coordination of mixed outline findings without regenerating a book."""
from __future__ import annotations

import copy
import hashlib

from core import planning_coordinated as contract
from core.outline_planning import OutlinePlanningError, _json, content_hash
from core.planning_repair_context import build_plan_context, repair_contexts


def _groups(plan: dict, review: dict, chapter_count: int) -> list[dict]:
    """Merge corrections that write a chapter another correction must read."""
    groups = []
    for item in plan['issues']:
        if not item['requested_chapters']:
            continue
        adjacent = {n for r in item['requested_chapters'] for n in (r - 1, r + 1)
                    if 1 <= n <= chapter_count}
        groups.append({'requested_chapters': set(item['requested_chapters']),
                       'dependency_chapters': set(item['dependency_chapters']) | adjacent,
                       'indices': {item['issue_index']}})
    merged = True
    while merged:
        merged = False
        for left in range(len(groups)):
            for right in range(left + 1, len(groups)):
                a, b = groups[left], groups[right]
                if (a['requested_chapters'] & (b['requested_chapters'] | b['dependency_chapters'])
                        or b['requested_chapters'] & a['dependency_chapters']):
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


def _limit(planner) -> int:
    from core.gemini_gateway import INPUT_LIMIT_BYTES, ATTACHMENT_LIMIT_BYTES
    provider = getattr(planner.client, 'provider_name', None) or getattr(planner.client, 'provider', None)
    capacity = getattr(planner.client, 'text_input_limit_bytes', None)
    value = capacity() if provider == 'gemini_openai' and callable(capacity) else None
    return value if type(value) is int and INPUT_LIMIT_BYTES <= value <= ATTACHMENT_LIMIT_BYTES else INPUT_LIMIT_BYTES


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


def repair(planner, candidate: dict, review: dict) -> tuple[dict, dict]:
    """One immutable-input repair round; retries reuse complete saved responses.

    Classification is evidence for review, never permission to bypass it. No
    original global/batch/review checkpoint or author source is overwritten.
    """
    from core.pipeline_runtime import runtime_stage

    # Native CLI resumes must respect the same spent-attempt boundary as the API.
    old_attempts = ('repair', 'global-reference-repair-v1', 'global-source-restoration-v2')
    if any(list(planner.cache.glob(f'{prefix}*.json')) or
           list((planner.cache / 'raw').glob(f'{prefix}.*')) for prefix in old_attempts):
        planner._needs_attention('已有其他定向修订记录；请检查保存结果，不能通过升级重置修订额度。')
        raise OutlinePlanningError('Existing repair attempt cannot be reset by coordinated recovery')

    limit = _limit(planner)
    extra = {'target_words': planner.target_words,
             **({'confirmed_story_capacity': planner.capacity} if planner.capacity else {})}
    plan_context = build_plan_context(candidate, review, planner.source, planner.brief, planner.locked, extra)
    plan_name = contract.VERSION + '-plan'
    dependencies = {'candidate': content_hash(candidate), 'review': content_hash(review),
                    'context': content_hash(plan_context), 'policy': content_hash(contract.PLAN_INSTRUCTION)}
    validate_plan = lambda value: contract.validate_plan(candidate, review, planner.source, value)
    planner._progress('semantic_review', 'running', '定位核查问题与跨章节依赖；复用全部已保存细纲')
    try:
        plan = planner._load(plan_name, dependencies, validate_plan, strict_dependencies=True)
        if plan is None:
            with runtime_stage('planning.coordinated_repair_plan'):
                plan = planner._call(plan_name, contract.PLAN_INSTRUCTION, plan_context, validate_plan,
                                     input_limit_bytes=limit, strict_dependencies=True)
                planner._save(plan_name, dependencies, plan)
        source_conflicts = [i for i in plan['issues'] if i['classification'] == 'source_conflict']
        if source_conflicts:
            raise OutlinePlanningError('Coordinated repair needs an author decision: ' +
                                       '; '.join(i['reason'] for i in source_conflicts))

        groups = _groups(plan, review, planner.chapter_count)
        global_design = {k: copy.deepcopy(v) for k, v in candidate.items() if k != 'chapters'}
        contexts = repair_contexts(groups, candidate, global_design, review,
            source=planner.source, brief=planner.brief, locked=planner.locked,
            fits=lambda ctx: planner._prompt_fits(contract.INSTRUCTION + '\n\n' + _json(ctx), 'review', limit),
            extra_context={**extra, 'coordinated_issue_plan': plan}) if groups else []
        requests = [_request_protocol(planner, f'{contract.VERSION}-group-{i:03d}', ctx, plan)
                    for i, ctx in enumerate(contexts, 1)]
        if any(not planner._prompt_fits(instruction + '\n\n' + _json(ctx), 'review', limit)
               for ctx, instruction, _, _ in requests):
            raise OutlinePlanningError('Coordinated repair protocol exceeds negotiated capacity; no group was sent')
        assembled = copy.deepcopy(candidate)
        map_positions = {item['number']: index for index, item in enumerate(candidate['chapter_map'])}
        resolutions = {}
        reference_audits = {}
        for index, context in enumerate(contexts, 1):
            from core import planning_reference_recovery as references
            from core import planning_repair_protocol as wire
            numbers = context['requested_chapters']
            scoped_review = context['review']
            original_indices = context['repair_scope']['original_issue_indices']
            group_name = f'{contract.VERSION}-group-{index:03d}'
            request_context, instruction, use_wire, protocol_dependencies = requests[index - 1]
            if use_wire:
                marker_name = group_name + '-protocol'
                if not (planner.cache / f'{marker_name}.json').exists():
                    planner._save(marker_name, protocol_dependencies, {'protocol': wire.VERSION})
            group_dependencies = {'plan': content_hash(plan), 'context': content_hash(request_context),
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
                changed = [c for c in fixed['chapters'] if c != candidate['chapters'][c['number'] - 1]]
                if changed:
                    planner._batch({'chapters': copy.deepcopy(changed)}, [c['number'] for c in changed], global_design)
                planner._global({k: copy.deepcopy(v) for k, v in fixed.items() if k != 'chapters'})
                return value

            def validate_response(value):
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
                f'协调修订 {index}/{len(contexts)} 组 · 第 {"、".join(map(str, numbers))} 章；其他章节保持原文')
            patch = planner._load(group_name, group_dependencies, validate, strict_dependencies=True)
            if patch is None:
                with runtime_stage('planning.coordinated_repair', chapter=numbers[0]):
                    patch = planner._call(group_name, instruction, request_context, validate_response,
                        input_limit_bytes=limit, strict_dependencies=True,
                        **({'recover_completed': recover_completed} if not use_wire else {}))
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
                raise OutlinePlanningError('Coordinated repair needs an author decision: ' + patch['reason'])
            # Every group is bound to the original candidate. _groups ensures no
            # other group edits its requested/read-only dependency chapters.
            fixed = contract.apply(candidate, scoped_review, planner.source, patch, requested_chapters=numbers)
            for n in numbers:
                assembled['chapters'][n - 1] = fixed['chapters'][n - 1]
                position = map_positions[n]
                assembled['chapter_map'][position] = fixed['chapter_map'][position]
            for item in patch['issues']:
                resolutions[original_indices[item['issue_index']]] = copy.deepcopy(item)

        audit = {'schema': contract.VERSION, 'baseline_candidate_sha256': content_hash(candidate), 'issues': []}
        for item in plan['issues']:
            audit['issues'].append({**copy.deepcopy(item),
                'original_issue': copy.deepcopy(review['issues'][item['issue_index']]),
                **({'reference_binding': reference_audits[item['issue_index']]}
                   if item['issue_index'] in reference_audits else {}),
                'resolution': resolutions.get(item['issue_index'], {
                    'status': 'recheck', 'reason': item['reason'], 'source_quotes': item['source_quotes']})})
        planner._save(contract.VERSION + '-candidate', {'plan': content_hash(plan), 'audit': content_hash(audit)},
                      {'candidate': assembled, 'verification': audit})
    except (OutlinePlanningError, ValueError) as exc:
        planner._needs_attention('协调修订尚未形成可核查结果；原大纲和本轮已保存结果均保留。' + str(exc))
        if isinstance(exc, OutlinePlanningError):
            raise
        raise OutlinePlanningError(str(exc)) from exc

    planner._progress('semantic_review', 'running', '修订结果已保存；重新核查受影响内容，逐项复用输入完全相同的已通过核查')
    try:
        checked = planner._semantic_review(assembled, contract.REVIEW_NAME,
            repair_record=audit, reuse_from='review-initial', baseline_candidate=candidate)
    except ValueError:
        planner._needs_attention('协调修订后的核查未形成有效结果；已有回复与全部细纲保留，不重复调用相同核查。')
        raise
    if checked['status'] != 'passed':
        planner._needs_attention('本轮协调修订后仍有实质问题；保留修订结果，不自动追加修订或重做全书。')
        raise OutlinePlanningError('The bounded coordinated repair still needs attention; progress is preserved: ' +
                                   '; '.join(i['message'] for i in checked['issues']))
    return assembled, checked
