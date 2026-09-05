"""Process-local coordination for project mutations and permanent deletion.

Project data spans canonical files, database projections and media blobs.  A
per-project operation gate prevents an ordinary HTTP mutation from overlapping
the destructive interval where those stores are removed.  The gate is keyed by
the resolved project path so equal display ids in different workspace roots do
not block each other.

The tombstone is deliberately process-lifetime state.  Once destructive work
has started, synchronous mutations may not recreate part of the project behind
the Library's back; only a retry of the deletion operation can enter again.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class ProjectMutationBlocked(RuntimeError):
    """The requested mutation targets a deleting or deleted project."""


@dataclass
class _ProjectState:
    active_mutations: int = 0
    deletion_active: bool = False
    tombstoned: bool = False
    confirmed_title: str | None = None


class ProjectDeletionLease:
    """Exclusive project lease; committing makes its tombstone permanent."""

    def __init__(self, state: _ProjectState) -> None:
        self._state = state
        self._committed = False

    @property
    def confirmed_title(self) -> str | None:
        return self._state.confirmed_title

    def commit(self, title: str) -> None:
        self._state.tombstoned = True
        self._state.confirmed_title = title
        self._committed = True


class ProjectOperationGate:
    """Serialize destructive deletion against project-scoped mutations."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._states: dict[str, _ProjectState] = {}

    @staticmethod
    def _key(project_path: str | Path) -> str:
        return str(Path(project_path).resolve())

    def unavailable(self, project_path: str | Path) -> bool:
        """Whether a creation candidate is reserved by deletion."""
        key = self._key(project_path)
        with self._condition:
            state = self._states.get(key)
            return bool(
                state and (state.deletion_active or state.tombstoned)
            )

    @contextmanager
    def mutation(self, project_path: str | Path) -> Iterator[None]:
        """Enter one ordinary synchronous write operation.

        Writes are serialized because the canonical state file is itself a
        single-writer resource.  None can start after deletion has declared
        intent, and deletion waits for the writer that entered first to leave.
        """
        key = self._key(project_path)
        with self._condition:
            state = self._states.setdefault(key, _ProjectState())
            while (
                state.active_mutations
                and not state.deletion_active
                and not state.tombstoned
            ):
                self._condition.wait()
            if state.deletion_active or state.tombstoned:
                raise ProjectMutationBlocked(
                    f"Project at {key!r} is being or has been deleted"
                )
            state.active_mutations += 1
        try:
            yield
        finally:
            with self._condition:
                state.active_mutations -= 1
                self._condition.notify_all()

    @contextmanager
    def deletion(self, project_path: str | Path) -> Iterator[ProjectDeletionLease]:
        """Enter exclusive deletion, waiting for older mutations to finish.

        Concurrent deletion retries serialize.  Until ``commit`` is called,
        leaving the context removes the provisional block (for example after
        a title-confirmation conflict or a running-job conflict).  Once
        committed, the tombstone remains even if storage cleanup raises, so a
        retry can finish cleanup but no ordinary write can recreate residue.
        """
        key = self._key(project_path)
        with self._condition:
            state = self._states.setdefault(key, _ProjectState())
            while state.deletion_active:
                self._condition.wait()
            state.deletion_active = True
            while state.active_mutations:
                self._condition.wait()
            lease = ProjectDeletionLease(state)
        try:
            yield lease
        finally:
            with self._condition:
                state.deletion_active = False
                if not lease._committed and not state.tombstoned:
                    state.confirmed_title = None
                self._condition.notify_all()


# One process-wide coordinator, matching the process-wide JobRunner.
project_operations = ProjectOperationGate()


__all__ = [
    "ProjectDeletionLease",
    "ProjectMutationBlocked",
    "ProjectOperationGate",
    "project_operations",
]
