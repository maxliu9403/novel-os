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

Run unattended and compile Markdown plus EPUB:

```bash
python core/orchestrator.py run \
  --project ./projects/my-novel \
  --prompt ./prompt/master-prompt.md \
  --chapters 24 \
  --words 80000 \
  --approval auto \
  --output markdown epub
```

The runner processes chapters sequentially because each chapter updates the
facts used by the next chapter. It persists `run.json`, `events.jsonl`, artifact
hashes, prompts, and raw provider responses so an interrupted run can resume.

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
