---
name: novel-brainstorm-workshop
description: "Turn a rough novel idea into a confirmed, causally sound story design and an executable Novel OS prompt using a user-confirmed audience profile and local commercial-story mechanisms. Use this skill whenever a user mentions writing a novel, developing a story premise, researching readers, comparing markets, brainstorming characters, expanding a plot, building a story bible, tracking secrets or timelines, improving a novel prompt, starting a new book, developing an English-language novel, or strengthening commercial retention, the opening hook, the first three chapters, conflict variety, atmosphere, reader identification, or a paid-reading bridge. It is especially useful when the user has only a few scenes, a theme, a relationship conflict, or an ending idea and needs structured creative development before drafting."
---

# Novel Brainstorm Workshop

Turn an incomplete novel idea into a deliberate story engine, a reviewed design, and a prompt that can drive a complete Novel OS run. The workshop creates predictable decisions, not identical stories: preserve the user's emotional intent while making every important assumption visible and every major turn causally earned.

## Operating contract

- Use the user's requested output language and target-market register. Chinese and English are first-class paths; record market-specific idiom, social context, and localization decisions as approved design choices.
- Use fictional setting names by default. Story-facing locations, cities, districts, institutions, and landmarks use invented names or abstract regional labels; real geography appears only in the user-confirmed market profile when needed.
- Give every novel a short reader-facing story lead before chapter one. It previews the core conflict and an earned satisfaction path in the output language, but it does not replace chapter one's own hook or reveal the full ending.
- Treat the user's rough idea as the source of intent, not as a finished outline. Preserve explicit decisions and label low-impact additions as assumptions.
- Keep the interaction focused. Ask at most five high-impact questions, one question per message. Ask only when an answer could change the story identity, audience, causal engine, protagonist arc, or ending.
- Treat the target audience as a required user decision. When the conversation and confirmed project scope do not identify the primary reader segment, ask who the novel is for before creative branching; never fill this field as an assumption. Confirm at least the audience's age or life stage and primary genre expectation, reading motivation, or emotional need. Ask about gender tendency, platform, or purchasing context only when it materially changes the design.
- Treat narrative length as a required user decision. Ask whether the customer wants a short novel, a standalone long novel, one multi-volume novel, or a multi-book series. The customer may request an AI recommendation, but that recommendation becomes a production contract only after confirmation. An explicit chapter count already supplied by the customer counts as a selection; summarize it for confirmation instead of asking the same question again.
- For a new selection, lock the target market before creative branching: country, region or city culture, primary language, and single-market versus multi-market release. Carry this user-confirmed profile into the design without a default external-retrieval phase.
- Present two or three materially different approaches before fixing the structure. Include a recommendation and the tradeoff behind it.
- Present the design in reviewable sections. Wait for confirmation after each section before writing the final prompt.
- Use positive quality targets. A prohibition belongs only beside a concrete replacement behaviour.
- The default deliverable is a prompt file plus a start command. The user decides when to run the model.
- Every approved Prompt includes a strict JSON cover handoff. After Sections A-E are approved and the Prompt validates, route cover requests to `novel-cover-studio`; do not call the image model while story direction is changing.
- Keep existing canon separate from a new concept. A new prompt never silently changes an existing project.

## Route the request

Select the narrowest branch before doing creative work:

| Signal in the request | Branch | Deliverable |
|---|---|---|
| Rough premise, theme, relationship, ending, or a few scenes | `workshop` | Reviewed design, prompt file, intake result, start command |
| Existing outline that needs expansion | `expand` | Revised story engine, chapter plan, prompt update |
| Existing prompt that needs stronger structure | `prompt-revise` | Evidence-based prompt revision and validation |
| Existing manuscript or project | `canon-aware` | Canon extraction, continuity-aware design or continuation prompt |
| One chapter only | `chapter-shortcut` | Compact chapter contract or draft; skip full project design unless requested |
| Commercial web fiction,爽文, retention, opening hook, first-three-chapter, conflict variety, atmosphere, reader identification, or paid-conversion request | Add `retention_first` and `commercial_story` profiles to the narrowest branch | Approved commercial contract, conflict braid, free micro-arc, and retention review |

If the request is ambiguous, assume `workshop` and state the detected object in one compact `Current:` line.

## Phase 0: inspect context and capture intent

Before asking a question, inspect the current workspace when it is available:

1. Check for `AGENTS.md`, `CONTEXT.md`, existing `prompt/`, `projects/`, `core/orchestrator.py`, and `core/prompt_intake.py`.
2. Read only the files relevant to the requested branch. For an existing project, inspect its manifest, story bible, chapter files, and run status before proposing changes.
3. Extract a compact intake record:
   - genre and subgenre;
   - target readers, country, region or city culture, release markets, language, and tone;
   - protagonist, desire, capability, limitation, and fear;
   - relationship dilemma and opposing force;
   - external objective, deadline, failure cost, and ending direction;
   - world rules or realism constraints;
   - setting mode, fictional place-name policy, and any real-world references that need fictionalization;
   - chapter range, per-chapter length, and free-to-paid constraints;
   - reader promise, primary satisfaction source, opening window, and desired retention profile when relevant;
   - story-lead conflict, identification trigger, emotional target, satisfaction promise, unanswered question, and language-adjusted length;
   - explicit must-have, must-avoid, and existing canon.

Do not repeat facts already supplied. If a low-impact field is absent, write a conservative assumption and continue.

## Phase 0.5: lock audience and apply the local commercial profile

Run this phase for `workshop`, `expand`, and `prompt-revise` before comparing
story approaches.

1. If the target audience is missing, ask one compact question. Record the
   user-confirmed primary segment, age or life stage, reading motivation, and
   any material platform context.
2. Confirm country or cultural region, output language and register, and release
   scope only when they change character behavior, institutions, or idiom.
3. For commercial or retention fiction, load
   [references/commercial-story-design.md](references/commercial-story-design.md).
   Use its default 35-60 female-reader profile only when the user confirms that
   audience; another confirmed profile overrides it.
4. If samples or a corpus informed the request, also load
   [references/originality-isolation.md](references/originality-isolation.md)
   and enforce mechanism-only isolation.
5. Record the result as `audience_profile`; do not add a mandatory retrieval,
   source-ledger, query-list, embedding, vector-search, or RAG step.

```yaml
audience_profile:
  primary_reader_segment: <user-confirmed segment>
  age_or_life_stage: <user-confirmed range or stage>
  reading_motivation: <genre expectation or emotional need>
  country_or_cultural_region: <confirmed value or not material>
  language_and_register: <confirmed output language and register>
  release_scope: <single market or named markets>
  platform_context: <confirmed value or not material>
  creative_implications: []
```

Maintain a parallel `workshop_trace` for the reasoning that led to the design:

```yaml
workshop_trace:
  intake: <normalized user intent and explicit constraints>
  audience_decision: <user-confirmed primary segment, age/life stage, and reading motivation>
  market_decision: <country/region/language/release scope and date>
  approach_options:
    - id: approach_a
      summary: <structure and pressure>
      tradeoffs: <retention and continuity tradeoffs>
      selected: true|false
  section_decisions:
    section_a: <confirmed contract>
    section_b: <confirmed characters and relationships>
    section_c: <confirmed world, secrets, and timeline>
    section_d: <confirmed structure and opening>
    section_e: <confirmed quality gates and assumptions>
  open_assumptions: []
```

Append to this trace after every user answer or section confirmation. Preserve
rejected approaches and their tradeoffs as decision history; never rewrite
them as if they were selected. The final prompt carries the confirmed trace
and the unresolved assumptions, while the reader-facing manuscript excludes
the trace.

## Phase 1: high-impact question loop

Rank missing information by how much it can change the story:

1. If still missing, the user-confirmed target audience, then country, region, language, and release scope;
2. the user-selected narrative length and packaging mode;
3. protagonist identity, external objective, and failure cost;
4. core relationship or opposing force;
5. unusual restriction, irreversible midpoint, or ending choice.

Ask one concise question at a time. Prefer three or four concrete options plus a custom option. After each answer, update the intake record and remove the answered uncertainty. Stop when the story engine is causal and the next design section can be reviewed, or after five questions. Do not ask for names, cities, occupations, or decorative details when they do not change the engine; choose them later and record them as assumptions.

At the end of this phase, state the locked decisions and the remaining assumptions in a short table. The completion criterion is that a reader can repeat who wants what, why action is urgent, what makes it difficult, and what failure costs.

### Narrative-format decision

When length is absent, ask one focused question with these branches:

- short novel;
- standalone long novel;
- one multi-volume novel;
- let the workshop recommend, then confirm.

If the customer selects a long form, ask one follow-up only when needed to fix
total chapters, volume count, or whether “series” means several volumes inside
one book or several separately publishable books. Record the answer as
`author_selected`; record an accepted recommendation as
`recommended_then_confirmed`. Keep `confirmation_status: confirmed` in the
final Prompt. A raw `engine_recommended / pending_confirmation` value is a
proposal and does not enter a Workshop production Prompt.

## Phase 2: compare story approaches

Offer two or three approaches that differ in structure or pressure, not merely in names. For each approach include:

- opening point and time order;
- primary engine of conflict;
- how the protagonist gains agency;
- how the first sentence, first screen, and first three chapters deliver value;
- which conflict dimensions braid together and how the setting carries pressure;
- likely emotional experience and retention strength;
- continuity or pacing risk;
- what kind of ending it naturally supports.

Recommend one approach. Keep the user's stated emotional promise intact even when the recommended structure changes the chronology or setting. Wait for the user's choice before entering design review.

## Phase 3: sectioned design review

Present and confirm these sections in order. A user may approve all sections in one reply, but still show the sections clearly.

### Section A: story contract and emotional promise

Define:

- a one-sentence logline;
- event, relationship, emotional, and meaning promises;
- the protagonist's starting misbelief and final belief choice;
- the external objective, deadline, failure cost, and irreversible ending direction;
- the five interacting story forces: objective, relationship dilemma, internal misbelief, secret/question, adaptive pressure.
- a book-level `ending_contract` that names the finale window, main conflict
  payoff, protagonist's final choice, antagonist consequence, emotional
  afterglow, and every core payoff that must be evidenced before compilation.
- for every principal character, a lifecycle target (`required_arc_stage`) and
  a distinct semantic result (`required_outcome`) with an observable choice or
  changed condition that can prove the result in canon.
- when `retention_first` is active, a machine-readable opening contract with a
  reader promise, immediate event and stakes, first-screen signals, conflict
  braid, satisfaction loop, atmosphere pressure, identification anchor, chapter 1-3 value map,
  irreversible chapter-three step, and paid bridge.
- for every novel, a machine-readable `story_lead_contract` that fixes its
  placement before chapter one, output language, reader-facing heading,
  language-adjusted length range, core conflict, identification trigger,
  emotional target, earned satisfaction promise, unanswered question, and
  spoiler boundary.

### Section B: characters and relationships

For the protagonist, opposing force, and core supporting cast record:

- public identity, desire, need, capability, limitation, fear, boundary, secret, resources, and pressure response;
- personality core, visible behaviours, stress response, decision style, speech habits, emotional expression, strengths, flaws, change evidence, and personality complement or conflict with other characters;
- starting arc stage, intended final arc stage, semantic outcome, and the
  concrete action or durable condition that will evidence that outcome;
- what each person knows, misreads, hides, and wants from the others;
- relationship power, mutual leverage, trust evidence, suspicion evidence, and the next behaviour that can change the relationship.

Give supporting characters independent goals. A helper contributes a constrained resource; an opponent adapts after feedback. The protagonist remains the person whose choices change the causal chain.

### Section C: world, rules, secrets, and timeline

For realistic fiction, track money, work, family, law, health, logistics, and social pressure. For speculative fiction, track rule triggers, observable effects, limits, costs, exceptions, social consequences, and who knows each rule. Keep story-facing places, cities, districts, institutions, and landmarks fictional or abstract; retain real geography only as audience-research metadata or an explicit source citation.

For every major secret or mystery record its truth, knowledge distribution, first observable clue, fair misreading, escalation, payoff window, reveal, and changed action. Build a dated timeline with locations, travel or process duration, resource changes, and character knowledge at each milestone.

Maintain a payoff ledger with stable ids (`chN:fsM` for foreshadowing and
`payoff_N` for broader promises). Each item has a setup, target chapter, status
(`planted`, `recalled`, `paid`, or `intentional_open`), and concrete evidence.
Only `intentional_open` items may remain unresolved at the end, and they must
be declared in the ending contract before drafting.

### Section D: structure, opening, and retention arc

When the request signals `retention_first`, load
[references/retention-opening.md](references/retention-opening.md) before
designing this section. Use its language-neutral contract and adapt the window
and examples to the selected language, market, genre, and platform.

Use the customer's confirmed chapter count and packaging mode. When the customer requested a recommendation, explain the story-capacity score and obtain confirmation before locking it. Map acts or volumes with goals, midpoint revaluation, irreversible choice, stage payoff, and carry-forward consequence. Design the first three chapters as a complete micro-arc:

1. premise collision and immediate loss;
2. strategy test, relationship pressure, and first earned resource;
3. local payoff, costly protagonist choice, visible irreversible consequence, and a concrete next objective.

Before chapter one, design the required reader-facing story lead. Use roughly
`180-260` Chinese characters or `120-180` English words by default; for other
languages, choose and record a comparable platform-first-screen range based on
language density. The lead is compact packaging prose, not an author note or a
scene summary. It must name a concrete conflict or injustice, attach it to an
immediate loss, desire, fear, or boundary, expose a relationship/status/power
contrast, promise an earned counteraction or reversal, and leave one specific
question open. Preserve the main ending and the mechanism of the largest payoff.
Do not reuse the first paragraphs of chapter one; chapter one still begins with
its own event, choice, and local value delivery.

For `retention_first`, make the first sentence or opening window show at least two concrete signals, braid two or three conflict dimensions into a causal chain, and give every one of the first three chapters a local value delivery. Show atmosphere through details that affect pressure, choice, evidence, or relationship, and anchor identification in a specific desire, fear, habit, object, or boundary. Rotate the source of satisfaction across competence, evidence, status, relationship, moral courage, fair revelation, or survival rather than repeating one confrontation pattern.

If the user has a free-reading window, make the first paid chapter immediately deliver the direct consequence of chapter three. A chapter ending earns its hook by changing knowledge, power, relationship, resources, identity, rules, moral cost, or time. The retention reference supplies the review rubric and anti-pattern replacements.

Reserve the final 3-5 chapters as an explicit finale window. Plan them as a
chain of escalation, revelation, protagonist choice, visible cost,
antagonist consequence, emotional payoff, and afterglow. Do not open a new
core promise in the final chapter. The last chapter must emit
`Ending_Evidence` for an irreversible state change and reader-facing closure.

### Section E: quality gates and assumptions

List the chapter contract, continuity ledgers, emotional and payoff rotation, likely cliches, realism risks, and assumptions the Architect may resolve. Confirm that the ending resolves both the external problem and the protagonist's defining belief choice. Treat an unresolved core payoff, active main conflict, unclosed protagonist arc, missing antagonist consequence, or finale without an irreversible change as a blocking quality issue. Treat only explicitly declared intentional open threads as warnings.

The completion criterion for design review is explicit approval of the design direction. Before approval, keep the artifact as a proposal in the conversation rather than a final prompt.

## Phase 4: build the prompt artifact

After design approval, generate a complete prompt rather than a short summary. Load `references/prompt-contract.md`, `references/novel-classification.md`, `references/narrative-format.md`, and the relevant sections of `references/genre-adapters.md` and `references/quality-gates.md` before writing. When `retention_first` or `commercial_story` is active, also load `references/retention-opening.md`, `references/commercial-story-design.md`, and `references/originality-isolation.md` and include the approved opening and commercial contracts in the Prompt.

### File and naming rules

1. If the workspace contains Novel OS, write the prompt to `prompt/<title>.md`; otherwise ask for or use the current project's prompt directory.
2. Preserve Chinese filenames when the title is Chinese. Create a slug for the project directory by removing filesystem separators and collapsing whitespace; retain a readable title in the command.
3. Do not overwrite an existing prompt or project. Add a short suffix only when the user requests a new version; overwrite only after explicit instruction.
4. Include a `## Assumptions` section in the prompt for details the user did not decide.
5. Keep the prompt authoritative: repeat the locked title, language, genre, audience, tone, POV, chapter count, and word target in parseable `Key: Value` fields near the top, followed by exactly one canonical `[NOVEL_CLASSIFICATION_JSON]` block and one author-confirmed `[NARRATIVE_FORMAT_JSON]` block.

The prompt must instruct Novel OS to plan before drafting, preserve the structured `audience_profile`, preserve the confirmed `workshop_trace` and decision history, maintain character/relationship/secret/timeline/resource ledgers, emit and preserve machine-readable `story_lead_contract` and `ending_contract` records, update a payoff ledger after every chapter, use a per-chapter causal contract, preserve POV and knowledge boundaries, rotate conflict and hook types, reserve the final 3-5 chapters for payoff, and produce a final reader-facing manuscript without agent commentary. It must also preserve `setting_policy.mode: fictionalized` and use invented or abstract story-facing place names. Add exactly one schema-v2 JSON cover block using the `COVER_HANDOFF_BEGIN` and `COVER_HANDOFF_END` boundaries from `references/prompt-contract.md`; it records every required protagonist's confirmed age or age band, lived identity, wardrobe or environment, agency, relationships, decisive nodes, and source refs using only confirmed Sections A-E. For an active commercial profile, add exactly one `[COMMERCIAL_STORY_JSON]` block from the approved design and do not add a corpus lookup payload. The Scribe writes the story lead once at the start of chapter one's artifact, the Editor sharpens it without inventing unsupported promises, the Continuity Guardian checks its claims against the planned story, and the Style Curator preserves the output-language register. When `retention_first` is active, the prompt must also preserve the opening contract, first-screen evidence, conflict braid, satisfaction loop, atmosphere and identification decisions, first-three-chapter value map, and paid bridge.

Use this exact machine-readable Markdown boundary in the chapter-one artifact so
Novel OS can compile the lead without replacing chapter-one navigation:

```markdown
## STORY_LEAD: 序

<Chinese reader-facing lead, normally 180-260 characters>

# 第一章 <chapter title>

<chapter-one prose with its own opening hook>
```

For English, use `## STORY_LEAD: Story Lead` and the approved English chapter
heading. For other languages, keep the ASCII `STORY_LEAD:` marker and localize
only the reader-facing label after the colon. The compiled Markdown, EPUB,
HTML, PDF, and DOCX display the localized label and omit the machine marker.

Every Scribe, Editor, and Continuity Guardian state block must include these
fields when applicable:

```text
Payoff_Events:
  - <payoff_id> | status=<recalled|paid|intentional_open> | evidence=<observable change> | chapter=<number>
Arc_State_Updates:
  - <character_id> | stage=<beginning|middle|climax|resolution> | progress=<0-100> | outcome=<canonical outcome value from ending_contract> | evidence=<choice or observable state>
Personality_State_Updates:
  - <character_id> | trait=<性格特征> | pressure_response=<压力下反应> | evidence=<具体行为>
Ending_Evidence:
  - irreversible_change=<observable final state>
  - emotional_payoff=<reader-facing closure>
```

For the final window, repeat the authoritative ending contract in the
Architect, Scribe, Editor, and Guardian contexts. Compile only after
`book_completion_report.json` records a passing ending review.
Treat `stage` as narrative lifecycle position and `outcome` as the
story-specific result, such as independence, accountability, reconciliation,
or a deliberately chosen loss. Never encode a semantic result as an invented
arc stage.
Use stable outcome identifiers with exact matching when possible. When
`outcome_match_mode` is omitted, `auto` keeps short ASCII identifiers exact and
allows formatting or appended detail in natural-language outcomes. An explicit
`normalized` mode tolerates formatting-only differences, while `contains`
permits an intentionally richer actual state. Put approved paraphrases in
`required_outcome_aliases`; avoid fuzzy similarity for the quality gate.

Keep `audience_profile`, `workshop_trace`, `Personality_State_Updates`, and
the confirmed Section A-E decisions in working artifacts and reports so a
project detail view can render the reasoning trail without another design pass.

## Phase 5: validate the prompt

When `core/prompt_intake.py` exists, validate before handing off:

```bash
PYTHONPATH=core ./venv/bin/python -c "from pathlib import Path; from prompt_intake import ingest_prompt; r=ingest_prompt(Path('PROMPT_VALIDATION_PROJECT'), Path('PROMPT_PATH')); print(r.brief)"
```

Replace `PROMPT_VALIDATION_PROJECT` and `PROMPT_PATH` with the actual paths. Confirm that the parsed brief contains the locked title, genre, language, chapter count, word target, audience, tone, premise, a canonical `classification` object whose ids match the approved design, and a canonical `narrative_format` whose `confirmation_status` is `confirmed`. Verify that its chapter and volume boundaries match the customer's decision. Also confirm that the prompt contains a user-confirmed `audience_profile`, a `setting_policy` with `mode: fictionalized`, a `workshop_trace` with Section A-E decisions and open assumptions, plus personality fields for every principal character. For an active commercial profile, parse exactly one `COMMERCIAL_STORY_JSON` block and verify that no raw sample, corpus path, embedding, vector-search, or nearest-match payload is present. Parse the cover block with `core.cover_handoff.parse_cover_handoff`; verify the exact title, user-confirmed audience, core conflict, decisive node, secondary task, fictional world signals, and forbidden elements. Use a temporary validation project when no project has been selected; keep a real project untouched until the user runs the command.

Also run `git diff --check -- <prompt path>` when the file is inside a Git workspace. For every Prompt, verify that `story_lead_contract.required` is true, its language and length unit agree, its conflict/payoff/question fields are concrete, and the chapter-one output protocol uses the `STORY_LEAD:` marker before the real chapter heading. For a `retention_first` Prompt, also verify the opening contract, the first-screen signal list, at least two conflict dimensions, local value for chapters 1-3, and the paid bridge are present and internally consistent. Report parser output and any corrected field; do not claim validation from file existence alone.

## Phase 6: hand off the run command

Only after validation, provide a command built from the actual artifact and approved design. Detect the repository's supported launcher before choosing the command:

1. When executable `./deploy.sh` exists and `./deploy.sh novel --help` succeeds, use the Docker launcher as the primary path.
2. Use the native venv/orchestrator path only when the Docker launcher is absent or the user explicitly selects native execution.

### Docker launcher (preferred when available)

For a known Prompt, print the direct interactive command with actual paths:

```bash
cd /path/to/Novel-OS
./deploy.sh novel './prompt/TITLE.md'
```

The launcher displays the approved values for confirmation, reuses a healthy
backend container, starts only the backend when needed, and leaves the frontend
and existing containers in place. A separate `./deploy.sh up` or
`./deploy.sh restart` step is not part of a normal novel run.

When exact approved values must survive non-default environment settings, print
them with the command rather than relying on the operator to re-enter them:

```bash
cd /path/to/Novel-OS
NOVEL_OS_PROJECT_NAME='PROJECT_SLUG' \
NOVEL_OS_TITLE='TITLE' \
NOVEL_OS_GENRE='GENRE' \
NOVEL_OS_CHAPTERS='CHAPTERS' \
NOVEL_OS_WORDS='WORDS' \
NOVEL_OS_EDIT_MODE='developmental' \
NOVEL_OS_APPROVAL='auto' \
NOVEL_OS_QUALITY_POLICY='evidence_v1' \
NOVEL_OS_OUTPUT='markdown epub' \
./deploy.sh novel './prompt/TITLE.md'
```

Replace every uppercase token with the approved value before showing the user.
For automation rather than an interactive terminal, add
`NOVEL_OS_NONINTERACTIVE=1`; include all required values explicitly. Report the
Docker artifact path as `docker-data/projects/PROJECT_SLUG/outputs/`.

Use the launcher's persisted-run commands for recovery:

```bash
./deploy.sh novel-status 'RUN_ID'
./deploy.sh novel-resume 'RUN_ID'
./deploy.sh novel-retry 'RUN_ID'
```

Use only `RUN_ID` for a specific persisted run; the launcher resolves it across
all projects. Omit `RUN_ID` when the user explicitly wants the most recent run.
If a legacy deployment contains a duplicate RUN_ID, the launcher reports the
matches and accepts the compatibility form `PROJECT RUN_ID` to disambiguate.

### Native fallback

In native mode, use the repository venv interpreter so the command does not
depend on a global `python` executable:

```bash
cd /path/to/Novel-OS
PYTHONPATH=core ./venv/bin/python core/orchestrator.py run \
  --project './projects/PROJECT_SLUG' \
  --prompt './prompt/TITLE.md' \
  --title 'TITLE' \
  --genre 'GENRE' \
  --chapters CHAPTERS \
  --words WORDS \
  --edit-mode developmental \
  --approval auto \
  --quality-policy evidence_v1 \
  --max-retries 5 \
  --max-quality-repairs 2 \
  --output markdown
```

Replace every uppercase token with the actual values before showing the user the command. In both modes, explain that `approval auto` enables bounded quality repair and that evidence-backed promotion preserves committed chapters across recovery. The command is a handoff, not an invitation to run the model in the current turn.

If the user asks to run it, first verify the configured provider endpoint, Prompt path, and selected launcher, then execute the command. In Docker mode, go directly through `./deploy.sh novel`; keep a healthy service running. A recoverable failure should use the selected launcher's persisted resume/retry path; a durable evidence or canon-integrity issue remains a visible blocked result.

When cover creation is in scope, hand the validated Prompt to `novel-cover-studio` only after Sections A-E are approved. That Skill presents 3-5 distinct concepts before image generation, then prefers `./deploy.sh novel-cover` and returns the candidate paths plus Studio selection URL. Cover generation is independent of the novel run and never requires a service restart.

## Existing canon branch

When an existing manuscript or run is present:

1. Read the current manifest, receipts, final artifacts, story state, and prompt source.
2. Separate confirmed canon, derived indexes, and unresolved hypotheses.
3. Build the new design around confirmed canon or explicitly start a new project.
4. Preserve promoted chapters and their evidence receipts; never regenerate them merely because an upstream outline changed.
5. Include the canonical names, timeline, secrets, and unresolved threads in the revised prompt or continuation contract.

## Completion report

Finish with a compact report containing:

- active title and project path;
- locked design decisions and assumptions;
- prompt path and parser result;
- story-lead language, target length, and quality-gate result;
- cover-handoff validation and whether cover concepts or images were requested;
- exact start command;
- what was verified locally;
- whether a model run was started.

The Skill is complete when the user can open the prompt, see every approved design decision represented, run the printed command without editing placeholders, and understand the expected final artifact path.
