# Novel Classification Contract

Use Novel OS catalog version `novel-types.2026-10`. Classification is a story
design contract, not a free-form list of promotional words. Existing September
contracts remain readable with their original identity; new ids require October.

## Axes

Select exactly one primary genre:

- `romance` — Romance / 浪漫
- `womens_fiction` — Women's Fiction / 女性小说
- `family_drama` — Family Drama / 家庭
- `mystery` — Mystery / 悬疑
- `thriller` — Thriller / 惊悚
- `fantasy` — Fantasy / 奇幻
- `xuanhuan` — Xuanhuan / 玄幻
- `horror` — Horror / 恐怖
- `supernatural` — Supernatural / 超自然
- `contemporary_realism` — Contemporary Realism / 情感现实主义
- `young_adult` — Young Adult / 青春

Select zero to two secondary genres from the same list. Select up to three
story types:

- `sweet_romance`, `ceo_romance`, `dark_romance`
- `revenge`, `second_chance`
- `mafia`, `werewolf`, `royal_intrigue`
- `domestic_betrayal`, `marriage_crisis`
- `ethical_dilemma`, `female_growth`, `male_growth`
- `celebrity`, `abuse_survival`, `queen_empress`
- `love_at_first_sight`, `office_romance`, `workplace_comedy`, `same_sex_romance`
- `single_parent`, `billionaire`, `pregnancy`, `apocalypse`

Select up to three tones:

- `angst`, `sweet`, `dark`, `suspenseful`, `passionate`, `emotional_realism`

Select up to two settings:

- `contemporary_urban`, `social_life`, `family_domestic`
- `royal_court`, `western`, `post_apocalyptic`

Use audience channel `female`, `male`, or `general`; age band `teen`, `adult`,
`midlife`, or `unknown`. Use length form `short` or `long` and one nonoverlapping
chapter band: `chapters_0_50`, `chapters_51_100`, `chapters_101_150`,
`chapters_151_200`, or `chapters_201_plus`.

`sweet_romance` and `dark_romance` are mutually exclusive. Every selected
story type must change the causal outline, protagonist decisions, climax, and
ending rather than appearing only in metadata.

## Required Prompt Block

Place exactly one block after the parseable top fields. Use canonical ids only:

````text
[NOVEL_CLASSIFICATION_JSON]
```json
{
  "schema_version": "novel-classification.v1",
  "catalog_version": "novel-types.2026-10",
  "primary_genre_id": "<one primary id>",
  "secondary_genre_ids": ["<zero to two ids>"],
  "story_type_ids": ["<zero to three ids>"],
  "tone_ids": ["<zero to three ids>"],
  "setting_ids": ["<zero to two ids>"],
  "audience": {"channel": "<channel>", "age_band": "<age band>"},
  "length": {"form": "<short or long>", "chapter_band": "<chapter band>"}
}
```
[/NOVEL_CLASSIFICATION_JSON]
````

Novel OS validates the block, derives its classification id, localized labels,
commercial tier, and flattened `filter_type_ids`, then publishes the canonical
result to Markdown, EPUB, delivery metadata, and H5 PublicationPackage V2.
