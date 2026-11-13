"""Adapt a reviewed historical ending for publication, without editing the plan.

Legacy contracts allowed free text where the manuscript runner now requires a
lifecycle enum. Preserve that text as an obligation. A model may classify its
meaning, but cannot write, omit or replace any story or contract fields.
"""
from copy import deepcopy

from core.planning_errors import OutlinePlanningError, PlanningInputTooLarge
from core.plot_thread_contract import ARC_STAGES


INSTRUCTION = """STAGE: ending_arc_vocabulary
Classify each historical character arc at the required point in the finale:
beginning, middle, climax, or resolution. Use the full supplied character and
reviewed ending obligation. These are lifecycle positions, not proof of success,
absolution or reconciliation. Preserve intentionally open outcomes. Return only
{"stages":{"<character_id>":{"stage":"<lifecycle position>","quote":"<exact
nonblank passage from this character's required_arc_stage, required_outcome or
required_choice>"}}}. If the supplied material cannot support a position, use
"undetermined" instead of inventing one. No prose or revised obligations."""


def _assign(planner, rows, candidate_hash):
    from core.outline_planning import _json, content_hash
    context = {'obligations': rows}
    dependencies = {'candidate': candidate_hash, 'context': content_hash(context),
                    'instruction': content_hash(INSTRUCTION)}
    name = 'ending-arc-vocabulary-v1-' + content_hash(dependencies)

    def validate(value):
        if (not isinstance(value, dict) or set(value) != {'stages'}
                or not isinstance(value['stages'], dict) or set(value['stages']) != set(rows)):
            raise ValueError('Ending stage classification must cover exactly the requested characters')
        for key, choice in value['stages'].items():
            arc = rows[key]['obligation']
            if (not isinstance(choice, dict) or set(choice) != {'stage', 'quote'}
                    or not isinstance(choice['stage'], str) or choice['stage'] not in ARC_STAGES | {'undetermined'}
                    or not isinstance(choice['quote'], str) or not choice['quote'].strip()
                    or not any(isinstance(arc.get(field), str) and choice['quote'] in arc[field]
                               for field in ('required_arc_stage', 'required_outcome', 'required_choice'))):
                raise ValueError('Invalid ending stage or evidence for ' + key)
        return value

    saved = planner._load(name, dependencies, validate, strict_dependencies=True)
    if saved is None:
        # Remember a capacity split before issuing either child, so a model
        # with a larger context cannot repurchase completed child requests.
        keys = list(rows)
        pieces = {'children': [keys[:len(keys)//2], keys[len(keys)//2:]]}

        def split(value):
            if len(keys) < 2 or value != pieces:
                raise ValueError('Invalid saved ending-stage partition')
            return value

        partition = planner._load(name + '-split', dependencies, split, strict_dependencies=True)
        if (partition is None and len(keys) > 1
                and not any((planner.cache / 'raw').glob(name + '.*.prompt.txt'))
                and not planner._prompt_fits(INSTRUCTION + '\n\n' + _json(context), 'story_design', None)):
            # Creative preparation has its own absolute envelope ceiling even
            # when the transport has no declared limit. Split before dispatch.
            partition = pieces
            planner._save(name + '-split', dependencies, partition)
        if partition is None:
            try:
                saved = planner._call(name, INSTRUCTION, context, validate, strict_dependencies=True,
                                      recover_archived=True)
            except PlanningInputTooLarge as exc:
                if exc.stage_name != name or len(keys) < 2:
                    raise
                partition = pieces
                planner._save(name + '-split', dependencies, partition)
        if partition is not None:
            combined = {}
            for ids in partition['children']:
                combined.update(_assign(planner, {key: rows[key] for key in ids}, candidate_hash))
            saved = validate({'stages': combined})
        planner._save(name, dependencies, saved)
    if any(row['stage'] == 'undetermined' for row in saved['stages'].values()):
        raise OutlinePlanningError('历史角色终局语义不足以判断弧光阶段；原大纲、核查和分类依据均已保存。')
    return saved['stages']


def publication_contract(planner, candidate):
    from core.outline_planning import content_hash
    ending = deepcopy(candidate['ending_contract'])
    characters = {c['id']: c for c in candidate['characters']}
    ambiguous, derived = {}, {}
    for arc in ending['character_arcs']:
        key = arc['character_id']
        if key in derived or key in ambiguous:
            raise OutlinePlanningError('Duplicate character ending obligation: ' + key)
        raw = arc['required_arc_stage']
        normalized = raw.strip().lower()
        if normalized in ARC_STAGES:
            derived[key] = normalized
        elif normalized in {'complete', 'completed'}:
            derived[key] = 'resolution'
        else:
            ambiguous[key] = {'character': characters[key], 'obligation': arc}
    if ambiguous:
        choices = _assign(planner, ambiguous, content_hash(candidate))
        derived.update({key: row['stage'] for key, row in choices.items()})
    for arc in ending['character_arcs']:
        raw, stage = arc['required_arc_stage'], derived[arc['character_id']]
        if raw != stage:
            if arc.get('reviewed_stage_description', raw) != raw:
                raise OutlinePlanningError('Conflicting historical arc description; retained without overwriting')
            arc.update(reviewed_stage_description=raw, required_arc_stage=stage)
    return ending
