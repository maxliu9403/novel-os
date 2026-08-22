"""Book-level orchestration for the Novel OS CLI.

`NovelOrchestrator` remains the owner of individual agent phases.  This module
owns only the durable run lifecycle: ordering, checkpoints, retries, gates,
approval policy, and compilation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from compile_book import gather, render_bytes
from llm_client import LLMError
from pipeline_models import ManifestStore, RunManifest, RunSpec, StageResult
from prompt_intake import ingest_prompt
from styles import StyleSheet


class PipelineError(RuntimeError):
    """A run stopped for a reason that should be visible to the CLI."""

    def __init__(self, message: str, *, blocked: bool = False):
        super().__init__(message)
        self.blocked = blocked


class PipelineRunner:
    def __init__(self, project_path: str | Path | None = None, *, orchestrator_factory: Optional[Callable[[str], Any]] = None):
        self.project_path = Path(project_path) if project_path else None
        self._orchestrator_factory = orchestrator_factory or self._default_orchestrator

    @staticmethod
    def _default_orchestrator(project_path: str):
        from orchestrator import NovelOrchestrator

        return NovelOrchestrator(project_path)

    @staticmethod
    def _run_dir(project: Path, run_id: str) -> Path:
        return project / "outputs" / "runs" / run_id

    @classmethod
    def _store(cls, project: Path, run_id: str) -> ManifestStore:
        return ManifestStore(cls._run_dir(project, run_id) / "run.json")

    def run(self, spec: RunSpec) -> RunManifest:
        project = Path(spec.project_path).resolve()
        spec.project_path = str(project)
        if spec.prompt_path != "-":
            spec.prompt_path = str(Path(spec.prompt_path).resolve())
        manifest = RunManifest.new(spec)
        store = self._store(project, manifest.run_id)
        self._prepare_run(manifest, store)
        return self._execute(manifest, project, store)

    def resume(
        self,
        run_id: str,
        project_path: str | Path | None = None,
        *,
        approval_policy: Optional[str] = None,
        approve_chapter: Optional[int] = None,
    ) -> RunManifest:
        project = Path(project_path or self.project_path or ".").resolve()
        store = self._store(project, run_id)
        manifest = store.load()
        manifest.spec.project_path = str(project)
        if manifest.status == "completed":
            return self._repair_completed_run(manifest, project, store)
        if approval_policy:
            manifest.spec.approval_policy = approval_policy
        if approve_chapter is not None:
            self._record_human_approval(manifest, project, store, approve_chapter)
        self._prepare_run(manifest, store)
        return self._execute(manifest, project, store)

    def _repair_completed_run(
        self,
        manifest: RunManifest,
        project: Path,
        store: ManifestStore,
    ) -> RunManifest:
        """Audit completed outputs and deterministically restore damaged finals."""
        repaired: list[int] = []
        for number in range(1, manifest.spec.num_chapters + 1):
            final_relative = self._chapter_stage(number, "final")
            promote = manifest.get("chapter.promote", number)
            if promote is None or promote.status != "done":
                raise ValueError(
                    f"Completed run {manifest.run_id} has no completed promotion "
                    f"for chapter {number}"
                )
            expected_final = promote.artifact_hashes.get(final_relative, "")
            if self._artifact_valid(project, final_relative, expected_final):
                continue

            candidate_relative = self._chapter_stage(number, "candidate_final")
            style = manifest.get("chapter.style", number)
            expected_candidate = (
                style.artifact_hashes.get(candidate_relative, "") if style else ""
            )
            if (
                style is None
                or style.status != "done"
                or not self._artifact_valid(project, candidate_relative, expected_candidate)
            ):
                raise ValueError(
                    f"Cannot repair chapter {number}: candidate-final checkpoint is not trusted"
                )
            if expected_final and expected_final != expected_candidate:
                raise ValueError(
                    f"Cannot repair chapter {number}: promoted and candidate hashes disagree"
                )

            final = self._copy_candidate_to_final(project, number)
            if self._sha256(final) != expected_candidate:
                raise ValueError(f"Cannot repair chapter {number}: copied final hash mismatch")
            decision = "Final artifact repaired from its trusted candidate-final checkpoint."
            if decision not in promote.decisions:
                promote.decisions.append(decision)
            self._write_stage_result(manifest, project, promote)
            repaired.append(number)

        compile_result = manifest.get("compile")
        compile_valid = bool(
            compile_result
            and compile_result.status == "done"
            and self._checkpoint_valid(project, compile_result)
        )
        if repaired or not compile_valid:
            self._compile_book(manifest, project)
            result = compile_result or StageResult(phase="compile", status="done")
            result.status = "done"
            result.error = None
            result.retryable = False
            result.finished_at = self._now()
            result.artifact_paths = [
                f"outputs/deliverables/book.{self._format_extension(fmt)}"
                for fmt in manifest.spec.output_formats
            ]
            self._require_files(project, result.artifact_paths)
            result.artifact_hashes = {
                path: self._sha256(project / path) for path in result.artifact_paths
            }
            decision = (
                "Repaired final artifacts and recompiled chapters "
                + ", ".join(str(number) for number in repaired)
                if repaired
                else "Recompiled missing or invalid deliverables from trusted final chapters."
            )
            if decision not in result.decisions:
                result.decisions.append(decision)
            self._save_stage(manifest, project, store, result, snapshot_state=True)
            self._event(
                manifest,
                "run.integrity_repaired",
                chapters=repaired,
                recompiled=True,
            )

        manifest.status = "completed"
        manifest.current_phase = "compile"
        manifest.current_chapter = None
        manifest.error = None
        store.save(manifest)
        return manifest

    def retry(
        self,
        run_id: str,
        *,
        phase: str,
        chapter: Optional[int] = None,
        project_path: str | Path | None = None,
        approval_policy: Optional[str] = None,
    ) -> RunManifest:
        """Retry the named failed/blocked checkpoint, then continue the run."""
        project = Path(project_path or self.project_path or ".").resolve()
        manifest = self._store(project, run_id).load()
        target = manifest.get(phase, chapter)
        if target is None:
            raise ValueError(f"Run {run_id} has no stage {RunManifest.stage_key(phase, chapter)}")
        if target.status not in {"retryable", "blocked", "failed"}:
            raise ValueError(f"Stage {RunManifest.stage_key(phase, chapter)} is {target.status}, not retryable")
        self._prepare_manual_retry(manifest, project, phase, chapter)
        self._store(project, run_id).save(manifest)
        return self.resume(
            run_id,
            project,
            approval_policy=approval_policy,
        )

    def inspect(self, run_id: str, project_path: str | Path | None = None) -> RunManifest:
        project = Path(project_path or self.project_path or ".").resolve()
        return self._store(project, run_id).load()

    def _prepare_run(self, manifest: RunManifest, store: ManifestStore) -> None:
        run_dir = store.path.parent
        (run_dir / "stages").mkdir(parents=True, exist_ok=True)
        if manifest.status in {"completed", "cancelled"}:
            return
        manifest.status = "running"
        manifest.error = None
        store.save(manifest)
        self._event(manifest, "run.started")

    def _execute(self, manifest: RunManifest, project: Path, store: ManifestStore) -> RunManifest:
        # If one completed checkpoint is stale, every later stage is derived
        # from stale input and must be replayed as well.
        self._rerun_started = False
        self._last_valid_state_snapshot = ""
        self._active_orchestrator = None
        if manifest.spec.model:
            os.environ["NOVEL_OS_MODEL"] = manifest.spec.model
        try:
            self._stage(
                manifest,
                project,
                store,
                "intake",
                None,
                lambda: ingest_prompt(
                    project,
                    manifest.spec.prompt_path,
                    self._brief_overrides(manifest.spec),
                ),
                lambda _value: self._require_files(
                    project,
                    ["outputs/input/prompt.md", "outputs/input/brief.json", "outputs/story_bible.md"],
                ),
                ["outputs/input/prompt.md", "outputs/input/brief.json"],
            )
            self._resolve_spec_from_brief(manifest, project, store)

            if manifest.spec.dry_run:
                manifest.status = "completed"
                manifest.current_phase = "intake"
                store.save(manifest)
                self._event(manifest, "run.dry_run_completed")
                return manifest

            orchestrator = self._orchestrator_factory(str(project))
            setattr(orchestrator, "raise_llm_errors", True)
            self._active_orchestrator = orchestrator
            self._stage(
                manifest,
                project,
                store,
                "outline",
                None,
                lambda: orchestrator.plan_outline(
                    manifest.spec.num_chapters,
                    manifest.spec.target_words,
                ),
                lambda _value: self._require_files(
                    project,
                    ["outputs/outline.md", "outputs/input/foundation.json"],
                ),
                [
                    "outputs/outline.md",
                    "outputs/outline.json",
                    "outputs/input/foundation.json",
                    "outputs/story_bible.md",
                ],
            )

            for chapter in range(1, manifest.spec.num_chapters + 1):
                self._run_chapter(manifest, project, store, orchestrator, chapter)

            self._stage(
                manifest,
                project,
                store,
                "book.check",
                None,
                lambda: orchestrator.run_checks(None),
                lambda value: self._check_result(value, "whole-book continuity check"),
                [],
            )

            self._stage(
                manifest,
                project,
                store,
                "compile",
                None,
                lambda: self._compile_book(manifest, project),
                lambda value: bool(value),
                [
                    f"outputs/deliverables/book.{self._format_extension(fmt)}"
                    for fmt in manifest.spec.output_formats
                ],
            )
            manifest.status = "completed"
            manifest.error = None
            store.save(manifest)
            self._event(manifest, "run.completed")
            return manifest
        except PipelineError as exc:
            manifest.status = "paused" if exc.blocked else "failed"
            manifest.error = str(exc)
            store.save(manifest)
            self._event(manifest, "run.paused" if exc.blocked else "run.failed", error=str(exc))
            return manifest
        except Exception as exc:  # noqa: BLE001 - manifest must capture all run failures
            manifest.status = "failed"
            manifest.error = f"{type(exc).__name__}: {exc}"
            store.save(manifest)
            self._event(manifest, "run.failed", error=manifest.error)
            return manifest

    def _run_chapter(self, manifest: RunManifest, project: Path, store: ManifestStore, orchestrator: Any, number: int) -> None:
        spec = manifest.spec
        self._stage(
            manifest, project, store, "chapter.plan", number,
            lambda: orchestrator.plan_chapter(number, dry_run=False),
            lambda _value: self._require_files(project, [self._chapter_outline(number)]),
            [self._chapter_outline(number)],
        )
        self._stage(
            manifest, project, store, "chapter.write", number,
            lambda: orchestrator.write_chapter(number, dry_run=False),
            lambda _value: self._require_files(project, [self._chapter_stage(number, "draft")]),
            [self._chapter_stage(number, "draft")],
        )
        self._stage(
            manifest, project, store, "chapter.check.pre", number,
            lambda: orchestrator.run_checks(number),
            lambda value: self._check_result(value, "pre-edit continuity check"),
            [],
        )
        self._stage(
            manifest, project, store, "chapter.edit", number,
            lambda: orchestrator.edit_chapter(number, spec.edit_mode, dry_run=False),
            lambda _value: self._require_files(project, [self._chapter_stage(number, "revised")]),
            [self._chapter_stage(number, "revised")],
        )
        self._stage(
            manifest, project, store, "chapter.check.post", number,
            lambda: orchestrator.run_checks(number),
            lambda value: self._check_result(value, "post-edit continuity check"),
            [],
        )
        self._stage(
            manifest, project, store, "chapter.validate", number,
            lambda: orchestrator.validate_chapter(number, dry_run=False),
            lambda _value: self._validate_guardian(project, number),
            [self._chapter_report(number, "continuity")],
        )
        self._stage(
            manifest, project, store, "chapter.style", number,
            lambda: self._curate(orchestrator, number),
            lambda _value: self._require_files(
                project,
                [self._chapter_report(number, "style"), self._chapter_stage(number, "candidate_final")],
            ),
            [self._chapter_report(number, "style"), self._chapter_stage(number, "candidate_final")],
        )
        self._stage(
            manifest, project, store, "chapter.promote", number,
            lambda: self._promote(project, number, spec.approval_policy),
            lambda value: bool(value),
            [self._chapter_stage(number, "final" if spec.approval_policy == "auto" else "candidate_final")],
        )

    def _stage(
        self,
        manifest: RunManifest,
        project: Path,
        store: ManifestStore,
        phase: str,
        chapter: Optional[int],
        operation: Callable[[], Any],
        validator: Callable[[Any], Any],
        artifacts: list[str],
    ) -> StageResult:
        previous = manifest.get(phase, chapter)
        if previous and previous.status == "done" and not self._rerun_started:
            if self._checkpoint_valid(project, previous):
                if previous.state_snapshot_path:
                    self._last_valid_state_snapshot = previous.state_snapshot_path
                return previous
            if self._last_valid_state_snapshot:
                self._restore_state(project, self._last_valid_state_snapshot)
            elif len(manifest.stages) > 1:
                raise PipelineError(
                    f"Checkpoint {RunManifest.stage_key(phase, chapter)} changed and no prior state snapshot is available",
                    blocked=True,
                )
            self._rerun_started = True
        elif (
            previous
            and previous.status in {"running", "retryable", "blocked", "failed", "pending"}
            and self._last_valid_state_snapshot
            and not self._rerun_started
        ):
            self._restore_state(project, self._last_valid_state_snapshot)
            self._rerun_started = True

        max_attempts = manifest.spec.max_retries + 1
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            result = StageResult(
                phase=phase,
                chapter=chapter,
                status="running",
                attempt=attempt,
                started_at=self._now(),
            )
            self._save_stage(manifest, project, store, result)
            self._event(manifest, "stage.started", phase=phase, chapter=chapter, attempt=attempt)
            try:
                value = operation()
                validator(value)
                result.status = "done"
                result.finished_at = self._now()
                result.artifact_paths = [path for path in artifacts if (project / path).exists()]
                result.artifact_hashes = {
                    path: self._sha256(project / path) for path in result.artifact_paths
                }
                if phase == "chapter.promote" and manifest.spec.approval_policy == "auto":
                    result.decisions.append(
                        "Candidate promoted automatically under approval_policy=auto."
                    )
                result.provider, result.model = self._runtime_model()
                self._save_stage(manifest, project, store, result, snapshot_state=True)
                self._last_valid_state_snapshot = result.state_snapshot_path
                self._event(manifest, "stage.done", phase=phase, chapter=chapter, attempt=attempt)
                return result
            except PipelineError as exc:
                result.status = "blocked" if exc.blocked else "failed"
                result.error = str(exc)
                result.finished_at = self._now()
                self._save_stage(manifest, project, store, result)
                self._event(manifest, "stage.blocked" if exc.blocked else "stage.failed", phase=phase, chapter=chapter, error=str(exc))
                raise
            except Exception as exc:  # noqa: BLE001 - retryable classification is centralized here
                last_error = f"{type(exc).__name__}: {exc}"
                result.error = last_error
                result.retryable = attempt < max_attempts and self._is_retryable(exc)
                result.status = "retryable" if result.retryable else "failed"
                result.finished_at = self._now()
                self._save_stage(manifest, project, store, result)
                self._event(manifest, "stage.retry" if result.retryable else "stage.failed", phase=phase, chapter=chapter, error=last_error)
                if result.retryable:
                    delay = min(
                        manifest.spec.retry_backoff_seconds * (2 ** (attempt - 1)),
                        30.0,
                    )
                    if delay:
                        time.sleep(delay)
                else:
                    raise PipelineError(last_error) from exc
        raise PipelineError(last_error or f"Stage {phase} failed")

    @staticmethod
    def _brief_overrides(spec: RunSpec) -> Dict[str, Any]:
        return {
            "title": spec.title,
            "genre": spec.genre,
            "author": spec.author,
            "pov": spec.pov,
            "chapters": spec.num_chapters,
            "words": spec.target_words,
        }

    @staticmethod
    def _resolve_spec_from_brief(manifest: RunManifest, project: Path, store: ManifestStore) -> None:
        brief_path = project / "outputs/input/brief.json"
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
        manifest.spec.num_chapters = int(
            manifest.spec.num_chapters or brief.get("target_chapters") or 32
        )
        manifest.spec.target_words = int(
            manifest.spec.target_words or brief.get("target_words") or 80000
        )
        if manifest.spec.num_chapters < 1:
            raise PipelineError("Resolved chapter count must be at least 1")
        if manifest.spec.target_words < manifest.spec.num_chapters:
            raise PipelineError("Resolved target words must be at least the chapter count")
        store.save(manifest)

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _event(manifest: RunManifest, event: str, **fields: Any) -> None:
        path = Path(manifest.spec.project_path) / "outputs" / "runs" / manifest.run_id / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"event": event, "run_id": manifest.run_id, "at": PipelineRunner._now(), **fields}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _files_exist(project: Path, paths: list[str]) -> bool:
        return all(
            (project / path).is_file() and (project / path).stat().st_size > 0
            for path in paths
        )

    @classmethod
    def _artifact_valid(cls, project: Path, relative: str, expected_hash: str) -> bool:
        path = project / relative
        return bool(
            expected_hash
            and path.is_file()
            and path.stat().st_size > 0
            and cls._sha256(path) == expected_hash
        )

    @classmethod
    def _checkpoint_valid(cls, project: Path, result: StageResult) -> bool:
        if not cls._files_exist(project, result.artifact_paths):
            return False
        artifacts_valid = all(
            cls._artifact_valid(project, path, expected)
            for path, expected in result.artifact_hashes.items()
        )
        if not artifacts_valid:
            return False
        if result.state_snapshot_path:
            return cls._artifact_valid(
                project,
                result.state_snapshot_path,
                result.state_snapshot_hash,
            )
        return True

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _save_stage(
        self,
        manifest: RunManifest,
        project: Path,
        store: ManifestStore,
        result: StageResult,
        *,
        snapshot_state: bool = False,
    ) -> None:
        if snapshot_state:
            state = project / "outputs/state/story_state.json"
            if state.exists():
                snapshot = (
                    self._run_dir(project, manifest.run_id)
                    / "stages"
                    / f"{self._stage_slug(result)}.state.json"
                )
                self._atomic_copy(state, snapshot)
                result.state_snapshot_path = str(snapshot.relative_to(project))
                result.state_snapshot_hash = self._sha256(snapshot)
        manifest.record(result)
        store.save(manifest)
        self._write_stage_result(manifest, project, result)

    def _prepare_manual_retry(
        self,
        manifest: RunManifest,
        project: Path,
        phase: str,
        chapter: Optional[int],
    ) -> None:
        """Establish the trusted input boundary for an explicit stage retry.

        Deterministic check stages are read-only. If one blocks, the author or
        a state migration may repair the manuscript and StoryState before
        retrying. Adopt both as the new checkpoint for the producer stage so
        resume cannot restore the stale snapshot that caused the old finding.

        Guardian validation is different: a failed Guardian call may already
        have mutated StoryState, so its retry continues to restore the clean
        post-edit boundary below.
        """
        check_inputs = {
            "chapter.check.pre": ("chapter.write", "draft"),
            "chapter.check.post": ("chapter.edit", "revised"),
            "book.check": ("chapter.promote", None),
        }
        if phase in check_inputs and (chapter is not None or phase == "book.check"):
            producer_phase, manuscript_stage = check_inputs[phase]
            if phase == "book.check":
                producers = [
                    result for result in manifest.stages.values()
                    if result.phase == producer_phase and result.status == "done"
                ]
                producer = max(producers, key=lambda result: result.chapter or 0, default=None)
            else:
                producer = manifest.get(producer_phase, chapter)
            if producer is None or producer.status != "done":
                raise ValueError(
                    f"Cannot retry {phase}: target {chapter if chapter is not None else 'book'} has no completed "
                    f"{producer_phase} stage"
                )

            manuscript_relative = ""
            if manuscript_stage is not None:
                manuscript_relative = self._chapter_stage(chapter, manuscript_stage)
                manuscript = project / manuscript_relative
                if not manuscript.exists():
                    raise ValueError(f"Cannot retry {phase}: {manuscript_relative} is missing")
            state = project / "outputs/state/story_state.json"
            if not state.exists():
                raise ValueError(f"Cannot retry {phase}: current StoryState is missing")

            snapshot = (
                project / producer.state_snapshot_path
                if producer.state_snapshot_path
                else self._run_dir(project, manifest.run_id)
                / "stages"
                / f"{self._stage_slug(producer)}.state.json"
            )
            self._atomic_copy(state, snapshot)
            producer.state_snapshot_path = str(snapshot.relative_to(project))
            producer.state_snapshot_hash = self._sha256(snapshot)
            if manuscript_relative:
                producer.artifact_hashes[manuscript_relative] = self._sha256(manuscript)
            producer.decisions.append(
                f"Current manuscript and StoryState adopted for explicit {phase} retry."
            )
            self._write_stage_result(manifest, project, producer)
            return

        if phase != "chapter.validate" or chapter is None:
            return
        revised_relative = self._chapter_stage(chapter, "revised")
        revised = project / revised_relative
        if not revised.exists():
            raise ValueError(f"Cannot retry validation: {revised_relative} is missing")
        edit = manifest.get("chapter.edit", chapter)
        if edit is None or edit.status != "done":
            raise ValueError(f"Cannot retry validation: chapter {chapter} has no completed edit stage")
        edit.artifact_hashes[revised_relative] = self._sha256(revised)
        edit.decisions.append("Manual revised-manuscript edit accepted for validation retry.")
        self._write_stage_result(manifest, project, edit)
        if edit.state_snapshot_path:
            self._restore_state(project, edit.state_snapshot_path)

        post_check = manifest.get("chapter.check.post", chapter)
        if post_check:
            post_check.status = "pending"
            post_check.error = None
            post_check.retryable = False
            post_check.finished_at = ""
            post_check.state_snapshot_path = ""
            post_check.state_snapshot_hash = ""
            self._write_stage_result(manifest, project, post_check)

    def _write_stage_result(self, manifest: RunManifest, project: Path, result: StageResult) -> None:
        path = (
            self._run_dir(project, manifest.run_id)
            / "stages"
            / f"{self._stage_slug(result)}.json"
        )
        self._atomic_json(path, result.to_dict())

    @staticmethod
    def _stage_slug(result: StageResult) -> str:
        phase = re.sub(r"[^a-zA-Z0-9_-]+", "-", result.phase).strip("-")
        return f"{phase}-chapter-{result.chapter:03d}" if result.chapter is not None else phase

    @staticmethod
    def _atomic_copy(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f"{target.name}.tmp-", dir=str(target.parent))
        os.close(fd)
        try:
            shutil.copyfile(source, temp_name)
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f"{path.name}.tmp-", dir=str(path.parent), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _restore_state(self, project: Path, snapshot_relative: str) -> None:
        snapshot = project / snapshot_relative
        state = project / "outputs/state/story_state.json"
        if not snapshot.exists():
            raise PipelineError(f"State snapshot is missing: {snapshot_relative}", blocked=True)
        self._atomic_copy(snapshot, state)
        self._reload_active_state(project)

    def _reload_active_state(self, project: Path) -> None:
        orchestrator = getattr(self, "_active_orchestrator", None)
        if orchestrator is not None:
            from state_manager import StoryState

            orchestrator.state = StoryState(str(project))

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        text = str(exc).lower()
        non_retryable = (
            "no llm provider configured",
            "requires novel_os_",
            "api key is not set",
            "unknown provider",
            "agent prompt not found",
            "missing [",
            "empty revised chapter",
        )
        if any(marker in text for marker in non_retryable):
            return False
        if isinstance(exc, (LLMError, TimeoutError, ConnectionError)):
            return True
        name = type(exc).__name__.lower()
        return any(marker in name for marker in ("timeout", "connection", "ratelimit", "apierror", "serviceunavailable"))

    @staticmethod
    def _require_files(project: Path, paths: list[str]) -> None:
        invalid = [
            path
            for path in paths
            if not (project / path).is_file() or (project / path).stat().st_size == 0
        ]
        if invalid:
            raise PipelineError(
                f"Missing or empty required artifacts: {', '.join(invalid)}"
            )

    @staticmethod
    def _check_result(value: Any, label: str) -> None:
        if isinstance(value, int) and value > 0:
            raise PipelineError(f"{label} found {value} critical issue(s)", blocked=True)

    def _validate_guardian(self, project: Path, number: int) -> None:
        report = project / PipelineRunner._chapter_report(number, "continuity")
        PipelineRunner._require_files(project, [str(report.relative_to(project))])
        text = report.read_text(encoding="utf-8")
        match = re.search(r"status\s*[:*]+\s*(PASS|WARNING|FAIL)", text, re.IGNORECASE)
        if not match:
            raise PipelineError(f"Continuity report for chapter {number} has no PASS/WARNING/FAIL status", blocked=True)
        status = match.group(1).upper()
        from state_manager import StoryState

        state = StoryState(str(project))
        chapter = state.get_chapter(number)
        if chapter and chapter.continuity_checks.get("status") != status:
            chapter.continuity_checks["status"] = status
            state.save_state()
            self._reload_active_state(project)
        if status == "FAIL":
            raise PipelineError(f"Continuity Guardian blocked chapter {number}", blocked=True)

    @staticmethod
    def _curate(orchestrator: Any, number: int) -> Any:
        method = getattr(orchestrator, "curate_chapter", None)
        if method is None:
            raise PipelineError("Style Curator is not available in this orchestrator")
        return method(number, dry_run=False)

    def _promote(self, project: Path, number: int, policy: str) -> bool:
        source = project / PipelineRunner._chapter_stage(number, "candidate_final")
        if not source.exists():
            raise PipelineError(f"Cannot promote chapter {number}: candidate final is missing")
        if policy != "auto":
            raise PipelineError(
                f"Chapter {number} is ready for human review; resume with --approval auto after approval",
                blocked=True,
            )
        PipelineRunner._copy_candidate_to_final(project, number)
        self._reload_active_state(project)
        return True

    @staticmethod
    def _copy_candidate_to_final(project: Path, number: int) -> Path:
        source = project / PipelineRunner._chapter_stage(number, "candidate_final")
        if not source.is_file():
            raise PipelineError(f"Cannot approve chapter {number}: candidate final is missing")
        if source.stat().st_size == 0 or not source.read_text(encoding="utf-8").strip():
            raise PipelineError(f"Cannot approve chapter {number}: candidate final is empty")
        target = project / PipelineRunner._chapter_stage(number, "final")
        source_size = source.stat().st_size
        source_hash = PipelineRunner._sha256(source)
        PipelineRunner._atomic_copy(source, target)
        if (
            not target.is_file()
            or target.stat().st_size != source_size
            or PipelineRunner._sha256(target) != source_hash
        ):
            raise PipelineError(
                f"Cannot approve chapter {number}: copied final failed integrity verification"
            )
        from state_manager import StoryState

        state = StoryState(str(project))
        chapter = state.get_chapter(number)
        if chapter:
            chapter.status = "complete"
            state.save_state()
        return target

    def _record_human_approval(
        self,
        manifest: RunManifest,
        project: Path,
        store: ManifestStore,
        number: int,
    ) -> None:
        current = manifest.get("chapter.promote", number)
        if current is None or current.status != "blocked":
            raise ValueError(f"Chapter {number} is not waiting for review in run {manifest.run_id}")
        candidate_relative = self._chapter_stage(number, "candidate_final")
        candidate = project / candidate_relative
        if not candidate.exists():
            raise ValueError(f"Chapter {number} candidate final is missing")
        style = manifest.get("chapter.style", number)
        if style:
            # Human edits are an expected part of review, not checkpoint
            # corruption. Approval makes the reviewed candidate the new trusted
            # Style-stage artifact before resume validates prior checkpoints.
            style.artifact_hashes[candidate_relative] = self._sha256(candidate)
            style.decisions.append("Human reviewed the candidate-final artifact.")
        target = self._copy_candidate_to_final(project, number)
        relative = str(target.relative_to(project))
        result = StageResult(
            phase="chapter.promote",
            chapter=number,
            status="done",
            attempt=current.attempt,
            artifact_paths=[relative],
            artifact_hashes={relative: self._sha256(target)},
            decisions=["Human approval recorded by CLI; candidate promoted to canonical final."],
            provider=style.provider if style else "",
            model=style.model if style else "",
            started_at=current.started_at or self._now(),
            finished_at=self._now(),
        )
        self._save_stage(manifest, project, store, result, snapshot_state=True)
        manifest.error = None
        self._event(manifest, "chapter.human_approved", chapter=number)

    def _compile_book(self, manifest: RunManifest, project: Path) -> bool:
        state_path = project / "outputs/state/story_state.json"
        self._require_files(project, [str(state_path.relative_to(project))])
        from state_manager import StoryState

        state = StoryState(str(project))
        chapters = []
        for number in range(1, manifest.spec.num_chapters + 1):
            path = project / self._chapter_stage(number, "final")
            if not path.is_file():
                raise PipelineError(
                    f"Chapter {number} has no approved final artifact; use --approval auto or resume after review",
                    blocked=True,
                )
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                raise PipelineError(
                    f"Chapter {number} approved final artifact is empty",
                    blocked=True,
                )
            chapter = state.get_chapter(number)
            chapters.append({
                "number": number,
                "title": chapter.title if chapter else f"Chapter {number}",
                "text": text,
            })
        book = gather(
            title=state.metadata.get("title", "Untitled"),
            author=state.metadata.get("author", ""),
            genre=state.metadata.get("genre", ""),
            chapters=chapters,
        )
        if len(book.chapters) != manifest.spec.num_chapters:
            raise PipelineError(
                "Compiler gathered "
                f"{len(book.chapters)} of {manifest.spec.num_chapters} chapters",
                blocked=True,
            )
        sheet = StyleSheet.from_dict(state.compile_styles)
        output_dir = project / "outputs/deliverables"
        output_dir.mkdir(parents=True, exist_ok=True)
        for fmt in manifest.spec.output_formats:
            extension = self._format_extension(fmt)
            output = output_dir / f"book.{extension}"
            output.write_bytes(render_bytes(book, sheet, fmt))
        return True

    def _runtime_model(self) -> tuple[str, str]:
        llm = getattr(getattr(self, "_active_orchestrator", None), "_llm", None)
        if llm is not None:
            return str(getattr(llm, "provider", "")), str(getattr(llm, "model", ""))
        return os.environ.get("NOVEL_OS_LLM_PROVIDER", ""), os.environ.get("NOVEL_OS_MODEL", "")

    @staticmethod
    def _chapter_outline(number: int) -> str:
        return f"outputs/chapter_{number:03d}_outline.md"

    @staticmethod
    def _chapter_stage(number: int, stage: str) -> str:
        return f"outputs/manuscript/chapter_{number:03d}_{stage}.md"

    @staticmethod
    def _chapter_report(number: int, report: str) -> str:
        return f"outputs/feedback/chapter_{number:03d}_{report}_report.md"

    @staticmethod
    def _format_extension(fmt: str) -> str:
        return {"markdown": "md"}.get(fmt, fmt)
