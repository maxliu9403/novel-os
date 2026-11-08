"""Explicit, retryable chapter-count adjustment within the existing turn worker."""
from copy import deepcopy
import math

from pydantic import Field, ValidationError

from core.capacity_partition import repartition
from core.story_capacity import Record, digest
from . import workshop_capacity


class Request(Record):
    proposal_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    option_id: str = Field(min_length=1, max_length=60)
    chapters: int = Field(ge=1, le=511)


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
    retry = (prior and prior['request'] == request.model_dump()
             and prior.get('request_basis') == digest(workshop_capacity.source(session))
             and session.get('status') in {'error', 'paused'})
    if retry:
        return deepcopy(prior)
    if (state.get('status') != 'proposed' or state.get('proposal_sha256') != request.proposal_sha256
            or state.get('basis_sha256') != digest(workshop_capacity.source(session))):
        raise ValueError('篇幅方案已变化，请刷新后重新选择。')
    option = next((o for o in state['plan']['options'] if o['id'] == request.option_id), None)
    if option is None:
        raise ValueError('找不到选中的篇幅方案。')
    limit = workshop_capacity.chapter_limit(session)
    if not limit:
        raise ValueError('请先在基础设定中填写每章字数，再调整章数。')
    minimum = math.ceil(option['total_words'] / limit)
    if request.chapters < minimum or request.chapters > option['total_words']:
        raise ValueError(f'保持总篇幅和每章上限，至少需要 {minimum} 章；章节数也不能超过总字词数。')
    return {'request': request.model_dump(), 'plan': deepcopy(state['plan']),
            'source_sha256': state['source_sha256'], 'chapter_words_limit': limit,
            'total_words': option['total_words']}


def message(request: dict) -> str:
    count = request['request']['chapters']
    unit = '字' if request['plan']['count_unit'] == 'characters' else '词'
    return (f'请将所选篇幅方案重新安排为 {count} 章，每章规划不超过 {request["chapter_words_limit"]:,} {unit}，'
            f'保留总篇幅约 {request["total_words"]:,} {unit}和全部已确认故事。'
            '只调整自然分章与场景分段，先展示新方案，不生成骨架。')


def finish(session: dict, raw: dict) -> tuple[dict, dict]:
    request = session['capacity_replan']
    try:
        result = repartition(request['plan'], request['request']['option_id'],
                             request['request']['chapters'], request['chapter_words_limit'], raw)
    except ValidationError as exc:
        paths = ['.'.join(str(p) if isinstance(p, int) or p in {'reply', 'chapters', 'number', 'segments', 'scene_id', 'title', 'target_words'} else 'unknown'
                          for p in error['loc']) for error in exc.errors()[:4]]
        raise ValueError('分章结果格式不完整：' + '、'.join(paths) + '。原方案已保留，可重新安排。') from None
    return ({'reply': result['plan']['rationale'], 'questions': list(session.get('questions', [])),
             'prompt_md': None, 'ready_for_confirmation': False, 'capacity_plan': result['plan']},
            {'source_proposal_sha256': request['request']['proposal_sha256'],
             'source_option_id': request['request']['option_id'],
             'chapter_words_limit': request['chapter_words_limit'],
             'scenes': result['provenance']})
