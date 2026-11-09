"""Identify and preserve an uncommitted legacy outline before staged recovery."""

from __future__ import annotations

import json
from pathlib import Path

try:
    from .artifacts import ArtifactError, ArtifactStore
except ImportError:
    from artifacts import ArtifactError, ArtifactStore


def _has_only_intake_artifacts(project: Path) -> bool:
    """Validate immutable history without creating locks or writing projections."""
    store = ArtifactStore(project)
    for directory in (store.artifact_root, store.blob_root):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            return False
    for path in (store.revisions_path, store.heads_path):
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_size > 8_000_000)):
            return False
    revisions = {revision.revision_id: revision for revision in store.history()}
    # Immutable revisions survive missing projections and rolled-back state.
    # Even an unpromoted draft means the original planning must be preserved.
    if any(revision.chapter > 0 for revision in revisions.values()):
        return False
    # get_head() creates a lock file. Reuse the store's pure validator so GET
    # eligibility checks remain read-only, including orphan/corrupt heads.
    store._load_heads(revisions)
    known_blobs = {revision.sha256 for revision in revisions.values()}
    if store.blob_root.exists() and any(path.name not in known_blobs for path in store.blob_root.iterdir()):
        return False
    for revision in revisions.values():
        path = store.blob_root / revision.sha256
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8_000_000:
            return False
        store.read_text(revision.revision_id)
    return True


def can_upgrade_planning(project: Path, manifest: dict) -> bool:
    """Read-only eligibility; the executor must recheck under its project lock.

    Never infer a safe empty story from missing/corrupt state or a missing
    manifest stage. An upgrade is only for a failed/paused *outline* attempt.
    """
    if manifest.get("status") not in {"failed", "paused"}:
        return False
    spec = manifest.get("spec", {})
    if spec.get("outline_planning_mode", "legacy") != "legacy" or spec.get("dry_run"):
        return False
    stages = manifest.get("stages", {})
    outline = stages.get("outline", {})
    if (manifest.get("current_phase") != "outline"
            or outline.get("status") not in {"failed", "blocked", "retryable"}
            or any(key not in {"intake", "outline"} for key in stages)):
        return False
    state_dir = project / "outputs" / "state"
    state_path = state_dir / "story_state.json"
    try:
        inputs = project / "outputs" / "input"
        foundation = inputs / "foundation.json"
        if inputs.is_symlink() or foundation.is_symlink() or foundation.exists():
            return False
        if any(p.is_symlink() for p in (project / "outputs", state_dir, state_path)):
            return False
        if not state_path.is_file() or state_path.stat().st_size > 8_000_000:
            return False
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not all(isinstance(state.get(key), dict) for key in ("chapters", "characters", "plot_threads")):
            return False
        if "timeline" in state and not isinstance(state["timeline"], list):
            return False
        # Characters/threads can be installed by the legacy planner even if its
        # manifest checkpoint was interrupted. Such a story is not empty.
        if any(state.get(key) for key in ("chapters", "characters", "plot_threads", "timeline")):
            return False
        ledger = state_dir / "canon_ledger.jsonl"
        if ledger.is_symlink() or (ledger.exists() and (not ledger.is_file() or ledger.stat().st_size > 0)):
            return False
        for name in ("foundation_journal", "foundation_receipts", "promotion_journal", "promotion_receipts"):
            path = state_dir / name
            if path.is_symlink() or (path.exists() and (not path.is_dir() or next(path.iterdir(), None) is not None)):
                return False
        for relative in ("chapters", "outputs/chapters", "outputs/manuscript"):
            path = project / relative
            if path.is_symlink() or (path.exists() and (not path.is_dir() or next(path.iterdir(), None) is not None)):
                return False
        if next((project / "outputs").glob("chapter_*_outline.md"), None) is not None:
            return False
        if not _has_only_intake_artifacts(project):
            return False
    except (ArtifactError, OSError, ValueError, TypeError):
        return False
    return True


def preserve_outline_candidates(project: Path, run_directory: Path) -> None:
    from core.outline_planning import atomic_write
    from core.safe_project_io import read_project_bytes

    for name in ("outline.md", "outline.json"):
        original = read_project_bytes(project, ("outputs", name), missing_ok=True)
        if original is None:
            continue
        backup = run_directory / f"pre-staged-{name}"
        saved = read_project_bytes(project, backup.relative_to(project).parts, missing_ok=True)
        if saved is not None and saved != original:
            raise ValueError(f"Preserved {name} differs from the legacy candidate")
        if saved is None:
            atomic_write(backup, original.decode("utf-8"))
