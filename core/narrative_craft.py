"""Shared serial-fiction craft requirements for planning and manuscript stages."""

BLUEPRINT_CRAFT = """## Serial Continuity and Emotional Design

Plan chapters as one causal chain, not independent episodes. For each chapter,
name the incoming unresolved consequence, its local reader reward, the outgoing
hook caused by a choice or discovery, and the later chapter/arc that will answer
it. Track short-term, arc-level, and book-level promises separately, with setup
and intended payoff chapters. Answer smaller questions while deepening the
larger conflict; do not spend every reveal or relationship reversal up front,
and do not withhold all satisfaction until the finale. Later chapters must have
distinct earned turns, not repeated humiliation, arguments, or filler.

Give chapters 1-3 special attention (or all chapters if the book is shorter):
establish a concrete disturbance and emotional stake early, deliver a meaningful
small payoff, and make each nonfinal ending create a specific reason to read on.
Strong openings must preserve the planned long-range discoveries and climax.
Honor the confirmed free-trial beats and ending contract; a final chapter closes
its promises rather than manufacturing another cliffhanger.

For family betrayal, emotional entanglement, and female or male growth stories,
design the protagonist's inner causal chain: event -> personal interpretation
and threatened need -> mixed/evolving emotions -> choice -> cost and aftermath.
Show why they stay, hesitate, resist, trust, or leave, given concrete attachments,
constraints, fears, hopes, and history. Track how earlier injuries and small
victories change later decisions. Plan scene evidence for major changes; an
outline's emotion labels do not prove emotional resonance in the prose.

Lock each character's stable id, canonical name, approved aliases, gender, and
pronouns in the foundation. Do not infer gender from a name, occupation, or
romantic role. Establish missing details explicitly for newly invented cast;
leave unknown existing-canon details unspecified. Use only established identities
in later chapters and distinguish characters with similar names.
"""


def chapter_craft_guidance(chapter: int, total_chapters: int | None = None) -> str:
    """Keep the same obligations in outline, draft, edit, and review prompts."""
    sections = ["""## Serial Craft Obligations
- Carry forward the previous chapter's actual ending and its emotional residue.
  Address its immediate consequence before unrelated business; a POV/time shift
  must make the causal connection clear. Do not reset relationships or conflict.
- Deliver this chapter's local payoff while preserving arc-level and book-level
  promises for their planned payoff points. New pressure must grow out of an
  established choice, answer, cost, or relationship change. Avoid fake suspense,
  unrelated surprise arrivals, and stretching a spent conflict to fill pages.
- In the POV, dramatize event -> personal interpretation -> mixed/evolving
  emotions -> choice -> cost/aftermath. This is especially important for family
  betrayal, emotional entanglement, and growth: conflicting attachment, anger,
  shame, hope, or fear should explain what the character does and why now.
  Use specific thoughts, bodily response, memory cues, subtext, and behavior.
  Preserve meaningful quiet processing; avoid emotion labels, repetitive
  monologues, and instant personality changes. This chain is an internal craft
  check, not a visible template or a demand that every scene end in a decision.
- Keep canonical names, aliases, gender, and pronouns consistent. Chinese 他/她
  and English pronouns must refer to the established person. Repeat the name
  when an antecedent is ambiguous; do not guess an unknown identity or change
  the character record to excuse a prose mistake.
- During review, cite actual passages for broken carryover, premature payoff,
  unexplained emotional choices, and identity mismatches; propose precise fixes.
  An ambiguous antecedent or missing identity fact is uncertainty, not proof of
  a gender error. Preserve valid emotional depth during edits and final polish.
"""]
    final = total_chapters is not None and chapter == total_chapters
    if chapter <= 3:
        sections.append("""### Opening Chapters: Priority
Give this opening chapter an immediate, concrete story disturbance or its direct
consequence, a clear emotional stake, and a meaningful on-page reader reward
(evidence, agency, competence, a boundary, or a relationship shift). Make the
reward matter to this particular person. Seed larger questions and reserve the
long-range reversals; a strong opening is not a compressed whole-book finale.
Follow assigned free-trial beats without moving future payoffs into this chapter.
""")
    if final:
        sections.append("""### Final Chapter: Closure
Pay the remaining contracted promises and complete the emotional consequences.
The ending contract takes precedence over requests for a next-chapter hook;
use earned closure or authorized series carryover, not an artificial new crisis.
""")
    else:
        sections.append("""### Chapter Exit
Complete the local action or emotional movement, then leave a concrete causal
question, consequence, decision, or relationship expectation for the next chapter.
If this is the final chapter under the ending contract, honor closure instead.
""")
    return "\n".join(sections)
