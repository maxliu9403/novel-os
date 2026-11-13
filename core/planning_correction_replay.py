"""Recover an already issued correction before deriving a new metadata child.

Only exact archived request bindings and complete response receipts are usable.
This path never calls the model and never falls back to an older valid proposal.
"""
import json


class PendingCorrection(Exception):
    """A dispatched correction must resume through its original request session."""
    def __init__(self, prompt):
        super().__init__('Saved correction still needs its bound response')
        self.prompt = prompt


def completed_correction(planner, name, instruction, context, original, stage):
    from core.outline_planning import OutlinePlanningError
    correction_name = name + '-format-correction'
    results, pending, completed_prompts = set(), set(), set()
    # These directories are the verified same-run lineage, not a scan of nearby
    # caches. Model switches must not hide an already issued correction.
    for directory in (planner.cache, *planner.predecessors):
        try:
            raw = _from_directory(planner, directory, correction_name, instruction, context, original, stage)
        except PendingCorrection as exc:
            pending.add(exc.prompt)
            continue
        if raw is not None:
            response, prompt = raw
            results.add(response)
            completed_prompts.add(prompt)
    if len(results) > 1:
        raise OutlinePlanningError('Divergent completed corrections are preserved without selecting a story branch')
    pending -= completed_prompts
    if pending:
        if len(pending) > 1 or results:
            raise OutlinePlanningError('Ambiguous correction attempts are preserved without selecting a story branch')
        raise PendingCorrection(pending.pop())
    return next(iter(results)) if results else None


def _from_directory(planner, directory, correction_name, instruction, context, original, stage):
    from core.outline_planning import OutlinePlanningError, SCHEMA_VERSION, content_hash
    responses = list((directory / 'raw').glob(correction_name + '.*.response.txt'))
    prompts = list((directory / 'raw').glob(correction_name + '.*.prompt.txt'))
    saved_path = directory / (correction_name + '-response.json')
    if not saved_path.exists() and not saved_path.is_symlink() and not responses and not prompts:
        return None
    matches = {}
    for path in prompts:
        if path.is_symlink():
            raise OutlinePlanningError('Saved correction request cannot be a filesystem alias')
        prepared = path.read_text(encoding='utf-8')
        prefix = prepared.split('\n\n[CREATIVE_EXECUTION_CONTEXT]\n', 1)[0]
        head, separator, body = prefix.partition('\n\n')
        if not separator or not head.startswith('STAGE: format_correction\n'):
            continue
        try:
            payload = json.loads(body)
        except ValueError:
            continue
        if (isinstance(payload, dict)
                and set(payload) == {'original_instruction', 'original_context', 'completed_response', 'local_error'}
                and payload['original_instruction'] == instruction and payload['original_context'] == context
                and payload['completed_response'] == original and isinstance(payload['local_error'], str)):
            matches[prepared] = prefix
    if len(matches) != 1:
        raise OutlinePlanningError('Saved correction has no unique original request binding; all responses are preserved')
    prepared, prefix = next(iter(matches.items()))
    receipt = None
    if planner.creative_execution is not None:
        current, receipt = planner.creative_execution.prepare('outline_planner', prefix, None, None,
                                                              stage=stage, format_correction=True)
        if current != prepared:
            raise OutlinePlanningError('Saved correction belongs to different creative inputs; original records are preserved')
    elif prefix != prepared:
        raise OutlinePlanningError('Saved correction requires its original creative execution context')
    dependencies = {'prompt_hash': content_hash(prepared)}
    if saved_path.exists() or saved_path.is_symlink():
        # Cross-model response reuse is narrower than design-unit continuation:
        # its entire original request must match, in addition to the saved seed.
        try:
            if directory.is_symlink() or saved_path.is_symlink():
                raise ValueError('correction record cannot be a filesystem alias')
            record = json.loads(saved_path.read_text(encoding='utf-8'))
            if (record['schema_version'] != SCHEMA_VERSION or record['identity_hash'] != directory.name
                    or record['dependencies_hash'] != content_hash(dependencies)
                    or record['payload_hash'] != content_hash(record['payload'])
                    or not isinstance(record['payload']['raw'], str)):
                raise ValueError('correction identity, request or payload hash differs')
            return record['payload']['raw'], prefix
        except (KeyError, TypeError, ValueError) as exc:
            raise OutlinePlanningError(f'Saved correction is invalid; original files are retained: {exc}') from exc
    archived = set()
    for response in responses:
        request = response.with_name(response.name.removesuffix('.response.txt') + '.prompt.txt')
        if response.is_symlink() or request.is_symlink():
            raise OutlinePlanningError('Saved correction response cannot be a filesystem alias')
        if request.is_file() and request.read_text(encoding='utf-8') == prepared:
            archived.add(response.read_text(encoding='utf-8'))
    recover = getattr(planner.creative_execution, 'recover_response', None)
    if len(archived) == 1 and recover:
        raw = recover('outline_planner', prepared, receipt, archived.pop())
        if raw is not None:
            planner._save(correction_name + '-response', dependencies, {'raw': raw})
            return raw, prefix
    if not responses:
        # Do not approve the older candidate while a correction is unfinished.
        # The caller's shared request session owns unknown/terminal recovery.
        raise PendingCorrection(prefix)
    raise OutlinePlanningError('Saved correction lacks a verified complete response; no older candidate or new request was used')
