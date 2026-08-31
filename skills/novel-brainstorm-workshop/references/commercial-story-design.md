# Commercial Story Design

Use this reference for commercial, retention-first, domestic, relationship,
midlife, revenge, professional-erasure, grief, romantic-repair, suspense, or
speculative fiction. It is a local design prior, not a source-retrieval system.

## Reader profile

The default profile applies only after the user confirms it: women ages 35-60
who seek recognition, anger, pity, regret, agency, belonging, and hope. A
different user-confirmed audience replaces this default. Do not infer an
audience from market frequency.

The local profile was distilled from aggregate analysis of a corpus snapshot
identified by SHA-256
`a5458ce1332e5b74c52889e4a5aed5b9809f69e6e1a8f09cde25c5ab974ee49f`.
Runtime prompts receive this mechanism reference and the approved contract,
never corpus files, sample prose, titles, names, paths, or nearest matches.

## Reader jobs

Give every selected job an observable story delivery:

- `recognition`: name the protagonist's concrete labor, boundary, or loss.
- `anger`: make a chosen resource injustice visible rather than adding abuse.
- `pity`: show an irrecoverable cost and what the protagonist still protects.
- `regret`: reveal a credible life that could have existed.
- `agency`: let the protagonist deliberately change resources or rules.
- `belonging`: establish respect through self, child, work, friend, community,
  family of choice, or a tested romantic relationship.
- `hope`: show the new life beginning through action and durable conditions.

At least three jobs have nonzero priority. Belonging uses at least two anchors
across the book and cannot rely only on a replacement romantic partner.

## Premise engine

Build the premise from nine variables:

1. protagonist life stage;
2. invisible labor;
3. sacred asset;
4. boundary transfer expressed as a resource dimension;
5. visible proof;
6. deadline;
7. protagonist agency source;
8. action cost;
9. new belonging.

Also record the beneficiary role and relationship shape. The protagonist needs
a desire beyond escape. Agency must be seeded by chapter 2 and a major turn
must be caused by the protagonist rather than an unseeded rescuer.

Resource dimensions are `time`, `space`, `name`, `labor`, `body`, `money`,
`memory`, `relationship`, `system`, and `future`. A conflict escalates only
when it changes a resource, rule, relationship, identity, goal, or available
choice.

## Conflict ladder

Use exactly five causal levels:

1. a small but meaningful deviation;
2. the opponent converts the protagonist's kindness into assumed permission;
3. proof establishes a pattern;
4. the protagonist tests a concrete boundary and receives adaptive resistance;
5. the protagonist makes an irreversible choice and accepts a visible cost.

Every level records `resource_dimension`, `protagonist_action`, and
`observable_consequence`. Bind external, relationship, and internal pressure
to the same event where possible. Repeated humiliation without changed state
does not count as escalation.

## Sustainable satisfaction

Use one of `boundary`, `evidence`, `competence`, `identity`, `relationship`,
or `consequence`. `none` is valid only for an intentionally connective
chapter. Seed every capability or resource before or in the chapter that uses
it. Rotate satisfaction and hook mechanics.

The quality budgets are fixed:

```json
{
  "consecutive_humiliation_scenes_max": 2,
  "identical_hook_type_max": 1,
  "unseeded_rescue_max": 0,
  "child_voice_age_check": true,
  "institutional_plausibility_check": true,
  "protagonist_causes_major_turn": true
}
```

## Free-trial micro-arc

Use three or four chapters:

- Chapter 1 recognizes the final boundary violation and its concrete loss.
- Chapter 2 verifies the pattern and tests a boundary.
- In a three-chapter arc, chapter 3 delivers a local payoff, irreversible
  choice, visible cost, and concrete next expectation.
- In a four-chapter arc, chapter 3 performs the first strategic recovery and
  local payoff; chapter 4 crosses the irreversible threshold, shows the cost,
  and names the next expectation.

The free window completes a local arc before opening a larger problem. An
incoming call, door opening, or stranger arrival is a hook only when it carries
a consequence caused or prepared by prior action.

## Genre execution

Use the shared engine for all genres, then adapt its evidence and pressure:

- domestic ethics: labor, family space, children, money, and permission;
- motherhood: child safety and age-credible behavior expose adult costs;
- professional erasure: version history, records, work process, and authorship;
- departure or revenge: withdrawal of real support creates fair consequences;
- illness or grief: care work, time, choice, and shared history remain concrete;
- romantic repair: reliability, shared tasks, and respect for boundaries;
- suspense or crime: each clue narrows explanations and changes action;
- speculative fantasy: rules alter choices and carry visible costs.

## Approved prompt contract

After Sections A-E are confirmed, emit exactly one JSON object between these
literal boundaries:

```text
[COMMERCIAL_STORY_JSON]
{
  "schema_version": 1,
  "reader_contract": {
    "audience_age_band": "<user-confirmed band>",
    "life_contexts": ["<one to five contexts>"],
    "emotional_jobs": {
      "recognition": 0,
      "anger": 0,
      "pity": 0,
      "regret": 0,
      "belonging": 0,
      "agency": 0,
      "hope": 0
    }
  },
  "premise_engine": {
    "protagonist_life_stage": "<controlled value>",
    "protagonist_desire_beyond_escape": "<specific desire>",
    "invisible_labor": "<controlled value>",
    "sacred_asset": "<controlled value>",
    "boundary_transfer": "<resource dimension>",
    "beneficiary_role": "<controlled value>",
    "proof_type": "<controlled value>",
    "deadline_type": "<controlled value>",
    "agency_source": "<controlled value>",
    "agency_seeded_in_chapter": 1,
    "action_cost": "<controlled value>",
    "belonging_anchors": ["<at least two>"],
    "relationship_shape": "<controlled value>"
  },
  "conflict_ladder": [
    {
      "level": 1,
      "resource_dimension": "<controlled value>",
      "protagonist_action": "<observable action>",
      "observable_consequence": "<changed state>"
    }
  ],
  "free_trial_arc": {
    "chapter_count": 3,
    "recognition_event": "<event>",
    "pattern_proof": "<proof>",
    "first_boundary_test": "<test>",
    "local_payoff": "<payoff>",
    "irreversible_choice": "<choice>",
    "visible_cost": "<cost>",
    "next_concrete_expectation": "<next target>",
    "action_sequence": ["recognize", "verify", "test_boundary", "accept_cost"]
  },
  "quality_budgets": {
    "consecutive_humiliation_scenes_max": 2,
    "identical_hook_type_max": 1,
    "unseeded_rescue_max": 0,
    "child_voice_age_check": true,
    "institutional_plausibility_check": true,
    "protagonist_causes_major_turn": true
  }
}
[/COMMERCIAL_STORY_JSON]
```

The real block contains five conflict steps with levels 1 through 5. Do not
author `contract_id`; Prompt Intake computes it from canonical JSON. Once the
user approves the block, later agents may expand scenes and characters but may
not mutate its values.
