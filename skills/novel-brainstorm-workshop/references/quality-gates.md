# Quality Gates

Use these checks during design review and again before handing off the Prompt. They are evidence questions, not a numeric promise about commercial performance.

## Story engine

- A reader can state who wants what, why action is urgent, what blocks it, and what failure costs.
- Each of the five story forces changes at least two other forces.
- The protagonist's visible capability creates options and the limitation creates meaningful costs.
- The opponent or environment adapts after the protagonist acts.

## Chapter and retention

- Every chapter has an observable goal, obstacle, active choice, consequence, local payoff, and next pressure.
- The protagonist changes the causal chain in every chapter or in a deliberately marked interlude.
- The first three chapters form a complete micro-arc with a visible irreversible change.
- A free-window ending completes a stage payoff before creating the next target; the first paid chapter opens on its direct consequence.
- Recent chapters vary opening situation, setting, conflict, strategy, emotional result, and hook type.

## Continuity and fairness

- Names, ages, relationships, locations, resources, dates, and knowledge boundaries are tracked.
- Every major reversal has earlier evidence, a plausible motive, a fair prior interpretation, and a changed next action.
- Secrets have a payoff window, an answer, and an observable aftermath.
- Legal, medical, technical, cultural, and professional processes match the chosen setting.

## Emotional integrity

- Emotion is carried by concrete behaviour, objects, choices, and consequences rather than repeated labels.
- Hardship changes the protagonist's options and self-definition instead of serving as repeated punishment.
- Supporting characters have independent motives and boundaries.
- The ending resolves the external problem and the protagonist's defining belief choice without a coincidental rescue.

## Prompt handoff

- Top-level fields contain one fixed title, chapter count, and total word target.
- All approved decisions appear in the Prompt; assumptions are labeled in one section.
- Architect, Scribe, Editor, Continuity Guardian, and Style Curator have distinct responsibilities and shared state fields.
- The final reader-facing artifact excludes planning commentary, agent analysis, scores, and work logs.
- `prompt_intake` returns the expected title, genre, language, chapters, words, and premise when Novel OS is present.

