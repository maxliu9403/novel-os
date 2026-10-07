# Novel classification and H5 publication contract

Novel OS owns the canonical classification. Human-readable `genre` and
`genres` remain for compatibility; filtering uses stable ids from
`classification`.

## Schema

Every new Prompt Intake produces `novel-classification.v1` against catalog
`novel-types.2026-10`. A prompt may lock the classification in one
`[NOVEL_CLASSIFICATION_JSON]` block. Legacy projects are deterministically
projected from genre, premise, audience, tone, and chapter target.

The engine also accepts existing `novel-types.2026-09` objects without changing
their version, labels, or identity hash. New October-only ids require the October
catalog. The checked-in H5 book fixtures retain their original September contract.

The classification separates:

- one `primary_genre_id`;
- up to two `secondary_genre_ids`;
- up to three `story_type_ids`;
- up to three `tone_ids`;
- up to two `setting_ids`;
- audience channel and age band;
- length form and a nonoverlapping chapter band.

The classification length fields support filtering only. Customer choice,
volume boundaries, series identity, suitability analysis, and quality gates
live in `narrative-format.v1`; see `docs/narrative-format.md`.

`filter_type_ids` is the ordered, deduplicated union used by a simple H5 type
filter. `classification_id` hashes the semantic fields so importers can detect
changes. `display_labels` is a localized snapshot; database keys remain the
canonical ids.

## Expanded work types

The creation form supports revenge, ethics, female/male growth, CEO / dominant
boss romance, mafia, werewolf, xuanhuan, horror, domestic betrayal, celebrity,
abuse survival, queen/empress, love at first sight, office romance, workplace
comedy, same-sex romance, single parent, billionaire, pregnancy, and apocalypse.
Chinese and English selections map to stable ids. CEO and 霸总 share
`ceo_romance`; female and male growth are separate story types. Xuanhuan and
horror are primary genres. The remaining new themes are story types with
concrete planning requirements.

All selected genres remain in the creative brief. Canonical publication tags
retain their three-story-type limit; planning requirements include every
selected recognized theme, including those beyond that limit.

## Publication locations

The same canonical object is emitted to:

- `outputs/state/story_state.json` at `metadata.classification` for new runs;
- `outputs/input/brief.json` at `classification`;
- `outputs/publication/novel-classification.json`;
- `outputs/deliverables/meta/novel-classification.json`;
- `outputs/deliverables/package-manifest.json` as a classified file role;
- H5 `meta/publication_package.json` at `classification`;
- Markdown YAML front matter;
- EPUB `dc:subject` values and `novel-os:classification` JSON metadata.

The portable ZIP layout is:

```text
book-package.zip
├── book.epub
├── book.md
├── meta/
│   ├── novel-classification.json
│   ├── novel-serialization.json
│   └── h5-import.json
├── covers/
│   ├── pending/
│   └── selected-cover.png
└── package-manifest.json
```

The filename is a conventional projection, not the semantic contract. Importers
should first read `package-manifest.json.classification.path`; if that shortcut
is absent, locate the `files[]` entry whose `role` is
`novel_classification`. Verify that entry's `size` and `sha256` before parsing
the referenced JSON. A cover regeneration or cover selection rebuild preserves
the current classification artifact in the ZIP.

Each book's `meta/novel-classification.json` has this complete shape:

```json
{
  "schema_version": "novel-classification.v1",
  "catalog_version": "novel-types.2026-10",
  "classification_id": "classification:<sha256>",
  "primary_genre_id": "womens_fiction",
  "secondary_genre_ids": ["family_drama"],
  "story_type_ids": ["revenge", "domestic_betrayal"],
  "tone_ids": ["angst", "emotional_realism"],
  "setting_ids": ["family_domestic"],
  "audience": {
    "channel": "female",
    "age_band": "midlife"
  },
  "length": {
    "form": "short",
    "chapter_band": "chapters_0_50"
  },
  "commercial_tier": "core",
  "source": "author_confirmed",
  "confidence": 1.0,
  "filter_type_ids": [
    "womens_fiction",
    "family_drama",
    "revenge",
    "domestic_betrayal",
    "angst",
    "emotional_realism",
    "family_domestic"
  ],
  "display_labels": {
    "zh-CN": {
      "primary_genre": "女性小说",
      "secondary_genres": ["家庭"],
      "story_types": ["复仇", "家庭背叛"],
      "tones": ["虐心", "情感现实主义"],
      "settings": ["家庭生活"]
    },
    "en-US": {
      "primary_genre": "Women's Fiction",
      "secondary_genres": ["Family Drama"],
      "story_types": ["Revenge", "Domestic Betrayal"],
      "tones": ["Angst", "Emotional Realism"],
      "settings": ["Family / Domestic"]
    }
  }
}
```

The H5 immutable package contract is PublicationPackage V3 (`version: 3`). V3
adds the separate `serialization` object and per-chapter volume binding; the
classification object remains unchanged. That is the legacy MD evidence
projection. The current EPUB-first importer uses manifest V4 and
`meta/h5-import.json`; EPUB supplies the body, and every MD body is ignored.
See [the current H5 handoff](h5-import-handoff.md).

## H5 importer

At ZIP import time, resolve the classification artifact through the manifest:

```ts
const manifest = JSON.parse(readZipText("package-manifest.json"));
const matches = manifest.files.filter(
  (file: { role: string }) => file.role === "novel_classification",
);
if (matches.length > 1) throw new Error("duplicate_classification_role");
const declared = matches[0];
if (manifest.classification?.path && declared &&
    manifest.classification.path !== declared.path) {
  throw new Error("classification_path_conflict");
}
const classificationPath = manifest.classification?.path || declared?.path;
const classification = classificationPath
  ? JSON.parse(readZipText(classificationPath))
  : null;
```

After validating `schema_version`, `catalog_version`, `classification_id`, file
size, and SHA-256, map the fields as follows:

```ts
book.primaryTypeId = classification.primary_genre_id;
book.typeIds = classification.filter_type_ids;
book.storyTypeIds = classification.story_type_ids;
book.toneIds = classification.tone_ids;
book.settingIds = classification.setting_ids;
book.readerChannel = classification.audience.channel;
book.readerAgeBand = classification.audience.age_band;
book.lengthForm = classification.length.form;
book.chapterBand = classification.length.chapter_band;
book.classificationId = classification.classification_id;
```

Importing only manuscript content or replacing a cover may proceed when this
object is absent. Missing classification marks a new book for manual input;
on updates it preserves existing classification. Full automatic imports with
an invalid schema, unknown ids, conflicting metadata, missing referenced file,
or failed hash verification stop before database mutation and report the
specific error. A separately selected body-only or cover-only operation may
validate just its chosen component. H5 should not derive new ids from localized
display labels. The assignments above run only after classification is present
and valid.

Use `typeIds` for the existing combined “小说类型” filter. A later faceted UI
can query each axis independently without reimporting the book text. Query
canonical ids and render labels from `display_labels[locale]` or the catalog
endpoint `GET /api/novel-classification/catalog`.

For an older PublicationPackage V1, retain its current `tags` fallback during
migration. V2 already carries canonical classification. Once recompiled by the
current Novel OS, the package carries V3 classification plus serialization.

## Catalog governance

The engine accepts catalog ids only. Unknown model-generated categories are
rejected at Prompt Intake or project update. Entries include `priority_tier`
(`core`, `growth`, `experimental`, or compatibility-only) and story-type design
requirements. Operational performance data may change tiers in a later catalog
version while previously published classification ids remain stable.

## Complete dictionary and identity hash

The complete machine-readable catalog is
[novel-classification-catalog.json](examples/h5-import/novel-classification-catalog.json).
The generated [ID / axis / Chinese / English table](examples/h5-import/classification-dictionary.md)
contains every type, channel, age, length and chapter-band enum.

`classification_id` is `classification:` followed by SHA-256 of the UTF-8 JSON
encoding of exactly these fields:

```text
catalog_version
primary_genre_id
secondary_genre_ids
story_type_ids
tone_ids
setting_ids
audience: {channel, age_band}
length: {form, chapter_band}
```

Sort object keys lexicographically, retain array order and Unicode characters,
use compact separators with no whitespace, and omit the trailing newline.
Exclude `schema_version`, `classification_id`, `source`, `confidence`,
`commercial_tier`, `filter_type_ids` and `display_labels` from this identity
input. Derived filter ids are the ordered deduplicated union of primary,
secondary, story types, tones, settings; labels and tier come from the catalog.
Different books can share this classification id. By contrast,
`classification_sha256` in the H5 import sidecar hashes the complete JSON
file's original bytes, including source/confidence and formatting.
