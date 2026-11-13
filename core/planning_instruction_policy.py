"""Freeze the instructions used to address a staged outline's checkpoints.

A model route has its own execution namespace. The editorial policy belongs to
the story inputs and survives route changes. For unpinned runs, only historical
policies whose exact identity can be reconstructed are eligible for adoption.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re

from core.planning_errors import OutlinePlanningError

SCHEMA = 'planning.instruction-policy.v1'


@dataclass(frozen=True)
class Instructions:
    global_prompt: str
    global_seed_prompt: str
    batch_prompt: str
    projection_rules: str
    blueprint_craft: str
    legacy_review_prompt: str
    review_prompt: str
    review_window_rules: str
    arc_vocabulary: str = 'lifecycle'

    def identity_fields(self):
        from core.outline_planning import content_hash
        # This is the original namespace formula, including its legacy review
        # component. The full pin also binds the seed and current review text.
        return {'craft_hash': content_hash(self.blueprint_craft), 'instructions_hash': content_hash(
            [self.global_prompt, self.batch_prompt, self.legacy_review_prompt])}


def _safe(path, root):
    relative = path.relative_to(root)
    if '..' in relative.parts:
        raise ValueError('Planning policy is outside this planning directory')
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('Planning policy cannot use a symlink')
        if item == root:
            break


def _read(path, root, max_bytes):
    _safe(path, root)
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError('Missing or oversized planning policy record')
    record = json.loads(path.read_bytes())
    if not isinstance(record, dict):
        raise ValueError('Invalid planning policy record')
    return record


def _basis(identity):
    return {k: v for k, v in identity.items() if k not in {'model', 'craft_hash', 'instructions_hash'}}


def _record(basis_hash, rules):
    from core.outline_planning import content_hash
    body = {'schema': SCHEMA, 'basis_sha256': basis_hash, 'rules': asdict(rules)}
    return {**body, 'sha256': content_hash(body)}


def _read_rules(path, root, basis_hash):
    record = _read(path, root, 1024 * 1024)
    rules = Instructions(**record['rules'])
    if (any(not isinstance(text, str) or not text for text in asdict(rules).values())
            or rules.arc_vocabulary not in {'lifecycle', 'historical'}
            or record != _record(basis_hash, rules)):
        raise ValueError('Planning instruction policy checksum or story basis mismatch')
    return rules


def rules_for_identity(directory, identity, *, scoped, story_first, expected_pin=None):
    """Read the rule family of one verified checkpoint for status projections."""
    from core.outline_planning import content_hash
    basis_hash = content_hash(_basis(identity))
    path = directory / 'instruction-policies' / (basis_hash + '.instruction-policy.json')
    _safe(path, directory)
    if path.exists():
        rules = _read_rules(path, directory, basis_hash)
        if expected_pin is not None and expected_pin != _record(basis_hash, rules)['sha256']:
            raise ValueError('Active progress and planning instruction policy checksum differ')
        candidates = [rules]
    else:
        if expected_pin is not None:
            raise ValueError('Pinned planning instruction policy is missing')
        from core.planning_historical_instructions import historical_instructions
        candidates = historical_instructions(scoped=scoped, story_first=story_first)
    for policy in candidates:
        if all(identity.get(k) == v for k, v in policy.identity_fields().items()):
            return policy
    raise ValueError('Saved checkpoint does not match a recognized instruction policy')


def _historical_namespaces(directory, identity, previous, *, scoped, story_first):
    """Address known policy/model combinations, without enumerating unrelated books."""
    from core.outline_planning import content_hash
    from core.planning_historical_instructions import historical_instructions
    candidates = {}
    models = [identity['model']]
    if previous is not None and previous['model'] != models[0]:
        models.append(previous['model'])
    for policy in historical_instructions(scoped=scoped, story_first=story_first):
        for model in models:
            expected = {**identity, **policy.identity_fields(), 'model': model}
            cache = directory / 'checkpoints' / content_hash(expected)
            path = cache / 'identity.json'
            _safe(path, directory)
            if path.exists() and _read(path, directory, 128_000) != expected:
                raise ValueError('Planning checkpoint identity checksum mismatch')
            if path.exists():
                candidates[cache] = policy
    return candidates


def _matches(directory, cache, prompt_sha):
    raw = cache / 'raw'
    _safe(raw, directory)
    for prompt in raw.glob('*.prompt.txt'):
        _safe(prompt, directory)
        if prompt.is_file():
            digest = hashlib.sha256()
            with prompt.open('rb') as stream:
                for chunk in iter(lambda: stream.read(128 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() == prompt_sha:
                return True
    return False


def _previous(directory, identity, current):
    """Return the active identity and its policy receipt only for this story."""
    from core.outline_planning import content_hash
    progress_path = directory / 'progress.json'
    _safe(progress_path, directory)
    if not progress_path.exists():
        return None, None
    progress = _read(progress_path, directory, 8 * 1024 * 1024)
    address = progress['identity_hash']
    if not isinstance(address, str) or not re.fullmatch('[0-9a-f]{64}', address):
        raise ValueError('Invalid planning progress identity')
    path = directory / 'checkpoints' / address / 'identity.json'
    _safe(path, directory)
    if not path.exists() and 'instruction_policy_sha256' not in progress:
        # A standalone planning component may record progress without opening
        # a whole-run namespace. Such records can identify only this caller's
        # exact inputs. Full run() still requires its normal identity envelope.
        if address == content_hash({**identity, **current.identity_fields()}):
            return None, None
    previous = _read(path, directory, 128_000)
    if content_hash(previous) != address:
        raise ValueError('Planning checkpoint identity checksum mismatch')
    if _basis(previous) != _basis(identity):
        return None, None
    return previous, progress.get('instruction_policy_sha256')


def _adopt(directory, identity, current, previous, *, scoped, story_first):
    if previous is None:
        checkpoints = directory / 'checkpoints'
        _safe(checkpoints, directory)
        if not checkpoints.exists():
            return current
    from core.outline_planning import content_hash
    candidates = _historical_namespaces(directory, identity, previous, scoped=scoped, story_first=story_first)
    if previous is not None and directory / 'checkpoints' / content_hash(previous) not in candidates:
        raise ValueError('Unrecognized historical planning instructions for the same story inputs')
    if not candidates:
        return current
    if len(set(candidates.values())) == 1:
        return next(iter(candidates.values()))

    # A previous release could leave an empty replacement namespace in
    # progress.json. A bound request can identify its actual logical prompt,
    # including a late completion. Directory age and file counts prove nothing.
    from core.request_recovery import active_planning_prompt
    prompt_sha = active_planning_prompt()
    if prompt_sha:
        matches = {policy for cache, policy in candidates.items() if _matches(directory, cache, prompt_sha)}
        if len(matches) == 1:
            return matches.pop()
    raise ValueError('Ambiguous historical planning policy: the original request must identify a single branch')


@dataclass(frozen=True)
class InstructionPolicy:
    path: Path
    root: Path
    record: dict
    rules: Instructions

    @classmethod
    def resolve(cls, directory, identity, current, *, scoped, story_first):
        from core.outline_planning import content_hash
        basis_hash = content_hash(_basis(identity))
        path = directory / 'instruction-policies' / (basis_hash + '.instruction-policy.json')
        try:
            _safe(path, directory)
            previous, expected_pin = _previous(directory, identity, current)
            if path.exists():
                rules = _read_rules(path, directory, basis_hash)
            else:
                if expected_pin is not None:
                    raise ValueError('Pinned planning instruction policy is missing')
                rules = _adopt(directory, identity, current, previous, scoped=scoped, story_first=story_first)
            record = _record(basis_hash, rules)
            if expected_pin is not None and expected_pin != record['sha256']:
                raise ValueError('Active progress and planning instruction policy checksum differ')
            return cls(path, directory, record, rules)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OutlinePlanningError(f'Planning continuation policy could not be verified: {exc}') from exc

    def persist(self, write_json):
        _safe(self.path, self.root)
        if self.path.exists():
            if _read(self.path, self.root, 1024 * 1024) != self.record:
                raise ValueError('Planning instruction policy changed before continuation')
        else:
            write_json(self.path, self.record)
