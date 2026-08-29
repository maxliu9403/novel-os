# Prompt Contract

The generated prompt is the handoff between the approved design and Novel OS. Keep the top fields parseable and keep the body specific enough that Architect, Scribe, Editor, Continuity Guardian, and Style Curator share one canon.

## Required top fields

```text
Title: <approved title>
Genre: <primary and secondary genre>
Audience: <target readers>
Language: <output language>
Tone: <voice and emotional temperature>
POV: <viewpoint and switching rule>
Chapters: <fixed chapter count>
Words: <total prose target>
```

Use actual values in the final artifact. `Audience` must come from a user-confirmed target audience decision; when it is missing, return to the question loop before generating the Prompt. A range may be discussed in the body, but the top-level run needs one fixed chapter count and one total target.

## Required body sections

1. **Core task**: premise, scope, length, originality, and what the final reader sees.
2. **Story contract**: event, relationship, emotional, and meaning promises.
3. **Story engine**: external objective, relationship dilemma, internal misbelief, secret/question, and adaptive pressure.
4. **Audience research and regional adaptation**: market scope for every country or region, language and register, audience segment, platform and genre signals, cultural context, emotional drivers, source records, evidence type, confidence, and creative implications. Keep market branches separate for multi-market releases.
5. **Character ledger**: public identity, desire, need, capability, limitation, fear, boundary, secret, resources, knowledge, pressure response, personality core, visible behaviours, decision style, speech habits, emotional expression, strengths, flaws, change evidence, and personality conflicts or complements.
6. **Relationship ledger**: power, leverage, trust evidence, suspicion evidence, shared risk, boundaries, and next relationship-changing behaviour.
7. **World/rules ledger**: realistic constraints or speculative triggers, limits, costs, exceptions, social consequences, and the fictional setting/place-name policy.
8. **Secret and timeline ledger**: truth, knowledge distribution, clues, fair misreading, payoff window, dates, locations, duration, and state changes.
9. **Ending contract and payoff ledger**: finale window, main conflict resolution, protagonist final choice and state, antagonist consequence, emotional afterglow, stable payoff ids, target chapters, evidence requirements, and explicitly declared intentional open threads.
10. **Structure**: acts or volumes, goals, midpoint shifts, irreversible choices, stage payoffs, and carry-forward consequences. Reserve the final 3-5 chapters for the ending contract.
11. **Reader-facing story lead**: required placement before chapter one, output language, localized heading, language-adjusted length, core conflict, identification trigger, emotional target, earned satisfaction promise, unanswered question, spoiler boundary, and exact chapter-one marker protocol.
12. **Retention-first opening and first three chapters**: when selected, the opening contract, first-screen signals, conflict braid, atmosphere pressure, identification anchor, chapter value map, and irreversible threshold; otherwise retain the normal opening collision and micro-arc requirements.
13. **First paid chapter** when relevant: direct consequence of the free-window choice and immediate substantive delivery.
14. **Chapter contract**: objective -> obstacle -> action -> feedback -> choice -> cost -> payoff -> irreversible change -> next pressure.
15. **Pacing and rotation**: vary conflict, emotional result, setting, strategy, payoff, and hook type.
16. **Realism and originality boundaries**: make professional, legal, technical, cultural, and causal assumptions explicit.
17. **Agent output protocol**: exact state blocks and handoff expectations for each Novel OS agent.
18. **Quality gates**: continuity, knowledge boundaries, payoff, agency, timeline, resource, style, and ending checks.
19. **Assumptions**: only details the user did not decide.
20. **Final delivery**: story bible, market research and source ledger, machine-readable `workshop_trace` for intake, questions, alternatives, and Section A-E decisions, outline, complete chapters, reports, `book_completion_report.json`, and reader-facing manuscript.

## Required story lead contract

Every generated Prompt includes this contract, regardless of retention profile:

```yaml
story_lead_contract:
  required: true
  placement: before_chapter_1
  language: <output language>
  reader_heading: <localized short label, such as 序 or Story Lead>
  length:
    unit: characters|words
    target_range: [<minimum>, <maximum>]
  core_conflict: <concrete conflict or injustice>
  identification_trigger: <desire, loss, fear, humiliation, or boundary>
  emotional_target: resonance|anger|anticipation|mixed
  satisfaction_promise: <earned payoff promised to the reader>
  unanswered_question: <specific question that leads into chapter one>
  spoiler_limit: <ending or major payoff mechanism kept unrevealed>
  manuscript_marker: "## STORY_LEAD: <reader_heading>"
```

Use `180-260` characters for Chinese and `120-180` words for English by
default. For other languages, set an explicit range based on language density,
platform layout, and market register. The lead must contain a concrete conflict,
a recognizable identification trigger, a relationship/status/power contrast,
and an earned counteraction or reversal promise. End with a specific open
question, preserve the full ending, and do not copy chapter one's opening prose.

Chapter one's manuscript artifact uses this exact boundary:

```markdown
## STORY_LEAD: <localized reader heading>

<story lead>

# <localized chapter-one heading and title>

<chapter-one prose with an independent opening hook>
```

Keep `STORY_LEAD:` in ASCII. The compiler removes it from reader-facing output,
renders only the localized heading, and keeps the real chapter-one heading in
Markdown, EPUB, HTML, PDF, and DOCX navigation and reading order.

## Retention-first opening contract

Include this contract when the user requests commercial web fiction, stronger
retention, a satisfying or爽文 experience, a stronger opening, the first three
chapters, or a paid-reading bridge. The contract is language-neutral and must
carry the selected output language, market, genre, and platform assumptions.

```yaml
retention_profile:
  mode: retention_first
  language: <output language>
  market_scope: <country, region, and release markets>
  target_audience: <user-confirmed primary reader segment, age/life stage, and reading motivation>
  setting_mode: fictionalized
  reader_promise: <repeatable reader experience>
  primary_satisfaction: <competence, revenge, romance, mystery, power, belonging, or other>
  opening_window:
    unit: characters|words|sentences|platform_screen
    target: <language- and platform-adjusted range>
opening_contract:
  opening_event: <concrete event already in progress>
  opening_stakes: <immediate loss, opportunity, or risk>
  opening_question: <specific question that guides the first chapter>
  protagonist_immediate_choice: <choice with meaningful costs>
  first_screen_signals: [event, loss, choice]
  chapter_1_value: <local reward, reveal, counteraction, or meaningful change>
  chapter_2_reversal_or_resource: <feedback, evidence, resource, or relationship truth>
  chapter_3_irreversible_step: <visible state change and its cost>
  conflict_braid: [external, relationship, internal]
  satisfaction_loop: <pressure -> recognition -> move -> response -> consequence -> higher goal>
  atmosphere_pressure: <how setting and sensory detail affect choice or risk>
  identification_anchor: <specific desire, fear, habit, object, or boundary>
  paid_bridge: <direct consequence shown at the first paid chapter opening>
```

`first_screen_signals` records the signals actually present, not a list of
aspirations. Select one primary conflict and at least one supporting dimension;
each dimension must alter the protagonist's available choices. Keep Chinese,
English, and other market branches separate when idiom, social context, or
register changes the reader promise.

## First-three-chapter contract

The opening micro-arc must show the premise in action, the cost of doing nothing, the protagonist's competence and vulnerability, a first self-directed action, one local payoff, and a concrete next target. Under `retention_first`, chapter 1 delivers a local reward or reveal, chapter 2 shows opposing feedback and an earned resource or relationship truth, and chapter 3 answers a short-term question before a costly irreversible step. Chapter three must complete a visible state change before it creates the next pressure. The first paid chapter opens on that direct consequence and delivers substantive progress before widening the story.

## Audience research record

The Architect must preserve one record per target market in the foundation and
repeat the relevant branch in the prompt context. Use this shape so research
can be rendered in a project detail view without re-running the web search:

```yaml
audience_research:
  research_status: complete|partial|pending
  target_audience:
    primary_reader_segment: <user-confirmed segment>
    age_or_life_stage: <user-confirmed range or stage>
    reading_motivation: <genre expectation, emotional need, or satisfaction sought>
    gender_platform_or_purchase_context: <user-confirmed value or not material>
  market_scope:
    country: <country>
    region: <region or cultural area>
    language: <language and register>
    release_scope: <single market or named markets>
  audience_age: <range or segment>
  platform_signals: []
  genre_signals: []
  cultural_context: []
  emotional_drivers: []
  source_records:
    - id: src_01
      title: <source title>
      publisher: <publisher>
      url_or_id: <URL or publication id>
      published_at: <date or unknown>
      accessed_at: <date>
      market: <country/region>
      population: <sample or scope>
      finding: <directly supported finding>
      evidence_type: direct_data|reported_observation|creative_inference
      confidence: high|medium|low
  creative_implications:
    - implication: <market-specific writing or packaging decision>
      source_ids: [src_01]
      localization_risk: <risk or none>
```

Each creative implication cites the source ids that caused it. Missing or
conflicting public evidence is recorded as `partial` or `pending` with an
explicit assumption and a list of queries for later retrieval.

## Fictional setting and place-name policy

Every generated Prompt must carry this setting policy unless the project has an
explicit system-level requirement that supersedes it:

```yaml
setting_policy:
  mode: fictionalized
  story_place_names: invented_or_abstract
  real_place_names_in_story: false
  market_metadata_may_name_real_places: true
```

Use invented names for cities, districts, towns, institutions, landmarks, and
neighbourhoods in the story-facing setting, outline, chapter files, and
manuscript. Abstract labels such as `the northern port`, `the capital district`,
or `a coastal university town` are valid when a proper name adds no narrative
value. Audience research may retain the real country, region, platform, or
source location needed to explain market evidence; keep that metadata separate
from the fictional world ledger.

## Workshop decision trace

Persist the reasoning trail alongside the audience research. Update it after
each answer and section confirmation so downstream agents and a project detail
view can distinguish selected decisions from rejected options:

```yaml
workshop_trace:
  intake: <normalized user intent and explicit constraints>
  audience_decision: <user-confirmed primary segment, age/life stage, and reading motivation>
  market_decision: <country/region/language/release scope and date>
  research_queries: []
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
  evidence_links: [src_01]
```

Keep rejected approaches and their tradeoffs as decision history. The final
reader-facing Markdown excludes this trace.

## Ending contract requirements

The Architect must emit a machine-readable `ending_contract` in the story
foundation. It must include `finale_window`, `main_conflict`,
`character_arcs`, `plot_payoffs`, `antagonist_outcome`, and
`emotional_contract`. Every required payoff has a stable id, setup ids, a
deadline, and a concrete evidence description. The last chapter must emit
`Ending_Evidence` for both irreversible change and emotional payoff.
Each `character_arcs` entry must keep narrative lifecycle and story meaning
separate: `required_arc_stage` is one of `beginning`, `middle`, `climax`, or
`resolution`; `required_outcome` is the story-specific semantic result. Both
must be supported by observable evidence. Existing prompts that use
`required_end_state` remain readable, but new prompts must use the two explicit
fields. `outcome_match_mode` is optional and defaults to `auto`: short ASCII
outcome identifiers remain exact while natural-language outcomes tolerate
formatting and appended detail. Use `exact` to enforce a complete value,
`normalized` for formatting-only differences, `contains` for an intentionally
richer actual state, and `required_outcome_aliases` for explicitly approved
paraphrases. Stable outcome identifiers are preferred over long prose values.

## Agent handoff blocks

The prompt may request structured blocks such as:

```text
[SCRIBE_STATE_UPDATE]
Characters_Present: ...
Key_Events: ...
Emotional_Shifts: ...
New_Information_Revealed: ...
Foreshadowing_Planted: ...
Payoff_Events: <payoff_id> | status=<recalled|paid|intentional_open> | evidence=<observable change> | chapter=<number>
Arc_State_Updates: <character_id> | stage=<beginning|middle|climax|resolution> | progress=<0-100> | outcome=<canonical outcome value from ending_contract> | evidence=<choice or observable state>
Personality_State_Updates: <character_id> | trait=<性格特征> | pressure_response=<压力下反应> | evidence=<具体行为>
Ending_Evidence: irreversible_change=<observable final state>; emotional_payoff=<reader-facing closure>
[/SCRIBE_STATE_UPDATE]
```

Keep those blocks, the `story_lead_contract`, the market-scoped
`audience_research`, the `workshop_trace`,
and the confirmed Section A-E decisions in working artifacts and reports. The
final reader-facing Markdown contains only the title, localized story-lead
heading and prose, chapter headings, and chapter prose.
