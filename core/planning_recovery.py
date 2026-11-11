"""Read-only eligibility for explicit, bounded planning policy upgrades."""
from __future__ import annotations

import hashlib
import copy
import json
import re
from types import SimpleNamespace
from pathlib import Path

from core.planning_correction import VERSION
from core.planning_terminal import final_names, read_verdict, verified_terminal


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_restoration_available(project: Path, run_id: str, progress: dict) -> bool:
    """Offer recovery for v1's unsupported-scope result, never a failed review.

    This is a capability projection, not a state mutation or semantic approval.
    Native resume revalidates every dependency and the complete resulting plan.
    """
    recovery = progress.get('recovery')
    if (progress.get('status') != 'failed' or not isinstance(recovery, dict)
            or recovery.get('retryable') is not False
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
        if (root.resolve() != root.absolute() or any(root.glob(f'{VERSION}*.json'))
                or any(root.rglob('coordinated-evidence-v2*'))):
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


def coordinated_repair_available(project: Path, run_id: str, progress: dict) -> bool:
    """Offer one new mixed-scope repair only for a verified, untouched candidate.

    An interrupted *new* repair uses ordinary checkpoint resume. This upgrade
    never resets a spent allowance or changes a terminal repair into a retry.
    Reading eligibility does not mutate checkpoints, run state, or approvals.
    The native planner still revalidates the current runtime before any call.
    """
    return bool(_verified_planning(project, run_id, progress))


def _verified_planning(project: Path, run_id: str, progress: dict, *, allow_coordinated: bool = False,
                       evidence_upgrade: bool = False, specialized_review: bool = False, residual: bool = False):
    """Read the same complete baseline for both narrowly scoped recovery policies."""
    from core.outline_planning import LOCKED_FIELDS, REVIEW_CHECKS, SCHEMA_VERSION
    from core.planning_context import REVIEW_CONTEXT_VERSION

    recovery = progress.get('recovery')
    # An old chapter-only failed recheck sometimes had no recovery instruction.
    if specialized_review and recovery is None:
        recovery = {'retryable': False, 'action': 'inspect_saved_design'}
    if (progress.get('status') != 'failed' or not isinstance(recovery, dict)
            or recovery.get('retryable') is not False
            or recovery.get('action') not in ({'inspect_saved_design', 'inspect_residual_closure'}
                                             if residual else {'inspect_saved_design'})
            or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', run_id)):
        return False
    identity_hash = progress.get('identity_hash')
    if not isinstance(identity_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', identity_hash):
        return False
    run = project / 'outputs' / 'runs' / run_id
    root = run / 'planning' / 'checkpoints' / identity_hash

    def read(path: Path, limit: int = 8 * 1024 * 1024) -> dict:
        if path.resolve() != path.absolute() or path.stat().st_size > limit:
            raise ValueError('Not a bounded real checkpoint')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Not a checkpoint object')
        return value

    def payload(name: str, dependencies: dict) -> dict:
        record = read(root / f'{name}.json')
        value = record['payload']
        if (record.get('schema_version') != SCHEMA_VERSION
                or record.get('identity_hash') != identity_hash
                or record.get('dependencies_hash') != _hash(dependencies)
                or not isinstance(value, dict) or record.get('payload_hash') != _hash(value)):
            raise ValueError('Invalid checkpoint binding')
        return value

    try:
        if root.resolve() != root.absolute():
            return False
        # Raw request/response artifacts also count as attempts. A failed
        # transmission must not become a new policy allowance on refresh.
        spent = ('global-reference-repair-v1', VERSION, 'review-global-',
                 'repair', 'review-repaired', 'validated', 'coordinated-evidence-v2',
                 'review-coordinated-evidence-v2')
        if evidence_upgrade:
            spent = ('repair', 'review-repaired', 'review-global-', 'validated', 'coordinated-evidence-v2',
                     'review-coordinated-evidence-v2')
        if specialized_review:
            spent = ('coordinated-repair-v1', 'review-coordinated-repair-v1', 'validated',
                     'coordinated-evidence-v2', 'review-coordinated-evidence-v2')
        if residual:
            spent = ('validated',)
        if not allow_coordinated:
            spent += ('coordinated-repair-v1', 'review-coordinated-repair-v1')
        if any(path.is_symlink() or (path.is_file() and path.name.startswith(spent)) for path in root.rglob('*')):
            return False
        identity = read(root / 'identity.json')
        count, batch_size = identity.get('chapter_count'), identity.get('batch_size')
        if (_hash(identity) != identity_hash or identity.get('schema_version') != SCHEMA_VERSION
                or type(count) is not int or not 1 <= count <= 10_000
                or type(batch_size) is not int or not 3 <= batch_size <= 5):
            return False
        total = (count + batch_size - 1) // batch_size
        if (progress.get('chapter_count') != count or progress.get('total_batches') != total
                or progress.get('completed_batches') != total):
            return False
        source = project / 'outputs' / 'input' / 'prompt.md'
        if source.resolve() != source.absolute() or source.stat().st_size > 4_000_000:
            return False
        if hashlib.sha256(source.read_bytes()).hexdigest() != identity.get('source_sha256'):
            return False
        brief_path = source.with_name('brief.json')
        brief = read(brief_path, 4_000_000) if brief_path.exists() else {}
        if _hash(brief) != identity.get('brief_hash'):
            return False
        lock = None
        if identity.get('creative_lock_sha256'):
            lock = read(run / 'creative-design' / 'lock.json', 4_000_000)
            if (_hash(lock) != identity['creative_lock_sha256'] or lock.get('run_id') != run_id
                    or (lock.get('handoff') or {}).get('source_sha256') != identity['source_sha256']):
                return False
        global_design = payload('global', {})
        locked = {key: global_design[key] for key in LOCKED_FIELDS if key in global_design}
        if _hash(locked) != identity.get('locked_contracts_hash'):
            return False
        chapters = []
        for index, start in enumerate(range(1, count + 1, batch_size), 1):
            batch = payload(f'batch-{index:03d}',
                            {'global': _hash(global_design), 'previous': _hash(chapters)})
            values = batch.get('chapters')
            expected = list(range(start, min(start + batch_size, count + 1)))
            if (not isinstance(values, list) or len(values) != len(expected)
                    or any(not isinstance(item, dict) or type(item.get('number')) is not int for item in values)
                    or [item['number'] for item in values] != expected):
                return False
            chapters.extend(values)
        candidate = {**global_design, 'chapters': chapters}
        initial_deps = {'candidate': _hash(candidate), 'representation': REVIEW_CONTEXT_VERSION}
        initial_name = 'review-initial'
        if residual and (root / 'review-initial-authority-snapshot.json').exists():
            from core import planning_author_review as authority
            from core.planning_author_decisions import authorized_choices
            snapshot = payload('review-initial-authority-snapshot', {'stage': 'review-initial', 'version': 1})
            choices = snapshot['choices']
            known = authorized_choices(SimpleNamespace(directory=root.parent.parent,
                                       source=source.read_bytes().decode('utf-8')))
            if not isinstance(choices, list) or any(choice not in known for choice in choices):
                return False
            if choices:
                initial_name += '-choices-' + _hash(choices)[:16]
                initial_deps['authority'] = _hash([choices, authority.RULES])
        review = payload(initial_name, initial_deps)
        if initial_name != 'review-initial':
            authority.inspect_result(review, choices)
        if residual:
            from core.outline_planning import StagedOutlinePlanner
            StagedOutlinePlanner._review(SimpleNamespace(chapter_count=count), review)
            issues = review['issues']
        else:
            coverage, issues = review.get('coverage'), review.get('issues')
            if (review.get('status') != 'blocked' or not isinstance(coverage, dict)
                    or any(coverage.get(key) is not True for key in REVIEW_CHECKS)
                    or not isinstance(issues, list) or not issues):
                return False
            for issue in issues:
                if (not isinstance(issue, dict) or issue.get('scope') not in {'global', 'chapter'}
                        or any(not isinstance(issue.get(key), str) or not issue[key].strip()
                               for key in ('message', 'source_evidence'))):
                    return False
                if issue['scope'] == 'chapter' and (type(issue.get('chapter')) is not int
                                                    or not 1 <= issue['chapter'] <= count):
                    return False
        if not (evidence_upgrade or specialized_review or residual) and {issue['scope'] for issue in issues} != {'global', 'chapter'}:
            return False
        return {'root': root, 'run': run, 'identity': identity, 'identity_hash': identity_hash,
                'source': source.read_bytes().decode('utf-8'), 'brief': brief, 'lock': lock,
                'locked': locked, 'global': global_design, 'candidate': candidate,
                'review': review, 'read': read, 'payload': payload}
    except (OSError, ValueError, TypeError, KeyError):
        return False


def metadata_recovery_available(project: Path, run_id: str, progress: dict) -> bool:
    """Offer a local citation-only child of an exhausted, complete group reply.

    This reader binds both returned replies to their actual saved wire prompts,
    reconstructs the exact original group and never starts a client or writes a
    checkpoint. Ordinary resume must repeat every check before publishing a child.
    """
    from core import planning_coordinated as contract
    from core.outline_planning import SCHEMA_VERSION, StagedOutlinePlanner, _json, _parse_object
    from core.planning_coordinator import _groups
    from core.planning_repair_context import build_plan_context, build_repair_context
    from core.planning_reference_recovery import recover
    from core.story_capacity import read_contract

    saved = _verified_planning(project, run_id, progress, allow_coordinated=True)
    if not saved:
        return False
    root, source, candidate = saved['root'], saved['source'], saved['candidate']
    read, payload = saved['read'], saved['payload']

    def text_file(path: Path) -> str:
        if path.resolve() != path.absolute() or path.stat().st_size > 4_000_000:
            raise ValueError('Not a bounded real response artifact')
        return path.read_text(encoding='utf-8')

    def response(name: str) -> tuple[str, str]:
        record = read(root / f'{name}-response.json')
        value = record['payload']
        if (record.get('schema_version') != SCHEMA_VERSION
                or record.get('identity_hash') != saved['identity_hash']
                or not isinstance(value, dict) or not isinstance(value.get('raw'), str)
                or record.get('payload_hash') != _hash(value)):
            raise ValueError('Invalid completed response binding')
        for path in (root / 'raw').glob(f'{name}.*.prompt.txt'):
            prompt = text_file(path)
            if record.get('dependencies_hash') != _hash({'prompt_hash': _hash(prompt)}):
                continue
            returned = path.with_name(path.name[:-len('.prompt.txt')] + '.response.txt')
            if text_file(returned) == value['raw']:
                return value['raw'], prompt
        raise ValueError('Completed response has no exact saved request/response pair')

    try:
        if any(path.name.startswith((contract.REVIEW_NAME, contract.VERSION + '-candidate'))
               or '-metadata-v1' in path.name for path in root.rglob('*')):
            return False
        validator = StagedOutlinePlanner.__new__(StagedOutlinePlanner)
        validator.source, validator.locked = source, saved['locked']
        validator.chapter_count = saved['identity']['chapter_count']
        validator.capacity = read_contract(source)
        validator.story_first = bool(saved['lock'] and
                                     saved['lock'].get('handoff', {}).get('story_engine') == 'story-first.v1')
        if validator._global(copy.deepcopy(saved['global'])) != saved['global']:
            return False
        for start in range(1, validator.chapter_count + 1, saved['identity']['batch_size']):
            numbers = list(range(start, min(start + saved['identity']['batch_size'], validator.chapter_count + 1)))
            original = {'chapters': candidate['chapters'][start - 1:numbers[-1]]}
            if validator._batch(copy.deepcopy(original), numbers, saved['global']) != original:
                return False
        validator._review(copy.deepcopy(saved['review']))
        creative = None
        if saved['lock']:
            from core.creative_execution import CreativeExecution
            creative = CreativeExecution(project, saved['run'] / 'creative-design', saved['lock'])

        def wire(base: str, *, format_correction: bool = False) -> str:
            return creative.prepare('outline_planner', base, None, None, stage='review',
                **({'format_correction': True} if format_correction else {}))[0] if creative else base

        extra = {'target_words': saved['identity']['target_words'],
                 **({'confirmed_story_capacity': validator.capacity} if validator.capacity else {})}
        context = build_plan_context(candidate, saved['review'], source, saved['brief'], saved['locked'], extra)
        plan = payload(contract.VERSION + '-plan', {'candidate': _hash(candidate), 'review': _hash(saved['review']),
                       'context': _hash(context), 'policy': _hash(contract.PLAN_INSTRUCTION)})
        contract.validate_plan(candidate, saved['review'], source, plan)
        if any(item['classification'] == 'source_conflict' for item in plan['issues']):
            return False
        groups = _groups(plan, saved['review'], validator.chapter_count)
        for path in root.rglob('*'):
            numbered = re.match(re.escape(contract.VERSION) + r'-group-(\d{3})', path.name)
            if numbered and not 1 <= int(numbered.group(1)) <= len(groups):
                return False

        def validate_patch(value: dict, group_context: dict) -> None:
            fixed = contract.apply(candidate, group_context['review'], source, value,
                                   requested_chapters=group_context['requested_chapters'])
            changed = [ch for ch in fixed['chapters'] if ch != candidate['chapters'][ch['number'] - 1]]
            if changed:
                validator._batch({'chapters': copy.deepcopy(changed)}, [ch['number'] for ch in changed], saved['global'])
            validator._global({key: copy.deepcopy(value) for key, value in fixed.items() if key != 'chapters'})

        for index, group in enumerate(groups, 1):
            group_context = build_repair_context(candidate, saved['global'], saved['review'],
                group['requested_chapters'], source=source, brief=saved['brief'], locked=saved['locked'],
                dependency_chapters=group['dependency_chapters'], issues=group['issues'],
                original_issue_indices=group['original_issue_indices'],
                extra_context={**extra, 'coordinated_issue_plan': plan})
            name = f'{contract.VERSION}-group-{index:03d}'
            dependencies = {'plan': _hash(plan), 'context': _hash(group_context), 'policy': _hash(contract.INSTRUCTION)}
            if (root / f'{name}.json').exists():
                patch = payload(name, dependencies)
                validate_patch(patch, group_context)
                if patch['status'] != 'corrected':
                    return False
                continue
            # Only the first unfinished group can be recovered. A later attempt
            # cannot be hidden by rebuilding an earlier incomplete checkpoint.
            for later in range(index + 1, len(groups) + 1):
                if any(root.rglob(f'{contract.VERSION}-group-{later:03d}*')):
                    return False
            original_raw, original_prompt = response(name)
            if original_prompt != wire(contract.INSTRUCTION + '\n\n' + _json(group_context)):
                return False
            try:
                validate_patch(_parse_object(original_raw), group_context)
            except (KeyError, TypeError, ValueError) as exc:
                original_error = str(exc)
            else:
                return False
            final_raw, format_prompt = response(name + '-format-correction')
            base = (format_prompt.rsplit('\n\n[CREATIVE_EXECUTION_CONTEXT]\n', 1)[0]
                    if creative else format_prompt)
            if not base.startswith('STAGE: format_correction\n') or format_prompt != wire(base, format_correction=True):
                return False
            metadata = _parse_object(base.split('\n\n', 1)[1])
            if metadata != {'original_instruction': contract.INSTRUCTION, 'original_context': group_context,
                            'completed_response': original_raw, 'local_error': original_error}:
                return False
            recovered = recover(group_context, source, _parse_object(final_raw), candidate=candidate)
            if recovered is None:
                return False
            canonical, _audit = recovered
            validate_patch(canonical, group_context)
            return canonical.get('status') == 'corrected'
        return False
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        return False


def _artifact(path: Path, *, limit: int = 8 * 1024 * 1024) -> str:
    if path.resolve() != path.absolute() or path.stat().st_size > limit:
        raise ValueError('Invalid planning artifact path or size')
    return path.read_bytes().decode('utf-8')


def legacy_evidence_plan(planner, candidate: dict, review: dict) -> dict | None:
    """Verify old plan receipts against the *current frozen* input and return evidence only.

    The old permission fields are proposals. A new evidence policy must independently
    classify them. Neither reading nor migrating changes a prior artifact.
    """
    from core import planning_coordinated as contract
    from core.outline_planning import SCHEMA_VERSION, _json, _parse_object
    from core.planning_repair_context import build_plan_context

    root = planner.cache
    name = contract.VERSION + '-plan'
    if not any(root.rglob(name + '*')):
        return None
    if root.resolve() != root.absolute():
        raise ValueError('Invalid legacy planning checkpoint location')
    extra = {'target_words': planner.target_words}
    if planner.capacity:
        extra['confirmed_story_capacity'] = planner.capacity
    context = build_plan_context(candidate, review, planner.source, planner.brief, planner.locked, extra)
    dependencies = {'candidate': _hash(candidate), 'review': _hash(review), 'context': _hash(context),
                    'policy': _hash(contract.PLAN_INSTRUCTION)}

    def record(label: str, deps: dict) -> dict:
        envelope = json.loads(_artifact(root / (label + '.json')))
        if (not isinstance(envelope, dict) or envelope.get('schema_version') != SCHEMA_VERSION
                or envelope.get('identity_hash') != planner.identity_hash
                or envelope.get('dependencies_hash') != _hash(deps)
                or not isinstance(envelope.get('payload'), dict)
                or envelope.get('payload_hash') != _hash(envelope['payload'])):
            raise ValueError('Legacy planning checkpoint does not match frozen inputs')
        return envelope['payload']

    def wire(base: str, correction: bool = False) -> str:
        if planner.creative_execution is None:
            return base
        return planner.creative_execution.prepare('outline_planner', base, None, None, stage='review',
                    **({'format_correction': True} if correction else {}))[0]

    retained = []

    def retain(text):
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest not in retained:
            retained.append(digest)

    def read_response(label, check_prompt) -> str | None:
        prompts = sorted((root / 'raw').glob(label + '.*.prompt.txt'))
        if not prompts:
            raise ValueError('No corresponding legacy planning request')
        response_record = root / (label + '-response.json')
        raw = None
        matched = False
        for path in prompts:
            text = _artifact(path)
            if not check_prompt(text):
                raise ValueError('Legacy request disagrees with the frozen input')
            reply_path = path.with_name(path.name.removesuffix('.prompt.txt') + '.response.txt')
            if not response_record.exists():
                # Interrupted before promotion. Unbound output is retained as diagnostic
                # evidence, not treated as a completed plan or a permission to edit.
                retain(text)
                if reply_path.exists():
                    retain(_artifact(reply_path))
                continue
            raw = record(label + '-response', {'prompt_hash': _hash(text)}).get('raw')
            if not isinstance(raw, str):
                raise ValueError('Invalid completed legacy planning response')
            if reply_path.exists() and _artifact(reply_path) == raw:
                matched = True
        if response_record.exists() and not matched:
            raise ValueError('Saved legacy planning response differs from its receipt')
        return raw

    def parse_proposal(text):
        if text is None:
            return None
        try:
            return _parse_object(text)
        except ValueError:
            retain(text)
            return None

    committed = None
    if (root / (name + '.json')).exists():
        committed = contract.validate_plan(candidate, review, planner.source, record(name, dependencies))
    if committed is not None and not (root / (name + '-response.json')).exists():
        # Old snapshots may retain just the validated checkpoint.
        if any((root / 'raw').glob(name + '.*')) or any(root.rglob(name + '-format-correction*')):
            raise ValueError('Missing completed legacy planning receipt')
        return {'latest': committed, 'prior': None}

    expected = wire(contract.PLAN_INSTRUCTION + '\n\n' + _json(context))
    first = read_response(name, lambda text: text == expected)
    correction = name + '-format-correction'
    if any(root.rglob(correction + '*')):
        if first is None:
            raise ValueError('Legacy correction lacks its completed first response')
        try:
            contract.validate_plan(candidate, review, planner.source, _parse_object(first))
        except (ValueError, TypeError, KeyError) as exc:
            original_error = str(exc)
        else:
            raise ValueError('Unexpected correction of an already valid legacy plan')

        def correction_matches(text):
            base = (text.rsplit('\n\n[CREATIVE_EXECUTION_CONTEXT]\n', 1)[0]
                    if planner.creative_execution else text)
            return (base.startswith('STAGE: format_correction\n') and text == wire(base, True)
                    and _parse_object(base.split('\n\n', 1)[1]) == {
                        'original_context': context, 'original_instruction': contract.PLAN_INSTRUCTION,
                        'completed_response': first, 'local_error': original_error})

        final = parse_proposal(read_response(correction, correction_matches))
        prior = parse_proposal(first)
    else:
        final = parse_proposal(first)
        prior = None
    if committed is not None and committed != final:
        raise ValueError('Legacy plan and bound final response disagree')
    return {'latest': final, 'prior': prior, **({'retained_artifact_sha256': retained} if retained else {})}


def evidence_recovery_available(project: Path, run_id: str, progress: dict) -> bool:
    """Offer native migration only for a verified legacy source decision or finished plan."""
    from core import planning_coordinated as contract, planning_correction
    from core.story_capacity import read_contract

    saved = _verified_planning(project, run_id, progress, allow_coordinated=True, evidence_upgrade=True)
    if not saved:
        return False
    root, review, candidate = saved['root'], saved['review'], saved['candidate']
    try:
        scopes = {item['scope'] for item in review['issues']}
        if scopes == {'global'} and (root / (VERSION + '.json')).exists():
            old = saved['payload'](VERSION, {'candidate': _hash(candidate), 'review': _hash(review),
                          'policy': _hash(planning_correction.INSTRUCTION)})
            planning_correction.apply(candidate, review, saved['source'], old, saved['identity']['chapter_count'])
            return old['status'] == 'requires_author'
        if any(path.name.startswith((VERSION, 'global-reference-repair-v1')) for path in root.rglob('*')):
            return False
        if not any(root.rglob(contract.VERSION + '-plan*')):
            return False
        creative = None
        if saved['lock']:
            from core.creative_execution import CreativeExecution
            creative = CreativeExecution(project, saved['run'] / 'creative-design', saved['lock'])
        reader = SimpleNamespace(cache=root, identity_hash=saved['identity_hash'], creative_execution=creative,
            source=saved['source'], brief=saved['brief'], locked=saved['locked'],
            target_words=saved['identity']['target_words'], capacity=read_contract(saved['source']))
        legacy = legacy_evidence_plan(reader, candidate, review)
        if legacy is None:
            return False
        if (any(root.rglob(contract.VERSION + '-group*'))
                or any(root.rglob(contract.VERSION + '-candidate*')) or any(root.rglob(contract.REVIEW_NAME + '*'))):
            return _legacy_group_needs_evidence(saved, legacy['latest'], reader.capacity)
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        return False


def _legacy_group_needs_evidence(saved: dict, plan: dict, capacity) -> bool:
    """Resume a writer's unsupported author escalation after validating completed groups."""
    from core import planning_coordinated as contract, planning_repair_protocol as wire
    from core.planning_coordinator import _groups
    from core.planning_repair_context import build_repair_context

    candidate, review, root = saved['candidate'], saved['review'], saved['root']
    contract.validate_plan(candidate, review, saved['source'], plan)
    if any(item['classification'] == 'source_conflict' for item in plan['issues']):
        return False
    groups = _groups(plan, review, saved['identity']['chapter_count'])
    extra = {'target_words': saved['identity']['target_words'], 'coordinated_issue_plan': plan}
    if capacity:
        extra['confirmed_story_capacity'] = capacity
    assembled = copy.deepcopy(candidate)
    map_positions = {item['number']: i for i, item in enumerate(candidate['chapter_map'])}
    resolutions, reference_audits = {}, {}
    for idx, group in enumerate(groups, 1):
        name = f'{contract.VERSION}-group-{idx:03d}'
        if not (root / f'{name}.json').exists():
            return False
        context = build_repair_context(candidate, saved['global'], review, group['requested_chapters'],
            source=saved['source'], brief=saved['brief'], locked=saved['locked'],
            dependency_chapters=group['dependency_chapters'], issues=group['issues'],
            original_issue_indices=group['original_issue_indices'], extra_context=extra)
        instruction, request = contract.INSTRUCTION, context
        if (root / (name + '-protocol.json')).exists():
            marker = saved['payload'](name + '-protocol', {'plan': _hash(plan),
                'context': _hash(context), 'protocol': wire.VERSION})
            if marker != {'protocol': wire.VERSION}:
                return False
            request, instruction = wire.build(context, saved['source']), wire.INSTRUCTION
        deps = {'plan': _hash(plan), 'context': _hash(request), 'policy': _hash(instruction)}
        audit = None
        if (root / (name + '-metadata-v1.json')).exists():
            from core import planning_reference_recovery as refs
            audit = saved['payload'](name + '-metadata-v1', {
                'candidate': _hash(candidate), 'source': saved['identity']['source_sha256'],
                'context': _hash(context), 'policy': refs.VERSION})
            deps['reference_metadata'] = _hash(audit)
        patch = saved['payload'](name, deps)
        fixed = contract.apply(candidate, context['review'], saved['source'], patch,
                               requested_chapters=context['requested_chapters'])
        indices = group['original_issue_indices']
        if audit is not None:
            if audit.get('output_patch_sha256') != _hash(patch):
                return False
            reference_audits.update({i: audit for i in indices})
        if patch['status'] == 'requires_author':
            return not any(root.rglob(contract.VERSION + '-candidate*'))
        for number in context['requested_chapters']:
            assembled['chapters'][number - 1] = fixed['chapters'][number - 1]
            pos = map_positions[number]
            assembled['chapter_map'][pos] = fixed['chapter_map'][pos]
        for item in patch['issues']:
            resolutions[indices[item['issue_index']]] = item

    # Historical coordinated review can be reopened under the new evidence policy
    # only when both its exact candidate and every resolution can be reconstructed.
    audit = {'schema': contract.VERSION, 'baseline_candidate_sha256': _hash(candidate), 'issues': []}
    for item in plan['issues']:
        index = item['issue_index']
        audit['issues'].append({**item, 'original_issue': review['issues'][index],
            **({'reference_binding': reference_audits[index]} if index in reference_audits else {}),
            'resolution': resolutions.get(index, {'status': 'recheck', 'reason': item['reason'],
                                                   'source_quotes': item['source_quotes']})})
    child = saved['payload'](contract.VERSION + '-candidate', {'plan': _hash(plan), 'audit': _hash(audit)})
    if child != {'candidate': assembled, 'verification': audit}:
        return False
    from core.outline_planning import StagedOutlinePlanner
    from core.planning_context import REVIEW_CONTEXT_VERSION
    from core.planning_review_reuse import RULES
    result = saved['payload'](contract.REVIEW_NAME, {'candidate': _hash(assembled),
        'representation': REVIEW_CONTEXT_VERSION, 'coordinated_verification': _hash(audit),
        'verification_policy': _hash(RULES)})
    validator = SimpleNamespace(chapter_count=saved['identity']['chapter_count'])
    return StagedOutlinePlanner._review(validator, result)['status'] == 'blocked'


def specialized_review_availability(project: Path, run_id: str, progress: dict) -> bool | None:
    """Reassemble a finished narrow correction and verify its blocked final review.

    None means this is a different lifecycle. False means artifacts claiming to
    represent a completed specialized review could not prove that provenance.
    """
    from core import planning_correction

    identity = progress.get('identity_hash')
    if (progress.get('status') != 'failed' or not isinstance(identity, str)
            or not re.fullmatch(r'[0-9a-f]{64}', identity)
            or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', run_id)):
        return None
    root = project / 'outputs/runs' / run_id / 'planning/checkpoints' / identity
    final_names = [n for n in (planning_correction.REVIEW_NAME, 'review-global-reference-v1', 'review-repaired')
                   if (root / (n + '.json')).is_file()]
    if not final_names or any(root.rglob('coordinated-evidence-v2*')):
        return None
    if len(final_names) != 1:
        return False
    saved = _verified_planning(project, run_id, progress, allow_coordinated=True, specialized_review=True)
    if not saved:
        return False
    try:
        validator = _saved_validator(saved)
        _, final = _specialized_pair(saved, validator, final_names[0])
        return True if final['status'] == 'blocked' else None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        return False


def _saved_validator(saved):
    from core.outline_planning import StagedOutlinePlanner
    from core.story_capacity import read_contract

    validator = StagedOutlinePlanner.__new__(StagedOutlinePlanner)
    validator.source, validator.locked = saved['source'], saved['locked']
    validator.brief = saved['brief']
    validator.chapter_count = saved['identity']['chapter_count']
    validator.target_words = saved['identity']['target_words']
    validator.capacity = read_contract(validator.source)
    validator.story_first = bool(saved['lock'] and
        saved['lock'].get('handoff', {}).get('story_engine') == 'story-first.v1')
    validator.cache, validator.directory = saved['root'], saved['run'] / 'planning'
    validator.identity_hash = saved['identity_hash']
    return validator


def _specialized_pair(saved, validator, final_name):
    from core import planning_correction
    from core.planning_context import REVIEW_CONTEXT_VERSION

    root, candidate, review = saved['root'], saved['candidate'], saved['review']
    scopes = {i['scope'] for i in review['issues']}
    deps = {'candidate': _hash(candidate), 'review': _hash(review)}
    if final_name == 'review-repaired':
        from core.planning_chapter_repair import VERSION as CHAPTER_POLICY, repair_chapters
        if scopes != {'chapter'}:
            raise ValueError('Specialized review does not match the baseline')
        def load(label, dependencies, validate, **options):
            if (label == 'repair' and not (root / 'repair.json').exists()
                    and (root / (CHAPTER_POLICY + '-plan.json')).is_file()):
                return None
            return validate(saved['payload'](label, dependencies))
        # Native reconstruction, with no write, prompt, or client available on this reader.
        validator._load = load
        validator._progress = lambda *a, **kw: None
        numbers = sorted({i['chapter'] for i in review['issues']})
        patch = repair_chapters(validator, candidate, saved['global'], review, numbers)
        replacements = {ch['number']: ch for ch in patch['chapters']}
        revised = {**candidate, 'chapters': [replacements.get(ch['number'], ch) for ch in candidate['chapters']]}
    else:
        if scopes != {'global'}:
            raise ValueError('Specialized review does not match the baseline')
        legacy = final_name == 'review-global-reference-v1'
        label = 'global-reference-repair-v1' if legacy else planning_correction.VERSION
        policy = planning_correction.LEGACY_POLICY_HASH if legacy else _hash(planning_correction.INSTRUCTION)
        patch = saved['payload'](label, {**deps, 'policy': policy})
        if patch.get('status') != 'corrected':
            raise ValueError('Specialized review does not match the baseline')
        revised = planning_correction.apply(candidate, review, validator.source, patch, validator.chapter_count)
        chapters = revised.pop('chapters')
        revised = {**validator._global(revised), 'chapters': chapters}
    if revised == candidate:
        raise ValueError('Specialized review does not match the baseline')
    deps = {'candidate': _hash(revised), 'representation': REVIEW_CONTEXT_VERSION}
    return revised, read_verdict(validator, final_name, deps, final_names(root, final_name))


def residual_recovery_availability(project: Path, run_id: str, progress: dict) -> bool | None:
    """Project a completed, blocked historical verdict onto the current repair capability."""
    from core import planning_correction, planning_evidence_plan
    from core.planning_author_decisions import authorized_choices

    identity = progress.get('identity_hash')
    recovery = progress.get('recovery') or {}
    if (progress.get('status') != 'failed' or not isinstance(recovery, dict) or recovery.get('retryable') is not False
            or recovery.get('action') not in {'inspect_saved_design', 'inspect_residual_closure'}
            or not isinstance(identity, str)
            or not re.fullmatch(r'[0-9a-f]{64}', identity) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', run_id)):
        return None
    root = project / 'outputs/runs' / run_id / 'planning/checkpoints' / identity
    prefix = planning_evidence_plan.VERSION
    if not any(re.fullmatch(re.escape('review-' + prefix) +
            r'(?:-scoped-[0-9a-f]{16})?(?:-author-[0-9a-f]{16})?(?:-followup)?(?:-choices-[0-9a-f]{16})?\.json',
            path.name) for path in root.glob('review-' + prefix + '*.json')):
        return None
    saved = _verified_planning(project, run_id, progress, allow_coordinated=True, residual=True)
    if not saved:
        return False
    try:
        validator = _saved_validator(saved)
        candidate, review = saved['candidate'], saved['review']
        specialized = [name for name in (planning_correction.REVIEW_NAME, 'review-global-reference-v1', 'review-repaired')
                       if final_names(root, name)]
        if len(specialized) > 1:
            return False
        if specialized:
            candidate, review = _specialized_pair(saved, validator, specialized[0])
        choices = authorized_choices(validator)
        if review['status'] == 'blocked':
            pair = _coordinated_terminal(validator, candidate, review, prefix, choices, include_origin=True)
            if pair is None:
                return False
            candidate, review, origin = pair
            if review['status'] == 'blocked':
                return (_identity_stall_recovery(saved, validator, candidate, review, origin, choices)
                        if recovery.get('action') == 'inspect_residual_closure' else True)
        # A passed design can subsequently be blocked at the publication gate
        # for a newly settled author choice. Preserve that intervening verdict.
        label = 'review-publication-authority-' + _hash(candidate)[:16]
        names = final_names(root, label)
        if names and choices:
            from core.planning_context import REVIEW_CONTEXT_VERSION
            review = read_verdict(validator, label,
                        {'candidate': _hash(candidate), 'representation': REVIEW_CONTEXT_VERSION}, names)
            if review['status'] == 'blocked':
                pair = _coordinated_terminal(validator, candidate, review, prefix, choices, include_origin=True)
                if not pair or pair[1]['status'] != 'blocked':
                    return False
                return (_identity_stall_recovery(saved, validator, *pair, choices)
                        if recovery.get('action') == 'inspect_residual_closure' else True)
        return None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        return False


def _coordinated_terminal(validator, candidate, review, prefix, choices, *, include_origin=False):
    scoped = prefix + '-scoped-' + _hash([candidate, review])[:16]
    targets = [prefix, scoped]
    if choices:
        suffix = '-author-' + _hash([choices, candidate, review])[:16]
        targets = [label + suffix for label in targets] + targets
    for label in targets:
        pair = verified_terminal(validator, candidate, review, label)
        if pair is not None:
            return (*pair, label) if include_origin else pair
    return None


def _identity_stall_recovery(saved, validator, candidate, review, origin, choices):
    """Replay the old finite convergence ledger read-only before offering a new capability."""
    from core.outline_planning import OutlinePlanningError
    from core.planning_residual_loop import converge, _legacy_identity_upgrade
    from core.planning_terminal import _read_candidate, _read_round

    root = saved['root']
    entry = 'residual-entry-' + _hash([origin, candidate, review])[:24] + '-author-inputs'
    dependencies = {'candidate': _hash(candidate), 'review': _hash(review), 'source': _hash(validator.source)}
    frozen = saved['payload'](entry, dependencies)
    if (set(frozen) != {'choices'} or not isinstance(frozen['choices'], list)
            or any(choice not in choices for choice in frozen['choices'])):
        raise ValueError('Residual entry no longer has its original authorized choices')
    last = None

    def load(name, dependencies, validate, **options):
        return validate(saved['payload'](name, dependencies))

    def refuse_write(*args, **kwargs):
        raise ValueError('Missing completed residual stop receipt')

    def replay(current, findings, prefix, previous_review):
        nonlocal last
        names = final_names(root, 'review-' + prefix)
        if names:
            fixed, result, _ = _read_round(validator, prefix, current, findings, names)
        else:
            fixed, _ = _read_candidate(validator, prefix, current, findings)
            if fixed != current:
                raise ValueError('Changed residual candidate requires its completed semantic review')
            result = findings
        last = (prefix, current, fixed)
        return fixed, result

    validator._load, validator._save = load, refuse_write
    validator._progress, validator.progress = lambda *args: None, {}
    try:
        converge(validator, candidate, review, replay, authority=frozen['choices'], allow_identity_upgrade=False)
    except OutlinePlanningError:
        if (validator.progress.get('recovery', {}).get('action') != 'inspect_residual_closure' or last is None):
            raise
        upgrade = _legacy_identity_upgrade(validator, *last)
        # A new capability can be offered once. Its interrupted requests already
        # use the normal resume lifecycle, and a new terminal stop stays terminal.
        return bool(upgrade and not any(root.glob(upgrade + '*')) and
                    not any((root / 'raw').glob(upgrade + '*')))
    return False
