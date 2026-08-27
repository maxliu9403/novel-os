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

Use actual values in the final artifact. A range may be discussed in the body, but the top-level run needs one fixed chapter count and one total target.

## Required body sections

1. **Core task**: premise, scope, length, originality, and what the final reader sees.
2. **Story contract**: event, relationship, emotional, and meaning promises.
3. **Story engine**: external objective, relationship dilemma, internal misbelief, secret/question, and adaptive pressure.
4. **Audience research and regional adaptation**: market scope for every country or region, language and register, audience segment, platform and genre signals, cultural context, emotional drivers, source records, evidence type, confidence, and creative implications. Keep market branches separate for multi-market releases.
5. **Character ledger**: public identity, desire, need, capability, limitation, fear, boundary, secret, resources, knowledge, pressure response, personality core, visible behaviours, decision style, speech habits, emotional expression, strengths, flaws, change evidence, and personality conflicts or complements.
6. **Relationship ledger**: power, leverage, trust evidence, suspicion evidence, shared risk, boundaries, and next relationship-changing behaviour.
7. **World/rules ledger**: realistic constraints or speculative triggers, limits, costs, exceptions, and social consequences.
8. **Secret and timeline ledger**: truth, knowledge distribution, clues, fair misreading, payoff window, dates, locations, duration, and state changes.
9. **Ending contract and payoff ledger**: finale window, main conflict resolution, protagonist final choice and state, antagonist consequence, emotional afterglow, stable payoff ids, target chapters, evidence requirements, and explicitly declared intentional open threads.
10. **Structure**: acts or volumes, goals, midpoint shifts, irreversible choices, stage payoffs, and carry-forward consequences. Reserve the final 3-5 chapters for the ending contract.
11. **First three chapters**: opening collision, emotional investment, protagonist capability, local payoff, costly choice, and next pressure.
12. **First paid chapter** when relevant: direct consequence of the free-window choice and immediate substantive delivery.
13. **Chapter contract**: objective -> obstacle -> action -> feedback -> choice -> cost -> payoff -> irreversible change -> next pressure.
14. **Pacing and rotation**: vary conflict, emotional result, setting, strategy, payoff, and hook type.
15. **Realism and originality boundaries**: make professional, legal, technical, cultural, and causal assumptions explicit.
16. **Agent output protocol**: exact state blocks and handoff expectations for each Novel OS agent.
17. **Quality gates**: continuity, knowledge boundaries, payoff, agency, timeline, resource, style, and ending checks.
18. **Assumptions**: only details the user did not decide.
19. **Final delivery**: story bible, market research and source ledger, machine-readable `workshop_trace` for intake, questions, alternatives, and Section A-E decisions, outline, complete chapters, reports, `book_completion_report.json`, and reader-facing manuscript.

## First-three-chapter contract

The opening micro-arc must show the premise in action, the cost of doing nothing, the protagonist's competence and vulnerability, a first self-directed action, one local payoff, and a concrete next target. Chapter three must complete a visible state change before it creates the next pressure. The first paid chapter opens on that direct consequence and delivers substantive progress before widening the story.

## Audience research record

The Architect must preserve one record per target market in the foundation and
repeat the relevant branch in the prompt context. Use this shape so research
can be rendered in a project detail view without re-running the web search:

```yaml
audience_research:
  research_status: complete|partial|pending
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

## Workshop decision trace

Persist the reasoning trail alongside the audience research. Update it after
each answer and section confirmation so downstream agents and a project detail
view can distinguish selected decisions from rejected options:

```yaml
workshop_trace:
  intake: <normalized user intent and explicit constraints>
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
fields.

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
Arc_State_Updates: <character_id> | stage=<beginning|middle|climax|resolution> | progress=<0-100> | outcome=<semantic end state> | evidence=<choice or observable state>
Personality_State_Updates: <character_id> | trait=<性格特征> | pressure_response=<压力下反应> | evidence=<具体行为>
Ending_Evidence: irreversible_change=<observable final state>; emotional_payoff=<reader-facing closure>
[/SCRIBE_STATE_UPDATE]
```

Keep those blocks, the market-scoped `audience_research`, the `workshop_trace`,
and the confirmed Section A-E decisions in working artifacts and reports. The
final reader-facing Markdown contains only the title, chapter headings, and
prose.
