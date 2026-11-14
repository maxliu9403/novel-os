"""Commit notifications for rebuildable readers; canonical stores stay independent.

Subscribers persist invalidation before/after a write. A failed observer never
invalidates a successful manuscript commit: its journal and periodic discovery
remain the replay authority. Destructive deletion opts into required delivery
before deleting files so a delayed index writer cannot resurrect a project.
Always import this module by its package name, including legacy core modules.
"""
from __future__ import annotations

import logging
import sys
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Callable

_lock = threading.Lock()
_subscribers: set[Callable] = set()
_log = logging.getLogger(__name__)
# The engine supports both package and direct script imports. Share one bus.
sys.modules.setdefault("core.projection_events", sys.modules[__name__])
sys.modules.setdefault("projection_events", sys.modules[__name__])


def subscribe(callback: Callable) -> Callable:
    with _lock:
        _subscribers.add(callback)

    def unsubscribe():
        with _lock:
            _subscribers.discard(callback)
    return unsubscribe


def notify(project_path: str | Path, *domains: str, kind: str = "changed", required: bool = False) -> None:
    with _lock:
        subscribers = tuple(_subscribers)
    if not subscribers or not project_path:
        return
    path = Path(project_path).resolve()
    for callback in subscribers:
        try:
            callback(path, tuple(domains or ("all",)), kind)
        except Exception:
            if required:
                raise
            _log.exception("Query projection notification failed; reconciliation will replay %s", path.name)


@contextmanager
def change(project_path: str | Path, *domains: str):
    notify(project_path, *domains, kind="intent")
    try:
        yield
    finally:
        # Even an interrupted write must invalidate an observed snapshot.
        notify(project_path, *domains)


def observes(path, *domains: str):
    """Attach an observer to a storage commit without changing its contract."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with change(path(*args, **kwargs), *domains):
                return function(*args, **kwargs)
        return wrapped
    return decorate
