# Retention-First Opening

Use this reference when the request mentions commercial web fiction, stronger
retention, a high-impact opening, a satisfying or爽文 reading experience, the
first three chapters, a free-reading window, or paid conversion. It applies to
Chinese, English, and other languages. The profile raises the chance of
continued reading by making the promise, pressure, agency, and short-cycle
payoff clear; it does not predict platform conversion without release data or
controlled comparison.

## Retention profile

Record the selected profile in the workshop artifact and generated Prompt:

Use quoted-key JSON for these nested contracts. An indented YAML `language:`
line is still treated as top-level metadata by the current Prompt Intake.

```json
{
  "retention_profile": {
    "mode": "retention_first",
    "language": "<output language>",
    "market_scope": "<country, region, and release markets>",
    "target_audience": "<user-confirmed primary segment, life stage, and reading motivation>",
    "setting_mode": "<author-approved real, fictionalized, or mixed setting>",
    "reader_promise": "<repeatable reader experience>",
    "primary_satisfaction": "<competence, revenge, romance, mystery, power, belonging, or other>",
    "opening_window": {
      "unit": "<characters, words, sentences, or platform_screen>",
      "target": "<language- and platform-adjusted suggestion>"
    },
    "free_window": "<chapters or milestone definition, or not applicable>"
  }
}
```

`retention_first` is a design priority, not a fixed voice. Preserve the chosen
genre, POV, register, and cultural setting while making the first meaningful
choice arrive early.

## Reader-facing story lead

Every novel carries a short story lead before chapter one, including projects
that use `balanced` or `literary_first`. The lead is reader-facing packaging
prose: it compresses the story's core conflict, identification trigger, and
earned satisfaction promise into a reason to begin chapter one. It is not an
author note, a neutral synopsis, a substitute for chapter one's opening scene,
or a guarantee of commercial conversion.

Use this contract in the approved design and generated Prompt:

```json
{
  "story_lead_contract": {
    "required": true,
    "placement": "before_chapter_1",
    "language": "<output language>",
    "reader_heading": "<localized short label, such as 序 or Story Lead>",
    "length": {
      "unit": "words",
      "target_range": [
        120,
        180
      ]
    },
    "core_conflict": "<specific conflict, injustice, threat, or impossible demand>",
    "identification_trigger": "<immediate desire, loss, fear, humiliation, or boundary>",
    "emotional_target": "<resonance, anger, anticipation, or mixed>",
    "satisfaction_promise": "<earned payoff promised to the reader>",
    "unanswered_question": "<specific question leading into chapter one>",
    "spoiler_limit": "<ending or major payoff mechanism kept unrevealed>",
    "manuscript_marker": "## STORY_LEAD: <reader_heading>"
  }
}
```

Default to `180-260` characters for Chinese and `120-180` words for English.
The JSON example illustrates the English unit and suggestion; adapt both to
the approved output language.
For another language, record a comparable range based on language density,
platform layout, and the target market's reading pattern. These are target
ranges for editorial guidance, not exact-length gates or performance claims.
Allow natural variation and preserve an explicit user choice over these defaults.

Build the lead in this order:

1. Open on a concrete conflict, unfair demand, betrayal, threat, or loss.
2. Show why it strikes this protagonist's desire, fear, dignity, or boundary.
3. Reveal a relationship, identity, status, or power contrast that intensifies it.
4. Promise a specific form of earned agency: evidence, competence, leverage,
   counterattack, relationship reversal, visible consequence, or rise.
5. End on one unanswered question while withholding the full outcome and its
   decisive mechanism.

Anger comes from recognizable unfairness and consequence, not stacked insults.
Satisfaction comes from the protagonist's choices, preparation, competence,
evidence, or changing leverage, not promotional adjectives. The lead may use
one or two short paragraphs, but it must not copy chapter one's opening lines.
Chapter one starts afterward with its own concrete event and causal hook.

Use this exact chapter-one boundary so the compiler can distinguish the lead
from navigation. Keep `STORY_LEAD:` in ASCII and localize only the visible label:

```markdown
## STORY_LEAD: <reader_heading>

<reader-facing lead>

# <localized chapter-one heading and title>

<chapter-one prose>
```

## Opening contract

Before drafting, emit one stable contract. Keep explanations and market
evidence outside the canonical fields so later agents can compare revisions.

```json
{
  "retention_profile": "retention_first",
  "target_audience": "...",
  "reader_promise": "...",
  "opening_event": "...",
  "opening_stakes": "...",
  "opening_question": "...",
  "protagonist_immediate_choice": "...",
  "first_screen_signals": ["event", "loss", "choice"],
  "chapter_1_value": "...",
  "chapter_2_reversal_or_resource": "...",
  "chapter_3_irreversible_step": "...",
  "conflict_braid": ["external", "relationship", "internal"],
  "satisfaction_loop": "pressure -> recognition -> move -> response -> consequence -> higher goal",
  "atmosphere_pressure": "...",
  "identification_anchor": "...",
  "paid_bridge": "..."
}
```

`reader_promise` names the recurring experience, not a sales claim. `paid_bridge`
states the direct consequence that the first paid chapter must show.

## First sentence and first screen

Design from the latest moment that contains a decision, consequence, or active
threat. The opening sentence or short opening passage should contain at least
two of these signals:

- a concrete event already in progress;
- a problem or loss that affects this protagonist now;
- a contradiction, reversal, or status imbalance;
- a visible cost, deadline, or risk;
- a specific unanswered question;
- an imminent choice with meaningful alternatives.

Use the language-appropriate `opening_window` from the profile. As a starting
point, inspect the first 150-300 Chinese characters or 100-180 English words,
then adjust for sentence length, platform layout, and genre. Treat these as
diagnostic windows rather than guarantees.

The first screen earns context through action. Background, weather, abstract
emotion, and biography may appear when they alter the current choice. End the
window with a readable direction: what the protagonist will obtain, prevent,
expose, protect, or risk in this chapter.

## Conflict braid, atmosphere, and identification

Select one primary conflict and braid in two or three supporting dimensions.
Each added dimension must change the protagonist's available choices.

| Dimension | Question | Useful pressure |
| --- | --- | --- |
| External objective | What must happen now? | task, rival, danger, opportunity |
| Relationship | Who needs, blocks, misreads, or mirrors the protagonist? | trust, desire, betrayal, obligation |
| Internal belief | What protective rule makes the choice harder? | pride, guilt, fear, loyalty, identity |
| Social/system | What institution or group assigns power and cost? | family, workplace, law, class, community |
| Time/resource | Why is delay expensive? | deadline, money, access, health, evidence |
| Information | Which fact would change the next action? | secret, false record, competing hypothesis |

Use a causal braid rather than simultaneous noise:

```text
external event
-> concrete loss or opportunity for the protagonist
-> relationship, belief, or system pressure narrows the options
-> protagonist chooses and acts
-> opponent or environment adapts
-> visible consequence changes the next objective
```

Atmosphere is part of the pressure system. Give each meaningful sensory detail
an action job: reveal status, expose a relationship, provide evidence, limit
movement, trigger memory, or make the cost physical. For reader identification,
anchor the scene in a specific desire, fear, habit, object, or boundary that a
reader can recognize even when the culture or genre is unfamiliar. Translate
that anchor through the target market's register and social context, rather than
assuming Chinese emotional shorthand transfers unchanged into English.

## First three chapters

Treat the first three chapters as a complete micro-arc with three separate
value deliveries. The exact chapter length may vary by platform and language.

| Chapter | Causal job | Required reader value | Exit state |
| --- | --- | --- | --- |
| 1 | Collision and first move | The premise becomes an event; the protagonist's capability and vulnerability are visible; one local reward, reveal, or counteraction lands | A specific pressure created by the protagonist's move |
| 2 | Feedback and strategy test | The opposing force responds; a resource, piece of evidence, tactical win, or relationship truth is earned; the cost becomes visible | A weakened assumption and a harder strategy |
| 3 | Micro-arc payoff and threshold | A short-term question is answered; the protagonist makes a costly choice; status, power, relationship, identity, resource, or time changes visibly | An irreversible step and a concrete next objective |

Every chapter should answer three review questions:

1. What value did the reader receive in this chapter?
2. What did the protagonist deliberately change?
3. What new pressure follows from that change?

If a chapter only adds a cliffhanger, add a local answer, win, discovery,
relationship shift, or meaningful loss before the hook.

## Satisfaction loop (爽点循环)

Use a repeatable but varied loop:

```text
imbalance or pressure
-> protagonist identifies the real constraint
-> protagonist investigates, prepares, or makes a move
-> opposition reacts
-> earned advantage or painful cost becomes visible
-> the reader receives a concrete consequence
-> a higher-level goal or risk opens
```

The satisfying beat may be competence, evidence, revenge, status change,
relationship reversal, moral courage, a fair reveal, or survival under a rule.
Seed the capability or resource, show the decision that activates it, and keep
the cost active. Rotate the satisfaction source and the escalation dimension;
repeated insults, humiliation, shouting, or unexplained instant victories do
not count as a durable progression.

## Paid bridge

For a free-reading window, complete the local micro-arc before the boundary:

1. Deliver one old question's answer or a substantive stage payoff.
2. Force a protagonist choice between options with real costs.
3. Show the choice's irreversible consequence before the boundary.
4. State the next target, opponent, deadline, or relationship risk concretely.

The first paid chapter opens on that direct consequence, shows the promised
next action, and supplies the first substantive piece of the new answer before
opening another thread. A boundary built from an arbitrary sentence cut or an
unrelated viewpoint weakens reader trust.

## Review rubric

Mark each item `pass`, `revise`, or `not_applicable`, and cite the outline or
draft evidence:

```yaml
retention_review:
  story_lead_present_before_chapter_1: pass|revise
  story_lead_length_matches_language: pass|revise
  story_lead_conflict_identification_and_payoff: pass|revise
  story_lead_withholds_ending_and_differs_from_chapter_1: pass|revise
  first_sentence_direction: pass|revise
  first_screen_has_two_signals: pass|revise
  protagonist_agency_in_chapter_1: pass|revise
  conflict_braid_has_two_or_more_dimensions: pass|revise
  atmosphere_changes_pressure_or_meaning: pass|revise
  identification_anchor_is_specific: pass|revise
  chapter_1_value_delivered: pass|revise
  chapter_2_feedback_and_resource: pass|revise
  chapter_3_irreversible_threshold: pass|revise
  paid_chapter_direct_consequence: pass|revise|not_applicable
  escalation_dimension_rotates: pass|revise
  cultural_register_matches_market: pass|revise
  evidence: []
```

When an item is `revise`, change the causal design or scene evidence. Do not
fix a weak opening by adding a louder promise while leaving the protagonist
passive or the local payoff absent.

## Anti-patterns and replacements

| Weak pattern | Replacement behaviour |
| --- | --- |
| Long setup before the first decision | Begin at the latest decision-bearing moment and weave context into consequences |
| Abstract hook or generic suffering | Show a concrete event, object, message, task, or choice with an immediate cost |
| One-dimensional conflict | Braid external pressure with a relationship, belief, system, time, or information constraint |
| Repeated humiliation | Change the strategy, power relation, evidence state, or protagonist boundary |
| Endless mystery | Answer one micro-question and sharpen a narrower, riskier one |
| Unseeded cheat or rescue | Establish capability/resource, show activation, and retain a visible cost |
| Cliffhanger without payoff | Complete the local beat, then make the next pressure causally unavoidable |
| Premature paywall | Finish a meaningful stage payoff and connect the next chapter to its consequence |
| Translation of emotional shorthand | Rebuild the identification anchor in the target market's register and social context |
