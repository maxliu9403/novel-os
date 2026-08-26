---
name: novel-brainstorm-workshop
description: "Turn a rough novel idea into a confirmed, causally sound story design and an executable Novel OS prompt. Use this skill whenever a user mentions writing a novel, developing a story premise, brainstorming characters, expanding a plot, building a story bible, tracking secrets or timelines, improving a novel prompt, or starting a new book. It is especially useful when the user has only a few scenes, a theme, a relationship conflict, or an ending idea and needs structured creative development before drafting."
compatibility: "Works with Novel OS prompt files and its Python prompt_intake/orchestrator commands when those files are present."
---

# Novel Brainstorm Workshop

Turn an incomplete novel idea into a deliberate story engine, a reviewed design, and a prompt that can drive a complete Novel OS run. The workshop creates predictable decisions, not identical stories: preserve the user's emotional intent while making every important assumption visible and every major turn causally earned.

## Operating contract

- Follow the user's language. Chinese requests receive Chinese discussion and Chinese prompt files unless the user asks otherwise.
- Treat the user's rough idea as the source of intent, not as a finished outline. Preserve explicit decisions and label low-impact additions as assumptions.
- Keep the interaction focused. Ask at most five high-impact questions, one question per message. Ask only when an answer could change the story identity, audience, causal engine, protagonist arc, or ending.
- Present two or three materially different approaches before fixing the structure. Include a recommendation and the tradeoff behind it.
- Present the design in reviewable sections. Wait for confirmation after each section before writing the final prompt.
- Use positive quality targets. A prohibition belongs only beside a concrete replacement behaviour.
- The default deliverable is a prompt file plus a start command. The user decides when to run the model.
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

If the request is ambiguous, assume `workshop` and state the detected object in one compact `Current:` line.

## Phase 0: inspect context and capture intent

Before asking a question, inspect the current workspace when it is available:

1. Check for `AGENTS.md`, `CONTEXT.md`, existing `prompt/`, `projects/`, `core/orchestrator.py`, and `core/prompt_intake.py`.
2. Read only the files relevant to the requested branch. For an existing project, inspect its manifest, story bible, chapter files, and run status before proposing changes.
3. Extract a compact intake record:
   - genre and subgenre;
   - target readers, language, and tone;
   - protagonist, desire, capability, limitation, and fear;
   - relationship dilemma and opposing force;
   - external objective, deadline, failure cost, and ending direction;
   - world rules or realism constraints;
   - chapter range, per-chapter length, and free-to-paid constraints;
   - explicit must-have, must-avoid, and existing canon.

Do not repeat facts already supplied. If a low-impact field is absent, write a conservative assumption and continue.

## Phase 1: high-impact question loop

Rank missing information by how much it can change the story:

1. protagonist identity, external objective, and failure cost;
2. core relationship or opposing force;
3. unusual restriction, rule, secret, or deadline;
4. irreversible midpoint or ending choice;
5. audience promise, tone, and length.

Ask one concise question at a time. Prefer three or four concrete options plus a custom option. After each answer, update the intake record and remove the answered uncertainty. Stop when the story engine is causal and the next design section can be reviewed, or after five questions. Do not ask for names, cities, occupations, or decorative details when they do not change the engine; choose them later and record them as assumptions.

At the end of this phase, state the locked decisions and the remaining assumptions in a short table. The completion criterion is that a reader can repeat who wants what, why action is urgent, what makes it difficult, and what failure costs.

## Phase 2: compare story approaches

Offer two or three approaches that differ in structure or pressure, not merely in names. For each approach include:

- opening point and time order;
- primary engine of conflict;
- how the protagonist gains agency;
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

### Section B: characters and relationships

For the protagonist, opposing force, and core supporting cast record:

- public identity, desire, need, capability, limitation, fear, boundary, secret, resources, and pressure response;
- what each person knows, misreads, hides, and wants from the others;
- relationship power, mutual leverage, trust evidence, suspicion evidence, and the next behaviour that can change the relationship.

Give supporting characters independent goals. A helper contributes a constrained resource; an opponent adapts after feedback. The protagonist remains the person whose choices change the causal chain.

### Section C: world, rules, secrets, and timeline

For realistic fiction, track money, work, family, law, health, logistics, and social pressure. For speculative fiction, track rule triggers, observable effects, limits, costs, exceptions, social consequences, and who knows each rule.

For every major secret or mystery record its truth, knowledge distribution, first observable clue, fair misreading, escalation, payoff window, reveal, and changed action. Build a dated timeline with locations, travel or process duration, resource changes, and character knowledge at each milestone.

Maintain a payoff ledger with stable ids (`chN:fsM` for foreshadowing and
`payoff_N` for broader promises). Each item has a setup, target chapter, status
(`planted`, `recalled`, `paid`, or `intentional_open`), and concrete evidence.
Only `intentional_open` items may remain unresolved at the end, and they must
be declared in the ending contract before drafting.

### Section D: structure, opening, and retention arc

Choose a chapter count within the user's range and explain the choice. Map acts or volumes with goals, midpoint revaluation, irreversible choice, stage payoff, and carry-forward consequence. Design the first three chapters as a complete micro-arc:

1. premise collision and immediate loss;
2. strategy test, relationship pressure, and first earned resource;
3. local payoff, costly protagonist choice, visible irreversible consequence, and a concrete next objective.

If the user has a free-reading window, make the first paid chapter immediately deliver the direct consequence of chapter three. A chapter ending earns its hook by changing knowledge, power, relationship, resources, identity, rules, moral cost, or time.

Reserve the final 3-5 chapters as an explicit finale window. Plan them as a
chain of escalation, revelation, protagonist choice, visible cost,
antagonist consequence, emotional payoff, and afterglow. Do not open a new
core promise in the final chapter. The last chapter must emit
`Ending_Evidence` for an irreversible state change and reader-facing closure.

### Section E: quality gates and assumptions

List the chapter contract, continuity ledgers, emotional and payoff rotation, likely cliches, realism risks, and assumptions the Architect may resolve. Confirm that the ending resolves both the external problem and the protagonist's defining belief choice. Treat an unresolved core payoff, active main conflict, unclosed protagonist arc, missing antagonist consequence, or finale without an irreversible change as a blocking quality issue. Treat only explicitly declared intentional open threads as warnings.

The completion criterion for design review is explicit approval of the design direction. Before approval, keep the artifact as a proposal in the conversation rather than a final prompt.

## Phase 4: build the prompt artifact

After design approval, generate a complete prompt rather than a short summary. Load `references/prompt-contract.md` and the relevant sections of `references/genre-adapters.md` and `references/quality-gates.md` before writing.

### File and naming rules

1. If the workspace contains Novel OS, write the prompt to `prompt/<title>.md`; otherwise ask for or use the current project's prompt directory.
2. Preserve Chinese filenames when the title is Chinese. Create a slug for the project directory by removing filesystem separators and collapsing whitespace; retain a readable title in the command.
3. Do not overwrite an existing prompt or project. Add a short suffix only when the user requests a new version; overwrite only after explicit instruction.
4. Include a `## Assumptions` section in the prompt for details the user did not decide.
5. Keep the prompt authoritative: repeat the locked title, language, genre, audience, tone, POV, chapter count, and word target in parseable `Key: Value` fields near the top.

The prompt must instruct Novel OS to plan before drafting, maintain character/relationship/secret/timeline/resource ledgers, emit and preserve a machine-readable `ending_contract`, update a payoff ledger after every chapter, use a per-chapter causal contract, preserve POV and knowledge boundaries, rotate conflict and hook types, reserve the final 3-5 chapters for payoff, and produce a final reader-facing manuscript without agent commentary.

Every Scribe, Editor, and Continuity Guardian state block must include these
fields when applicable:

```text
Payoff_Events:
  - <payoff_id> | status=<recalled|paid|intentional_open> | evidence=<observable change> | chapter=<number>
Arc_State_Updates:
  - <character_id> | stage=<beginning|middle|climax|resolution> | progress=<0-100> | evidence=<choice or observable state>
Ending_Evidence:
  - irreversible_change=<observable final state>
  - emotional_payoff=<reader-facing closure>
```

For the final window, repeat the authoritative ending contract in the
Architect, Scribe, Editor, and Guardian contexts. Compile only after
`book_completion_report.json` records a passing ending review.

## Phase 5: validate the prompt

When `core/prompt_intake.py` exists, validate before handing off:

```bash
PYTHONPATH=core ./venv/bin/python -c "from pathlib import Path; from prompt_intake import ingest_prompt; r=ingest_prompt(Path('PROMPT_VALIDATION_PROJECT'), Path('PROMPT_PATH')); print(r.brief)"
```

Replace `PROMPT_VALIDATION_PROJECT` and `PROMPT_PATH` with the actual paths. Confirm that the parsed brief contains the locked title, genre, language, chapter count, word target, audience, tone, and premise. Use a temporary validation project when no project has been selected; keep a real project untouched until the user runs the command.

Also run `git diff --check -- <prompt path>` when the file is inside a Git workspace. Report parser output and any corrected field; do not claim validation from file existence alone.

## Phase 6: hand off the run command

Only after validation, provide a command built from the actual artifact and approved design. In a Novel OS workspace, use the venv interpreter so the command does not depend on a global `python` executable:

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

Replace every uppercase token with the actual values before showing the user the command. Explain that `approval auto` enables bounded quality repair and that evidence-backed promotion preserves committed chapters across recovery. The command is a handoff, not an invitation to run the model in the current turn.

If the user asks to run it, first verify the configured provider endpoint and the project path, then execute the command. A recoverable failure should use the persisted manifest's resume/retry path; a durable evidence or canon-integrity issue remains a visible blocked result.

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
- exact start command;
- what was verified locally;
- whether a model run was started.

The Skill is complete when the user can open the prompt, see every approved design decision represented, run the printed command without editing placeholders, and understand the expected final artifact path.
