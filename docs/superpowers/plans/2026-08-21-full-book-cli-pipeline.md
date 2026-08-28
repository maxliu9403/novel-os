# Full Book CLI Pipeline Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with tests before production changes. Do not create a commit unless the user explicitly requests one.

**Goal:** Add a resumable CLI that accepts one prompt and drives Novel OS from story intake through chapter drafting, editing, validation, style curation, and compiled manuscript output.

**Architecture:** Keep `NovelOrchestrator` as the single-stage engine. Add a focused `core/pipeline_runner.py` that owns the run manifest, checkpoints, retries, approval policy, and sequential chapter loop. Persist raw input and structured brief artifacts in the project filesystem so CLI and future API/UI share the same contract.

**Tech Stack:** Python standard library dataclasses/JSON/argparse, existing `StoryState`, `NovelOrchestrator`, `LLMClient`, continuity engine, and `core.compile_book`.

---

### Task 1: Run contracts and persistence primitives

**Files:**
- Create: `core/pipeline_models.py`
- Create: `tests/test_pipeline_models.py`

- [x] Define `RunSpec`, `StageResult`, and `RunManifest` with JSON round-tripping, stable statuses, approval policy, retry limit, and artifact metadata.
- [x] Test that a manifest survives serialization and that stage status transitions reject invalid values.
- [x] Test atomic JSON writes through the manifest store helper.

### Task 2: Prompt intake and structured brief

**Files:**
- Create: `core/prompt_intake.py`
- Create: `tests/test_prompt_intake.py`

- [x] Read a UTF-8 prompt file or stdin, preserving the exact source under `outputs/input/prompt.md`.
- [x] Build a deterministic brief from explicit CLI overrides and prompt text, save `brief.json`, and update metadata/story bible without replacing the raw prompt.
- [x] Test file input, stdin input, override precedence, and secret-free persisted artifacts.

### Task 3: Resumable pipeline runner

**Files:**
- Create: `core/pipeline_runner.py`
- Create: `tests/test_pipeline_runner.py`

- [x] Implement `run(spec)`, `resume(run_id)`, and `inspect(run_id)` over a project-local `outputs/runs/<run_id>` directory.
- [x] Execute intake, outline, per-chapter plan/write/check/edit/validate/style/promote, book compile in order.
- [x] Record retryable, blocked, and failed stages; never advance after a failed contract or continuity gate.
- [x] Use an injectable orchestrator/LLM factory so a fake LLM can run a two-chapter integration test.
- [x] Verify resume skips valid checkpoints and does not overwrite valid artifacts.

### Task 4: CLI surface

**Files:**
- Modify: `core/orchestrator.py`
- Create: `tests/test_pipeline_cli.py`

- [x] Add `run`, `resume`, `run-status`, and `retry` commands with prompt/project/chapter/word/model/approval options.
- [x] Return non-zero exit codes for blocked or failed runs and keep all existing commands unchanged.
- [x] Test `--dry-run`, invalid input, and the happy-path command dispatch.

### Task 5: Style, final promotion, and compilation

**Files:**
- Modify: `core/pipeline_runner.py`
- Reuse: `core/compile_book.py`
- Create: `tests/test_pipeline_integration.py`

- [x] Run Style Curator after Guardian validation and write a candidate final artifact.
- [x] Require explicit `--approval auto` for unattended promotion; default to review-required.
- [x] Gather only completed chapters and render Markdown/HTML/DOCX/EPUB/PDF from one compiled book source.
- [x] Test that incomplete chapters cannot enter exports and auto approval is audited.

### Task 6: Verification and documentation

**Files:**
- Modify: `docs/WORKFLOWS.md`
- Modify: `README.md`

- [x] Document the one-command workflow, resume behavior, approval policy, and Sub2API/OpenAI-compatible environment variables.
- [x] Run focused pipeline tests and the existing regression suite.
- [x] Confirm no commit or external submission is created.
