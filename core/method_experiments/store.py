"""Owned experiment directories, checksummed journals and process-wide run lock."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from narrative_methods.models import canonical, digest


def assert_isolated_destination(root: Path):
    """Shared by model experiments and technical sample builders; no writes."""
    for parent in (Path(root).absolute(), *Path(root).absolute().parents):
        if parent.is_symlink():
            raise ValueError('experiment paths must not contain symlinks')
        if parent.name in {'projects', 'docker-data'} or any((parent / marker).exists() for marker in (
            'project_identity.json', 'outputs/state/project_identity.json', 'outputs/state/story_state.json', 'outputs/deliverables',
        )):
            raise ValueError('experiments must be outside Novel OS project data')


class ExperimentStore:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    def path(self, relative: str) -> Path:
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("invalid experiment path")
        assert_isolated_destination(self.root)
        path = self.root
        for part in Path(relative).parts:
            path /= part
            if path.is_symlink():
                raise ValueError("experiment paths must not contain symlinks")
        return path

    @contextmanager
    def locked(self, *, initialize=False):
        path = self.path(".lock")
        if initialize:
            self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ValueError("experiment has not been planned")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if not self.path("plan.json").exists() and any(p.name != '.lock' for p in self.root.iterdir()):
                raise ValueError("experiment directory is not empty or owned")
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def read(self, relative):
        path = self.path(relative)
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > 16_000_000:
            raise ValueError("experiment record exceeds limits")
        value = json.loads(path.read_text(encoding="utf-8"))
        if set(value) != {"data", "sha256"} or digest(value['data']) != value['sha256']:
            raise ValueError("experiment record checksum mismatch")
        return value['data']

    def write(self, relative, value, *, immutable=True):
        previous = self.read(relative)
        if previous is not None and immutable:
            if digest(previous) != digest(value):
                raise ValueError("immutable experiment record differs")
            return previous
        self.write_bytes(relative, canonical({'data': value, 'sha256': digest(value)}) + b'\n', immutable=False)
        return value

    def write_bytes(self, relative, data: bytes, *, immutable=True):
        path = self.path(relative)
        if path.exists() and immutable:
            if path.read_bytes() != data:
                raise ValueError('immutable experiment bytes differ')
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.experiment-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
