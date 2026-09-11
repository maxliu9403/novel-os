# Author-confirmed narrative format

Novel OS separates story classification from publication shape. The engine may
estimate long-form capacity, while the customer selects the final length and
packaging mode.

## Workshop interaction

When the customer has not supplied a chapter count or length choice, ask:

1. short novel;
2. standalone long novel;
3. one multi-volume novel;
4. AI recommendation followed by customer confirmation.

For a long form, distinguish:

- `standalone_long`: one continuous book without internal volumes;
- `multi_volume`: one novel divided into volumes, such as 80 chapters in four
  20-chapter volumes;
- `series_installment`: one separately publishable book belonging to a
  multi-book series. A series installment may itself contain volumes.

An already stated target such as “80 chapters divided into four volumes” is an
explicit customer choice. Summarize it in the Section D review and do not ask a
duplicate question.

## Decision authority

- `author_selected`: the customer chose the format directly.
- `recommended_then_confirmed`: the workshop proposed it and the customer
  accepted it.
- `prompt_explicit`: a direct Prompt supplied the chapter target.
- `engine_recommended`: advisory state used outside a completed Workshop.
- `legacy_migration`: deterministic projection for an older project.

A Workshop production Prompt uses `confirmation_status: confirmed` and either
`author_selected` or `recommended_then_confirmed`. An unconfirmed engine
recommendation stays in design review.

## Long-form recommendation dimensions

Explain recommendations using story capacity rather than genre alone:

- renewal capacity of the central conflict;
- number of durable stages in the protagonist's arc;
- reveal ladder depth;
- subplot independence and interaction;
- world or social-system expansion;
- availability of distinct volume-level payoffs.

Classification contributes a prior, not a verdict. Fantasy, supernatural,
mafia, royal intrigue, multigenerational family drama, layered mystery, and
thriller frequently provide long-form capacity. Domestic betrayal, marriage
crisis, revenge, second chance, romance, and women's fiction remain flexible:
they support a long form when the design has several genuinely different
pressure systems and irreversible arc stages.

## Required Prompt block

Emit exactly one block after `[NOVEL_CLASSIFICATION_JSON]`:

```text
[NARRATIVE_FORMAT_JSON]
{
  "schema_version": "narrative-format.v1",
  "mode": "multi_volume",
  "total_chapters": 80,
  "target_words": 72000,
  "volumes": [
    {
      "volume_id": "volume_01",
      "volume_number": 1,
      "chapter_start": 1,
      "chapter_end": 20,
      "target_words": 18000,
      "structural_role": "rupture_and_awakening"
    }
  ],
  "series": {
    "series_id": "",
    "title": "",
    "book_number": null,
    "planned_books": null
  },
  "selection_source": "author_selected",
  "confirmation_status": "confirmed",
  "long_form_suitability": {
    "score": 68,
    "recommendation": "flexible",
    "requested_fit": "supported_with_expansion",
    "dimensions": {
      "conflict_renewal": 70,
      "character_arc_capacity": 75,
      "reveal_ladder": 62,
      "subplot_capacity": 68,
      "world_expansion": 55,
      "volume_payoff_capacity": 70
    },
    "strengths": ["character_arc_capacity", "conflict_renewal"],
    "risks": ["world_expansion"],
    "confidence": 0.82
  }
}
[/NARRATIVE_FORMAT_JSON]
```

The abbreviated volume array above demonstrates the fields. The actual block
must contain every contiguous volume span, cover chapters 1 through
`total_chapters` exactly once, distribute `target_words` across the spans, and
include the derived fields produced by `NarrativeFormat.to_dict`, including
`format_id`, `is_long_form`, `chapter_target_words`, `volume_count`, and
`audit_policy`. Generate the canonical object with
`core.narrative_format.infer_narrative_format`, then change only the confirmed
selection source when appropriate.

## Long-form volume contract

The Architect preserves the confirmed format and enriches each span with:

- a distinct title and central conflict;
- a concrete reader promise;
- an irreversible protagonist shift;
- a climax in the final 35 percent of the volume;
- an observable payoff;
- a carryover consequence for every nonfinal volume.

The engine derives stable milestone ids (`volume_01_promise`,
`volume_01_midpoint`, `volume_01_climax`, `volume_01_payoff`, and
`volume_01_carryover`). Chapter contracts record due ids in
`world_event_ids`. Five-chapter and volume-end audits use these ids to detect
drift, filler, repeated state changes, and missing payoffs.
