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

## Creation workspace lifecycle

- **Cover progress**: a concise projection of active tasks, usable directions, candidate quality and explicit selection. It is separate from manuscript completion and does not grant cover approval.
- **Single-candidate repair**: a source-bound revision of one candidate. Explicit approval covers exactly one new image call, or zero for local recomposition/evaluation. The result is saved in a new batch with retained-candidate lineage; the parent batch and official selection remain unchanged.
- **Reference-image refinement**: a user-requested edit of an existing candidate, with the requested changes and original image hash bound to a reviewed single-candidate direction. Approval permits one image edit using the actual reference bytes. Composed covers edit the retained text-free artwork and recompose the approved title. Fresh image review and explicit selection are required; parent and sibling images remain intact.
- **Refinement retry**: an explicit recovery from a failed or interrupted refinement. A still-valid reviewed proposal can be copied into a new awaiting-approval task without another planning call; the original reference and request stay bound. The user must approve one new image edit. Duplicate retry requests return the same successor, and another failure is retried from that successor. Old attempts, consumed allowances and costs remain recorded. A saved edited image is reviewed or recomposed instead of being generated again; an outdated proposal requires explicit replanning.
- **Restarted brainstorm round**: an explicitly confirmed fresh derivation from edited basics. Earlier discussion and skeletons remain archived, while a revision boundary excludes them from the new model context and candidate fallback. Usage history remains attributable to the same discussion.
- **Skeleton delivery checkpoint**: a complete saved story design, chapter plan, or assembled skeleton, bound to one discussion input and Skill version. Explicit recovery reuses saved stages and regenerates only unfinished work; it does not revive a terminated remote model request. The assembled result must pass the full existing validation before replacing the formal skeleton.
- **Observed model activity**: counts of public summary or text deltas from a native Codex metric batch. Receipt time is a batch observation, not an exact token timestamp. Connection metadata, prewarming, worker heartbeats and token billing are separate signals and do not prove ongoing model generation.

## Commercial cover expression

- **Literal scene**: a cover design asserting an actual story event; its participants, physical interactions and consequences remain source-supported. A subtle pose need not uniquely prove every motive.
- **Editorial expression**: a source-grounded promotional interpretation using characters, juxtaposition, meaningful objects, setting or aftermath. It need not depict a literal meeting, protagonist action or complete causal chain. It cannot invent core identity, relationships, world rules or major events, or violate author/spoiler constraints.
- **Commercial advisory**: an optional improvement in appeal, compatible visual ambiguity, composition or thumbnail readability. It remains visible with honest scores but does not block approval or trigger automatic paid repair.
- **Material cover blocker**: an affirmative contradiction of a substantive story fact, author constraint or source integrity; image review may also flag title/output defects. Each classified blocker names its concrete assertion and evidence. These findings guide design repair; they do not require a written justification or a passing score to select an already completed image.
- **Scoped direction review**: fresh review of a changed candidate while reusing only matching approved siblings’ evidence receipts. Missing provenance triggers complete factual review without reopening unaffected aesthetic choices. An out-of-scope hard error stops single-candidate repair instead of repeating it.

New Studio directions use cover-profiles.v11 and cover-compiler.v18 for explicit expression modes; old approvals, missing fields and compiler hashes remain unchanged. Missing/uncertain evidence is not an advisory.

- **Direct cover selection**: the user may choose any completed candidate, including one previously rejected, without submitting an acceptance reason or waiting for other candidates to pass. Selection and model quality status are independent; preserve the original report and historical notes. Existing image/source/recipe integrity checks still apply.
