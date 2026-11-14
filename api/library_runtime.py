"""App-lifetime catalog coordination. No story work runs in a library GET."""
from __future__ import annotations

import threading
import os
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.engine import make_url

from core.projection_events import subscribe
from . import tenancy
from .library_catalog import LibraryCatalog
from .library_indexer import LibraryIndexer


class LibraryRuntime:
    def __init__(self, database_url: str):
        # The canonical API supports PostgreSQL as well as SQLite. Its query
        # catalog is local/rebuildable in this stage and must not prevent a
        # PostgreSQL installation from starting. Operators may place it on a
        # persistent volume independently of their canonical database.
        configured = os.environ.get("NOVEL_OS_LIBRARY_DB")
        url = make_url(configured or database_url)
        if not configured and url.get_backend_name() != "sqlite":
            url = make_url("sqlite:///./novel_os.library.sqlite")
        self._temporary = None
        if url.get_backend_name() != "sqlite":
            raise ValueError("NOVEL_OS_LIBRARY_DB must use a SQLite catalog URL")
        if not url.database or url.database == ":memory:":
            self._temporary = TemporaryDirectory(prefix="novel-library-")
            self.database = Path(self._temporary.name) / "catalog.sqlite"
        else:
            self.database = Path(url.database).resolve()
        self.cache_root = self.database.parent / f"{self.database.stem}.library-cache"
        self._lock = threading.RLock()
        self._scopes: dict[tuple[str, str], tuple[LibraryCatalog, LibraryIndexer]] = {}
        self._unsubscribe = None
        self._running = False

    def start(self, service):
        with self._lock:
            if not self._running:
                self._running = True
                self._unsubscribe = subscribe(self._changed)
        return self.for_service(service)

    def for_service(self, service) -> tuple[LibraryCatalog, LibraryIndexer]:
        root = service.base_dir.resolve()
        workspace = service.workspace.id if service.workspace else tenancy.DEFAULT_WORKSPACE_ID
        key = (str(root), workspace)
        with self._lock:
            entry = self._scopes.get(key)
            if entry is None:
                catalog = LibraryCatalog(self.database, root, workspace_id=workspace)
                indexer = LibraryIndexer(catalog, root, self.cache_root)
                entry = (catalog, indexer)
                self._scopes[key] = entry
            if self._running:
                entry[1].start()
            return entry

    def _changed(self, path: Path, domains: tuple[str, ...], kind: str):
        with self._lock:
            entries = [entry for (root, _), entry in self._scopes.items() if root == str(path.parent)]
        for catalog, indexer in entries:
            if kind == "deleted":
                catalog.tombstone(path.name)
            elif kind == "purged":
                indexer.purge_project(path.name)
            elif kind == "created":
                if (path / "outputs/state/story_state.json").is_file():
                    # Only an explicit, successful new-project initialization
                    # can reopen a deleted slug. Scanner notifications cannot.
                    catalog.discover(path.name, fingerprint=f"created:{uuid.uuid4().hex}", allow_recreate=True)
            else:
                # An HTTP guard can precede a handler that returns 404, and
                # initial creation emits intent before its state exists. Do
                # not turn those unsuccessful operations into phantom cards.
                # This check is at the write boundary, never in library GET.
                if catalog.get_record(path.name) is None and not (path / "outputs/state/story_state.json").is_file():
                    continue
                catalog.invalidate(path.name, domains=domains)
            indexer.wake()

    def stop(self):
        with self._lock:
            self._running = False
            if self._unsubscribe:
                self._unsubscribe()
                self._unsubscribe = None
            entries = list(self._scopes.values())
        for _, indexer in entries:
            indexer.stop()
