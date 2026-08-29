# Novel OS System Architecture

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│  web/  React + Vite + Tailwind v4 (the studio UI)          │
│         Library · Dashboard · Chapter (binder/flow/editor)    │
└───────────────▲──────────────────────────────────────────────┘
                │  HTTP / JSON  (polls jobs)
┌───────────────┴──────────────────────────────────────────────┐
│  api/  FastAPI (query projection and mutation boundary)    │
│   routes  →  services (ProjectService)  →  db (SQLite)        │
│                         │                                     │
│                    JobRunner (threads) → core/ orchestrator   │
└───────────────┬──────────────────────────────────────────────┘
                │  reads/writes files                          
┌───────────────┴──────────────────────────────────────────────┐
│  core/  multi-agent engine (file-based)                    │
│   orchestrator · agents · StoryState · continuity_engine      │
│   LLMClient (claude_cli / anthropic / openai / …)             │
└──────────────────────────────────────────────────────────────┘
```

## Canonical authority and projections

The canonical manuscript is the content-addressed artifact head in
`outputs/artifacts/`, together with its immutable canon proposal, evaluation
report, and promotion receipt. `PromotionService` is the only authority that
may advance a Final head or apply a `StoryState` canon delta. The receipt binds
the candidate SHA, previous head SHA, base canon SHA, proposal, report, actor,
reason, and decision metadata. Rollback means promoting the previous artifact
revision with a new receipt; deleting or editing a receipt is not rollback.

`outputs/manuscript/*_final.md`, `story_state.json`, SQLite `Artifact` rows, and
the rich-text `doc_json` column are projections for agents and the UI. They are
updated only after a receipt commits. A GET conversion from legacy Markdown to
ProseMirror is in-memory and read-only; a `legacy_migration` receipt is needed
before the converted document becomes canonical.

## Two stores, one bridge

Novel OS deliberately keeps **two** stores and an ingest bridge between them:

1. **Filesystem the agent engine's working store and Final authority.**
   `core/` is file-based and unchanged: each project is a folder under
   `NOVEL_OS_PROJECTS_DIR` with `outputs/state/story_state.json` and per-chapter
   stage files (`outline`, `draft`, `revised`, `final`). Agents read/write these.

2. **SQLite database the API's query projection.** (`api/db.py`, via SQLModel)
   Stores UI compatibility rows and append-only projections of core evidence:
   - `Project`, `Chapter` registry/metadata
   - `Artifact` the **text** projection of each stage (`outline/draft/revised/final`)
   - `ArtifactRevisionProjection`, `EvaluationReportProjection`,
     `QualityFindingProjection`, and `PromotionReceiptProjection`
   - `Snapshot` version history (label, text, word count, timestamp)
   - `Comment` annotations (body, optional quote, resolved)

   DB location: `NOVEL_OS_DB` (default `sqlite:///./novel_os.db`).
   Postgres is supported by URL (`postgresql+psycopg://…`); SQLite-only
   `connect_args` are applied only for `sqlite:` URLs. Story truth stays on disk.

3. **Ingest bridge** (`db.ingest_project`). After agents produce files, reading a
   chapter's stages mirrors non-Final files into `Artifact` rows. For Final,
   ingest reads the content-addressed artifact head; a stale legacy file cannot
   overwrite the canonical projection. The core file records remain authoritative
   while SQLite remains rebuildable.

4. **Context packs** (`core/context_pack.py`). Architect / Scribe / Guardian /
   continue / consequence prompts receive a **ranked** chapter pack (cast, bonds,
   threads, prior synopses, Codex) with per-purpose budgets instead of dump-then-
   truncate. No vector database — embeddings deferred to P4 semantic search.

5. **Keyword search** (`GET /api/projects/{id}/search?q=`). Ranked substring/token
   hits over characters, Codex, chapter titles, and relationships. Powers ⌘K.
   Postgres FTS can replace the scorer later behind the same route.

### Why two stores?
The agents are a mature file-based pipeline; rewriting them to be DB-native is
risk with no near-term payoff. The DB gives the UI fast queries, version history,
comments, and a real schema **without touching the engine**. The bridge keeps them
in sync. Over time, more reads move DB-first (the ingest path already populates it).

## Request lifecycle examples

- **View a chapter:** `GET /chapters/{n}/stages` → `ProjectService` reads non-Final
  stages from working files and Final from the verified artifact head, while
  best-effort `ingest_project` mirrors them into the DB → returns stages.
- **Edit Final:** `PUT /chapters/{n}/final` → candidate revision → deterministic
  report → canon proposal → `PromotionService` receipt → file/SQLite projection.
- **Run an agent:** `POST /run` → `JobRunner` runs the orchestrator on a daemon
  thread; the UI polls `GET /jobs/{id}`; on completion the UI refetches (which
  re-ingests).
- **Snapshot / restore:** snapshots live in the DB; restore creates a new
  `snapshot_restore` candidate and receipt after auto-snapshotting the current Final.

## Cover generation and delivery boundary

Cover generation is adjacent to manuscript production, not a manuscript phase.
A provider failure cannot pause, resume, retry, or alter a full-book run. The
workflow begins only after the approved Prompt contains one strict
`COVER_HANDOFF` contract produced from confirmed audience, story, and world
decisions.

```text
approved Prompt
  -> cover_handoff parser
  -> 3-5 distinct CoverConcept records
  -> CoverService + gpt-image-2 (one n=1 call per concept)
  -> content-addressed media + durable CoverSet
  -> Studio review / explicit selection
  -> selected projection + deterministic delivery ZIP
```

Ownership is split deliberately:

| Component | Owns |
|---|---|
| `core/cover_handoff.py` | strict handoff parsing and deterministic concept families |
| `core/image_client.py` | Sub2API request contract, retry classification, bounded reads, byte/dimension validation |
| `api/cover_service.py` | candidate lifecycle, media registration, projections, selection, package rebuild |
| `core/cover_store.py` | atomic set persistence and set/active compare-and-swap revisions |
| `api/media.py` | immutable project-scoped image bytes keyed by SHA-256 |
| `core/delivery_package.py` | deterministic manifest and ZIP projection |
| `web/.../CoverStudio.tsx` | review, confirmation, polling, and conflict reconciliation |

`CoverSet` files under `outputs/covers/sets/` are durable lifecycle state. The
`outputs/deliverables/covers/pending/` filenames are only the current customer
projection and may be replaced by a newer generation. Selection therefore
reads the candidate's content-addressed media by `(project_id, sha256,
extension)`, revalidates the digest/type/exact `2048x3072` dimensions, and only
then writes `selected-cover.*`. This prevents a historical set from selecting a
newer image that reused `cover-01.webp`.

Selection has two compare-and-swap boundaries:

1. `CoverSet.revision` protects candidate transitions within one set.
2. `outputs/covers/index.json:revision` protects the project-wide active pointer.

An active-pointer conflict rolls back the just-written candidate transition
before returning HTTP 409. An already-selected historical set can be activated
again: the pointer advances, the original bytes are restored, and the package
is rebuilt without another image request.

Cover secrets are input-only settings. Persisted request provenance includes
model, provider, format, size, `n=1`, prompt, and request ID, but excludes URL
credentials and authorization headers. Selection/rejection services do not
resolve provider settings, so existing assets remain manageable after a key is
removed.

`package-manifest.json` and `book-package.zip` are rebuildable projections. ZIP
members have stable order, timestamps, permissions, and compression. Existing
book formats are included when present; cover generation does not require a
completed manuscript. The archive never includes itself or temporary files.

## Persistence schema (SQLite)

| Table | Key fields |
|---|---|
| `project` | id (slug), title, genre, author, status |
| `chapter` | project_id, number, title, status, pov, word_count |
| `artifact` | project_id, chapter, stage, text, word_count |
| `artifact_revision_projection` | revision_id, SHA, parent, source, provider/model, contract heads |
| `evaluation_report_projection` | report/rubric/model, artifact binding, hard gates |
| `quality_finding_projection` | report, severity, evidence, suggested repair |
| `promotion_receipt_projection` | old/new artifact and canon heads, proposal, actor, reason |
| `snapshot` | project_id, chapter, label, source, text, word_count, created_at |
| `comment` | project_id, chapter, body, quote, resolved, created_at |

## Conventions
- Agent phases never run inline in a request always via `JobRunner` (threads),
  polled by the UI.
- Cover generation and retry also use `JobRunner`; candidate selection and
  rejection are short synchronous CAS mutations.
- Human-owned content is promoted through the same receipt authority as evidence
  runs; snapshots/comments remain UI projections.
- Final-derived chapter metadata (actual word count plus any chapter header title,
  POV, location, and time) travels in the same canon proposal as the Final revision.
- After a receipt commits, legacy Markdown and SQLite projections are rebuilt
  independently. A projection write failure does not turn the committed mutation
  into an API failure; subsequent reads continue from the verified Final head.
- Compare-and-swap and evidence-integrity conflicts return HTTP 409. A promotion
  outcome that still needs durable reconciliation returns HTTP 503 and directs the
  caller to refresh quality state before another mutation; the service first
  reconciles the exact request identity internally.
- `quality_policy=legacy` preserves old retry behavior and records a synthetic
  legacy receipt. `quality_policy=evidence_v1` requires a passing report, exact
  revision/proposal bindings, and a real promotion receipt.
- Role-specific runtime provenance records the provider/model used by Architect,
  Scribe, Editor, Guardian, and Style; never infer it from a global setting.
- Final head advancement is receipt-gated and compare-and-swap protected. Its
  compatibility Markdown projection uses atomic temp + `os.replace`; artifact
  revisions, evaluation reports, proposals, and receipts are immutable append-only
  evidence.

## Roadmap (DB-forward)
- Move list/detail reads fully DB-first (ingest already populates the rows).
- Persist job history in the DB.
- Codex (characters/locations/world) and outliner metadata as first-class tables.
- See `docs/superpowers/plans/2026-06-10-novel-os-frontend-product-roadmap.md`.
