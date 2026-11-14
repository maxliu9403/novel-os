# Delivery receipt profile compatibility

## Failure and cause

Task `ac3f15f6efff` completed its manuscript and package. After selecting a cover,
the saved delivery marker was `ready`, but consumers rejected the package with
`delivery_validation_version`. The actual ZIP independently passed inspection,
and all 148 source hashes matched the receipt. Rebuilding would reproduce the
same failure without changing this mismatch.

`publication_taxonomy.export_target()` selects the H5 export target from the
published classification. Dynamic Genre/Tags use `novel-classification.v2` and
`novel-h5-export.dynamic-taxonomy.v1`, with a digest of the published taxonomy.
The package builder correctly saved this inspector report. The receipt reader
still compared every package against the old static `PROFILE` constants.

## Fix

- Receipt parsing continues to validate schema, successful engine status,
  source snapshot and archive digest shapes.
- Source-bound validation reads the published manifest and its declared
  classification. Canonical classification parsing and the same `export_target()`
  used by the builder determine the expected target, validator revision,
  taxonomy digest and required capabilities. Mutable catalogue settings and
  unrelated leftover sidecars do not select the target.
- These checks belong to the dependency-aware cache, not the receipt-only cache.
  Source or receipt changes invalidate cached results. Status views do not
  decode images or inspect ZIP contents.
- Download still independently inspects the exact ZIP and now also compares its
  target fields with the receipt. Re-labelling a dynamic ZIP as static cannot
  bypass this check. Malformed canonical metadata yields a recoverable delivery
  conflict instead of an unhandled parser exception.
- Static and unclassified packages keep their existing target. No receipt,
  manuscript, cover selection, checkpoint or historical input is rewritten.

## Verification

Before the fix, three full consumer tests (unspecified, male and female channels)
failed with the exact `delivery_validation_version` exception immediately after
successful package creation. They now pass through binding, download validation,
delivery status, project list/detail and HTTP ZIP download.

The affected-area suite passed **169 tests**, with **1 skipped** external pinned
H5-source check. Coverage includes ten target-field tampering cases, independent
inspection of a forged static projection, source-cache invalidation, malformed
classification metadata, old static/unclassified delivery, selected-cover local
rebuild, retained bytes and read-only status/download behaviour. Fixtures use
temporary directories and no model calls.

Read-only verification of the real task using the fixed code returns `ready` and
the unchanged ZIP SHA-256:
`75940a9d4079e75e96219fde766c056ebec4524ba62c2fc61ed34b7a45d9993d`.
This proves local engine validation, not successful import into an external H5
installation.

## Deployment and authorized resume

After author approval to pause/deploy/resume, the original pause API accepted the
active `fac096deaf31` job. Its callable finished at `2026-11-14T07:17:21Z` with
the normal paused result. We required terminal job status, no active task job or
unknown request, and idle project/workshop/cover workers before backing up data.
No force termination was used.

Commit `e93c1e41cafb76ca41dbca5a93c551570266dd70` was deployed as
`novel-os-backend:delivery-profile-e93c1e41c`, image
`sha256:c2a138eff7c6fbde1e146cc2878f995ff542a69d25394bff59c6d4a81758e279`.
The isolated read-only, network-disabled image passed four delivery profile
scenarios with no production mounts. The frontend stayed unchanged; nginx was
reloaded. Runtime source (541 files), frontend assets (46), environment, mounts,
ports, 47,732 data files and 16 SQLite tables were verified after cutover.

The real `ac3f15f6efff` project now reports `delivery.status=ready`. Its package GET
returned HTTP 200, 12,905,880 bytes, with the exact pre-fix digest above. All 148
bound sources were unchanged. Neither package regeneration nor manuscript
generation was needed for this fix.

The original `fac096deaf31` resume API was called once, returning job
`a1265c8fb6fd4d6ebe3bb53cdac569d4`. Post-resume verification found the job running
with no job error, all 103 chapter promotion records intact, and all 104 saved
prose/input hashes unchanged. At that observation it was still executing local
integrity verification before entering the pipeline: native `run.json` retained
the previous paused/book.check state and cancellation message while the public
task projection correctly reflected the active running job. This is not evidence
of completed publication. Always read current state before another operation.

The slow pause exposed a separate existing issue: whole-book source validation
repeatedly reads all promotion records for each chapter and checks cancellation
only after the stage returns. This release does not change that execution path.

Release evidence, consistent data backup and image-only rollback configuration:
`output/delivery-profile-release-20261114/`. Never restore that older data snapshot
over later user progress when rolling back an image.
