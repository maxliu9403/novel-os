# Cover Handoff Contract

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
- Derive 3-5 concepts. A count outside that range is a validation error before any provider call.
