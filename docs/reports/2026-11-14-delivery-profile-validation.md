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

Release preparation and evidence: `output/delivery-profile-release-20261114/`.
At preparation time another task, `fac096deaf31`, was running and reusing saved
chapters. No live cutover or task interruption has been performed at this point.
