# Novel Cover Generation and Delivery Package Design

Date: 2026-08-29
Status: Approved in conversation; awaiting written-spec review

## 1. Goal

Novel OS will generate a set of commercial, story-specific novel covers through
an OpenAI-compatible Sub2API endpoint using `gpt-image-2`. The cover workflow
starts only after the brainstorm has fixed the title, target audience, core
task, story line, major characters, and world rules.

Each generation produces four high-quality, portrait cover candidates by
default. The generated image includes the exact novel title as part of the
model request. Customers can inspect the candidates, select one, reject an
individual candidate, or regenerate a new set. Pending and selected covers are
delivered with the common book exports.

## 2. Confirmed Product Decisions

- Image model: `gpt-image-2` through a configurable Sub2API base URL.
- Rendering strategy: the image model generates the complete cover, including
  title typography. Novel OS does not add a deterministic title overlay.
- Resolution: `2048x3072` pixels, portrait `2:3`, with the short edge at 2K.
- Default candidate count: four; accepted request range is three to five.
- Default output: high-quality WebP.
- Cover source: a structured handoff extracted from the approved brainstorm
  Prompt, not the entire raw Prompt copied into an image request.
- Delivery: pending candidates, selection metadata, and book export files live
  under `outputs/deliverables/` and are included in a distributable ZIP.
- Common package formats: Markdown, EPUB, PDF, and DOCX. HTML remains the
  Studio preview format and is excluded from the default ZIP.
- Existing writing-model settings remain unchanged. Cover generation has an
  independent configuration group and may reuse the same Sub2API URL and key.

## 3. Non-goals

- Training or fine-tuning an image model.
- Reproducing a specific published cover or artist's signature style.
- Generating illustrations for every chapter.
- Treating OCR as proof that generated title typography is exact.
- Automatically choosing a commercial winner without customer review.
- Replacing the existing project media library or manuscript compiler.

## 4. Commercial Visual Direction

The reference direction is a high-retention serialized-fiction cover designed
to remain legible at feed-card size. A candidate should communicate one primary
sales promise, not summarize the full plot.

Required visual properties:

- A dominant conflict readable before decorative detail.
- A clear protagonist identity and emotional position.
- A relationship, status, or power contrast when relevant to the premise.
- One secondary story signal expressed through a person, environment, or
  symbolic object rather than another competing focal scene.
- Strong genre signaling through composition, palette, wardrobe, setting, and
  typography.
- The exact approved novel title, prominent enough for a mobile thumbnail.
- Sufficient foreground/background separation and uncluttered title space.

The four default concepts use distinct sales strategies:

1. **Protagonist confrontation** - identity, opposition, and power imbalance.
2. **Decisive story node** - the irreversible incident or reversal.
3. **Symbolic evidence** - a contract, ring, letter, record, device, heirloom,
   or other object that represents the conflict.
4. **World and relationship pressure** - a character-led scene whose setting
   communicates the larger system pressing on the protagonist.

The Skill can replace an irrelevant strategy, but it must keep the candidates
meaningfully different. Color-only variants do not count as different concepts.

## 5. Brainstorm-to-cover Contract

`novel-brainstorm-workshop` will add a machine-readable `cover_handoff` section
to every final Prompt once story design is approved. It contains only confirmed
facts or clearly labeled visual assumptions.

```yaml
cover_handoff:
  schema_version: 1
  title: <exact approved title>
  author: <approved author or empty>
  language: <title and market language>
  genre: <primary and secondary genre>
  target_audience: <confirmed primary audience>
  market_scope: <release market branch>
  core_task: <reader-facing premise and protagonist objective>
  core_conflict: <specific opposition and stakes>
  emotional_promise: <anger, anticipation, romance, revenge, wonder, etc.>
  protagonist:
    role: <story role>
    visual_identity: <age-band, presentation, wardrobe, posture, expression>
    agency_signal: <visible action or decision>
  relationship_or_power_contrast: <visualizable contrast>
  decisive_story_node: <major irreversible event suitable for a cover>
  secondary_task:
    story_function: <supporting pressure or promise>
    visual_signal: <one person, setting feature, or symbolic object>
  world_signals:
    - <fictional setting, institution, era, technology, or social rule>
  title_direction:
    hierarchy: <title hierarchy guidance>
    preferred_zone: <top, center, or lower third>
    readability: mobile_thumbnail
  forbidden_elements:
    - <spoilers, real geography, unsupported character traits, or genre drift>
```

The handoff inherits the existing fictional-setting policy: story-facing cover
copy and visual landmarks must not identify real cities or real-world places.
Market research may still use real locations as metadata, but those locations
must not leak into the generated cover.

If title, audience, core conflict, protagonist identity, or decisive story node
is missing, the brainstorm Skill must return to its question loop. Cover
generation starts only after those fields are confirmed.

## 6. Cover Brief and Concept Model

The backend accepts a validated `CoverBrief`, normally constructed from the
Prompt's `cover_handoff`. The backend does not infer story canon from arbitrary
manuscript fragments.

```text
CoverBrief
- schema_version
- title, author, language
- genre, target_audience, market_scope
- core_task, core_conflict, emotional_promise
- protagonist
- relationship_or_power_contrast
- decisive_story_node
- secondary_task
- world_signals
- title_direction
- forbidden_elements
- source_prompt_sha256
- foundation_sha256, when available
```

The cover Skill converts the brief into three to five `CoverConcept` records.
Each concept records its visual strategy, focal scene, composition, palette,
secondary signal, title treatment, and final image-generation prompt. The image
prompt must:

- quote the exact title and require it to appear exactly once;
- state the output language and prohibit invented subtitle text;
- specify a single focal conflict and one subordinate visual signal;
- state the protagonist's visible action rather than only an appearance list;
- express genre and audience through concrete visual choices;
- require a portrait `2:3` commercial book-cover composition;
- prohibit real place names, watermarks, logos, UI chrome, and extra copy;
- avoid unsupported spoilers or visual facts absent from the handoff.

## 7. Generation Architecture

### 7.1 Configuration

Add an independent cover group to Studio settings and environment resolution:

```env
NOVEL_OS_COVER_BASE_URL=
NOVEL_OS_COVER_API_KEY=
NOVEL_OS_COVER_MODEL=gpt-image-2
NOVEL_OS_COVER_SIZE=2048x3072
NOVEL_OS_COVER_QUALITY=high
NOVEL_OS_COVER_FORMAT=webp
NOVEL_OS_COVER_COUNT=4
NOVEL_OS_COVER_TIMEOUT_SECONDS=180
```

Resolution rules:

- An empty cover base URL falls back to `NOVEL_OS_BASE_URL`.
- An empty cover API key falls back to `NOVEL_OS_API_KEY`.
- The key is write-only in API responses and never logged or persisted in run
  manifests.
- Cover model and parameters never replace `NOVEL_OS_MODEL` or writing-agent
  model assignments.
- Docker loopback translation follows the existing Studio-settings behavior.

### 7.2 Image client

Create a narrow OpenAI-compatible image client for:

```http
POST {base_url}/v1/images/generations
Authorization: Bearer <key>
Content-Type: application/json
```

Each concept is sent as an independent `n=1` request. Independent calls make
partial retry, per-concept provenance, and replacement of a single failed
candidate possible. The client accepts base64 image responses and records safe
provider metadata such as model and request ID.

The client validates:

- successful HTTP status and bounded response size;
- a supported decoded PNG, JPEG, or WebP payload;
- exact `2048x3072` dimensions;
- non-empty, decodable image pixels;
- configured timeout and cancellation;
- no secret values in raised errors.

Transport retries are limited to retryable timeouts, rate limits, and 5xx
responses. Semantic rejection, authentication failure, malformed data, and
dimension mismatch are surfaced without blind repetition.

### 7.3 Cover service

`CoverService` owns brief validation, concept validation, generation,
persistence, candidate replacement, and selection. It does not own manuscript
compilation or general media browsing.

A generation request creates a new versioned `CoverSet`; it never overwrites an
older set. Generated images and their provenance become immutable when ready.
The active selection pointer remains mutable through revision-checked updates.
Candidate generation can finish partially. A partially generated set is
inspectable and retryable; readiness requires at least three valid candidates.

```text
CoverSet
- cover_set_id
- project_id
- source_prompt_sha256
- foundation_sha256
- status: generating | partial | ready | selected | failed | stale
- requested_count
- candidates
- selected_candidate_id
- created_at, updated_at

CoverCandidate
- candidate_id, concept_id
- status: pending | ready | failed | rejected | selected
- relative_path, media_id, sha256
- width, height, format
- provider, model, request_id
- generation_prompt
- safe_request_parameters
- error
- created_at
```

If the Prompt or foundation hash changes after generation, the set is marked
`stale`; images remain available, and activation requires explicit customer
confirmation.

## 8. Storage and Delivery Package

The existing media store remains the content-addressed source for Studio image
serving and deduplication. The cover service projects customer-facing files into
the project's deliverables directory using atomic writes.

```text
outputs/deliverables/
|-- book.md
|-- book.epub
|-- book.pdf
|-- book.docx
|-- covers/
|   |-- pending/
|   |   |-- cover-01.webp
|   |   |-- cover-02.webp
|   |   |-- cover-03.webp
|   |   `-- cover-04.webp
|   |-- selected-cover.webp       # present after selection
|   `-- cover-set.json
|-- package-manifest.json
`-- book-package.zip
```

The package manifest records every payload file's relative path, media type,
byte size, SHA-256, role, and cover-selection state. It does not recursively
record its own hash or the archive's hash. The ZIP contains the manifest and is
reproducible: deterministic manifest serialization plus normalized entry order
and timestamps ensure identical inputs produce the same archive hash.

The package includes only regular payload files below `outputs/deliverables`;
path traversal, symlinks, temporary files, and the current or previous
`book-package.zip` are excluded. Rebuilding uses a temporary archive plus atomic
rename, so a failed build preserves the previous valid package.

The normal launcher defaults to Markdown, EPUB, PDF, and DOCX for new runs.
Explicit user-selected output formats remain authoritative and are listed in
the package manifest. HTML stays available through Studio's compile-preview
endpoint but is not packaged by default.

Before customer selection, book exports and all ready candidates are packaged
without claiming an active cover. After selection, the package is rebuilt and
includes `selected-cover.webp`. Embedding the selected cover into EPUB, PDF,
DOCX, and Markdown is a separate follow-up capability; the first release keeps
the cover as an explicit adjacent asset to avoid changing manuscript renderer
semantics silently.

## 9. API, CLI, and Docker Launcher

### 9.1 API

Add project-scoped endpoints:

```text
POST /api/projects/{project_id}/covers/generate
GET  /api/projects/{project_id}/covers
GET  /api/projects/{project_id}/covers/{cover_set_id}
POST /api/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/select
POST /api/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/reject
POST /api/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/retry
GET  /api/projects/{project_id}/deliverables/package
```

Generation is a background job because a high-quality image request may take
minutes. Job status exposes candidate progress without exposing secrets or raw
provider responses. Selection and rejection are idempotent.

### 9.2 CLI and launcher

Add a native command and Docker wrapper:

```text
python core/orchestrator.py cover --project PROJECT --prompt PROMPT [--count 3..5]
./deploy.sh novel-cover PROMPT_OR_PROJECT
```

The Docker launcher reuses a healthy backend and does not restart frontend or
backend services. It resolves the project from Prompt metadata when a Prompt
path is supplied, prints the candidate paths, and prints the Studio selection
URL. `./deploy.sh novel-cover --help` documents generation, listing, retry, and
selection forms.

## 10. Studio Experience

Add a project cover workspace with:

- configuration readiness without revealing the key;
- four stable `2:3` candidate slots that do not shift while loading;
- visible generation, failed, stale, rejected, ready, and selected states;
- full-resolution inspection and download;
- select, reject, and retry actions;
- concept summary and source-story signals for each candidate;
- package status and download after generation or selection.

Selection requires an explicit action. The UI never marks the first successful
candidate as selected automatically. A failed candidate can be retried without
regenerating successful candidates.

## 11. Skill Integration

Create `skills/novel-cover-studio/` with a concise `SKILL.md` and focused
references for the handoff schema and commercial cover direction.

The Skill applies when a user requests a novel cover after story design is
confirmed. Its workflow is:

1. Load the approved Prompt and parse `cover_handoff`.
2. Stop before generation when required story decisions are absent and return to
   the `novel-brainstorm-workshop` question loop.
3. Draft three to five genuinely distinct `CoverConcept` records.
4. Show the concepts and exact title treatment before spending image calls.
5. Use `./deploy.sh novel-cover` when available, otherwise use the native CLI.
6. Return candidate files and the Studio selection URL.
7. After selection, verify `cover-set.json`, `package-manifest.json`, and the ZIP.

Update `novel-brainstorm-workshop` so its final Prompt always emits
`cover_handoff` and its final response offers the cover workflow only after the
user has approved Sections A-E. The brainstorm Skill does not call the image
model while story direction is still changing.

Repository Skills remain the source of truth for version control. Installation
to `$HOME/.codex/skills` remains an explicit host synchronization step; Docker
does not load Codex Skills.

## 12. Error and Recovery Semantics

- Missing cover configuration: report the missing field and preserve the set as
  pending; do not affect a novel-writing run.
- Partial provider failure: preserve valid candidates, mark the set partial,
  and allow candidate-level retry.
- Invalid image dimensions or bytes: store no deliverable projection; retain a
  sanitized candidate error for diagnosis.
- Prompt/foundation drift: mark the set stale without deleting files.
- Package failure: keep the prior valid package and expose a package rebuild
  action.
- Selection conflict: use the current cover-set revision for compare-and-swap;
  return a conflict rather than silently replacing another selection.
- Cover failure never rewrites chapter artifacts, story state, or run manifests.

## 13. Security and Operational Constraints

- API keys are accepted through environment or ignored Studio settings only.
- API responses expose `has_api_key`, never the key value.
- Logs contain provider, model, status, duration, and request ID, not prompts
  containing unpublished story details unless debug logging is explicitly
  enabled.
- Project IDs and file paths use existing project-root containment checks.
- Decoded images and provider JSON responses have explicit byte limits.
- External image URLs are not fetched in the first release; Sub2API must return
  base64 image data. This avoids server-side request-forgery exposure.
- Generated content is original direction based on story facts. The Skill does
  not request imitation of a living artist or replication of a published cover.

## 14. Verification Strategy

Automated tests will cover:

- settings defaults, fallback, secret redaction, and Docker URL translation;
- image request serialization and base64 response parsing with a fake server;
- timeout, retry classification, malformed data, size, and dimension failures;
- brief and concept validation, title preservation, and candidate-count bounds;
- partial generation, candidate retry, idempotent selection, and stale sets;
- media-store deduplication and deliverable projection;
- deterministic package manifest and ZIP creation;
- API authorization-independent project isolation and job progress;
- CLI and `deploy.sh` behavior without service restart;
- Studio loading, failure, selection, retry, and responsive layouts;
- Prompt-contract parsing and both Skill validators.

No automated test calls a billable image endpoint. After local tests pass, an
explicit live smoke test generates one high-quality candidate using configured
Sub2API credentials, validates its decoded dimensions, stores it in a temporary
project, and records only safe response metadata. A four-candidate production
smoke run occurs only after the one-image probe succeeds.

## 15. Acceptance Criteria

1. A confirmed brainstorm Prompt contains a complete, parseable
   `cover_handoff` with no invented real place names.
2. Cover settings can reuse the existing Sub2API URL and key while selecting
   `gpt-image-2` independently from writing models.
3. A default request produces four distinct, valid `2048x3072` WebP candidates
   or a recoverable partial set with precise candidate errors.
4. Every ready candidate is traceable to its brief, concept, source Prompt hash,
   generation prompt, safe parameters, model, request ID, and image hash.
5. Studio presents three to five candidates and never selects one implicitly.
6. Customer selection is durable, idempotent, and reflected in both
   `cover-set.json` and `package-manifest.json`.
7. `book-package.zip` contains the selected book formats, all ready pending
   covers, cover metadata, and the selected cover when one exists.
8. `./deploy.sh novel-cover` reuses healthy containers and does not restart the
   novel-writing service.
9. Cover errors leave manuscript, canon, chapter, and novel-run state unchanged.
10. Focused backend, frontend, launcher, packaging, and Skill tests pass before
    any billable live verification.
