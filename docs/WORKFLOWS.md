# Novel OS Workflows

Step-by-step recipes for using Novel OS day to day.

---

## 🚀 First-time setup

```bash
git clone https://github.com/mrigankad/Novel-OS.git
cd Novel-OS
pip install -r requirements.txt
cp .env.example .env
# edit .env and set ONE provider key (Anthropic / OpenAI / Gemini / Kimi / NVIDIA / etc.)
```

The first key found is auto-detected. Override with `NOVEL_OS_LLM_PROVIDER` if you have several keys set.

For Docker, follow the [deployment setup](../README.md#docker-deployment) and
run `./deploy.sh up` before using the workflows below that need a Docker backend.
The Docker launcher defaults to `NOVEL_OS_APPROVAL=auto`; the native CLI defaults
to `review_required` unless explicitly overridden.

---

## Full-book CLI from one prompt

Use this path when you want Novel OS to perform the entire book workflow instead
of clicking or invoking one chapter stage at a time.

The `--chapters` and `--words` flags are optional; explicit flags win, otherwise
the intake stage infers targets from the prompt.

For an OpenAI-compatible gateway such as Sub2API, configure `.env` without
putting the API key in a prompt or command line:

```bash
NOVEL_OS_LLM_PROVIDER=openai_compatible
NOVEL_OS_BASE_URL=https://your-sub2api-host.example/v1
NOVEL_OS_API_KEY=your-key
NOVEL_OS_MODEL=your-model-id
```

Preview the intake without a model call:

```bash
python core/orchestrator.py run \
  --project ./projects/my-novel \
  --prompt ./prompt/master-prompt.md \
  --chapters 24 \
  --words 80000 \
  --dry-run
```

Run unattended and compile Markdown, EPUB, and PDF:

```bash
python core/orchestrator.py run \
  --project ./projects/my-novel \
  --prompt ./prompt/master-prompt.md \
  --chapters 24 \
  --words 80000 \
  --approval auto \
  --output markdown epub pdf
```

The runner processes chapters sequentially because each chapter updates the
facts used by the next chapter. It persists `run.json`, `events.jsonl`, artifact
hashes, prompts, and raw provider responses so an interrupted run can resume.

Runs record a `quality_policy`: `legacy` keeps compatibility with older
manifests and emits a synthetic receipt, while `evidence_v1` requires a passing
evaluation report, revision-bound proposals, contract-head checks, and a real
promotion receipt. The provider/model recorded for each role is the runtime
client that produced or judged that artifact.

The default policy is `review_required`. The run pauses after producing a
candidate final for the current chapter:

```bash
python core/orchestrator.py run-status \
  --project ./projects/my-novel --run-id RUN_ID

# Review outputs/manuscript/chapter_001_candidate_final.md, then:
python core/orchestrator.py resume \
  --project ./projects/my-novel --run-id RUN_ID --approve-chapter 1
```

To switch the remaining run to unattended approval:

```bash
python core/orchestrator.py resume \
  --project ./projects/my-novel --run-id RUN_ID --approval auto
```

Retry a recorded failed or blocked checkpoint:

```bash
python core/orchestrator.py retry \
  --project ./projects/my-novel \
  --run-id RUN_ID \
  --phase chapter.validate \
  --chapter 7
```

Resuming a run already marked `completed` performs an integrity audit instead
of returning blindly. Every approved final must be a non-empty file whose hash
matches its promotion checkpoint. If a final is damaged, the runner restores it
only when the corresponding Style candidate is non-empty and still matches its
trusted checkpoint; it then recompiles the requested deliverables without
calling an agent or the model. Missing, empty, or untrusted source artifacts
stop the repair rather than producing a partial book.

An explicit retry of `chapter.check.pre`, `chapter.check.post`, or `book.check`
adopts the current manuscript and StoryState as the new trusted input
checkpoint. This preserves intentional continuity repairs instead of restoring
the stale state that produced the original deterministic finding. Guardian
validation retries still restore the clean post-edit checkpoint because a
failed Guardian response may have partially updated state.

Every API Final mutation follows the same evidence boundary: text or rich text
first becomes an immutable candidate revision, then a deterministic report and
canon proposal are created, and only `PromotionService` can commit the new head.
The same proposal carries the actual Final word count and any parsed chapter-header
title, POV, location, and time, so manuscript and StoryState metadata advance atomically.
This includes stage acceptance, direct text saves, ProseMirror saves, snapshot
restore, consequence acceptance, and force overrides. Force overrides record
the actor, reason, scope, candidate SHA, and base SHA; they still enforce stale
head checks and create a receipt. A GET of a legacy Final may convert Markdown
to an in-memory ProseMirror document, but it does not persist that conversion.
Once a Final head exists, reads verify and use that immutable revision; the mutable
Markdown and SQLite rows are rebuildable projections. Projection failures after a
receipt commit do not reverse or hide the committed result. Concurrent-head and
integrity conflicts surface as HTTP 409, while an unresolved durable outcome uses
HTTP 503 and requires a quality-state refresh before another mutation.

To roll back, select the previous receipt's `old_artifact_revision_id` and
promote that exact revision as a new candidate with a new idempotency key. Keep
both receipts so the audit trail remains append-only.

Critical deterministic findings or a Guardian `FAIL` block promotion. Network,
timeout, and rate-limit failures use bounded exponential retries; configuration
and output-contract failures stop immediately with a non-zero exit code.

Primary artifacts:

```text
outputs/input/prompt.md
outputs/input/brief.json
outputs/input/foundation.json
outputs/outline.md
outputs/runs/<run-id>/run.json
outputs/runs/<run-id>/events.jsonl
outputs/manuscript/chapter_NNN_candidate_final.md
outputs/manuscript/chapter_NNN_final.md
outputs/deliverables/book.md
outputs/deliverables/book.epub
outputs/artifacts/revisions.jsonl
outputs/state/promotion_receipts/<id>.json
outputs/quality/evaluation_reports/<id>.json
```

### Commercial story runs

Use `quality_policy=evidence_v1` with a Prompt containing exactly one approved
`[COMMERCIAL_STORY_JSON]` block. Prompt Intake activates the contract; no CLI
flag privately activates a commercial story. The run adds these artifacts:

```text
outputs/input/story-fingerprint.json
outputs/quality/story-originality-report.json
outputs/quality/commercial/chapter_NNN_design.json
outputs/quality/commercial/chapter_NNN_report.json
outputs/quality/commercial-free-trial-report.json
outputs/quality/commercial-book-report.json
```

The configured free window is exactly three or four chapters. After its final
chapter is promoted, `commercial.free_trial_review` verifies recognition,
pattern proof, a boundary test, local payoff, irreversible choice, visible cost,
and the next concrete expectation from exact candidate evidence. A blocker stops
the run before planning the next chapter.

After all configured chapters are promoted, `commercial.book_review` verifies the approved
conflict/resource progression, reader-value progression, hook distribution,
protagonist-caused turns, humiliation and resource-seeding budgets, and observable
payoff for at least two approved belonging anchors. It records factual
distributions rather than a composite score. A blocker stops ending review,
whole-book checks, publication copy, and compile.

`publication.copy` owns the reader guide projected before chapter one. Every
compiled Markdown, HTML, EPUB, PDF, and DOCX renders an explicit localized
`Introduction` / `导读` section containing a one-sentence hook and a source-bound
blurb. The copy must foreground the whole-book core conflict, promise at least
two concrete protagonist-driven cathartic rewards, and end on a consequential
open loop without revealing the ending. Policy or prompt-version changes
invalidate the publication-copy checkpoint before downstream recompilation.

Resume reuses either report only while all bound story/chapter contract heads,
final artifact revisions and SHAs, chapter commercial reports, promotion
receipts, and the free-window report remain unchanged. Retry the named blocked
review only after its upstream chapter evidence has been repaired and promoted.

The workflow uses local abstract contracts and static fingerprints. It does not
run online audience research, load advertising telemetry, retrieve raw sample
chapters, or use embedding/vector/RAG lookup. Those exclusions keep the source
corpus out of runtime prompts and reduce near-neighbor imitation risk.

---

## Story design to cover delivery

Use the cover workflow after the audience, exact title, core conflict,
protagonist agency, decisive story node, and fictional world are approved.
`novel-brainstorm-workshop` writes one strict JSON object between
`COVER_HANDOFF_BEGIN` and `COVER_HANDOFF_END`; `novel-cover-studio` reviews the
visual concepts before a billable image request.

Configure Sub2API in the ignored `.env` file. Cover-specific values are optional
when the writing endpoint and key can also access `gpt-image-2`:

```dotenv
NOVEL_OS_COVER_BASE_URL=https://your-sub2api-host.example/v1
NOVEL_OS_COVER_API_KEY=your-key
NOVEL_OS_COVER_MODEL=gpt-image-2
NOVEL_OS_COVER_SIZE=2048x3072
NOVEL_OS_COVER_QUALITY=high
NOVEL_OS_COVER_FORMAT=jpeg
NOVEL_OS_COVER_COUNT=4
NOVEL_OS_COVER_TIMEOUT_SECONDS=180
# Optional: omit these to reuse the writing model route.
NOVEL_OS_COVER_DIRECTOR_PROVIDER=openai_compatible
NOVEL_OS_COVER_DIRECTOR_MODEL=your-planning-model
NOVEL_OS_COVER_DIRECTOR_BASE_URL=https://your-text-endpoint.example/v1
NOVEL_OS_COVER_DIRECTOR_API_KEY=your-text-key
NOVEL_OS_COVER_DIRECTOR_TIMEOUT_SECONDS=180
```

Generate 3-5 independent candidates through the healthy Docker backend:

```bash
./deploy.sh novel-cover --help
NOVEL_OS_PROJECT_NAME='my-novel' NOVEL_OS_COVER_COUNT=4 \
  ./deploy.sh novel-cover './prompt/my-novel.md'
./deploy.sh novel-cover list 'my-novel'
```

This command does not invoke `up`, `restart`, or `down`. A failed cover request
does not pause, retry, or mutate a manuscript run. It records each candidate
separately so successful images remain reviewable.

Open `http://localhost:5174/projects/my-novel/covers` to:

1. inspect the source image and its recorded native resolution (all accepted covers preserve portrait `2:3`);
2. retry only a failed candidate;
3. reject an unsuitable direction after confirmation;
4. select one ready image as the delivery cover after confirmation;
5. download `book-package.zip`.

The CLI exposes the same recovery operations. Use revision values returned by
`list`; they are compare-and-swap guards, not arbitrary counters:

```bash
./deploy.sh novel-cover retry PROJECT COVER_SET CANDIDATE REVISION
./deploy.sh novel-cover reject PROJECT COVER_SET CANDIDATE REVISION
./deploy.sh novel-cover select PROJECT COVER_SET CANDIDATE REVISION ACTIVE_REVISION
```

When intentionally selecting a candidate from a Prompt version now marked
`stale`, append `--confirm-stale`. A 409 conflict means another operation changed
the set or active pointer; reload the list and review the current state before
submitting again.

To use the native path without Docker:

```bash
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover generate \
  --project './projects/my-novel' --prompt './prompt/my-novel.md' --count 4
```

Delivery artifacts:

```text
outputs/covers/sets/cover-<id>.json       durable candidate state
outputs/covers/index.json                 active-cover pointer and revision
outputs/deliverables/covers/pending/      current candidate projection
outputs/deliverables/covers/selected-cover.jpg
outputs/deliverables/covers/cover-set.json
outputs/deliverables/package-manifest.json
outputs/deliverables/book-package.zip
```

The original bytes are retained in the content-addressed media store. Selecting
a historical set restores bytes by candidate SHA-256 instead of trusting the
mutable `pending/cover-01.*` filename. Selecting another ready candidate or an
older set is the rollback operation: it advances the active pointer and rebuilds
the selected projection and ZIP while preserving prior cover-set records.

On another Codex host, sync both repository Skills; Docker does not load them:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$CODEX_HOME/skills/novel-brainstorm-workshop"
mkdir -p "$CODEX_HOME/skills/novel-cover-studio"
rsync -a --delete skills/novel-brainstorm-workshop/ \
  "$CODEX_HOME/skills/novel-brainstorm-workshop/"
rsync -a --delete skills/novel-cover-studio/ \
  "$CODEX_HOME/skills/novel-cover-studio/"
```

---

## 📚 Standard chapter loop

```mermaid
flowchart LR
    P[plan chapter] --> W[write]
    W --> E[edit]
    E --> Ck[check]
    Ck --> V[validate]
    V --> A[approve]
    A -.next.-> P
```

```bash
# 1. Plan Architect expands the chapter outline
python core/orchestrator.py plan chapter --number 1 --pov "Lena Vasquez"

# 2. Write Scribe drafts the prose
python core/orchestrator.py write --chapter 1

# 3. Edit Editor refines (pick a mode)
python core/orchestrator.py edit --chapter 1 --mode line
# Modes: line | developmental | pacing | dialogue | tension

# 4. Check free deterministic continuity scan
python core/orchestrator.py check --chapter 1

# 5. Validate LLM Guardian validates (pre-check findings are included automatically)
python core/orchestrator.py validate --chapter 1

# 6. Approve gated on Status: FAIL
python core/orchestrator.py approve --chapter 1
```

---

## 🧪 Dry-run mode (no API calls)

Every LLM-backed command supports `--dry-run`. It writes the prompt to disk so you can run it yourself in a chat UI, then submit the output via the `--draft-file` / `--edited-file` flags.

```bash
python core/orchestrator.py write --chapter 1 --dry-run
# -> outputs/chapter_001_scribe_prompt.md

# Run that prompt in ChatGPT/Claude/etc, save the response, then:
python core/orchestrator.py write --chapter 1 --draft-file my_response.md
```

Submitted files still get parsed `[SCRIBE_STATE_UPDATE]` / `[EDITOR_STATE_UPDATE]` blocks update state regardless of how the chapter was produced.

---

## 🔬 Auditing an existing project

```bash
python core/orchestrator.py status            # dashboard
python core/orchestrator.py check             # whole-project continuity scan
python core/orchestrator.py check --chapter 12   # as-of a specific chapter
python core/orchestrator.py character list    # full cast with arc state
python core/orchestrator.py plot list         # threads by priority
```

The continuity engine exits non-zero on critical findings wire it into CI if you care.

---

## ✍️ Hand-edited prose

Want to edit a chapter manually instead of running the Editor agent?

```bash
# Skip 'edit' entirely, then:
python core/orchestrator.py write --chapter N --draft-file my_revised.md
# Or replace the file directly:
# outputs/manuscript/chapter_NNN_revised.md
python core/orchestrator.py validate --chapter N   # Guardian still runs
python core/orchestrator.py approve  --chapter N
```

---

## 🔄 Re-running a phase

Phases are idempotent re-running overwrites the corresponding artifact and re-parses.

```bash
python core/orchestrator.py edit --chapter 3 --mode tension   # re-edit
python core/orchestrator.py validate --chapter 3              # re-validate
```

---

## 📦 Exporting

```bash
python core/orchestrator.py export --format markdown
# -> outputs/Your_Title.md
```

Only chapters with status `complete` are included. Approve everything before exporting.

---

## 🎯 Progress checkpoints

| Milestone | Typical % | What should be true |
|---|---|---|
| Story bible done | 5% | Setting, themes, world rules filled in |
| All characters defined | 10% | Each has desire, goal, fear, arc start |
| Outline approved | 15% | `outline.json` reviewed, acts sized |
| Act 1 complete | 25% | Catalyst landed, debate resolved |
| Midpoint | 50% | Stakes irreversibly raised |
| All-is-lost | 75% | Protagonist at lowest point |
| First draft complete | 100% | All chapters `approve`d, `export` runs |

---

*Workflows v1.1*
