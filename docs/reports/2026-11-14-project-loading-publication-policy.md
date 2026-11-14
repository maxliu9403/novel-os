# Project loading and publication counting compatibility

The project page's read-only task projection rebuilt the current publication
stage's execution authority even when its recovery pointer referred to a
completed earlier chapter request. The rebuild repeatedly verified the whole
book's promotion history. Real project and task requests exceeded 20 seconds;
the backend consumed about one CPU core while the page waited.

Commit `c546628` skips that rebuild only for a verified complete response from a
different stage, with no unresolved recovery probe. Executing, authorizing or
resuming a request still validates full authority. Invalid or unfinished
bindings do not take this path. After deployment, fresh browser navigation
renders the project; project and task GETs take about 0.2 seconds.

## Language diagnosis

The paused publication job used the display label `简体中文`, but the old
counter recognized only locale prefixes. Its three preserved hook candidates
contain 35, 39 and 44 content characters; they were incorrectly counted as 1,
1 and 2 words. Their blurbs contain 412, 412 and 578 content characters. All
three meet the intended CJK length ranges. This proves the length rejection was
incorrect; it does not replace the remaining semantic review.

New generation uses one shared language policy for its writer prompt, length
bounds, counters and repetition checks. Stored metadata is not translated or
rewritten.

## Compatibility correction

Independent review caught a regression in the first fix: applying the new
counter to already approved v3 artifacts could invalidate recorded counts.
For example, `英文` previously split a hyphenated English word; the new alias
mapping counts it as a single word. The regression fixture produced 25 expected
failures and one pass before the correction.

The corrected contract uses `publication-copy-policy.v4` and
`publication-copy-writer.v4` for new work. Parsing v1-v3 uses the original
counting behavior and still checks the stored unit and both counters exactly.
Unknown policy versions are rejected. New candidates cannot choose a legacy
policy to bypass current checks.

Completed publication checkpoints retain their original policy hash only when
their other inputs have not changed. The parsed artifact must match that
checkpoint's policy. Recompiling unchanged approved copy therefore neither
rewrites it nor invokes a model. A missing or corrupt accepted artifact remains
a recovery error in completed, paused and failed runs; it is not a reason to
silently replace approved material. Changed source inputs require v4 generation.

## Verification and operational boundaries

Regression coverage includes historical aliases and v1-v3 receipts, altered
counters, v4 round trips, policy/checkpoint mismatch, changed source input,
compilation recovery and missing accepted artifacts. Tests use local model
substitutes. All 17 existing production publication artifacts were parsed
read-only without changing their bytes.

The first deployment preserved 47,729 data files and 16 SQLite tables. The
103-chapter task `fac096deaf31` remains paused at `publication.copy`; its prose,
outlines, checkpoints and failed responses are intact. This repair does not
authorize treating an old candidate as semantically approved or clear the
historical error without a successful run. No real model request has been sent
as part of this repair.

The earlier full-suite attempt included unrelated audience sample, telemetry
and Gemini SDK/environment failures. Only the targeted results should be
reported as passing; this report does not claim a green full suite.

The final affected-area regression run collected 587 tests: 586 passed and one
optional external H5 Go importer check was skipped because its pinned source
checkout is not configured. The run covers publication, pipeline recovery,
project/task APIs, compilation, packaging and local H5 contracts. The 34 new
policy/checkpoint cases pass, and independent review found no remaining issues.
