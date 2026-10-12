# Source and Cover Handoff Contract

## Current source-bound workflow (v11 / compiler v18)

New work first captures a complete skeleton snapshot from project input or the selected workshop revision, then completes segmented story analysis with evidence coverage. The director receives the complete verified analysis and frozen source binding. A handoff block remains a compact compatibility projection; it does not replace the complete skeleton or permit partial-analysis fallback.

### Brainstorm-to-Studio boundary

New schema-v2 handoffs may include an optional `cover_constraints` object. It is
owned by the brainstorm stage and contains only story-facing constraints:
confirmed facts to preserve, user-selected nodes or characters, forbidden
content, a spoiler ceiling, visual preferences, and source references. Treat it
as a compact input filter, never as a precomputed cover plan. Typed node,
character, signal, and evidence references are machine-checked; free-text
facts and spoiler wording are passed into the prompt and still require the
Studio's source review.

The Studio owns the following outputs and must derive them from its complete
source analysis: cover epoch selection, `scene` or `campaign_composite`, cast
blocking, visible action and reaction, visual hook, camera, lighting, palette,
title placement, and the direction portfolio (four by default, 3–5 when explicitly configured or requested). Do not add a parallel
`candidate_moments`, `cast_priority`, `visual_motifs`, or `inference_policy`
contract to the skeleton; those concepts already belong to the Studio's
campaign and visual-assumption models.

Cache the complete analysis by immutable source digest. Direction changes,
quality review, and image regeneration reuse that analysis. Re-run analysis only
when the skeleton, source policy, or analysis configuration changes.

A campaign explicitly chooses `scene` or `campaign_composite`. Scene mode is one continuous instant. Composite mode validates every layer, source epoch, narrative function and cross-layer relationship; it must not invent meetings, chronology or character relationships. Character count is not a quality metric. Separately choose expression_mode: literal_scene preserves an actual event, while editorial may use portraits, objects, environments, juxtaposition or aftermath to convey the core premise. Editorial designs need genuine source anchors but not a quoted event for every pose. Explicit story_moment layers and physical interactions still preserve source facts. Compatible visual ambiguity is advice.

`cover-profiles.v11` and `cover-compiler.v18` freeze new executable recipes before approval. V10 campaigns without expression_mode keep that field absent and retain v17 recipes and old hashes. A single-candidate revision can preserve such historical siblings byte-for-byte. `source_binding` and `compiled_recipes` are optional additions; absent fields in historical records remain absent so old digests stay valid. Approval binds source/analysis/brief/direction/recipes. Stale-source policy and any pinned-snapshot choice are explicit.

The default title mode is `composed`: the provider creates text-free artwork, while exact title text and approved layout are rendered locally with a pinned font digest. Preserve artwork/final/layout/font hashes. Native-title recipes are explicit alternatives. A native image without a validated title mask has no independent blind evidence; a valid mask supports only a partial read.

Direction review classifies hard factual/author/source errors separately from optional commercial advice. Target-only review reuses unchanged siblings only after their actual approved source/analysis/shared/plan provenance matches; otherwise complete facts are rechecked. Quality uses independent A (neutral artwork observation) and B (story verification with frozen A), with the same distinction for new explicit expression modes. Scores do not automatically turn stylistic weakness into factual failure. Missing scores remain unknown; uncertainty and text contamination are durable. Design failure returns to direction approval; evaluation failure retries evaluation only. Extra image attempts default to zero and require explicit approval, with failed calls and manual retries counted cumulatively. No quality finding itself grants image spend.

## Historical handoff input


Schema v1 remains accepted as source input, but new direction and image work
normalizes it to schema v2 before approval. Missing age, lived identity, or
environment facts become visible `pending_confirmation` assumptions; they are
not inferred from audience or market labels.

Read one JSON object between these exact boundaries. Parsing is strict JSON, not YAML or prose extraction.

```text
COVER_HANDOFF_BEGIN
```json
{
  "schema_version": 1,
  "title": "Exact approved title",
  "author": "",
  "language": "English",
  "genre": "primary and secondary genre",
  "target_audience": "user-confirmed audience and reading motivation",
  "market_scope": "release market branch",
  "core_task": "protagonist objective and reader-facing premise",
  "core_conflict": "specific opposition, stakes, and power imbalance",
  "emotional_promise": "dominant reader emotion and earned payoff",
  "protagonist": {
    "role": "story role",
    "visual_identity": "age band, presentation, wardrobe, posture, expression",
    "agency_signal": "visible decision or action"
  },
  "relationship_or_power_contrast": "visualizable contrast",
  "decisive_story_node": "major irreversible event suitable for the cover",
  "secondary_task": {
    "story_function": "supporting pressure or promise",
    "visual_signal": "one person, setting feature, or symbolic object"
  },
  "world_signals": ["fictional setting, institution, era, technology, or rule"],
  "title_direction": {
    "hierarchy": "dominant title",
    "preferred_zone": "top",
    "readability": "mobile_thumbnail"
  },
  "forbidden_elements": ["real places", "logos", "watermarks", "unsupported spoilers"]
}
```
COVER_HANDOFF_END
```

## Validation

- Require title, language, genre, target audience, market scope, core task, core conflict, emotional promise, all protagonist fields, power contrast, decisive node, and a secondary visual signal.
- Preserve title spelling and language exactly. Native-title prompts render the exact title once and no other copy. Composed recipes keep the title out of the image prompt and render it from the approved layout.
- Story-facing `world_signals` use invented or abstract locations. Real market locations stay in `audience_research` and never enter cover prompts.
- Use only confirmed story facts. Visual styling may interpret mood, composition, lighting, and palette, but not invent identity, relationships, spoilers, or world rules.
- State `core_conflict` as a concrete cause-and-consequence relationship. Preserve a confirmed betrayal, exclusion, divided loyalty, concealed choice, competing family structure, institutional pressure, or environmental threat precisely; do not collapse it into generic sadness or distance, and do not strengthen ambiguity into an unsupported affair or other event.
- Derive four concepts by default; explicit configuration or requests may choose 3–5. A count outside that range is a validation error before any provider call. Existing approved plans and image allowances remain unchanged when the default changes.

## Historical V2 scene direction gate (v8 / compiler v13)

These constraints describe legacy scene-only records. Keep them readable without rewriting approved hashes. For new source-bound work, apply the current campaign contract above; a validated campaign composite is not rejected simply for using layers.

- `principal_characters` contains every required protagonist with
  `must_appear: true`. It may also contain a small number of story-confirmed,
  cover-relevant conflict participants with `must_appear: false`; these are an
  approved visual pool, not mandatory cast in every concept.
- Include all required protagonists in one continuous scene. A concept may add
  approved optional conflict participants when they make the emotional cause
  legible. For four or more visible characters, keep one or two foreground
  emotional anchors and stage the causal relationship action in the
  middle/background rather than arranging a flat group portrait.
- Every scene records cast IDs, evidence refs, frozen action, gaze graph,
  blocking, lived-environment anchors, primary prop, camera/depth/light, title
  safe zone, and a truthful `VisualHook`.
- Persist the normalized brief beside the immutable direction. Approval requires
  its exact `brief_sha256` and `direction_sha256`.
- Compile facts before style and reject critical assumptions, invented identity,
  unknown evidence, extra text, real landmarks, collage scenes, and prompts over
  12,000 Unicode code points before calling the configured image model.
- Historical photographic directions use `cover-profiles.v8` and explicitly declare live-action
  photography in `art_style`. Illustration, oil painting, relief and CGI are
  rendering mismatches, not alternative diversity slots. Older directions remain
  readable; create a new photographic direction rather than rewriting their hashes.
  New directions derive a source-bound core-conflict
  visual contract and require visible cause and consequence in every plan.
  At least one plan shows the complete causal relationship directly and at least
  one shows a concrete active protagonist decision. Other plans may use visible
  evidence of pressure, not generic sadness; no fixed ensemble percentage.
  Historical v5/v6 directions retain their original validation rules.
- `cover-compiler.v13` protects executable lighting, color, camera, spatial
  grammar and typography as well as story locks. It trims explanatory rationale
  under the shared 12,000-code-point budget, never the tail of a lighting plan.
  Repairing a historical prompt recompiles from its approved scene fields.
  The title is one continuous readable lockup in exact word order.
- Historical corrective attempts retain their original promotion evidence and
  `repair_not_promoted` result. They do not authorize another call. New semantic
  failures return to direction approval; all new extra image spend is explicit.

- V8 keeps the established JSON scene schema and flexible portfolio coverage.
  Acting instructions belong in `frozen_action`, `gaze_graph`, `blocking` and
  `depth_plan`; no new required schema fields. V13 protects a compact human
  performance module through normal compilation and face/action repairs.
  Existing approved direction facts and images remain unchanged; replan to
  obtain newly designed performance rather than only retrying an old gesture.
