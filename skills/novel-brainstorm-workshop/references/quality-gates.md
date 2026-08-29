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

### Reader-facing story lead gate

Apply to every Prompt and completed novel:

- `story_lead_contract.required` is true and placement is `before_chapter_1`.
- The chapter-one artifact begins with `## STORY_LEAD: <localized reader heading>`, then the lead, then the real chapter-one heading and prose.
- Chinese leads normally contain `180-260` characters; English leads normally contain `120-180` words; another language uses its recorded market- and platform-adjusted range.
- The lead names a concrete conflict or injustice and connects it to a recognizable desire, loss, fear, humiliation, or boundary.
- A relationship, identity, status, or power contrast intensifies the conflict instead of relying on abstract promotional claims.
- The satisfaction promise is earned through agency, competence, evidence, leverage, relationship change, or visible consequence.
- The ending leaves one specific question open while preserving the full outcome and the mechanism of the largest payoff.
- The lead does not copy chapter one's opening paragraphs, and chapter one retains its own event, choice, and local value delivery.
- Reader-facing Markdown, EPUB, HTML, PDF, and DOCX show only the localized lead heading; `STORY_LEAD:` is absent and chapter-one navigation remains correct.

### Retention-first opening gate

Apply when the Prompt contains `retention_profile.mode: retention_first`:

- The opening contract names a concrete event, immediate stakes, a specific question, and a protagonist choice.
- The selected first-screen window contains at least two recorded signals from `event`, `loss`, `contradiction`, `cost`, `question`, or `choice`.
- The first three chapters use at least two conflict dimensions and connect them through protagonist action and opposing feedback.
- Chapter 1 shows agency and delivers a local reward, reveal, counteraction, or meaningful change.
- Chapter 2 shows an adaptive response and an earned resource, evidence, tactical win, or relationship truth; the cost remains active.
- Chapter 3 answers a short-term question and crosses a visible irreversible threshold with a concrete next objective.
- The satisfaction loop names the protagonist's recognition, move, opposing response, visible consequence, and higher-level goal; each cycle delivers a distinct form of value.
- Atmosphere details change pressure, evidence, relationship, movement, or meaning; they are not interchangeable decoration.
- The identification anchor is a specific desire, fear, habit, object, or boundary expressed in the target market's register.
- Satisfaction sources and escalation dimensions rotate across competence, evidence, status, relationship, moral choice, knowledge, resources, and time.
- The first paid chapter displays the direct consequence named in `paid_bridge` before opening a separate thread.
- The evidence cites chapter, scene, or draft locations. Numeric scores do not replace evidence.

`retention_first` is a cross-language design profile. Adjust the opening window,
idiom, social assumptions, and reader promise for each language and market
branch; a Chinese character count is not a universal English or multilingual
threshold.

## Continuity and fairness

- Names, ages, relationships, locations, resources, dates, and knowledge boundaries are tracked.
- Story-facing cities, districts, institutions, landmarks, and other place names are invented or abstract; real geography appears only in separated audience-research metadata.
- Every major reversal has earlier evidence, a plausible motive, a fair prior interpretation, and a changed next action.
- Secrets have a payoff window, an answer, and an observable aftermath.
- Legal, medical, technical, cultural, and professional processes match the chosen setting.

## Emotional integrity

- Emotion is carried by concrete behaviour, objects, choices, and consequences rather than repeated labels.
- Hardship changes the protagonist's options and self-definition instead of serving as repeated punishment.
- Supporting characters have independent motives and boundaries.
- The ending resolves the external problem and the protagonist's defining belief choice without a coincidental rescue.

## Book ending gate

- The story foundation contains an enforced `ending_contract` with a finale
  window covering the last 3-5 chapters.
- The main conflict thread reaches its required terminal status.
- Every required payoff is `paid` with chapter evidence, or is explicitly
  declared `intentional_open` in the contract.
- Each principal character reaches both the required lifecycle stage and the
  distinct semantic outcome through an observable choice or durable changed
  condition, not a retrospective explanation.
- Outcome matching is explicit: omitted `outcome_match_mode` uses `auto`
  (strict for short ASCII identifiers, tolerant for natural-language state);
  `exact` enforces a complete value, `normalized` tolerates formatting, and
  `contains` permits a richer state. Paraphrases are listed in
  `required_outcome_aliases` rather than accepted by fuzzy similarity.
- The antagonist receives a causal consequence proportional to the story's
  promise when the contract marks that outcome as required.
- The final chapter contains explicit `Ending_Evidence` for an irreversible
  state change and emotional closure.
- A generated `book_completion_report.json` is the final quality receipt;
  `completed` and Compile are not synonyms for a passing ending review.

## Prompt handoff

- The target audience is explicitly confirmed by the user and records the primary reader segment, age or life stage, and main reading motivation; it is not an inferred assumption.
- The Prompt carries `setting_policy.mode: fictionalized` and keeps real market/source locations inside `audience_research` rather than the story-facing world ledger.
- The Prompt carries a complete `story_lead_contract` and assigns drafting, editing, continuity validation, and style preservation responsibilities for the lead.
- Top-level fields contain one fixed title, chapter count, and total word target.
- All approved decisions appear in the Prompt; assumptions are labeled in one section.
- Architect, Scribe, Editor, Continuity Guardian, and Style Curator have distinct responsibilities and shared state fields.
- The final reader-facing artifact excludes planning commentary, agent analysis, scores, and work logs.
- `prompt_intake` returns the expected title, genre, language, chapters, words, and premise when Novel OS is present.
