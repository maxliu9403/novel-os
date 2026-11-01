"""Private, checksummed checkpoints and cross-process worker ownership."""
from __future__ import annotations
import fcntl
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from core.safe_project_io import read_project_bytes
from .models import canonical, digest


class ReviewConflict(ValueError):
    pass


class ReviewStore:
    prefix = ("outputs", "quality", "reader_reviews")

    def __init__(self, project):
        self.project = Path(os.path.abspath(project))

    @staticmethod
    def open_lock(directory, name):
        flags = os.O_RDWR | os.O_NOFOLLOW
        try:
            fd = os.open(name, flags, dir_fd=directory)
        except FileNotFoundError:
            try:
                fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
            except (FileExistsError, FileNotFoundError):
                # A competing creator won. Open that inode without O_CREAT;
                # some APFS openat creation races report ENOENT, not EEXIST.
                fd = os.open(name, flags, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ReviewConflict("评议锁不是普通文件")
        return fd

    @staticmethod
    def key(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
            raise ValueError("invalid review identifier")
        return value

    def path(self, relative):
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("invalid review path")
        current = self.project
        if current.is_symlink() or not current.is_dir():
            raise ReviewConflict("作品目录已不存在或不安全")
        for part in (*self.prefix, *Path(relative).parts):
            current /= part
            if current.is_symlink():
                raise ReviewConflict("评议路径不能包含符号链接")
        return current

    def read(self, relative):
        self.path(relative)
        raw = read_project_bytes(self.project, (*self.prefix, *Path(relative).parts), missing_ok=True,
                                 max_bytes=64 * 1024 * 1024)
        if raw is None:
            return None
        if len(raw) > 64 * 1024 * 1024:
            raise ReviewConflict("评议记录超过读取上限")
        wrapper = json.loads(raw)
        if set(wrapper) != {"data", "sha256"} or digest(wrapper["data"]) != wrapper["sha256"]:
            raise ReviewConflict("评议记录完整性校验失败")
        return wrapper["data"]

    def write(self, relative, data, *, immutable=False):
        previous = self.read(relative)
        if immutable and previous is not None:
            if digest(previous) != digest(data):
                raise ReviewConflict("不可变评议快照不能覆盖")
            return
        with self.parent(relative, create=True) as directory:
            temporary = ".review-" + uuid.uuid4().hex
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(canonical({"data": data, "sha256": digest(data)}))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, Path(relative).name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass

    @contextmanager
    def parent(self, relative, *, create=False, prefix=None):
        self.path(relative)
        descriptors = []
        try:
            current = os.open(self.project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(current)
            for part in (*(self.prefix if prefix is None else prefix), *Path(relative).parts[:-1]):
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=current)
                    except FileExistsError:
                        pass
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                current = child
                descriptors.append(current)
            yield current
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    @contextmanager
    def locked(self):
        # The reader's existing state directory is stable before the first review.
        # Lock it before creating the private tree, including across API processes.
        # Never race mkdir/openat for the lock's own parent (notably on APFS).
        with self.parent(".reader-reviews.lock", prefix=("outputs", "state")) as directory:
            fd = self.open_lock(directory, ".reader-reviews.lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def acquire_worker(self, review_id):
        with self.parent(f"{self.key(review_id)}/worker.lock") as directory:
            fd = self.open_lock(directory, "worker.lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise ReviewConflict("评议仍在执行或等待当前调用退出，请勿重复启动")
        return fd

    def worker_active(self, review_id):
        try:
            with self.parent(f"{self.key(review_id)}/worker.lock") as directory:
                fd = os.open("worker.lock", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            return False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
        finally:
            os.close(fd)

    def manifests(self):
        root = self.path("")
        if not root.exists():
            return []
        result = []
        for path in root.iterdir():
            if re.fullmatch(r"[a-f0-9]{32}", path.name):
                item = self.read(f"{path.name}/manifest.json")
                if item:
                    result.append(item)
        return sorted(result, key=lambda x: x["created_at"], reverse=True)
