"""Reversible cover-only source checkpoints. Never deploys or edits runtime data.

Capture the six cover implementation files from the running backend and the local
candidate. Preview by default; restore requires --apply and matching source and
shared-contract hashes. Archives contain code, not credentials or manuscripts.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import uuid
import zipfile

COVER_FILES = (
    'core/cover_director.py', 'core/cover_prompt_compiler.py',
    'core/cover_render_policy.py', 'core/cover_validator.py',
    'core/cover_quality.py', 'api/cover_service.py',
)
# Read-only compatibility guards: these are NEVER restored by this tool.
GUARD_FILES = (
    'core/cover_models.py', 'core/cover_models_v2.py', 'core/cover_design.py',
    'core/cover_profiles.py', 'core/cover_novelty.py', 'core/cover_handoff.py',
    'core/cover_store.py', 'core/cover_cli.py', 'core/image_client.py',
    'core/delivery_package.py', 'api/routes.py', 'api/media.py',
)
MAX_FILE_BYTES = 1_000_000


class CheckpointError(ValueError):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(data) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _source_path(repo: Path, name: str) -> Path:
    path = repo / name
    # No symlinked source files or parents; restoring code must stay in this repo.
    current = path
    while current != repo:
        if current.is_symlink():
            raise CheckpointError(f'Symlinked source path: {name}')
        current = current.parent
    if not path.is_file():
        raise CheckpointError(f'Missing source file: {name}')
    return path


def _versions(files: dict[str, bytes]) -> dict:
    values = {}
    for key, filename, variable in (
        ('compiler', 'core/cover_prompt_compiler.py', 'COMPILER_VERSION'),
        ('profile', 'core/cover_render_policy.py', 'PHOTOGRAPHIC_PROFILE'),
    ):
        for node in ast.parse(files[filename]).body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == variable for target in node.targets
            ):
                values[key] = ast.literal_eval(node.value)
                break
        else:
            raise CheckpointError(f'Checkpoint is missing {variable}')
    return values


def _validate_sources(files: dict[str, bytes]) -> None:
    if set(files) != set(COVER_FILES):
        raise CheckpointError('Checkpoint source allowlist mismatch')
    for name, data in files.items():
        if not data or len(data) > MAX_FILE_BYTES:
            raise CheckpointError(f'Invalid source size: {name}')
        ast.parse(data, filename=name)


def create_checkpoint(repo: Path, before: dict[str, bytes], *, source: dict) -> Path:
    repo = repo.resolve()
    after = {name: _source_path(repo, name).read_bytes() for name in COVER_FILES}
    _validate_sources(before)
    _validate_sources(after)
    guards = {name: _digest(_source_path(repo, name).read_bytes()) for name in GUARD_FILES}
    checkpoint_id = datetime.now(timezone.utc).strftime('cover-%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
    manifest = dict(schema_version=1, checkpoint_id=checkpoint_id, source=source, guards=guards)
    for label, files in (('before', before), ('after', after)):
        manifest[label] = dict(versions=_versions(files), files={
            name: dict(sha256=_digest(data), size=len(data)) for name, data in files.items()
        })
    manifest['manifest_sha256'] = _digest(_json_bytes(manifest))
    directory = repo / '.cover-checkpoints'
    if directory.is_symlink():
        raise CheckpointError('Checkpoint directory is a symlink')
    directory.mkdir(exist_ok=True)
    archive_path = directory / (checkpoint_id + '.zip')
    # Exclusive creation: a snapshot is immutable, never replace a previous one.
    with archive_path.open('xb') as output:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', _json_bytes(manifest))
            for label, files in (('before', before), ('after', after)):
                for name, data in files.items():
                    archive.writestr(f'{label}/{name}', data)
        output.flush()
        os.fsync(output.fileno())
    _load_checkpoint(archive_path)
    return archive_path


def _load_checkpoint(checkpoint: Path) -> tuple[dict, dict]:
    expected = {'manifest.json'} | {f'{label}/{name}' for label in ('before', 'after') for name in COVER_FILES}
    try:
        with zipfile.ZipFile(checkpoint) as archive:
            names = archive.namelist()
            if len(names) != len(expected) or set(names) != expected:
                raise CheckpointError('Unexpected or duplicate checkpoint archive entries')
            if any(info.file_size > MAX_FILE_BYTES for info in archive.infolist()):
                raise CheckpointError('Oversized checkpoint entry')
            manifest = json.loads(archive.read('manifest.json'))
            claimed = manifest.pop('manifest_sha256')
            if manifest.get('schema_version') != 1 or claimed != _digest(_json_bytes(manifest)):
                raise CheckpointError('Checkpoint manifest checksum mismatch')
            if set(manifest['guards']) != set(GUARD_FILES):
                raise CheckpointError('Checkpoint shared-contract allowlist mismatch')
            bundles = {}
            for label in ('before', 'after'):
                if set(manifest[label]['files']) != set(COVER_FILES):
                    raise CheckpointError('Checkpoint source allowlist mismatch')
                files = {name: archive.read(f'{label}/{name}') for name in COVER_FILES}
                _validate_sources(files)
                for name, data in files.items():
                    if manifest[label]['files'][name] != {'sha256': _digest(data), 'size': len(data)}:
                        raise CheckpointError(f'Checkpoint checksum mismatch: {label}/{name}')
                if manifest[label]['versions'] != _versions(files):
                    raise CheckpointError('Checkpoint version metadata mismatch')
                bundles[label] = files
    except (OSError, zipfile.BadZipFile, KeyError, TypeError, SyntaxError, UnicodeError) as exc:
        raise CheckpointError(f'Invalid checkpoint: {exc}') from exc
    return manifest, bundles


def inspect_checkpoint(repo: Path, checkpoint: Path) -> dict:
    repo = repo.resolve()
    manifest, bundles = _load_checkpoint(checkpoint)
    current = {name: _digest(_source_path(repo, name).read_bytes()) for name in COVER_FILES}
    matches = {label: all(current[name] == _digest(files[name]) for name in COVER_FILES)
               for label, files in bundles.items()}
    unknown = [name for name in COVER_FILES
               if current[name] not in {_digest(bundles[label][name]) for label in bundles}]
    changed_guards = [name for name in GUARD_FILES
                      if _digest(_source_path(repo, name).read_bytes()) != manifest['guards'][name]]
    state = 'both' if all(matches.values()) else next((k for k, match in matches.items() if match),
                                                     'modified' if unknown else 'mixed')
    return dict(checkpoint=str(checkpoint.resolve()), checkpoint_id=manifest['checkpoint_id'],
                checkpoint_sha256=_digest(checkpoint.read_bytes()), source=manifest['source'],
                before=manifest['before']['versions'], after=manifest['after']['versions'],
                active_source=state, modified_files=unknown, changed_shared_contracts=changed_guards,
                switch_ready=not unknown and not changed_guards,
                runtime_changed=False, note='Source only; running service is unchanged. Deployment is a separate explicit action.')


def _atomic_write(path: Path, data: bytes) -> None:
    mode = path.stat().st_mode & 0o777
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            name = output.name
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


@contextmanager
def _restore_lock(repo: Path):
    directory = repo / '.cover-checkpoints'
    if directory.is_symlink():
        raise CheckpointError('Checkpoint directory is a symlink')
    directory.mkdir(exist_ok=True)
    lock = directory / 'restore.lock'
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CheckpointError('A restore lock exists; verify no restore is running before removing the stale lock') from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def restore_checkpoint(repo: Path, checkpoint: Path, *, to: str, apply: bool = False) -> dict:
    if to not in {'before', 'after'}:
        raise CheckpointError('Target must be before or after')
    repo = repo.resolve()
    manifest, bundles = _load_checkpoint(checkpoint)
    state = inspect_checkpoint(repo, checkpoint)
    if not state['switch_ready']:
        raise CheckpointError('Later edits or changed shared contracts detected; no files restored: ' +
                              ', '.join(state['modified_files'] + state['changed_shared_contracts']))
    changes = [name for name in COVER_FILES if _source_path(repo, name).read_bytes() != bundles[to][name]]
    result = dict(state, target=to, files_to_restore=changes, applied=False)
    if not apply:
        return result
    with _restore_lock(repo):
        # Repeat preflight after obtaining the lock. Never overwrite unknown edits.
        if not inspect_checkpoint(repo, checkpoint)['switch_ready']:
            raise CheckpointError('Source changed during restore preflight')
        originals = {name: _source_path(repo, name).read_bytes() for name in COVER_FILES}
        changed = []
        try:
            for name in changes:
                path = _source_path(repo, name)
                if path.read_bytes() != originals[name]:
                    raise CheckpointError(f'Source changed during restore: {name}')
                _atomic_write(path, bundles[to][name])
                changed.append(name)
            if any(_source_path(repo, name).read_bytes() != bundles[to][name] for name in COVER_FILES):
                raise CheckpointError('Post-restore verification failed')
        except Exception as exc:
            recovery_errors = []
            for name in reversed(changed):
                try:
                    path = _source_path(repo, name)
                    if path.read_bytes() != bundles[to][name]:
                        raise CheckpointError('Concurrent edit detected')
                    _atomic_write(path, originals[name])
                except Exception:
                    recovery_errors.append(name)
            raise CheckpointError(f'Restore interrupted: {exc}; recovery errors: {recovery_errors}') from exc
    return dict(inspect_checkpoint(repo, checkpoint), target=to, files_to_restore=changes, applied=True)


def _docker(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(['docker', *args], cwd=repo, stderr=subprocess.PIPE, timeout=30)
    except subprocess.CalledProcessError as exc:
        raise CheckpointError(exc.stderr.decode('utf-8', errors='replace').strip()) from exc


def capture_running(repo: Path) -> Path:
    ids = _docker(repo, 'compose', 'ps', '-q', 'backend').decode().split()
    if len(ids) != 1 or not re.fullmatch(r'[0-9a-f]{12,64}', ids[0]):
        raise CheckpointError('Expected one running backend container; nothing captured')
    container = ids[0]
    image_id = _docker(repo, 'inspect', '--format', '{{.Image}}', container).decode().strip()
    before = {}
    for name in COVER_FILES:
        raw = _docker(repo, 'cp', f'{container}:/app/{name}', '-')
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            members = archive.getmembers()
            if len(members) != 1 or not members[0].isfile() or members[0].size > MAX_FILE_BYTES:
                raise CheckpointError(f'Unexpected container source entry: {name}')
            source = archive.extractfile(members[0])
            assert source is not None
            before[name] = source.read()
    if _docker(repo, 'compose', 'ps', '-q', 'backend').decode().split() != ids:
        raise CheckpointError('Backend container changed during capture; retry capture')
    return create_checkpoint(repo, before, source={'container_id': container, 'image_id': image_id,
        'origin': 'running backend /app cover source; not a whole-image or data backup'})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('capture', help='Read running backend code and save before/after checkpoint')
    for name in ('status', 'restore'):
        sub = commands.add_parser(name)
        sub.add_argument('checkpoint', type=Path)
        if name == 'restore':
            sub.add_argument('--to', choices=('before', 'after'), required=True)
            sub.add_argument('--apply', action='store_true', help='Explicitly restore source files; default is preview')
    args = parser.parse_args()
    try:
        if args.command == 'capture':
            checkpoint = capture_running(args.repo)
            result = inspect_checkpoint(args.repo, checkpoint)
        elif args.command == 'status':
            result = inspect_checkpoint(args.repo, args.checkpoint)
        else:
            result = restore_checkpoint(args.repo, args.checkpoint, to=args.to, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (CheckpointError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f'Cover checkpoint error: {exc}\n')


if __name__ == '__main__':
    main()
