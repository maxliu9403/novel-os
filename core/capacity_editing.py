"""Length revisions within existing scene estimates, or explicit re-estimation.

Numeric edits never establish literary adequacy. The local path only allocates
within the existing editorial ranges and never changes a scene or its ordering.
"""
from copy import deepcopy
from typing import Literal

from pydantic import Field, model_validator

from .capacity_partition import PartitionChapter, repartition
from .story_capacity import Record, validate_plan


class ChapterTarget(Record):
    number: int = Field(ge=1, le=511)
    target_words: int = Field(ge=1)


class SceneEstimate(Record):
    scene_id: str = Field(min_length=1, max_length=60)
    minimum: int = Field(ge=1)
    maximum: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=800)

    @model_validator(mode='after')
    def ordered(self):
        if self.maximum < self.minimum:
            raise ValueError('场景重新估算的上限不能小于下限。')
        return self


class Adjustment(Record):
    status: Literal['adjusted', 'needs_more_space']
    reply: str = Field(min_length=1, max_length=3000)
    chapters: list[PartitionChapter] = Field(default_factory=list, max_length=511)
    scene_estimates: list[SceneEstimate] = Field(default_factory=list, max_length=511)
    minimum_total_words: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def complete(self):
        if self.status == 'adjusted':
            if not self.chapters or not self.scene_estimates or self.minimum_total_words is not None:
                raise ValueError('调整后的方案必须包含完整分章和场景篇幅依据。')
        elif self.chapters or self.scene_estimates or self.minimum_total_words is None:
            raise ValueError('无法保全故事时应说明所需篇幅，不得同时伪造已完成的分章。')
        return self


def within_estimates(plan: dict, option_id: str, chapters: int, limit: int,
                     total_words: int, chapter_targets: list[dict] | None = None) -> dict | None:
    """Return a complete local candidate only for unchanged scene placement."""
    original = validate_plan(plan)
    option = next(o for o in original['options'] if o['id'] == option_id)
    if len(option['chapters']) != chapters:
        return None
    scenes = {scene['id']: scene for scene in original['scenes']}
    bounds = [(sum(scenes[s]['minimum'] for s in ch['scene_ids']),
               min(limit, sum(scenes[s]['maximum'] for s in ch['scene_ids'])))
              for ch in option['chapters']]
    if any(lo > hi for lo, hi in bounds) or not sum(lo for lo, _ in bounds) <= total_words <= sum(hi for _, hi in bounds):
        return None
    if chapter_targets is not None:
        targets = [ch['target_words'] for ch in chapter_targets]
        if len(targets) != chapters or sum(targets) != total_words:
            raise ValueError('各章字数之和必须等于目标总字数。')
        if any(not lo <= target <= hi for target, (lo, hi) in zip(targets, bounds, strict=True)):
            return None
    else:
        targets = [min(hi, max(lo, ch['target_words']))
                   for ch, (lo, hi) in zip(option['chapters'], bounds, strict=True)]
        difference = total_words - sum(targets)
        if difference:
            direction = 1 if difference > 0 else -1
            room = [(hi - value if direction > 0 else value - lo)
                    for value, (lo, hi) in zip(targets, bounds, strict=True)]
            remaining = abs(difference)
            available = sum(room)
            portions = [remaining * space // available for space in room]
            remainder = remaining - sum(portions)
            for i in sorted(range(len(room)), key=lambda n: (-(remaining * room[n] % available), n)):
                if remainder and portions[i] < room[i]:
                    portions[i] += 1
                    remainder -= 1
            targets = [value + direction * part for value, part in zip(targets, portions, strict=True)]
    unit = '字' if original['count_unit'] == 'characters' else '词'
    edited = {**deepcopy(option), 'id': 'edited', 'title': f'{chapters} 章 · 自定义篇幅',
              'recommended': True, 'total_words': total_words,
              'tradeoff': f'保留全部场景及既有顺序；约 {total_words:,} {unit}，每章规划不超过 {limit:,} {unit}。',
              'chapters': [{**deepcopy(ch), 'target_words': target}
                           for ch, target in zip(option['chapters'], targets, strict=True)]}
    candidate = validate_plan({**original, 'options': [edited],
        'rationale': '已在原有场景篇幅估算范围内调整分配，全部故事、情绪作用和场景顺序保持。请查看新方案后采用。'})
    return {'plan': candidate,
            'provenance': [{'scene_id': s['id'], 'source_scene_id': s['id']} for s in original['scenes']]}


def reestimate(plan: dict, option_id: str, chapters: int, limit: int, total_words: int,
               raw: dict, chapter_targets: list[dict] | None = None) -> dict:
    """Validate complete re-estimates without trusting returned story fields."""
    candidate = Adjustment.model_validate(raw)
    if candidate.status == 'needs_more_space':
        if candidate.minimum_total_words <= total_words:
            raise ValueError('所需最小篇幅必须大于本次目标；不能用无法保全故事解释已可满足的总量。')
        return {'unsatisfied': {'reason': candidate.reply,
                               'minimum_total_words': candidate.minimum_total_words}}
    estimates = {row.scene_id: row.model_dump(exclude={'scene_id'}) for row in candidate.scene_estimates}
    if len(estimates) != len(candidate.scene_estimates):
        raise ValueError('每个原场景只能重新估算一次。')
    return repartition(plan, option_id, chapters, limit,
                       {'reply': candidate.reply, 'chapters': [ch.model_dump() for ch in candidate.chapters]},
                       total_words=total_words, estimates=estimates, chapter_targets=chapter_targets)
