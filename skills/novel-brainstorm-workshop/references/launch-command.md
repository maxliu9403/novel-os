# Read-only handoff and complete launch command

Run the helper against the trusted Novel OS checkout that will execute the book.
It reads the checkout's actual `prompt_intake.build_brief`, classification and
narrative-format parsers; it does not copy their JSON schema into the Skill.
It also inspects `deploy.sh` and the CLI argument declarations without importing
or running the orchestrator. A missing or incompatible launcher is an error,
not a successful compatibility check.

## Validate a final prompt

From the repository root:

```bash
python3 skills/novel-brainstorm-workshop/scripts/handoff.py validate \
  './prompt/my-novel.md' --repo .
```

If the Skill has been installed elsewhere, invoke its own `scripts/handoff.py`
and give `--repo /path/to/Novel-OS` and the prompt's path explicitly. There are
no developer-specific paths or model credentials in the Skill. Use a Python
interpreter compatible with the selected checkout (Python 3.10 or later for
this helper).

The standard top block contains `Title`, `Author`, `Genre`, `Audience`,
`Language`, `Tone`, `POV`, `Chapters`, and `Words` exactly once, before the first
`##` heading. `Author:` may intentionally be blank; the normal creation flow
can generate a pen name later. Validation never generates an author. Write
integer chapter and total-word targets without thousands separators. Do not
reuse these metadata labels or their intake aliases (such as `Primary audience`
or `Point of view`) later in the document: the runtime intake scans the entire
prompt, so later labels could silently override the header.

Include exactly one complete `[NOVEL_CLASSIFICATION_JSON]` block and one
complete `[NARRATIVE_FORMAT_JSON]` block. The latter records the user's chosen
mode and `confirmation_status: confirmed`; only write that declaration after
the user has actually confirmed the choice. The helper cannot prove consent.

Validation checks the metadata, actual engine JSON contracts, chapter/word
target agreement, and recorded structure confirmation. It does **not** certify
story quality, emotional depth, whether human confirmation really occurred,
or unwritten prose. Volume word-budget differences are advisory; per-chapter
length is a flexible creative target, not a hard pass/fail condition. A
consistent total target in metadata and JSON does not mean exact prose length.
Complete the Skill's human-facing content checklist separately.

This is an in-memory check. Do not substitute `NOVEL_OS_DRY_RUN=1`: the existing
dry-run path persists intake/project files and is not a read-only validator.

## Generate the command without executing it

```bash
python3 skills/novel-brainstorm-workshop/scripts/handoff.py command \
  './prompt/my-novel.md' --repo . --project 'my-novel'
```

The validated shell command goes to stdout; metadata, mode and limitations go
to stderr. It contains real line continuations and safely quoted values,
including apostrophes, dollar signs and shell substitutions. Do not rebuild
it using string interpolation or insert literal `\\n` sequences. Generated
paths are absolute for the selected deployment; on another machine run the
helper again with that machine's repository and prompt paths.

The project name must be a single directory name. Prefer a descriptive slug
such as `my-novel-book-02`. The current launcher removes the final dot suffix
even from an explicitly supplied project name; this helper therefore rejects
dotted names instead of silently starting under another name. Control
characters are also rejected. Do not overwrite an existing project merely
because its name matches: choose the intended new project, or use the
separately authorized resume workflow.

Default generated settings are:

| Environment variable | Value/source |
|---|---|
| `NOVEL_OS_NONINTERACTIVE` | `1` |
| `NOVEL_OS_PROJECT_NAME` | Explicit `--project` |
| `NOVEL_OS_TITLE` | Final prompt title, preserving punctuation |
| `NOVEL_OS_GENRE` | Final prompt genre |
| `NOVEL_OS_CHAPTERS` | Current book's total chapters |
| `NOVEL_OS_WORDS` | Current book's flexible total target |
| `NOVEL_OS_APPROVAL` | `auto` |
| `NOVEL_OS_EDIT_MODE` | `line` |
| `NOVEL_OS_QUALITY_POLICY` | `evidence_v1` |
| `NOVEL_OS_MAX_RETRIES` | `5` |
| `NOVEL_OS_MAX_QUALITY_REPAIRS` | `2` |
| `NOVEL_OS_OUTPUT` | `markdown html docx epub pdf` |
| `NOVEL_OS_DRY_RUN` | `0` |

The helper accepts optional `--output`, `--approval`, `--edit-mode`,
`--quality-policy`, `--max-retries`, and `--max-quality-repairs`; supported
choices are checked against the selected checkout. All defaults above are
explicit in the command. Author, language, audience, tone, and POV belong in
the MD; do not invent `NOVEL_OS_AUTHOR`, `NOVEL_OS_LANGUAGE`, `NOVEL_OS_SERIES`,
or `NOVEL_OS_VOLUMES` launcher options. Connection settings, model credentials,
data directory and web port inherit the user's deployment configuration.

When the author explicitly chooses the read-only writing review setting, pass
`--method-mode off` or `--method-mode advisory` to the helper. It verifies the
launcher's support and emits `NOVEL_OS_METHOD_MODE` for the existing engine
option. Omit it to inherit the project's policy. The Web creation entry passes
the author's selected value so its copied command and one-click run agree.

ZIP packaging is an existing delivery phase, not an `output=zip` format.
Requesting all five formats does not prove the exports have already succeeded.
The cover workflow remains separate; supply its handoff and use the existing
Cover Studio when the user requests cover work.

## Structure modes and launch scope

| `NARRATIVE_FORMAT_JSON.mode` | Scope of one launch |
|---|---|
| `short_novel` / `standalone_long` | One book, continuous chapters |
| `multi_volume` | One book, multiple volume spans, continuous chapter numbers |
| `series_installment` | Only this installment of the named series |

Volume membership and series identity are encoded in the existing JSON
contract, not in a new shell flag. For a multi-book series, supply a series
overview plus a complete final prompt and command for **each** installment.
Each command's chapter count and word target describe that installment, not
the entire series. Include independent book payoffs and cross-book continuity
in the prompts. Do not claim that one launch automatically runs later books.

Only execute a generated command when the user has authorized starting the
novel. Otherwise deliver it for review. When execution is authorized, use
`deploy.sh novel`; its healthy-backend path reuses the existing service.
Retain the deployment's configured port (5174 by default). Do not start a new
frontend, change ports, or run `deploy.sh restart` merely to launch a book.

Report actual output paths from the deployment configuration and run results.
The default project root is `docker-data/projects/<project>/outputs`; a custom
`NOVEL_OS_DATA_DIR` changes it. Do not report delivery or chapter checks as
complete before the run has produced and verified those artifacts.
