"""Project-wide permanent deletion orchestration.

The story engine is file-canonical, while the API also owns query projections,
uploaded media and ephemeral rewrite previews.  Permanent deletion belongs in
one service so callers cannot accidentally remove only the Library card while
leaving the manuscript or private media behind.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import db, tenancy
from .jobs import JobRunner, ProjectJobsRunning
from .media import MediaStore
from .project_operations import project_operations
from .services import ProjectNotFound, ProjectService


class ProjectDeletionError(RuntimeError):
    """Permanent deletion could not remove every project-owned resource."""


class ProjectDeletionConfirmationError(RuntimeError):
    """The supplied title no longer matches the canonical project title."""

    def __init__(self, expected_title: str) -> None:
        self.expected_title = expected_title
        super().__init__("Project title confirmation does not match canonical state")


def _tree_stats(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    files = 0
    size = 0
    for item in path.rglob("*"):
        if item.is_file() or item.is_symlink():
            files += 1
            size += item.lstat().st_size
    return files, size


class ProjectDeletionService:
    def __init__(
        self,
        projects: ProjectService,
        media_store: MediaStore,
        jobs: JobRunner,
    ) -> None:
        self.projects = projects
        self.media_store = media_store
        self.jobs = jobs

    @property
    def _workspace_id(self) -> str:
        return (
            self.projects.workspace.id
            if self.projects.workspace is not None
            else tenancy.DEFAULT_WORKSPACE_ID
        )

    def _resolve(self, project_id: str) -> tuple[Path, bool]:
        """Resolve through the same tenant boundary as all project services.

        A missing canonical state is allowed for DELETE retries, but the id and
        ownership boundary are still enforced before any path is touched.
        """
        try:
            safe_path = tenancy.project_dir(
                self.projects.root, project_id, self.projects.workspace
            )
        except tenancy.TenancyError as exc:
            raise ProjectNotFound(project_id) from exc

        # The shared resolver guarantees the resolved destination stays inside
        # the workspace.  Deletion adds one stricter rule: a user-visible
        # project entry may not be a symlink alias to a different project,
        # because recursively deleting the resolved alias would remove the
        # target project's canonical directory.
        logical_path = (
            tenancy.workspace_dir(self.projects.root, self.projects.workspace)
            / project_id
        )
        if logical_path.is_symlink():
            raise ProjectNotFound(project_id)

        owner = db.project_workspace(project_id)
        if owner is not None and owner != self._workspace_id:
            raise ProjectNotFound(project_id)

        try:
            return self.projects.project_path(project_id), True
        except ProjectNotFound:
            return safe_path, False

    @staticmethod
    def _metadata(path: Path, project_id: str) -> tuple[str, int]:
        state_file = path / "outputs" / "state" / "story_state.json"
        if not state_file.is_file():
            return project_id, 0
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return project_id, 0
        metadata = state.get("metadata") or {}
        chapters = state.get("chapters") or {}
        return str(metadata.get("title") or project_id), len(chapters)

    def inventory(self, project_id: str, *, require_project: bool) -> dict:
        path, canonical_exists = self._resolve(project_id)
        counts = db.project_data_counts(project_id)
        database_exists = any(counts.values())
        # For a retry after partial deletion, only inspect/remove a residual
        # directory when canonical state or database ownership proves it is a
        # Novel OS project.  Never treat an arbitrary same-named folder as one.
        inspect_project_dir = canonical_exists or database_exists
        project_files, project_bytes = (
            _tree_stats(path) if inspect_project_dir else (0, 0)
        )
        media_files, media_bytes = self.media_store.project_stats(project_id)
        exists = canonical_exists or database_exists or bool(media_files)
        if require_project and not exists:
            raise ProjectNotFound(project_id)
        title, chapter_count = self._metadata(path, project_id)
        return {
            "project_id": project_id,
            "title": title,
            "chapter_count": chapter_count,
            "exists": exists,
            "counts": counts,
            "project_files": project_files,
            "project_bytes": project_bytes,
            "media_files": media_files,
            "media_bytes": media_bytes,
        }

    def preview(self, project_id: str) -> dict:
        path, _canonical_exists = self._resolve(project_id)
        inventory = self.inventory(project_id, require_project=True)
        running = self.jobs.running_for_project(
            project_id, project_path=str(path)
        )
        return {
            **inventory,
            "running_job_ids": [str(job["job_id"]) for job in running],
            "can_delete": not running,
        }

    def delete(self, project_id: str, *, confirm_title: str) -> dict:
        path, _canonical_exists = self._resolve(project_id)

        # The deletion lease is the cross-store seam: every HTTP mutation has
        # left before this block starts, and new ones are refused until the
        # operation either aborts safely or commits a permanent tombstone.
        with project_operations.deletion(path) as lease:
            path, canonical_exists = self._resolve(project_id)
            before = self.inventory(project_id, require_project=False)
            canonical_title, _chapter_count = self._metadata(path, project_id)
            expected_title = (
                canonical_title
                if canonical_exists
                else lease.confirmed_title or before["title"]
            )
            # A no-data retry after process restart has no canonical title left
            # to compare.  It remains safe and idempotent as long as the caller
            # still supplied the required confirmation parameter.
            if before["exists"] or lease.confirmed_title is not None:
                if confirm_title != expected_title:
                    raise ProjectDeletionConfirmationError(expected_title)

            # This check and fence are one JobRunner lock operation.  Once it
            # succeeds, existing jobs cannot still be writing and new route
            # jobs carrying this resolved path are refused.  Commit before the
            # first destructive side effect so cleanup failures stay retryable
            # without reopening the project to ordinary mutations.
            self.jobs.prepare_project_deletion(project_id, str(path))
            lease.commit(expected_title)

            project_deleted = False
            media_deleted = False
            try:
                database_exists = any(before["counts"].values())
                if (canonical_exists or database_exists) and path.exists():
                    shutil.rmtree(path)
                    project_deleted = True
                media_deleted = self.media_store.delete_project(project_id)
                db.project_data_delete(project_id)
                cleared_previews = ProjectService.clear_consequence_previews(project_id)
            except Exception as exc:  # noqa: BLE001 - retain both safety fences
                raise ProjectDeletionError(
                    f"Project {project_id!r} was not completely deleted: {exc}"
                ) from exc

            after = self.inventory(project_id, require_project=False)
            residual = (
                after["exists"]
                or any(after["counts"].values())
                or after["project_files"] > 0
                or after["project_bytes"] > 0
                or after["media_files"] > 0
                or after["media_bytes"] > 0
            )
            if residual:
                raise ProjectDeletionError(
                    f"Project {project_id!r} still has residual inventory: {after!r}"
                )

            deleted_anything = before["exists"] or project_deleted or media_deleted
            return {
                "project_id": project_id,
                "status": "deleted" if deleted_anything else "already_deleted",
                "before": before,
                "after": after,
                "cleared_consequence_previews": cleared_previews,
            }


__all__ = [
    "ProjectDeletionError",
    "ProjectDeletionConfirmationError",
    "ProjectDeletionService",
    "ProjectJobsRunning",
]
