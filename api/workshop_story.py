"""Versioned whole-book view and explicit, atomic major decisions.

Model summaries are proposals with provenance, never author approvals. Known open
questions survive malformed replies. Only user actions consume a pending question.
"""
from copy import deepcopy
import hashlib
import json
from typing import Literal
from pydantic import Field
from core.reader_investment import snapshot, instructions
from .workshop_story_result import AREAS as AREAS, Record, validate_candidate

VERSION = 'story-workshop.v1'
RULES = '''
STORY WORKSPACE (story-workshop.v1; overrides optional-choice advice above):
The author decides major story direction, not minor staging, logistics or prose.
Normally present ONE consequential question with 2–3 mutually exclusive options
using design_notes.proposals + guidance.groups. Do not create a question merely
to fill a round. Explain the stakes, reader experience and downstream differences.
The server records explicit select/custom/delegate/question/reframe actions.
A pending question cannot be implicitly approved by a casual 'continue'. Questions
and requests for alternatives are NOT adoption. Delegate authority is limited to
this one question. Keep settled choices unless explicitly revised by the author.
After adoption, advance the whole-book route rather than inventing local minutiae.
If story_state.pending remains, address that same issue; do not introduce a new
major fork unless the author requests reframing. On reframe supply replacement
alternatives for the SAME issue. Do not silently decide unrelated open premises.

In the SAME discussion response add design_notes.story (concise, not a second novel):
{"synopsis":"current opening-to-ending route; distinguish adopted facts from proposals",
 "focus":"direction|opening|middle|climax|ending",
 "nodes":[{"area":"direction|opening|middle|climax|ending", "summary":"current route or gap",
   "status":"missing|proposed|settled|conflict", "evidence":[{"message_index":0,"quote":"exact author words"}]}],
 "changes":["what this round changed and why"],
 "opening":{"situation":"concrete opening conflict/contrast", "anchor":"person, desire or relationship to care about",
   "reader_experience":"intended reader response, not merely protagonist emotion",
   "genre_cause":"how selected types change the event", "first_value":"value delivered in the opening chapter",
   "next_expectation":"specific reason to read on", "preview":"optional short illustrative scene, not approved prose"},
 "assumptions":[{"text":"unconfirmed premise", "kind":"major_premise|causal_gap|detail|external_fact",
   "option_index":1}]}
Supply all five map areas, even where only a broad proposed route or gap exists.
Do not claim a percentage complete. settled requires exact author evidence (same
Evidence schema as guidance). Synopsis is a MODEL SYNTHESIS, not new canon.
Assumptions are new or changed items only: attach a major premise to its proposal
via option_index; never ask authors to decide minor detail.
To resolve an earlier causal_gap or detail, add story.resolutions:[{"id":"saved assumption ID",
"explanation":"concrete causal solution within adopted scope"}]. This records a model
explanation, not verified truth. Never resolve major_premise/external_fact this way. Ordinary details can
remain provisional; you must solve causal gaps within adopted scope. Real-world
facts remain unverified, even after an author chooses a fictional premise.
If correcting an earlier assumption, give a new concrete replacement in the reply;
never claim verified research you did not perform. Track unknowns, not a checkbox quiz.
Recommend skeleton delivery once the route, major choices and ending are coherent;
no fixed round count. Do not prolong discussion by requesting names or minor beats.
The reply, map and proposal must agree. All prior discussion and later explicit
revisions remain authoritative; story_state is a navigation aid, not a replacement.
'''

class Action(Record):
    mode: Literal['select', 'custom', 'delegate', 'question', 'reframe', 'reopen']
    question_id: str = Field(min_length=1, max_length=100)


def enabled(design):
    return (design or {}).get('policy', {}).get('story_workshop_version') == VERSION


def enable(design):
    from . import workshop_guidance
    value = workshop_guidance.enable(design)
    if not enabled(value):
        policy = value['policy']
        policy['story_workshop_version'] = VERSION
        policy['reader_investment'] = snapshot()
        policy['rules'] += '\n' + instructions(policy['reader_investment'], 'brainstorm') + RULES
        policy['delivery_rules'] += '\n' + instructions(policy['reader_investment'], 'story_design')
        value['policy_sha256'] = hashlib.sha256(json.dumps(policy, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        value['story_state'] = {'map': None, 'pending': None, 'actions': [], 'assumptions': []}
        previous = value.get('rounds', [])[-1:]
        if previous:
            groups = (previous[0].get('guidance') or {}).get('groups', [])
            latest = {d['proposal_id']: d for d in value.get('decisions', [])}
            accepted = sum(latest.get(pid, {}).get('status') in {'accepted', 'revised'}
                           for pid in groups[0]['option_ids']) if len(groups) == 1 else 0
            if len(groups) == 1 and accepted != 1:
                value['story_state']['pending'] = {**deepcopy(groups[0]), 'proposals': deepcopy(previous[0]['proposals']),
                    'recommendation_reason': previous[0].get('recommendation_reason', '')}
    return value


def selected(session, ids):
    pending = session['creative_design'].get('story_state', {}).get('pending')
    if len(ids) != 1 or not pending or ids[0] not in pending['option_ids']:
        raise ValueError('请针对当前问题选择一个方案；历史方案需先重新讨论。')
    return [next(p for p in pending['proposals'] if p['id'] == ids[0])]


def validate_action(session, action, ids, message, intent):
    state = session['creative_design']['story_state']
    pending = state.get('pending')
    if action and action.mode == 'reopen':
        if pending or ids or intent == 'deliver' or not any(a['question']['id'] == action.question_id and a['mode'] in {'select', 'custom', 'delegate'} for a in state['actions']):
            raise ValueError('请先处理当前问题，再重新讨论历史决定。')
        return
    if not pending:
        if action or ids:
            raise ValueError('当前问题已变化，请刷新后重试。')
        return
    if action is None or action.question_id != pending['id']:
        raise ValueError('请先选择本轮方案、自定义方向或明确委托本题；也可仅提问或要求换一组。')
    if action.mode == 'select':
        selected(session, ids)
    elif ids:
        raise ValueError('此操作不能同时采用方案。')
    if action.mode in {'custom', 'question'} and not message.strip():
        raise ValueError('请填写自定义方向或要询问的问题。')
    if intent == 'deliver' and action.mode in {'question', 'reframe'}:
        raise ValueError('提问或换一组不会决定故事走向，请决定本题后再生成骨架。')


def apply_action(session, action, ids, message, now):
    """Caller validates everything before mutation, under the session lock."""
    if action is None:
        return ''
    state = session['creative_design']['story_state']
    if action.mode == 'reopen':
        previous = next(a for a in reversed(state['actions']) if a['question']['id'] == action.question_id)
        question = deepcopy(previous['question'])
        question['id'] = f"r{session['revision'] + 1}:reopen"
        state['pending'] = question
        text = f"重新讨论「{question['topic']}」。此前决定保留在历史中，新决定前不要自动替换。"
    else:
        question = deepcopy(state['pending'])
        text = {'select': '采用所选方案', 'custom': '采用下面的自定义方向',
                'delegate': '请你在本题已展示的方案范围内专业选择，明确说明选择及理由',
                'question': '仅解释本题，不采用方案、不推进到下一项重大决定',
                'reframe': '本题方案都不合适，请换一组，保留其余已确认方向'}[action.mode]
        text = f"对「{question['topic']}」：{text}。"
        if action.mode in {'select', 'custom', 'delegate'}:
            state['pending'] = None
            if action.mode in {'custom', 'delegate'}:
                latest = {d['proposal_id']: d for d in session['creative_design']['decisions']}
                for pid in question['option_ids']:
                    if latest.get(pid, {}).get('status') in {'accepted', 'revised'}:
                        session['creative_design']['decisions'].append({
                            'proposal_id': pid, 'status': 'deferred', 'note': text,
                            'source': 'author_action', 'revision': session['revision'] + 1,
                            'message_index': len(session['messages']), 'created_at': now})
            for item in state['assumptions']:
                if item.get('question_id') == question['id'] and item['kind'] == 'major_premise':
                    if action.mode == 'select':
                        item['status'] = 'adopted_premise' if item.get('proposal_id') in ids else 'not_adopted'
                    else:
                        item['status'] = 'delegated' if action.mode == 'delegate' else 'superseded_by_custom'
    state['actions'].append({'mode': action.mode, 'question': question, 'proposal_ids': ids,
                             'note': message.strip(), 'revision': session['revision'] + 1, 'created_at': now})
    return text + '\n本次操作仅针对这个重大问题；其他假设不会被自动批准，现实资料不视为已核实。'


def _question_from_record(record):
    """Usable proposals do not depend on a valid progress/map companion.

    Only validated structured alternatives qualify. Never parse reply prose into
    actions or turn missing navigation into readiness or author approval.
    """
    guidance = record.get('guidance') or {}
    groups = guidance.get('groups', [])
    proposals = record.get('proposals', [])
    if len(groups) == 1:
        group = groups[0]
    elif guidance.get('status') == 'unavailable' and record.get('status') == 'recorded' and 2 <= len(proposals) <= 3:
        recommended = [p['id'] for p in proposals if p.get('recommended')]
        group = {'id': f"r{record['source_revision']}:options", 'topic': '选择本轮故事方案',
                 'why': '方案已保存，可选择一个并补充修改，也可以自定方向或要求换一组。',
                 'option_ids': [p['id'] for p in proposals],
                 'recommended_id': recommended[0] if len(recommended) == 1 else None}
    else:
        return None
    return {**deepcopy(group), 'proposals': deepcopy(proposals),
            'recommendation_reason': record.get('recommendation_reason', '')}


def restore_unanswered_options(session):
    """Project pre-fix saved options consistently for reads and turn validation.

    Do not reopen a reply the author has since answered in free text, a running
    turn, or an explicitly resolved question. No disk write or model call here.
    """
    design = session.get('creative_design') or {}
    if (not enabled(design) or session.get('status') in {'running', 'launched'}
            or session.get('restart_required') or session.get('deleted_at')):
        return
    state = design['story_state']
    rounds, messages = design.get('rounds', []), session.get('messages', [])
    if state.get('pending') or not rounds or not messages:
        return
    record = rounds[-1]
    if (record.get('message_index') != len(messages) - 1 or messages[-1].get('role') != 'assistant'
            or (record.get('guidance') or {}).get('status') != 'unavailable'):
        return
    question = _question_from_record(record)
    if question and not any(a['question']['id'] == question['id'] for a in state['actions']):
        ids = set(question['option_ids'])
        if not any(d['proposal_id'] in ids for d in design.get('decisions', [])):
            state['pending'] = question


def record_story(raw, record, session):
    state = session['creative_design']['story_state']
    question = _question_from_record(record)
    # Preserve the current issue on explanation/malformed responses. Only an
    # explicit reframe may replace it; an answered issue permits the next one.
    last = state['actions'][-1] if state['actions'] else {}
    reframe = last.get('mode') == 'reframe' and last.get('revision') == record['source_revision']
    if question and (state['pending'] is None or reframe):
        if reframe and state['pending']:
            for item in state['assumptions']:
                if item.get('question_id') == state['pending']['id'] and item['kind'] == 'major_premise':
                    item['status'] = 'reframed'
        state['pending'] = question
    projected = validate_candidate(raw, record, session)
    validation = projected['validation']
    if projected['map'] is not None:
        state['map'] = projected['map']
        record['story'] = deepcopy(projected['map'])
    known = {a['id']: a for a in state['assumptions']}
    for resolution in projected['resolutions']:
        known[resolution['id']].update(status='model_resolved', resolution=resolution['explanation'],
                                       resolved_revision=record['source_revision'])
    for assumption in projected['assumptions']:
        if assumption['id'] not in known:
            state['assumptions'].append(assumption)
            known[assumption['id']] = assumption
    state['validation'] = validation
    record['story_validation'] = deepcopy(validation)
    if validation['issues']:
        state['notice'] = ('讨论已保存，故事地图未更新；当前展示上次地图。'
                           if validation['map_status'] != 'updated' else
                           '讨论与故事地图已更新；部分辅助信息未采用，详见处理详情。') + '不会自动重试。'
    else:
        state.pop('notice', None)
    if state['pending'] and record.get('guidance', {}).get('next_action') == 'generate':
        record['guidance']['next_action'] = 'choose'
        record['guidance']['reason'] = '请先决定当前重大问题，再生成完整骨架。'
    elif record.get('guidance', {}).get('next_action') == 'generate':
        from core.story_design import enabled as story_first_enabled
        session_story_first = story_first_enabled(session)
        # One source of readiness: this response's validated five-node map.
        # A preserved older map or model-written "settled" without author evidence
        # cannot turn a broken companion into a recommendation to finish design.
        current = projected['map']
        unresolved = any(a['kind'] in {'major_premise', 'causal_gap'} and a['status'] == 'pending'
                         for a in state['assumptions'])
        if (current is None or validation['issues'] or unresolved
                or not all(n['status'] in ({'settled', 'proposed'} if session_story_first else {'settled'}) for n in current['nodes'])):
            record['guidance']['next_action'] = 'refine'
            record['guidance']['reason'] = '当前故事路线仍有未决内容或缺少作者依据，请先协调；已有讨论与决定保留。'
