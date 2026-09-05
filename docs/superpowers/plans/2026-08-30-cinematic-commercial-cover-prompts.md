# Cinematic Commercial Cover Prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Novel OS cover generation from free-text concept templates to a versioned, canon-bound cinematic cover pipeline that produces realistic multi-character covers through `gpt-image-2`.

**Architecture:** Keep `CoverService` as the lifecycle coordinator and preserve the existing v1 `CoverBrief`/`CoverSet` read and selection paths. Add a v2 contract layer, a deterministic prompt compiler, and explicit genre profiles before introducing a structured Art Director and Canon Validator. Add post-generation quality reports and thumbnail review as advisory gates; only an explicit human selection activates a cover.

**Tech Stack:** Python 3.11 standard library, existing FastAPI/Pydantic/job runner, existing OpenAI-compatible `LLMClient`, content-addressed media store, React 19/TypeScript/Vite, Vitest, pytest, and the existing launcher/Skill conventions.

**Spec:** `docs/superpowers/specs/2026-08-30-cinematic-commercial-cover-prompt-design.md`

## Global Constraints

- Final cover image requests use the exact model `gpt-image-2`; Art Director and Visual Evaluator never generate final images.
- Every candidate is an independent `n=1` request on a portrait `2:3` canvas; default request is `2048x3072`, `high`, JPEG.
- New generation accepts JPEG or PNG only; existing v1 candidates, including WebP, remain readable, selectable, and deliverable.
- Required character identity, age/age band, lived environment, relationship, and story evidence come only from the approved story contract.
- Two or three required protagonists are all clear in one continuous scene; four or more use two or three foreground anchors and the remaining required characters in the same scene's middle/background action.
- The compiled prompt is deterministic, ordered by facts before style, and capped at 12,000 Unicode code points.
- No real landmarks, artist names, public figures, duplicated faces, collage scenes, unsupported spoilers, extra copy, logos, watermarks, or model-generated marketing claims.
- Commercial visual goals contain audience, thumbnail context, and truthful reader promise only; no campaign, placement, impression, click, conversion, revenue, or experiment fields are added.
- Missing critical facts, stale direction approvals, unresolved required assumptions, validator blockers, and prompt compilation failures occur before billable image calls.
- Cover failures never mutate manuscript, Canon, chapter, or novel-run state.
- Existing content-addressed media, revision checks, partial success, single-candidate retry, and explicit selection semantics remain intact.
- Automated tests use fixtures and never call a billable image or text provider.

## File Map

| File | Responsibility after this plan |
| --- | --- |
| `core/cover_models.py` | Keep v1 persistence types and add stable v2 projection metadata only where backward-compatible. |
| `core/cover_models_v2.py` | Define strict `CoverBriefV2`, `CoverScenePlan`, `VisualHook`, `ArtDirectionSet`, `CompiledCoverPrompt`, and `CoverQualityReport` contracts. |
| `core/cover_normalizer.py` | Convert v1 handoffs and legacy foundation/state artifacts into v2 facts with source paths and explicit assumptions. |
| `core/cover_profiles.py` | Store romance, family-ethics, and neutral visual grammar without ad-performance fields. |
| `core/cover_prompt_compiler.py` | Pure deterministic compiler and repair-code module. |
| `core/cover_director.py` | Structured Art Director client, fixture adapter, and direction persistence boundary. |
| `core/cover_validator.py` | Canon/evidence/multi-character validation before image generation. |
| `core/cover_quality.py` | Binary checks, evaluator protocol, quality report construction, and repair-code mapping. |
| `core/cover_handoff.py` | Preserve handoff parsing and route v1/legacy input through the normalizer. |
| `core/studio_settings.py` | Add independent director/evaluator settings while retaining locked image model settings. |
| `api/models.py` and `api/routes.py` | Expose direction, approval, generation, quality, and repair request contracts. |
| `api/cover_service.py` and `core/cover_store.py` | Coordinate direction lifecycle, immutable attempts, stale checks, and delivery metadata. |
| `web/src/routes/CoverStudio.tsx` | Render facts, direction approval, generation, and review states. |
| `web/src/api/client.ts` | Type and call the direction/quality endpoints. |
| `skills/novel-cover-studio/*` | Document the v2 entry gate, approval hash, and quality review commands. |
| `tests/test_cover_*`, `tests/test_cover_director.py`, `tests/test_cover_prompt_compiler.py`, `tests/test_cover_quality.py` | Contract, golden, lifecycle, and UI regression coverage. |

---

## Phase 1: Facts and Deterministic Prompt Compilation

### Task 1: Add the v2 story contract and normalizer

**Files:**
- Create: `core/cover_models_v2.py`
- Create: `core/cover_normalizer.py`
- Modify: `core/cover_handoff.py`
- Test: `tests/test_cover_models_v2.py`
- Test: `tests/test_cover_normalizer.py`
- Test: `tests/test_cover_handoff.py`

**Interfaces:**
- `CoverBriefV2.from_dict(data: Mapping[str, Any], *, source_prompt_sha256: str, foundation_sha256: str = "") -> CoverBriefV2`
- `CoverBriefV2.to_dict() -> dict[str, Any]`
- `normalize_cover_brief(source: CoverBrief | Mapping[str, Any], *, source_prompt_sha256: str, foundation_sha256: str = "") -> CoverBriefV2`
- `normalize_legacy_project(project: Path, prompt_text: str) -> CoverBriefV2`

- [x] **Step 1: Write RED contract tests.** Cover a single protagonist, two and three required protagonists, four required protagonists, v1 conversion, and rejected missing critical fields. Assert that each required character includes `age` or `age_band`, `occupation_and_status` or equivalent lived identity, `daily_wardrobe` or environment anchor, and `agency_signal`.

```python
def test_v2_round_trip_preserves_required_characters_and_visual_goal():
    brief = CoverBriefV2.from_dict(two_character_fixture(), source_prompt_sha256="a" * 64)
    assert CoverBriefV2.from_dict(brief.to_dict(), source_prompt_sha256="a" * 64) == brief
    assert [item.character_id for item in brief.principal_characters] == ["char_mara", "char_oren"]
    assert brief.commercial_visual_goal.display_context == "mobile_thumbnail"

def test_v2_rejects_required_character_without_age_or_lived_identity():
    payload = two_character_fixture()
    payload["principal_characters"][0].pop("age")
    payload["principal_characters"][0].pop("age_band")
    with pytest.raises(ValueError, match="age"):
        CoverBriefV2.from_dict(payload, source_prompt_sha256="a" * 64)

def test_normalizer_marks_v1_missing_identity_as_pending_assumptions():
    result = normalize_cover_brief(v1_fixture(), source_prompt_sha256="b" * 64)
    assert result.schema_version == 2
    assert result.principal_characters[0].character_id == "protagonist"
    assert any(item.status == "pending_confirmation" for item in result.visual_assumptions)
```

- [x] **Step 2: Run the RED tests.**

Run: `venv/bin/python -m pytest -q tests/test_cover_models_v2.py tests/test_cover_normalizer.py tests/test_cover_handoff.py`

Expected: FAIL because the v2 contracts and normalizer do not exist.

- [x] **Step 3: Implement strict contracts and conversion.** Use frozen dataclasses, explicit tuple/list serialization, SHA-256 validation, character-id references, and source-path strings for legacy facts. Preserve the existing `CoverBrief` API for v1 callers. Update `parse_cover_handoff()` and `resolve_cover_brief()` to return v2 only through an explicit normalizer call so current v1 generation remains compatible during migration.

- [x] **Step 4: Run GREEN and compatibility tests.**

Run: `venv/bin/python -m pytest -q tests/test_cover_models_v2.py tests/test_cover_normalizer.py tests/test_cover_handoff.py tests/test_cover_models.py tests/test_cover_api.py`

Expected: PASS, including existing v1 round trips and project isolation tests.

- [x] **Step 5: Commit the contract boundary.**

```bash
git add core/cover_models_v2.py core/cover_normalizer.py core/cover_handoff.py tests/test_cover_models_v2.py tests/test_cover_normalizer.py tests/test_cover_handoff.py
git commit -m "feat: add versioned cover story contract"
```

### Task 2: Add genre profiles and deterministic scene-plan compiler

**Files:**
- Create: `core/cover_profiles.py`
- Create: `core/cover_prompt_compiler.py`
- Test: `tests/test_cover_profiles.py`
- Test: `tests/test_cover_prompt_compiler.py`

**Interfaces:**
- `resolve_genre_profile(brief: CoverBriefV2) -> GenreEmotionProfile`
- `compile_cover_prompt(brief: CoverBriefV2, scene: CoverScenePlan, *, compiler_version: str = "cover-compiler.v2") -> CompiledCoverPrompt`
- `compile_repair_prompt(brief: CoverBriefV2, scene: CoverScenePlan, prior: CompiledCoverPrompt, repair_codes: Sequence[str]) -> CompiledCoverPrompt`

- [x] **Step 1: Write RED golden tests.** Assert the exact module order (`ROLE AND OUTPUT`, `STORY TRUTH`, `CAST LOCK`, `SINGLE CINEMATIC MOMENT`, `RELATIONSHIP BLOCKING`, `LIVED ENVIRONMENT`, `GENRE EMOTION`, `CAMERA`, `MOBILE COMMERCIAL`, `TITLE`, `PHOTOREALISM`, `EXCLUDE`), deterministic byte-for-byte output, no empty field markers, and a 12,000-code-point limit.

```python
def test_compile_is_deterministic_and_contains_age_environment_and_hook():
    first = compile_cover_prompt(brief_fixture(), scene_fixture())
    second = compile_cover_prompt(brief_fixture(), scene_fixture())
    assert first.text == second.text
    assert "CAST LOCK" in first.text
    assert "age 34" in first.text
    assert "lived-in apartment kitchen" in first.text
    assert "VISUAL HOOK" in first.text

def test_compile_rejects_unresolved_required_assumption_and_extra_copy():
    brief = brief_fixture(pending_required_assumption=True)
    with pytest.raises(ValueError, match="pending"):
        compile_cover_prompt(brief, scene_fixture())

def test_repair_compile_changes_only_requested_modules():
    baseline = compile_cover_prompt(brief_fixture(), scene_fixture())
    repaired = compile_repair_prompt(brief_fixture(), scene_fixture(), baseline, ["age_mismatch"])
    assert repaired.text.count("CAST LOCK") == 1
    assert repaired.revision > baseline.revision
    assert repaired.modules["SINGLE CINEMATIC MOMENT"] == baseline.modules["SINGLE CINEMATIC MOMENT"]
```

- [x] **Step 2: Run RED.**

Run: `venv/bin/python -m pytest -q tests/test_cover_profiles.py tests/test_cover_prompt_compiler.py`

Expected: FAIL because no profile or compiler exists.

- [x] **Step 3: Implement profiles and compiler.** Encode the romance submodes (`tender_slow_burn`, `reconciliation`, `forbidden_tension`, `betrayal_romance`), family-ethics submodes (`accusation_triangle`, `public_exclusion`, `domestic_betrayal`, `boundary_and_departure`), and a neutral baseline. Build the final prompt from structured blocks, explicitly describe numerical age plus visible age phase, preserve only approved identity traits, apply the 2/3 versus 4+ cast rule, and reject unsupported marketing adjectives and real places.

- [x] **Step 4: Run GREEN and property checks.**

Run: `venv/bin/python -m pytest -q tests/test_cover_profiles.py tests/test_cover_prompt_compiler.py tests/test_cover_handoff.py`

Expected: PASS with identical output for identical inputs and all prompts under the length cap.

- [x] **Step 5: Commit the compiler.**

```bash
git add core/cover_profiles.py core/cover_prompt_compiler.py tests/test_cover_profiles.py tests/test_cover_prompt_compiler.py
git commit -m "feat: compile canon-bound cinematic cover prompts"
```

### Task 3: Route the current fallback through v2 and enforce image2 at every boundary

**Files:**
- Modify: `core/cover_models.py`
- Modify: `core/cover_handoff.py`
- Modify: `core/image_client.py`
- Modify: `core/studio_settings.py`
- Modify: `api/cover_service.py`
- Modify: `api/routes.py`
- Modify: `web/src/routes/Settings.tsx`
- Test: `tests/test_image_client.py`
- Test: `tests/test_cover_service.py`
- Test: `tests/test_cover_settings.py`
- Test: `tests/test_studio_ux.py`

**Interfaces:**
- `ImageGenerationClient` rejects any settings model other than `gpt-image-2` before constructing a request.
- `CoverService` records `compiler_version`, `brief_schema_version`, and the compiled prompt on each new candidate without changing v1 selection behavior.
- `PUT /api/studio/cover` accepts only the locked image model and migrates legacy `webp` setting values to JPEG while retaining endpoint and key state.

- [x] **Step 1: Add RED boundary tests.** Instantiate a direct `CoverSettings(model="dall-e-3", ...)` and assert client construction fails; assert an injected generated image with a non-image2 model is not persisted; assert a v1 WebP candidate can still be selected; assert the Settings UI exposes a read-only image model.

- [x] **Step 2: Run RED.**

Run: `venv/bin/python -m pytest -q tests/test_image_client.py tests/test_cover_service.py tests/test_cover_settings.py tests/test_studio_ux.py && npm --prefix web test -- Settings`

Expected: FAIL on the new direct-client, provenance, and UI assertions.

- [x] **Step 3: Implement the boundary checks.** Keep `_GENERATION_EXTENSIONS` limited to JPEG/PNG, use a separate historical extension map for WebP selection, normalize legacy WebP settings to JPEG without deleting independent base URL/key fields, and make the UI model field read-only. Persist v2 metadata only when present so v1 JSON remains loadable.

- [x] **Step 4: Run GREEN and the full first-version suite.**

Run: `venv/bin/python -m pytest -q && npm --prefix web test && npm --prefix web run build`

Expected: PASS; the Python image-client fixtures may require host permission because they bind ephemeral local ports.

- [x] **Step 5: Commit Phase 1.**

```bash
git add core/cover_models.py core/cover_handoff.py core/image_client.py core/studio_settings.py api/cover_service.py api/routes.py web/src/routes/Settings.tsx tests/test_image_client.py tests/test_cover_service.py tests/test_cover_settings.py tests/test_studio_ux.py
git commit -m "feat: enforce v2 cover prompt and image model boundaries"
```

## Phase 2: Structured Art Director and Approval

### Task 4: Implement ArtDirectionSet and Canon Validator

**Files:**
- Create: `core/cover_director.py`
- Create: `core/cover_validator.py`
- Modify: `core/cover_models_v2.py`
- Test: `tests/test_cover_director.py`
- Test: `tests/test_cover_validator.py`

**Interfaces:**
- `CoverArtDirector.plan(brief: CoverBriefV2, *, count: int) -> ArtDirectionSet`
- `CoverArtDirector.from_fixture(payload: Mapping[str, Any]) -> CoverArtDirector`
- `validate_direction(brief: CoverBriefV2, direction: ArtDirectionSet) -> tuple[ValidationFinding, ...]`

- [x] **Step 1: Write RED fixture tests.** Use a fixed JSON director response and assert four distinct hook types (`emotional_identification`, `relationship_tension`, `evidence_reveal`, `irreversible_moment`), valid evidence references, two/three-person complete casts, and four-plus foreground/middle-ground blocking.

```python
def test_fixture_director_builds_four_distinct_scene_plans():
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(brief_fixture(), count=4)
    assert [plan.visual_hook.hook_type for plan in direction.plans] == [
        "emotional_identification", "relationship_tension", "evidence_reveal", "irreversible_moment",
    ]

def test_validator_blocks_unknown_evidence_and_identity_invention():
    direction = direction_fixture(unknown_evidence=True, invented_age=True)
    findings = validate_direction(brief_fixture(), direction)
    assert {item.code for item in findings} >= {"unknown_evidence", "identity_invention"}
```

- [x] **Step 2: Run RED.**

Run: `venv/bin/python -m pytest -q tests/test_cover_director.py tests/test_cover_validator.py`

Expected: FAIL because the structured director and validator do not exist.

- [x] **Step 3: Implement the structured boundary.** Build a director prompt that requests JSON only through the existing text client, parse and validate the response before exposing it, record `director_model`, profile version, brief hash, and assumptions, and stop before image generation when provider/JSON/schema validation fails. No market profile may add ethnicity, nationality, age, or social class.

- [x] **Step 4: Run GREEN.**

Run: `venv/bin/python -m pytest -q tests/test_cover_director.py tests/test_cover_validator.py tests/test_cover_prompt_compiler.py`

Expected: PASS with provider failures represented as direction errors and no image client calls.

- [x] **Step 5: Commit.**

```bash
git add core/cover_director.py core/cover_validator.py core/cover_models_v2.py tests/test_cover_director.py tests/test_cover_validator.py
git commit -m "feat: add structured cover direction validation"
```

### Task 5: Add direction persistence, approval hash, stale handling, and API/CLI workflow

**Files:**
- Modify: `core/cover_store.py`
- Modify: `api/cover_service.py`
- Modify: `api/models.py`
- Modify: `api/routes.py`
- Modify: `core/cover_cli.py`
- Modify: `deploy.sh`
- Test: `tests/test_cover_api.py`
- Test: `tests/test_cover_store.py`
- Test: `tests/test_cover_cli.py`
- Test: `tests/test_deploy_script.py`

**Interfaces:**
- `CoverStore.save_direction(direction: ArtDirectionSet) -> ArtDirectionSet`
- `CoverStore.approve_direction(project_id: str, direction_id: str, *, expected_brief_sha256: str, approved_direction_sha256: str) -> ArtDirectionSet`
- `POST /api/projects/{project_id}/covers/directions`
- `POST /api/projects/{project_id}/covers/directions/{direction_id}/approve`
- `POST /api/projects/{project_id}/covers/generate` requires an approved, non-stale direction id for v2 requests.

- [x] **Step 1: Write RED API and persistence tests.** Assert approval binds the exact brief and direction hashes, a changed brief marks the direction stale, stale approval cannot generate images, and the non-interactive launcher requires `NOVEL_OS_COVER_APPROVED_DIRECTION_SHA256`.

- [x] **Step 2: Run RED.**

Run: `venv/bin/python -m pytest -q tests/test_cover_api.py tests/test_cover_store.py tests/test_cover_cli.py tests/test_deploy_script.py`

Expected: FAIL because direction records and endpoints do not exist.

- [x] **Step 3: Implement direction persistence and routes.** Store each direction as a new immutable version, bind approval to the latest server-timestamped direction and exact hashes, and return `409` for stale/revision conflicts. The previously released schema-v1 direct-generation path remains operational during migration; schema-v2 and the Studio workflow require an approved latest direction before any image call.

- [x] **Step 4: Run GREEN.**

Run: `venv/bin/python -m pytest -q tests/test_cover_api.py tests/test_cover_store.py tests/test_cover_cli.py tests/test_deploy_script.py`

Expected: PASS with no API key or raw prompt leakage.

- [x] **Step 5: Commit.**

```bash
git add core/cover_store.py api/cover_service.py api/models.py api/routes.py core/cover_cli.py deploy.sh tests/test_cover_api.py tests/test_cover_store.py tests/test_cover_cli.py tests/test_deploy_script.py
git commit -m "feat: persist and approve cover directions"
```

### Task 6: Add the Studio facts and direction-approval states

**Files:**
- Modify: `web/src/api/client.ts`
- Modify: `web/src/routes/CoverStudio.tsx`
- Modify: `web/src/routes/CoverStudio.test.tsx`
- Modify: `skills/novel-cover-studio/SKILL.md`
- Modify: `skills/novel-cover-studio/references/cover-handoff.md`

**Interfaces:**
- `api.coverDirections(id: string) -> Promise<CoverDirection[]>`
- `api.approveCoverDirection(id: string, directionId: string, briefSha256: string, directionSha256: string) -> Promise<CoverDirection>`
- Studio displays facts, ages, lived environment, assumptions, scene plans, hook, approval status, and stale state before enabling generation.

- [x] **Step 1: Write RED component tests.** Assert that required characters and pending assumptions are visible, generation is disabled before approval, approval changes the state, and stale directions require a new approval.

- [x] **Step 2: Run RED.**

Run: `npm --prefix web test -- CoverStudio`

Expected: FAIL because the direction API and UI states are absent.

- [x] **Step 3: Implement the state machine.** Keep fixed `2:3` slots and current progress behavior, add facts/direction panels before generation, use icon buttons with accessible labels, and never display API keys or raw unpublished prompt text.

- [x] **Step 4: Run GREEN and build.**

Run: `npm --prefix web test -- CoverStudio && npm --prefix web run build`

Expected: PASS with no desktop/mobile overflow in the existing Cover Studio layout.

- [x] **Step 5: Commit Phase 2.**

```bash
git add web/src/api/client.ts web/src/routes/CoverStudio.tsx web/src/routes/CoverStudio.test.tsx skills/novel-cover-studio/SKILL.md skills/novel-cover-studio/references/cover-handoff.md
git commit -m "feat: add cover direction approval workspace"
```

## Phase 3: Visual Quality and Targeted Retry

### Task 7: Add thumbnail projection and quality report contracts

**Files:**
- Create: `core/cover_quality.py`
- Modify: `core/cover_models_v2.py`
- Modify: `api/models.py`
- Modify: `api/routes.py`
- Modify: `api/cover_service.py`
- Test: `tests/test_cover_quality.py`
- Test: `tests/test_cover_api.py`

**Interfaces:**
- `evaluate_binary_cover(data: bytes, *, expected_sha256: str, expected_content_type: str) -> tuple[QualityFinding, ...]`
- `CoverVisualEvaluator.evaluate(image: bytes, thumbnail: bytes, brief: CoverBriefV2, scene: CoverScenePlan) -> CoverQualityReport`
- `GET /api/projects/{project_id}/covers/{cover_set_id}/quality`

- [x] **Step 1: Write RED quality tests.** Assert binary checks report format, digest, portrait ratio, and size failures; an evaluator-unavailable result is `human_review_required`; a report with any blocker is `blocked`; and only scores of at least 80 for the five required dimensions can become `recommended_for_human_review`.

```python
def test_quality_report_blocks_bad_digest_and_ratio():
    findings = evaluate_binary_cover(b"not-an-image", expected_sha256="a" * 64, expected_content_type="image/jpeg")
    assert {item.code for item in findings} >= {"decode_failure", "sha_mismatch"}

def test_unavailable_visual_evaluator_requires_human_review():
    report = unavailable_evaluator().evaluate(image=b"fixture", thumbnail=b"fixture", brief=brief_fixture(), scene=scene_fixture())
    assert report.status == "human_review_required"
    assert report.blockers == []
```

- [x] **Step 2: Run RED.**

Run: `venv/bin/python -m pytest -q tests/test_cover_quality.py tests/test_cover_api.py`

Expected: FAIL because quality contracts and route do not exist.

- [x] **Step 3: Implement advisory evaluation.** Reuse `core.image_binary` for deterministic checks, create a thumbnail projection through the existing media URL/UI without altering source bytes, define the multimodal evaluator protocol, and provide an unavailable evaluator that never fabricates scores. Store report status, scores, blockers, evidence, and repair codes next to the candidate.

- [x] **Step 4: Run GREEN.**

Run: `venv/bin/python -m pytest -q tests/test_cover_quality.py tests/test_cover_api.py tests/test_cover_service.py`

Expected: PASS; selecting a candidate remains the only activation path.

- [x] **Step 5: Commit.**

```bash
git add core/cover_quality.py core/cover_models_v2.py api/models.py api/routes.py api/cover_service.py tests/test_cover_quality.py tests/test_cover_api.py
git commit -m "feat: add cover quality reports and thumbnail review"
```

### Task 8: Add immutable attempt history and repair-code retry

**Files:**
- Modify: `core/cover_models_v2.py`
- Modify: `core/cover_prompt_compiler.py`
- Modify: `core/cover_store.py`
- Modify: `api/cover_service.py`
- Modify: `api/models.py`
- Modify: `api/routes.py`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/routes/CoverStudio.tsx`
- Modify: `web/src/routes/CoverStudio.test.tsx`
- Test: `tests/test_cover_service.py`
- Test: `tests/test_cover_prompt_compiler.py`
- Test: `tests/test_cover_api.py`

**Interfaces:**
- `CoverService.retry_candidate(..., repair_codes: Sequence[str]) -> CoverSet`
- `CoverStore.append_attempt(candidate_id: str, attempt: CoverGenerationAttempt) -> CoverSet`
- `POST /api/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/retry` accepts only reported repair codes.

- [x] **Step 1: Write RED retry tests.** Assert `age_mismatch` changes only CAST LOCK, `missing_character` changes CAST LOCK plus blocking, `generic_ai_face` changes PHOTOREALISM plus CAMERA, and an invalid repair code is rejected without an image call. Assert prior prompt, report, and image metadata remain immutable in attempt history.

- [x] **Step 2: Run RED.**

Run: `venv/bin/python -m pytest -q tests/test_cover_prompt_compiler.py tests/test_cover_service.py tests/test_cover_api.py`

Expected: FAIL because repair-code attempts are not modeled or routed.

- [x] **Step 3: Implement append-only attempts and targeted recompilation.** Validate repair codes against the report, create a new prompt revision, call only the selected candidate through `gpt-image-2`, retain the old attempt, and keep automatic quality evaluation advisory. No quality report may start a retry without an explicit user action or an approved retry budget.

- [x] **Step 4: Run GREEN and the full verification matrix.**

Run: `venv/bin/python -m pytest -q && npm --prefix web test && npm --prefix web run build && git diff --check`

Expected: PASS; host permission is required for the local HTTP image fixtures when the sandbox rejects ephemeral port binding.

- [x] **Step 5: Commit Phase 3.**

```bash
git add core/cover_models_v2.py core/cover_prompt_compiler.py core/cover_store.py api/cover_service.py api/models.py api/routes.py web/src/api/client.ts web/src/routes/CoverStudio.tsx web/src/routes/CoverStudio.test.tsx tests/test_cover_service.py tests/test_cover_prompt_compiler.py tests/test_cover_api.py
git commit -m "feat: add quality-gated cover repair attempts"
```

## Final Self-Review Checklist

- [x] Every v2 field in the approved spec has a contract test or a direct consumer test.
- [x] v1 CoverSet files remain readable and historical WebP selection remains deliverable.
- [x] All image requests are visibly and programmatically locked to `gpt-image-2`, `n=1`, and portrait `2:3`.
- [x] No prompt path infers ethnicity, age, class, location, or relationship facts from a market label.
- [x] Two/three and four-plus character composition rules are tested separately.
- [x] Romance and family-ethics profiles create behavior, distance, light, and props rather than generic color adjectives.
- [x] Prompt compiler output is deterministic, bounded, and free of empty field markers or extra copy.
- [x] Direction approval is hash-bound, stale-aware, and required before billable schema-v2 generation.
- [x] Quality reports never auto-select or auto-retry candidates.
- [x] No API or persisted cover object contains campaign, impression, click, conversion, revenue, or experiment data.
- [x] Full Python tests, full frontend tests, frontend build, and `git diff --check` were rerun before completion was reported.
