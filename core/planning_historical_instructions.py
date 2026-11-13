"""Staged planner instructions shipped before per-story policy pins.

Frozen from 098f770 and 041ba18. These strings address existing, unpinned
checkpoints and recover the exact inputs used for their outstanding requests.
Add a version for a historical release instead of editing a shipped snapshot.
"""

from functools import lru_cache

from core.planning_instruction_policy import Instructions

_GLOBAL_INTRO = """Convert the COMPLETE adopted skeleton into a global execution design once. Preserve
all author-confirmed facts, relationships, reveals, chronology, chapter assignments,
scene/emotional intentions, ending choices and caveats. Fill only implementation
gaps; record genuine missing information as explicit assumptions. Do not reinvent
the book. Return one JSON object with:
title, premise, logline, thematic_argument, themes[], setting{time_period,
primary_location,world_rules[]}, characters[{id,name,role,gender,pronouns,aliases,
age,physical_description,internal_desire,external_goal,fear,weakness,strength,
secret,arc}], relationships[], plot_threads[{id,name,description,type,priority,
resolution_chapter}], causal_chain[], act_beat_map[], style{tone,pov,tense,prose_style},
assumptions[], structural_risks[], source_anchors[{id,quote:exact source quotation}]"""

_GLOBAL_MAP = """,
chapter_map[{number,purpose,incoming_consequence,outgoing_commitment,
source_anchor_ids[],reserved_reveals,ending_obligations}]. Map every requested chapter
exactly once. Anchors must retain important qualifiers; source text is authoritative.
"""

_GLOBAL_CONTRACTS = """Do not draft complete chapter plans yet. Global design may retain additional fields
needed to preserve the adopted story. Do not return a second Markdown version.
Do not echo narrative_format, commercial_story_contract/id or classification:
code binds these immutable, already-approved contracts locally. Honor their content.
Include volume_contracts with distinct titles/conflicts/promises/protagonist shifts,
climax/payoff/carryover as in the supplied template; replace all placeholders.
Include ending_contract {schema_version:1,enforce:true,finale_window:{start_chapter,
end_chapter},main_conflict:{thread_id,required_status,protagonist_choice,consequence},
character_arcs:[{character_id,required_arc_stage,required_outcome,required_choice,
outcome_match_mode:"auto",required_outcome_aliases:[]}],plot_payoffs:[{id,setup_ids,
required_payoff,deadline,allow_intentional_open:false}],antagonist_outcome:{required},
emotional_contract:{reader_emotion,afterglow_state}}. Final chapter ends the finale
window. Use stable source setup IDs and observable outcomes. Empty payoffs only if
there are no tracked long-range promises. Do not mandate an unsupported closure or
change a deliberately unresolved source outcome; document deliberate open threads.
"""

_SEED_DIRECTIONS = """.
In this stage save the global story decisions only. Identify plot-wide causes, irreversible turns, delayed reveals and ending obligations across the complete source. Preserve every author-confirmed exception and its scope. The chapter_map and chapters are separate deliverables in later stages; omit both keys from this object.
"""

_LIFECYCLE_ADDENDUM = """For required_arc_stage use only beginning, middle, climax, or resolution. Never
use completed as a lifecycle stage; put the semantic result in required_outcome.
"""

_STATUS_RULES = """
required_status is a lifecycle enum: active, resolved, abandoned, foreshadowed.
Put story-specific meaning and remaining limits in protagonist_choice/consequence;
never invent a status such as resolved_with_bounded_autonomy.
"""

_BATCH_PROMPT = """STAGE: chapter_batch
Convert and complete ONLY requested_chapters against the full original skeleton and
the same frozen global design. Follow the source chapter plan, preserve rich emotional
and scene design, and add specificity only where needed. Earlier completed chapters
are immutable; maintain their outgoing consequences, reserve later revelations and
deliver this batch's promised payoffs. Do not rewrite global facts or customer choices.
Return {chapters:[{number,title,pov,summary,target_words,goal,conflict,
incoming_consequence,active_choice,emotional_cause,emotional_change,local_payoff,
ending_hook,continuity_obligations:[],setups:[],payoffs:[],reserved_later_payoffs:[],
source_anchor_ids:[],scenes:[{pov,goal,conflict,change,setting,emotional_progression,
choice_and_cost,subtext,continuity}]}]}. Use meaningful complete prose in design fields,
not emotion labels. Preserve mixed feelings, hesitations, attachments and costs.
Scaffold the protagonist's event->interpretation->emotion->choice->aftermath chain.
Use established IDs and exact source anchors; explicitly empty arrays when none apply.
Add other fields to retain source detail rather than discard it. Do not output chapter
prose, Markdown, the whole book, a replacement global design or repeated contracts.
"""

_PROJECTION_RULES = """
If adopted_source_projection is supplied, its coverage explicitly identifies omitted
distant chapter plans. Every global section and requested/adjacent source plan remains
verbatim. Preserve the frozen global roadmap's future commitments; do not pretend to
have reread omitted chapters. Whole-book review uses the complete original source.
"""

_EXECUTION_CONTRACT_RULES = """
For each chapter also include execution_contract with schema_version:1 and chapter
matching number, and nonempty strings goal,obstacle,active_choice,cost,
irreversible_change,local_payoff,ending_pressure; preserve_facts and allowed_knowledge
are string lists. Derive these from the SAME saved chapter design, never a competing
new plot. In a quiet or non-protagonist chapter describe its actual response/meaning
change instead of inventing a protagonist decision or irreversible external event.
Only include POV knowledge already established by this point. The application
projects this contract directly; no extra per-chapter planning model is called.
"""

_WINDOW_RULES = """
This is a bounded review window, NOT a claim to have read the complete candidate.
All global candidate fields and the full chapter roadmap are present. Original
global source sections are present; source/candidate chapter coverage is explicit.
Check every requested chapter against its complete source plan and the adjacent
candidate chapters, including boundary transitions, future obligations and global
causal/relationship rules. Do not treat omitted distant chapter bodies as missing
from the book or as inspected prose. Review each required dimension within this
window; report material global contradictions as scope=global. Chapter issues must
name a supplied candidate chapter. The application combines ALL windows before
accepting coverage; this reply cannot approve the whole book by itself.
"""

_BLUEPRINT_CRAFT = """## Serial Continuity and Emotional Design

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

_LEGACY_REVIEW_PROMPT = """STAGE: semantic_review
Review this exact whole-book candidate against the complete adopted source.
Do not redesign author decisions or demand literal prose not needed by a blueprint.
Check full source design preservation, character identity/relationships, causal and
emotional continuity across batch boundaries, scene specificity, active choices,
opening rewards, delayed reveals, every ending payoff, classification and approved
commercial promises. Flag material omissions or contradictions. Source-backed global
problems must have scope=global; never conceal them in a chapter repair.
Return {status:passed|blocked,coverage:{each required check:true},issues:[{scope:chapter|global,
chapter:integer when applicable,message:specific correction,source_evidence:source
quotation or exact contract reference}]}. coverage means the check was performed,
not that it passed. A passed review has no issues.
"""

_REVIEW_PROVENANCE = """Separate facts explicitly stated in adopted author text from the generated
candidate, including its assumptions. A candidate's inference is never author evidence.
An allegation or official account need not establish what happened. For each issue add
finding_basis: source_departure|candidate_assumption|possible_author_conflict|contract.
For source_departure use a verbatim author passage as source_evidence; explain the
candidate error separately in message. An author conflict requires two distinct,
explicit incompatible author statements about the same circumstances. Cite both; do
not ask the author based on an assumption, silence, or your preferred interpretation.
"""


@lru_cache(maxsize=4)
def historical_instructions(*, scoped: bool, story_first: bool) -> tuple[Instructions, Instructions]:
    """Return the pre-041ba18 and 041ba18 families in release order."""
    batch = _BATCH_PROMPT
    if scoped:
        batch = batch.replace('full original skeleton', 'supplied original skeleton') + _PROJECTION_RULES
    if story_first:
        batch += _EXECUTION_CONTRACT_RULES

    def released(contracts: str, vocabulary: str) -> Instructions:
        tail = contracts + (_STATUS_RULES if scoped else '')
        return Instructions(
            global_prompt='STAGE: global_design\n' + _GLOBAL_INTRO + _GLOBAL_MAP + tail,
            global_seed_prompt='STAGE: global_design_seed\n' + _GLOBAL_INTRO + _SEED_DIRECTIONS + tail,
            batch_prompt=batch, projection_rules=_PROJECTION_RULES, blueprint_craft=_BLUEPRINT_CRAFT,
            legacy_review_prompt=_LEGACY_REVIEW_PROMPT, review_prompt=_LEGACY_REVIEW_PROMPT + _REVIEW_PROVENANCE,
            review_window_rules=_WINDOW_RULES, arc_vocabulary=vocabulary,
        )

    return released(_GLOBAL_CONTRACTS, 'historical'), released(_GLOBAL_CONTRACTS + _LIFECYCLE_ADDENDUM, 'lifecycle')
