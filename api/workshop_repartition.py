"""Explicit, retryable chapter-count adjustment within the existing turn worker."""
from copy import deepcopy
import math

from pydantic import Field, ValidationError, model_serializer, model_validator

from core.capacity_partition import repartition
from core.capacity_editing import ChapterTarget, reestimate, within_estimates
from core.story_capacity import Record, digest, validate_contract
from . import workshop_capacity


class Request(Record):
    proposal_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    option_id: str = Field(min_length=1, max_length=60)
    chapters: int = Field(ge=1, le=511)
    total_words: int | None = Field(default=None, ge=1)
    words_per_chapter: int | None = Field(default=None, ge=1)
    chapter_targets: list[ChapterTarget] | None = Field(default=None, min_length=1, max_length=511)

    @model_validator(mode='after')
    def ordered_targets(self):
        if self.chapter_targets is not None:
            if [c.number for c in self.chapter_targets] != list(range(1, self.chapters + 1)):
                raise ValueError('各章字数必须完整覆盖连续章节。')
            if self.total_words is not None and sum(c.target_words for c in self.chapter_targets) != self.total_words:
                raise ValueError('各章字数之和必须等于目标总字数。')
            if self.words_per_chapter is not None and any(c.target_words > self.words_per_chapter for c in self.chapter_targets):
                raise ValueError('指定章节的字数超过每章规划上限。')
        return self

    @model_serializer(mode='wrap')
    def preserve_legacy_request_identity(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


RULES = '''
CHAPTER REPARTITION ONLY. You are a novel editor arranging natural chapter breaks.
The full author context and chosen capacity plan are supplied. Preserve all story
events, character choices, relationships, genre promises and ending. Do not reopen
story decisions, invent new plot, generate a skeleton, or ask additional questions.
Use exactly the requested number of chapters and unchanged total_words. Each
chapter's sum must be <= chapter_words_limit. Preserve the chosen option's scene
order. A source scene may be split into consecutive segments at meaningful action,
reaction, change or anticipation beats. Retain its original reader value and all
its content across segments; do not split by a mechanical word counter or repeat
the same scene. Summed segment words for each source scene must stay within that
scene's original minimum/maximum. Provide concise descriptive segment titles.
Return ONLY {"reply":"Brief Chinese explanation of the new reading rhythm",
"chapters":[{"number":1,"segments":[{"scene_id":"original scene id",
"title":"descriptive segment title","target_words":positive_integer}]}]}.
Chapters are numbered 1..requested_chapters. All original scenes occur in order,
no omissions/additions/reordering. A scene may recur only contiguously. No tools,
new facts, story-map updates, capacity_plan, approval claims or private reasoning.

For a capacity_adjustment request, use its supplied adjustment_rules instead of
the unchanged-total response format above. The complete original scene texts,
source quotes, reader values and author decisions remain binding.
'''

ADJUSTMENT_RULES = '''
AUTHOR LENGTH ADJUSTMENT: satisfy exactly the requested total_words, chapter count,
chapter_words_limit, and any explicit chapter_targets. This is a new estimate, not
author adoption or proof of literary quality. Preserve all original source scenes,
their order, on_page/summary presentation, choices, costs, reader values and payoffs.
Do not drop, invent or summarize away story to fit the number. Re-estimate the space
each complete original scene needs, explaining what receives space or tighter pacing;
do not mechanically scale every scene or repeat content to reach a larger target.
Return only {"status":"adjusted","reply":"Chinese explanation of pacing tradeoffs",
"scene_estimates":[{"scene_id":"original id","minimum":positive_int,
"maximum":positive_int,"reason":"concrete scene-specific estimate justification"}],
"chapters":[{"number":1,"segments":[{"scene_id":"original id",
"title":"natural beat title","target_words":positive_int}]}]}.
Every original scene must have exactly one estimate; chapter segments retain all
scenes in original order, contiguous when split. Segment totals must fit each new
scene estimate, exact requested total, ceiling and any chapter_targets.
If the requested total cannot preserve the complete story and its emotional payoff,
return only {"status":"needs_more_space","reply":"Specific affected scenes and
why their choices, costs or payoffs need room; offer a practical next step",
"minimum_total_words":positive_integer_greater_than_requested_total}.
Do not silently return a different total or claim an unfulfilled request succeeded.
'''


def prepare(session: dict, request: Request | None, *, intent: str, mixed: bool) -> dict | None:
    if request is None:
        return None
    if not workshop_capacity.enabled(session) or intent != 'discuss' or mixed:
        raise ValueError('调整章数需单独提交；请先发送故事补充或完成当前选择。')
    if session.get('creative_design', {}).get('story_state', {}).get('pending'):
        raise ValueError('请先完成故事走向选择，再调整章数。')
    state = session.get('capacity', {})
    prior = session.get('capacity_replan')
    if (not state.get('plan') or digest({'plan': state['plan'], 'basis_sha256': state.get('basis_sha256')}) != state.get('proposal_sha256')):
        raise ValueError('已保存的篇幅方案校验不匹配，请恢复完整方案后继续编辑。')
    retry = (prior and prior['request'] == request.model_dump()
             and prior.get('request_basis') == digest(workshop_capacity.source(session))
             and session.get('status') in {'error', 'paused'})
    if retry:
        return deepcopy(prior)
    # A failed *numeric* edit can be corrected in the same editor. The retained
    # plan is not adoptable while stale; only this verified editing scope survives.
    prior_edit = (prior and prior.get('request_basis') == digest(workshop_capacity.source(session))
                  and prior.get('plan') == state.get('plan') and session.get('status') in {'error', 'paused'})
    unmet_edit = (state.get('adjustment', {}).get('status') == 'needs_more_space'
                 and state['adjustment'].get('basis_sha256') == digest(workshop_capacity.source(session)))
    editable_stale = state.get('status') == 'stale' and bool(prior_edit or unmet_edit)
    if ((state.get('status') not in {'proposed', 'confirmed'} and not editable_stale) or state.get('proposal_sha256') != request.proposal_sha256
            or (state.get('status') == 'proposed' and state.get('basis_sha256') != digest(workshop_capacity.source(session)))):
        raise ValueError('篇幅方案已变化，请刷新后重新选择。')
    if state.get('status') == 'confirmed':
        confirmed = validate_contract(state.get('confirmed'))
        if confirmed['option']['id'] != request.option_id or confirmed['plan_sha256'] != digest(state['plan']):
            raise ValueError('只能调整当前已确认的篇幅方案，请刷新后重试。')
    option = next((o for o in state['plan']['options'] if o['id'] == request.option_id), None)
    if option is None:
        raise ValueError('找不到选中的篇幅方案。')
    limit = request.words_per_chapter or workshop_capacity.chapter_limit(session)
    if not limit:
        raise ValueError('请先在基础设定中填写每章字数，再调整章数。')
    total = request.total_words or option['total_words']
    minimum = math.ceil(total / limit)
    if request.chapters < minimum or request.chapters > total:
        raise ValueError(f'保持总篇幅和每章上限，至少需要 {minimum} 章；章节数也不能超过总字词数。')
    targets = [row.model_dump() for row in request.chapter_targets] if request.chapter_targets is not None else None
    if targets is not None and (sum(row['target_words'] for row in targets) != total or any(row['target_words'] > limit for row in targets)):
        raise ValueError('各章字数必须满足目标总字数与每章规划上限。')
    result = {'request': request.model_dump(), 'plan': deepcopy(state['plan']),
            'source_sha256': state['source_sha256'], 'chapter_words_limit': limit,
            'total_words': total}
    editing = any(value is not None for value in (request.total_words, request.words_per_chapter, request.chapter_targets))
    if editing:
        result.update(kind='capacity_adjustment', adjustment_rules=ADJUSTMENT_RULES,
                      requires_reestimate=True)
        local = within_estimates(state['plan'], request.option_id, request.chapters, limit, total, targets)
        if local is not None:
            result.update(local_result=local, requires_reestimate=False)
    return result


def message(request: dict) -> str:
    count = request['request']['chapters']
    unit = '字' if request['plan']['count_unit'] == 'characters' else '词'
    verb = '将目标总篇幅调整为' if request.get('kind') == 'capacity_adjustment' else '保留总篇幅'
    scope = ('重新估算各场景的展开空间与自然分章' if request.get('kind') == 'capacity_adjustment'
             else '只调整自然分章与场景分段')
    return (f'请将所选篇幅方案重新安排为 {count} 章，每章规划不超过 {request["chapter_words_limit"]:,} {unit}，'
            f'{verb}约 {request["total_words"]:,} {unit}，保留全部已确认故事、重要情绪场面与兑现。'
            f'{scope}，先展示新方案，不生成骨架。')


def apply_preferences(session: dict, prepared: dict | None) -> None:
    """Apply only explicit numeric preferences after archiving the old capacity."""
    if not prepared:
        return
    request = prepared['request']
    if 'words_per_chapter' in request:
        session['input']['words_per_chapter'] = request['words_per_chapter']
    if 'total_words' in request:
        session['input']['target_total_words'] = request['total_words']


def complete_edit(session: dict, receipt: dict) -> None:
    """Commit within the existing worker's session lock; never adopt a proposal."""
    state = session.get('capacity', {})
    state['repartition'] = deepcopy(receipt)
    if receipt.get('status') == 'needs_more_space':
        state.update(status='stale', confirmed=None,
                     notice='目标篇幅尚不能保全当前故事；原方案与场景完整保留。' + receipt['reason'])
        state['adjustment'] = deepcopy(receipt)
        state['adjustment']['basis_sha256'] = digest(workshop_capacity.source(session))
    else:
        state.pop('adjustment', None)
    session['capacity_replan'] = None


def finish(session: dict, raw: dict) -> tuple[dict, dict]:
    request = session['capacity_replan']
    try:
        if request.get('local_result'):
            result = deepcopy(request['local_result'])
        elif request.get('kind') == 'capacity_adjustment':
            result = reestimate(request['plan'], request['request']['option_id'], request['request']['chapters'],
                                request['chapter_words_limit'], request['total_words'], raw,
                                request['request'].get('chapter_targets'))
        else:
            result = repartition(request['plan'], request['request']['option_id'],
                                 request['request']['chapters'], request['chapter_words_limit'], raw)
    except ValidationError as exc:
        paths = ['.'.join(str(p) if isinstance(p, int) or p in {'reply', 'chapters', 'number', 'segments', 'scene_id', 'title', 'target_words'} else 'unknown'
                          for p in error['loc']) for error in exc.errors()[:4]]
        raise ValueError('分章结果格式不完整：' + '、'.join(paths) + '。原方案已保留，可重新安排。') from None
    receipt = {'source_proposal_sha256': request['request']['proposal_sha256'],
               'source_option_id': request['request']['option_id'],
               'chapter_words_limit': request['chapter_words_limit']}
    if request.get('kind') == 'capacity_adjustment':
        receipt.update(requested_total_words=request['total_words'], requested_chapters=request['request']['chapters'],
                       locally_allocated=bool(request.get('local_result')),
                       author_request=deepcopy(request['request']))
    if 'unsatisfied' in result:
        receipt.update(status='needs_more_space', **result['unsatisfied'])
        return ({'reply': result['unsatisfied']['reason'], 'questions': list(session.get('questions', [])),
                 'prompt_md': None, 'ready_for_confirmation': False}, receipt)
    receipt['scenes'] = result['provenance']
    return ({'reply': result['plan']['rationale'], 'questions': list(session.get('questions', [])),
             'prompt_md': None, 'ready_for_confirmation': False, 'capacity_plan': result['plan']},
            receipt)
