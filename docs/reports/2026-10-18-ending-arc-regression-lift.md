# Ending arc regression: When the Lift Doors Open (3822f6ff360a)

## Symptom

The paused full-book task stopped at `ending.review` with:

```text
Book ending gate blocked compile: Character ruth_mercer arc stage is middle, expected resolution.
```

Thirty chapters had already been approved; the failure was in the book-level gate,
not in chapter generation or prose delivery.

## Root cause

Chapter 22 recorded Ruth Mercer's arc as `resolution / 100` in both the Scribe
and Guardian proposals. Chapter 24's Scribe estimate then reported
`middle / 85` because that chapter only covers the return agreement, not the
character's final book-level state. The old parser overwrote the character
index with every later estimate. The ending gate read only that index, so the
already-achieved resolution disappeared.

The chapter records were not wrong; the derived index update was non-monotonic.

## Fix

### 1. Monotonic parser guard

`core/state_parser.py` now applies arc stage/progress through
`_advance_arc_state`:

- stage rank cannot decrease (`beginning < middle < climax < resolution`);
- progress cannot decrease;
- `100` progress implies `resolution`; `resolution` implies `100`;
- a rejected estimate is retained in the chapter event as
  `reported_stage` / `reported_progress` with `regression_ignored: true`;
- the effective event records the accepted stage/progress.

### 2. Historical gate read

`core/ending_quality.py::_arc_stage` reads the highest stage/progress across
the evaluated chapter history and caps the scan at `as_of_chapter`. Existing
books whose current index was already regressed can pass without editing
immutable chapter records.

### 3. DeepSeek reasoning budget

The first recovery passed `ending.review` and `book.check`, then exposed a
second issue at `publication.copy`: three Guardian calls returned empty
content. Usage showed `output_tokens = 8192` and `reasoning_tokens = 8192`,
i.e. a max-effort thinking pass consumed the whole 8K output budget.

`core/llm_client.py` now applies a minimum output budget for DeepSeek:
`low 8192`, `high 16384`, `max 32768` (empty effort uses the provider default
`high`). The floor is a cap, not a charge; only generated tokens are billed.
The task's current state index was also repaired to `resolution / 100` after
completion, with a pre-repair backup.

## Verification

- Production task resumed with run `3822f6ff360a`:
  `ending.review`, `book.check`, `publication.copy`, `compile` and
  `delivery.package` all completed; `run.completed`.
- `book_completion_report.json`: `pass`, critical 0, warnings 0.
- Deliverables: Markdown, HTML, DOCX, EPUB, PDF and H5 package under
  `outputs/deliverables/`.
- `book-package.zip`: 355581 bytes, 43 members, CRC pass,
  SHA256 `c6fe7f517a1819df09d515549fd8597f8922457fcfb8603c6b5f706ff29a1a5f`.
- Delivery manifest: 42 files checked against size/SHA, no mismatches.
- Regression tests: parser monotonicity, historical resolution, future-chapter
  exclusion, DeepSeek output floor. Full backend suite run separately.

Backups: `/private/tmp/novelos-lift-arc-recovery-20261018T140641` (pre-resume
project, SQLite and Studio settings) and
`/private/tmp/when-the-lift-story_state-pre-arc-repair-20261018T143744.json`.
