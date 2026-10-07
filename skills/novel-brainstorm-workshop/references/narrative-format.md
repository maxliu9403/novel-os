# Author-confirmed narrative format

Read this reference before producing a launchable Prompt. Structure is an author
decision, not a genre-based automatic split. Ask only for missing choices and
allow later revisions. Word targets are approximate; the exact chapter counts
and structural boundaries identify what the run will produce.

## Three reader-facing choices, four engine modes

| Author's choice | Engine `mode` | Deliverable and scope |
| --- | --- | --- |
| One continuous short novel | `short_novel` | One Prompt, project, command and book |
| One continuous long novel | `standalone_long` | One Prompt, project, command and book; no internal division into several volumes |
| One book divided into volumes | `multi_volume` | One Prompt, project and command; continuous global chapter numbers and several volume spans |
| Several separately publishable books in a series | `series_installment` for each book | Series overview and shared design, plus a separate Prompt, project and command for every installment |

“长篇” alone does not mean “分卷”. A chapter count alone does not decide whether
the author wants a series. For a long work with no packaging decision, ask
whether it is continuous, divided into volumes, or several separately published
books. Reuse explicit answers such as “80 chapters in four volumes”; do not
ask again. If “子系列” is ambiguous, explain these choices and clarify it.

For a volume structure, establish each span's start/end chapters, target length,
distinct central conflict, payoff and carryover. For a series, establish series
identity, planned book count, each book's chapter count and length, shared canon,
individual ending and authorized cross-book promises. Each installment may
itself have internal volumes. Do not invent nested series schema fields.

## What current Novel OS actually supports

The existing engine reads `[NARRATIVE_FORMAT_JSON]` from the Prompt. There is no
`NOVEL_OS_SERIES`, `NOVEL_OS_VOLUMES`, or `--series` switch in `deploy.sh novel`.
Chapter planning binds chapters to volume obligations and long-form audits.
All modes still write the current book sequentially, chapter 1 through N.

- `multi_volume`: 80 chapters split 1–20, 21–40, 41–60, 61–80 is **one run**.
  Compilation produces the current book's files; do not promise four separate
  EPUBs or volume title pages in every export format.
- `series_installment`: three 21-chapter books are **three separate runs**.
  Each command uses `CHAPTERS=21` and that book's target words, not 63 chapters
  and the whole series budget. Series identity is metadata, not automatic
  scheduling, cross-project memory or inheritance of the prior book's canon.

## Confirmed configuration

Emit exactly one complete block after the classification block:

```text
[NARRATIVE_FORMAT_JSON]
<complete JSON serialized by the current engine>
[/NARRATIVE_FORMAT_JSON]
```

The marker above is a template, not a deliverable. Generate actual JSON with
the current repository's `core.narrative_format` helpers. Always pass the
**approved mode explicitly**: inference from chapter count alone can select
`multi_volume` even when the author requested a continuous 80-chapter novel.

Use `source="author_selected"` for a direct choice, or
`source="recommended_then_confirmed"` for an accepted proposal. Set
`explicit_length=True` after confirmation so `confirmation_status` is
`confirmed`. These flags record confirmation; calling the helper does not
create user approval. Advisory `engine_recommended/pending_confirmation`
objects stay in workshop drafts.

This read-only example emits a valid **80-chapter continuous** configuration;
replace the example story and numeric inputs with the approved design. Run
from the selected Novel OS checkout with its Python environment:

```python
import json
from core.narrative_format import infer_narrative_format
from core.novel_classification import infer_classification

classification = infer_classification(
    genre="Fantasy", premise="An archivist must restore stolen memories."
)
# For production use the already approved classification object, not a second
# independent classification guess.
format_contract = infer_narrative_format(
    classification,
    chapters=80,
    target_words=72000,
    mode="standalone_long",
    volume_count=1,
    explicit_length=True,
    source="author_selected",
)
print("[NARRATIVE_FORMAT_JSON]")
print(json.dumps(format_contract.to_dict(), ensure_ascii=False, indent=2))
print("[/NARRATIVE_FORMAT_JSON]")
```

For a four-volume version, change `mode="multi_volume", volume_count=4`.
For book two of a three-book series, use `mode="series_installment"`, this
book's own chapter/word targets, its internal `volume_count`, and:

```python
series_id="memory-archive",
series_title="The Memory Archive",
series_book_number=2,
planned_books=3,
```

The serialized nested keys are `series.series_id`, `series.title`,
`series.book_number`, and `series.planned_books`. Empty series fields belong
to non-series modes; series identifiers are required for `series_installment`.

The helper divides spans approximately evenly. If the author approved unequal
volumes, use `dataclasses.replace` with a complete tuple of
`core.narrative_format.VolumeSpan` objects before calling `to_dict()`:

```python
from dataclasses import replace
from core.narrative_format import VolumeSpan

format_contract = replace(
    format_contract,
    mode="multi_volume",
    volumes=(
        VolumeSpan("volume_01", 1, 1, 15, 13500, "discovery"),
        VolumeSpan("volume_02", 2, 16, 45, 27000, "costly_counteraction"),
        VolumeSpan("volume_03", 3, 46, 80, 31500, "accountability"),
    ),
)
```

Volume ids and numbers must be contiguous; spans cover chapters 1 through N
exactly once with no gaps/overlaps. Allocate the **planning** word total among
volumes consistently; it is not a final prose quota. Use `to_dict()` after any
change so `format_id` and other derived values are recalculated. Do not invent
or hand-edit a hash. Validate the final Prompt with `scripts/handoff.py` as
documented in [launch-command.md](launch-command.md).

## Design capacity and revisions

Assess conflict renewal, character-arc stages, reveal depth, interacting
subplots, world expansion and distinct volume payoffs. The engine's suitability
score is advice, not permission to override the author's format. If a premise
does not sustain the requested scale, propose substantive expansion or a shorter
form; do not fill chapters with recycled humiliation or repeated arguments.

For each volume plan a distinct conflict, reader promise, irreversible character
shift, climax, observable payoff, and nonfinal carryover. Preserve the existing
volume milestone protocol (`volume_01_promise`, `volume_01_midpoint`,
`volume_01_climax`, `volume_01_payoff`, `volume_01_carryover`) used by chapter
`world_event_ids` and long-form audits. This is an existing runtime contract.

When the author changes mode, boundaries or count, revise the chapter map,
reveal/payoff timings and ending first, regenerate the format object, then
regenerate the command. The latest accepted structure replaces the old one;
never export both configurations in the same Prompt.

## Series handoff

Create a readable series overview in `prompt/<series>/series-bible.md`, plus
`book-01.md`, `book-02.md`, etc., with distinct safe project names. The overview
contains the series promise, shared identities and world, timeline, per-book
goals and closures, knowledge boundaries, and cross-book payoff ledger. It is
**not** itself a launchable single-book Prompt.

Each book Prompt contains the relevant shared facts inline, its own complete
chapter map and format block, and explicit intended starting/ending states.
Do not rely on the engine following a link to another project. Later-book
plans may be prepared now; mark their unverified prior-book assumptions in the
handoff report. Before running a sequel, reconcile those assumptions with the
prior book's actual approved ending and known facts. Design intentions are not
already written canon.

Provide separate validated commands. Do not generate an automatic series runner,
execute all installments, or promise automatic carryover under this Skill.
