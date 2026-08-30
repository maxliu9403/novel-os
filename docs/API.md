# Novel OS Programmatic API

Everything Novel OS does is available as importable Python the CLI is just a thin layer.

---

## Module map

```
core/
├── orchestrator.py          NovelOrchestrator workflow + CLI
├── state_manager.py         StoryState, Character, PlotThread, ChapterState, ...
├── llm_client.py            LLMClient (13+ providers), LLMError
├── state_parser.py          ingest_agent_output, parse_*, apply_to_state
├── continuity_engine.py     run_all, Finding, individual check_* fns
├── cover_handoff.py         strict approved-Prompt parser + concept builder
├── cover_models.py          cover brief, concept, candidate, and set contracts
├── cover_store.py           atomic cover-set and active-pointer CAS persistence
├── image_client.py          Sub2API/OpenAI-compatible image request adapter
└── delivery_package.py      deterministic manifest and ZIP builder
```

---

## StoryState

```python
import sys; sys.path.insert(0, "core")
from state_manager import StoryState, Character, PlotThread

state = StoryState("/path/to/project")

# Characters
state.add_character(Character(
    id="char_001", full_name="Lena Vasquez", role="protagonist",
    internal_desire="uncover the truth", external_goal="find her brother",
    fear="being silenced", weakness="trusts too easily",
))
lena = state.get_character_by_name("Lena Vasquez")
state.update_character_location(lena.id, "Observatory rooftop", chapter=1)

# Plot threads
state.add_plot_thread(PlotThread(
    id="plot_001", name="The Theta-7 Signal",
    description="An impossible transmission predates the colony",
    thread_type="main", priority=5, start_chapter=1,
    target_resolution_chapter=28,
))

# Chapters
ch = state.create_chapter(1, title="First Contact")
state.update_chapter(1, {"status": "drafted", "word_count": 2451})

# Persist (atomic, .bak rollback)
state.save_state()
```

---

## LLMClient any provider

```python
from llm_client import LLMClient

# Auto-detect from env keys
client = LLMClient()
print(client.provider, client.model)

# Or explicit
client = LLMClient(provider="nvidia", model="meta/llama-3.3-70b-instruct")
client = LLMClient(provider="openai_compatible",
                   base_url="https://my-endpoint/v1",
                   api_key="...", model="my-model")

# Single-turn completion
text = client.complete(system="You are terse.", user="Reply: pong")

# Run an agent (loads agents/<name>/prompt.md as system message)
draft = client.run_agent("scribe", user_prompt="Write chapter 1 about...")
```

Supported providers: `anthropic`, `openai`, `azure`, `gemini`, `nvidia`, `kimi`, `groq`, `together`, `openrouter`, `deepseek`, `mistral`, `fireworks`, `ollama`, `lmstudio`, `openai_compatible`.

---

## State parser agent output → state mutations

```python
from state_parser import ingest_agent_output

changes = ingest_agent_output(
    state=state,
    chapter_number=1,
    agent_name="scribe",          # or "editor" / "continuity_guardian" / "style_curator"
    agent_output=raw_llm_response,
)
for entry in changes:
    print(entry)
state.save_state()
```

The parser recognizes `[SCRIBE_STATE_UPDATE]`, `[EDITOR_ANALYSIS]`, `[EDITOR_STATE_UPDATE]`, `[CONTINUITY_REPORT]`, `[CONTINUITY_STATE_UPDATE]`, `[STYLE_ANALYSIS]`, `[STYLE_STATE_UPDATE]` blocks. Both `[TAG]…[/TAG]` and unclosed `[TAG]…` (stops at next known tag) are supported.

State-update blocks may also carry durable continuity metadata. Use
`Plot_Thread_Updates` entries such as
`plot_001 | status=resolved | milestone=Acquisition closed | chapter=8`,
`Character_References` entries such as
`char_007 | chapter=8 | note=Referenced while off-page`, and
`Foreshadowing_Resolved` ledger entries such as
`id=ch6:fs2 | note=The hidden risk is disclosed`. These updates are written to
the chapter state and survive checkpoint restore.

Plot thread updates treat `resolved` and `abandoned` as terminal statuses. A
model cannot reopen one by restating it as `active`; an author-controlled
update must include `reopen=true`.

Individual parsers and the applier are exposed if you need them:

```python
from state_parser import (
    extract_block, parse_fields,
    parse_scribe, parse_editor, parse_continuity, parse_style,
    apply_to_state,
)
```

---

## Continuity engine deterministic checks

```python
from continuity_engine import run_all, Finding
from pathlib import Path

findings = run_all(state, Path("/path/to/project"), as_of_chapter=12)
for f in findings:
    print(f.format())   # human-readable
    d = f.to_dict()     # serializable
```

`Finding` fields: `severity` (`critical` | `warning` | `info`), `category`, `message`, `suggestion`, `chapter`, `entity_id`.

Individual checks are exposed too:

```python
from continuity_engine import (
    check_dormant_threads, check_overdue_threads,
    check_unresolved_foreshadowing, check_absent_characters,
    check_dead_characters_reappearing, check_chapter_file_consistency,
    check_required_character_fields,
)
```

---

## NovelOrchestrator high-level workflow

```python
from orchestrator import NovelOrchestrator

orch = NovelOrchestrator("/path/to/project")

orch.init_project("My Novel", "Sci-Fi Thriller", author="Mriganka")
orch.add_character("Lena Vasquez", "protagonist")
orch.plan_outline(num_chapters=32, target_words=80000)

orch.plan_chapter(1, pov="Lena Vasquez")             # Architect (real LLM)
orch.plan_chapter(1, pov="Lena Vasquez", dry_run=True)  # save prompt only

orch.write_chapter(1)                                # Scribe
orch.edit_chapter(1, mode="line")                    # Editor
critical = orch.run_checks(chapter_number=1)         # deterministic
orch.validate_chapter(1)                             # Guardian (LLM)
orch.approve_chapter(1)                              # gates on FAIL
orch.export(format="markdown")
```

Constructor injection for tests:

```python
orch = NovelOrchestrator("/tmp/test_project")
orch._llm = my_fake_llm_client       # bypass real LLM calls
```

---

## Environment configuration

| Variable | Purpose |
|---|---|
| `NOVEL_OS_LLM_PROVIDER` | Override provider auto-detection |
| `NOVEL_OS_MODEL` | Override the default model for the active provider |
| `NOVEL_OS_MAX_TOKENS` | Per-call cap (default 8192) |
| `NOVEL_OS_API_KEY` | Generic key for `openai_compatible` / aliases |
| `NOVEL_OS_BASE_URL` | Endpoint URL for `openai_compatible` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / etc. | Provider-native keys |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION` | Azure-specific |
| `KIMI_BASE_URL`, `<PROVIDER>_BASE_URL` | Override an alias's endpoint |
| `NOVEL_OS_COVER_BASE_URL` | Optional cover endpoint; falls back to `NOVEL_OS_BASE_URL` |
| `NOVEL_OS_COVER_API_KEY` | Optional cover key; falls back to `NOVEL_OS_API_KEY` |
| `NOVEL_OS_COVER_MODEL` | Cover model, default `gpt-image-2` |
| `NOVEL_OS_COVER_SIZE` | Preferred request size (default `2048x3072`); must preserve portrait `2:3` |
| `NOVEL_OS_COVER_QUALITY` | `low`, `medium`, `high`, or `auto`; default `high` |
| `NOVEL_OS_COVER_FORMAT` | `jpeg` or `png`; default `jpeg` |
| `NOVEL_OS_COVER_COUNT` | Default candidate count, 3-5; default 4 |
| `NOVEL_OS_COVER_TIMEOUT_SECONDS` | Per-image provider timeout; default 180 |
| `NOVEL_OS_COVER_DIRECTOR_PROVIDER` | Optional Art Director provider; falls back to the writing provider |
| `NOVEL_OS_COVER_DIRECTOR_MODEL` | Optional Art Director model; falls back to the writing model, never the image model |
| `NOVEL_OS_COVER_DIRECTOR_BASE_URL` | Optional Art Director endpoint; falls back to `NOVEL_OS_BASE_URL` |
| `NOVEL_OS_COVER_DIRECTOR_API_KEY` | Optional Art Director key; stored write-only in Studio |
| `NOVEL_OS_COVER_DIRECTOR_TIMEOUT_SECONDS` | Art Director timeout; default 180 |

`.env` files in the project root are auto-loaded (with or without `python-dotenv`).

---

## Cover HTTP API

All routes are project-scoped under `/api`. Image generation runs through the
in-memory `JobRunner`; poll `GET /api/jobs/{job_id}` until `status` is `done` or
`error`. Job errors and metadata never include provider credentials.

### Cover model settings

```http
GET /api/studio/cover
PUT /api/studio/cover
```

`PUT` accepts image fields (`base_url`, `api_key`, locked `model`, `size`,
`quality`, `output_format`, `count`, `timeout_seconds`) and optional independent
Art Director fields (`director_provider`, `director_model`, `director_base_url`,
`director_api_key`, `director_timeout_seconds`). Responses expose only key
presence flags, never either key.

### Plan and approve art direction

```http
POST /api/projects/{project_id}/covers/directions
Content-Type: application/json

{"count": 4}

GET /api/projects/{project_id}/covers/directions

POST /api/projects/{project_id}/covers/directions/{direction_id}/approve
Content-Type: application/json

{
  "expected_brief_sha256": "BRIEF_SHA256",
  "approved_direction_sha256": "DIRECTION_SHA256"
}
```

The server reads the current v2 handoff, blocks unresolved critical facts,
creates 3-5 structured scene plans through the configured Art Director, and
persists the facts snapshot. Approval binds the exact brief and direction
hashes and makes no image request.

### Generate an approved direction

```http
POST /api/projects/{project_id}/covers/generate
Content-Type: application/json

{
  "count": 4,
  "direction_id": "DIRECTION_ID",
  "approved_direction_sha256": "DIRECTION_SHA256"
}
```

Before submitting the background job, the server resolves the current project
Prompt again and marks an older approval stale. It then compiles each approved
scene and issues one independent `gpt-image-2`, `n=1` request. Historical v1
brief/concept submission remains available for compatibility; Studio uses the
direction-gated workflow.

The response is HTTP 202 with a job object:

```json
{
  "job_id": "JOB_ID",
  "kind": "cover.generate",
  "status": "running",
  "error": null,
  "meta": {"project_id": "PROJECT"}
}
```

### Read candidate sets

```http
GET /api/projects/{project_id}/covers
GET /api/projects/{project_id}/covers/{cover_set_id}
```

Sets are newest first. Each set contains the approved brief, concepts,
candidates, source hashes, set `revision`, and the project-wide
`active_revision`. Ready candidate responses expose a project-scoped media URL,
SHA-256, dimensions, content type, provider/model provenance, and safe request
parameters. API keys are absent.

### Select, reject, and retry

```http
POST /api/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/select
{"expected_revision": 5, "expected_active_revision": 1, "confirm_stale": false}

POST /api/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/reject
{"expected_revision": 5}

POST /api/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/retry
{"expected_revision": 5}
```

Selection and rejection require an explicit UI/client decision. Selection uses
two compare-and-swap guards, projects the content-addressed original into
`selected-cover.*`, and atomically rebuilds the delivery package. Retry returns
HTTP 202 and calls the provider only for the named failed candidate. Selecting
or rejecting an existing candidate does not resolve image-provider settings or
require an API key.

Status codes:

| Code | Meaning |
|---|---|
| 202 | generation or retry job accepted |
| 400 | invalid handoff, count, transition, or stale selection without confirmation |
| 404 | project, cover set, candidate media, or package missing |
| 409 | set revision or active-pointer revision changed |
| 502 | image provider returned an unusable response |
| 503 | cover provider configuration is incomplete |

### Media and delivery

```http
GET /api/projects/{project_id}/media/{media_id}/raw
GET /api/projects/{project_id}/deliverables/package
```

The package endpoint downloads `book-package.zip`. The ZIP contains available
book exports, ready/rejected/selected candidate projections, selected cover when
present, `cover-set.json`, and `package-manifest.json`. The manifest records the
path, media type, byte size, SHA-256, file role, and cover selection state.

---

*API v1.2*
