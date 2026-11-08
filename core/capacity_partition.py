"""Chapter repartitioning preserves the chosen story budget and source scenes."""
from copy import deepcopy
from itertools import groupby

from pydantic import Field

from .story_capacity import Record, validate_plan


class Segment(Record):
    scene_id: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=160)
    target_words: int = Field(ge=1)


class PartitionChapter(Record):
    number: int = Field(ge=1, le=511)
    segments: list[Segment] = Field(min_length=1, max_length=60)


class Partition(Record):
    reply: str = Field(min_length=1, max_length=3000)
    chapters: list[PartitionChapter] = Field(min_length=1, max_length=511)


def repartition(plan: dict, option_id: str, chapters: int, limit: int, raw: dict) -> dict:
    """Validate model-chosen natural breaks; never slice prose arithmetically."""
    original = validate_plan(plan)
    option = next(o for o in original['options'] if o['id'] == option_id)
    partition = Partition.model_validate(raw)
    if [c.number for c in partition.chapters] != list(range(1, chapters + 1)):
        raise ValueError('新方案的章节数与所选章数不一致。')
    order = [sid for chapter in option['chapters'] for sid in chapter['scene_ids']]
    segments = [s for c in partition.chapters for s in c.segments]
    if [sid for sid, _ in groupby(s.scene_id for s in segments)] != order:
        raise ValueError('分章调整必须保留全部原场景及顺序；只能在场景内部设置分段。')
    targets = [sum(s.target_words for s in c.segments) for c in partition.chapters]
    if max(targets) > limit:
        raise ValueError('新方案仍有章节超过每章规划上限，请增加章节或重新安排。')
    if sum(targets) != option['total_words']:
        raise ValueError('调整章数不能改变所选方案的总篇幅。')
    originals = {s['id']: s for s in original['scenes']}
    for sid in order:
        total = sum(s.target_words for s in segments if s.scene_id == sid)
        scene = originals[sid]
        if not scene['minimum'] <= total <= scene['maximum']:
            raise ValueError('分段后的场景篇幅必须保留原场景估算范围。')
    scenes, allocations, provenance = [], [], []
    for chapter, target in zip(partition.chapters, targets):
        refs = []
        for segment in chapter.segments:
            sid = f'part_{len(scenes) + 1}'
            original_scene = originals[segment.scene_id]
            # Original source, presentation and reader-value obligations survive
            # even when the model proposes a shorter navigation title.
            scenes.append({**original_scene, 'id': sid, 'partition_label': segment.title,
                           'minimum': segment.target_words, 'maximum': segment.target_words})
            refs.append(sid)
            provenance.append({'scene_id': sid, 'source_scene_id': segment.scene_id})
        allocations.append({'number': chapter.number, 'scene_ids': refs, 'target_words': target})
    unit = '字' if original['count_unit'] == 'characters' else '词'
    result = validate_plan({**original, 'rationale': partition.reply, 'scenes': scenes,
        'options': [{**deepcopy(option), 'id': 'repartitioned', 'title': f'{chapters} 章 · 保留完整故事',
                     'tradeoff': f'保留原方案约 {option["total_words"]:,} {unit}与全部场景；每章规划不超过 {limit:,} {unit}。',
                     'recommended': True, 'chapters': allocations}]})
    return {'plan': result, 'provenance': provenance}
