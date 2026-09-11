# Cover Handoff Contract

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
- Preserve title spelling and language exactly. Candidate prompts render the exact title once and no other copy.
- Story-facing `world_signals` use invented or abstract locations. Real market locations stay in `audience_research` and never enter cover prompts.
- Use only confirmed story facts. Visual styling may interpret mood, composition, lighting, and palette, but not invent identity, relationships, spoilers, or world rules.
- State `core_conflict` as a concrete cause-and-consequence relationship. Preserve a confirmed betrayal, exclusion, divided loyalty, concealed choice, competing family structure, institutional pressure, or environmental threat precisely; do not collapse it into generic sadness or distance, and do not strengthen ambiguity into an unsupported affair or other event.
- Derive 3-5 concepts. A count outside that range is a validation error before any provider call.

## V2 direction gate

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
  12,000 Unicode code points before calling `gpt-image-2`.
- New directions use `cover-profiles.v8` and explicitly declare live-action
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
- Automated semantic correction records both attempts. A correction replaces the
  active candidate only with complete passing story/craft scores, no known score
  regression, and no blockers or repair codes. Otherwise retain the original and
  add `repair_not_promoted`. This does not select a cover for publication.

- V8 keeps the established JSON scene schema and flexible portfolio coverage.
  Acting instructions belong in `frozen_action`, `gaze_graph`, `blocking` and
  `depth_plan`; no new required schema fields. V13 protects a compact human
  performance module through normal compilation and face/action repairs.
  Existing approved direction facts and images remain unchanged; replan to
  obtain newly designed performance rather than only retrying an old gesture.
