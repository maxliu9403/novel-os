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
4. **Audience profile and regional adaptation**: user-confirmed audience segment, life stage, reading motivation, country or cultural region, language and register, release scope, platform context, and approved creative implications.
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
20. **Cover handoff**: strict JSON derived from the approved story, audience, conflict, protagonist, decisive node, secondary task, and fictional world signals.
21. **Final delivery**: story bible, audience profile, machine-readable `workshop_trace` for intake, questions, alternatives, and Section A-E decisions, outline, complete chapters, reports, `book_completion_report.json`, and reader-facing manuscript.

## Required cover handoff

Every generated Prompt includes exactly one JSON object inside these literal boundaries:

````text
COVER_HANDOFF_BEGIN
```json
{
  "schema_version": 2,
  "title": "<exact approved title>",
  "author": "<approved author or empty>",
  "language": "<title and output language>",
  "genre": "<primary and secondary genre>",
  "target_audience": "<user-confirmed primary audience>",
  "market_scope": "<release market branch>",
  "core_task": "<reader-facing premise and protagonist objective>",
  "core_conflict": "<specific opposition and stakes>",
  "emotional_promise": "<dominant emotion and earned payoff>",
  "principal_characters": [{
    "character_id": "<stable story character id>",
    "name": "<confirmed name>",
    "narrative_role": "<protagonist or co-protagonist role>",
    "must_appear": true,
    "age": null,
    "age_band": "<confirmed age phase; use an integer age instead when known>",
    "gender_presentation": "<confirmed presentation or empty>",
    "physical_identity": "<confirmed visible identity only>",
    "occupation_and_status": "<occupation and lived status>",
    "daily_wardrobe": "<credible repeated-use clothing>",
    "lived_environment": "<daily material environment>",
    "current_emotional_state": "<state at the decisive node>",
    "agency_signal": "<visible action or decision>",
    "relationships": ["<related character id>"],
    "source_refs": ["<approved story-contract path>"]
  }],
  "relationship_map": [{
    "from_character_id": "<character id>",
    "to_character_id": "<character id>",
    "relationship": "<confirmed relationship>",
    "power_balance": "<current power balance>",
    "visible_tension": "<visualizable behavior>",
    "shared_risk": "<shared stake or empty>"
  }],
  "lived_environment": {
    "era": "<confirmed era>",
    "fictional_place": "<invented or abstract story place>",
    "primary_spaces": ["<lived story space>"],
    "economic_signals": ["<material reality signal>"],
    "cultural_signals": ["<confirmed routine or object>"],
    "weather_and_season": "<confirmed value or empty>",
    "environment_truths": ["<durable setting fact>"]
  },
  "decisive_story_nodes": [{
    "node_id": "<stable node id>",
    "description": "<major irreversible action suitable for a cover>",
    "evidence_refs": ["character:<id>"]
  }],
  "secondary_signals": [{
    "signal_id": "<stable signal id>",
    "description": "<one person, setting feature, or story object>",
    "story_function": "<supporting pressure or promise>"
  }],
  "genre_emotion_profile": {
    "primary_genre": "<genre>",
    "submode": "<confirmed romance, family-ethics, or neutral submode>",
    "emotional_temperature": "<scene temperature>",
    "desired_viewer_feeling": "<first emotional response>",
    "relationship_motion": "<visible move closer, apart, exclusion, or boundary>",
    "prohibited_shortcuts": ["<genre cliche that would mislead>"]
  },
  "commercial_visual_goal": {
    "market": "<release market branch>",
    "audience_segment": "<confirmed audience>",
    "display_context": "mobile_thumbnail",
    "thumbnail_reference_width": 120,
    "thumbnail_reference_height": 180,
    "first_glance_priority": "<one relationship or decisive action>",
    "reader_identification": "<truthful identification anchor>",
    "truthful_story_promise": "<what this scene honestly promises>"
  },
  "title_direction": {
    "hierarchy": "<title hierarchy>",
    "preferred_zone": "<top, center, or lower third>",
    "readability": "mobile_thumbnail"
  },
  "forbidden_elements": ["real landmarks", "logos", "watermarks", "unsupported spoilers"],
  "visual_assumptions": []
}
```
COVER_HANDOFF_END
````

The contents must be valid JSON after replacing every placeholder. Title,
target audience, conflict, principal-character ages and lived identities,
relationships, decisive nodes, and environment signals come from confirmed
Sections A-E rather than new assumptions. Include every protagonist or
co-protagonist whose arc is part of the reader promise with `must_appear: true`.
Also include a small number of cover-relevant conflict participants with
`must_appear: false` when their presence lets a cover show the cause of the
emotion—such as an opposing alliance, exclusion, divided loyalty, concealed
choice, or public power move. These optional entries use the same confirmed age,
lived-identity, agency, relationship, and evidence fields; they are not invented
extras and are not mandatory in every concept. Prefer enough approved cast for
at least one causal conflict tableau whose foreground shows the emotional cost
and whose middle/background shows the story-supported cause. Keep real
market geography only in `audience_profile`; cover-facing places are fictional
or abstract. This block is production metadata and does not enter reader prose.

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

## Commercial story contract

For commercial or retention fiction, follow
`commercial-story-design.md` and include exactly one
`[COMMERCIAL_STORY_JSON]...[/COMMERCIAL_STORY_JSON]` block. The JSON contains
the approved reader contract, premise engine, five-level conflict ladder,
three- or four-chapter free arc, and fixed quality budgets. Do not author a
contract ID; Prompt Intake computes it. The block must not contain corpus
paths, sample identities, source expression, embeddings, or retrieval results.

## Audience profile

Preserve the user-confirmed audience and market decisions in this compact form:

```yaml
audience_profile:
  primary_reader_segment: <user-confirmed segment>
  age_or_life_stage: <user-confirmed range or stage>
  reading_motivation: <genre expectation, emotional need, or satisfaction sought>
  country_or_cultural_region: <confirmed value or not material>
  language_and_register: <confirmed output language and register>
  release_scope: <single market or named markets>
  platform_context: <confirmed value or not material>
  creative_implications:
    - <approved writing or localization decision>
```

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
value. The audience profile may retain a real country or cultural region; keep
that context separate from the fictional world ledger.

## Workshop decision trace

Persist the reasoning trail alongside the audience profile. Update it after
each answer and section confirmation so downstream agents and a project detail
view can distinguish selected decisions from rejected options:

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

## Commercial agent handoff boundaries

When `commercial_story` is active, keep the runtime inputs separated by role:

- **Scribe** receives the approved story contract, the current chapter contract,
  recent verified and promoted reader-value summaries, and the ranked context
  pack. It never receives raw corpus material, corpus paths, retrieval results,
  or a self-certification field for reader-value delivery.
- **Editor** checks repeated humiliation, passive protagonist turns, unsupported
  rescue, and repeated hook mechanics. It must preserve the contract while
  making each change observable in the scene.
- **Continuity Guardian** checks evidence provenance, child knowledge and voice,
  institutional plausibility, and contract-to-prose delivery. It certifies
  reader-value claims only against the exact candidate artifact.
- **Style Curator** checks character-specific attention, work knowledge, speech
  strategy, shame trigger, body response, and template phrase repetition while
  preserving the novel's established voice.

All roles keep structural labels in their analysis/state blocks rather than in
reader-facing prose. The following expressions are a repetition review, not a
blanket ban: `I did not cry`, `I did not scream`, `my blood ran cold`, `my world
shattered`, `they thought I was weak`, and `the game had just begun`. One
contextually earned use may remain; repeated uses across recent chapters need a
scene-specific replacement or a documented repair finding.

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

Keep those blocks, the `story_lead_contract`, the user-confirmed
`audience_profile`, the `workshop_trace`,
and the confirmed Section A-E decisions in working artifacts and reports. The
final reader-facing Markdown contains only the title, localized story-lead
heading and prose, chapter headings, and chapter prose.
