---
name: novel-cover-studio
description: Use when an approved novel design or Prompt needs story-specific commercial cover concepts, 2K image candidates, cover selection, candidate retry, or delivery-package verification.
---

# Novel Cover Studio

Turn the approved story design into 3-5 distinct commercial cover candidates. Preserve the exact title, target audience, core conflict, protagonist agency, decisive story node, and fictional-world policy; do not infer canon from arbitrary manuscript fragments. Cover exports use JPEG or PNG, with JPEG as the default. For English or United States / North American markets, apply the premium Western profile in [references/commercial-direction.md](references/commercial-direction.md): preserve only explicitly established identity traits, use a magazine/key-art finish, stage a visible plot moment, and reserve Western editorial typography.

## Entry gate

1. Locate the approved Prompt and read its single `COVER_HANDOFF_BEGIN` / `COVER_HANDOFF_END` JSON block. Read [references/cover-handoff.md](references/cover-handoff.md) for the contract.
2. Confirm the brainstorm Sections A-E are approved. If title, audience, conflict, protagonist identity, decisive node, or fictional world signals are unresolved, return to `novel-brainstorm-workshop` for that decision.
3. Read [references/commercial-direction.md](references/commercial-direction.md) before drafting concepts.

## Concept review

Create 3-5 concepts with distinct focal scenes and visual strategies. Each concept states the protagonist action, power contrast, one secondary signal, composition, palette, and exact title treatment. Quote the exact title once; add no subtitle, author copy, logo, watermark, real place name, or unsupported spoiler. For the English / US profile, make the focal scene a consequential frozen action with visible reaction and stakes, and keep faces and the decisive prop clear of the title-safe zone.

Show the concepts before billable image generation. Identify any inferred visual detail. Continue only after the user approves the set or explicitly asks for immediate generation from already approved concepts.

## Execute

Detect the repository launcher first:

```bash
./deploy.sh novel-cover --help
```

When available, use Docker without restarting services:

```bash
NOVEL_OS_PROJECT_NAME='PROJECT' NOVEL_OS_COVER_COUNT='4' \
  ./deploy.sh novel-cover './prompt/TITLE.md'
```

Use actual values, never placeholders. The launcher reuses a healthy backend and writes portrait `2:3` candidates at the provider's native resolution (the preferred request is `2048x3072`) plus the delivery ZIP. Native fallback:

```bash
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover generate \
  --project './projects/PROJECT' --prompt './prompt/TITLE.md' --count 4
```

Do not call `up`, `restart`, or `down` for an already healthy service. Do not run a live provider smoke test unless the user approved the image spend.

## Review and delivery

Return candidate paths and `/projects/PROJECT/covers`. Use the Studio for full-resolution inspection and explicit selection. CLI recovery forms are documented by `./deploy.sh novel-cover --help`; retry only the failed candidate.

After selection, verify non-empty `covers/selected-cover.*`, `covers/cover-set.json`, `package-manifest.json`, and `book-package.zip`. Report candidate status separately from package status.

Repository Skills are the source of truth. To sync this Skill on a Codex host, install it under `$HOME/.codex/skills/novel-cover-studio`; Docker does not load host Skills.
