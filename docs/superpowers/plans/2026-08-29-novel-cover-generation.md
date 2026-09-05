# Novel Cover Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate four story-specific portrait `2:3` covers through Sub2API `gpt-image-2`, preserve provider-native resolution, let customers select a candidate in Studio, and ship covers with common novel exports in a deterministic package.

**Architecture:** Add an independent cover settings resolver and image client under `core`, then place cover lifecycle and project persistence behind a focused `CoverService`. Reuse the existing content-addressed media store for Studio delivery and project `outputs/deliverables` for customer-facing projections. The brainstorm Skill emits a structured handoff; a new cover Skill turns that handoff into distinct concepts before the API spends image calls.

**Tech Stack:** Python 3.11 standard library, FastAPI/Pydantic/SQLModel, existing Novel OS media and job layers, React 19/TypeScript/Vite, Vitest, pytest, Bash/Docker Compose, Codex Skills.

**Spec:** `docs/superpowers/specs/2026-08-29-novel-cover-generation-design.md`

## Global Constraints

- Use `gpt-image-2` independently from all writing-agent model assignments.
- Default to four candidates; accept only three through five.
- Request portrait `2048x3072` and `high` quality JPEG by default, while accepting provider-native portrait resolutions.
- Send each concept as an independent `n=1` request to permit candidate-level retry.
- Persist no API key in manifests, errors, job metadata, logs, or API responses.
- Story-facing prompts and images use fictional locations, never real city or place names.
- Cover failure leaves manuscript, canon, chapter, and novel-run state unchanged.
- Reuse healthy Docker services; `novel-cover` must not restart them.
- Automated tests never call a billable endpoint.
- Do not modify or delete unrelated untracked workspace files.

---

### Task 1: Independent Cover Settings

**Files:**
- Modify: `core/studio_settings.py`
- Modify: `api/models.py`
- Modify: `api/routes.py`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/routes/Settings.tsx`
- Modify: `.env.example`
- Test: `tests/test_cover_settings.py`
- Test: `tests/test_studio_ux.py`

**Interfaces:**
- Produces: `CoverSettings` with `base_url`, `api_key`, `model`, `size`, `quality`, `output_format`, `count`, and `timeout_seconds`.
- Produces: `resolve_cover_settings() -> CoverSettings` and `cover_status() -> dict[str, Any]`.
- Produces: `GET/PUT /api/studio/cover` with write-only `api_key` and `has_api_key` response state.

- [ ] **Step 1: Write failing settings tests**

```python
def test_cover_settings_default_to_image2_and_2k(monkeypatch):
    settings = resolve_cover_settings({})
    assert settings.model == "gpt-image-2"
    assert settings.size == "2048x3072"
    assert settings.quality == "high"
    assert settings.output_format == "jpeg"
    assert settings.count == 4

def test_cover_settings_reuse_writing_endpoint_and_key(monkeypatch):
    settings = resolve_cover_settings({
        "NOVEL_OS_BASE_URL": "https://sub2api.example/v1",
        "NOVEL_OS_API_KEY": "secret",
    })
    assert settings.base_url == "https://sub2api.example/v1"
    assert settings.api_key == "secret"

def test_cover_status_redacts_key(monkeypatch):
    status = cover_status({"NOVEL_OS_COVER_API_KEY": "secret"})
    assert status["has_api_key"] is True
    assert "secret" not in repr(status)
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_cover_settings.py tests/test_studio_ux.py -q`
Expected: FAIL because the cover resolver and `/api/studio/cover` do not exist.

- [ ] **Step 3: Implement the settings contract**

Add a frozen dataclass, strict enum/range validation, environment fallbacks, safe status projection, Pydantic request/response models, routes, and a quiet settings section using the existing form components. Keep key fields blank after saving.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_cover_settings.py tests/test_studio_ux.py -q && npm --prefix web run test -- Settings`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/studio_settings.py api/models.py api/routes.py web/src/api/client.ts web/src/routes/Settings.tsx .env.example tests/test_cover_settings.py tests/test_studio_ux.py
git commit -m "feat: add independent cover model settings"
```

### Task 2: Image Client and Binary Validation

**Files:**
- Create: `core/image_client.py`
- Test: `tests/test_image_client.py`

**Interfaces:**
- Consumes: `CoverSettings` from Task 1.
- Produces: `GeneratedImage(data, content_type, width, height, request_id, model)`.
- Produces: `ImageGenerationClient.generate(prompt: str) -> GeneratedImage`.
- Produces: typed `ImageClientError(message, retryable, status_code)` with sanitized messages.

- [ ] **Step 1: Write failing request/response tests**

Use an in-process HTTP server to assert `POST /v1/images/generations` receives:

```json
{
  "model": "gpt-image-2",
  "prompt": "PROMPT",
  "n": 1,
  "size": "2048x3072",
  "quality": "high",
  "output_format": "jpeg"
}
```

Return base64 fixtures with valid JPEG and PNG headers and assert portrait `2:3` ratio validation, request ID propagation, retry classification for 429/5xx/timeouts, rejection of malformed base64 and wrong ratios, and absence of the key from every error string.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_image_client.py -q`
Expected: FAIL with missing `core.image_client`.

- [ ] **Step 3: Implement the minimal client**

Use `urllib.request` with injectable opener and sleeper, bounded response reads, explicit retry status rules, content sniffing, `api.media.dimensions`-compatible header parsing moved or reused without importing FastAPI, and sanitized exception construction.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_image_client.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/image_client.py tests/test_image_client.py
git commit -m "feat: add Sub2API image generation client"
```

### Task 3: Cover Brief, Concepts, and Durable State

**Files:**
- Create: `core/cover_models.py`
- Create: `core/cover_store.py`
- Test: `tests/test_cover_models.py`
- Test: `tests/test_cover_store.py`

**Interfaces:**
- Produces: `CoverBrief.from_dict(data, source_prompt_sha256, foundation_sha256="")`.
- Produces: `CoverConcept`, `CoverCandidate`, and `CoverSet` JSON round trips.
- Produces: `CoverStore(project_path).create/load/list/save` with atomic revision-checked writes.

- [ ] **Step 1: Write failing domain tests**

Test required title/audience/conflict/protagonist/decisive-node fields, fictional-place forbidden elements, candidate count bounds, status transitions, immutable ready-image provenance, exact title inclusion in every concept prompt, stale detection by source hash, and compare-and-swap selection revisions.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_cover_models.py tests/test_cover_store.py -q`
Expected: FAIL because the modules are absent.

- [ ] **Step 3: Implement JSON contracts and store**

Store sets under `outputs/covers/sets/<cover_set_id>.json` and the active pointer under `outputs/covers/index.json`. Use UUID-derived IDs, UTC timestamps, deterministic serialization, root containment checks, temporary files, and atomic replacement.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_cover_models.py tests/test_cover_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/cover_models.py core/cover_store.py tests/test_cover_models.py tests/test_cover_store.py
git commit -m "feat: add durable cover candidate contracts"
```

### Task 4: Cover Service, Media Projection, and Partial Recovery

**Files:**
- Create: `api/cover_service.py`
- Modify: `api/media.py`
- Test: `tests/test_cover_service.py`

**Interfaces:**
- Consumes: `CoverBrief`, `CoverConcept`, `CoverStore`, `ImageGenerationClient`, `MediaStore`, and existing `db.media_add`.
- Produces: `CoverService.generate(project_id, project_path, brief, concepts) -> CoverSet`.
- Produces: `retry_candidate`, `reject_candidate`, and `select_candidate` with idempotent behavior.

- [ ] **Step 1: Write failing service tests**

Test four successful independent calls, partial success with two ready and two failed candidates, set readiness at three images, retry of only one candidate, media SHA deduplication, no deliverable file for invalid bytes, explicit selection, idempotent reselection, and stale-set selection confirmation.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_cover_service.py -q`
Expected: FAIL with missing `api.cover_service`.

- [ ] **Step 3: Implement service orchestration**

Raise the media byte limit for generated cover JPEG/PNG only through a separate validated limit, register each image as `kind="cover"`, persist safe provider metadata, preserve successful candidates across failures, and update sets after each independent request so process interruption loses at most one in-flight candidate.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_cover_service.py tests/test_media.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/cover_service.py api/media.py tests/test_cover_service.py tests/test_media.py
git commit -m "feat: generate and recover cover candidates"
```

### Task 5: Deterministic Delivery Package

**Files:**
- Create: `core/delivery_package.py`
- Modify: `core/pipeline_models.py`
- Modify: `core/pipeline_runner.py`
- Test: `tests/test_delivery_package.py`
- Modify: `tests/test_pipeline_integration.py`

**Interfaces:**
- Produces: `build_delivery_package(project_path, cover_set=None) -> PackageResult`.
- Produces: `outputs/deliverables/package-manifest.json` and `book-package.zip`.
- Consumes ready cover media through a `read_media(candidate) -> bytes` callback so core packaging remains storage-agnostic.

- [ ] **Step 1: Write failing package tests**

Test inclusion of selected user formats, ready pending covers, `selected-cover.jpg` after selection, exclusion of HTML by default, exclusion of symlinks/temp/archive recursion, sorted manifest entries, normalized ZIP timestamps, stable archive bytes, and atomic preservation of a prior package on failure.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_delivery_package.py tests/test_pipeline_integration.py -q`
Expected: FAIL because package creation is absent.

- [ ] **Step 3: Implement package builder and compile hook**

Change new-run defaults to `("markdown", "epub", "pdf", "docx")`. After compile, build a text-only package when no cover exists. Cover generation and selection rebuild it with cover projections. Record payload hashes without recursively hashing the manifest or archive.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_delivery_package.py tests/test_pipeline_integration.py tests/test_compile_binary.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/delivery_package.py core/pipeline_models.py core/pipeline_runner.py tests/test_delivery_package.py tests/test_pipeline_integration.py
git commit -m "feat: package novel exports with cover candidates"
```

### Task 6: Cover API and Background Jobs

**Files:**
- Modify: `api/models.py`
- Modify: `api/routes.py`
- Modify: `api/jobs.py`
- Modify: `api/services.py`
- Test: `tests/test_cover_api.py`

**Interfaces:**
- Produces project-scoped generate/list/detail/select/reject/retry endpoints from the spec.
- Produces `GET /api/projects/{project_id}/deliverables/package`.
- Extends job projection with optional `meta: dict` containing only safe progress fields.

- [ ] **Step 1: Write failing API tests**

Use dependency overrides for the image client and media store. Assert project isolation, validated concept count, 202 background generation, progress polling, safe errors, ready candidate URLs, revision conflicts as 409, idempotent selection, retry scoping, and package download disposition.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_cover_api.py -q`
Expected: FAIL with 404 routes.

- [ ] **Step 3: Implement routes and service wiring**

Resolve project paths through `ProjectService`, parse the prompt handoff through Task 8's parser, submit long work to `JobRunner`, and update only safe job progress. Map domain errors to 400/404/409/422/502/503 without exposing provider response bodies.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_cover_api.py tests/test_studio_ux.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/models.py api/routes.py api/jobs.py api/services.py tests/test_cover_api.py
git commit -m "feat: expose project cover workflow API"
```

### Task 7: CLI and Docker Launcher Without Restart

**Files:**
- Modify: `core/orchestrator.py`
- Modify: `deploy.sh`
- Modify: `compose.yaml`
- Test: `tests/test_cover_cli.py`
- Modify: `tests/test_deploy_script.py`

**Interfaces:**
- Produces: native `cover generate|list|select|reject|retry` commands.
- Produces: `./deploy.sh novel-cover ...` forms that execute inside a healthy backend container.

- [ ] **Step 1: Write failing launcher tests**

Assert help lists every cover form, a healthy backend uses `docker compose exec -T backend` without `up`, `build`, `restart`, or `down`, generation preserves explicit Prompt/project/count values, selection prints the package and Studio URLs, and invalid counts fail before Docker execution.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_cover_cli.py tests/test_deploy_script.py -q`
Expected: FAIL because `novel-cover` is unknown.

- [ ] **Step 3: Implement CLI and launcher routing**

Use the same core/service interfaces as the API. The command emits concise JSON suitable for both humans and scripts. Pass cover settings into Compose without changing writing settings.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_cover_cli.py tests/test_deploy_script.py -q && bash -n deploy.sh`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator.py deploy.sh compose.yaml tests/test_cover_cli.py tests/test_deploy_script.py
git commit -m "feat: add non-restarting Docker cover command"
```

### Task 8: Brainstorm Handoff and Cover Skill

**Files:**
- Modify: `skills/novel-brainstorm-workshop/SKILL.md`
- Modify: `skills/novel-brainstorm-workshop/references/prompt-contract.md`
- Create: `skills/novel-cover-studio/SKILL.md`
- Create: `skills/novel-cover-studio/agents/openai.yaml`
- Create: `skills/novel-cover-studio/references/cover-handoff.md`
- Create: `skills/novel-cover-studio/references/commercial-direction.md`
- Create: `core/cover_handoff.py`
- Test: `tests/test_cover_handoff.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Produces: `parse_cover_handoff(prompt_text: str) -> CoverBrief`.
- Produces: a required YAML-shaped `cover_handoff` block in final brainstorm Prompts.
- Produces: a discoverable `novel-cover-studio` Skill that emits three to five concepts and uses the repository launcher.

- [ ] **Step 1: Write failing parser and Skill behavior tests**

Test complete Chinese and English handoffs, missing audience/conflict/node fields, exact title preservation, no real story-facing location, machine-readable boundaries, cover Skill launcher detection, concept diversity, title-only copy, and host installation guidance using `$HOME/.codex/skills`.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_cover_handoff.py tests/test_skills.py -q`
Expected: FAIL because the parser and Skill are absent.

- [ ] **Step 3: Implement parser and Skill resources**

Use explicit `COVER_HANDOFF_BEGIN`/`COVER_HANDOFF_END` boundaries with JSON inside the Markdown Prompt so parsing uses `json.loads`, not an ad hoc YAML parser. Keep the Skill entrypoint concise and route visual/detail guidance to references.

- [ ] **Step 4: Validate Skills and tests**

Run:

```bash
pytest tests/test_cover_handoff.py tests/test_skills.py -q
python /Users/max/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/novel-cover-studio
python /Users/max/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/novel-brainstorm-workshop
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/cover_handoff.py skills/novel-brainstorm-workshop skills/novel-cover-studio tests/test_cover_handoff.py tests/test_skills.py
git commit -m "feat: hand off approved story designs to cover skill"
```

### Task 9: Studio Cover Selection Workspace

**Files:**
- Create: `web/src/routes/CoverStudio.tsx`
- Create: `web/src/routes/CoverStudio.test.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/routes/ProjectDashboard.tsx`
- Modify: `web/src/components/Sidebar.tsx`
- Modify: `web/src/icons/registry.ts`

**Interfaces:**
- Consumes Task 6 cover APIs and job polling.
- Produces route `/projects/:id/covers` with fixed `2:3` candidate slots, full-resolution preview, generation, retry, reject, select, and package download.

- [ ] **Step 1: Write failing component tests**

Test empty/configuration-missing state, four stable loading slots, partial success, failed-candidate retry, explicit selection confirmation, selected and stale labels, no implicit selection, full-resolution link, and package download visibility.

- [ ] **Step 2: Verify RED**

Run: `npm --prefix web run test -- CoverStudio`
Expected: FAIL because the route does not exist.

- [ ] **Step 3: Implement the workspace**

Use the existing visual system, Icon component, toast, confirmation dialog, and API client. Keep candidate cards at `aspect-ratio: 2 / 3`, use restrained status chips and one primary action per state, and ensure controls wrap without overlap on mobile.

- [ ] **Step 4: Verify GREEN and build**

Run: `npm --prefix web run test -- CoverStudio && npm --prefix web run build && npm --prefix web run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/routes/CoverStudio.tsx web/src/routes/CoverStudio.test.tsx web/src/App.tsx web/src/api/client.ts web/src/routes/ProjectDashboard.tsx web/src/components/Sidebar.tsx web/src/icons/registry.ts
git commit -m "feat: add Studio cover selection workspace"
```

### Task 10: Documentation and End-to-End Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/WORKFLOWS.md`
- Modify: `docs/API.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `tests/test_readme_commands.py`

**Interfaces:**
- Documents Sub2API configuration, Skill sync, generation/selection/retry, deliverable layout, package semantics, and rollback.

- [ ] **Step 1: Write failing documentation assertions**

Assert README contains `NOVEL_OS_COVER_MODEL`, `2048x3072`, `./deploy.sh novel-cover --help`, both Skill sync commands, the Studio route, and the output package tree.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_readme_commands.py -q`
Expected: FAIL on missing cover documentation.

- [ ] **Step 3: Update maintained documentation**

Document secrets as ignored local configuration, explain fallback to the writing Sub2API key, state that direct model typography may require candidate regeneration, and show selection/package verification commands with real launcher syntax.

- [ ] **Step 4: Run focused and full verification**

Run:

```bash
pytest tests/test_cover_settings.py tests/test_image_client.py tests/test_cover_models.py tests/test_cover_store.py tests/test_cover_service.py tests/test_delivery_package.py tests/test_cover_api.py tests/test_cover_cli.py tests/test_cover_handoff.py tests/test_skills.py tests/test_deploy_script.py tests/test_readme_commands.py -q
pytest -q
npm --prefix web run test
npm --prefix web run build
npm --prefix web run lint
bash -n deploy.sh
git diff --check
```

Expected: all commands pass.

- [ ] **Step 5: Docker and browser verification**

Build without restarting an already healthy writing run, start on an unused port only when needed, verify `/api/health`, cover settings status, SPA fallback, desktop/mobile Cover Studio screenshots, stable aspect ratios, no overlap, and package download. Use a fake image client for this pass.

- [ ] **Step 6: Optional live Sub2API probe**

Only when a key is configured and the user approves billable execution: generate one candidate first, verify its format and portrait `2:3` ratio, then generate the default set. Report safe request ID/model/native dimensions metadata without key or raw unpublished Prompt logs.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/WORKFLOWS.md docs/API.md docs/ARCHITECTURE.md tests/test_readme_commands.py
git commit -m "docs: document cover generation workflow"
```
