---
name: novel-cover-studio
description: Design novel covers from a complete skeleton using promotional poster art, four concepts, direct image selection, single-image refinement, local typography and verified delivery; preserve legacy cover workflows.
---

# Novel Cover Studio

## Current project workflow: skeleton_poster

For new **project Cover Studio** work, follow the approved
[product specification](../../docs/design/2026-10-16-cover-studio-product-spec.md).
The versioned `skeleton_poster` pipeline supersedes the legacy entry and review
gates below. Historical V11 and workshop records keep their original protocol.

1. Capture the complete skeleton and explicitly declared attachments with source
   hashes. Do not automatically scan manuscript chapters or require a complete
   legacy analysis. Send the full frozen source once in a compact joint request
   for the shared story core, important relationships and four art directions.
2. Use `poster_staging`: symbolic montage, promotional co-presence and static
   poses may convey relationships without reproducing a literal scene. Keep
   source identities, ages, core relationships, world and author hard limits.
   Explore relevant main/supporting characters in relationship stories without
   enforcing a headcount quota or forcing romance imagery onto other genres.
3. Choose commercial retouched photography, cinematic montage, semi-realism or
   illustration according to this book. No universal photographic-only rule.
   Check executable JSON, exact title and request budgets locally. At most one
   bounded format correction; no automatic aesthetic or semantic repair loop.
4. The explicit generate action approves the exact immutable plan and reserves
   a durable idempotent task. Generate each requested image once, sequentially,
   and save/display each completed result immediately. At most one optional
   background portfolio suggestion; it never blocks display, selection or edits.
5. Any complete image is directly selectable without a reason or passing review.
   Refresh/navigation only reconnects. Failed work can be explicitly retried in
   a new version; unknown remote results are not silently replayed. If artwork
   already exists, recover local composition instead of paying for another image.
6. Refine a selected original with the user's prompt and verified image bytes in
   one image edit, without replanning or a new analysis. Preserve originals,
   siblings and the current formal selection. Save a child version and require
   explicit selection to replace the formal cover.
7. Compose the exact complete title locally from text-free artwork. Preview and
   export use the same renderer, font hashes and ordered lines, including
   per-line font/size/color hierarchy, spacing and optional backing. Local
   typography makes no model call. Old native-title images honestly lack this
   capability unless a verified text-free artwork exists.
8. Record requested versus observed models honestly, real returned tokens and
   event-frozen reference pricing/FX. Unknown model/usage is not zero or inferred.
   Selected image, ZIP manifest and any existing EPUB must identify the same
   final artifact and SHA-256. Preserve historical approvals and hashes.

The current production adapters are `api/poster_studio.py`,
`api/poster_image_service.py`, `core/cover_poster_planner.py` and
`core/cover_poster_typography.py`. The project UI uses two main tabs: design and
candidates. Legacy history stays available separately. Do not route a new
poster request through `CoverWorkflow.generate()` or the V11 analysis gate.

Real visual quality requires representative real output, not only fixture tests.
Do not send private book sources or incur unapproved calls during development;
use bounded synthetic acceptance data where authorized. Never restart active
novel jobs to exercise this workflow.

## Legacy V11 reference

The remainder applies to the older scene/evidence pipeline and its historical
records, not the new project poster workflow.

Turn the complete novel skeleton into four distinct, truthful film-poster cover directions by default. The supported range remains 3–5 when explicitly configured or requested. Preserve the exact title, source-established character identities, central conflict, protagonist choices, and spoiler boundary. Read [references/cover-handoff.md](references/cover-handoff.md) for source and compatibility contracts and [references/commercial-direction.md](references/commercial-direction.md) for art direction.

Apply the four-candidate default to new batches only. An existing direction keeps its approved plan count, hashes, and image allowance; never append an image or rewrite approval to match a changed default. A different count requires a new direction and its own approval.

## Entry gate

1. Inspect the selected source before analysis. Supported sources include project input and a workshop's current, prepared, or archived skeleton. Show the actual source, revision, content digest, and freshness policy. The source is a complete skeleton; a `COVER_HANDOFF` is a compressed view, not a substitute for reading it.
2. Capture an immutable source snapshot and analyze every segment. Distinguish verified facts, visual interpretation, and unresolved assumptions. Only a complete analysis may enter the new director. Failed or partial analysis never silently falls back to a handoff-only direction.
3. With `cover-profiles.v11`, create the conflict contract and a portfolio of `CoverScenePlan` values containing validated campaigns. Each plan explicitly chooses `scene` or `campaign_composite`, its cover epoch, visual hook, truthful promise, and evidence references. Canon validation precedes compilation.
4. Review the source binding, story facts, assumptions, scene/layer relationships, and executable art direction. `cover-compiler.v18` produces immutable recipes for new explicit expression modes before approval. Historical plans without the new field retain their original compiler version and hashes. Approval binds the exact source, analysis, brief, direction, recipes, title layout, and image-call allowance. Changed content requires new approval; an approval for another digest does not carry over.

The brainstorm handoff is a source-bound constraint projection from the
confirmed Sections A-E, not a second
cover director. If the V2 handoff contains `cover_constraints`, preserve the
frozen object, use its confirmed facts and spoiler limits in the existing
director/quality review, and use selected nodes or characters only as candidate
hints. `must_show_if_supported`, free-text facts, and preferences never create
an unsupported event or a second cover plan. Exact typed and section references
are verified against the immutable snapshot before generation. Do not ask the
brainstorm stage to provide cover scenes, blocking, visual hooks, camera,
lighting, or image prompts. The Studio remains the only owner of those
decisions.

Complete source analysis once per immutable source digest and reuse the stored
analysis for direction, review, and regeneration requests. A changed skeleton,
source policy, or analysis configuration invalidates the digest and requires a
new analysis; switching between cover directions must not re-analyze the same
source. `cover_constraints` may narrow the director's candidate pool, but it
never replaces the full skeleton snapshot or the required source coverage.

## Concept review

A cover introduces the story and creates reading interest. It does not have to reproduce every plot detail or prove a complete causal chain in one still. Apply `cover-readability.v2` for new v11 work and keep the legacy policy on historical records.

Choose `campaign.expression_mode` explicitly:

- `literal_scene`: a source-supported event with truthful participants, actions and consequences. Check what the design actually asserts; compatible interpretations of a subtle pose are not factual errors.
- `editorial`: a promotional interpretation using character juxtaposition, a meaningful object, environment, symbolic contrast or emotional aftermath. It may have no event or physical interaction and need not show a protagonist. Bind the core meaning to verified source evidence; ordinary lighting, gaze and staging need not occur word-for-word in the skeleton. Explicit story moments and physical interactions still require source support.

Use premium live-action film campaign photography with credible people, light, wardrobe and environment. Choose the cast and visual approach this novel needs. Seek four distinct visual arguments without requiring a central encounter, a fixed headcount or an exact protagonist decision in any fixed number of slots. A portrait, object study or quiet relationship composition can be effective. Explain the image's actual story and emotional promise honestly rather than claiming a small gesture proves refusal or disclosure.

The spatial `scene` / `campaign_composite` mode remains separate from expression mode. Preserve physical coherence within a photograph and clearly designed relationships between composite layers. Editorial juxtaposition does not assert literal co-presence; a claimed meeting or consequential action does. Preserve source-established identities, relationships, world rules, author requirements and spoiler limits. Never infer identity from audience or market.

Independent direction review uses `cover-commercial-review.v1` with two explicit channels:

- **Blockers:** material story contradictions, violated author constraints, or missing/invalid source support. Name the concrete false claim, existing plan field and verified source evidence. These enter the bounded repair loop.
- **Advisories:** marketing appeal, compatible visual ambiguity, composition craft and thumbnail readability. Give actionable suggestions and retain honest assessments. These do not block approval, mark a model call failed or trigger automatic paid repair.

Every requested evidence claim still receives an independent supported/unsupported/uncertain verdict. Editorial plans have a substantive source-bound expression claim even when no literal event is selected. Bind receipts to the exact analysis, plans and shared context; reject missing, stale, malformed or uncertain evidence. Never reclassify a historical failed review from its wording or scores. Keep drafts and rejected responses in owner-local diagnostics.

For targeted revisions, return `cover-direction-patch.v1` replacements bound to the exact base hash. Keep IDs, count, order and unchanged plans. Change only the requested candidate and its dependent fields. Reuse other candidates' evidence reviews only from their actual approved directions when source, analysis, shared context and each retained plan match. Mixed historical/new portfolios require each sibling's own provenance. Otherwise independently recheck complete facts, without reopening unchanged aesthetic decisions. A material issue outside the target stops that repair with an explicit scope conflict instead of another futile model call.

Normal valid planning requires a draft and an independent review. The existing limit of three drafts and three reviews remains a ceiling for hard errors, not a target. Advice alone never consumes another round. Once schema and source structure are valid, collect execution-fit and semantic hard errors in the same bounded round. A stored critique is feedback, never fresh approval. No latency or success guarantee follows from mocked tests.

Read persisted phases, response counts and request scope to report progress. Token usage settles at call completion; it is not a live speed meter. Full source and verified evidence remain intact. Stable prefixes permit caching but do not prove cache reuse. Do not silently lower a user's configured model or reasoning effort.

Evaluate thumbnail hierarchy and visual appeal before approval, but do not require unique proof of an invisible motive. Tiny gestures, crowded faces and weak contrast deserve specific improvement advice; title, identity, author constraints and actual execution failures remain enforceable. Stage A stays blind; facts-aware Stage B may judge the combined title/image story promise while preserving A's actual uncertainty.

Compiler v18 preserves executable performance, lighting, color, layer boundaries and protected title geometry while allowing the chosen editorial expression. Do not invent an additional physical object from a symbolic enlargement. Oversized protected instructions require concise revision, never silent removal. Existing approved recipes retain their versions and hashes.

## Title and rendering

Prefer `composed`: generate a text-free artwork, then locally compose the exact approved title using pinned font bytes, line breaks, region, size range, color, spacing, and protected story zones. Review both the artwork and final cover. Do not add an author line, subtitle, tagline, logo, watermark, or explanatory lettering. The image prompt for composed mode must not ask the provider to render the title.

`native` is an explicit alternative whose prompt includes the title. An independent blind read requires an approved, validated title mask; it remains a partial read because the mask hides image content. Without a valid projection, report unavailable blind evidence rather than claiming independence.

Use portrait `2:3` JPEG or PNG, preserving provider-native resolution. The preferred request is `2048x3072`, `quality=high`; honor configured settings. Each candidate is one configured image-model call with `n=1`. New API profiles default to `gpt-image-2.5-sunburst` and also support `gpt-image-2.5-flare`; existing profiles retain their chosen model. The current Codex CLI image tool cannot select those exact 2.5 models: use an image API connection for them, and never silently substitute another model. Distinguish the requested model from a provider-reported model; absent confirmation remains unverified. Keep the artwork, final image, title layout and font digests, and immutable attempts.

## Execute

Inspect the repository CLI first:

```bash
./deploy.sh novel-cover --help
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover --help
# Historical launcher form retained for installations that expose subcommands:
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover generate --help
```

The new CLI flow is `cover inspect-source` → `analyze` → `plan` → `approve` → `generate`; inspect each subcommand's help and pass real IDs and exact hashes. Source-bound generation consumes a reserved initial batch, not an unlimited approval. `approve --extra-image-attempts` defaults to `0`; any nonzero allowance must be explicitly authorized. Approval of the initial candidates does not authorize corrective image spend.

When the existing repository launcher supports the selected project workflow, reuse its healthy backend. Never restart a service merely to run cover work. Do not run live provider smoke tests without authorized spend. Repository and mocked tests do not require a live provider.

## Review, repair and selection

Stage A observes the artwork and thumbnail without title, synopsis, expected hook, or design rationale. Persist its relationships, actions, stakes, anomalies, open questions, uncertainty and text-contamination assessment before Stage B. Stage B independently receives the frozen A result, final image and story facts, then checks story fidelity and craft. New explicit expression modes use the classified v2 policy: genuine story/author/source violations, wrong title or unusable output can block; weak micro-action or causal readability stays advice and an honest score. Low scores do not automatically become factual blockers. The title may complement the broad story promise, but cannot erase a depicted contradiction. It cannot rewrite A. Missing dimensions are unknown, not zero or a fabricated passing score. An unavailable, incomplete or contaminated assessment requires explicit human review.

Bind each review stage to its actual image and thumbnail hashes. In composed mode,
A sees the text-free artwork while B sees the titled final cover; the expected
addition of the title is not a contradiction of A. A native-title mask remains a
partial view with explicit limits. Unreadable incidental prop markings alone do
not prove explanatory text contamination, but unresolved uncertainty must remain
visible and must never be upgraded automatically to a verified blind read.

Use the persisted repair decision:

- **Design/semantic failure:** return to the director and approve a new direction before new image calls. Do not keep drawing against an unchanged prompt because the conflict was not understood.
- **Evaluation failure:** reevaluate the retained image; this calls the evaluator but never the image provider.
- **Typography failure:** the review action can locally recompose the approved layout from retained artwork, including recovery after a composition failure. When a different layout is needed, use `cover propose-typography` → inspect the exact local PNG → `cover approve-typography --expected-amendment-sha256 ... --confirm-layout` → `cover apply-typography --expected-revision ...`. The separate amendment preserves the original recipe; `overlay` changes only local type, while `contain_panel` can fit the complete uncropped artwork into a solid-background layout. Both use zero image calls, but applying either requires new actual Stage A/B review and cannot carry over previous human acceptance or replace a selected candidate. Inspect subcommand help and the repository document `docs/cover-typography-amendments.md` for binding, recovery, project/Workshop API and import limits. Do not retry an unfit layout indefinitely.
- **Rendering failure:** when supported, retry the same approved recipe within the explicitly approved cumulative allowance. Current provider fallback redraws the whole image; it is not local editing. Failures and manual retries consume the same allowance. Do not silently spend more calls.

Compare original and repaired attempts, full-size images, thumbnails, and quality evidence. Any completed candidate may be explicitly selected without a written acceptance reason, regardless of review score, warnings, blocked assessment or missing evaluation. Previously rejected completed images may be selected again. Keep quality reports and any historical acceptance notes unchanged; choosing an image does not declare that its model review passed. Pending/failed output without a completed final image is not selectable. Preserve existing source, approval, recipe and image integrity checks. Historical candidates remain readable with their original contracts and hashes; legacy implicit correction is not authorization for new image spend.

The Studio also supports **reference-image refinement** (`精修这张`) for an existing
source-bound candidate, including a liked image without a blocking review. Ask for
the desired changes and what to retain. The saved request (1–2000 characters) first
revises and independently reviews only that candidate's direction; it cannot invent
story facts or alter the other candidates. Bind the request and original image hashes
to the proposal. After explicit approval, send the actual reference image through
the image edit endpoint/attachment exactly once. Codex edits send the frozen
original bytes as one structured app-server image data URL with original detail
in a fresh ephemeral session, then use `num_last_images_to_include=1`. Do not use
CLI `--image`/`localImage` (which can resize and expose a path) or provide a local
path to the image tool for a second filesystem read. Never save the reference in
output discovery or insert another conversation image before the edit. This uses
the uploaded attachment without changing Codex or container sandbox permissions. Do not silently fall back to a new
text-to-image generation. Composed covers use the retained text-free artwork and
locally recompose the newly approved title layout; native covers use the original
final image. Editing can change surrounding details, so do not promise pixel-perfect
preservation. Save a new version, perform fresh A/B review, and leave selection,
parent images and siblings unchanged. Unsupported historical import/typography
bindings must use their existing verification workflow. Refresh and failure never
authorize another image call.

For a failed or interrupted refinement, use the Studio's **重试精修** action to
recover the original reference and saved request. It creates a new proposal for
explicit approval and can reuse a still-valid reviewed direction without another
planning call. It never resets the consumed allowance or automatically edits an
image. Duplicate submissions recover the same successor; after another failure,
retry that successor. If the source or proposal has changed, use **修改要求并重新规划**.
If edited artwork already exists, inspect and review/recompose that result instead
of spending another image call. Retained failed slots in newer batches must resolve
through verified parent lineage, not candidate position or an unrelated latest task.

For large single-candidate repair requests, normalize the persisted base before
patch hashing: remove its duplicate evidence ledger only after verifying equality
with the authoritative complete ledger. Keep full source text and all evidence; apply the validated target-only review
or complete-facts fallback described above. Restore the authoritative ledger when saving the
direction. Never truncate facts or raise the byte limit merely to repeat a failed
request.

Return candidate paths and the applicable project/workshop Studio link. Workshop work can happen before project creation and must not create fake project deliverables. Project selection must verify the selected cover, `cover-set.json`, manifest, and package ZIP; report candidate state separately from package state. A standalone preview is not a published Studio candidate.

Repository Skills are the source of truth. Host installation is separate; Docker does not load host Skills. If a host sync is explicitly requested, the conventional destination is `$HOME/.codex/skills/novel-cover-studio`; do not silently edit a host-installed Skill while changing this repository copy.
