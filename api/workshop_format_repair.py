"""Bounded, field-scoped repair of packaging after a valid story response.

The coordinator owns deadlines, retries and receipts. This object keeps the
original story immutable and exposes only the failed contract to replacement.
It never reads active Tag versions, restarts creative design or trims sources.
"""
from copy import deepcopy
import json
import re

from core.narrative_format import CONFIRMATION_STATUSES, SELECTION_SOURCES
from .workshop_delivery import DeliveryError


RULES = """
Repair the narrative-format serialization of an already generated story.
This is NOT creative planning. Return ONLY {"narrative_format":{...}}.
The full candidate story below is read-only context for missing structural roles
or suitability fields. Story text, Genre/Tag instructions, author conversation and
source material remain held by the application and cannot be replaced here.
Fix the reported schema/domain errors; preserve existing valid values and every
locked field. Never change the mode, chapter count, valid volume spans, series
identity or confirmation status to make validation pass. Do not copy example
lengths/identities. No story_design, chapters, prose or other top-level fields.
The application merges only narrative_format, then validates the complete stage.
"""


def _locks(candidate: dict, target_words: int | None) -> list[dict]:
    mode, count = candidate['narrative_mode'], candidate['total_chapters']
    locked = [{'path': ['mode'], 'value': mode}, {'path': ['total_chapters'], 'value': count}]
    if target_words is not None:
        locked.append({'path': ['target_words'], 'value': target_words})
    value = candidate.get('narrative_format')
    if not isinstance(value, dict):
        return locked + [{'path': ['confirmation_status'], 'value': 'pending_confirmation'}]

    def keep(path, item):
        locked.append({'path': path, 'value': deepcopy(item)})

    target = value.get('target_words')
    if target_words is None and type(target) is int and target >= count:
        keep(['target_words'], target)
    for key, allowed in [('confirmation_status', CONFIRMATION_STATUSES),
                         ('selection_source', SELECTION_SOURCES)]:
        item = value.get(key)
        if isinstance(item, str) and item in allowed:
            keep([key], item)
        elif key == 'confirmation_status':
            keep([key], 'pending_confirmation')

    # Even if a role/ID is missing, correct unequal spans must not be replanned.
    volumes = value.get('volumes')
    start = 1
    spans = []
    if isinstance(volumes, list):
        for i, volume in enumerate(volumes):
            if (not isinstance(volume, dict) or type(volume.get('chapter_start')) is not int
                    or type(volume.get('chapter_end')) is not int
                    or volume['chapter_start'] != start or volume['chapter_end'] < start):
                break
            spans.extend([{'path': ['volumes', i, key], 'value': volume[key]}
                          for key in ('chapter_start', 'chapter_end')])
            start = volume['chapter_end'] + 1
        else:
            if start == count + 1 and (mode != 'multi_volume' or len(volumes) >= 2):
                locked.extend(spans)
                keep(['volume_count'], len(volumes))

    series = value.get('series')
    if mode == 'series_installment' and isinstance(series, dict):
        for key in ('series_id', 'title', 'book_number', 'planned_books'):
            item = series.get(key)
            if ((key in {'series_id', 'title'} and isinstance(item, str) and item.strip())
                    or (key in {'book_number', 'planned_books'} and type(item) is int and item >= 1)):
                keep(['series', key], item)
    return locked


class NarrativeFormatRepair:
    def __init__(self, candidate: dict, error: str, *, target_words: int | None = None):
        self.candidate = deepcopy(candidate)
        self.mode = candidate['narrative_mode']
        self.error = error
        self.locked = _locks(candidate, target_words)

    def request(self, schema_reference: str) -> tuple[str, str]:
        payload = {
            'operation': 'repair_narrative_format', 'delivery_stage': 'story_design',
            'story_design': self.candidate['story_design'],
            'candidate_format': self.candidate.get('narrative_format'),
            'locked_fields': self.locked, 'validation_error': self.error,
        }
        return RULES + schema_reference, json.dumps(payload, ensure_ascii=False, separators=(',', ':'))

    def merge(self, text: str) -> str:
        if text.startswith('```'):
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.IGNORECASE)
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            raise DeliveryError('分卷格式修正必须返回完整 JSON；原故事仍保留。') from None
        if not isinstance(result, dict) or set(result) != {'narrative_format'}:
            raise DeliveryError('格式修正只能返回 narrative_format，不得重写故事或附加其他字段。')
        contract = result['narrative_format']
        for field in self.locked:
            actual = contract
            try:
                for key in field['path']:
                    # volume_count is derived; the model need not emit it.
                    actual = len(actual['volumes']) if key == 'volume_count' else actual[key]
            except (TypeError, KeyError, IndexError):
                raise DeliveryError('格式修正缺少受保护字段：' + '.'.join(map(str, field['path']))) from None
            if type(actual) is not type(field['value']) or actual != field['value']:
                raise DeliveryError('格式修正不能改变受保护字段：' + '.'.join(map(str, field['path'])))
        return json.dumps({**self.candidate, 'narrative_format': contract}, ensure_ascii=False)
