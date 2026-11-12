"""The workshop's opt-in, revision-bound length decision. No inference here."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib

from pydantic import Field, ValidationError

from core.story_capacity import VERSION, Record, bind_contract, contract, digest, language_key, validate_plan

RULES = '''
STORY-FIRST CAPACITY (story-capacity.v1):
initial_brief.chapters is an initial preference. words_per_chapter is the ceiling
for each planned chapter target, not permission to compress away adopted story.
If the full story needs more space, offer more chapters with natural scene breaks
rather than longer chapter targets. Settle the major route first, then in the SAME
discussion response supply optional capacity_plan. Do not add another questionnaire
or ask the author to estimate minor scenes. Estimate the space needed to EXPERIENCE
opening conflict, choices, relationship changes, reversals, climax and aftermath.
Summarize transitions; stage consequential moments on-page. More words are not by
themselves better. Do not use scene-count multiplication as a quality proof.
One plan covers the whole adopted story, not a partial act. Cite exact source_quote
from the supplied conversation/brief, source_reference or supplied SOURCE_EVIDENCE
for each scene group (8+ characters). A source citation establishes provenance,
not author adoption of proposed events. Split a
cross-chapter scene into independently estimated groups; assign each exactly once.
Offer 1–3 genuinely different length/partition options, no new plot alternatives:
retain all adopted events, cast and ending in every option. Explain tradeoffs and
recommend at most one. Never silently cut a subplot to fit an initial preference.
Use the story's language and explicit counting unit (English words, Chinese characters).
capacity_plan = {language:string, count_unit:'words'|'characters', rationale:string,
 scenes:[{id:string,title:string,source_quote:string,presentation:'on_page'|'summary',
 reader_value:string,minimum:positive_int,maximum:positive_int,reason:string}],
 options:[{id:string,title:string,tradeoff:string,recommended:boolean,total_words:positive_int,
 chapters:[{number:1,scene_ids:[string],target_words:positive_int}]}]}.
All options cover every scene once; chapter numbers start at 1; targets sum to total;
each chapter target is inside the sum of its scenes' ranges. At most 511 chapters.
Do not emit source hashes, author approval or STORY_CAPACITY_JSON: the server owns
these. A recommendation is not an accepted execution contract. Recommend generating
the skeleton once major decisions are settled; the UI lets the author adopt length
and generate in one action. Missing/invalid estimates do not invalidate the reply.
'''
DELIVERY_RULES = '''
AUTHOR-CONFIRMED CAPACITY:
story_capacity.confirmed is the author's execution allocation. Preserve its language,
total words, exact chapter count, per-chapter targets and complete scene coverage.
Carry the on-page reader value into the chapter plan: a major encounter, reversal
or aftermath cannot be replaced by a one-line mention. Do not invent/cut plot to
meet a numeric quota. The target is planning space; prose may vary reasonably.
The server binds STORY_CAPACITY_JSON locally. Do not write this block yourself.
'''


class Choice(Record):
    proposal_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    option_id: str = Field(min_length=1, max_length=60)


def enable(design: dict, initial_input: dict) -> dict:
    if initial_input.get('length_planning') != 'story_first':
        return design
    value = deepcopy(design)
    value['policy']['capacity_version'] = VERSION
    value['policy']['skeleton_review_version'] = 'skeleton-review.v1'
    from core.skeleton_review import RULES as review_rules
    value['policy']['skeleton_review_rules'] = review_rules
    value['policy']['rules'] += RULES
    value['policy']['delivery_rules'] += DELIVERY_RULES
    # Full-document revisions use the same review schema as the last assembly
    # part. Its detailed schema is supplied only to the actual review request.
    # Keep the same policy hashing convention as the existing workshop.
    import json
    value['policy_sha256'] = hashlib.sha256(json.dumps(value['policy'], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return value


def enabled(session: dict) -> bool:
    return session.get('creative_design', {}).get('policy', {}).get('capacity_version') == VERSION


def chapter_limit(session: dict) -> int | None:
    value = session.get('input', {}).get('words_per_chapter')
    return value if type(value) is int and value > 0 else None


def source(session: dict) -> dict:
    design = session.get('creative_design', {})
    return {'input': session['input'], 'conversation': session['messages'],
            'prompt': session.get('prompt_md', ''), 'policy_sha256': design.get('policy_sha256'),
            'decisions': design.get('decisions', []), 'story_state': design.get('story_state')}


def _text_leaves(value):
    """Keep exact field boundaries: two unrelated values cannot form one quote."""
    if isinstance(value, dict):
        for child in value.values():
            yield from _text_leaves(child)
    elif isinstance(value, list):
        for child in value:
            yield from _text_leaves(child)
    elif isinstance(value, str):
        yield value


def _receipt_basis(snapshot):
    # This extra request binding must not alter the historical capacity contract
    # fingerprint returned by source(), including already confirmed contracts.
    return digest({'id': snapshot.get('id'), 'revision': snapshot['revision'],
                   'source': source(snapshot), 'source_context': snapshot.get('source_context')})


@dataclass(frozen=True)
class _SourceReceipt:
    basis_sha256: str
    source_context_sha256: str | None
    source_evidence_sha256: str | None
    texts: tuple[str, ...]
    valid: bool


def make_source_receipt(snapshot: dict, context: dict, *, evidence_packet=None) -> _SourceReceipt:
    """Bind only source text actually supplied by the successful host request.

    Call after the source coordinator returns complete coverage, never from model
    output or merely because a packet exists on disk. The in-memory type prevents
    a model-supplied JSON field from granting itself extra citation scope.
    """
    from core.source_route_context import enabled as source_enabled, validate_context, validate_packet
    reference = snapshot.get('source_context')
    basis = _receipt_basis(snapshot)
    try:
        if not isinstance(context, dict) or context.get('source_reference') != reference:
            raise ValueError('Request source reference mismatch')
        if source_enabled(reference):
            validate_context(reference)
        texts = tuple(_text_leaves(reference))
        packet_sha = None
        if evidence_packet is not None:
            if not source_enabled(reference):
                raise ValueError('Evidence packet requires a bound source route')
            packet = validate_packet(evidence_packet, reference)
            texts += tuple(_text_leaves(packet['content']))
            packet_sha = packet['sha256']
        return _SourceReceipt(basis, digest(reference) if reference else None, packet_sha, texts, True)
    except (ValueError, TypeError, KeyError):
        # A bad optional companion cannot discard the completed discussion.
        # record() retains its candidate and a safe, structured repair reason.
        return _SourceReceipt(basis, None, None, (), False)


def context(session: dict) -> dict:
    if not enabled(session):
        return {}
    state = session.get('capacity', {})
    return {'story_capacity': {'schema': VERSION,
            'confirmed': deepcopy(state.get('confirmed')) if state.get('status') == 'confirmed' else None,
            **({'chapter_words_limit': chapter_limit(session),
                'chapter_limit_rule': 'For unconfirmed plans, each chapter target must not exceed this limit. Expand chapter count to retain the complete story; split scenes at meaningful beats. Do not silently increase chapter length.'}
               if state.get('status') != 'confirmed' and chapter_limit(session) else {}),
            **({'candidate_to_repair': deepcopy(state['candidate']),
                'validation': deepcopy(state['validation'])}
               if state.get('status') in {'unavailable', 'stale'} and 'candidate' in state
               and state.get('validation', {}).get('status') == 'invalid' else {}),
            'notice': state.get('notice', '故事路线确定后，请给出完整篇幅建议。')}}


def invalidate(session: dict, notice='故事或创作决定已变化，请更新篇幅建议。'):
    session.pop('capacity_replan', None)
    if enabled(session) and session.get('capacity'):
        state = session['capacity']
        if state.get('status') != 'stale':
            session.setdefault('capacity_history', []).append(deepcopy(state))
        state.update(status='stale', confirmed=None, notice=notice)


def record(session: dict, result: dict, snapshot: dict, *, source_receipt=None):
    if not enabled(session) or snapshot.get('turn_intent') == 'deliver':
        return
    raw = result.get('capacity_plan')
    if raw is None:
        return
    validation = {'status': 'invalid', 'source_revision': snapshot['revision'], 'issues': []}
    try:
        validation['candidate_sha256'] = digest(raw)
    except (ValueError, TypeError):
        pass
    issues = validation['issues']
    texts = tuple(_text_leaves(source(snapshot)))
    if source_receipt is not None:
        if (not isinstance(source_receipt, _SourceReceipt) or not source_receipt.valid
                or source_receipt.basis_sha256 != _receipt_basis(snapshot)):
            issues.append({'code': 'source_evidence_invalid', 'message': '本轮来源资料的请求绑定未能验证，篇幅候选已保留。'})
        else:
            texts += source_receipt.texts
            validation.update(source_context_sha256=source_receipt.source_context_sha256,
                              source_evidence_sha256=source_receipt.source_evidence_sha256)
    try:
        plan = validate_plan(raw)
        issues.extend({'code': 'source_quote_not_found', 'path': ['scenes', index, 'source_quote'],
                       'scene_id': scene['id'], 'message': '场景的来源引文未在本轮已提供的故事或资料中找到。'}
                      for index, scene in enumerate(plan['scenes'])
                      if not any(scene['source_quote'] in text for text in texts))
        language = session['input'].get('language', '')
        if language.strip() and language_key(language) != language_key(plan['language']):
            issues.append({'code': 'language_mismatch', 'message': '篇幅建议不能改变已经填写的正文语言。'})
        if session['input'].get('mode') in {'multi_volume', 'series_installment'}:
            issues.append({'code': 'unsupported_mode', 'message': '当前篇幅建议仅用于单本连续章节。'})
        if session.get('creative_design', {}).get('story_state', {}).get('pending'):
            issues.append({'code': 'pending_major_decision', 'message': '请先完成当前的大故事走向选择，再规划篇幅。'})
    except ValidationError as exc:
        issues.extend({'code': 'invalid_plan', 'path': list(error['loc']), 'type': error['type'],
                       'message': '篇幅建议的结构或场景分配不完整，请根据保留的候选协调修订。'}
                      for error in exc.errors(include_input=False, include_context=False, include_url=False))
    except (ValueError, TypeError):
        issues.append({'code': 'invalid_plan', 'message': '篇幅建议的格式或长度不符合约定，完整候选已保留。'})
    if issues:
        # A malformed companion must never throw away a useful discussion reply
        # or buy another model request. Preserve any older proposal as stale.
        invalidate(session)
        session.setdefault('capacity', {}).update(status='unavailable', confirmed=None,
            candidate=deepcopy(raw), validation=validation,
            notice='本轮篇幅建议需要修订，候选与原因已保存；继续规划即可修复。')
        return
    validation['status'] = 'valid'
    basis = digest(source(session))
    session['capacity'] = {'schema': VERSION, 'status': 'proposed', 'plan': plan,
        'candidate': deepcopy(raw), 'validation': validation,
        'source_sha256': digest(source(snapshot)), 'basis_sha256': basis,
        'proposal_sha256': digest({'plan': plan, 'basis_sha256': basis}),
        'source_revision': snapshot['revision'], 'reviewed_revision': session['revision'],
        'confirmed': None, 'notice': '这是编辑估算；采用后冻结执行篇幅，不代表已验证正文质量。'}


def validate_choice(session: dict, choice: Choice | None, intent: str, message: str,
                    has_story_action: bool) -> dict | None:
    if not enabled(session):
        if choice:
            raise ValueError('本讨论尚未启用故事容量规划。')
        return None
    if intent != 'deliver':
        if choice:
            raise ValueError('采用篇幅时请使用“采用并生成骨架”。')
        return None
    if has_story_action:
        raise ValueError('请先提交故事走向选择，模型更新篇幅建议后再采用。')
    # Additional story changes must be discussed before freezing the estimate.
    if message.strip() not in {'', '请根据已确认的故事设计生成完整小说骨架。保留既有设定和决定。'}:
        raise ValueError('请先发送故事补充并更新篇幅建议，再采用方案生成骨架。')
    state = session.get('capacity', {})
    if choice is None:
        if state.get('status') == 'confirmed' and state.get('confirmed'):
            return state['confirmed']
        raise ValueError('请先采用一个篇幅方案，再生成完整骨架。')
    if (state.get('status') != 'proposed' or state.get('proposal_sha256') != choice.proposal_sha256
            or state.get('basis_sha256') != digest(source(session))):
        raise ValueError('篇幅建议已过期，请根据当前故事重新规划。')
    option = next((o for o in state['plan']['options'] if o['id'] == choice.option_id), None)
    limit = chapter_limit(session)
    if option and limit and any(ch['target_words'] > limit for ch in option['chapters']):
        raise ValueError('方案中有章节超过你设置的每章字数；请先增加章节并重新安排，再采用生成骨架。')
    return contract(state['plan'], choice.option_id, state['source_sha256'])


def adopt(session: dict, allocation: dict, now: str) -> str:
    option = allocation['option']
    session['capacity'].update(status='confirmed', confirmed=deepcopy(allocation),
                               confirmed_at=now, confirmed_revision=session['revision'] + 1)
    # Keep the author's chapter-length setting. Actual allocation lives in the
    # frozen contract, not a rounded average that overwrites their preference.
    session['input'].update(chapters=len(option['chapters']), language=allocation['language'])
    session['design_needs_revision'] = True
    return f"作者已采用篇幅方案《{option['title']}》：{len(option['chapters'])} 章，共约 {option['total_words']} {allocation['count_unit']}。保留全部已确认故事，按确认的场景与分章分配生成完整骨架。"


def bind(session: dict, prompt: str) -> str:
    if not enabled(session):
        return prompt
    allocation = session.get('capacity', {}).get('confirmed')
    if session.get('capacity', {}).get('status') != 'confirmed' or not allocation:
        raise ValueError('未确认当前篇幅，不能交付骨架。')
    from core.narrative_format import parse_narrative_format_block
    narrative = parse_narrative_format_block(prompt)
    option = allocation['option']
    if (narrative is None or narrative.total_chapters != len(option['chapters'])
            or narrative.target_words != option['total_words']):
        raise ValueError('骨架章节数或总字数未落实已确认的篇幅方案。')
    return bind_contract(prompt, allocation)


def bind_stage(session: dict, stage: str, data: dict, *, chapter_range: tuple[int, int] | None = None) -> dict:
    if not enabled(session):
        return data
    allocation = session.get('capacity', {}).get('confirmed')
    if not allocation or session.get('capacity', {}).get('status') != 'confirmed':
        raise ValueError('未确认篇幅，不能生成骨架阶段成果。')
    option = allocation['option']
    if data.get('total_chapters') != len(option['chapters']):
        raise ValueError('章节数必须与已确认篇幅方案一致。')
    if data.get('narrative_mode') not in {'short_novel', 'standalone_long'}:
        raise ValueError('已确认的篇幅方案仅用于单本连续章节。')
    if stage == 'chapter_plan':
        first, last = chapter_range or (1, len(option['chapters']))
        for chapter, budget in zip(data['chapters'], option['chapters'][first - 1:last], strict=True):
            chapter['target_words'] = budget['target_words']
            chapter['capacity_scene_ids'] = list(budget['scene_ids'])
    return data
