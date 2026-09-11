---
name: novel-cover-studio
description: Use when an approved novel design or Prompt needs story-specific commercial cover concepts, 2K image candidates, cover selection, candidate retry, or delivery-package verification.
---

# Novel Cover Studio

Turn the approved story design into 3-5 distinct commercial cover candidates. Preserve the exact title, target audience, core conflict, protagonist agency, decisive story node, and fictional-world policy; do not infer canon from arbitrary manuscript fragments. Cover exports use JPEG or PNG, with JPEG as the default. For English or United States / North American markets, apply the premium Western profile in [references/commercial-direction.md](references/commercial-direction.md): preserve only explicitly established identity traits, use a magazine/key-art finish, stage a visible plot moment, and reserve Western editorial typography.

## Entry gate

1. Locate the approved Prompt and normalize its single `COVER_HANDOFF_BEGIN` / `COVER_HANDOFF_END` JSON block to `CoverBriefV2`. Read [references/cover-handoff.md](references/cover-handoff.md) for the contract.
2. Confirm Sections A-E are approved and every required protagonist has an age or age band, occupation/lived status, daily wardrobe or lived environment, agency signal, and evidence refs. Pending critical assumptions stop before image generation.
3. Create one source-bound `CoreConflictVisualContract` and 3-5 structured `CoverScenePlan` values through the Art Director, run the Canon Validator, and persist the resulting direction. Read [references/commercial-direction.md](references/commercial-direction.md) before reviewing it.
4. Show Story facts and Art direction in Cover Studio. Continue only after exact direction-hash approval; stale direction approval never carries across a changed brief.

## Concept review

New directions use `cover-profiles.v8`: premium live-action film campaign photography is the rendering medium. Preserve mature ages, natural skin and hair, clear causal gestures, contemporary or canon-appropriate wardrobe, physically coherent light and a real story location. Do not choose oil painting, gouache, illustration, paper relief, painted figures, sculpture or CGI to create portfolio diversity. Vary the story instant, camera, blocking, lighting, environment and editorial lettering instead. Story objects may involve painting or pottery; people remain photographic. Prefer `quality=high` and `2048x3072` when configuring a new profile; honor existing configured settings without silently changing them; a higher pixel count does not correct an incompatible medium.

Create 3-5 concepts as one deliberately varied portfolio, not repeated variants of a single tableau. The conflict contract records the protagonist, pressure source, relationship/status stakes, visible cause, decisive consequence, approved pressure characters, evidence references, and spoiler boundary. Every plan must communicate that cause-and-consequence story at thumbnail size without prescribing one foreground/background formula. At least one plan shows the complete approved causal relationship directly, and at least one shows a concrete active protagonist decision. Choose the other plans' cast and direct/indirect expression from the specific story moment, not a group quota. Every plan still needs visible causal evidence and a readable protagonist response; indirect does not mean a character-free still life or generic sad portrait. Give every plan a distinct `conflict_delivery` as well as a different composition, scene, photographic treatment, emotion, and typography system. Keep camera, lighting, color script, spatial grammar and typography concise and executable; explanations belong in rationale fields. Design polished key art, not default documentary gloom. Keep the title in one continuous lockup in exact reading order (English: left-to-right, then top-to-bottom), never reversed clauses across character columns. Quote the exact title once; add no subtitle, author copy, logo, watermark, real place name, or unsupported spoiler.

The structured direction stores and displays the treatment ids for each plan: `portfolio_slot`, `composition_family`, `scene_family`, `location_family`, `art_style`, `emotion_register`, and `typography_style`. V8 invents story-specific photographic directions, not a fixed slot order. The assigned blueprint ids and fixed hook order apply only to legacy v3 directions.

Existing illustrated directions stay available as history, but require a newly planned and approved photographic direction before generation. Do not merely append “photorealistic” to an oil-painting prompt or silently rewrite an approved direction. A standalone preview is not a published Cover Studio candidate; report its actual resolution and keep publication selection separate.

For natural human performance, use the existing scene fields: one captured beat, each actor's intention and eyeline, differentiated reactions, believable weight and hand/prop contact. A character's general agency signal is context, not an extra simultaneous gesture. Do not repeat a raised palm as the universal symbol of refusal or display all evidence objects toward camera. Keep cinematic design while allowing natural focus falloff and subtle facial asymmetry; avoid waxy smoothing, etched pores and hyper-sharp cutout edges. The compiler protects this guidance during prompt fitting and face/action repair.

Show the conflict contract and concepts before billable image generation. Identify every visual assumption. Approval binds both `brief_sha256` and `direction_sha256`; a boolean confirmation or global skip flag is not approval.

## Execute

Detect the repository launcher first:

```bash
./deploy.sh novel-cover --help
```

When available, use Docker without restarting services:

```bash
NOVEL_OS_PROJECT_NAME='PROJECT' NOVEL_OS_COVER_COUNT='4' \
NOVEL_OS_COVER_DIRECTION_ID='DIRECTION_ID' \
NOVEL_OS_COVER_APPROVED_DIRECTION_SHA256='DIRECTION_SHA256' \
  ./deploy.sh novel-cover './prompt/TITLE.md'
```

Use actual values, never placeholders. The launcher reuses a healthy backend and writes portrait `2:3` candidates at the provider's native resolution (the preferred request is `2048x3072`) plus the delivery ZIP. Each candidate is a separate `gpt-image-2`, `n=1` call. Native fallback:

```bash
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover generate \
  --project './projects/PROJECT' --prompt './prompt/TITLE.md' --count 4 \
  --direction-id 'DIRECTION_ID' \
  --approved-direction-sha256 'DIRECTION_SHA256'
```

Do not call `up`, `restart`, or `down` for an already healthy service. Do not run a live provider smoke test unless the user approved the image spend.

## Review and delivery

Return candidate paths and `/projects/PROJECT/covers`. Use the Studio for full-resolution and mobile-thumbnail inspection, quality blockers, and explicit selection. The configured multimodal review route compares the rendered image—not the prompt intent—against cast, core conflict, causal relationship, and protagonist agency. A missing cause, missing pressure relationship, or passive protagonist triggers one bounded corrective generation attempt and records both attempts. Promote the correction to current candidate only when story and craft scores are known, at least 80, no known score regresses and no blockers/repair codes remain. Otherwise keep the original active with `repair_not_promoted`; both images remain in history by immutable media ID/SHA. Title order errors block even if all words are present. Operator selection remains separate. An unavailable visual evaluator means `human_review_required`; it never invents scores. Retry a failed candidate directly, or retry a ready candidate only with repair codes present in its quality report. Every retry appends an immutable generation attempt.

After selection, verify non-empty `covers/selected-cover.*`, `covers/cover-set.json`, `package-manifest.json`, and `book-package.zip`. Report candidate status separately from package status.

Repository Skills are the source of truth. To sync this Skill on a Codex host, install it under `$HOME/.codex/skills/novel-cover-studio`; Docker does not load host Skills.
