"""Provisional continuation design, separate from observations of the source."""

from typing import Annotated

from pydantic import Field

from .creative_types import StrictModel

Line = Annotated[str, Field(min_length=1, max_length=2400)]


class ContinuationCard(StrictModel):
    title: Line
    pov: Line
    source_anchor: Line
    core_plot: Annotated[list[Line], Field(min_length=2, max_length=5)]
    reader_payoff: Line
    foreshadowing: Line
    ending_hook: Line


class ContinuationPlan(StrictModel):
    premise: Line
    overall_arc: Line
    proposed_ending: Line
    cards: Annotated[list[ContinuationCard], Field(min_length=3, max_length=6)]
    major_choices: Annotated[list[Line], Field(max_length=3)]
    source_conflicts: Annotated[list[Line], Field(max_length=10)]


PLANNING_RULES = """
When continuation_plan is requested, act as a commercial fiction story designer.
First finish the factual reading, then propose a vivid continuation from its known
endpoint. The plan is invented NEW material awaiting author adoption, never an
assertion about the unseen original. Keep it outside the factual analysis fields.
Use the Synopsis's emotional promise and established desires, alliances, secrets
and unfinished choices. Create an overall arc with escalation, earned reversals,
emotional rewards and a proposed ending. Give 3–6 substantial upcoming story cards
in the style: narrative viewpoint / concrete core plot / reader payoff /
foreshadowing and payoff / ending hook. Each card contains 2–5 specific events:
who acts against whom, what is revealed or chosen, and how it changes the next
situation. Anchor each card in an actual source detail (chapter/path or observed
unresolved thread), then extend it imaginatively. Do not substitute abstract
advice or endless questions for a story. Cards are dramatic units, NOT a mandatory
chapter count or a ten-chapter template. Leave only 1–3 major route choices when
needed. A proposed secret may reinterpret an open detail but cannot contradict
established facts or silently change the protagonist/central relationship.
For anthologies keep each story separate; identify which unit is continued.
Female-oriented emotional rewards, agency and emotional self-command guide new
designs without erasing the original cast's identity. All NEW characters are 18+;
preserve ages of existing source characters. Romantic/sexual tension involves
adults only, may be intense through power play, testing and calculation, never
explicit. A minor's presence is not permission to age them up or sexualize them.
Report source conflicts briefly and provide a workable direction within these
bounds. Do not copy source paragraphs. Author's future choices override proposals.
"""
