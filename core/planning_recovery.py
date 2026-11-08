"""Read-only eligibility for explicit, bounded planning policy upgrades."""
from __future__ import annotations

import hashlib
import copy
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


def coordinated_repair_available(project: Path, run_id: str, progress: dict) -> bool:
    """Offer one new mixed-scope repair only for a verified, untouched candidate.

    An interrupted *new* repair uses ordinary checkpoint resume. This upgrade
    never resets a spent allowance or changes a terminal repair into a retry.
    Reading eligibility does not mutate checkpoints, run state, or approvals.
    The native planner still revalidates the current runtime before any call.
    """
    return bool(_verified_planning(project, run_id, progress))


def _verified_planning(project: Path, run_id: str, progress: dict, *, allow_coordinated: bool = False):
    """Read the same complete baseline for both narrowly scoped recovery policies."""
    from core.outline_planning import LOCKED_FIELDS, REVIEW_CHECKS, SCHEMA_VERSION
    from core.planning_context import REVIEW_CONTEXT_VERSION

    recovery = progress.get('recovery')
    if (progress.get('status') != 'failed' or not isinstance(recovery, dict)
            or recovery.get('retryable') is not False
            or recovery.get('action') != 'inspect_saved_design'
            or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', run_id)):
        return False
    identity_hash = progress.get('identity_hash')
    if not isinstance(identity_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', identity_hash):
        return False
    run = project / 'outputs' / 'runs' / run_id
    root = run / 'planning' / 'checkpoints' / identity_hash

    def read(path: Path, limit: int = 512_000) -> dict:
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
                 'repair', 'review-repaired', 'validated')
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
        review = payload('review-initial', {'candidate': _hash(candidate), 'representation': REVIEW_CONTEXT_VERSION})
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
        if {issue['scope'] for issue in issues} != {'global', 'chapter'}:
            return False
        return {'root': root, 'run': run, 'identity': identity, 'identity_hash': identity_hash,
                'source': source.read_text(encoding='utf-8'), 'brief': brief, 'lock': lock,
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
