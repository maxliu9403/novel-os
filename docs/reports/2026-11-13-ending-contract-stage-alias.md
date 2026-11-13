# Ending contract stage alias diagnosis and repair

## Diagnosis

Task `ac3f15f6efff` was blocked in `ending.review` because its persisted
`outputs/input/ending_contract.json` used `required_arc_stage: "completed"`
for Dahlia, Amias, Sofia, and Olivia. The canonical character lifecycle in
Novel OS is `beginning → middle → climax → resolution`; `StoryState` already
recorded all four characters at `resolution / 100` with outcome states and
terminal evidence. The failure was a contract vocabulary mismatch, not an
unfinished manuscript arc.

The deterministic reproduction was the existing project copied to a temporary
directory and evaluated with `evaluate_ending(..., as_of_chapter=60)`. Before
the change it produced the four reported `character_arc_unclosed` errors. The
same fixture now passes with no critical findings and reports four explicit
compatibility notes.

## Systemic repair

- `core/plot_thread_contract.py` now owns the shared arc vocabulary and maps
  the historical contract spelling `completed` to canonical `resolution`.
- New staged and story-first foundation candidates normalize the alias before
  persistence, so future contracts use one canonical value. The prompt also
  explicitly forbids `completed` as a lifecycle value and directs semantic
  completion to `required_outcome` and evidence.
- Existing contracts are read through the compatibility mapping without
  rewriting their original contract value or the canonical story state.
- Ending-quality evaluation and source-bound ending evidence use the same
  canonicalization helper, preventing a second mismatch during review or
  recovery.
- Unknown stage values still fail with a clear contract error; no fuzzy
  acceptance or story-state mutation was added.

## Verification

The focused ending, evidence, outline, orchestrator, pipeline, and state-parser
regressions pass. The original project was not modified during reproduction;
the two derived files touched by the diagnostic invocation were restored byte
for byte from the release backup. The live service was not restarted or
deployed per the author's instruction, so the API continues to show the
historical paused error until a later approved deployment.
