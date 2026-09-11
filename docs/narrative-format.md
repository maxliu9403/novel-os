# Narrative format, volumes, series, and long-form quality

`novel-classification.v1` answers what kind of story a book is.
`narrative-format.v1` answers how the customer chose to package it.

## Customer decision flow

The brainstorming Skill treats length as a customer decision:

1. short novel;
2. standalone long novel;
3. one novel divided into multiple volumes;
4. one separately publishable installment in a multi-book series;
5. AI recommendation followed by customer confirmation.

An explicit request such as “80 chapters divided into four volumes” is stored
as `author_selected / confirmed`. If length is absent, Prompt Intake records an
`engine_recommended / pending_confirmation` proposal. A completed Workshop
Prompt always contains a confirmed `[NARRATIVE_FORMAT_JSON]` block.

The long-form suitability score is advisory. It evaluates conflict renewal,
character-arc capacity, reveal depth, subplot capacity, world expansion, and
volume-payoff capacity. It explains expansion risks without replacing the
customer's choice.

## Volume versus series

- A single 80-chapter story split into four reading units is `multi_volume`.
- Four independently publishable books under one umbrella are a series.
- A `series_installment` records `series_id`, series title, book number, and
  planned book count. The installment can still contain several volumes.

## Three-level planning contract

Long-form production uses:

```text
whole-book ending and story contracts
  -> volume contracts
    -> chapter contracts
```

Every volume contract contains a distinct central conflict, reader promise,
protagonist shift, climax, payoff, and nonfinal carryover consequence. The
engine assigns stable milestone ids:

```text
volume_01_promise
volume_01_midpoint
volume_01_climax
volume_01_payoff
volume_01_carryover
```

The relevant chapter contract records each due id in `world_event_ids`. This
creates explicit evidence rather than asking a later checker to infer whether a
volume promise was delivered.

Every `ChapterState` and H5 chapter row carries:

```json
{
  "volume_id": "volume_02",
  "volume_number": 2,
  "chapter_in_volume": 3,
  "volume_role": "escalation",
  "series_id": "",
  "series_book_number": null
}
```

## Quality gates

Before drafting, the plan gate checks:

- complete, nonoverlapping volume ranges;
- complete chapter-to-volume binding;
- distinct volume conflicts, promises, and payoffs;
- a climax in the final 35 percent of every volume;
- a carryover consequence for every nonfinal volume.

During drafting, long novels are audited every five chapters and at every
volume boundary. The audit blocks later planning when it finds missing chapter
contracts, a missing volume milestone, duplicated active choices, duplicated
irreversible changes, duplicated local payoffs, or an incomplete volume Final.
The last volume boundary is also the whole-book long-form audit.

Reports are written to:

```text
outputs/quality/long-form/plan-report.json
outputs/quality/long-form/chapter_005_audit.json
outputs/quality/long-form/chapter_020_audit.json
...
```

## Publication and H5

The canonical wrapper is `novel-serialization.v1` and is emitted to:

- `outputs/state/story_state.json` (`metadata.narrative_format` and
  `story_bible.volume_contracts`);
- `outputs/input/brief.json`;
- `outputs/input/foundation.json`;
- `outputs/publication/novel-serialization.json`;
- `outputs/deliverables/meta/novel-serialization.json`;
- Markdown front matter;
- EPUB metadata at `novel-os:serialization`;
- H5 PublicationPackage V3 at `serialization`.

H5 keeps EPUB as its only body source. Read the independent serialization JSON
and `meta/h5-import.json` for chapter/volume binding. The complete contract,
EPUB locators, import identity and entitlement rules are in
[the H5 handoff](h5-import-handoff.md).

The following volume-projection example assumes the serialization file exists
and has passed validation. If absent, skip this volume projection and preserve
existing volume rows and entitlements; the EPUB body import is independent.

```ts
const serialization = readSerializationJson();
const importContract = readH5ImportJson();
if (importContract.status !== "ready") {
  // Route to the legacy/body-only handler; preserve existing chapter/volume rows.
  return;
}

book.narrativeMode = serialization.format.mode;
book.volumeCount = serialization.format.volume_count;
book.seriesId = serialization.format.series.series_id || null;

book.volumes = serialization.volumes.map((volume) => ({
  id: volume.volume_id,
  number: volume.volume_number,
  title: volume.title,
  chapterStart: volume.chapter_start,
  chapterEnd: volume.chapter_end,
}));

const incomingChapters = importContract.chapters.map((chapter) => ({
  ...chapter,
  volumeId: chapter.volume_id,
  volumeNumber: chapter.volume_number,
  chapterInVolume: chapter.chapter_in_volume,
  volumeRole: chapter.volume_role,
  seriesId: chapter.series_id || null,
  seriesBookNumber: chapter.series_book_number,
  sourceChapterId: chapter.chapter_id,
  epubItemId: chapter.epub.item_id,
  epubHref: chapter.epub.href,
}));
// Upsert by sourceChapterId into existing chapter rows; do not delete/reinsert.
upsertChaptersPreservingLocalIdsAndEntitlements(book.id, incomingChapters);
```

Older packages remain importable through the existing EPUB branch. Recompile
an older Novel OS project to produce manifest V4 with explicit EPUB mapping.
Treat missing old volume bindings as unassigned; do not infer them from spine
positions. `serialization_id` is a structure hash, not book identity. Use
`h5-import.json.book_id` and keep a nonunique index on structure hashes.
