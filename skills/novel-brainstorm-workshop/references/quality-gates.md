# Quality Gates

Use these checks during design review and again before handing off the Prompt. They are evidence questions, not a numeric promise about commercial performance.

## Evidence and stage boundaries

For a plan, cite chapter-map entries, character decisions, and payoff setups.
For a draft, cite actual chapter/scene passages and observed consequences.
Use `pass`, `revise`, or `not_run` per applicable gate; never mark unwritten
chapters as passing because their outline promises quality. Keep planning,
parser validation, and manuscript review as separate results. See
[creation-path.md](creation-path.md) for prerequisite and repair routes.

When `high-retention-web-novel` performs prose review, use its existing
rubric as a diagnostic aid after checking hard failures. A total score never
overrides passage-level problems in voice, language, emotion, originality, or
continuity. Use only applicable criteria; a normal non-paywall chapter is not
penalized for lacking a paid bridge. Missing required evidence is unreviewed,
not not-applicable.

## Originality and material use

- Classify every supplied material before canon extraction. The user's own
  framework is `author_brief`; preserve approved facts. External inspiration
  stays `reference_only`; current-project continuation uses `project_canon`.
  A format example does not supply the new book's cast or plot.
- Compare the approved design with the supplied references: independent
  character agency, relationship dynamics, distinctive event/reveal sequence,
  and climax mechanism must be supported by the new story's causal design.
- Generic genre conventions may overlap. Renaming a cast or changing wording
  while retaining a distinctive plot chain fails.
- Keep source-specific comparisons private; inspect all runtime attachments for
  accidental source injection. Review actual prose again before delivery.

## Story engine

- A reader can state who wants what, why action is urgent, what blocks it, and what failure costs.
- Story forces interact: changing a goal, relationship, belief, secret, or
  pressure changes meaningful choices elsewhere rather than adding parallel
  threads merely to fill a template.
- The protagonist's visible capability creates options and the limitation creates meaningful costs.
- The opponent or environment adapts after the protagonist acts.

## Chapter and retention

- Every chapter has a stated function and observable reader value. Active plot
  chapters show goal, obstacle, choice, and consequence. Aftermath, ensemble,
  observational, or quiet chapters can deepen understanding, emotional meaning,
  or relationship experience without an external turn or on-page protagonist
  decision. Identify what would be lost by deletion.
- The protagonist's choices drive the main turns. An interlude must change a
  relevant relationship, knowledge, consequence, or earned emotional meaning
  that the subsequent story uses; labeling a chapter an interlude is no exemption.
- The first three chapters form a complete micro-arc with a visible irreversible change.
- A free-window ending completes a stage payoff before creating the next target; the first paid chapter opens on its direct consequence.
- Recent chapters vary in function and emotional experience; repeated settings,
  rituals, or motifs may remain when their changing meaning earns the return.

## Quality without padding

For active plot chapters record: entry state -> goal/pressure -> action and
feedback -> choice/cost -> exit state -> reader value -> next consequence.
For other chapter functions, cite the before/after difference in reader
understanding, emotional experience, or relationship meaning and the relevant
passage. Do not invent actions merely to fill a contract template.
During drafting, verify those claims against the prose rather than copying the
plan's claims into a passing report.

- **Deletion test:** if removing a scene or repeated passage leaves causality,
  character understanding, relationship development, and the earned emotional
  experience unchanged, cut, merge, or replace it. Identify what is lost when a
  valuable scene is removed. Quiet grief, intimacy, recovery, and reflection
  can earn space without an external twist or a new fight.
- **Causal progress:** new pressure follows an action or changing circumstance.
  Repeated accusations, punishments, misunderstandings, or threats need a new
  decision or durable consequence; louder repetitions do not count.
- **Character depth:** choices reveal competing needs, limits, self-deception,
  and change. Antagonists and helpers have goals beyond provoking or rescuing
  the protagonist. Convenient stupidity cannot supply the plot engine.
- **Emotional development:** a feeling develops through perception, behavior,
  subtext, and consequence. Repeating an emotional label or inner monologue
  does not deepen it. Let payoff and recovery have enough space to feel earned.
- **Language:** precise, character-specific attention and purposeful dialogue
  take precedence over generic slogans, stock bodily reactions, and repetitive
  atmospheric description. Preserve voice and rhythm rather than flattening
  every scene into short action sentences.
- **Length:** word counts are planning constraints, not permission to pad.
  When substance cannot sustain the agreed length, revise conflict/arc design
  or obtain approval for a revised length. Do not silently shorten, inflate,
  or add unrelated subplots to meet a quota.

Repair in this order: originality and causal structure -> agency and character
arcs -> payoff and emotional development -> continuity -> pacing and prose.
Return a failed chapter to its contract when the problem is structural; surface
polish cannot repair a missing decision or payoff. Recheck the revision and
affected later setups before promoting its facts into canonical state.

## Prose, character, and whole-arc review

- **Voice calibration:** use this project's approved prose or concise original
  conflict/quiet samples grounded in its design. New samples are not canon.
  Define narrative distance, attention, register, rhythm, imagery, and dialogue
  strategy; avoid rigid sentence quotas or imitation of reference novels.
- **POV and language:** inspect representative passages for what the focal
  character can perceive or infer, unexplained distance shifts, borrowed
  knowledge, interchangeable metaphors, and narrator explanation that preempts
  the reader's experience. Each character's history shapes attention and voice.
- **Scene presence:** important decisions and emotional turns unfold through
  observable behavior, dialogue/subtext, perceptions, and reactions. Check that
  prose has not merely expanded the outline into a list of completed events.
  Ordinary transitions may be summarized.
- **Emotional evidence:** major trust/belief changes connect prior state,
  trigger, interpretation/resistance, choice or behavior, and later evidence.
  Review neighboring chapters for abrupt forgiveness, intimacy, or repeated
  identical awakenings. Plans remain plans until the prose supports them.
- **Arc rhythm:** at a major arc boundary, reread consecutive prose, including
  recovery and payoff aftermath. Check whether familiar dilemmas gain meaning,
  earlier consequences persist, and the ending of the arc grows from its scenes.
  Do not demand a cliffhanger, reversal, or moral ambiguity in every scene.
- **Separate prose findings:** record evidence and `pass|revise|not_run` for
  voice/POV, language, dialogue/subtext, scene presence, and emotion. Recheck
  affected facts after stylistic revision. A high structural score is not a
  reason to waive a substantial prose problem.
- **Independent reading:** when available, give a fresh reader actual prose,
  necessary adjacent passages, and reader/language constraints. Withhold the
  outline, planned payoff, author explanation, prior scores, and desired verdict.
  Ask for specific places of confusion, disengagement, and emotional engagement.
  Verify continuity separately against canon; flag unavailable context instead
  of inventing it. Without a separate reader, report a second self-review, not
  an independent test. Model responses are editorial simulations, not real
  reader feedback or conversion predictions.

These are Skill-level writing and review practices. Do not modify the framework
or claim automatic backend enforcement to satisfy this checklist.

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
- Story-facing places follow the approved real, fictionalized or mixed setting.
  Keep release geography separate; do not silently replace real cities supplied
  by the author. Record material professional or jurisdictional research needs.
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
  window appropriate to the approved structure; the last 3-5 chapters are a
  recommendation, never a demand for chapters beyond the actual book length.
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
- The Prompt carries the approved `setting_policy` and records release-market
  context in `audience_profile`, separately from story geography.
- A commercial Prompt carries exactly one valid `COMMERCIAL_STORY_JSON` block and no raw corpus, embedding, vector-search, or nearest-match payload.
- The Prompt carries a complete `story_lead_contract` and assigns drafting, editing, continuity validation, and style preservation responsibilities for the lead.
- Top-level fields contain one fixed title, chapter count, and total word target.
- Per-chapter word/character targets and lead lengths allow reasonable variation.
  Diagnose weak or padded prose from its content, not a minor length deviation.
  The Skill does not add strict manuscript-length gates or alter engine checks.
- All approved decisions appear in the Prompt; assumptions are labeled in one section.
- Architect, Scribe, Editor, Continuity Guardian, and Style Curator have distinct responsibilities and shared state fields.
- The Scribe receives only the approved story contract, current chapter contract,
  verified and promoted reader-value summaries, and the current context pack;
  no raw corpus material, corpus path, retrieval result, or self-certification
  payload is routed into its prompt.
- Editor review explicitly covers repeated humiliation, passive turns,
  unsupported rescue, and repeated hook mechanics.
- Continuity review explicitly covers evidence provenance, child knowledge and
  voice, institutional plausibility, and contract-to-prose delivery.
- Style review explicitly covers character-specific attention, work knowledge,
  speech strategy, shame trigger, body response, and template phrase repetition.
- The six template expressions (`I did not cry`, `I did not scream`, `my blood
  ran cold`, `my world shattered`, `they thought I was weak`, `the game had just
  begun`) are reviewed for repeated use across recent chapters; they are not
  blanket word bans.
- The final reader-facing artifact excludes planning commentary, agent analysis, scores, and work logs.
- `prompt_intake` returns the expected title, genre, language, chapters, words, and premise when Novel OS is present.
