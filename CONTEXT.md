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

- **Reviewed ending evidence**: an append-only, named reviewer decision that supplies a missing payoff or character-outcome record from already approved prose. Each claim binds the project instance, ending contract, current Final revision, promotion receipt and exact source quotations. It supplements the ending gate's projection without altering canon or historical proposals. Source changes invalidate it; an explicit superseding review preserves history. Earlier unresolved status does not negate later verified completion, while contemporaneous or later contradictory status remains a blocker.

- **Method experiment**: an isolated, budget-confirmed comparison of frozen Scribe prompt variants; it has no promotion authority.
- **Blind pair**: both planned multi-chapter samples and their shared design, exported without arm or model labels; failures remain in the denominator.
- **H5 acceptance receipt**: externally supplied importer/database observations bound to exact package bytes; local consistency checks do not prove external execution.

## Creation workspace lifecycle

- **Cover progress**: a concise projection of active tasks, usable directions, candidate quality and explicit selection. It is separate from manuscript completion and does not grant cover approval.
- **Explicit cover batch**: one deliberate generation request reusing a still-valid approved direction, identified by a durable owner-scoped request ID and a separate server task reservation. Another deliberate request can create another batch after completion or failure; the prior batch, spent attempts and costs remain recorded. Replaying the same request or a coalesced active-task alias returns the original task, even after termination or restart. Refresh never authorizes new images, and single-candidate repair approvals do not grant full-batch permission.
- **Single-candidate repair**: a source-bound revision of one candidate. Explicit approval covers exactly one new image call, or zero for local recomposition/evaluation. The result is saved in a new batch with retained-candidate lineage; the parent batch and official selection remain unchanged.
- **Reference-image refinement**: a user-requested edit of an existing candidate, with the requested changes and original image hash bound to a reviewed single-candidate direction. Approval permits one image edit using the actual reference bytes. Composed covers edit the retained text-free artwork and recompose the approved title. Fresh image review and explicit selection are required; parent and sibling images remain intact.
- **Refinement retry**: an explicit recovery from a failed or interrupted refinement. A still-valid reviewed proposal can be copied into a new awaiting-approval task without another planning call; the original reference and request stay bound. The user must approve one new image edit. Duplicate retry requests return the same successor, and another failure is retried from that successor. Old attempts, consumed allowances and costs remain recorded. A saved edited image is reviewed or recomposed instead of being generated again; an outdated proposal requires explicit replanning.
- **Restarted brainstorm round**: an explicitly confirmed fresh derivation from edited basics. Earlier discussion and skeletons remain archived, while a revision boundary excludes them from the new model context and candidate fallback. Usage history remains attributable to the same discussion.
- **Workshop turn intent**: `discuss` is the default operation, including route choices, answers and further exploration with an existing skeleton. It returns phased story proposals and retains the saved skeleton; model output cannot promote the turn into delivery. The author's separate Generate/Update Complete Skeleton action sends `deliver`, including any unsent instructions, without an extra confirmation or a minimum round count. Intent persists through pause/retry; a verified legacy delivery checkpoint can still be explicitly resumed.
- **Skeleton delivery checkpoint**: a complete saved story design, chapter plan, or assembled skeleton, bound to one discussion input and Skill version. Fresh explicit delivery starts with actual story design, choosing and freezing the narrative mode in that response rather than spending a separate automatic-handoff call. Explicit recovery reuses saved stages and regenerates only unfinished work; it does not revive a terminated remote model request. The assembled result must pass the full existing validation before replacing the formal skeleton.
- **Assembly part**: one complete, validated supplement to saved story and chapter plans. Six bounded parts provide publication settings, relationships/world rules, secrets/payoffs, reading/style guidance, cover handoff, and delivery review. The application assembles the final 17 sections locally without requesting another transcription of the existing chapters. Corrections append a part revision; earlier artifacts and their hashes remain intact. Legacy v1 checkpoints can acquire parts during explicit continuation.
- **Delivery time budget**: each model request retains the configured workshop timeout; a staged delivery has a fixed total allowance of three times that timeout from the original turn start, including waiting and storage. Format repairs share that total and the existing two-repair allowance. Activity does not extend either deadline; timeout stops without automatically replaying a request. Ordinary discussion and existing-skeleton edits retain their original turn budget.
- **Observed model activity**: counts of public summary or text deltas from a native Codex metric batch. Receipt time is a batch observation, not an exact token timestamp. Connection metadata, prewarming, worker heartbeats and token billing are separate signals and do not prove ongoing model generation.

## Commercial cover expression

- **Lossless campaign source transport**: large outgoing requests share complete readable source passages between frozen documents and quoted evidence, dictionary-encode repeated metadata, and reference exactly matching ledger views. A local decoder must reconstruct the prior request byte-for-byte at the JSON-value level before sending. Source facts, qualifiers, original evidence IDs, persisted analysis and patch hashes remain unchanged; final byte/token limits still apply. This is request representation, not summarization, relaxed story review or permission to retry a model.

- **Campaign source-index projection**: when the lossless representation remains large, an explicitly versioned model input can omit repeated extracted `statement` annotations while retaining complete frozen documents, every evidence ID and full source quotation, global analysis and all other metadata. Existing ledger summaries remain supplied separately. The complete canonical annotations stay saved locally; verifying the permitted projection requires that canonical baseline, rather than pretending the model received every original annotation. Creation, repair and review share this projection and the actual final request limits. It changes no source facts, approval, retry allowance or persisted patch hashes.

- **Reviewed source amendment**: an explicit, named, source-cited correction to an exhausted cover analysis. It creates one independently verified child while preserving the parent's extraction, evidence, failed reviews and spent automatic correction allowance. It changes existing narrative fields and appends known evidence references; it does not rewrite the source or declare a model review passed. All source units and the global review must verify the amended projection before downstream direction planning.

- **Analysis recovery guidance**: a read-only runtime projection distinguishing an exhausted parent needing correction, an available amendment, a pending amendment and failed amendment verification. Viewing or refreshing a record never starts verification. An exhausted parent under the same policy is rejected before job allocation or timestamp changes; the amended child is resumed only by explicit request. An explicit null guidance permits ordinary processing or a supported policy upgrade, while an absent field denotes an older backend.

- **Unspecified event location**: an explicit empty location string means the analysis has not established one source-supported place for the whole event; remote or compound actions can still be complete story facts. Participants do not prove co-presence. Literal cover scenes need an established event location; editorial portraits or objects may express the supported theme without inventing an encounter.

- **Literal scene**: a cover design asserting an actual story event; its participants, physical interactions and consequences remain source-supported. A subtle pose need not uniquely prove every motive.
- **Editorial expression**: a source-grounded promotional interpretation using characters, juxtaposition, meaningful objects, setting or aftermath. It need not depict a literal meeting, protagonist action or complete causal chain. It cannot invent core identity, relationships, world rules or major events, or violate author/spoiler constraints.
- **Commercial advisory**: an optional improvement in appeal, compatible visual ambiguity, composition or thumbnail readability. It remains visible with honest scores but does not block approval or trigger automatic paid repair.
- **Material cover blocker**: an affirmative contradiction of a substantive story fact, author constraint or source integrity; image review may also flag title/output defects. Each classified blocker names its concrete assertion and evidence. These findings guide design repair; they do not require a written justification or a passing score to select an already completed image.
- **Scoped direction review**: fresh review of a changed candidate while reusing only matching approved siblings’ evidence receipts. Missing provenance triggers complete factual review without reopening unaffected aesthetic choices. An out-of-scope hard error stops single-candidate repair instead of repeating it.

Legacy Studio directions use cover-profiles.v11 and cover-compiler.v18 for explicit expression modes; old approvals, missing fields and compiler hashes remain unchanged. Missing/uncertain evidence is not an advisory.

- **Skeleton poster workflow**: the current project studio captures the complete skeleton and declared attachments once, jointly plans the story brief and four directions, then generates images without the legacy analysis prerequisite. `poster_staging` allows promotional co-presence and symbolic composition while preserving core identities and relationships. Shape corrections are bounded; commercial advice is nonblocking. The workshop cover entry retains its older protocol.
- **Poster refinement and typography**: an explicit reference-image edit creates one new candidate version without a fresh analysis or direction review. Typography-only versions use retained artwork and the same server renderer for preview/export. Parent images, retained siblings and current selection remain intact. Failed work is explicitly retried; saved artwork is reused locally.
- **Frozen usage estimate**: each new poster task captures configured prices and exchange rate. Actual returned usage is attributed to planning, generation, refinement, retries or advice; unobserved use stays unknown. Older records lacking snapshots keep their labeled legacy estimate behavior.
- **Current delivery package**: image selection and ZIP/EPUB synchronization are distinct outcomes. Failed delivery never invalidates a completed selected image, but a ZIP bound to an earlier choice is not offered as current. Explicit local rebuilding can recover delivery without a model call.

- **Direct cover selection**: the user may choose any completed candidate, including one previously rejected, without submitting an acceptance reason or waiting for other candidates to pass. Selection and model quality status are independent; preserve the original report and historical notes. Existing image/source/recipe integrity checks still apply.
