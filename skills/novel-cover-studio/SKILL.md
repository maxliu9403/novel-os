---
name: novel-cover-studio
description: Use when an approved novel design or Prompt needs story-specific commercial cover concepts, 2K image candidates, cover selection, candidate retry, or delivery-package verification.
---

# Novel Cover Studio

Turn the approved story design into 3-5 distinct commercial cover candidates. Preserve the exact title, target audience, core conflict, protagonist agency, decisive story node, and fictional-world policy; do not infer canon from arbitrary manuscript fragments. Cover exports use JPEG or PNG, with JPEG as the default. For English or United States / North American markets, apply the premium Western profile in [references/commercial-direction.md](references/commercial-direction.md): preserve only explicitly established identity traits, use a magazine/key-art finish, stage a visible plot moment, and reserve Western editorial typography.

## Entry gate

1. Locate the approved Prompt and normalize its single `COVER_HANDOFF_BEGIN` / `COVER_HANDOFF_END` JSON block to `CoverBriefV2`. Read [references/cover-handoff.md](references/cover-handoff.md) for the contract.
2. Confirm Sections A-E are approved and every required protagonist has an age or age band, occupation/lived status, daily wardrobe or lived environment, agency signal, and evidence refs. Pending critical assumptions stop before image generation.
3. Create 3-5 structured `CoverScenePlan` values through the Art Director, run the Canon Validator, and persist the resulting direction. Read [references/commercial-direction.md](references/commercial-direction.md) before reviewing it.
4. Show Story facts and Art direction in Cover Studio. Continue only after exact direction-hash approval; stale direction approval never carries across a changed brief.

## Concept review

Create 3-5 concepts as one deliberately varied portfolio, not repeated variants of a single tableau. For the default four-cover set, assign four different expression systems: an intimate character reckoning, a deep-focus relationship tableau, an evidence-led editorial revelation, and a kinetic irreversible threshold. Each system must differ in composition family, depicted narrative beat, photographic art treatment, emotion register, and title typography; changing only crop, pose, palette, prop, lens, or camera angle is repetition. Use causal foreground/background emotional geography for the relationship tableau, while the other systems follow their own intimate, object-led, or motion-led grammar. Every concept still states the protagonist action, power contrast, one secondary signal, palette, and exact title treatment. Quote the exact title once; add no subtitle, author copy, logo, watermark, real place name, or unsupported spoiler. For the English / US profile, make every focal scene a consequential frozen action with visible reaction and stakes, and keep faces and the decisive prop clear of the title-safe zone.

The structured direction stores and displays the treatment ids for each plan: `portfolio_slot`, `composition_family`, `scene_family`, `location_family`, `art_style`, `emotion_register`, and `typography_style`. The v3 validator requires the assigned ids, unique scene families, unique frozen beats, the full portfolio hook order, and use of every approved primary location before any location repeats.

Show the concepts before billable image generation. Identify every visual assumption. Approval binds both `brief_sha256` and `direction_sha256`; a boolean confirmation or global skip flag is not approval.

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

Return candidate paths and `/projects/PROJECT/covers`. Use the Studio for full-resolution and mobile-thumbnail inspection, quality blockers, and explicit selection. An unavailable visual evaluator means `human_review_required`; it never invents scores. Retry a failed candidate directly, or retry a ready candidate only with repair codes present in its quality report. Every retry appends an immutable generation attempt.

After selection, verify non-empty `covers/selected-cover.*`, `covers/cover-set.json`, `package-manifest.json`, and `book-package.zip`. Report candidate status separately from package status.

Repository Skills are the source of truth. To sync this Skill on a Codex host, install it under `$HOME/.codex/skills/novel-cover-studio`; Docker does not load host Skills.
