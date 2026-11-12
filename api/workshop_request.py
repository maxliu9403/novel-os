"""One discussion contract and a lossless author-context projection.

Author conversation, selected type guidance and adopted facts stay complete.
Only byte-for-byte equal repeated proposal objects use references. Export
instructions are selected by operation, never by story content or a model guess.
"""
from copy import deepcopy

VERSION = 'workshop-discussion.v2'

TRANSPORT = '''
CURRENT REQUEST PROFILE: AUTHOR-GUIDED EXPLORATION (workshop-discussion.v2).
Operation is CONTINUE DISCUSSION, even with an existing skeleton or complete brief.
Return one JSON object: {"reply":"Chinese Markdown advice", "questions":[],
"prompt_md":null, "ready_for_confirmation":false}, plus the enabled companions below.
Use short paragraphs and clear option headings in reply; no HTML or outer code fence.
The questions field is a required JSON artifact containing only essential unresolved
author questions, not interactive tool input; a generic no-questions preamble does not suppress it.
Never emit requests_delivery/delivery_mode, write files, invoke tools or start execution.
Only the separate Generate/Update Complete Skeleton action authorizes delivery.
Full conversation, current_complete_skeleton and initial_brief are supplied; preserve
confirmed facts and scoped author decisions. Latest explicit changes supersede earlier
ones. Previous unanswered questions are context, not a checklist to repeat. Historical
response formats do not override this current request contract.
'''

PROPOSALS = '''
Add design_notes: {"proposals":[{"title":"short", "story":"event, choice, consequence",
"tradeoff":"reader experience and sacrifice", "recommended":true}],
"recommendation_reason":"why", "conflicts":[],
"next_focus":{"topic":"next consequential issue", "why":"why now"}}.
Use 0–3 concise proposals, at most one recommended; omit next_focus if guidance supplies
the next action. No IDs or approval flags: the server assigns these. Explain the selected
Genre/Tag mechanisms in the actual story; do not fill genre_roles/mechanisms tables.
'''

GUIDANCE = '''
Add design_notes.guidance: {"groups":[{"topic":"one major decision", "why":"why now",
"option_indexes":[1,2], "recommended_index":1}], "next_action":"choose|refine|generate",
"reason":"next step", "missing":[]}.
Option indexes are 1-based references to this turn's proposals; one mutually exclusive
group of 2–3 options, or no group for explanations/edits/convergence. While story_state.pending
exists, address that issue; only an explicit reframe replaces its options. Select/custom/
delegate author actions settle that question; question/reframe/continue do not adopt it.
Do not ask authors to confirm minor details. Recommend generate when the whole route and
ending are coherent and major decisions settled; this is advice, never delivery permission.
'''

STORY = '''
Add design_notes.story: {"synopsis":"concise current whole-book route, marking proposals",
"focus":"direction|opening|middle|climax|ending",
"nodes":[{"area":"direction|opening|middle|climax|ending", "summary":"route or gap",
"status":"missing|proposed|settled|conflict", "evidence":[]}], "changes":[]}.
Supply exactly five DISTINCT nodes, one per area, and at most four changes.
settled needs exact USER words: evidence:[{"message_index":0,"quote":"exact quote"}]
with the zero-based conversation index, OR {"input_field":"premise","quote":"exact quote"}.
Use other status values where author support is absent; assistant proposals are not evidence.
For user messages with action develop_story or plan_capacity, content contains host
operation instructions. Only their author_note field contains the author's own words;
an empty author_note cannot prove adoption or settle a story node.
Do not output guidance.coverage: the validated five-node map is the sole progress view.
If an opening is designed, include its current concise story.opening with six string fields:
situation, anchor, reader_experience, genre_cause, first_value, next_expectation;
optional preview is a brief illustrative scene, not approved prose. Before design, omit opening.
Only new/changed unknowns go in story.assumptions (max 6):
[{"text":"unknown", "kind":"major_premise|causal_gap|detail|external_fact", "option_index":1}].
A major_premise must cite its proposal's 1-based option_index; omit that key otherwise.
Do not duplicate these in design_notes.assumptions. Resolve known causal_gap/detail IDs via
story.resolutions:[{"id":"saved ID","explanation":"causal solution"}] (max 6).
These are model explanations; they cannot adopt major premises or verify external facts.
'''

CAPACITY = '''
When the route converges, include optional capacity_plan in this same response; omit
while a major choice is pending unless the author explicitly requests a length comparison.
Cover the entire adopted story, not just this act. Budget meaningful choices, relationship
changes, climax and aftermath on-page, compress transitions rather than story substance.
Prefer one recommended allocation; add an alternative only for a useful pacing tradeoff.
Respect story_capacity.chapter_words_limit for EVERY planned chapter: expand chapter
count with natural breaks when needed, never silently increase chapter length or cut plot.
capacity_plan = {language:string, count_unit:'words'|'characters', rationale:string,
scenes:[{id:string,title:string,source_quote:string,presentation:'on_page'|'summary',
reader_value:string,minimum:positive_int,maximum:positive_int,reason:string}],
options:[{id:string,title:string,tradeoff:string,recommended:boolean,total_words:positive_int,
chapters:[{number:1,scene_ids:[string],target_words:positive_int}]}]}.
Cite exact source_quote (8+ characters) from the provided brief/conversation,
source_reference or supplied SOURCE_EVIDENCE content for each scene. Source citations
establish provenance, not author adoption: source facts, model proposals and author
decisions remain distinct. Do not cite unseen files or invent supporting quotations.
Each of 1–3 options covers every scene once; consecutive chapters start at 1 (max 511),
targets sum to total and lie within their assigned scenes' summed ranges. Split cross-chapter
scenes into independently budgeted groups. All options retain the same cast/events/ending.
Do not emit hashes or approval; only explicit author adoption confirms the allocation.
'''

COMBINATION_CHOICES = '''
AUDIENCE AND COMBINED STORY DIRECTIONS:
The selected audience_channel is the author's readership, not a demand to change
protagonist sex, orientation, POV or ending. Apply the channel-specific methods.
For the first meaningful open-premise turn, propose 2–3 coherent WHOLE-STORY routes
from the SAME selected Genre and 1–2 Tags, with one recommendation and a single choice
group. Keep author-fixed events; vary goals, relationship pressure, opposition and payoff.
Each proposal also includes choice, cost, payoff (one concise sentence each) and
tag_contributions:[{tag_id:"exact selected Tag ID",contribution:"how this Tag changes
this route's concrete decision and later consequence"}]. Include every selected Tag
once; no unselected Tag, forced chapter pattern or separate Tag approval round.
Treat combinations mentioned inside a reference as conditional methods, not permission
to add other Tags. If a new Tag is needed, recommend changing the author's selection.
For an already developed story, offer only useful unresolved major choices, not another
full reset. Every candidate must honor the latest supplemental author request; explain
changes briefly. No selection/delegation means no adoption. When the whole route is
settled, recommend skeleton delivery; do not keep asking about minor plot details.
'''

def discussion_system(session: dict, creative_rules: str) -> str:
    """Compile enabled protocol parts once; delivery keeps its frozen contract.

    Frozen author/type/source data are still sent in full. Old stacked discussion
    policy text stays in history but does not compete with the current transport.
    The actual request is versioned here and hashed by the invocation receipt.
    """
    from . import workshop_capacity, workshop_guidance, workshop_story

    parts = [creative_rules, TRANSPORT]
    design = session.get('creative_design')
    if design:
        parts.append(PROPOSALS)
        if (design.get('policy', {}).get('type_snapshot') or {}).get('schema') == 'selection-snapshot.v2':
            from core.author_requirements import PRIORITY_RULES
            parts.extend((COMBINATION_CHOICES, PRIORITY_RULES))
        if workshop_story.enabled(design):
            from core.story_design import enabled as story_first_enabled
            story_rules = STORY
            if story_first_enabled(session):
                story_rules = STORY.replace("Supply exactly five DISTINCT nodes, one per area, and at most four changes.", "Supply only changed nodes (unique areas) and at most four changes; keep synopsis as the complete current story. For a partial update add base_revision equal to story_state.map.source_revision, or null before the first map. Unchanged nodes are preserved by code; not yet designed areas remain missing.")
            parts.extend((GUIDANCE, story_rules))
        elif workshop_guidance.enabled(design):
            # Historical clients without a story workspace still consume coverage.
            parts.append(workshop_guidance.RULES)
        else:
            parts.append('Put unconfirmed assumptions in design_notes.assumptions (max 6 strings).')
    if workshop_capacity.enabled(session):
        parts.append(CAPACITY)
    from core.source_route_context import enabled as route_source_enabled, RULES as source_route_rules
    if route_source_enabled(session.get('source_context')):
        policy = session['source_context'].get('execution_policy', {})
        parts.append(policy.get('handoff_rules', source_route_rules))
        if policy.get('discussion_rules'):
            parts.append(policy['discussion_rules'])
        parts.append('For the direction node only, evidence may be {"author_action_ref": source_reference.selection.action_id}. '
                     'This proves selection of a discussion direction only; opening/middle/climax/ending still require '
                     'their own exact author evidence to be settled. Keep proposed details editable.')
    return '\n'.join(parts)


def discussion_context(context):
    value = deepcopy(context)
    design = value.get('creative_design')
    if not isinstance(design, dict):
        return value
    adopted = {item['proposal']['id']: item['proposal'] for item in design.get('adopted_proposals', [])}
    replaced = False
    for record in design.get('rounds', []):
        for index, proposal in enumerate(record.get('proposals', [])):
            if proposal == adopted.get(proposal.get('id')):
                record['proposals'][index] = {'id': proposal['id'], 'source': 'adopted_proposals'}
                replaced = True
    if replaced:
        design['proposal_reference_rule'] = 'A proposal with source=adopted_proposals is the identical complete proposal with that id below; read it there. No proposal content was summarized or removed.'
    return value


def delivery_context(context: dict) -> dict:
    """Stage-specific views of an existing complete story, with explicit scope.

    Global design reads the complete author conversation and global skeleton.
    Chapter planning then reads every original chapter, all author words and the
    newly saved global design. Earlier assistant alternatives were already read
    in the preceding stage; they are not author decisions or a second baseline.
    Unknown layouts and first deliveries keep their existing complete context.
    """
    from core.skeleton_projection import story_parts

    stage = context.get('delivery_stage')
    if stage not in {'story_design', 'chapter_plan'}:
        return context
    parts = story_parts(context.get('current_complete_skeleton', ''))
    if parts is None:
        return context
    saved = context.get('saved_story_stages', {}).get('story_design')
    if stage == 'chapter_plan' and not saved:
        return context
    value = deepcopy(context)
    value.pop('current_complete_skeleton')
    view = {k: parts[k] for k in ('source_sha256', 'source_bytes', 'chapter_count', 'header', 'narrative_format')}
    if stage == 'story_design':
        view.update(global_text=parts['global_text'], chapter_index=parts['chapter_index'], scope='global_design',
                    deferred_to_chapter_plan='All original chapter scenes, hooks and payoffs are supplied verbatim to the next chapter_plan stage. Preserve this existing route; do not invent changes to unseen chapter details.')
    else:
        view.update(chapter_text=parts['chapter_text'], scope='all_original_chapters',
                    global_design_source='saved_story_stages.story_design is the newly completed global design. Reconcile every original chapter with it and the explicit author changes; do not silently discard scenes, hooks or payoffs.')
        messages = value.get('conversation', [])
        # Keep positions stable for references, all user messages byte-for-byte,
        # and the latest assistant reply for the author's immediate references.
        assistants = [i for i, m in enumerate(messages) if m.get('role') == 'assistant']
        omitted = assistants[:-1]
        for i in omitted:
            messages[i] = {'role': 'assistant', 'content': '[Earlier assistant exploration read in story_design; use the saved global design and adopted proposals.]'}
        design = value.get('creative_design', {})
        state = design.get('story_state', {})
        if 'map' in state:
            state.pop('map')
            state['map_source'] = 'saved_story_stages.story_design; assumptions, decisions and unresolved questions below keep their stated status.'
        if omitted:
            value['conversation_scope'] = {
                'mode': 'all_author_messages_and_latest_assistant', 'omitted_assistant_indexes': omitted,
                'rule': 'Only earlier assistant exploration is deferred to the complete conversation already read by story_design. No user message is removed. Adopted proposals, author decisions, pending issues and source facts remain supplied.',
            }
    # Public classification is already selected in initial_brief. Keep all
    # metadata if the caller lacks that authoritative selection.
    if not value.get('initial_brief', {}).get('publication_taxonomy'):
        view['publication_metadata'] = parts['publication_metadata']
    view['publication_rule'] = 'Publication serialization is code-owned; use initial_brief.publication_taxonomy. The source file and complete chapter map remain stored unchanged.'
    value['previous_skeleton'] = view
    value['context_projection'] = {'schema': 'workshop-delivery-context.v1', 'stage': stage}
    return value
