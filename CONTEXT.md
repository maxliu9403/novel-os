# Novel OS domain language

## Novel classification

- **Novel classification**: the engine-owned, versioned metadata contract for a
  story. It is stored as canonical ids and projected into publication outputs.
- **Primary genre**: the single durable shelf for a novel.
- **Secondary genre**: zero to two additional shelves that materially describe
  the story.
- **Story type**: a commercial narrative engine such as revenge, second chance,
  domestic betrayal, or werewolf. It must change the causal story design.
- **Tone**: the reader's dominant emotional experience, such as angst or sweet.
- **Setting**: the story world or social environment, such as contemporary urban
  or post-apocalyptic.
- **Filter type ids**: the ordered union of genre, story type, tone, and setting
  ids used by a combined H5 type filter.
- **Classification catalog**: the versioned allowlist of canonical ids, labels,
  commercial tiers, conflicts, and story-design requirements.
- **Legacy genre**: the human-readable `genre` / `genres` text retained for old
  projects and display. It is not the canonical query key.
- **PublicationPackage V2**: the immutable H5 delivery object that includes a
  canonical `classification` field alongside titles, copy, tags, and chapters.

The Novel OS repository owns classification generation and publication. The H5
application consumes the published contract through a separately scheduled
integration.

## Narrative methods

- **Method policy**: a private, versioned selection of writing methods and review scope; P1 supports off and advisory only.
- **Run method lock**: a frozen method/configuration snapshot bound to a logical project and run, not a mutable global setting.
- **Method critique**: a read-only opinion about an exact artifact revision. Verified quotations do not prove the opinion or grant promotion authority.
- **Explicit method retry**: a user-requested new critique of the same input, linked to its failed predecessor; repeating that same request is idempotent.

Method sidecars are private diagnostics, not canon and not H5 publication metadata.

- **Method experiment**: an isolated, budget-confirmed comparison of frozen Scribe prompt variants; it has no promotion authority.
- **Blind pair**: both planned multi-chapter samples and their shared design, exported without arm or model labels; failures remain in the denominator.
- **H5 acceptance receipt**: externally supplied importer/database observations bound to exact package bytes; local consistency checks do not prove external execution.
