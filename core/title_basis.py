"""Small naming inputs from retained route design or the Workshop story overview.

This is a read-only projection, not another story planner or canon store. Whole
narrative values are retained; provenance, chapters and execution ledgers remain
in their original stores. No model is used to create this view.
"""
from copy import deepcopy
import json

from .author_requirements import author_text
from .creative_types import digest
from .source_route_context import enabled as source_enabled, validate_context


def _source(identity, value, purpose='story_facts'):
    return {'id': identity, 'purpose': purpose,
            'text': value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(',', ':'))}


def _overview(session):
    state = (session.get('creative_design') or {}).get('story_state') or {}
    value = state.get('map')
    if not isinstance(value, dict) or not isinstance(value.get('synopsis'), str) or not value['synopsis'].strip():
        return None
    nodes = value.get('nodes')
    areas = {'direction', 'opening', 'middle', 'climax', 'ending'}
    if (not isinstance(nodes, list) or len(nodes) != 5
            or any(not isinstance(row, dict) or not isinstance(row.get('summary'), str)
                   or not row['summary'].strip() or row.get('status') not in
                   {'missing', 'proposed', 'settled', 'conflict'} for row in nodes)
            or {row.get('area') for row in nodes} != areas):
        return None
    result = {'synopsis': value['synopsis'], 'nodes': [
        {key: row[key] for key in ('area', 'summary', 'status')} for row in nodes]}
    opening = value.get('opening')
    if isinstance(opening, dict):
        result['opening'] = {key: opening[key] for key in
            ('situation', 'anchor', 'reader_experience', 'genre_cause', 'first_value', 'next_expectation')
            if isinstance(opening.get(key), str) and opening[key]}
    return result


def _overview_boundary(session):
    """Locate the actual saved round, never compare a message index to a revision."""
    design = session.get('creative_design') or {}
    view = (design.get('story_state') or {}).get('map') or {}
    source = view.get('source_revision')
    messages = session.get('messages', [])
    matches = [row.get('message_index') for row in design.get('rounds', [])
               if type(source) is int and row.get('source_revision') == source]
    if (len(matches) == 1 and type(matches[0]) is int and 0 <= matches[0] < len(messages)
            and messages[matches[0]].get('role') == 'assistant'):
        return matches[0]
    return -1  # Unknown legacy chronology: retain exact author text, never guess.


def overview_revised(session):
    """A retained map may predate decisions already reflected in the skeleton."""
    boundary = _overview_boundary(session)
    if any(row.get('action') == 'skeleton_revision'
           for row in session.get('messages', [])[boundary + 1:]):
        return True
    design = session.get('creative_design') or {}
    state = design.get('story_state') or {}
    source_revision = (state.get('map') or {}).get('source_revision')

    def after_map(row):
        index = row.get('message_index')
        if boundary >= 0 and type(index) is int:
            return index > boundary
        revision = row.get('revision', row.get('source_revision'))
        return not (type(source_revision) is int and type(revision) is int and revision <= source_revision)

    latest = {row['proposal_id']: row for row in design.get('decisions', [])}
    if any(row.get('status') in {'accepted', 'revised'} and after_map(row) for row in latest.values()):
        return True
    if any(row.get('mode') in {'select', 'custom', 'delegate'} and after_map(row)
           for row in state.get('actions', [])):
        return True
    # Invalid companion maps intentionally preserve an older map. Their newer
    # saved narrative is usable; no new model summary or user reconciliation.
    validation = state.get('validation') or {}
    return validation.get('map_status') in {'preserved', 'unavailable'} and after_map(validation)


def _author_inputs(session):
    initial = session.get('input') or {}
    origins = session.get('input_origins') or {}
    return [{'source': f'input.{field}', 'text': initial[field]}
            for field in ('premise', 'constraints')
            if origins.get(field, 'author') == 'author'
            and isinstance(initial.get(field), str) and initial[field].strip()]


def author_updates(session):
    """Keep actual author words; a model overview cannot retire a requirement."""
    source = session.get('source_context') or {}
    if not source_enabled(source):
        source = {}
    updates = []
    def add(text, origin):
        # Repeating an earlier instruction can reverse an intervening change.
        # Preserve its chronological position (A, B, A), not just unique texts.
        if isinstance(text, str) and text.strip():
            updates.append({'source': origin, 'text': text})
    for index, row in enumerate(source.get('author_feedback', [])):
        if isinstance(row, dict):
            add(row.get('exact_text'), f'source_feedback[{index}]')
    updates.extend(_author_inputs(session))
    selection = session.get('source_route_selection') or source.get('selection') or {}
    add(selection.get('author_note'), 'route_selection.author_note')
    for index, message in enumerate(session.get('messages', [])):
        add(author_text(message), f'messages[{index}]')
    return updates


def _route(session, source):
    route = deepcopy(source['selected_proposal'])
    adjustments = ((session.get('development') or {}).get('accepted') or {}).get('receipt', {}).get('author_adjustments', [])
    if adjustments:
        # Existing authority validation is local. Only the resulting route
        # values enter naming, never the full baseline/author ledger again.
        from .creative_baseline import build
        baseline = build(session)
        items = {row['id']: row for row in baseline['items']}
        for change in baseline.get('author_adjustments', []):
            path = items[change['commitment_id']]['path']
            if not path or path[0] != 'selected_proposal':
                continue
            target = route
            for key in path[1:-1]:
                target = target[key]
            target[path[-1]] = deepcopy(change['effective_value'])
        # A null adjustment explicitly retires the exact obligation. Remove it
        # after applying all original indexes, so later indexes cannot shift.
        for key, value in list(route.items()):
            if isinstance(value, list):
                route[key] = [row for row in value if row is not None]
            elif isinstance(value, dict):
                route[key] = {k: v for k, v in value.items() if v is not None}
    return route


def basis_identity(session):
    """Bind all inputs that can affect this view without sending them to a model."""
    design = session.get('creative_design') or {}
    return digest({'source_context': session.get('source_context'),
        'selection': session.get('source_route_selection'),
        'overview': (design.get('story_state') or {}).get('map'),
        'author_inputs': _author_inputs(session),
        'rounds': [{key: row.get(key) for key in ('source_revision', 'message_index')} for row in design.get('rounds', [])],
        'decisions': design.get('decisions'),
        'story_actions': (design.get('story_state') or {}).get('actions'),
        'map_validation': (design.get('story_state') or {}).get('validation'),
        'author_updates': [{'text': author_text(row), 'action': row.get('action')} for row in session.get('messages', []) if row.get('role') == 'user'],
        'author_adjustments': ((session.get('development') or {}).get('accepted') or {}).get('receipt', {}).get('author_adjustments', [])})


def select_basis(session):
    """Return None only when an older session needs the narrative fallback."""
    source = session.get('source_context') or {}
    reference = source_enabled(source)
    if reference:
        source = validate_context(source)
    revised = overview_revised(session)
    overview = None if revised else _overview(session)
    if not reference and overview is None:
        return None
    sources = []
    if reference:
        route = _route(session, source)
        fields = {'reader_promise': 'promise', 'continuity_basis': 'continuity',
                  'character_relationship_arcs': 'relationships', 'emotional_design': 'emotions',
                  'highlights': 'highlights', 'proposed_assumptions': 'assumptions'}
        for field, identity in fields.items():
            value = route.get(field)
            if value:
                sources.append(_source(identity, value, 'unconfirmed_design' if identity == 'assumptions' else 'route_design'))
        arc = route.get('causal_arc')
        if not isinstance(arc, dict) or not any(arc.values()):
            raise ValueError('选中路线缺少完整故事走向，原路线仍保留。')
        sources.append(_source('story', {key: value for key, value in arc.items() if key != 'ending'}, 'route_design'))
        for identity, value in (('ending', arc.get('ending')), ('payoffs', route.get('long_hooks_and_payoffs'))):
            if value:
                sources.append(_source(identity, value, 'private_spoiler_boundary'))
        if overview is not None:
            sources.append(_source('current_story', overview, 'current_story_development'))
        kind = 'reference_route'
    else:
        sources.append(_source('story', overview, 'current_story_development'))
        kind = 'story_overview'
    return {'kind': kind, 'sources': sources,
            'needs_current_narrative': reference and revised}
