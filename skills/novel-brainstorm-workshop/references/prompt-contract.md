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
4. **Character ledger**: public identity, desire, need, capability, limitation, fear, boundary, secret, resources, knowledge, and pressure response.
5. **Relationship ledger**: power, leverage, trust evidence, suspicion evidence, shared risk, boundaries, and next relationship-changing behaviour.
6. **World/rules ledger**: realistic constraints or speculative triggers, limits, costs, exceptions, and social consequences.
7. **Secret and timeline ledger**: truth, knowledge distribution, clues, fair misreading, payoff window, dates, locations, duration, and state changes.
8. **Structure**: acts or volumes, goals, midpoint shifts, irreversible choices, stage payoffs, and carry-forward consequences.
9. **First three chapters**: opening collision, emotional investment, protagonist capability, local payoff, costly choice, and next pressure.
10. **First paid chapter** when relevant: direct consequence of the free-window choice and immediate substantive delivery.
11. **Chapter contract**: objective -> obstacle -> action -> feedback -> choice -> cost -> payoff -> irreversible change -> next pressure.
12. **Pacing and rotation**: vary conflict, emotional result, setting, strategy, payoff, and hook type.
13. **Realism and originality boundaries**: make professional, legal, technical, cultural, and causal assumptions explicit.
14. **Agent output protocol**: exact state blocks and handoff expectations for each Novel OS agent.
15. **Quality gates**: continuity, knowledge boundaries, payoff, agency, timeline, resource, and style checks.
16. **Assumptions**: only details the user did not decide.
17. **Final delivery**: story bible, ledgers, outline, complete chapters, reports, and reader-facing manuscript.

## First-three-chapter contract

The opening micro-arc must show the premise in action, the cost of doing nothing, the protagonist's competence and vulnerability, a first self-directed action, one local payoff, and a concrete next target. Chapter three must complete a visible state change before it creates the next pressure. The first paid chapter opens on that direct consequence and delivers substantive progress before widening the story.

## Agent handoff blocks

The prompt may request structured blocks such as:

```text
[SCRIBE_STATE_UPDATE]
Characters_Present: ...
Key_Events: ...
Emotional_Shifts: ...
New_Information_Revealed: ...
Foreshadowing_Planted: ...
[/SCRIBE_STATE_UPDATE]
```

Keep those blocks in working artifacts and reports. The final reader-facing Markdown contains only the title, chapter headings, and prose.

