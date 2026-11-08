"""Genre-independent craft policy, frozen with each new discussion/run.

No inference, mutable profile lookup, publication fields, or literary pass score.
Historical manifests lacking a snapshot deliberately retain their old behavior.
"""
from copy import deepcopy
import hashlib
import json
import sys

# Both CLI and packaged API imports share one policy module.
sys.modules.setdefault('reader_investment' if __name__.startswith('core.') else 'core.reader_investment', sys.modules[__name__])

VERSION = 'reader-investment.v3'
SUPPORTED_VERSIONS = {'reader-investment.v1', 'reader-investment.v2', VERSION}
COMMON = """READER INVESTMENT: SYSTEM-WIDE FICTION METHOD
Apply to every genre, audience and book, including mixed or newly maintained types.
Preserve author choices, genre identity, originality, POV and agreed length. Reference
examples are NOT this book's facts. Design concrete desires and relationships, causal
pressure, meaningful responses and consequences, scene experience, reader expectations
and earned payoffs. Genre mechanisms must change possibilities, choices or consequences,
not merely appear as labels. Specialist profiles refine this method; none is required.
Distinguish character emotion, intended reader experience and observed reader response.
A composed character may evoke anger; an awkward choice may evoke humor or sympathy.
Use specific actions, perceptions, dialogue and relationship contrasts, not emotional
labels, repeated humiliation or exaggerated adjectives. Do not prescribe one gender,
market, emotion sequence, betrayal trope or identical plot for all books.
Opening: enter a concrete consequential situation with a legible emotional anchor.
Establish whom/what to care about, tension or contrast, the genre's causal contribution,
and a specific expectation. The opening chapter must provide real narrative value;
a synopsis, Introduction or a promise of future excitement cannot substitute for it.
Across the book, develop and change that investment; earn major emotional turns through
motives, resistance, choices and aftermath. Answer meaningful promises; let consequences
create further interest. Quiet, comic, intimate and reflective scenes can provide value.
Scene capacity: distinguish indispensable lived moments from connective summary.
Protect the encounter, resistance, response and meaningful aftermath of major turns;
do not replace them with a report that a choice, negotiation or reconciliation occurred.
Records, rules and arrangements matter through what people want, risk and do with them,
not through repeated procedural explanation. Once readers have seen a rule work, recall
only the changed limit, contested exception or new cost needed now. A signed record can
carry emotional weight; a procedural explanation need not be repeated in every voice.
For consequential decisions, make the deciding person's specific desire, fear or attachment
legible through POV-permitted actions or speech. A correct procedure is not a personal motive.
Keep the trigger of a consequence separate from later decisions about it: recalling an
injury or loss must not attribute it to a different, later choice. When capability changes
or a departure matters, supply the minimum visible bridge needed to understand it; do not
turn this into a medical, legal or travel checklist for every story.
Reader reward means a valued change earned through earlier pressure and action: agency,
recognition, discovery, reciprocal trust, justice, comic reversal, mastery or bittersweet
release according to THIS book. Show the changed situation and let its emotional effect
land. Do not substitute a declaration of empowerment, a promise of later payoff, repeated
defeat or a villain's implausible collapse. Not every genre rewards revenge or reunion.
Estimate space from this book's actual
scenes, language, POV and rhythm, not a universal scene quota or a genre word formula.
When the intended story exceeds the agreed space, explain the concrete tradeoff and
propose a revised allocation or length for author confirmation. Never silently delete
adopted choices, expand the approved count, or add filler. More words alone prove nothing.
Vary intensity and rhythm. Do not force an accident, reversal or protagonist decision
into every scene, or a rigid first-screen word quota. Narrative intent is not proof of
successful prose or measured retention. Preserve output contracts and factual checks;
this method adds no automatic review call, numerical literary gate or endless retry.

SCENE FOCUS AND BACKGROUND TRUTH
Continuity constraints, preserve_facts, allowed_knowledge and reserved promises
bound what may be written; they are NOT a list of sentences, dialogue topics or
mandatory recaps. Keep those facts true even when unmentioned. Chapter goal, beats,
local_payoff and emotional change define scene intent. If a beat includes an old
fact, preserve its causal function with only the reminder needed for this scene;
do not delete an adopted event or due reveal. A fact becoming public can be a new
event even when readers know it: dramatize exposure, resistance, changed leverage
and reactions rather than replaying the entire testimony. Give new readers needed
orientation without treating a new listener as a reason to re-explain every rule.
The SCENE_EXECUTION_BRIEF is a lossless source view: scene_plan contains the plan;
continuity_constraints are background truth; chapter_contract includes both intent
and guardrails. Keep all contract obligations, but distinguish their roles above.
Previously approved prose is evidence of what readers have experienced; future
plans and committed summaries are not proof of wording or successful portrayal.

GENRE AS LIVED EXPERIENCE
Check both causal mechanism and perceptible experience. In representative scenes,
let this book's established genre/world shape bodily attention, spatial possibility,
relationships, ordinary practices and the texture of action as appropriate. A rule
that changes a decision may still feel generic if its lived consequences stay unseen.
Use only established capabilities and cultures; no mandatory keyword, sensory count,
creature, romance, violence or invented custom. Grounded and realist genres equally
need a specific life. Briefly bridge significant form/location/capability changes.

VOICE, SUBTEXT AND CHAPTER VALUE
Different people pursue different conversational aims with different knowledge,
attention, evasions and rhythms. Do not distribute one polished thesis across every
speaker or explain again in narration what an action has already made felt. Preserve
necessary interpretation, character thought and effective motifs whose meaning changes.
Before a chapter break, deliver its action or emotional movement. Do not repeatedly
move the answer to the next chapter merely because the last sentence is a question.
Keep the author-approved chapter/word limits; choose natural scene or aftereffect
boundaries and vary endings, including quiet releases. Never cure repetition with
filler, arbitrary conflict, a different ending, or more chapters without agreement.
"""
STAGES = {
    'brainstorm': 'Propose complete causal routes and their reader experience. The author chooses major direction; you handle professional detail. Keep an opening-to-ending view, recommend convergence when core choices are settled, and do not keep asking about minor staging.',
    'story_design': 'Translate adopted choices into the full-book causal and emotional route. Identify opening investment, major relationship turns, costly choices and payoffs in existing skeleton fields. Unchosen alternatives stay proposals.',
    'chapter_plan': 'Plan each chapter by its function: entry situation, POV attention, action or understanding change, reader value and resulting expectation/aftereffect. Identify its essential on-page moments and which transitions can be summarized. Distinguish what approved prose has already shown from what only the plan knows. Identify who makes the consequential choice and what personal motive can be seen without head-hopping. Use existing scene/choice/local_payoff fields rather than a second parallel chapter outline. Allocate space unevenly when scene needs differ; surface capacity conflicts instead of hiding them in a compressed outline. Preserve future reveals and vary scene functions.',
    'draft': 'Write the actual scene rather than an expanded plot summary. Let readers experience important choices, discoveries and relationship shifts through concrete language and distinctive voices. Use existing chapter goals and approved prior prose to focus attention: what is new now, whose motive matters, which earlier pressure earns this reward, and what must remain unrevealed. Carry these into the scene without printing a checklist or commentary on its intended effect. Give the chapter its due value before leaving the next expectation.',
    'review': 'Read actual prose for causal and emotional steps, relationship change, reader orientation, repetition, voice and earned payoff. Check whether a rule explanation merely repeats established knowledge, whether a decisive personal motive is visible, and whether a retrospective consequence is attributed to its actual trigger. Distinguish contradiction from missing orientation and from optional aesthetic improvement. Cite the passage behind a concern; preserve effective restraint and aftermath. Never invent a reaction because the plan intended it, or repair motivation by inserting omniscient facts.',
}
READING = """Across all genres, distinguish emotions described in the characters from the
response suggested by the actual prose. Observe where the reader can understand a desire,
care about a relationship, feel tension/humor/tenderness/anger or another experience,
form an expectation, or receive a payoff. Cite concrete passages, not author intention.
Notice confusion, repetitive stimulation, unearned emotional shifts and broken promises
when evidenced. Do not demand one emotion, high intensity or a reversal in every scene.
Quiet understanding and aftermath can be strengths. Do not invent shortcomings to fill
a checklist. These are simulated observations, not measured reader reactions.
"""


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def snapshot():
    value = {'schema': VERSION, 'common': COMMON, 'stages': deepcopy(STAGES)}
    return {**value, 'sha256': _digest(value)}


def validate(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'schema', 'common', 'stages', 'sha256'}
            or value['schema'] not in SUPPORTED_VERSIONS or not isinstance(value['common'], str)
            or not isinstance(value['stages'], dict) or set(value['stages']) != set(STAGES)
            or any(not isinstance(v, str) or not v.strip() for v in value['stages'].values())
            or not value['common'].strip() or len(json.dumps(value)) > 24000
            or value['sha256'] != _digest({k: v for k, v in value.items() if k != 'sha256'})):
        raise ValueError('Invalid frozen reader investment policy')
    return deepcopy(value)


def instructions(value, stage):
    policy = validate(value)
    if policy is None:
        return ''
    stage = {'architect': 'chapter_plan', 'outline_planner': 'story_design',
             'scribe': 'draft', 'editor': 'review', 'continuity_guardian': 'review',
             'style_curator': 'review'}.get(stage, stage)
    return policy['common'] + '\n' + policy['stages'][stage]
