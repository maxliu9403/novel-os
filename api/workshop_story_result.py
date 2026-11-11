"""Independent, bounded validation of a model's story navigation companion.

This is a projection of public model output, never an author approval or a reason
for an additional model request. Invalid components cannot poison valid siblings.
"""
from copy import deepcopy
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .workshop_guidance import Evidence, SourceRouteEvidence, _evidence_valid

AREAS = ('direction', 'opening', 'middle', 'climax', 'ending')
MAX_CANDIDATE_BYTES = 128 * 1024
MAX_ISSUES = 24

class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, strict=True)

class Node(Record):
    area: Literal['direction', 'opening', 'middle', 'climax', 'ending']
    summary: str = Field(min_length=1, max_length=1800)
    status: Literal['missing', 'proposed', 'settled', 'conflict']
    evidence: list[Evidence | SourceRouteEvidence] = Field(default_factory=list, max_length=4)

class Opening(Record):
    situation: str = Field(min_length=1, max_length=1200)
    anchor: str = Field(min_length=1, max_length=1200)
    reader_experience: str = Field(min_length=1, max_length=1200)
    genre_cause: str = Field(min_length=1, max_length=1200)
    first_value: str = Field(min_length=1, max_length=1200)
    next_expectation: str = Field(min_length=1, max_length=1200)
    preview: str = Field(default='', max_length=2000)

class Assumption(Record):
    text: str = Field(min_length=1, max_length=1600)
    kind: Literal['major_premise', 'causal_gap', 'detail', 'external_fact']
    option_index: int | None = Field(default=None, ge=1, le=3)

class Resolution(Record):
    id: str = Field(min_length=1, max_length=40)
    explanation: str = Field(min_length=1, max_length=1600)

class MapCore(Record):
    synopsis: str = Field(min_length=1, max_length=6000)
    focus: Literal['direction', 'opening', 'middle', 'climax', 'ending']
    nodes: list[dict] = Field(min_length=5, max_length=5)
    changes: list[str] = Field(default_factory=list, max_length=4)


class Story(MapCore):
    nodes: list[Node] = Field(min_length=5, max_length=5)
    opening: Opening | None = None
    assumptions: list[Assumption] = Field(default_factory=list, max_length=6)
    resolutions: list[Resolution] = Field(default_factory=list, max_length=6)



def encode_candidate(raw):
    """Keep complete bounded public JSON or an explicit omission, never a slice."""
    try:
        data = json.dumps(raw, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        return None
    return data if len(data) <= MAX_CANDIDATE_BYTES else None


def validate_candidate(raw, record, session):
    issues = []
    def issue(component, path, code, message):
        if len(issues) < MAX_ISSUES:
            issues.append(dict(component=component, path=path.lstrip('.') or 'story', code=code, message=message))

    def parse(model, value, component, path):
        try:
            return model.model_validate(value)
        except ValidationError as exc:
            # Only schema-owned field names/indices. Never echo input values,
            # unknown keys, prompts or pydantic's value-containing messages.
            known_fields = set().union(*(set(cls.model_fields) for cls in (Story, MapCore, Node, Opening, Assumption, Resolution, Evidence, SourceRouteEvidence)))
            for error in exc.errors(include_url=False, include_input=False, include_context=False)[:6]:
                location = path
                for part in error['loc']:
                    if type(part) is int and 0 <= part < 1000:
                        location += f'[{part}]'
                    elif isinstance(part, str) and part in known_fields:
                        location += '.' + part
                    else:
                        location += '.unknown'
                issue(component, location, 'invalid_fields', '缺少必填字段，该项未采用。' if error['type'] == 'missing' else '字段格式不符合要求，该项未采用。')
            return None
        except (TypeError, ValueError):
            issue(component, path, 'invalid_fields', '字段格式不符合要求，该项未采用。')
            return None

    def items(key):
        values = raw.get(key, [])
        if not isinstance(values, list) or len(values) > 6:
            issue(key, key, 'invalid_list', '列表格式不符或超过6项，本轮该列表未采用。')
            return []
        return values

    encoded = encode_candidate(raw)
    source_revision = record['source_revision']
    validation = {'schema': 'story-validation.v1', 'source_revision': source_revision,
                  'reviewed_revision': session['revision'], 'map_status': 'preserved' if session['creative_design']['story_state'].get('map') else 'unavailable',
                  'opening_status': 'unavailable', 'issues': issues,
                  'candidate_sha256': hashlib.sha256(encoded).hexdigest() if encoded is not None else None}
    result = dict(map=None, assumptions=[], resolutions=[], validation=validation)
    if not isinstance(raw, dict) or encoded is None:
        issue('map', 'story', 'missing_story' if raw is None else 'invalid_story',
              '未收到可处理的故事地图对象；讨论和已有地图保留。')
        validation['status'] = 'unavailable'
        return result
    from core.story_design import enabled as story_first_enabled
    if story_first_enabled(session) and 'base_revision' in raw:
        previous = session['creative_design']['story_state'].get('map') or {}
        if raw['base_revision'] != previous.get('source_revision'):
            issue('map', 'story', 'stale_update', '地图更新基于旧版本；保留当前故事地图。')
            validation['status'] = 'unavailable'
            return result
        incoming = raw.get('nodes')
        if (not isinstance(incoming, list) or any(not isinstance(n, dict) or n.get('area') not in AREAS for n in incoming)
                or len({n['area'] for n in incoming}) != len(incoming)):
            issue('map', 'nodes', 'invalid_update', '地图更新包含重复或未知区域。')
            validation['status'] = 'unavailable'
            return result
        merged = {n['area']: deepcopy(n) for n in previous.get('nodes', [])}
        merged.update({n['area']: n for n in incoming})
        raw = {k: v for k, v in raw.items() if k != 'base_revision'}
        raw['nodes'] = [merged.get(area, {'area': area, 'summary': '尚未设计', 'status': 'missing', 'evidence': []}) for area in AREAS]
        if 'opening' not in raw and previous.get('opening'):
            raw['opening'] = deepcopy(previous['opening'])
    unknown = set(raw) - set(Story.model_fields)
    if unknown:
        issue('map', 'story', 'unknown_fields', '未识别的附加字段未采用。')
    core = parse(MapCore, {k: raw[k] for k in ('synopsis', 'focus', 'nodes', 'changes') if k in raw}, 'map', '')
    nodes = []
    if core is not None:
        for index, value in enumerate(core.nodes):
            path = f'nodes[{index}]'
            node = parse(Node, {**value, 'evidence': []}, 'map', path)
            if node is None:
                continue
            evidence = value.get('evidence', [])
            rejected = False
            if not isinstance(evidence, list) or len(evidence) > 4:
                issue('evidence', path + '.evidence', 'invalid_evidence', '引用格式不符；已确认状态降回提案。')
                evidence, rejected = [], True
            for j, item in enumerate(evidence):
                evidence_model = SourceRouteEvidence if isinstance(item, dict) and 'author_action_ref' in item else Evidence
                parsed = parse(evidence_model, item, 'evidence', f'{path}.evidence[{j}]')
                if isinstance(parsed, SourceRouteEvidence) and node.area != 'direction':
                    parsed = None
                    rejected = True
                    issue('evidence', path + '.evidence', 'direction_only', '路线选择仅确认方向，具体事件仍是提案。')
                if parsed is None or not _evidence_valid(parsed, session, record['message_index']):
                    rejected = True
                    if parsed is not None:
                        issue('evidence', f'{path}.evidence[{j}]', 'unverifiable_evidence', '引用不匹配作者原话，未作为确认依据。')
                else:
                    node.evidence.append(parsed)
            if node.status == 'settled' and (rejected or not node.evidence):
                node.status = 'proposed'
            nodes.append(node)
        complete = len(nodes) == 5 and {n.area for n in nodes} == set(AREAS)
        if not complete:
            issue('map', 'nodes', 'incomplete_areas', '需要核心方向、开篇、中段、高潮和结局五个不同区域。')
        if any(not c.strip() or len(c) > 1600 for c in core.changes):
            complete = False
            issue('map', 'changes', 'invalid_changes', '本轮变化字段为空或过长。')
        if complete:
            view = core.model_dump()
            view['nodes'] = [n.model_dump() for n in nodes]
            view.update(reviewed_revision=session['revision'], source_revision=source_revision)
            result['map'] = view
            validation['map_status'] = 'updated'
    if raw.get('opening') is not None:
        opening = parse(Opening, raw['opening'], 'opening', 'opening')
        if opening and result['map'] is not None:
            result['map']['opening'] = opening.model_dump()
            validation['opening_status'] = 'updated'
    groups = (record.get('guidance') or {}).get('groups', [])
    proposals = record.get('proposals', [])
    for index, value in enumerate(items('assumptions')):
        path = f'assumptions[{index}]'
        item = parse(Assumption, value, 'assumptions', path)
        if item is None:
            continue
        if item.kind == 'major_premise' and item.option_index is None:
            issue('assumptions', path + '.option_index', 'major_premise_requires_option', '重大前提必须关联本轮可选方案，未自动采用。')
            continue
        if item.option_index is not None and (len(groups) != 1 or item.option_index > len(proposals)):
            issue('assumptions', path + '.option_index', 'invalid_option', '假设关联的本轮方案不存在。')
            continue
        pid = proposals[item.option_index - 1]['id'] if item.option_index else None
        if pid and pid not in groups[0]['option_ids']:
            issue('assumptions', path + '.option_index', 'invalid_option', '假设关联的方案不属于当前问题。')
            continue
        value = item.model_dump(exclude_none=True)
        value.update(id=hashlib.sha256((item.kind + item.text + str(pid)).encode()).hexdigest()[:16],
                     source_revision=source_revision, proposal_id=pid,
                     question_id=groups[0]['id'] if pid else None,
                     status='provisional' if item.kind == 'detail' else 'unverified' if item.kind == 'external_fact' else 'pending')
        result['assumptions'].append(value)
    known = {a['id']: a for a in session['creative_design']['story_state']['assumptions']}
    seen = set()
    for index, value in enumerate(items('resolutions')):
        path = f'resolutions[{index}]'
        item = parse(Resolution, value, 'resolutions', path)
        if item is None:
            continue
        if item.id not in known or known[item.id]['kind'] not in {'causal_gap', 'detail'}:
            issue('resolutions', path + '.id', 'resolution_not_allowed', '只可解释已记录的因果缺口或细节，不能确认重大前提或现实事实。')
        elif item.id in seen:
            issue('resolutions', path + '.id', 'duplicate_resolution', '重复的假设解释未采用。')
        else:
            result['resolutions'].append(item.model_dump())
            seen.add(item.id)
    validation['status'] = 'partial' if issues and result['map'] is not None else 'unavailable' if result['map'] is None else 'complete'
    return result


def candidate_artifact(raw, record, snapshot):
    """Private replay artifact; no prompt, credentials or reasoning stream."""
    data = encode_candidate(raw)
    return {'schema': 'story-candidate.v1',
            'source': {'session_id': snapshot['id'], 'job_id': snapshot.get('job_id'), 'revision': snapshot['revision'],
                       'context_sha256': hashlib.sha256(json.dumps({
                           'input': snapshot['input'], 'messages': snapshot['messages'],
                           'policy_sha256': snapshot.get('creative_design', {}).get('policy_sha256')},
                           sort_keys=True, ensure_ascii=False).encode()).hexdigest()},
            'raw_story': json.loads(data) if data is not None else None,
            'omitted': data is None,
            'validation': deepcopy(record['story_validation']),
            'record': {k: deepcopy(record[k]) for k in ('source_revision', 'message_index', 'proposals', 'guidance') if k in record}}
